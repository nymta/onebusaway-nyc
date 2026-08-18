# External dependencies that can break replay

Everything the inference engine reads besides the bustechGPS feed. Covers both the local harness and the
EC2 deployment, because both run the **same Maven profile** (`local-ie-testing`) — EC2 just overrides
individual properties in `local-loop/ec2/opt-oba/run-inference.sh`.

Provenance matters here: some of this is upstream OBA, some was added by Ranajay or Timothy for our
instance. Each entry says which.

---

## 1. Wall-clock reads in the inference path — fixed, now read the replay clock

The worst of these was stale-fix load shedding, `VehicleLocationInferenceServiceImpl:868-869`:

```java
if (_shedStaleMs > 0 && !_simulation && _nycRawLocationRecord != null
    && (_clock.millis() - _nycRawLocationRecord.getTimeReceived()) > _shedStaleMs) {
```

`_clock.millis()` was `System.currentTimeMillis()`. Wall clock minus the record's timestamp: archived
records are days or weeks old, so **every record was shed**. It is off by default (`_shedStaleMs = 0`,
`:165`) but **EC2 sets `-Doba.shed.maxAgeSec=50`** (`run-inference.sh:19`), so this one setting would
have dropped 100% of an EC2 replay. Added by Ranajay Sen, 2026-07-21.

Three sibling reads were converted with it, so no replay run needs `-Doba.shed.maxAgeSec=0` any more:

| Site | What it gates | Was it live? |
| --- | --- | --- |
| `VehicleLocationInferenceServiceImpl:1075` `computeTimeDifference` | TDS record age | Only when `display.checkAge` is true, which defaults false (`:184`, `:355`) — inert, converted anyway |
| `VehicleLocationInferenceServiceImpl:1099` `isValidRecord` | Rejects records too far in the future | Harmless for past archives; same pattern |
| `QueuePredictionIntegrationServiceImpl:239-243` `getTime()` | Prediction age limit, via `computeTimeDifference:345` and `isPredictionRecordPastAgeLimit:323` | Live wherever the age limit is on; wired bean per `data-sources.xml:209` |

`getTime()` prefers the test hook `_serviceTime`, then the injected clock, then wall clock, so the class
still works when constructed directly by a test with no Spring context.

Reads deliberately left on the wall clock, because they measure real elapsed time rather than data time:
`VehicleLocationInferenceServiceImpl:318-319` (an `awaitIdle` timeout) and `:860`/`:933` (processing
duration). `QueuePredictionIntegrationServiceImpl:291` is also left alone — it is inside
`logPredictionLatency`, which only logs, so under replay it reports a nonsense latency but changes no
behaviour.

## 2. Ingestion deadband — safe, but verify before changing it

`InputServiceImpl.passesDeadband`, enabled on EC2 with
`-Doba.deadband.enabled=true -Doba.deadband.minMeters=10 -Doba.deadband.minIntervalSec=7 -Doba.deadband.maxAgeSec=30`.

Safe under replay: `now` is parsed from the record's own `time-reported` (`:192`), and every comparison at
`:203-211` is relative to that vehicle's last kept record. No wall-clock read.

It does drop records — a rate cap below `minIntervalSec` and a distance test below `minMeters` — so
input and published counts will not match, by design. Added by Ranajay Sen, 2026-07-21.

## 3. UTS crew roster — handled, reads the archive as-of the replay clock

Called per record at `VehicleInferenceInstance:608` and `:657`. Feeds `obs.getOpAssignedRunId()`, so it
changes inferred output via `RunLikelihood:66` and `BlockStateObservation:70`.

Two implementations, selected by `oba.crew.service.class`:

| Implementation | Where | Source |
| --- | --- | --- |
| `OperatorAssignmentServiceImpl` | top-level default, `pom.xml:104` | TDM over HTTP, one request per service date |
| `S3UtsOperatorAssignmentServiceImpl` | `local-ie-testing` (`pom.xml:248`) and EC2 | S3 object |

EC2 passes real credentials from SSM (`/oba/uts/s3/{accessKey,secretKey}`).

**Archive layout** (`s3://mtabuscis-uts-archive`), confirmed 2026-08-12:

- `latest/CIS.txt` plus one prefix per UTC generation date, ~90 days of retention.
- 24 hourly `crew_YYYYMMDD_HHMMSS.csv` per prefix. Filename timestamp is UTC, matching `LastModified`.
- The prefix is the generation date, not the service date. A file holds the previous service date until
  ~02:10Z, both across 02:10–08:10Z, and only the new one from 09:10Z. So an overnight lookup for the
  previous service date does resolve.
- Row counts grow through the day as assignments are entered, so the roster is not static.
- `popi_*` objects sit alongside `crew_*`: pull-out data, unused today (entry 5).

**Replay path.** `-Doba.crew.snapshotDir=<dir>` switches the service to a directory of prefetched
snapshots, selected as-of the replay clock — the newest snapshot at or before `_clock.millis()`
(`CrewSnapshotIndex.asOf`, wired at `S3UtsOperatorAssignmentServiceImpl:180-192`). No timer, no S3 call
during the run, and the roster reloads when the clock crosses a snapshot boundary. Selecting by date
prefix instead would leak a roster that did not exist yet, since the next service date is published the
evening before. Prefetch with `fetch-crew-snapshots.sh`, which also pulls the preceding day for the
as-of lookback.

Unset, production behaviour is unchanged: `latest/CIS.txt`, 30-minute timer, refresh on cache miss.

**Kill switch: `-Doba.crew.disabled=true`** (`:136`), guarding both the scheduled refresh (`:139`) and
the cache-miss refresh (`:312`). Needed only when no snapshot directory is available; on EC2 it goes in
`ec2/opt-oba/run-inference.sh` beside the `-Doba.crew.*` block at `:28-30`.

**Resolved.** A miss used to re-parse the whole file on every lookup — 3,440 fetches in one 5,585-record
run at 28% CPU. In snapshot mode a loaded snapshot is not re-read, so a missing service date answers from
memory. The same amplification shape still exists in the TDM implementation (`../OBA-BUGS.md` defect 7).

Both classes are Timothy Shertzer's, 2026-08-06.

## 4. Static GTFS and STIF — point-in-time, via the bundle

Separate feeds, both consumed at bundle-build time, not read at runtime:

- **GTFS** — routes, trips, stops, shapes, calendar.
- **STIF** — MTA's internal schedule format: run and block structure, operator assignment data, and the
  DSC-to-route mapping.

Consequences for replay:

- A replay window must sit inside **one pick**. The archive spans a C6 boundary at 2026-06-28, so a run
  crossing it would need two bundles and cannot be done in a single pass.
- The bundle must be the one active on the replayed date. A later bundle silently produces different
  trip matches rather than failing.
- `determinism/replay-determinism.sh` asserts the bundle directory name against the fixture's
  `.manifest.json`, which is the only automated guard for this today.

Everything bundle-derived is therefore safe at runtime but date-sensitive: `TransitGraphDao`,
`BlockCalendarService`, `BlockIndexService`, `ScheduledBlockLocationService`, `ShapePointService`,
`RunService`, `DestinationSignCodeService`, `BaseLocationService`, `NycTransitDataService`.

## 5. Vehicle pull-out (pipo) — stubbed in both environments

Called per record at `VehicleInferenceInstance:264` to obtain `assignedBlockId`, which sets
`hasValidAssignedBlockId` and feeds candidate generation.

`local-ie-testing` selects `DummyVehiclePulloutService` (`pom.xml:246`, carabalb 2020-12-17), which
returns null and logs it — once per record, no network call. EC2 does not override this, so **production
runs the stub too**.

So this is not a replay risk, and replay matches production. Worth recording that both are inferring
without pull-out data. Note `pipo.host` is set to a real hostname (`yardtrek.mtabuscis.net`,
`pom.xml`, Lenny 2019-02-18) and `PullOutApiLibrary` logs it at startup even though the Dummy bean is
wired — that startup line is not evidence of a live dependency.

## 6. Unassigned vehicle service — stubbed in both environments

`local-ie-testing` selects `DummyUnassignedVehicleServiceImpl` (`pom.xml`, carabalb 2020-12-17); EC2 does
not override it. Its real implementation runs on its own scheduler and reads instance state, so if it is
ever enabled it becomes a third thread touching per-vehicle state.

## 7. Bundle discovery and switching — runs, but is a no-op with one bundle

Correction to an earlier claim in this file: batch mode does **not** suppress the two timers. The guard at
`BundleManagementServiceImpl:288` is only `_taskScheduler != null`, so both are scheduled (`:292`, `:295`).
Batch mode does the opposite — `refreshApplicableBundles:216-221` is
`isApplicableToDate(getServiceDate()) || isBatchMode()`, making *every* bundle applicable.

What keeps it harmless is having one bundle: `reevaluateBundleAssignment:244-258` sorts by
`BundleItem.compareTo` (which is `getUpdated()`) and `changeBundle` early-returns on an unchanged id. With
two bundle directories present, the hourly trigger would switch mid-run, drain inference threads for 60 s
and then force-cancel them, and `verifyVehicleResultMappingToCurrentBundle:741-746` would reset vehicle
state. `replay-determinism.sh` asserts exactly one bundle, which is the only guard.

Under replay both tasks are now gated off (`ReplayDomain.BUNDLE`, see `../REPLAY.md`). `standaloneMode`
also means discovery scans the local directory rather than calling the TDM.

## 8. TDM configuration service — resolved: the engine never reaches the TDM

`ConfigurationServiceImpl` fetches every `tdm.*` / `tds.*` / `display.*` setting from the TDM config API
at startup, again at T+1min, then every 3 minutes (`:114`, `:116`), returning the caller's inline default
on failure.

`local-ie-testing` leaves `tdm.host`, `tdm.port` and `tdm.url` **empty** (`pom.xml:223-225`; the
`tdm.dev.obanyc.com` block at `:227-229` is commented out), and `run-inference.sh` passes no override. That
is why the log prints an empty hostname. Both environments therefore run on call-site defaults plus the
values force-seeded at startup by the `MethodInvokingFactoryBean` beans in `data-sources.xml:87-150`
(queue endpoints, `inference-engine.acceptAllVehicles`, `display.useTimePredictions`).

Consequences: no `tdm.*` switch can be changed without either reaching the TDM or adding a seed bean; and
`acceptAllVehicles` is seeded `true` unconditionally, in production too, so depot partitioning is bypassed
everywhere. Newer knobs bypass this service entirely and read system properties
(`oba.crew.*`, `oba.shed.maxAgeSec`, `oba.deadband.*` at `InputServiceImpl:87-90`).

Under replay the refresh is gated off (`ReplayDomain.CONFIG`), so config cannot change mid-run.

---

## Checklist before an EC2 replay

| Item | Action |
| --- | --- |
| Load shedding | Nothing to do — the check reads the replay clock (entry 1). Leave `-Doba.shed.maxAgeSec=50` as production has it |
| Crew roster | Prefetch with `fetch-crew-snapshots.sh` and pass `-Doba.crew.snapshotDir`; otherwise `-Doba.crew.disabled=true` |
| Scheduled tasks | `-Doba.replay.tasks=` (empty) blocks all background work; add domains to re-enable |
| Bundle | Must match the replayed date; do not span a pick boundary |
| Deadband | Safe to leave as production has it; expect fewer published than input records |
| Input listener | EC2 uses `RabbitMqInputQueueListenerTask`; replay needs `ReplayFileInputTask` |
| Output queue | EC2 uses `OutputQueueSenderServiceImpl`; that is what an observer subscribes to |
