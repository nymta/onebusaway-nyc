# EC2 replay scaling findings (2026-08-17 to 2026-08-18)

Investigation of replay throughput on `oba-nyc-replay` (`i-09cab5faf0f4ba4d6`, c7i.12xlarge, 48 vCPU /
24 physical cores). Builds on [REPLAY.md](REPLAY.md)'s M4 laptop baseline and
[HANDOFF.md](HANDOFF.md)'s open items. All full-fleet numbers use the `08-45` slot, unfiltered; all
Manhattan numbers use `-Dreplay.routeFilter='^M[0-9]'`.

## Fleet sizes

| Run | Window | Vehicles | Source |
| --- | --- | --- | --- |
| Manhattan filter | 300s (08-45 to 08-45) | 536 admitted | `replay: done` line, `(N vehicles admitted)` |
| Manhattan filter | 600s (08-45 to 08-50) | 548 admitted | same |
| Full fleet, unfiltered | 300s (08-45 to 08-45) | 4,939 distinct vehicles | `avg processing time` log line, `_vehicleInstancesByVehicleId.size()` (`VehicleLocationInferenceServiceImpl.java:939`) |

Full fleet carries roughly 9.2x the vehicles of the Manhattan filter (4,939 vs 536), consistent with the
~9.03x ratio already seen in dispatched-record counts (86,434 vs 9,575).

The Manhattan filter's route universe (matching `^M[0-9]` in the bundle's `routes.txt`) is 44 distinct
routes. Not all 44 are necessarily active in a given 5-minute window; this is the filter's target set,
not a per-run measurement.

## The inference engine is genuinely CPU-bound

Three independent measurements agree, on the unfiltered full-fleet run, 46 stripes (`oba.inference.threads`):

1. **Per-core CPU utilization** (`mpstat -P ALL 5`, averaged over the dispatch+drain window): 78.6%
   user, 21.1% idle system-wide. Broken out per core, all 48 cores individually fall between 75% and
   90% busy (idle range 9.9%-25.2%), no core sitting mostly idle. This rules out a scheduling problem
   where a few cores are pegged and the rest go unused.
2. **Per-thread snapshot** (`top -H`): roughly 43 of the 46 stripe threads (`pool-N-thread-1`) captured
   simultaneously at 73-91% CPU each, system-wide `%Cpu(s)` at 89.1% user / 10.9% idle.
3. **GC overhead is small**: 13.1s of GC pause out of a 178.3s run (7.4% of wall), matching REPLAY.md's
   prior M4 laptop finding of "GC ≤ 10% of wall" (`REPLAY.md:160`). Confirms the cost is compute, not
   allocation or collection.

This matches REPLAY.md's own thread-dump profile from the M4 laptop: "CPU-bound: thread dumps show zero
monitor contention, `FastMath.log` + `FoldedNormalDist`/`StudentT` dominate" (`REPLAY.md:159-160`).

## Particle count scales throughput roughly linearly

Default particle count is 200 (`ParticleFactoryImpl.java:62`, now overridable with
`-Doba.particle.count=<n>`, same convention as `-Doba.inference.threads`). Halving it to 100 (a 2.0x
reduction) on the same 08-45 slot, same box, same thread count:

| Fleet | Particles | Speed | rec/s inferred | Ratio vs. 200-particle baseline |
| --- | --- | --- | --- | --- |
| Full fleet | 200 (default) | 1.7x | 485 | — |
| Full fleet | 100 | 3.0x | 854 | 1.76x |
| Manhattan, 600s window | 200 (default) | 7.9x | 252 | — |
| Manhattan, 600s window | 100 | 14.1x | 449 | 1.78x |

A 2.0x reduction in particle count produced a 1.76-1.78x increase in throughput, consistently across two
very different fleet sizes. That is close to, but not quite, linear (roughly 88-89% of the theoretical
2.0x), likely because part of each record's cost (map-matching, bundle lookups, output serialization)
does not scale with particle count.

## vCPU / thread count scales throughput roughly linearly, with a socket caveat

Full-fleet run, same box, same particle count, only `REPLAY_THREADS` changed:

| Threads | Speed | rec/s inferred | Wall time |
| --- | --- | --- | --- |
| 32 | 1.2x | 347 | 249.2s |
| 46 | 1.7x | 485 | 178.3s |

Thread count increased 1.44x (32 to 46); throughput increased 1.40x (347 to 485 rec/s). Within 3% of
linear.

**Socket caveat.** This test ran entirely within one box's single NUMA node. AWS's `describe-instance-types`
API confirms vCPU/physical-core counts for the c7i family but does not expose socket count directly:

| Instance | vCPUs | Physical cores |
| --- | --- | --- |
| c7i.12xlarge (current box) | 48 | 24 |
| c7i.16xlarge | 64 | 32 |
| c7i.24xlarge | 96 | 48 |
| c7i.48xlarge | 192 | 96 |

Physical core count exactly doubles between `24xlarge` (48 cores) and `48xlarge` (96 cores). AWS's
Sapphire Rapids SKU in this family tops out around 48 physical cores per socket, so this doubling is
consistent with `24xlarge` and everything smaller staying single-socket, and `48xlarge` being the first
size that needs two sockets. This is a well-supported inference from the core-count pattern, not a
directly confirmed fact — worth checking against AWS's own documentation before committing budget to a
`48xlarge`. Practical takeaway: `c7i.24xlarge` (96 vCPUs / 48 cores) is the safer bet for extending this
result; `c7i.48xlarge` is where cross-socket memory latency becomes a real unknown, since every stripe
reaches the same shared ~4.5GB transit graph.

Also worth ruling out before assuming more threads always helps: OBA's original default stripe-count
formula, `2 + availableProcessors() * 20` (`VehicleLocationInferenceServiceImpl.java:168`), would give
962 stripes on this box's 48 vCPUs. That formula is designed for production's mostly-idle-stripe
workload (each stripe mostly waits on sporadic real-time GPS pings), not replay's continuously-saturated
batch workload. Oversubscribing 962 runnable threads onto 48 logical CPUs would add scheduling and
cache-thrashing overhead with no corresponding parallelism gain; expected to hurt replay throughput, not
help it. Not empirically tested.

## Multithreaded determinism: root cause found and fixed

HANDOFF.md listed multithreaded determinism as unsolved; only single-threaded reproducibility had been
verified (`compare-replay-runs.py`: 270/270 records, 0 differing fields, per REPLAY.md's Verified table).

**Root cause**: `ScheduleLikelihood.java` declared `POS_SCHED_DEV_CUTOFF` as a mutable `private static
double` field on a Spring singleton (`@Component`). Every particle-filter evaluation wrote this shared
field from its own vehicle's trip data (`ScheduleLikelihood.java:91-99`, pre-fix), then read it back a
few lines later (`getScheduleDevLogProb`, pre-fix lines 158/177) to compute a probability. With 46
concurrent single-thread stripes all calling this on the same singleton, one stripe's read could see a
value another stripe had just written for a different vehicle: a genuine cross-thread data race, not
just cache contention. This would silently corrupt some fraction of particle weights depending on
scheduling luck, exactly the kind of bug that produces non-deterministic multithreaded output.

**Fix** (commit `618576d79`): removed the static field. The cutoff is now a local variable in
`computeSchedTimeProb`, threaded through as a parameter to `getScheduleDevLogProb`. No behavior change
to the intended per-vehicle logic, only removes the cross-thread contamination. Same commit also added
the `-Doba.particle.count` override used above.

**Verification**: re-ran `local-loop/determinism/replay-determinism.sh` locally with
`OBA_INFERENCE_THREADS` set above 1 (exercising real cross-stripe concurrency on the fixture's 5
vehicles) against the fixed code. The two runs' outputs now match. Multithreaded replay is deterministic
with this fix in place.

## Open items / what to check next

- The `c7i.24xlarge` vs `c7i.48xlarge` socket boundary is inferred, not confirmed against AWS
  documentation.
- `REPLAY_THREADS=962` (the production default formula) has not been empirically tested; expected to be
  neutral-to-negative for replay throughput.
- Particle count below 100 is untested; unclear where accuracy degrades enough to matter for a
  correctness-sensitive benchmark.
- `FastMath` (commons-math 2.2) vs plain `Math` on JDK11 is a real candidate for further speedup but
  needs a microbenchmark, not just a swap (see the earlier code-review findings).
