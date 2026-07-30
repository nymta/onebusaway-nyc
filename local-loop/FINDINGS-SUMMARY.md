# EC2 OBA vs MTA production GTFS-RT — consolidated findings

**Single entry point** for everything learned from the feed-comparison work, 2026-07-22 → 2026-07-30
(8 live runs + 4 confounder-free archive runs + a Mongo history study), including the two coverage
fixes deployed on 07-29 and the freshness regression they caused, found on 07-30. Every discrepancy
class, its root cause, the evidence, and the fix.

> ⚠️ **Current state (2026-07-30): coverage is at parity and trip matching is at its best ever, but
> the deployment is running with 69 s-old position anchors instead of 15 s, because the +30% fleet from
> the coverage fix pushed the host past its peak capacity and load-shedding now fires at every peak.
> Near-term ETA accuracy has roughly halved as a result. Restoring freshness is the top action (§2c,
> §8 #1), and ETA numbers measured before and after 2026-07-29 are not comparable until it is.**

This document *summarises*; it does not replace the sources:

| source | what it holds |
|---|---|
| [`COMPARISON-RUNBOOK.md`](COMPARISON-RUNBOOK.md) | how to re-run everything; baselines; confounder checklist |
| [`feed-comparison-report.md`](feed-comparison-report.md) | running log, one dated section per run + the 07-22 code deep-dive |
| [`discrepancy-investigation-2026-07-27-pm.md`](discrepancy-investigation-2026-07-27-pm.md) | the deepest single write-up (§1–11): B1, bias decomposition, history depth |
| `feed-comparison-YYYY-MM-DD*.md`, `archive-window-*.md`, `discrepancy-detail.txt`, `bias-*.txt` | raw tool output. The fixed-window evidence for §2c is `archive-window-2026-07-29-vs-30-am-NYCT.md` and `archive-window-pm-nyct-series.md` |
| [`EC2-DEPLOYMENT.md`](EC2-DEPLOYMENT.md), [`PRODUCTIONIZING.md`](PRODUCTIONIZING.md) | host/ops and the prediction-engine model |

---

## 1. What is being compared

- **Ours:** `http://ec2-52-70-255-34.compute-1.amazonaws.com/{tripUpdates,vehiclePositions}` — a
  single-host experimental OBA-NYC deployment. **Same codebase and same BusTech AVL feed as prod**,
  differing in three ways that matter: **how much of the shared GPS stream we process** (§1a — *not* a
  finer GPS source; that is a common misstatement), **no UTS operator→run assignments**, and **Mongo
  prediction history only since 2026-07-21** (MTABC only since 2026-07-29). The MTABC-STIF gap that
  dominated earlier rounds was **closed 2026-07-29**; both agencies are now in the bundle. Prediction
  weights 20/40/40 — *verified identical to prod's TDM config*.
- **Prod ("BusTech prod"):** `http://gtfsrt.prod.obanyc.com/...` — the incumbent production Bus Time
  GTFS-RT. Public, no API key. **Naming: both deployments are MTA's**, so "prod" here means the
  vendor-operated production instance (same OneBusAway codebase), not "MTA" as opposed to us. Where
  this document says *MTA* it means the agency or its data owners (MTA Bus Company, an MTA access
  grant, MTA's TDM), not the production system.
- Both emit bare GTFS ids and identical STIF-style `trip_id`s (**100% parity vs the C6 public GTFS**),
  so the feeds join directly on vehicle/trip/stop.
- **Goal:** an A/B test — processing more of the shared GPS stream (§1a) plus warmed history should eventually *beat* prod. Alignment with
  prod is therefore a **proxy for sanity, not the target**. Nothing here says which engine is more
  accurate; that needs the ground-truth harness (PRODUCTIONIZING §5).

### 1a. What the GPS difference actually is — corrected 2026-07-30

Earlier versions of this document, the runbook, and `PRODUCTIONIZING.md` framed this as **"we run 5 s GPS
vs prod's 30 s"**, implying a ~6× advantage. Measurement does not support that, and the distinction
matters because this is the experiment's independent variable.

| | value | source |
|---|---|---|
| BusTech AVL source rate — **identical for both systems** | **5.6 s per bus** | 533,648 fixes / 4,986 vehicles / 600 s, replayed from the `bustechGps` archive |
| fixes we *admit* to inference after the deadband | **286/s of 889/s (32%)** | `simulate-deadband.py`, real peak window |
| our effective sampling — **moving** bus | **~11 s** | rate cap 7 s > source 5.6 s, so every other fix is skipped |
| our effective sampling — **stationary** bus | **~30 s** | held by the `maxAgeSec` failsafe, since it never moves 10 m |
| our effective sampling — fleet mean | **21.4 s** | measured |
| MTA's documented pipeline rate | ~30 s | MTA documentation |
| **published position anchor age — the end-to-end number** | **ours 16–19 s vs MTA's 39–48 s (~2.4×)** | `probe-coverage.py`, `analyze-anchor-age.py` |

**The accurate claim: we do not have finer GPS — we have the same GPS, and we process and republish more
of it.** Both systems drink from the same 5.6 s BusTech stream; MTA down-samples to ~30 s, and we
down-sample less. The real, defensible, end-to-end statement is the **published anchor age: ~2.4×
fresher**, not 6×.

**Two nuances worth carrying forward:**

1. **The fleet-mean 21 s figure understates us**, because the deadband is *designed* to skip redundant
   fixes from buses that have not moved 10 m. A stationary bus genuinely does not need reprocessing — its
   position is unchanged. For a **moving** bus, which is the case that matters for link travel times, the
   sampling is **~11 s**. So the mean is dragged down by correct behaviour, not by coarseness.
2. **The rate cap is not the binding limit once it drops below the source rate — the distance test is.**
   At `minIntervalSec=3` only 0.1% of fixes are dropped by the cap while **52% are dropped as "not
   moved"**. So no rate-cap setting alone recovers the full 5.6 s stream; that needs `minMeters` lowered
   too, which costs far more load (`0 m / 5 s` admits **822 fixes/s**, roughly 2× even the resized host's
   ceiling).

**Opportunity now that the host has been resized (§2d).** `minIntervalSec` 7 → **5** would take a moving
bus from ~11 s to the full **5.6 s source rate** — genuinely realising the premise this A/B was built to
test — at **405 fixes/s**. That sits at the top of the resized host's estimated ~380–405/s ceiling, so it
is *plausible but marginal*, and the ceiling is an extrapolation from the 32-core measurement rather than
a measured value. **Sequence: confirm the PM peak is healthy at the current 7 s first (§2d), then try 5 s
and re-measure.** Do not do both at once.

---

## 2. Headline: coverage solved, freshness now the binding constraint

> **2026-07-29: both coverage defects were fixed and deployed. The network is at parity with prod —
> 0 routes missing, 99% of prod's vehicles, and trip matching is the best it has ever been (98.4% on
> 07-30).** See §2b for what changed.
>
> **2026-07-30: that fix cost prediction freshness.** The +30% fleet pushed the host past its peak
> capacity; our position anchors went 15 s → 69 s and near-term ETA MAE roughly doubled on the
> *unchanged* NYCT fleet. See §2c — this is now the most important open item.

| metric | 07-22 mid | 07-23 AM | 07-24 PM pk | 07-27 AM pk | 07-27 PM pk | 07-29 PM pk | **07-30 AM pk** |
|---|---|---|---|---|---|---|---|
| same `trip_id` | 97.3% | 98.1% | 96.5% | 96.7% | 97.1% | 98.0% | **98.4%** |
| ETA MAE — ALL | 53 s | 45 s | 41 s | 37 s | 37 s | 49 s | 44 s |
| — 0–5 min | 22 | 17 | 16 | 18 | 16 | 23 | 23 |
| — 15–30 min | 55 | 47 | 36 | 36 | 34 | 43 | 42 |
| — 30+ min | 81 | 73 | 70 | 59 | 59 | 79 | 72 |
| ETA median — ALL | +2 | +4 | −4 | −7 | −4 | −11 | −13 |
| ETA median — 30+ min | +10 | +14 | −13 | −18 | −10 | −21 | **−28** |
| express MAE | 113 | 109 | 108 | 76 | 97 | 131 | 104 |
| vehicle coverage | 98–99% NYCT | 99% | 98–99% | 98% | 97.4% | 98.4/97.0% | **97.7–98.3% NYCT / 98.0% MTABC** |
| routes missing vs prod | 74 | 75 | 93 | 90 | 92 | **0** | **0** |
| **our position anchor age** | 13–19 s | 13–19 s | 15 s | 15 s | 15 s | **69 s** | **69 s** |

**This live table is for coverage and trip matching only. Do not read ETA trends off it** — it mixes
time of day, the MTABC fleet joining on 07-29, and the anchor regression. Use the fixed-window,
agency-held-fixed table below, which is the only clean ETA series.

**Confounder-free cross-day series** (archives replayed at one clock window, **NYCT only**, ~2,560–2,610
vehicles per row, 1–2.3 M stop-prediction deltas each):

| window / date | same `trip_id` | ALL | 0–5 min | 15–30 min | 30+ min | local | express |
|---|---|---|---|---|---|---|---|
| **17:25** 07-24 (Fri) | 96.6% | −6 / 37 | −2 / **13** | −8 / 35 | −16 / 60 | −5 / 32 | −29 / 88 |
| **17:25** 07-27 (Mon) | 97.2% | −5 / 38 | −2 / **13** | −6 / 33 | −13 / 65 | −4 / 30 | −32 / 119 |
| **17:25** 07-29 (Wed, post-deploy) | 96.9% | −15 / 46 | −6 / **28** | −16 / 42 | −26 / 66 | −15 / 41 | −18 / 101 |
| **08:45** 07-29 (Wed, pre-deploy) | 97.4% | −9 / 31 | −2 / **11** | −12 / 30 | −24 / 53 | −9 / 28 | −24 / 83 |
| **08:45** 07-30 (Thu, post-deploy) | 97.8% | −16 / 42 | −6 / **25** | −17 / 40 | −33 / 64 | −15 / 39 | −40 / 107 |

**Read:** trip matching genuinely improved at AM (97.4% → 97.8% with the fleet held fixed) and is flat
at PM. But **every ETA bucket regressed after the deploy, and the near-term bucket roughly doubled at
both windows** (11 → 25, 13 → 28) while 30+ barely moved (65 → 66). That pattern — damage concentrated
at short horizons, absent at long ones — is the signature of a stale position anchor, not of link
times or of MTABC's cold history (which cannot apply here at all, since these rows are NYCT-only).

**This corrects the 07-29 entry.** It recorded "NYCT improved 37 → 36 s" and a "record 98.0% match"
from live runs; at a fixed window with the agency held fixed, NYCT ETA *regressed* (38 → 46 s) and the
98.0% was the MTABC blend — MTABC matches better than NYCT (98.3% vs 97.8% on 07-30) because its
longer headways leave fewer adjacent departures to confuse. The MTABC-cold-history effect was real
too; both were present on 07-29 and the live method could not separate them.

## 2b. What was deployed 2026-07-29

Two fixes, both verified before and after deploy:

| | before | after |
|---|---|---|
| **B1** (NYCT route, silently absent) | 0 of 14 vehicles | 12 of 13 |
| Q84 / Q30 / Q75 | 43% / 43% / 50% | 80% / 100% / 100% |
| **MTABC fleet** | **0 of ~502 (0%)** | **487 of 502 (97.0%)** |
| MTABC routes absent | ~90 | **2** (`BXM18`, `QM32`) |
| routes missing vs prod | 92 | **0** |
| GTFS trips with no STIF linkage | 28,845 | **0** |
| sign codes in bundle | 653 | **881** |
| vehicles ours / prod | 1,953 / 2,412 | 3,356 / 3,398 |

Delivered by three changes: the **dual-key STIF matcher** (§4B), the **MTABC STIF input** (§4A), and
the build tooling that made both diagnosable (§4C).

**Cost: two ~40-second feed outages** (38 s and 44 s), both measured. Only `oba-gtfsrt` interrupts the
feed — inference and predictions were restarted first and reloaded invisibly, which is now the
documented ordering. Bundle builds took 3.5–4 min at a 6 g heap with no OOM and no feed impact.

**But the outages were not the real cost** — the sustained capacity overrun below was.

## 2c. The 07-29 fix caused a freshness regression — found 2026-07-30

**Symptom.** `probe-coverage.py` reported our position age at **72 s median / 89 s p90** against prod's
**32 s**, reversing the 2–3× freshness *advantage* that held in every prior run. Four consecutive
probes confirmed 65–74 s, and `analyze-bias.py` §4 measures it independently: **prod VP age − our VP
age = −39 s**, where it was **+22 to +25 s** on 07-27.

**Dated precisely to the deploy, and platform-wide** — archives replayed at one fixed window
(`analyze-anchor-age.py`), NYCT only so the new MTABC fleet cannot be the cause:

| window | 07-24 | 07-27 | 07-28 | 07-29 | 07-30 |
|---|---|---|---|---|---|
| 08:45, NYCT only (~2,600 veh) | – | 16 s | 15 s | 15 s | **69 s** |
| 17:25, both agencies | 15 s | 15 s | 15 s | **69 s** | – |
| feed *header* age, same windows | 5–6 s | 5 s | 4–5 s | 5 s | 6 s |

MTABC's anchor age on 07-30 is **68 s**, identical to NYCT's, and the NYCT fleet size is unchanged
across the step (2,633 → 2,620) — so this is not composition.

> **The distinction that matters, and the monitoring gap it exposes: the feed is published on time;
> the data inside it is old.** Header age never moved (4–6 s) — and header age is exactly what the
> `FeedStalenessSeconds` metric and its alarm watch. **No existing metric observes anchor age**, which
> is why a 4.6× freshness regression ran for a full day unnoticed. Adding it is §8 #2.

**Cause: capacity, not configuration.** Verified on the host — the flags are unchanged
(`deadband minMeters=10 minIntervalSec=7 maxAgeSec=30`, `shed maxAgeSec=50`) and inference last
restarted at the deploy itself (07-29 18:16 UTC). What changed is load. CloudWatch at AM peak (08:00):

| | 07-27 | 07-28 | 07-29 | **07-30** |
|---|---|---|---|---|
| `TripUpdateEntities` | 2,602 | 2,651 | 2,671 | **3,482** (+30%) |
| `CPUUtilization` avg | 54% | 51% | 57% | **74%** |
| `InferenceBacklogThreads` avg | 1,397 | 1,545 | 1,490 | **14,994** (max 15,244) |
| `InferenceShedFixes` per 2 min | **0** | **0** | **0** | **3,869** (max 4,612) |

`InferenceShedFixes` had been **exactly 0 for the whole recorded history** and now fires at every peak
(2,745/interval at 07-29 PM, 3,869 at 07-30 AM), going quiet overnight as load falls. This is the §4.10
trade-off (coverage for freshness) being exercised for the first time.

**What the metric means:** `InferenceShedFixes` is the *delta* of a cumulative counter between
`monitor.sh` runs (~2 min), i.e. **the number of AVL fixes inference threw away without running the
particle filter at all** (`VehicleLocationInferenceServiceImpl:751-758` — a fix is dropped if it waited
in the queue longer than `oba.shed.maxAgeSec`). It is a count, not a rate or a percentage.

### The mechanism, measured at the peak itself (08:49 EDT, inside the comparison window)

The engine log is far more informative than the CloudWatch metrics, and it reframes the diagnosis:

| | off-peak (10:32) | **peak (08:49)** |
|---|---|---|
| threads active | **15–20 of 642** | **642 of 642 — pool fully saturated** |
| avg processing time per fix | **75 ms** | **2,300–2,700 ms (~32×)** |
| backlog counter | 277–2,278 (sawtooth) | ~14,600, pinned |
| shed rate | 0 | ~16–32 fixes/s (only **~3%** of inflow) |

**Only ~3% of fixes are actually shed, so shedding is not what makes the anchor old — queue wait is.**
The chain is: peak demand **slightly exceeds** sustainable throughput, so a queue forms; backlog ÷
throughput ≈ 30 s of waiting, and **`oba.shed.maxAgeSec=50` then pins the worst case** — every fix that
survives to be processed has waited up to 50 s, which lands exactly on the observed 69 s anchor age
(≈50 s queue + ~15 s of AVL transport and deadband, the old baseline). So the 50 s threshold is not just
a safety net; **it is currently *setting* our anchor age.**

**The two rates, both now measured rather than estimated** (`simulate-deadband.py`, replaying the real
08:45–08:55 AVL — 533,648 raw fixes from 4,986 vehicles):

| quantity | value | how |
|---|---|---|
| raw AVL inbound | **889 fixes/s** (5.6 s per vehicle) | counted from the `bustechGps` archive |
| **admitted to inference** after the deadband | **286 fixes/s** (32% of inbound; **~21 s per vehicle**) | replaying `passesDeadband` exactly |
| **sustainable throughput** | **~254–270 fixes/s** | derived: at equilibrium capacity = admitted − shed rate = 286 − (16…32) |
| implied real per-fix cost at peak | **~114–126 ms** | 32 cores ÷ capacity (vs 75 ms off-peak) |

So the system is over capacity by only **~6–12%** — a small deficit, which is why it degrades gracefully
into a pinned queue rather than collapsing. It also cross-validates the pre-regression baseline: a ~21 s
processing cadence implies a mean anchor age of ~10 s plus transport ≈ the 15 s that was measured for
three straight days.

> **A framing correction this forces — now written up in full at §1a.** "We run 5 s GPS vs prod's ~30 s"
> describes the *shared source*, not what we process: the deadband thins it to ~11 s for a moving bus and
> ~21 s fleet-mean before inference sees it. The defensible comparison is the end-to-end one — our
> ~16 s anchor age against prod's ~39–48 s, about **2.4× fresher, not 6×**.

Two aggravating factors, both worth fixing on their own merits:

- **The thread pool is 20× oversubscribed.** `_numberOfProcessingThreads = 2 + cores × 20` = **642** on
  32 vCPU (`VehicleLocationInferenceServiceImpl:144`) for a CPU-bound particle filter. Running 642
  runnable threads on 32 cores inflates each task's wall-clock latency ~20× (75 ms → ~2,400 ms) without
  adding throughput. It is a plain Spring setter with **no system property**, so changing it needs a
  wiring change.
- **The backlog accounting is O(n²) and unsynchronized.** `InferenceBacklogThreads` is
  `_inferenceProcessingThreads.size()` — a plain `ArrayList<Future>` of *every submitted task*, pruned
  only when it exceeds `MAX_EXPECTED_THREADS = 3000`, via a scan plus O(n) `remove()` per dead entry
  (`BundleManagementServiceImpl:348-359,546-560`). Past 3,000 the prune runs on **every registration**,
  i.e. ~500×/s against a 14,600-element list, from 642 threads with no synchronisation. This is
  plausibly a large part of the missing CPU (74%, not pinned) and it also means **the metric is not a
  queue depth** — healthy behaviour is a 0–3,000 sawtooth, and values above 3,000 mean the prune can no
  longer keep up. Read it as a saturation flag, not a queue length.

**Cost, with the fleet held fixed:** near-term (0–5 min) MAE 11 → 25 s at AM and 13 → 28 s at PM;
ALL 31 → 42 s and 38 → 46 s; 30+ min essentially flat (65 → 66 s). See the §2 fixed-window table.
Near-term ETAs are almost entirely determined by where the bus is now, which is why a stale anchor hits
them hardest and long horizons barely at all.

### Fix options, in recommended order

The deficit is only ~6–12%, so a resize is not required to *restore freshness*. But see the note on
option 1: the cheap fix costs GPS granularity, which is the experiment's independent variable.

> ⚠️ **Corrected 2026-07-30 by the offline replay: `minIntervalSec` 7 → 10 does almost nothing** —
> 286 → **279 fixes/s (−2.4%)**, still above the ~254–270 ceiling. My original recommendation of 7 → 10
> would not have fixed this. **The first setting that works is 7 → 12** (236 fixes/s, −17%).
>
> The reason is quantization against the 5.6 s inbound cadence. When the rate cap drops a fix, the
> "last kept" timestamp does not advance, so the next fix is ~11.2 s from it. Any cap between ~6 s and
> ~11 s therefore admits on the *same* every-other-fix rhythm; only a cap above ~11.2 s forces an extra
> skip. Hence the cliff between 10 s (279/s) and 12 s (236/s). **Deadband demand falls in steps at
> multiples of the inbound cadence, not smoothly** — so pick settings from the measured table, never by
> interpolating.

| # | change | effect | cost / risk |
|---|---|---|---|
| 1 | **RECOMMENDED: deadband `minIntervalSec` 7 → 15**, leaving `minMeters=10` and `maxAgeSec=30` unchanged (`run-inference.sh`, restart inference) | 286 → **226 fixes/s**, a 12–17% margin under the ~254–270 ceiling, so the queue drains instead of being capped | free, reversible, no rebuild. **Costs ~2 s of anchor age** (~15 → ~17 s) — see the arithmetic below |
| 2 | **`oba.shed.maxAgeSec` 50 → 30**, as a guardrail alongside #1 | after #1 shedding should stop firing entirely, so this becomes a pure ceiling — and it should sit at the **parity threshold**: 30 caps the worst case near ~34 s ≈ prod's 32 s, where 50 permits 65 s (worse than prod). This is what stops the goal being breached silently again | free, same restart. **Roughly loss-neutral** — see the note below |
| 3 | **Cut `numberOfProcessingThreads` 642 → ~64** | removes the ~20× latency amplification (2,400 ms → ~150 ms per fix) and much of the prune cost; raises effective throughput | needs a Spring wiring change (no `-D` hook today). Low risk — throughput is set by cores, not thread count |
| 4 | **Fix `_inferenceProcessingThreads`** — bound it, synchronise it, or replace it with a counter | removes an O(n²) unsynchronised hot path at ~500 calls/s, and makes `InferenceBacklogThreads` mean something | small code change in `BundleManagementServiceImpl`; genuine latent bug (concurrent `ArrayList` mutation) |
| 5 | **Resize c7i.8xlarge → c7i.12xlarge** (48 vCPU) — **PREFERRED as of 2026-07-30, cost accepted** | ceiling ~254–270 → **~380–405 fixes/s** if scaling is linear, clearing today's 286/s with ~33% headroom **while keeping the 7 s deadband and the ~15 s anchor age** | **+$521/mo (+50%)** on-demand. Needs a stop/start, so a ~10–20 min off-peak outage window. See §2d |

### The recommendation, against a stated goal of PARITY with prod

**Goal: anchor age ≤ prod's ~32 s** (not "as fresh as possible" — the current project objective is
parity, which makes this a much easier constraint and buys a lot of headroom).

Anchor age is roughly **half the processing cadence plus transport**, which the pre-regression data pins
down: a 21.4 s cadence produced a measured 15 s anchor age, so transport ≈ 4.3 s. Applying that:

| `minIntervalSec` | admitted | vs ceiling | cadence/veh | **predicted anchor age** | vs prod's 32 s |
|---|---|---|---|---|---|
| 7 *(today, saturated)* | 286/s | **over** | 21.4 s | **69 s actual** — queue dominates | **2× worse** |
| 10 | 279/s | over (marginal) | 21.7 s | ~15 s | better |
| 12 | 236/s | under, 7–13% | 24.3 s | ~16.5 s | ~1.9× better |
| **15 ← recommended** | **226/s** | **under, 12–17%** | 25.1 s | **~17 s** | **~1.9× better** |
| 20 (with `maxAge` 45) | 173/s | under, 32%+ | 33.5 s | ~21 s | ~1.5× better |

**Why 15 rather than 12.** The freshness difference is 0.4 s, irrelevant against a 32 s target; the
capacity difference is not. The ceiling is *derived*, not measured, so it may be lower than estimated —
and under-shooting means staying saturated at 69 s, a total failure of the goal. Buying 12–17% margin
instead of 7–13% costs 0.4 s. It also absorbs the fleet growth still pending (`BXM18`, `QM32`, ~15
MTABC vehicles).

**Why not 20, since parity would allow it.** At 20 s the cadence reaches 33.5 s, level with prod, and
that is where a *different* risk starts: sparser observations degrade the particle filter itself, not
just freshness. We lack the UTS run data prod uses to disambiguate adjacent trips, so we depend more on
dense geometry. Trip matching at 98.4% is currently the best result in this work — do not spend it on
headroom that is not needed.

**Why change only the rate cap.** The other two knobs thin the stream in worse ways:

- **`minMeters` 10 → 15/25 preferentially drops slow-moving buses** — exactly the congested cases whose
  ETAs are hardest and most valuable — and measurement shows it buys only ~2% more reduction (231 vs 236
  fixes/s at a 12 s cap). The rate cap thins uniformly in time; the distance test thins selectively by
  speed. Prefer uniform.
- **`maxAgeSec` 30 → 45 weakens the failsafe** that keeps stationary buses present in the feed. It saves
  ~3% of load (failsafe admissions 8% → 4.4%) while letting a parked bus go 45 s with no update, which
  inflates the anchor-age tail and risks vehicles dropping out of coverage. Bad trade.

**Verify all three of these at the next peak, not just the first:** `InferenceShedFixes` back to **0**;
anchor age **~15–20 s** (`analyze-anchor-age.py`); and **trip match still ≥98%**, which is the guard
against having thinned observations too far. If shedding persists, the capacity estimate was optimistic
and 20 s is the next step. Then re-run the NYCT-only fixed-window comparison: near-term MAE should
return toward 11–13 s, confirming the diagnosis and unblocking the F2/F3 re-derivation.

#3/#4 remain worth doing afterwards on their own merits — they raise the ceiling at zero infrastructure
cost, so the *next* fleet increase does not force another cadence cut, and they would allow going back
toward 7 s if the objective ever changes from matching prod to beating it. #5 stays unneeded.

## 2d. Remedy applied: resized to c7i.12xlarge — DONE 2026-07-30 11:27 EDT

> **Executed.** c7i.8xlarge → **c7i.12xlarge (48 vCPU / 96 GiB)**, in place, EIP and both EBS volumes
> retained. **Total feed outage: ~1 min 50 s** — far below the 5–12 min estimated. The deadband was left
> at 7 s deliberately, so the 5 s-ingestion premise is intact.
>
> | phase | time (EDT) |
> |---|---|
> | graceful stop of OBA units + `docker stop oba-mongo` (exit 0) | ~11:27:00 |
> | `stop-instances` → `stopped` | 11:27:13 → 11:27:46 (**33 s**) |
> | `modify-instance-attribute` → `start-instances` → `running` | → 11:28:06 (**53 s** end-to-end) |
> | inference thread pool created (size **962**) / weights re-applied 20/40/40 | 11:28:39 / 11:28:40 |
> | feed serving again (HTTP 200) | **11:28:52** |
> | fleet fully re-converged | 11:34:47 |
>
> **Verified after:** `nproc` 48, 92 GiB RAM, all five units active, Mongo up, `/data` intact (8.7 G,
> both volumes persisted), `oba-weights` re-applied 20/40/40 automatically (HTTP 200), coverage back to
> **NYCT 98.4% / MTABC 98.0%** with 2,319 vehicles versus 2,320 immediately before, and **position age
> 19 s median against prod's 43 s**.
>
> ⚠️ **Still to confirm: the PM peak (15:00–19:00).** Off-peak was never the problem — at 11:26,
> pre-resize, anchor age was already a healthy 17 s. The test is whether
> `InferenceShedFixes` stays at **0** and anchor age stays ~15 s when the fleet reaches ~3,480. Note the
> thread pool auto-grew to **962** (`2 + 48 × 20`), so the 20× oversubscription and the O(n²)
> `_inferenceProcessingThreads` prune are both still present and the capacity gain may be sublinear.
> If shedding returns at peak, do §2c #3/#4 rather than resizing again.

### Original decision rationale (2026-07-30)

**Cost was accepted as not binding, which changes the recommendation.** Resizing is preferable to the
deadband route because it is the only option that keeps the **5 s ingestion premise** intact: demand
stays at 286 fixes/s under the current 7 s cap, anchor age returns to ~**15 s** (2× fresher than prod
rather than merely at parity), no observation density is sacrificed, and the pending fleet additions
(`BXM18`, `QM32`, remaining MTABC vehicles) plus future picks get absorbed. It also gives Mongo room for
a larger WiredTiger cache, which should help the historical-component lookups.

**Recommended plan — three steps, never leaving the deployment degraded:**

1. **Now (≈5 min, no feed outage):** apply `minIntervalSec=15` + `shed.maxAgeSec=30` and
   `systemctl restart oba-inference`. Only `oba-gtfsrt` interrupts the feed, so restarting inference is
   invisible to consumers (§2b). This keeps **today's PM peak healthy** instead of eating another one.
2. **Tonight, 02:00–04:00 ET** (`TripUpdateEntities` ~165–315, the daily minimum): resize.
3. **After verifying:** revert `minIntervalSec` to **7** and `shed.maxAgeSec` to **30** (keep the tighter
   ceiling), restart inference, and confirm at the next peak.

That ordering also yields a free measurement of scaling behaviour: one peak at 15 s / 32 vCPU and one at
7 s / 48 vCPU. If cadence-quality tradeoffs are ever revisited, that is exactly the data needed.

**The resize itself** (EIP and EBS persist, so the public hostname does not change):

```bash
IID=i-0386b6bb8338b2f67
aws ec2 stop-instances  --instance-ids $IID          # systemd units stop with the OS
aws ec2 wait instance-stopped --instance-ids $IID
aws ec2 modify-instance-attribute --instance-id $IID --instance-type c7i.12xlarge
aws ec2 start-instances --instance-ids $IID
aws ec2 wait instance-running --instance-ids $IID
```

`c7i.12xlarge` is offered in our AZ (`us-east-1a`) — verified. Expect **~10–20 min of feed outage**:
2–4 min for the stop/start plus service startup and the 675 MB bundle load.

**Verify after the restart:** `nproc` = 48; all five units active and Mongo's container up; the feed
returns on the unchanged EIP; `oba-weights` re-applied 20/40/40 (it is `PartOf` predictions, so it should
fire automatically — check, since the override resets on every restart); then at the next peak
`InferenceShedFixes` = 0 and `analyze-anchor-age.py` ≈ 15 s.

**Three things to watch, in priority order:**

- ⚠️ **The thread pool auto-scales with cores** — confirmed at 962 after the resize — `2 + availableProcessors × 20` becomes **962 threads**
  on 48 vCPU. Oversubscription therefore stays at 20×, and the unsynchronised O(n²)
  `_inferenceProcessingThreads` prune gets hammered by *more* threads. **That serial bottleneck does not
  scale with cores**, so the capacity gain may be sublinear — expect somewhere between +25% and +50%
  rather than a guaranteed +50%. Even at +25% (318–338 fixes/s) demand of 286/s is covered, so the
  resize should still work; but this is the reason to follow up with §2c #3/#4, which would raise the
  ceiling further at no extra cost.
- **Heaps do not need to grow.** The workload is CPU-bound, which is why the c-family was chosen; the
  current 52 GB of committed heap (inference 30 g, predictions 10 g, gtfsrt 6 g, Mongo WT 6 GB) simply
  gains headroom on 96 GiB. The one change worth making opportunistically is **Mongo WT cache 6 → 12 GB**,
  since `AggregateLinkTimes` lookups are on the prediction path.
- **Consider bundling `predictions.PredictionLevel=NEXT_TRIP`** (§8 #5/#6) into the same window. ETA
  baselines already have to be re-established because of the anchor regression, so doing both at once
  costs one re-baseline instead of two — and it removes the F1 rollover artifact permanently. Anchor age
  and shed rate are unaffected by it, so it does not confound the freshness verification.

### Resize cost

AWS Pricing API, us-east-1, Linux/shared, 730 h/mo:

| option | USD/hr | USD/mo | vs today |
|---|---|---|---|
| **c7i.8xlarge on-demand (today)** — 32 vCPU / 64 GiB | 1.428 | **1,042** | — |
| c7i.12xlarge on-demand — 48 vCPU / 96 GiB | 2.142 | **1,564** | **+521 (+50%)** |
| c7i.12xlarge, 1-yr No Upfront RI | 1.417 | 1,034 | ≈ today's bill, but a 1-year commitment |
| c7i.12xlarge, 3-yr No Upfront RI | 0.943 | 688 | −354 |

Two things to note. **There is no intermediate size** — c7i goes 8xlarge (32 vCPU) → 12xlarge (48), so
the smallest purchasable increment is +50% capacity for +50% cost, when the actual shortfall is ~6–12%.
That alone argues for tuning first. And the commitment rows are not a like-for-like saving: a 1-yr RI on
the *current* box would cut today's bill similarly, so the true incremental cost of the resize is ~+50%
under any consistent pricing model. The deployment is deliberately on-demand/no-commitment today
(EC2-DEPLOYMENT §9).

> **What lowering the shed threshold does and does not do** — worth being precise, because the intuitive
> reading is wrong. In steady state every queued fix leaves either processed or discarded, so
> **discard rate = arrival rate − service rate**, i.e. ~500 − ~470 ≈ **30 fixes/s regardless of the
> threshold**. (This matches the measurement: observed shed ~16–32/s against a computed ~16–30/s
> deficit.) The threshold does not control *how many* fixes are dropped — it controls *how long the
> survivors wait*. So 50 → 25 is approximately **loss-neutral and strictly fresher**, and it slightly
> *raises* capacity by keeping the backlog (and therefore the O(n²) prune) smaller.
>
> **Two things it cannot do.** It cannot get us below ~40 s while saturated — anchor age is
> `threshold + ~15 s` of inherent AVL transport and deadband holding, so beating prod's 32 s requires
> #1 or #5, not a smaller threshold. And it does not make the loss harmless: the risk is **observation
> density**, since a particle filter needs a dense stream to stay converged, and sparser updates are
> part of what produces direction flips and wrong-trip errors (classes C/D). At ~6% loss that is fine,
> but it means ETA freshness and trip matching pull in opposite directions here — **verify the
> trip-match rate (98.4% today) has not fallen before lowering the threshold further.**

**Correction to an earlier reading in this document:** the CPU figure (74%, not pinned) initially
suggested the bottleneck might not be cores at all. The peak log resolves it — the *thread pool* is
100% saturated and per-fix latency is 32× its off-peak value, so the system is genuinely at its
throughput ceiling; the unpinned CPU is explained by the oversubscription and prune overhead above, not
by spare capacity.

---

## 3. Discrepancy classes at a glance

| # | class | size (latest) | nature | root cause | fix |
|---|---|---|---|---|---|
| A | ~~MTABC fleet absent~~ | was 792 veh / 91 routes | **coverage defect** | no MTABC STIF → no DSC/run data → zero candidate blocks | **FIXED & DEPLOYED 07-29** — MTABC STIF added, 97.0% coverage |
| B | ~~route B1 (NYCT) absent~~ (+ Q84/Q30/Q75) | was 10–15 veh, all day | **coverage defect** | bundle build dropped 87% of B1 trips from the DSC index — GTFS/STIF disagree on non-revenue first/last stops | **FIXED & DEPLOYED 07-29** — dual-key index; B1 at 92% |
| C | same route, different trip | **1.2%** of pairs (was 2.6–3.4%) | expected w/o data | no UTS run assignments → 3 of 4 likelihoods flat | TDM crew API / YardTrek |
| D | direction flips | 0.4% | transient | 135° gate skipped while stationary + DSC direction-blind | same UTS fix; self-corrects |
| E | route confusion | 0.01% | noise | shared-corridor DSC is a preference, not a rejection | none needed |
| F1 | rollover joins | **0.2%** of pairs (was 0.5%) | **measurement artifact** | prod emits current+next TU, we emit current only | join prod's current TU; set `NEXT_TRIP` |
| F2 | long-horizon "early bias" (local) | 30+ median −22 s *in the controlled stratum* | **was** an artifact; **premise now inverted** | *was* prod's anchor ~22 s older than ours; since 07-29 **ours is ~39 s older** and the bias got deeper, so the old mechanism cannot explain it | **re-derive after §2c is fixed** — the control is confounded by the regression |
| F3 | express long-horizon bias | 30+ median −18…−21 s, and **+0 s** in the controlled stratum | **unresolved** | thin history on long links (§6) — but the residual that made this "real" is not visible in the 07-30 run | re-measure after §2c; do not act on the current reading |
| I | **position anchor 4.6× staler than before, and staler than prod's** | 69 s vs 15 s; prod 30–36 s | **real platform regression** | +30% fleet from the 07-29 coverage fix exceeded peak capacity → load-shedding fires → longer effective update interval per vehicle | **§2c** — resize, or widen the deadband; add anchor-age monitoring |
| G | vehicles only in our feed | 36–55 | benign, **now quantified 07-30** | **two causes, measured:** ~24% are stale records (our publication expiry is 300 s; prod's is **exactly 120 s**, measured as a hard ceiling in their archive) and **~76% are freshly-reporting buses prod suppresses** — assignment/depot filtering we lack, not staleness. Supersedes the earlier "likelier a TTL difference" reading | tighten expiry to 120 s (free, 0.02% collateral) + consume depot rosters off `busSpear`; `simulate-publication-expiry.py` |
| H | 1.00 vs 1.82 TripUpdates/vehicle | — | config gap | `PredictionLevel=CURRENT_TRIP` vs prod's `NEXT_TRIP` | **needs a code change, not a config key** — see §5 H |

Three of these are artifacts of *how we measure*, not of the feed. That distinction is the single most
important lesson of this work (§7) — and F2/F3 now show its converse: an artifact explanation can stop
being true when the platform changes underneath it, so a control has to be re-verified, not inherited.

---

## 4. Coverage defects (the only things actually broken)

### A. The entire MTABC fleet was missing — RESOLVED 2026-07-29

> **Resolved.** The MTABC STIF was supplied on 2026-07-29, added to `stif-c6-all/`, and the bundle
> rebuilt. `gtfs_trips_with_no_stif_match` went **28,845 → 0** (28,845 was exactly MTABC's trip
> count), matched trips 202,565 → 244,350, sign codes 658 → 881, with **no sign code's count
> decreasing**. Live coverage is now **97.0%** of prod's MTABC fleet, and only `BXM18`/`QM32` remain
> absent. No config change was needed — the loader recurses through the STIF directory.
> The root cause below is retained because it explains the mechanism, and it is the same mechanism
> that would recur if any future pick ships without a STIF slice.

**Evidence.** 792 MTABC vehicles / 91 routes in prod, **zero** in ours: former private lines
(Q06–Q115, B100/B103, BX23) and MTABC express (QM/BM/BXM, SIM8/10). Ranges 412 → 850 vehicles by time
of day across runs. NYCT coverage is unaffected (97–99%).

**Root cause — code-confirmed.** The bundle has MTABC *GTFS* but no MTABC *STIF*, so there is no
DSC→trip sign-code map and no run data. Every MTABC DSC is `hasValidDsc=false`, which zeroes all three
candidate-block sources:
1. snap generation is skipped unless `hasRunResults() || hasValidDsc()` —
   `BlocksFromObservationServiceImpl.java:196-207`;
2. default-on `_requireDSCImpliedRoutes` + `_requireRunMatchesForNullDSC` discard every spatial snap —
   `BlockStateService.java:115,122,489-506`;
3. DSC/run candidate generation returns empty — `BlocksFromObservationServiceImpl.java:357-373`.

Ruled out: `DscLikelihood` (unknown DSC scores a neutral 1.0), and ingestion (`acceptAllVehicles=true`
bypasses the depot filter). MTABC geometry *is* in the spatial index — only the STIF-derived gates kill
it. **Fix:** obtain the MTABC STIF and rebuild. (Relaxing the two flags is possible but risks accuracy.)

### B. Route B1 (NYCT) — found 07-27, root-caused 07-28, **FIXED & DEPLOYED 07-29**

> **Deployed.** The dual-key matcher shipped on 2026-07-29. Live result: B1 **0% → 92%** (12 of 13
> vehicles, on 12 distinct trips rather than the old two-stuck-on-one), Q30 and Q75 at **100%**, Q84
> at 80%, and the per-route coverage gate is silent for the first time — no NYCT route below 20%.
> The open question below (why prod never had this defect) is **still unanswered** and still worth
> resolving: if prod's STIF input differs, fixing the input beats carrying this patch.


This is the condition the runbook names as alarming: a **NYCT** route in the missing list. It hid
inside the MTABC route list for three runs.

**Evidence.** Prod runs 10–15 B1 vehicles continuously; ours has **0** at every sampled window from
06:00 to 17:10, except two vehicles at 08:00 both pinned to the *same* trip
`UP_C6-Weekday-SDon-046400_B1_2` (one with a single stop prediction — a stuck hypothesis, not working
inference).

**The decisive test.** Three buses (7163, 7165, 7563) rotate between B1 and B6/B36/B64 during the day:

| prod says the bus is on… | in our feed | absent |
|---|---|---|
| B1 | 2 | **25** |
| B6 / B36 / B64 | 18 | 1 |

Same buses, same AVL, same depot — inferred normally on their other routes, dropped whenever they
display B1. So this is **route-scoped**, not vehicle-, depot- or ingestion-scoped. And B1 trips *are*
in the bundle (we emitted a real B1 trip id), so it is not a missing route either.

**Root cause — confirmed on the host, and it is NOT a missing STIF file.** My earlier hypothesis
(that the B1 STIF slice was missing or unmatched, like MTABC) was **wrong on the input side**. Every
input checks out:

| check | result |
|---|---|
| B1 STIF file present? | **yes** — `stif.b_0001__.426581.wkd.{open,closed}` + `.sat`/`.sun`, Ulmer Park, 516 KB |
| DSCs declared in STIF? | **yes** — 4010 (118 recs), 4011 (120), 4012 (5) |
| STIF trips ↔ GTFS trips? | **243 coded weekday records vs 243 GTFS weekday-SDon B1 trips**; origin times overlap 234/234 |
| STIF stop ids valid? | **yes** — e.g. 300000 = ORIENTAL BLVD/MACKENZIE ST, present in `stops.txt` |
| run numbers align? | **yes** — 37/37 GTFS runs present in STIF |
| what do the buses broadcast? | **4010 / 4011**, under the correct agency `MTA NYCT/2008`, and used by no other vehicles |

**The defect is in the built bundle.** Deserialising the bundle's DSC indices
(`/data/oba-bundle/2026C6_wholeMTA/{TripsForDSCIndices,DSCForTripIndices}.obj`):

- B1 has **124 of 928 trips (13.4%)** carrying a DSC; comparable routes have **100%** (B6 1807/1807,
  B9 748/748, B38 1220/1220).
- **DSC 4010 is absent from the bundle entirely**, 4012 too, and **4011 maps to just 7 trips.** Of
  B1's 124 mapped trips, 117 carry *other* routes' sign codes (interline/pull-out entries).
- So `StifTripLoader` silently dropped ~87% of B1's trips at build time.

**Why that produces zero vehicles**, code-confirmed at `BlocksFromObservationServiceImpl.java:353-373`:
DSC trip ids are added only `if (... && observation.hasValidDsc())`. A bus signed **4010** has an
unknown DSC → contributes no trips, and `BlockStateService`'s default-on `_requireDSCImpliedRoutes`
then discards every spatial snap because the implied-route set is empty — **the identical mechanism to
the MTABC gap** (§4A), reached by a different path. A bus signed **4011** can only ever match those 7
trips, which is exactly why the only B1 output ever observed was two vehicles pinned to
`046400_B1_2` — one of the 7.

**Scope: three routes, and the feed already told us which.** Per-route DSC coverage across all 188
routes with ≥100 trips:

| route | DSC coverage in bundle | vehicle coverage in our feed |
|---|---|---|
| **B1** | **13.4%** (124/928) | **0%** (0 of 14) |
| **Q84** | **50.0%** (58/116) | 43% (3 of 7) |
| **Q30** | **52.0%** (66/127) | 43% (3 of 7) |
| M101 | 81.5% | fine |
| everything else | 96–100% | fine |

Only **one** route is below 50% and only three below 96%. Those three are *precisely* the three NYCT
routes the new per-route gate flagged independently — so **bundle DSC coverage is what the feed gate
is really measuring**, which makes the gate a validated proxy for this class of build defect.

**The exact drop reason — RESOLVED 2026-07-28 by reproducing the build locally.** Built the C6 inputs
in a JDK 11 container off the host's own 254 jars (§4D), with `multiCSVLogger` given a `basePath` and
log4j configured. The build reproduced the shipped bundle **exactly** (`DSC 4011 → 7 trips`; 4060/4061/
4062 → 155/440/327, all identical), so its diagnostics are trustworthy. They show:

| diagnostic | rows |
|---|---|
| `stif_trips_with_no_gtfs_match` | **866 — every single one B1** |
| `trips_with_duplicate_run_and_start_time` | 0 |
| `trips_with_null_dscs` | 0 |
| `stif_trip_layers_with_missing_route` | 0 |
| `trips_with_failed_stif_to_gtfs_stop_id_matching` | 0 |

And the previously-invisible warning names the failing field:

```
WARN [StifTripLoader] gtfs trip not found for
  TripIdentifier(B1, 05:20:00 or 32000, 05:56:00 or 35600, 901473, B1-5 from 901473 block 39702663)
```

`startStop = 901473` is **"MACKENZIE LOOP (no G-A-R: use 300000)"** — a non-revenue point
(`pickup_type=1, drop_off_type=1`). B1's GTFS trips prepend it at `stop_sequence=1`; B6/B9/B38 start on
a revenue stop:

```
UP_C6-Weekday-006000_B1_1, 01:00:00, 901473, seq=1, pickup=1   <- STIF side keys on this
UP_C6-Weekday-006000_B1_1, 01:00:00, 300000, seq=2, pickup=0   <- GTFS side keys on this
```

**Root cause: the two halves of the match key disagree about non-revenue stops.**
`getTripAsIdentifier` (GTFS side) honours `_excludeNonRevenue=true` and skips to the first *revenue*
stop; `getIdentifierForStifTrip` (STIF side) always takes the raw first event record. `equals` requires
`startStop` to match exactly → every B1 trip is discarded → no DSC → `hasValidDsc=false` → no candidate
blocks → the bus never appears. The 7 survivors are the trips that happen to escape this shape.

**The obvious config fix is WRONG — do not ship it.** Flipping `excludeNonRevenue=false` on
`stifLoaderTask` fixes Brooklyn beautifully but wrecks Queens. Measured per borough:

| | Brooklyn before → after | Queens before → after |
|---|---|---|
| `stif_trips_with_no_gtfs_match` | 866 → **8** | 697 → **9,182** |
| `matched_trips_gtfs_stif` | 65,855 → **66,922** | 35,560 → **26,002** |
| `gtfs_trips_matched_with_multiple_stif_trips` | 57 → 0 | 0 → 352 |
| B1 DSCs | 4010 absent→**439**, 4011 7→**429**, 4012 absent→**5** | — |
| other routes' DSC counts | **byte-identical** (no regression) | breaks Q17/Q27/Q4/Q5/Q85/Q58/Q87/Q86… |

B1's trips carry the non-revenue stop at the **start**; many Queens trips carry them at the **end**, so
the global flag just trades one broken set for a bigger one. **Neither setting is correct.**

**Fix implemented and measured (2026-07-28): a dual exact-key index in `StifTripLoaderSupport`.**

The decisive observation is that for every affected trip the STIF key is **byte-identical to the GTFS
*raw* key**. So nothing needs loosening — each GTFS trip is indexed twice, under two exact keys:

- **primary**: the identifier built *with* non-revenue skipping (today's behaviour, unchanged)
- **secondary**: the identifier built *without* it (`getTripAsIdentifier(trip, false)`), stored only
  when it differs, and consulted only on a primary miss

Both keys are exact and the secondary is a pure fallback, so it cannot change a match that already
succeeds and adds no ambiguity — the safety property the global flag lacks.

Measured with the patched class shadowing the shipped jar, `excludeNonRevenue` at its default.
**Verified on every borough individually *and* on the real whole-MTA configuration** (5 NYCT boroughs +
MTABC, full STIF tree) — 8 builds, all exit 0:

| borough | unmatched STIF trips | GTFS trips matched by *multiple* STIF trips |
|---|---|---|
| Brooklyn | 866 → **0** | 57 → **0** |
| Queens | 697 → **0** | 0 → 0 |
| Manhattan | 484 → **0** | 263 → **0** |
| Bronx | 0 → 0 | 0 → 0 — **fix provably inert** |
| Staten Island | 0 → 0 | 0 → 0 — **fix provably inert** |
| **WHOLE-MTA** | **2,047 → 0** | **320 → 0** |

Other whole-MTA counters: `stif_trips_without_pullout` **2,560 → 0**; sign codes **653 → 658**;
`non-revenue_gtfs_stoptimes_updated` 31,380 → 33,387; index cost 8,301 raw keys atop 143,054 revenue
keys for 176,019 trips.

**Regression check over all 653 sign codes: 0 decreased.** Three increased (`4011` 7→429, `2030`
85→329, `2033` 89→329) and five are newly present (`4010`, `4012`, `5753`, `5843`, `8303`). Bronx and
Staten Island are byte-identical before/after, which is the safety property made visible.

**A consistency check that falls out of the whole-MTA run:** residual
`gtfs_trips_with_no_stif_match` settles at **28,845** — exactly the MTABC trip count in
`gtfs_stats.csv`. So after this fix the *only* GTFS trips lacking STIF linkage are the MTABC fleet,
i.e. the genuinely missing input (class A). Nothing else is left unexplained.

**On the earlier comparison of variants** (Brooklyn): flag-flip reached 8 unmatched and the
startStop-tolerant patch 472, versus **0** here. Note `matched_trips_gtfs_stif` *falls* slightly under
the dual key (Brooklyn 65,855 → 66,930 but Manhattan 36,317 → 36,034, whole-MTA 203,058 → 202,565).
That is not lost coverage: it is the elimination of duplicate match rows where one GTFS trip was
previously claimed by several STIF trips (320 → 0 whole-MTA). Sign-code coverage — the measure that
actually drives inference — strictly increased.

Two incidental confirmations: the 62 B1 trips my offline analysis had bucketed as "no GTFS trip for
that (route,run,block)" now match, confirming that bucket was an artifact of my own reconstruction of
run tokens/block ids as flagged; and Manhattan turns out to have had the same defect (484 trips) that
the earlier two-borough test never revealed.

### What is unique about the dropped routes

Analysed by taking every dropped STIF trip, locating its GTFS counterpart by (route, run, block), and
reporting **which field of the key differs** — computing the GTFS side both ways: `rev` (skipping
non-revenue stop_times, i.e. what `excludeNonRevenue=true` does) and `raw` (as-is).

| route | dropped | which field differs | would the **raw** key have matched? |
|---|---|---|---|
| **B1** | 866 | 359 × `startStop` only; 445 × `startStop`+`startTime`; 62 × no GTFS trip for that (route,run,block) | **yes for 804 of 866 (93%)** |
| **Q84** | 275 | 58 × `startStop`+`startTime`; 217 × no GTFS trip for that (route,run,block) | yes for 58 |
| **Q30** | 262 | 61 × **`endTime`**; 201 × no GTFS trip | yes for 61 |
| **Q75** | 160 | 20 × **`endTime`**; 140 × no GTFS trip | yes for 20 |

**The shared property: the GTFS trip carries a non-revenue `stop_time` at its start or its end.** That
is the whole class. Worked examples:

```
B1  run B1-5   STIF(stop=901473 start=05:20:00 end=05:56:00)
               GTFS rev(stop=300000 start=05:20:00 end=05:56:00)   <- only the stop differs
               GTFS raw(stop=901473 start=05:20:00 end=05:56:00)   <- identical to STIF

B1  run B1-7   STIF(stop=308644 start=06:38:00 end=07:23:00)
               GTFS rev(stop=301614 start=06:38:38 ...)            <- stop AND time differ
               GTFS raw(stop=308644 start=06:38:00 end=07:23:00)   <- identical to STIF

Q30 run Q30-401 STIF(stop=501471 start=15:10:00 end=15:54:00)
               GTFS rev(... end=15:49:28)                          <- the END differs
               GTFS raw(... end=15:54:00)                          <- identical to STIF
```

Two things fall out of this:

1. **It explains why the global flag trades one borough for the other.** It is the *same* defect at
   opposite ends of the trip: B1/Q84 carry the non-revenue stop at the **start**, Q30/Q75 at the
   **end**. `excludeNonRevenue` is a single switch applied to both ends, so no setting can satisfy both.
2. **It explains why my startStop-only fallback recovered 394 and missed 472.** Where the non-revenue
   stop shares a departure time with the first revenue stop (B1-5: both 05:20:00) only the stop differs
   and the fallback catches it; where it is a few seconds/minutes earlier (B1-7: 06:38:00 vs 06:38:38)
   the time differs too and it does not.

**A second, distinct population:** 558 of the 697 Queens drops (and 62 of B1's) have **no GTFS trip at
all** for that (route, run, block) — not a non-revenue issue. *Caveat:* this bucket is the least
trustworthy of the analysis, since it depends on my reconstruction of the run token and block id from
`trips.txt`; some of it may be my indexing rather than genuinely absent trips. Worth confirming before
treating it as a separate defect.

### Why does prod cover B1 when we don't? — candidate root causes

The fix above treats *our* symptom, but prod runs the same software on the same pick and covers B1
fine, so something upstream differs. Ruled out, each with evidence:

| # | hypothesis | verdict |
|---|---|---|
| 1 | B1 STIF file missing from our inputs | **no** — present (`stif.b_0001__.*`, 516 KB), declares 4010/4011/4012 |
| 2 | Our GTFS differs from prod's | **no** — prod emits 16/16 B1 trip_ids that exist in our GTFS, with the identical non-revenue `901473` seq-1 stop |
| 3 | STIF loader code differs between prod's release and ours | **no** — `git diff 2.47.3 HEAD -- '*/bundle/tasks/stif/*'` is **empty** (only a pom version bump) |
| 4 | The 2016 STIF refactor (`6ee8c84a3`) changed behaviour vs prod | **no** — it is in 146 release tags, everything ≥ 2.35 |
| 5 | Prod compensates at runtime using its UTS operator→run assignments | **no** — `RunServiceImpl.setup()` reads run→trip from the bundle's `TripRunData.obj`, which has the *identical* 124/928 B1 gap (both maps are outputs of the same STIF↔GTFS match). No runtime feed can recover trips the bundle never linked |
| 6 | Prod disables the DSC gates (`_requireDSCImpliedRoutes` / `_requireRunMatchesForNullDSC`) via TDM config | **no** — both are plain `private static boolean` with static setters, no `@ConfigurationParameter` and no config-service read |
| 7 | Prod's YardTrek pull-out block is an extra candidate source | **no** — `getAssignedBlockId` is consumed by `BlockLikelihood` / `BlockStateObservation` (hypothesis *scoring*), not candidate *generation* |

Since candidates can only come from the DSC map, the run map, or geometry snaps — and the first two
share the same gap while snaps for a null DSC fall back to the run map — **a bundle built like ours
cannot produce B1 coverage no matter what prod feeds it at runtime.** So prod's *bundle* differs.
Remaining live candidates, ranked:

- **A. Prod's STIF drop differs from the copy we were handed.** We verified GTFS parity but never STIF
  parity; ours arrived via a colleague's "GTFS + STIF Files C6" folder, not necessarily prod's drop. If
  prod's B1 event records begin at revenue stop `300000` rather than the loop point `901473`, identical
  code matches them. **Test:** diff/checksum prod's `stif.b_0001__*` against ours.
- **B. Prod's bundle-build configuration differs.** Prod builds via the admin webapp
  (`BundleBuildingServiceImpl`), not a hand-written XML like ours, so it may set STIF task properties or
  a task set we omit. Note `excludeNonRevenue=false` alone cannot be it — measured to break Queens.
- **C. Different bundle vintage** — prod's bundle may predate/postdate a GTFS revision that introduced
  B1's non-revenue prefix. **Test:** prod's bundle build date.
- **D. Prod's bundle GTFS differs from the GTFS we were given** even though trip ids coincide (e.g. no
  `901473` seq-1 rows). **Test:** compare stop_times for one B1 trip in prod's bundle inputs.
- **E.** Prod carries build patches not on this tag — can't exclude without their build manifest.

**One question settles it:** does prod's C6 bundle DSC map contain **4010** (and what is
`stif_trips_with_no_gtfs_match` in prod's bundle `summary.csv`)? If yes → their inputs/config differ and
we diff those. **If no → this model is wrong** and prod is reaching B1 by a mechanism not yet found,
which reopens the runtime question.

**Why the earlier startStop-tolerant attempt was only partial** — kept because the reasoning
generalises. It fixed the *lookup* instead of the *key*: it tolerated one field while still requiring
`startTime` and `endTime` to match exactly. But the non-revenue stop only *sometimes* shares a
departure time with the first revenue stop:

- shares the time (B1-5: `901473` and `300000` both 05:20:00) → only the stop differs → tolerance
  works. **359 cases.**
- a few seconds earlier (B1-7: `308644` 06:38:00 vs revenue `301614` 06:38:38) → the *time* differs
  too → unreachable. **445 cases.**
- Queens' cases are `endTime` mismatches (Q30-401: raw end 15:54:00 vs revenue end 15:49:28) → also in
  the key → unreachable. **139 cases.**

Tolerating fields one at a time chases each newly-discovered shape; indexing the raw key handles all of
them at once, because it reproduces exactly what the STIF side already computes. That is the general
lesson: **make the two sides agree on how the key is built, rather than making the comparison fuzzy.**
2. **Q84 / Q30 / Q75 are a SEPARATE bug** — under the default flag their unmatched keys carry ordinary
   *revenue* startStops (Q30 → `501471` "NASSAU BLVD/LITTLE NECK PKWY", and Q84's trips begin on
   revenue stop `500482`), so they are not the non-revenue mechanism at all. Same symptom, different
   cause; still open.
3. **Add a bundle-build gate**: assert per-route DSC coverage ≥ ~90% and fail the build otherwise.
   That would have caught B1, Q84 and Q30 before deploy.

**Guardrail added:** `probe-coverage.py` now prints a **per-route NYCT coverage gate** flagging any
route below 20% of prod's fleet. A single 0% route is invisible inside a 97.4% aggregate — that is
exactly how B1 stayed hidden.

---

### C. How the bundle is built — and why its diagnostics vanished

**We build it, on our own host. It is not something MTA hands us.** MTA supplies the raw GTFS + STIF
zips; turning them into a bundle is our step, so the B1/Q84/Q30 defect sits in a stage we fully
control and can iterate on without waiting for anyone.

| | |
|---|---|
| inputs (MTA-supplied, staged by us) | `/data/bundle-src/wholeMTA/` — 6 GTFS zips (5 NYCT boroughs + `GTFS_MTABC`), 5 STIF zips, `NotInServiceDSCs.txt`, plus the unzipped `stif-c6-all/` |
| driver | `FederatedTransitDataBundleCreatorMain` with our Spring config `bundle-wholeMTA.xml` (committed: [`ec2/bundle/bundle-wholeMTA.xml`](ec2/bundle/bundle-wholeMTA.xml)) |
| our custom task chain | `clearCSVsTask` → `checkShapesTask` → **`stifLoaderTask`** (`StifImportTask`, `stifPath=/data/bundle-src/wholeMTA/stif-c6-all` — this is the task that builds the DSC→trip map) → `summarizeCSVTask`, all inserted before `transit_graph` |
| output | `/data/oba-bundle/2026C6_wholeMTA` (~675 MB), built 2026-07-21 14:41; log `build-wholeMTA.log` |
| trigger | **manual** — the procedure is documented in EC2-DEPLOYMENT §"Refresh the bundle", but there is **no build script** on the host (only app build/deploy/run scripts) and it is not in CI |

**Why the four STIF diagnostic CSVs were lost — a one-line omission in our own config.** The bean is
declared with no output path:

```xml
<bean id="multiCSVLogger" class="org.onebusaway...tasks.MultiCSVLogger"/>   <!-- no basePath! -->
```

`MultiCSVLogger.postConstruct()` then falls back to `java.io.tmpdir` and warns
`MultiCSVLogger initialized without path: using /tmp` — a warning log4j (never initialised for the
build) swallowed. Both `clearCSVsTask` and `summarizeCSVTask` *did* run per the build log, so
`stif_trips_with_no_gtfs_match.csv`, `trips_with_duplicate_run_and_start_time.csv`,
`trips_with_null_dscs.csv`, `stif_trip_layers_with_missing_route.csv` and `summary.csv` were all
written to `/tmp` on 2026-07-21 and have since been cleaned up. (`gtfs_stats.csv` survives in the
bundle dir because a different task writes it with an explicit path.)

**So recovering the B1 drop reason needs no new data from MTA — just:**

```xml
<bean id="multiCSVLogger" class="org.onebusaway...tasks.MultiCSVLogger">
  <property name="basePath" value="/data/oba-bundle/csv-2026C6"/>
</bean>
```

…plus a log4j config on the build JVM, then rebuild and read those CSVs. Two other gaps worth closing
while in there: **script the bundle build** (it is currently manual and unreproducible) and **add the
per-route DSC-coverage gate** so a 13%-coverage route fails the build.

### D. Reproducing the bundle build locally (reusable)

Established 2026-07-28 — a zero-risk way to iterate on bundle defects without touching the EC2 host.

| piece | how |
|---|---|
| Java 11 (host uses Corretto 11.0.31; local machine has only 21, which this Hibernate 5.2 / Guice 3.0 stack won't run on) | `docker run --rm -v $PWD/buildkit:/work -w /work --memory 7g amazoncorretto:11 java -Xmx6g …` |
| the 254 build jars (128 MB) | copied from the host's own `/opt/oba/.m2` via the classpath recorded in `build-wholeMTA.log` → guarantees identical versions to the shipped bundle. Flat dir + `-cp "/work/jars/*"` (verified no basename collisions) |
| C6 GTFS + STIF inputs (78 MB zips; STIF is not public) | pulled off the host over an **SSM port-forward** to a temporary localhost-bound `python3 -m http.server`, sha256-verified, then the staging dir was deleted. The host role cannot `s3:PutObject`, so S3 is not a transfer path |
| scope | per-borough builds (`<ref bean="gtfs_b"/>` + a STIF tree containing only that borough) fit in a 6 g heap and finish in ~2 min, vs whole-MTA which does not fit Docker's 7.7 GB VM |
| fidelity check | a Brooklyn-only build reproduced the shipped bundle's DSC counts exactly (4011→7, 4060/4061/4062→155/440/327). **Always confirm this before trusting a scoped build.** |

Entry point: [`ec2/opt-oba/build-bundle.sh`](ec2/opt-oba/build-bundle.sh) (also usable on the host; it
refuses to write into the live bundle dir without `ALLOW_LIVE=1`), plus
[`ec2/bundle/log4j-bundle.properties`](ec2/bundle/log4j-bundle.properties).

## 5. Prediction differences

### C. Same route, different trip — **1.2% as of 07-30** (2.6% at the 07-29 PM peak, 3.4% before the fixes)

> The dual-key matcher and the MTABC STIF both cut this: more correctly-linked trips in the bundle means
> fewer wrong-but-adjacent candidates. The mechanism below is unchanged and still sets the floor — it
> cannot go to zero without UTS run data. The figures in this section are the 07-27 PM measurements,
> retained because that is where the persistence and per-pair analysis was done.

**Evidence.** 196 of 7,659 pairs. The two engines never disagree between *simultaneous* departures:
the start-time gap is median 10–15 min (only 3 cases under 1 min). In **137/196** the trip we chose is
in prod's feed **on a different vehicle** — prod knows which bus runs which departure; we are guessing
between adjacent ones. Persistence-checked: 80% of disagreeing vehicles still disagree with the
identical trip pair 90 s later, so it is an assignment difference, not snapshot timing.

Examples: `6113` M103 ours `094500_M101_133` vs prod `092100_M101_70`; `7124` B45 ours
`103200_B65_866` vs prod `102000_B45_816` (our trip on vehicle 7119).

**Root cause — code-confirmed.** Prod feeds inference UTS operator→run assignments; we have none, so
three of the four trip-selection likelihoods go flat: `RunLikelihood.java:66-68` (`NO_RUN_INFO` = 1.0),
`BlockLikelihood.java:83-84` (`NO_BLOCK_INFO` = 1.0), `RunTransitionLikelihood.java:127-133`
(constant). The only separator left is `ScheduleLikelihood`, which without a formal run uses an
**informal Student-T prior (dof 2, scale ≈ 9 min)** — `ScheduleLikelihood.java:42-43,166-168`. Two
departures 10–20 min apart differ by only 1–2 scale units, and same-start hypotheses are *exactly*
tied, so resampling decides. **A 2–3% adjacent-trip disagreement rate is the designed behaviour of
this codebase without UTS, not a bug.** Worse at peak, as expected.

**Impact is bounded:** position, route and near-term ETAs stay right; only the scheduled-departure
label is off. **Fix:** TDM crew API / YardTrek S3 access.

### D. Direction flips — 0.3%

25 pairs, and mostly *not* corridor confusion: both feeds pick the **same run** (`M98_905` vs
`M98_905`, `BX402_36` vs `BX402_36`) with trips 13–98 min apart — adjacent opposite-direction trips in
one block, around a terminal. One engine has advanced to the return trip; the other hasn't.

**Root cause:** wrong-direction snaps are rejected only once the bus has *moved* —
`BlockStateService.java:600-616` skips the ≥135° orientation test below the distance cutoff, and a NaN
observed heading counts as agreeing (`:609-613`). DSC can't break the tie because the DSC→route map
drops direction entirely (`DestinationSignCodeServiceImpl.java:136-159`). Transient, self-correcting
once underway; same UTS fix.

### E. Route confusion — 0.01%

1 pair in 7,659 (ours M1 vs prod M7 on a shared Manhattan corridor; earlier runs: BX1/BX2 on the Grand
Concourse, BX18A/BX18B branch variants). `DscLikelihood` hard-rejects only when the sign implies no
route at all; an exact-DSC trip scores 13/30 vs 8/30 for a same-corridor route-implied trip
(`DscLikelihood.java:144-166`) — a *preference*, not a rejection, and on shared geometry nothing
separates them until the shapes diverge. Noise level; no action.

### F. ETA deltas — and what actually drives the "early bias"

Big-delta TripUpdates fell from **7.2% (07-22) to 3.5% (07-27)**. Shapes: 205 grow-downstream drift,
49 whole-trip offset, 10 mid-trip bumps; 92% of big-delta stop-predictions sit at 30+ min.

The gtfsrt layer is **verified pass-through** — one `StopTimeUpdate` per `TimepointPredictionRecord`,
times copied verbatim, no extrapolation (`TripUpdateFeedBuilderImpl.java:118-120`,
`GtfsRealtimeLibrary.java:120-132`) — so everything here is engine-side. Decomposing it:

**F1 — rollover joins (measurement artifact, 0.5% of pairs).** Prod publishes current + next
TripUpdates; we publish only current. When our engine has rolled onto the next trip and prod hasn't,
the `trip_id` join matches **our in-progress trip against prod's not-yet-started trip**, so every ETA
differs by a near-constant offset measuring *rollover timing*, not quality:

| | prod-current pairs | rollover pairs |
|---|---|---|
| ALL | n=136,304, median −3, MAE 36 s | n=1,041, mean **−71**, MAE **108 s** |
| 30+ min | n=45,977, median −9, MAE 56 s | n=461, mean **−146**, MAE **193 s** |

Worked example: vehicle 8767 (Q84). Prod has it finishing `100400_MISC_837` with 3 stops left and
publishes `105500_MISC_837` as a future trip starting 17:35; we already have it *on*
`105500_MISC_837`. The join reported a −23 min "error" that is purely in-progress vs not-started.
**Fix:** restrict the ETA join to prod's current TripUpdate, and/or set `PredictionLevel=NEXT_TRIP`.

**F2 — position-anchor staleness (measurement artifact **through 2026-07-28**; premise inverted since
07-29 — read the box below before using any of this).**

> ⚠️ **This section describes the regime up to 2026-07-28, when prod's anchor was ~22 s older than
> ours. Since the 07-29 deploy, ours is ~39 s older than prod's (§2c), and the long-horizon bias got
> *deeper* rather than flipping sign — so the mechanism below cannot be the whole explanation.**
> The 07-30 control reads, local, median delta by distance × horizon:
>
> | local | 0–5 | 5–15 | 15–30 | 30+ |
> |---|---|---|---|---|
> | 0–50 m (07-27) | +3 | +2 | +0 | **−3** |
> | 0–50 m (**07-30**) | +0 | −1 | −6 | **−22** |
> | 50–150 m (07-30) | −7 | −11 | −15 | −28 |
> | 150–400 m (07-30) | −15 | −18 | −23 | −39 |
>
> Near-term is still ~0 where the feeds agree on position, but the 30+ residual the control used to
> remove is now **−22 s**; and express in that same stratum is now **+3/+4/+12/+0**, i.e. the F3
> residual is not visible. **Do not treat either as a new finding.** Under a 69 s anchor the strata
> stop meaning what they meant: "both feeds within 50 m" now selects disproportionately for slow and
> stationary buses, since that is where a 39 s anchor gap produces little distance. The control is
> confounded by the regression it would need to control for. **Re-derive the F2/F3 split once §2c is
> fixed**, and until then quote no bias attribution from it.

Prod's VehiclePositions **were ~22–25 s older** than ours (prod median 37–50 s vs ours 13–19 s). A
prediction anchored to where the bus *was* reads later — i.e. we look "early". Cross-tabbing median
delta by inter-feed position distance × horizon (trip-matched, rollover excluded), **as measured
2026-07-27**:

| local buses, distance between the two feeds' positions | 0–5 min | 5–15 | 15–30 | 30+ |
|---|---|---|---|---|
| 0–50 m (feeds agree on position) | **+3** | **+2** | **+0** | **−3** |
| 50–150 m | −4 | −6 | −10 | −15 |
| 150–400 m | −16 | −17 | −22 | −30 |
| 400 m+ | – | −56 | −80 | −60 |

Where both feeds place the bus within 50 m, the local bias is ~0 at short horizons and −3 to −13 s at
30+ min (two samples). It appears only as the anchors diverge, and the magnitude matches the mechanism:
150–400 m is roughly what a bus covers in ~22 s. **In that regime this was an artifact in our favour** —
our predictions were fresher, not faster. A uniformly optimistic engine would show its bias in the
position-agreeing stratum too, and it didn't. (Since 07-29 the freshness advantage is gone, so the "in
our favour" reading no longer applies — §2c.)

Also **ruled out: a deliberate policy difference.** Per-route 30+ min medians are two-sided —
SIM7 −338 s, SIM1 −218 s, QM63 −109 s at one end, **M11 +94 s, M5 +64 s, B15 +57 s, M4 +38 s** at the
other. A policy offset would be one-signed.

**F3 — the express residual (was "the one real engine gap"; now unresolved).** As measured 07-27, at
30+ min: local median −8 s vs **express −67 s**, with express staying at −41…−70 s *even in the
position-agreeing stratum* and deepening to −125 s at 150–400 m. Express MAE 88 → 119 s at a fixed
window — the only class not improving. Cause was attributed in §6 (shallow buckets on long links).

**The 07-30 run does not reproduce it.** Express in the position-agreeing stratum is now
**+3 / +4 / +12 / +0** across horizons, and at the fixed PM window express MAE *improved* 119 → 101 s
with its median moving −32 → −18 s. The per-route 30+ ordering inverted too: the negative end is now
Brooklyn/Queens **locals** (B16 −150 s, Q25 −120, B9 −103, Q60 −99) and the most positive route is
**SIM1C +66 s**, where SIM7/SIM1/QM63 used to sit at the negative extreme. Because the stratification
is confounded under a 69 s anchor (see the F2 box), this is **not** evidence that the express gap
closed — it is evidence that the measurement no longer isolates it. §6's history-depth argument stands
on its own per-link evidence and is unaffected; what is suspended is the *feed-comparison* read of
express bias. Re-measure after §2c.

### G / H. Two small known items

**G.** 35–50 vehicles appear in our VehiclePositions but not prod's; **none of them are in prod's
TripUpdates either**, and their position age skews old (p90 169 s). Prod filters unassigned/ghost buses
via `bustrek.mta.info/api/ghostbus/records`; we don't consume it. Benign.

**H.** 1.00 vs 1.82 TripUpdates/vehicle is entirely `PredictionLevel` (`CURRENT_TRIP` vs prod's
`NEXT_TRIP`), and fixing it also removes F1.

> ⚠️ **Corrected 2026-07-30: this is NOT "one seeded config key".** Earlier notes (including this
> document) called it an XS config change; reading the deployed code on the host disproves that.
> `PredictionsGeneratorService:271-279` resolves it via
> `_configService.getConfigurationValueAsString("predictions", "predictions.PredictionLevel",
> "CURRENT_TRIP")`, and our `_configService` is **`DummyConfigurationServiceImpl`**, whose
> `getConfigurationValueAsString(...)` **returns the caller's `defaultValue` unconditionally** and whose
> `setConfigurationValue(...)` has an **empty body**. So the value can never be anything but
> `CURRENT_TRIP`: there is no system property, no file, and no API that reaches it. The weights are
> settable only because they have a dedicated `WeightSetterAPI` controller (`/api/weight`) that bypasses
> the config service entirely — the predictions webapp's only other endpoints are `/cache-purge`,
> `/cache-historical`, `/cache-recent`, `/release` and `/tdf`.
>
> **Re-verified in depth 2026-07-30 (second pass, after challenge). The conclusion holds, and the
> mechanism is now fully understood:**
>
> - The stub is **hard-wired in Spring XML**, not selected by a property:
>   `application-context-webapp.xml:26` reads
>   `<bean id="PredictionsConfigurationService" class="...DummyConfigurationServiceImpl" /><!-- local: no TDM -->`.
>   `git status` on the host clone is clean, so this is the committed state, not a local edit.
>   `PredictionsGeneratorService` `@Autowired`s that bean. **`-Dtdm.host=` does not influence it.**
> - **Why the weights *do* work, and this doesn't.** `WeightSetterAPI` never touches the config service —
>   it calls a dedicated `PredictionsWeightsService.setPredictionWeights()` that sets the values directly
>   in memory. The config service is only the *initial* source
>   (`PredictionsWeightsServiceImpl:43-45`, defaults 100/0/0) — which is exactly why the stub yields
>   100/0/0 and why `oba-weights` has to re-POST after every restart. **There is no equivalent service or
>   endpoint for `PredictionLevel`**; `RefreshPredictionLevel()` only re-reads the stub.
> - The only other implementation, `PredictionsConfigurationServiceImpl`, is **TDM-backed** — it
>   `@Autowired`s `TransitDataManagerApiLibrary` and polls every 5 min.
>
> **So there is no flag, property, file, or endpoint that can set it as deployed.** Options:
>
> 1. **Make `DummyConfigurationServiceImpl` fall back to system properties** — *recommended*. ~5 lines in
>    one class, and it unlocks **every** `predictions.*` key via `-D`, not just this one: notably
>    `minuteIntervalsInDay` (the lever for the weekend-history problem, §6),
>    `historicalComponentRecordCount`, and `prognosticatorThreadCount`. It would also let the weights come
>    from `-D` and make the `oba-weights` unit redundant. Best value by a wide margin.
>    *(While in there: `getConfigurationValueAsBoolean` returns `null` instead of `defaultValue` — a
>    latent bug worth fixing.)*
> 2. **Add a `PredictionLevelSetterAPI`** mirroring `WeightSetterAPI` plus a re-apply unit. Runtime
>    settable without restart, but more code and it only solves this one key.
> 3. Change the hard-coded default at `PredictionsGeneratorService:273`. One line, but bakes it into the
>    build and leaves every other key unreachable.
> 4. Switch the bean to `PredictionsConfigurationServiceImpl` and stand up a TDM-shaped endpoint.
>    **Not recommended** — it would then govern *all* `predictions.*` keys including the weights,
>    conflicting with the `oba-weights` path, for no benefit over option 1.
>
> All need a **build + deploy** of the predictions webapp, so this is an S, not an XS.

---

## 5b. Data sources prod consumes that our instance does not

Verified on the host 2026-07-28 by reading the deployed Spring wiring and the service
implementations, cross-referenced against the prod TDM config dump (EC2-DEPLOYMENT §11). Our
deployment is **TDM-less** (`-Dtdm.host=` empty, `DummyConfigurationServiceImpl`), so every
TDM-backed feed is either stubbed or inert.

**How to tell a stub is wired:** the deployed contexts name them explicitly —
`onebusaway-nyc-vehicle-tracking-webapp/target/classes/data-sources.xml` wires
**`DummyVehiclePulloutService`** and **`DummyUnassignedVehicleServiceImpl`**;
`onebusaway-nyc-predictions-webapp/.../application-context-webapp.xml` wires
**`DummyConfigurationServiceImpl`**. `DummyVehiclePulloutService.java:54` is visible in the live log
~520 times per 25 min: `getAssignedBlockId for MTA NYCT_xxxx returning null`.

| # | data source | prod endpoint | consumed by / where | our state | observable consequence |
|---|---|---|---|---|---|
| 1 | **UTS crew / operator→run assignment** | **`http://tdm.prod.obanyc.com/api/crew/{YYYY-MM-DD}/list`** (port **80**). URL assembled by `TransitDataManagerApiLibrary` as `http://{tdm.host}:{_tdmPort}/api/` + `crew/{date}/list` (`_apiEndpointPath = "/api/"`, `_tdmPort = 80`). **Provenance of the hostname:** set as `<tdm.host>tdm.prod.obanyc.com</tdm.host>` in the prod Maven profiles of the sibling repo — `onebusaway-nyc-predictions/onebusaway-nyc-predictions-standalone/pom.xml:107,117,157` (host clone under `/opt/oba`; that repo is **not** cloned on the dev Mac, which is why a local-only search misses it); also the historical value in *this* repo before commit `62c1cc4b6` (2017-03-09, "changing tdm ref to dev until templating occurs") swapped it to dev; and it **resolves today** → `ec2-23-21-88-207.compute-1.amazonaws.com` / `23.21.88.207`. Other environments: `tdm.dev.obanyc.com`, `tdm.qa.obanyc.com`, `tdm.staging.obanyc.com` | `OperatorAssignmentServiceImpl:111-112` (`getItemsForRequest("crew", date, "list")`); feeds `RunLikelihood`, `BlockLikelihood`, `RunTransitionLikelihood`, and the *formal* branch of `ScheduleLikelihood` | **absent** — we launch with `-Dtdm.host=` **empty** (`run-predictions.sh`, `run-gtfsrt.sh`; `run-inference.sh` doesn't set it at all, and inference is the consumer that matters). `TransitDataManagerApiLibrary` skips building a client when the hostname is blank, so every TDM call is a silent no-op rather than a failed request | **class C + D**: 3 of 4 trip-selection likelihoods go flat → 2.6% same-route/wrong-trip, 0.3% direction flips |
| 2 | **Vehicle pull-in/pull-out (YardTrek)** | `s3://automatic-dob-uploads/yardtrek/runs-last-24-hours.json`; config key `tdm.vehiclePipoUrl` (`VehiclePulloutServiceImpl:131`) | block assignment at pull-out — gives inference the *actual* block a bus left the depot on | **stubbed** — `DummyVehiclePulloutService` returns null. Our creds get **403** on that bucket (needs a cross-account grant) | no depot-derived block prior; inference must infer the block from geometry alone |
| 3 | **Depot vehicle roster** | TDM `GET /api/depot/{depotId}/vehicles/list` (`VehicleAssignmentServiceImpl:78`); prod also `https://fleetview.mtabuscis.net/api/legacy/prservices` | which vehicles belong to which depot → narrows candidate routes | **absent**; we run `acceptAllVehicles=true` and bypass the depot filter | wider candidate set; contributes to classes C/E |
| 4 | **Ghost-bus / unassigned-vehicle filter** | `https://bustrek.mta.info/api/ghostbus/records`; config key `vtw.unassignedVehicleServiceUrl` (`UnassignedVehicleServiceImpl:190`) | suppresses vehicles with no valid assignment before publishing | **stubbed** — `DummyUnassignedVehicleServiceImpl` | **class G**: 35–50 vehicles in our feed that prod filters out |
| 5 | **CAPI cancelled trips** | `http://capi.prod.obanyc.com/api/canceled-trips.json`; config keys `capiService`, `capiStart`, `capiRefreshInterval` | inference skips cancelled trips when matching | **absent** | we can match a bus to a trip prod knows is cancelled |
| 6 | **APC occupancy** | `https://apc-bustrek-store.s3.amazonaws.com/latestdata.json` (**publicly readable** — the one source we could add today) | occupancy fields on output | **absent** | no occupancy in our feed; no effect on ETAs |
| 7 | **TDM configuration service** | TDM `GET/POST /api/config/...` | all `predictions.*` / `tds.*` tuning, incl. weights and `PredictionLevel` | **stubbed** (`DummyConfigurationServiceImpl`, defaults 100/0/0) | why `oba-weights` must `POST /api/weight` 20/40/40 on every restart, and why **`PredictionLevel` stayed `CURRENT_TRIP`** → **class F1/H** |
| 8 | **MTABC STIF** (bundle input, not an API) | MTA scheduling drop | `StifTripLoader` → DSC→trip + run data | **not provided** | **class A**: entire MTABC fleet absent (792 veh, 91 routes) |
| 9 | **Years of prediction history** (Mongo) | prod's own accumulated `AggregateLinkTimes` | historical component of the 20/40/40 blend | **7 days** | **class F3**: express long-horizon bias; see §6 |

### 5b.1 UTS crew data in detail — how it is used, and what breaks without it

This is the single most consequential missing feed, so it is worth spelling out. It is the root cause
of discrepancy classes **C** (same-route/wrong-trip, 2.6%) and **D** (direction flips, 0.3%).

**What the data is.** A per-service-date roster of **operator → run** assignments: which driver is
working which *run* today. A "run" (e.g. `B1-5`, `M15-402`) is a scheduled day's-work assignment; the
bundle links run → trips, so knowing the run narrows a bus to a handful of trips instead of the whole
route.

**How it reaches inference.**

1. Each BusTech AVL ping carries an `operatorID` designator and a `runID` designator.
2. `VehicleLocationInferenceServiceImpl:356-359` maps those onto the `Observation`.
3. The **operator id** is then resolved against the UTS roster
   (`OperatorAssignmentServiceImpl:111-112` → `GET /api/crew/{date}/list`) to produce the
   **`opAssignedRunId`** — an *authoritative* run for this vehicle right now.
4. Separately, the raw `runID` from the AVL is fuzzy-matched to produce `bestFuzzyRunIds` — a *guess*.

**We have step 1, 2 and 4, but not step 3.** The AVL run designators are well populated (~94% runID,
~87–93% operatorID), so we are not blind — but we only ever have the *fuzzy* run, never the
*confirmed* one. That distinction is what the code keys off:

| consumer | with UTS | without UTS (us) |
|---|---|---|
| **Candidate generation** — `BlocksFromObservationServiceImpl:362-368` adds `getTripIdsForRunId()` for the op-assigned run *and* the fuzzy runs | authoritative run contributes a small, correct trip set | only fuzzy runs contribute; wrong or empty run ⇒ wrong or no candidates |
| **`RunLikelihood`** (`:66-68`) — scores how well a hypothesis' run matches the observed run | discriminates strongly between adjacent departures | returns **`NO_RUN_INFO` = 1.0** for *every* hypothesis — completely flat |
| **`BlockLikelihood`** (`:83-84`) — scores against the assigned block | discriminates | returns **`NO_BLOCK_INFO` = 1.0** — flat (also needs YardTrek pull-out, §5b row 2) |
| **`RunTransitionLikelihood`** (`:127-133`) — penalises implausible run-to-run changes across a trip boundary | prevents nonsensical run switches at layovers | its discriminating branches need run data ⇒ **constant** |
| **`ScheduleLikelihood`** (`:42-43,166-168`) — the schedule-adherence prior | uses the **formal** run prior (tight) | falls back to the **informal** prior: Student-T, **dof 2, scale ≈ 9 min** |
| **Snap gating** — `BlockStateService._requireRunMatchesForNullDSC` (`:414,495`) only allows snapping to op-assigned or best-fuzzy runs when the DSC is unusable | a valid run rescues buses with unknown DSCs | nothing rescues them — this is why the B1/MTABC DSC gaps become *total* absence rather than degraded accuracy |

**Why that makes trip matching worse, concretely.** Three of the four trip-selection likelihoods go
flat, so the particle filter is left with `ScheduleLikelihood`'s informal prior as effectively the only
discriminator between candidate trips on the same route. With a **9-minute scale**, two departures
10–20 minutes apart differ by only ~1–2 scale units — a weak preference, not a decision. And for two
candidate trips with the *same* start time the geometry is identical, so Gps/Edge/Dsc scores tie
exactly and the choice reduces to a **particle-resampling coin flip**.

That is exactly the measured signature: the start-time gap between our trip and prod's is **median
10–15 min** with *zero* cases below 1 min, and in **137/196** cases the trip we picked is in prod's feed
**on a different vehicle** — i.e. prod knows which bus has which run and we are guessing between
neighbours. It is worse at PM peak because peak means more layovers, reliefs and closely-spaced
departures, which is precisely the regime the run data disambiguates. **A 2–3% adjacent-trip
disagreement rate is the designed behaviour of this codebase without UTS, not a bug** — and it also
caps how much of the remaining gap any prediction tuning could close.

**Sources we *do* share with prod:** the BusTech AVL stream itself (RabbitMQ, `-Doba.rmq.*`) — and
usefully, those pings already carry `operatorID` and `runID` designators (~94% / ~87–93% populated),
which `VehicleLocationInferenceServiceImpl:356-359` maps in. So **fuzzy** run matching has input
today; what is missing is the *formal* UTS assignment (#1). Depot rosters also flow live on RabbitMQ
`nyct.bus.spear` (archived at `s3://mtalirr/data-archiver/busSpear/`), so #3 is obtainable without the
TDM.

**Cheapest wins, in order:** #6 (public URL, no access needed) → #3 via the `busSpear` queue we
already receive → #7 partial (set `PredictionLevel=NEXT_TRIP`, one seeded key) → #1/#2 (need access
grants; largest accuracy payoff).

## 6. The prediction history: state, effect, and timeline

The prognosticator blends schedule/recent/historical **20/40/40** per link and chains
`arrival[n] = arrival[n-1] + linkTime[n]`. **Where a link has no historical record the 40% silently
reverts to schedule** (PRODUCTIONIZING §3) — so warm-up is the mechanism that should close the gap.

**Bucket key:** `{routeId, headId, tailId, timeOfDay, scheduleType}` in Mongo `AggregateLinkTimes` —
one directed stop-pair, one route, one clock hour, one day-type; `traversalTimes` `$slice`-capped at
**300** with a precomputed median. **`timeOfDay` is the UTC hour**, verified against raw
`LinkTravelTimes` timestamps (harmless within a season since the offset is whole hours; **re-check at
the 2026-11-01 DST change**).

**State at history day 7 (2026-07-28):** 904,289 buckets, 8.04 M observations, mean depth 8.9.
**Nothing is at the 300 cap — the deepest bucket anywhere is 157.** 59% of buckets hold ≤5 traversals;
only 0.6% hold ≥50 (2.0% of weekday buckets). Weekday mean depth 19.0 vs Saturday 3.5 / Sunday 3.0.
Deepest hours: local 07:00 (27.8) and 08:00 (27.1), then 18:00 (25.2). Chart:
`history-fill-2026-07-28.png`.

**Depth demonstrably buys agreement with prod.** Measured per-link — `(ours[i]−ours[i−1]) −
(prod[i]−prod[i−1])`, which isolates one link instead of accumulated offset — deduped, and stratified
by link duration (deep buckets sit on high-frequency routes with short links):

| | deep (n≥50) | shallow (n≤5) | overall |
|---|---|---|---|
| per-link MAE | **5.1–5.6 s** | 12.4–19.8 s | 5.6–6.2 s |
| stop-level MAE | **21–27 s** | 39–75 s | 25–40 s |
| relative error (links ≥20 s) | 4.3–4.9% | 7.2–8.8% | 5.7–6.4% |
| **links >180 s** | **8.2–8.7 s** | 22.7–42.4 s | 13.5–16.5 s |

Monotonic in depth, and it survives the duration control in every band. But the honest framing is
three-tiered: **~8–10% better than the overall average** (most links already have *some* history),
**2.4–3.5× better than genuinely shallow buckets**, and **the gain concentrates almost entirely on long
links** — short links agree regardless.

**This closes the express question.** Express is **6× over-represented among shallow links** (17.3% vs
2.8% of deep — QM63/64/68, SIM1C/23/24/30/33C, X27/28/38) *and* has the longest links: the two
conditions that cost the most. The express-only bias in F3 is the shallow-bucket penalty landing on the
service class with the longest links — sample depth, now evidenced directly rather than inferred.

**Time to fill.** Rate ≈ `60/headway_min` per qualifying day, and
`weeks to N ≈ (N − depth) / (rate × qualifying_days_per_week)` where that last term is **5 for weekday
buckets but 1 for Saturday and 1 for Sunday** — the structural reason weekend history stays ~5× behind.
Measured per bucket against 4.89 elapsed weekday-equivalents:

- **to the 300 cap:** weekday 39% within 13 weeks, 75% within 26; **Saturday 58% and Sunday 69% need
  2+ years**.
- **to depth 50** (the tier that actually buys accuracy): **weekday 80% within 4 weeks**, 92% within 8;
  weekend 13–26+ weeks.

**Don't wait for 300** — it is a rolling window, and the benefit saturates near 50 (depth 80–199 barely
beat 50–79). **Re-measure the weekday A/B in late August 2026.** Weekend accuracy needs a coarser key
(fold Sat+Sun, or widen `minuteIntervalsInDay` past hourly), not patience. And **MTABC/B1 buckets never
fill at all** — every day those bundle fixes wait is also a day of lost history for those routes.

---

## 7. Measurement lessons (apply these before believing any future number)

The biggest risk in this work is attributing a measurement artifact to the engine. Nine controls, all
learned the hard way:

1. **Compare identical clock windows.** Time of day dominates day-to-day deltas. The live runs said
   express *improved* 108 → 97 s; the fixed archive window said it **worsened 88 → 119 s**. Use
   `compare-archives.py`.
2. **Stratify any bias claim by inter-feed position distance** before blaming the engine (F2).
3. **Exclude rollover joins** — prod's next-trip TripUpdates masquerading as disagreement (F1).
4. **Check per-route coverage, not just the aggregate** — B1 at 0% hid inside 97.4% NYCT.
5. **Dedupe repeated snapshots.** 10 s polling re-measures the same link dozens of times; raw counts
   overstated one sample ~30×.
6. **Control for link duration** when comparing link-level error — short links cannot disagree much.
7. **Hold the agency fixed across 2026-07-29** (`AGENCY=NYCT`). MTABC joined with zero history *and*
   matches trips better than NYCT, so a blended number moves for two unrelated reasons at once. This is
   what made 07-29 read as "NYCT improved" when NYCT had in fact regressed.
8. **Measure anchor age, not feed age.** `header.timestamp` says when the feed was *published*;
   `vehicle.timestamp` says how old the evidence behind it is. The first stayed at 5 s while the second
   went 15 → 69 s (§2c). Every prior run's "we are 2–3× fresher than prod" claim came from the second,
   and it silently stopped being true.
9. **Re-verify an inherited control, don't assume it.** F2's artifact explanation was correct when
   established and false eight days later, because the platform changed underneath it. A control that
   is not re-measured is an assumption.

Also always check: host load-shedding state (`InferenceShedFixes` — **0 through 07-28, nonzero at every
peak since 07-29**), bundle pick changes (re-run `verify-parity.py`), and our own config drift.

---

## 8. Actions, in impact order

| # | action | class | blocker | effort |
|---|---|---|---|---|
| **1** | **Restore position freshness: deadband `minIntervalSec` 7 → 15 and `shed.maxAgeSec` 50 → 30** (keep `minMeters=10`, `maxAgeSec=30`; **not** 10 s — measured at −2.4%, ineffective). Predicted anchor age ~17 s vs prod's 32 s, i.e. parity with margin. Anchor age 69 s is costing ~2× near-term ETA MAE and has invalidated the F2/F3 controls; everything ETA-related is blocked behind it. Full justification + verification checklist in §2c | **I**, F2, F3 | none | XS |
| **1a** | **Resize to c7i.12xlarge in an off-peak window** (§2d) — chosen remedy now that cost is accepted; keeps the 7 s deadband and a ~15 s anchor age. Then revert `minIntervalSec` to 7 | I | off-peak window | S |
| **1b** | Cut `numberOfProcessingThreads` 642 → ~64, and bound/synchronise `_inferenceProcessingThreads` (O(n²) unsynchronised hot path; also makes `InferenceBacklogThreads` meaningful) | I | small code change | S |
| **2** | **Publish `PositionAnchorAgeSeconds` to CloudWatch + alarm at 40 s** — i.e. tied to the parity target (prod ~32 s), not an arbitrary number. `FeedStalenessSeconds` is blind to this by construction; a 4.6× regression ran a full day unseen. `monitor.sh` already polls the feed, so this is a few lines | I | none | S |
| 3 | **Add a build gate: per-route DSC coverage ≥90% fails the build** — still open, and still the thing that would have caught B1 before deploy | B | none | S |
| 4 | Obtain **UTS run/operator assignments** (TDM crew API / YardTrek S3) | C, D | access grant | M |
| 5 | Set `PredictionLevel=NEXT_TRIP` (feed-shape parity; also kills F1). **Not a config key** — the stubbed config service ignores it, so this needs a code change + deploy (§5 H lists three options; making the dummy read system properties is the best) | F1, H | none | S |
| 6 | Restrict the ETA join to prod's current TripUpdate | F1 | see note | XS |
| 7 | Re-measure the weekday A/B **late August**, when ~80% of weekday buckets pass depth 50 — **only meaningful once #1 is done** | F3 | #1, time | S |
| 8 | Decide on a coarser weekend bucket key (fold Sat+Sun / wider intervals) — needs its own A/B | F3 | design call | M |
| 9 | Keep the per-route coverage gate in every run | B | done | — |
| 10 | **Ground-truth scoring harness** — the only way to answer "who is right" | all | PRODUCTIONIZING §5 | L |
| 11 | Optional: consume the ghostbus feed to match prod's filtering | G | none | S |
| ~~—~~ | ~~diagnostics + rebuild / dual-key matcher~~ → **DONE 07-29**, deployed | B | — | — |
| ~~—~~ | ~~script the bundle build~~ → **DONE**: `build-bundle.sh` (its frozen classpath file should be resolved fresh — §4C) | B | — | — |
| ~~—~~ | ~~obtain **MTABC STIF** + rebuild~~ → **DONE 07-29**: 97–98% coverage, 0 routes missing | A | — | — |

**Note on #6:** deliberately not applied yet — it would redefine the headline MAE mid-series and break
comparability with the 07-22 → 07-27 baselines. Cost of leaving it is quantified (0.5% of pairs, and it
is down to 0.2% as of 07-30, worth <1 s of ALL-bucket MAE). Bundle it with the next change that forces a
new baseline — **the §2c freshness fix is exactly that change**, so apply #5 and #6 alongside it and
re-baseline once.

**Explicitly not recommended:** tuning `ScheduleLikelihood`'s informal precision or the DSC route-match
ratios to raise agreement with prod. That optimises toward prod's answers rather than correctness, and
the remaining disagreement classes are small and self-correcting.

---

## 9. What is still open

- **Restoring position freshness (§2c)** — the one item blocking everything ETA-related. Which remedy
  (resize vs deadband) is a cost decision that has not been made.
- **Whether the ETA regression fully reverses when freshness is restored.** The attribution is strong
  (near-term-only damage, dated to the deploy, fleet held fixed, config unchanged) but it is inferred
  from correlation across days, not from an intervention. Restoring freshness *is* the experiment.
- **The F2 / F3 split** — has to be re-derived, since the 07-30 control is confounded by the very
  staleness it would need to remove. Currently the local 30+ residual survives the control (−22 s) and
  the express one does not appear at all; both readings are suspect.
- **Which engine is more accurate** at long horizons — unanswerable by feed comparison; needs ground
  truth. Everything above measures *agreement*.
- **Does the read path use the same UTC hour convention** as the write path? Almost certainly yes
  (same clock), but the predictions repo is not cloned locally, so it was not code-verified. Re-check
  at the DST change.
- **Express trajectory** — 88 → 119 → 101 s at the fixed PM window, but the last point is inside the
  stale-anchor regime, so the trend is not readable yet. Still wants a Mon-vs-Mon window.
- **Weekend accuracy** — structurally slow to warm; needs the §8 #8 decision.
- **`BXM18` / `QM32`** — the two MTABC routes still absent, plus ~15 MTABC vehicles.
- **Why prod covers B1 when our bundle build didn't** (§4B) — the dual-key matcher fixed our symptom;
  the upstream difference is still unexplained and would be better to fix at the input.

---

## 10. Tooling built along the way

| script | purpose |
|---|---|
| `compare-feeds.py` | main live comparison → dated report |
| `probe-coverage.py` | coverage/freshness probe **+ per-route NYCT coverage gate** |
| `analyze-discrepancies.py` | classifies diff-trip pairs and big ETA deltas by shape |
| `analyze-bias.py` | strips rollover joins, stratifies bias by position distance × horizon |
| `compare-archives.py` | replays S3 archives for the **same clock window** across dates; **`AGENCY=NYCT` holds the fleet fixed across 07-29** |
| `analyze-coverage-delta.py` | **(new 07-30)** splits the vehicle-count difference into ours-only vs prod-only and characterises each (age, agency, route spread, presence in the other feed's TripUpdates) — so the delta can be attributed instead of guessed |
| `analyze-anchor-age.py` | **(new 07-30)** position-anchor age at a fixed window across dates — separates *feed* age from *data* age; `AGENCY=` supported |
| `simulate-publication-expiry.py` | **(new 07-30)** tests a candidate feed-expiry cutoff against both archives — measures prod's actual cutoff, and the benefit/collateral of matching it |
| `simulate-deadband.py` | **(new 07-30)** replays the archived raw BusTech AVL through an exact port of `passesDeadband` to measure what load a candidate setting admits — lets deadband/capacity changes be tested offline, with no host access and without waiting for a peak |
| `analyze-history-depth.py` | history-depth A/B (deep vs shallow buckets) |
| `make-history-fill-chart.py` | regenerates the history-fill chart |
| `verify-parity.py` | static-GTFS id parity gate (re-run per bundle pick) |
