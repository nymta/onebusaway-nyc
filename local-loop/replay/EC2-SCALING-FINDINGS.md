# EC2 replay scaling findings (2026-08-17 to 2026-08-18)

Investigation of replay throughput on `oba-nyc-replay` (`i-09cab5faf0f4ba4d6`, c7i.12xlarge, 48 vCPU /
24 physical cores). Builds on [WORKLOG.md](WORKLOG.md)'s M4 laptop baseline (2026-08-18 entry) and
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
3. **GC overhead is small**: 13.1s of GC pause out of a 178.3s run (7.4% of wall), matching WORKLOG.md's
   prior M4 laptop finding of "GC ≤ 10% of wall" (2026-08-18 Benchmarks entry). Confirms the cost is
   compute, not allocation or collection.

This matches WORKLOG.md's own thread-dump profile from the M4 laptop: "CPU-bound: thread dumps show zero
monitor contention, `FastMath.log` + `FoldedNormalDist`/`StudentT` dominate" (2026-08-18 Benchmarks entry).

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

Physical core count exactly doubles between `24xlarge` (48 cores) and `48xlarge` (96 cores). Checked
against outside sources (2026-08-18): the exact chip AWS uses here, Intel Xeon Platinum 8488C, is a
48-core-per-socket part. Independent sources describing AWS's Nitro bare-metal `metal-24xl`/`metal-48xl`
split consistently say `metal-24xl` is one physical dual-socket host split into two isolated
single-socket bare-metal instances, and `metal-48xl` is the same physical host passed through whole,
both sockets. That lines up exactly with the core-count doubling here. AWS's own instance-type
specification pages confirm vCPU/core/thread-per-core counts but do not list socket count for any
instance type, so this is corroborated by multiple independent secondary sources, not stated outright
by AWS itself. Practical takeaway unchanged: `c7i.24xlarge` (96 vCPUs / 48 cores) is the safer bet for
extending this result; `c7i.48xlarge` is where cross-socket memory latency becomes a real unknown, since
every stripe reaches the same shared ~4.5GB transit graph.

Also worth ruling out before assuming more threads always helps: OBA's original default stripe-count
formula, `2 + availableProcessors() * 20` (`VehicleLocationInferenceServiceImpl.java:168`), would give
962 stripes on this box's 48 vCPUs. That formula is designed for production's mostly-idle-stripe
workload (each stripe mostly waits on sporadic real-time GPS pings), not replay's continuously-saturated
batch workload. Oversubscribing 962 runnable threads onto 48 logical CPUs would add scheduling and
cache-thrashing overhead with no corresponding parallelism gain; expected to hurt replay throughput, not
help it. Not empirically tested.

## Cost to replay one week of full-fleet data

Estimates below hold particle count at the default (200) and only vary vCPU count and machine count,
extrapolating from the measured 1.7x speed at 46 threads on `c7i.12xlarge`. On-demand pricing, us-east-1,
Linux, checked 2026-08-18: `c7i.12xlarge` $2.142/hour, `c7i.24xlarge` $4.284/hour (exactly double, AWS
prices this family linearly per vCPU). Stopped-instance cost is EBS storage only, compute is not billed
while stopped; the current benchmark box carries 130 GB of gp3 (30 GB root, 100 GB data) at $0.08/GB
month, $10.40/month per machine, charged whether the machine is running or stopped.

Multi-machine rows shard by vehicle, each machine processes an equal fraction of the fleet for the full
week, running in parallel, then results are combined. Speed per machine is assumed to scale linearly
with the smaller per-machine fleet share; this is the same assumption validated within about 3% for
thread count, but not directly measured for fleet-share sharding. It is a conservative (upper bound on
cost) assumption: the measured Manhattan-vs-full-fleet comparison suggests high-volume runs are
somewhat *more* efficient per vehicle than low-volume ones, so actual sharded runs likely finish a bit
faster and cost a bit less than the numbers below.

| Configuration | vCPUs | Estimated speed | Wall-clock time for 1 week of data | Running cost | Stopped cost |
| --- | --- | --- | --- | --- | --- |
| 1x `c7i.12xlarge` (48 core) | 48 | 1.7x | 98.8 hours (4.1 days) | ~$212 | $10.40/month |
| 1x `c7i.24xlarge` (96 core) | 96 | ~3.4x | 49.4 hours (2.1 days) | ~$212 | $10.40/month |
| 2x `c7i.12xlarge`, fleet split in half | 96 (48 each) | ~3.4x combined | 49.4 hours (2.1 days) | ~$212 | $20.80/month |
| 4x `c7i.12xlarge`, fleet split in quarters | 192 (48 each) | ~6.8x combined | 24.7 hours (1.0 day) | ~$212 | $41.60/month |

The running cost is close to constant across every configuration, about $212 regardless of how many
vCPUs or machines are used. This falls directly out of the linear-scaling assumption: doubling vCPUs
(by any means, a bigger box or more boxes) roughly halves wall-clock time while roughly doubling the
hourly rate paid, so the product, total cost, stays flat. What changes is turnaround time, not price.
Going bigger or wider is close to free from a compute-cost standpoint; the real cost of more machines is
the added stopped-storage bill (which scales with machine count) and, in the `c7i.24xlarge` case, the
unconfirmed single-socket assumption from the section above.

*Cross-check against Ranajay's plan doc (`Replay Test Harness Plan - ReplayDriver Version.md`,
2026-08-13): its quarter-scale estimate, ~$3,100 on-demand for 90 days on `c7i.8xlarge` (32 vCPU,
~5,800-bus fleet, 200 particles), normalizes to ~$241/week, within about 12% of the ~$212 above. The
remaining gap tracks the fleet-size difference (5,800 vs. this doc's 4,939 measured vehicles), not a
throughput disagreement.*

## Preliminary: shared-pool dispatch vs. fixed stripes (2026-08-26)

`VehicleLocationInferenceServiceImpl`'s vehicle dispatch defaults to a shared N-thread pool now (any
thread can pick up any vehicle's pending work) instead of one fixed single-thread stripe per vehicle -
opt out with `-Doba.inference.sharedPool=false`. Motivated by the idle-stripe cost found in the sharding
investigation above, but the same imbalance exists on a plain non-sharded run too, just as a smaller,
structural cost (uneven per-stripe vehicle load) rather than a whole shard's stripes sitting idle.

Same-window (2026-08-10 08:00-08:55), non-sharded, unseeded comparison against `_stripes`: record counts
and the input's own timestamp-skew warning counts matched exactly (dispatch order held), and:

| | `_stripes` | shared pool |
|---|---|---|
| total wall time | 1323.7s | 1119.2s |
| speed multiplier | 2.7x | 3.2x |
| inferred rec/s | 761 | 900 |
| drain phase | 123.1s | 2.3s |

~15% faster, almost entirely from the collapsed drain phase. Preliminary: only tested non-sharded so
far; a sharded run to confirm the larger-magnitude fix (the case this was actually built for) is still
open. Full detail: [WORKLOG.md](WORKLOG.md), 2026-08-26.

## Open items / what to check next

- The `c7i.24xlarge` vs `c7i.48xlarge` socket boundary is corroborated by independent secondary sources
  (Intel's chip spec, Nitro bare-metal write-ups), not stated outright in AWS's own documentation.
- `REPLAY_THREADS=962` (the production default formula) has not been empirically tested; expected to be
  neutral-to-negative for replay throughput.
- Particle count below 100 is untested; unclear where accuracy degrades enough to matter for a
  correctness-sensitive benchmark.
- `FastMath` (commons-math 2.2) vs plain `Math` on JDK11 is a real candidate for further speedup but
  needs a microbenchmark, not just a swap (see the earlier code-review findings).
- Sharded run to confirm the shared-pool dispatch fix at the magnitude it was built for (see above).
