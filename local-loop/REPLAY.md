# Replay: what was changed and why

Worklog for making the inference engine replay archived AVL on data time. One entry per change, newest
last. Determinism is its own problem — see [determinism/DETERMINISM.md](determinism/DETERMINISM.md).
Sharding strategies (and why they're currently blocked) are in [SHARDING.md](SHARDING.md).

External inputs and their current status: [replay/EXTERNAL-DEPENDENCIES.md](replay/EXTERNAL-DEPENDENCIES.md).

---

## How to run one

From the archive (the normal path — crew fetch, output upload and shutdown are automatic):

```
local-loop/build.sh                  # once per code change
local-loop/replay/preflight.sh       # checks all three credential surfaces
OBA_MAVEN_OPTS="-Xmx12g" local-loop/replay/replay-stream.sh \
  --prefix s3://mtalirr/data-archiver/bustechGps/ \
  --from 2026-07-28/08-45 --to 2026-07-28/08-45 \
  -- -Dreplay.routeFilter='^M[0-9]'          # optional extras after --
```

Bounds are `YYYY-MM-DD` or `YYYY-MM-DD/HH-MM` (5-minute slot start, ET, matching the key names).
On EC2: `/opt/oba/run-replay.sh --prefix ... --from D --to D` (same flags, prod-sized defaults).

From a local fixture: `local-loop/replay/replay.sh <fixture.jsonl>`.
Two-run reproducibility check: `local-loop/determinism/replay-determinism.sh [fixture] -- <extra -D args>`.

| Property | Effect |
| --- | --- |
| `-Dspring.profiles.active=replay` | Swaps in `MutableClock` and `GatedTaskScheduler` |
| `-Dreplay.file=<path>` | Input for `ReplayFileInputTask` (file or FIFO) |
| `-Dreplay.exitWhenDone=true` | Exit after the stripes drain (replay-stream default; non-zero if truncated) |
| `-Dreplay.maxOutstanding=N` | Reader lead bound, default 2000; 0 disables |
| `-Dreplay.routeFilter=<regex>` | Replay only vehicles serving matching routes |
| `-Doba.crew.snapshotDir=<dir>` | Roster from prefetched snapshots, as-of the replay clock |
| `-Doba.replay.tasks=A,B` | Background task domains to allow. Empty blocks everything; `ALL` allows all |
| `-Doba.inference.threads=N` | `1` removes threading as a variable |
| `-Doba.inference.seed=N` | Per-vehicle RNG seed |
| `-Doba.crew.disabled=true` | Skip UTS entirely, when no snapshot directory is available |
| `-Doba.replay.output.dir/.rollLines/.gzip` | Output spool location, part size, compression |

`replay.sh` defaults (env-overridable): `particle.filter.debug=false` (`OBA_PF_DEBUG`), production
deadband 10m/7s/30s (`OBA_DEADBAND_*`), 12 threads (`OBA_INFERENCE_THREADS`), crew from
`/tmp/uts-snapshots` (`OBA_CREW_SNAPSHOT_DIR`; missing dir disables crew).
`replay-stream.sh` env: `REPLAY_OUT_S3` overrides the output bucket (`none` = local only),
`REPLAY_OUT_DIR`, `REPLAY_SKIP_CREW_FETCH=1`, `REPLAY_EXIT_WHEN_DONE=false` keeps Jetty up.

---

## Changes

### 1. A clock that can be driven — `MutableClock`, bean `obaClock`

`data-sources.xml:256-267`. `Clock.systemUTC()` in every profile, `MutableClock` under `replay`. Every
in-path "now" resolves through it, so the driver can set time from the data.

### 2. Replay input task — `ReplayFileInputTask`

Reads the archive wrapper (`{"ts":…,"b":…}`) as well as bare envelopes, advances the clock to each
record's broker `ts` before dispatching it, and exits when done. Driving on the outer `ts` rather than the
device `time-reported` reproduces queue arrival order; the engine itself never sees `ts`.

Archive lateness was measured at 1.154 s max and the bucket is already in `ts` order (36 of 266,112 out of
sequence), so no reorder buffer is used.

### 3. Wall-clock reads converted to the injected clock

`VehicleLocationInferenceServiceImpl:869` (stale-fix load shedding — the one that would have dropped 100%
of an EC2 replay, since `run-inference.sh:19` sets `-Doba.shed.maxAgeSec=50`), `:1075`, `:1099`, and
`QueuePredictionIntegrationServiceImpl:239-243`. Reads left on the wall clock measure real elapsed time:
`VehicleLocationInferenceServiceImpl:318-319`, `:860`, `:933`, and `logPredictionLatency:291`.

### 4. JVM timezone pinned

`env.sh:30` and `ec2/opt-oba/run-inference.sh` set `-Duser.timezone=America/New_York`. Crew matching
compares two Joda `DateMidnight` values built in the default zone, and Joda's `equals` compares chronology,
so patching one side breaks equality. Pinning the JVM fixes both.

### 5. Background tasks gated by domain

No `@Scheduled` anywhere in this repo; everything is `@PostConstruct` plus an injected
`ThreadPoolTaskScheduler`. All 15 consumers inject the concrete class, so the gate is a subclass:

- `util/replay/ReplayDomain` — `INFERENCE_INPUT`, `CONFIG`, `BUNDLE`, `OUTPUT`, `TELEMETRY`
- `util/replay/Replayable` — on the task's own class, which is what the scheduler inspects
- `util/replay/GatedTaskScheduler` — drops tasks whose domains are not in `-Doba.replay.tasks`

18 task classes annotated, covering all 19 schedule call sites. Wired only in the `replay` profile, so
production keeps the plain scheduler. Unannotated tasks are dropped, so anything added later has to be
classified deliberately.

Two things the gate does not reach, both by design: raw `Executors` loops
(`OutputQueueSenderServiceImpl:285-286` — the output path, which must keep running) and the crew
cache-miss refresh, which is not scheduled.

### 6. Crew roster as-of the replay clock

`CrewSnapshotIndex` plus `-Doba.crew.snapshotDir`. The roster is a function of the clock: the newest
prefetched snapshot at or before `_clock.millis()`, reloaded when the clock crosses a boundary. No timer
and no S3 call during a run, so no virtual-time task machinery is needed.

`replay/fetch-crew-snapshots.sh` prefetches a window with a sha256 manifest, including the preceding day
for the as-of lookback. `replay/probe-crew-snapshots.sh` reports which service dates each snapshot holds.

Also fixed here: the refresh lock was the roster map itself, which `refreshData` replaces — two threads
straddling the swap locked different objects. Now a dedicated `_refreshLock`. And a loaded snapshot is not
re-read, which removes the per-record re-parse (previously 3,440 fetches in a 5,585-record run at 28% CPU).

### 7. Reader throttle — the clock must not outrun inference

`submitForVehicle` only queues, so the read loop finished a 300 s bucket in 21 s while inference was near
the start: the virtual clock hit end-of-window, every queued record looked minutes stale to Ranajay's
load-shedding check (70,464 of 86,434 shed), and 81k queued Futures put `BundleManagementServiceImpl`'s
list sweep at O(n²) and the heap under enough pressure to double per-record time. The reader now waits
while `getOutstandingTaskCount()` exceeds `-Dreplay.maxOutstanding` (default 2000, below the 3000 sweep
threshold), and reports progress every 15 s: `read/dispatched/rejected/filtered | throttled % | pending |
rec/s`. Shedding stays off in replay regardless — it is a live-recency valve; replay has no consumer
waiting on freshness.

### 8. Input from the archive bucket — streamed, never staged

`s3://mtalirr/data-archiver/bustechGps/<YYYY-MM-DD>/<HH-MM>.jsonl.gz`, one gzip JSONL per 5-minute slot,
named by slot start in **ET** (filename timestamps verified UTC-consistent against `LastModified`), so
lexicographic key order is chronological. `replay/replay-stream.sh` (local) and `ec2/opt-oba/run-replay.sh`
(EC2) list a prefix, bound it with `--from/--to` at date or slot granularity, and feed the objects in order
through a FIFO: `aws s3 cp | gzip -dc` per object, one at a time. Backpressure chains from the reader
throttle through the 64 KB pipe to the S3 socket, so days of data hold ~2000 records in memory and nothing
touches disk. A failed download aborts the feed loudly rather than leaving a silent gap.

Crew snapshots for the window are fetched automatically before launch — UTC prefixes `[from .. to+1]`,
because ET evening slots cross into the next UTC day; cached days are skipped and a fully-cached window
needs no credentials.

### 9. Output to S3 — spool, rename on completion, upload with the CLI

`S3OutputQueueSenderServiceImpl` (`-Die.output.queue=`, the replay-stream default) replaces the ZMQ
sender: rolling gzip NDJSON parts, serialized byte-identically to production's `enqueue`, closed by a JVM
shutdown hook because `exitWhenDone` ends the run with `System.exit`. A part is written as `.open` and
renamed when complete; the *scripts* upload finished parts with `aws s3 mv` (rolling loop + final sweep)
to `s3://ds-oba/replay/inference-outputs/<from>-to-<to>-<timestamp>/`. The upload cannot live in the
engine: the bundled AWS SDK is 1.3.9 (2012), SigV2-only, and modern buckets answer it with AccessDenied —
CLI-side upload also means laptop profile and EC2 instance role both just work.

### 10. Route filter — replay a subset of the fleet

`-Dreplay.routeFilter='^M[0-9]'`. Per record, the DSC resolves through the bundle's own
`DestinationSignCodeService`; a vehicle is admitted at its first record whose DSC maps to routes that all
match, then keeps every later record so its track stays continuous. Looser than the offline majority-vote
cut (`08-45` slot: 536 vehicles admitted vs 296 offline) — a single all-matching DSC record admits the
vehicle. Counters: `filtered=` and `(N vehicles admitted)` in the done line.

---

## Benchmarks (M4 laptop, 10 cores, 12 threads, full fleet 08-45 slot)

Steady-state ~200–250 ms/record after warm-up (early samples of 45–120 ms are warm-up, not attainable
rate); ~73–97 rec/s ≈ 0.25x real time on 86k accepted records. CPU-bound: thread dumps show zero monitor
contention, `FastMath.log` + `FoldedNormalDist`/`StudentT` dominate; GC ≤ 10% of wall with the throttle
(live set is the transit graph, ~4.5 GB; allocation churn is young-gen and cheap). Production
`c7i.12xlarge` ≈ 5x this box's effective FP throughput. `particle.filter.debug=true` (the old
`local-ie-testing` default) retains every particle's ancestry and made per-record time climb without
bound — now off by default.

---

## Verified

| Claim | Evidence |
| --- | --- |
| Single-threaded replay is reproducible | `compare-replay-runs.py`: 270/270 records, 0 differing fields |
| Crew lookups match real operators | 210/270 records carry `managementRecord.assignedRunId`, 4 distinct runs |
| The 60 non-matching records are correct | That operator is rostered `ROUTE=MISC RUN=116`; `isValidRunId` rejects it (`VehicleInferenceInstance:613`) |
| Gating is inert with respect to inference | Output unchanged with all domains blocked |
| Production behaviour is untouched | `S3UtsOperatorAssignmentServiceImplTest` passes; `taskScheduler` and `obaClock` both profile-scoped |
| S3 input matches the local file | Streamed `08-45` slot: `read=266112 wrapped=266112`, same as the local copy |
| Output format matches the observer's | Spooled parts parse with the same per-vehicle analysis scripts |
| Upload path works end to end | A spooled part `aws s3 mv`'d to `ds-oba`, listed, removed |

## Not done

- **Multithreaded determinism.** Unsolved; see the determinism docs. Runs above used `-Doba.inference.threads=1`.
- **Hourly crew reload unexercised.** The committed fixture spans 12:45–12:49Z, inside one snapshot. Testing
  it needs a fixture crossing a boundary — 09:10Z, where the previous service date is dropped, is the
  interesting one.
- **Crew matching cannot be tested from the committed fixture.** Its operator designators are surrogates
  (`replay/scrub-fixture.py`), so lookups cannot hit. The 210/270 result used an unscrubbed local cut.
- **Cross-bundle runs.** Would need batch mode off and the switch driven from the replay clock; see
  `replay/EXTERNAL-DEPENDENCIES.md` entry 7.
- **Predictions engine.** Separate repo, not audited.
- **Pull-out.** `popi_*` snapshots exist in the UTS archive, but the wired bean is
  `DummyVehiclePulloutService` in production as well as replay.
- **In-run part rolling unexercised at scale.** No real run has reached the 250k-record roll yet; the
  roll/rename/upload cycle is verified only in an isolated smoke test. First multi-slot run exercises it.
- **EC2 run not yet performed.** The instance role's access to `mtalirr` (read) and `ds-oba` (write) is
  unverified — `replay/preflight.sh` on the instance is the test.
