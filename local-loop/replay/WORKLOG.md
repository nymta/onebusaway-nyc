# Worklog: `marcos/replay-harness`

## 2026-08-18, as of `e253dbf12`

### Changes vs. `timothy/ec2-oba-improvements-v2`

`timothy/ec2-oba-improvements-v2` (tip `5a5efcf9`, "Archive the predictions stream to S3 for offline
comparison") is fully contained in this branch. Every commit below is additional; nothing from
Timothy's branch is missing or overwritten.

Where a commit changes code, whether it is active only under a replay flag or changes default
(production) behavior is called out explicitly, since that distinction determines how carefully it
needs review before merging back.

| Commit | Theme | Purpose | Files / LOC | Inference-engine impact |
|---|---|---|---|---|
| `61657d1a9` | Replay/local-dev harness tooling and docs | Scripts, docs, and fixture-prep tools for running and observing replay locally: build helpers, the observer, `RUNBOOK`/`REPLAY`/`OBA-BUGS` docs, the laptop `replay.sh` driver | 20 files, +1,681/-20 | **Zero.** Nothing under `src/main`; not compiled into any deployed artifact. |
| `ce747e13b` | Virtual clock for replay | `MutableClock` drives the engine's notion of "now" from each record's own timestamp instead of the wall clock, so replay runs as fast as inference allows rather than pacing at 1x | 1 file, +104 | **Zero outside replay.** Only resolves under the `replay` Spring profile; any other profile keeps `Clock.systemUTC()`, and fails loudly rather than silently if a replay driver is selected without it. |
| `33f3a26e7` | Per-vehicle striping; configurable thread/particle counts | Each vehicle hashes onto one single-thread stripe, so records process in arrival order instead of racing through a shared pool (which was dropping most out-of-order arrivals). Stripe count (`-Doba.inference.threads`) and particle count (`-Doba.particle.count`) are launch-time overrides | 2 files, +173/-36 | **Always-on** (the striping architecture itself changes production's concurrency model, not just replay's), **but the overrides are no-ops unless explicitly set**, so default values are unchanged. |
| `62a1a91eb` | Multi-threaded determinism | Three independent non-determinism causes, found and fixed: (1) one shared random generator across all vehicles, replaced by `InferenceRng`'s per-vehicle seeded streams; (2) `BlockConfigurationEntryImpl`/`TripEntryImpl` had no value-based `hashCode`, so `HashSet`/`HashMap` iteration order depended on memory addresses (shadow copies add a content-based `hashCode`, nothing else); (3) `ScheduleLikelihood.POS_SCHED_DEV_CUTOFF`, a mutable static field hit by every stripe concurrently, now a local variable. Verified with `replay-determinism.sh`: multi-threaded output now matches the single-threaded baseline | 16 files, +2,460/-23 | **Always-on**, and a genuine correctness improvement in all three cases, not just a replay concern. A shared RNG and address-dependent hash ordering are latent bugs in any concurrent use, not only in replay. |
| `6e1d40819` | Gate wall-clock periodic tasks off under replay | `GatedTaskScheduler`/`Replayable`/`ReplayDomain` let a background task (telemetry polling, config refresh, etc.) opt out of scheduling when a replay driver is active, since those tasks don't mean anything against a virtual clock. Each affected task gets one `@Replayable(...)` annotation | 16 files, +307/-2 | **Low outside replay.** Annotations are inert unless `GatedTaskScheduler` is wired in, which only happens under the replay profile. |
| `f2f010a5a` | UTS crew assignments from an S3 archive | `S3UtsOperatorAssignmentServiceImpl`/`CrewSnapshotIndex` read pre-fetched UTS operator/run assignment snapshots from S3, as of the replay clock, instead of calling the live TDM | 5 files, +413/-13 | **Medium.** Selected via the `oba.crew.service.class` property; `OperatorAssignmentServiceImpl`'s default behavior is unchanged unless that property is set. |
| `e253dbf12` | Replay driven from archived S3 GPS, with a route filter and S3 output | `ReplayFileInputTask` reads archived `bustechGps` records from S3 (through a FIFO fed by `run-replay.sh`, in order, on the virtual clock), optionally restricted by `-Dreplay.routeFilter` for a faster correctness smoke test. `S3OutputQueueSenderServiceImpl` writes inferred locations back to S3 instead of publishing live. `run-replay.sh`'s default stripe count derives from the box's vCPU count minus 2, not a hardcoded number | 5 files, +914/-10 | **Zero outside replay.** Only active under `-Die.listener=ReplayFileInputTask -Dspring.profiles.active=replay`. |
| `eb9958ea8` | EC2 scaling benchmarks | Documents fleet sizes, CPU-bound evidence, particle-count and vCPU scaling results (with a socket caveat), and the multithreaded determinism fix, from the c7i.12xlarge benchmark box | 1 file, +130 | **Zero.** Documentation only. |

#### Changes to default (production) behavior

Two commits change default behavior, not just replay behavior, and both are correctness fixes rather
than new features:

- **`33f3a26e7`**: the striping architecture itself. Every vehicle now processes on a dedicated
  single thread instead of a shared pool. This is a behavior change in production, motivated by a real
  bug (a shared pool dropping most out-of-order arrivals), not an optional feature.
- **`62a1a91eb`**: the per-vehicle RNG and the two `hashCode` fixes apply everywhere the affected
  classes are used, not just under replay. `ScheduleLikelihood`'s race fix is the same: it changes the
  schedule-likelihood probability computation for every concurrent run, replay or production.

Everything else in this branch is either purely additive tooling (zero footprint in a deployed
artifact) or gated behind a system property or Spring profile that defaults to today's behavior.

#### Verified

- `onebusaway-nyc-vehicle-tracking-webapp` and `onebusaway-nyc-gtfsrt-webapp` both build clean.
- Multi-threaded replay determinism, verified with `local-loop/determinism/replay-determinism.sh` at
  `OBA_INFERENCE_THREADS` above 1, output matches the single-threaded baseline.

### Scheduled/periodic task audit

`GatedTaskScheduler` only intercepts tasks scheduled through whichever Spring `TaskScheduler` bean is
actually wired in. Confirmed by tracing `web.xml`'s context load order (`data-sources.xml` loads last
of the three root context files), so its replay-profile `taskScheduler` bean (`GatedTaskScheduler`)
genuinely wins over the separate, always-plain `taskScheduler` beans defined in `onebusaway-nyc-util`
and other modules. The `@Replayable` annotations on util/transit-data-federation classes are real and
enforced, not inert.

Five domains (`ReplayDomain.java`), with what is actually tagged under each:

- **`INFERENCE_INPUT`**: "writes state the particle filter reads." Crew assignments
  (`S3UtsOperatorAssignmentServiceImpl`), vehicle pullout (`VehiclePulloutServiceImpl`),
  unassigned-vehicle tracking (`UnassignedVehicleServiceImpl`), TDM operator assignments
  (`OperatorAssignmentServiceImpl`).
- **`CONFIG`**: "refreshes configuration, which can change behaviour anywhere mid-run."
  `ConfigurationServiceImpl`, `CapiDaoHttpImpl`, part of `HTTPListenerTask`, part of
  `UnassignedVehicleServiceImpl`.
- **`BUNDLE`**: "discovers or switches bundles. A switch resets per-vehicle state."
  `BundleManagementServiceImpl`'s discovery and switch timers.
- **`OUTPUT`**: "output plumbing, or fields written onto the published record but not read by
  inference." Two secondary scheduled methods on `OutputQueueSenderServiceImpl` (not its main flush
  loop, which deliberately bypasses the gate through its own executor, since output has to keep
  flowing regardless), and `VehicleAssignmentServiceImpl`.
- **`TELEMETRY`**: "neither read by inference nor published." The three `Apc*` classes,
  `NycRouteTypeService`, part of `HTTPListenerTask`.

**`-Doba.replay.tasks` is never set in `run-replay.sh`.** `GatedTaskScheduler` defaults that to an
empty domain set, so today every `@Replayable` task is unconditionally dropped, regardless of domain.
This matches REPLAY.md's own tested claim that gating is inert with all domains blocked. The
five-domain taxonomy is currently unused in practice; nothing is being selectively re-enabled.

**The 30-minute crew-refresh task** (`S3UtsOperatorAssignmentServiceImpl`'s `CrewRefreshTask`,
`30 * 60` seconds) is covered twice over, not missed. It is `@Replayable(INFERENCE_INPUT)`, and
separately, when a local snapshot directory is configured (the actual replay path), the method
returns early before ever scheduling anything. The crew roster becomes a stateless lookup keyed on
the virtual clock instead of a periodic refresh.

**STIF/bundle case**: correct as-is, not a risk. GTFS/STIF are consumed at bundle-build time, not
runtime, so there is no live refresh to gate in the first place. The real safety comes from the
precondition that a replay window sits entirely inside one bundle's validity period, enforced by
`replay-determinism.sh` asserting exactly one bundle directory present, not from batch mode or from
gating the switch timer.

**Stubbing audit**: nothing stubbed under the replay profile diverges from current production.
`DummyVehiclePulloutService`/`DummyUnassignedVehicleServiceImpl` and the empty TDM host config are
already true in production; replay inherits them rather than introducing new stubbing.

**Conclusion**: no wall-clock task escapes the gate, and no gated task is being dropped when it
should instead run against virtual time. The crew solution (a stateless lookup keyed on the virtual
clock) is a better pattern than rescheduling against virtual time would have been, not a gap.

## 2026-08-19

### 48x vs 24x benchmark: does a second socket help?

To check empirically, the current `oba-nyc-replay` box (`c7i.24xlarge`, single socket, 96 vCPUs) was
cloned onto a fresh `c7i.48xlarge` (two sockets, 192 vCPUs; `lscpu` confirms NUMA node0=0-47,96-143,
node1=48-95,144-191) by snapshotting its root + data EBS volumes and attaching the clones to the new
instance - this carries over the built repo, JDK, bundle, and crew snapshots without re-running the
bootstrap.

First look was misleading: both boxes reported roughly the same `speed` (virtual-time/wall-time,
~4x) in `replay-monitor.log`, which read as zero benefit from doubling vCPUs. That comparison doesn't
hold up: `speed` is diluted by a run's drain-phase tail (the virtual clock freezes once dispatch ends,
but wall time keeps advancing while the last stripes finish), which eats a much larger fraction of a
short test run's total time than a multi-day job's - so a finished 15-minute test's `speed` and a
still-mid-dispatch multi-day job's `speed` are not measuring the same thing. The comparable number is
`aggregate_rec_s` (fleet-wide completions/wall-time, no virtual-clock semantics), and for the real
per-record cost, `VehicleLocationInferenceServiceImpl.java:941-951`'s "avg processing time" line (wall
time inside `ProcessingTask.run()` on the stripe thread itself - compute only, not queue wait).

| Box | avg processing time/task | stripes active (of total) | aggregate rec/s |
|---|---|---|---|
| 24x (96 vCPU, 1 socket) | ~66-72ms | 67-78 / 94 | ~847-855 |
| 48x (192 vCPU, 2 sockets) | ~101-110ms | 132-145 / 190 | ~1097-1158 |

Stripe count roughly doubled (94→190, 2.02x) but per-task time rose ~1.5x with it, netting only
**~1.28-1.36x** aggregate throughput, not 2x. Both boxes were confirmed CPU/inference-bound, not
S3-download-bound (throttled% 71-85%, i.e. the reader is mostly waiting on inference to drain, not on
the network); GC showed no storm signature either. The signature (per-task time rising with core
count on the same code, same warm-up pattern) points at cross-socket NUMA memory latency on the
shared ~4.5GB transit graph - a mostly-read structure every stripe's particle-filter step touches, with
no `-XX:+UseNUMA` or NUMA-aware allocation anywhere in the launch flags, so the heap likely landed
first-touch on one socket, leaving the other socket's stripes paying a remote-access penalty on every
read.

The fix that actually removes the penalty is splitting the fleet across two NUMA-pinned JVMs
(`numactl --cpunodebind=N --membind=N`, one per socket), which needs a new `-Dreplay.vehicleShard=i/n`
filter in `ReplayFileInputTask` (same shape as `passesRouteFilter`, simpler - no admit-once-then-keep
semantics needed since a vehicle's hash never changes) plus `run-replay.sh` changes (distinct Jetty
ports/output dirs/per-shard thread counts so two instances can run on one box without colliding).
Scoped, not started.

### Sanity check: currently-running week-long replay (`2026-08-10/02-00` to `2026-08-17/01-55`)

Before letting the 24x box's week-long job run for another ~2 days, spot-checked its S3 output
(`s3://ds-oba/replay/inference-outputs/2026-08-10_02-00-to-2026-08-17_01-55-20260819T055414Z/`, ~26%
through virtual time at check time) for anything indicating a broken run: part naming/rolling cadence
(15-min virtual buckets, sizes climbing 558B→~46MB tracking a believable diurnal ridership curve), JSON
validity (0 malformed lines across a 252,256-record sample), exact-duplicate `(vehicleId,
recordTimestamp)` pairs (0), phase distribution (74% `IN_PROGRESS`, rest a sane `DEADHEAD`/`LAYOVER`
split), distinct vehicle count in one 15-min window (~4,808). Nothing here indicates the run is broken.

Three things worth knowing, none blocking:

1. **`depotId` is null in every sampled record.** Expected, not new: `getAssignedDepotForVehicleId`
   (`VehicleAssignmentServiceImpl.java:198-201`) reads an in-memory map fed only by a live TDM HTTP
   call (`:80`, `getVehicleListForDepot`), refreshed by a scheduled task that is itself gated off under
   replay (`OUTPUT` domain, `:136`) - and even without that gate, the refresh only re-fetches depots
   already present in a map that nothing in the inference path ever seeds (`:117-126` iterates its own
   possibly-empty `keySet()`; the only method that adds an entry, `getAssignedVehicleIdsForDepot`, is
   never called here). Unlike UTS crew data, depot assignment has no snapshot-based replay path at all.
   This is working as intended, not a gap to close: Timothy evaluated fetching the same depot data from
   SPEAR (another MTA system) and found it made no measurable difference, so it was never merged into
   his branch and replay doesn't fetch it either. Worth revisiting only if depot data becomes a real
   blocker later.
2. **~0.066% (166/252,256) of records in the sampled window have out-of-NYC-bbox coordinates**, a
   handful of exact values repeated identically across different vehicles and timestamps, always
   `DEADHEAD_BEFORE`, with `observedLatitude/Longitude` exactly equal to the bad
   `inferredLatitude/Longitude` - confirms the bad value comes in on the raw archived GPS (or its
   decode), not introduced by the particle filter.
3. **~1% of records in a given 15-min output part have a `recordTimestamp` outside that part's own
   nominal window.** `S3OutputQueueSenderServiceImpl.enqueue()`'s roll condition only advances the
   bucket forward (`bucketStartFor(ts) > _currentBucketStart`), so a record that finishes computing
   late (expected under the striped/concurrent engine - completion order isn't perfectly
   timestamp-ordered) gets filed under whichever bucket happens to be open, not its own true bucket.
   Not data loss, just a labeling caveat for anyone doing strict per-file time-window analysis on the
   output later.

**Conclusion**: nothing found warrants stopping the run.

## 2026-08-21

### 48x two-JVM NUMA split: what actually happened

Built the fleet-split design from the 08-19 entry: `-Dreplay.vehicleShard=i/n` in `ReplayFileInputTask`
(hashes vehicles across n independent processes, same shape as `passesRouteFilter` but no admission
cache needed, since a vehicle's hash never changes), plus `run-replay.sh --shard i/n` to wrap each
process in `numactl --cpunodebind=i --membind=i`, offset its Jetty port, halve its thread default
correctly (per-shard vCPU share minus 2, not the whole-box default divided by n - those give different
numbers), and suffix its `LABEL`/`OUT_DIR`/`OUT_S3`/CloudWatch `RunId` so two shards never collide.

Hit one real bug before it ran clean: both JVMs opened the same local HSQLDB file
(`${bundle.location}/org_onebusaway_database`, wired in unconditionally by the container framework's
bean graph regardless of profile - nothing replay-specific about it), and the second process to start
lost the file-lock race and died in `SessionFactory` bootstrap. Fixed by giving each shard a shadow
bundle directory (symlinks to the real, large, read-only pick data, so nothing gets copied, but no
`org_onebusaway_database*` sibling files) so each JVM gets its own private copy of that scratch DB.

Once fixed, per-task processing time (`VehicleLocationInferenceServiceImpl.java:941-951`'s "avg
processing time") came back at **~50-63ms per shard** - not just back to the single-socket 24x
baseline (~66-72ms), better than it, plausibly because each shard's smaller vehicle population is a
smaller, more cache-friendly working set on top of the NUMA penalty being genuinely gone. The NUMA fix
is confirmed at the mechanism level, not just inferred from a throughput number.

Combined throughput still landed short of the ~1.8-1.9x hoped for: ~1275-1400 rec/s combined
(24x baseline ~850-900), roughly 1.5-1.6x. Traced to stripe utilization, not compute speed: each shard
only kept ~40 of its 94 stripes busy at once (down from ~64-76/94 for the single-JVM full-fleet run).

Leading hypothesis: the reader's backlog stays pinned at `maxOutstanding=2000` almost continuously
(`tasks pending` sits at ~2000 in every sample; the reader reports 84% throttled), which bounds how far
ahead in virtual time it can ever get. Within that fixed, narrow window, the vehicle-shard filter
discards half of every distinct vehicle before dispatch - so for the same window width, a shard sees
roughly half as many distinct vehicles as the single-JVM run did, which halves how many stripes can
ever be simultaneously busy. Matches the observed magnitude (~70 -> ~40). Checked and ruled out a
sharper alternative: correlated hashing between the shard filter and stripe assignment, which would
deterministically starve half the stripes given `_stripes.length=94` is even. Decompiled
`AgencyAndId.hashCode()` (`93 + agencyId.hashCode() + id.hashCode()`) against the filter's
`String`-concatenation hash and confirmed they're different formulas, not the same value reduced two
ways. Other candidates not yet checked: uneven hash distribution across 94 stripes for ~2500 vehicles,
rejected records (`rejected=358,705` vs `dispatched=168,061` in one run) concentrating on fewer
vehicles than raw volume suggests, and the "processing X of 94" log line only sampling once every 1000
global completions (a possible bias if completions come in bursts).

(Also reconfirmed: comparing this run's own `speed` field against the 24x box's actual 30.5-hour
full-week average is the same non-comparison as before - short-run drain/warm-up dilution versus a
clean multi-day steady state. `aggregate_rec_s` is still the number that means anything across runs of
different length.)

### `maxOutstanding`'s real ceiling, and an untried lever

`maxOutstanding` sits at 2000 not for throughput reasons but to stay under
`BundleManagementServiceImpl.MAX_EXPECTED_THREADS=3000` (`:84`) - a defensive sanity ceiling on live
bundle-switch bookkeeping (`registerInferenceProcessingThread`, `:349-357`), sized for production load
that was never expected to get close to it. Its cleanup (`removeDeadInferenceThreads`, `:548-562`)
only removes *finished* tasks; if outstanding ever sits permanently above 3000 nothing is ever finished
long enough to prune, and the list grows unbounded for the rest of the run - the O(n²) failure already
documented once in this project.

Replay never requests a bundle switch (single fixed bundle for the whole window, already an
established precondition), so this bookkeeping is dead weight here. Plan: gate
`registerInferenceProcessingThread` off entirely under replay (one early return, not a higher number -
raising the threshold instead trades the CPU-cost problem for an unbounded memory leak, since nothing
else would ever prune the list). That decouples `maxOutstanding` from this ceiling
(`getOutstandingTaskCount()` is separate, cheap, JDK-native per-stripe bookkeeping with no growth
problem of its own), opening room to raise it and widen the lookahead window against the filter-dilution
problem above. Not yet tried. Caveats: unmeasured, not a guaranteed fix (could plateau on some other
ceiling); and disabling the tracking bets on the audited assumption (no code path ever calls
`changeBundle()` during replay) continuing to hold, not on a proof that it always will.

### Considered alternative: split by time instead of by vehicle

Splitting the calendar window instead of the fleet - each shard runs the *full* fleet for a disjoint
half of the week (plain `--from`/`--to`, no vehicle filter needed at all) - would give each shard the
same per-vehicle concurrency density as the single-JVM baseline instead of a diluted one, which should
close most of the utilization gap above. It would also simplify output handling: two disjoint,
sequential time ranges don't need the per-record interleave-by-timestamp merge the vehicle-split
design requires, since there's no overlap to reconcile.

The real cost: whatever's held in memory per vehicle (`VehicleInferenceInstance`'s particle
population, `_previousObservation`, motion/journey state) lives in one JVM's heap and has no
serialization or handoff path to another process. Any vehicle actively mid-trip at the split boundary
loses that state entirely and reconverges cold in the second shard - not a new *kind* of event (a
genuine reporting gap already resets a vehicle's particle filter today), but forced onto potentially
most of the active fleet simultaneously, at one artificial instant, rather than scattered organically
across whenever each vehicle happens to have a real gap.

One mitigation considered and rejected: pick the split boundary during a low-activity window (e.g.
~3-4am) to minimize how many vehicles are affected. Rejected because it specifically degrades
whatever's running overnight - and late-night service is a realistic thing to actually want accurate
data on, not a throwaway period. Concentrating the accuracy cost exactly on the window someone might
later want to study defeats the point.

A second mitigation is still open, but not yet trustworthy as stated: overlap the two shards' windows
and discard the second shard's output until its particles have had time to reconverge, keeping only
its output past that warm-up buffer. This assumes the two shards would agree on trip/block matching by
the end of the overlap - but multithreaded determinism has only been verified for the *same* inputs
under different thread interleaving, over a small window (`replay-determinism.sh`). It has not been
verified for two runs of the same window where one shard has real per-vehicle history going into it
and the other starts cold. Whether a cold-started shard's particle filter actually reconverges to the
*same* trip/block match the continuously-run shard would have reached, by the end of whatever overlap
is chosen, is unverified - not something to assume safe without checking.
