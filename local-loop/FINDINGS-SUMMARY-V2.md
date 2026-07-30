# Our OBA-NYC instance vs BusTech prod — status and open gaps

**As of 2026-07-30.** This document answers one question: **is our experimental OBA-NYC deployment
behaving comparably to BusTech prod, the incumbent production Bus Time system?** It covers the evidence
for comparability and the gaps that remain open.

> **A note on naming.** Both systems are MTA's — the distinction is the deployment, not the owner.
> **"BusTech prod"** is the incumbent production Bus Time system: vendor-operated, currently serving
> riders, and running the same OneBusAway codebase we do. **"Ours"** is the experimental OBA-NYC
> deployment this document reports on. Where **"MTA"** appears below it means the agency or its data
> owners — MTA Bus Company the operating division, or an MTA access grant — never the production system.

**Scope — this is deliberately not a complete history.** Issues already diagnosed and fixed (the missing
MTA Bus Company fleet, the absent B1/Q84/Q30 routes, the bundle-build defect behind them, and the
capacity regression found this morning) are **excluded**; only live gaps appear here. Full history,
root-cause detail, code citations, and the measurement methodology live in
[`FINDINGS-SUMMARY.md`](FINDINGS-SUMMARY.md); reproduction steps are in
[`COMPARISON-RUNBOOK.md`](COMPARISON-RUNBOOK.md).

---

## 1. Bottom line

**Yes — the deployment is comparable to production, and the remaining differences are understood.**

Our instance runs the same codebase off the same live bus-radio feed as production, and independently
produces predictions for the same fleet. Comparing the two feeds directly:

| | our instance | BusTech prod | verdict |
|---|---|---|---|
| routes covered | **all of them** | — | **at parity** — 0 routes missing from ours at AM peak |
| buses tracked | 3,290 at AM peak | 3,321 | **99%** |
| **buses on the same scheduled trip** | — | — | **98.7%** agreement |
| **predictions within 1 minute of BusTech prod's** | — | — | **85%** |
| **predictions within 3 minutes** | — | — | **98%** |
| systematic optimism / pessimism | — | — | **none** — typical difference a few seconds either way |
| age of GPS behind each prediction | **19 s** | 43 s | we are **~2× fresher** |

The three material gaps are all **missing input data, not defects in the engine**: we do not have
MTA's crew-assignment feed, we have 9 days of accumulated travel-time history where production has
years, and one feed-shape setting still differs. Each is quantified in §4 with what it would take to
close.

---

## 2. The evidence for comparability

All figures below come from directly comparing the two public GTFS-realtime feeds. Both use identical
route, stop, and trip identifiers (verified 100% against the published C6 schedule), so the feeds can be
joined bus-by-bus and stop-by-stop with no translation.

### 2.1 We track the same buses

| measured 2026-07-30 | ours | BusTech prod | coverage |
|---|---|---|---|
| AM peak (08:47) | 3,290 | 3,321 | NYCT 97.7–98.3%, MTA Bus 98.0% |
| midday (11:34) | 2,319 | 2,309 | NYCT 98.4%, MTA Bus 98.0% |

**One caution on these percentages:** they are *net* figures, and the net flatters us. There are buses
only we have *and* buses only BusTech prod has, and they roughly cancel out. At midday, 42 buses were ours-only
and 39 were BusTech-prod-only — a net of +3 concealing ~81 individual mismatches. §4.11 breaks that down, since
the two populations have entirely different causes and different fixes.

Routes present in BusTech prod's feed but missing from ours: **0 at AM peak**. (One route, `SIM8`, showed as
absent in the lower-fleet midday sample — noted in §4.6 for confirmation, since at midday a route may
have only one or two buses running.) We check this *per route*, not just in aggregate, because a single
absent route is invisible inside a 98% total — that is exactly how the B1 gap escaped notice for three
days before being fixed.

### 2.2 We put those buses on the same trips

For every bus in both feeds, do we agree which scheduled trip it is running?

| | share |
|---|---|
| **Same trip — full agreement** | **98.7%** |
| Same route, adjacent departure (we picked the trip before or after BusTech prod's) | 1.1% |
| Same route, opposite direction (bus at a terminal, mid-turnaround) | 0.2% |
| Different route — genuine disagreement | **0.0%** |

Agreement has improved steadily as coverage was fixed: 96.6% → 97.2% → 97.8% at an identical clock
window with the fleet held constant. The 1.1% adjacent-departure cases have a single known cause (§4.1)
and the 0.2% direction cases self-correct within a minute once the bus moves.

### 2.3 Our arrival predictions agree with production

This is the metric that matters most, so it is worth stating plainly. Comparing our predicted arrival
time against BusTech prod's for the same bus at the same stop, across **2.25 million individual stop predictions**
measured over an identical clock window with the fleet held constant:

| how close our prediction is to BusTech prod's | share of predictions |
|---|---|
| within 30 seconds | 65% |
| **within 1 minute** | **85%** |
| **within 3 minutes** | **98%** |
| within 5 minutes | 99% |

**Typical difference: about 5 seconds. Average difference: about 38 seconds.**

**Agreement is strongest where riders actually care** — for a bus arriving in the next 5 minutes, our
prediction is within a minute of BusTech prod's **99%** of the time. It loosens the further ahead the prediction
reaches, which is expected and is the direct consequence of our thin travel-time history (§4.2):

| how far ahead the prediction looks | average difference | within 1 min of BusTech prod | within 3 min |
|---|---|---|---|
| **0–5 minutes** | **13 s** | **99%** | 100% |
| 5–15 minutes | 21 s | 96% | 100% |
| 15–30 minutes | 33 s | 88% | 99% |
| 30+ minutes | 65 s | 68% | 94% |

So the pattern is: **near-term predictions are effectively interchangeable with production's, and
long-range ones are looser but still within 3 minutes 94% of the time.** Since a bus 30+ minutes away is
inherently the least predictable for either system, and since this is the bucket that improves as history
accumulates, this is the expected shape rather than a concern.

**There is no systematic bias.** We are not consistently optimistic or pessimistic. The typical
(median) difference is **−5 seconds** in the fixed-window baseline above and **+1 second** in today's
live measurement — both effectively zero against a 38-second average spread, and on opposite sides of it.
Per-route differences likewise fall on both sides of zero. That is the signature of two independent
estimators of the same quantity, rather than of one system systematically running early or late.

### 2.4 Our position data is fresher than production's

Every prediction is calculated from the last GPS report processed for that bus. Ours is **19 seconds**
old; BusTech prod's published positions are **43 seconds** old — so our predictions rest on roughly **2.4× fresher**
evidence. This is the one axis on which we currently out-perform production.

**Worth stating precisely, because it is easy to overclaim.** We do **not** have a better GPS feed. Both
systems receive the *same* bus-radio stream, which we measured at one report per bus every **5.6
seconds**. The difference is how much of it each system processes and republishes: BusTech prod down-samples to
about 30 seconds, and we down-sample less. Our own ingestion filter deliberately skips reports from buses
that have not moved — a stationary bus does not need reprocessing — so we effectively sample a **moving**
bus every ~11 seconds and a stopped one every ~30.

So the honest claim is **"we act on more of the same data, and our published positions are ~2.4×
fresher"**, rather than "our GPS is 6× finer". The 2.4× figure is the one measured end-to-end from both
public feeds, and it is the one that actually affects prediction quality.

### 2.5 Configuration matches production

Verified against a dump of production's own configuration: **prediction weights are 20% schedule / 40%
historical / 40% recent — identical to production's.** Every other prediction-tuning value matches the
defaults we run, with one exception, the feed-shape setting in §4.3. We consume the same bus-radio feed
(BusTech, via the same message queue) and were built from the same published C6 schedule.

---

## 3. What this does and does not prove

**It proves the deployment is sound:** it ingests the same data, tracks the same fleet, assigns buses to
the same trips, and produces predictions that agree with a mature production system within a minute
85% of the time — with no systematic bias and fresher underlying data.

**It does not prove which system is more accurate.** Every number here measures *agreement with
production*, not correctness. Where the two disagree, this comparison cannot say which one the bus
actually honoured. Answering that requires comparing both against observed actual arrival times, which
is a separate piece of work (§4.8). Agreement is the right test for "is this deployment behaving
sanely", and the wrong test for "is this deployment better".

That distinction matters for how these numbers should be used: **they support "reasonably comparable to
production", and they do not yet support "as accurate as" or "more accurate than" production.**

---

## 4. Active gaps

| # | gap | practical impact | size | what closes it | blocked on |
|---|---|---|---|---|---|
| 4.1 | **No crew / run-assignment feed** | 1.1% of buses put on an adjacent departure; 0.2% direction flips | small, bounded | access to MTA's TDM crew API | **MTA access grant** |
| 4.2 | **9 days of travel-time history** (production has years) | long-range predictions (30+ min) weaker: 65 s average difference vs 13 s near-term | moderate, shrinking | time; largely self-resolving | nothing — improves weekly |
| 4.3 | **We publish ~36% fewer predictions than production** — current trip only, where production also covers each bus's next trip | riders get no estimate from us for a bus still finishing its previous trip; production gives one | **larger than it looks** — see below | small code change | our own backlog |
| 4.4 | **Peak-load capacity verification** | unverified since this morning's resize | unknown until measured | one measurement at PM peak | ~4 hours |
| 4.5 | **Several production data feeds we don't consume** | cancelled trips, ghost-bus filtering, occupancy, depot rosters, depot pull-out | mostly cosmetic; two affect accuracy | access grants + integration | **MTA access grants** |
| 4.6 | **2 MTA Bus routes still absent** (`BXM18`, `QM32`), possibly a third (`SIM8`, seen absent once — unconfirmed), plus ~15 buses | 2–3 routes out of roughly 250 | very small | schedule-data investigation | our own backlog |
| 4.7 | **Express routes are our weakest class** | long-range express predictions differ most from production | small share of fleet | follows from 4.2 | nothing — improves weekly |
| 4.8 | **No ground-truth measurement** | cannot claim we are *more* accurate, only comparable | — | build a scoring harness against actual arrivals | our own backlog |
| 4.9 | **No alert on prediction-data freshness** | a freshness regression ran ~1 day unnoticed | process risk | add one metric + alarm | our own backlog |
| 4.10 | **No schedule-data quality gate in the build** | a bad schedule build could silently drop a route again | process risk | add a coverage check that fails the build | our own backlog |
| 4.11 | **~40 buses each way appear in one feed but not the other** | net coverage looks near-parity while ~80 buses individually mismatch | small, and roughly self-cancelling | two separate fixes — see below | one is ours, one needs MTA data |

### 4.1 The crew feed — the single most valuable thing we could be given

**This is the largest remaining accuracy gap and it is purely an access question.**

Production receives a daily roster of which driver is assigned to which *run* (a run being a scheduled
day's work, e.g. `B1-5`). Knowing the run narrows a bus to a handful of candidate trips instead of every
trip on the route. We do not receive it.

Without it, three of the four signals the engine uses to choose between candidate trips go flat, leaving
only a broad schedule-adherence prior that cannot reliably separate departures 10–20 minutes apart. When
we and production disagree on which trip a bus is running, the departures are a median 10–15 minutes
apart and in 70% of cases the trip *we* chose is genuinely running in production's feed — on a
*different* bus. In other words we are choosing plausibly between neighbouring departures rather than
failing; production simply knows the answer and we are inferring it.

**Impact is bounded and does not affect what riders see most:** the bus's position, its route, and
near-term arrival estimates all remain correct. Only the scheduled-trip label is off. A 1–2%
adjacent-departure disagreement rate is the expected behaviour of this software without crew data — not
a defect.

**Ask: read access to the TDM crew API** (`/api/crew/{date}/list`). This would also fix 4.1's companion
direction-flip cases and is the prerequisite for closing most of the remaining trip-agreement gap.

### 4.2 Travel-time history — resolves itself, on a known schedule

The engine blends schedule, recent, and historical travel times 20/40/40. Where a road segment has no
historical record, that 40% quietly falls back to schedule — which is why our long-range predictions are
our weakest and why they will improve without any intervention.

We have measured that this genuinely matters and by how much: on road segments with substantial history,
our per-segment predictions differ from production's by 5–6 seconds, against 12–20 seconds on segments
with little history. The benefit concentrates on long segments, which is why express routes (4.7) are
the worst-affected class.

**Timeline: about 80% of weekday segments reach a useful depth within roughly 4 weeks**, and the benefit
saturates well before the engine's storage cap — so this should be substantially closed by late August
2026 without action. **Weekend coverage is the exception:** a Saturday only accumulates history on
Saturdays, so weekend segments fill roughly 5× slower and will need a deliberate change (grouping
weekend days together) rather than patience.

### 4.3 We publish about a third fewer predictions than production

Production publishes predictions for each bus's current *and* next trip; we publish the current trip
only. Measured directly over 2,229 buses present in both feeds:

| | ours | production |
|---|---|---|
| prediction sets per bus | 1.00 | **1.86** |
| stop predictions per bus | 17.7 | **30.2** |

**Production publishes 1.71× as many stop predictions as we do for the very same buses, and 36% of its
predictions are for trips we do not publish at all.** In rider terms: for a bus still completing its
previous trip, production can already tell you when it will reach your stop and we cannot.

**This also qualifies the agreement figures in §2.3.** Those compare predictions that *both* feeds
publish — which is 64% of production's output. The remaining 36% is not disagreement, it is silence on
our side. The 85%-within-a-minute figure is therefore a fair statement about the predictions we make,
and not a statement about covering everything production covers.

It is one behavioural setting, and it is the largest single parity gain available to us without new data
from MTA. Two things to plan around:

- **It will make our headline agreement numbers look worse before they look better.** Next-trip
  predictions are the hardest kind — they depend on when the bus finishes its current trip plus layover
  time — so adding them injects a large, long-horizon, high-error population into the comparison. Expect
  the "within 1 minute" figure to fall even though coverage improves. This needs a re-baseline, which is
  already required for other reasons.
- **Note for planning:** earlier notes recorded this as a trivial configuration change. It is not — the
  setting is read through a configuration service that is stubbed out in our deployment and returns
  defaults unconditionally, so it needs a small code change and a redeploy rather than flipping a value.
  The same fix would unlock the other prediction-tuning values we currently cannot reach, including the
  prognosticator's thread count (relevant, since this change increases its workload ~1.7×) and the
  interval setting behind the weekend-history problem in §4.2.

Worth noting for planning: earlier notes recorded this as a trivial configuration change. It is not —
the setting is read through a configuration service that is stubbed out in our deployment and returns
defaults unconditionally, so changing it requires a small code change and a redeploy rather than
flipping a value.

### 4.4 Peak-load capacity — the one open verification

The deployment was resized this morning (32 → 48 processing cores) after the fleet grew ~30% and pushed
it past its capacity at rush hour, which had degraded prediction freshness. Off-peak performance is
verified healthy — the freshness and no-bias figures in §2 are post-fix measurements. **The remaining
check is whether it holds at PM peak, when the fleet roughly doubles.** If it does not, the next step is
two known software inefficiencies (an oversized thread pool and an inefficient internal bookkeeping
structure) rather than more hardware.

### 4.5 Production data feeds we don't consume

| feed | what it does for production | effect on us |
|---|---|---|
| Cancelled trips | lets the engine skip trips that were cancelled | we may match a bus to a cancelled trip |
| Ghost-bus filter | suppresses buses with no valid assignment | ~30–50 buses appear in our feed that production filters out |
| Depot rosters / pull-out | narrows candidate routes; gives a starting block | wider candidate set, contributes to 4.1 |
| Occupancy (APC) | crowding data on output | no occupancy in our feed; no effect on predictions |

Occupancy is publicly readable and could be added at any time. The others need access grants. None
affects the core comparability picture.

### 4.11 The ~40 buses each way that don't match — and why

Comparing the two feeds bus-by-bus (`analyze-coverage-delta.py`, midday 2026-07-30):

| | count | their position age | in the other feed's predictions? |
|---|---|---|---|
| in both feeds | 2,298 | ours 16 s / BusTech prod 39 s | — |
| **ours only** | 42 | median 16 s but **p90 219 s, max 303 s** — a distinct stale tail absent from the shared baseline | **95% absent from BusTech prod's too** |
| **BusTech prod only** | 39 | median 40 s, **max only 65 s** | — |

**Scale first:** each population is about **1.5% of the fleet**, and because they are similar in size they
nearly cancel in the totals. That is why the headline "3,290 vs 3,321 buses" at AM peak looks tidier than
reality — the net is 31, but roughly **140 individual buses actually disagree**.

#### Both populations mostly come from one root cause

The single sentence that explains most of this: **production knows each bus's assignment; we have to infer
it.** The two populations are the false-positive and false-negative sides of that one gap.

| | production knows… | result |
|---|---|---|
| **Population A** | …this bus is **not** in revenue service, so it suppresses it | we cannot tell, so we infer a plausible trip and publish it — **false positives** |
| **Population B** | …this bus **is** in service, and on which run | we cannot place it at all, so it vanishes from our feed — **false negatives** |

Production gets that knowledge from three feeds we do not have: the **crew roster** (which driver has
which run today), **depot rosters** (which buses belong to which depot and are assigned), and **pull-out
records** (which block a bus actually left the depot on). We run with all three stubbed out and accept
every vehicle, so we are inferring from position and destination sign alone.

The one part *not* explained by that gap is a separate, simpler issue: our feed keeps stale buses longer
than production's does (below). Together they account for the whole difference.

#### Population A — buses we publish and BusTech prod doesn't (42)

This population splits roughly **1:3 between the two causes** — a quarter are simply stale, and three
quarters are buses reporting normally that production chooses not to publish.

**Tested against the archives** (`simulate-publication-expiry.py`, healthy post-resize window, 57 paired
snapshots, ~131k vehicle observations). Both feeds archive per-vehicle timestamps, so this is measured
rather than argued:

| | our feed | BusTech prod's feed |
|---|---|---|
| position age, buses in both feeds | median 9 s, p99 34 s, max 204 s | median 15 s, p99 32 s, **max 120 s** |
| position age, ours-only buses | median 15 s, **p90 190 s, max 300 s** | — |

**BusTech prod's expiry is now a measured fact, not an inference: their published ages stop dead at exactly
120 s, with zero vehicles beyond it.** Ours stop at 300 s. So both systems expire stale buses; ours is
simply 2.5× more permissive.

Simulating a tighter cutoff on our side — what it would remove, and what it would cost:

| cutoff | population A removed | buses in *both* feeds wrongly removed |
|---|---|---|
| 90 s | 25% | 0.11% |
| **120 s** (matches BusTech prod) | **24%** | **0.02%** |
| 150 s | 18% | 0.00% |
| 180 s | 12% | 0.00% |

| # | hypothesis | evidence | confidence |
|---|---|---|---|
| 1 | **BusTech prod suppresses buses it considers unassigned or out of service, and we don't.** We accept every vehicle, have no depot roster, and the ghost-bus/unassigned filter is stubbed out | **Now the dominant cause: 76% of population A are *freshly reporting* buses (under 120 s), not stale records.** They are absent from BusTech prod's predictions too, i.e. BusTech prod is not tracking them at all rather than disagreeing. We even place them on routes — which raises the sharper possibility that some are buses we have confidently assigned to a trip they are not actually running | **high** |
| 2 | **Our publication expiry is looser than BusTech prod's** — 300 s against their measured 120 s | Real, and confirmed by direct measurement, **but it accounts for only ~24% of population A**, not most of it. Cheap to fix and essentially free of collateral damage (0.02%) | **high, but a minority of the problem** |
| 3 | **Cancelled trips.** BusTech prod skips trips it knows are cancelled; we never see the cancellation feed | mechanism is real but affects which trip we show more than whether the bus appears | low |

> **Correction.** An earlier version of this section put the expiry first at high confidence, reasoning
> from the stale tail (p90 190–219 s). The tail is real, but the *median* of 15 s was the more important
> number and was under-weighted: three quarters of these buses are reporting normally. The expiry is
> still worth shipping — showing a bus five minutes after its last report is bad on its own terms — but
> it is a partial fix, and the larger half of population A is about **assignment filtering**, which is
> the same missing depot/roster data as elsewhere in this document.

#### Population B — buses BusTech prod publishes and we don't (39 midday, ~85 at AM peak)

The tell here is the opposite: **every one of them is currently reporting** (max age 65 s in BusTech prod's feed)
and carries a route. So these are live, in-service buses that BusTech prod can place and we cannot — not stale
records or non-revenue vehicles. They are also spread across **59 routes with the top 5 holding only
17%**, which rules out a route-level schedule-data gap; that would concentrate, which is exactly how the
B1 and MTA Bus problems presented before they were fixed.

| # | hypothesis | evidence | confidence |
|---|---|---|---|
| 1 | **Without crew data we cannot place a bus whose destination sign is unusable.** To emit a bus at all, the engine must match it to a scheduled block, and it draws candidates from three places: the destination sign code, the run, and geometry. We never have a *confirmed* run — only a fuzzy guess from the radio — and the safeguard that would rescue a bus with an unusable sign code requires a valid run. Unusable sign + no confirmed run leaves **no candidates at all**, so the bus disappears entirely rather than appearing with degraded accuracy | Fits the thin spread across many routes, and fits the **doubling at peak (39 → ~85)** — peak means more reliefs, pull-outs and layovers, precisely the states run data disambiguates. Same root cause as §4.1 | **high** |
| 2 | **Buses that have just left the depot.** A bus deadheading out has no in-service sign yet; production gets its assignment from a depot pull-out feed we cannot access | explains a peak-weighted subset; consistent with #1's mechanism | medium |
| 3 | **Convergence lag from the capacity problem.** While the host was overloaded, a bus's reports could be repeatedly discarded, delaying our ability to place it | this morning's ~85 figure was measured *during* the overload; the resize should shrink it. **Testable at the next peak** | medium, and decreasing |
| 4 | **Buses on detours/reroutes** whose position will not match any known route shape | small residual; expected in any map-matching system | low |
| 5 | The 2–3 MTA Bus routes still absent (§4.6) | accounts for a slice of the MTA Bus portion only | high but small |

#### Possible fixes

| fix | addresses | cost | blocked on |
|---|---|---|---|
| ~~Tighten our publication expiry from 300 s to 120 s~~ | A2 — ~24% of population A | **DONE 2026-07-30**: live at 120 s, matching BusTech prod's measured cutoff. The position-age tail fell from 300 s to 124 s, 13–19 stale buses are dropped per refresh cycle, and buses we publish that prod does not fell from ~55 to ~37 | — |
| **Consume depot rosters** to filter yard, deadhead and out-of-service buses | A1 — the remaining ~76% | small integration | **nothing** — these already reach us on the radio message bus we subscribe to, no new access needed. **Now the higher-value of the two** |
| Obtain the **crew/run assignment feed** | B1, B2 — the bulk of population B | integration | **MTA access grant** (already the top ask in §5) |
| Obtain **depot pull-out data** | B2 | integration | **MTA access grant** (cross-account read, currently denied) |
| Re-measure at PM peak now the host is resized | quantifies B3 | one measurement | nothing |
| Consume the ghost-bus filter and the cancelled-trip feed | part of A1, plus A3 | small | low priority — the ghost-bus feed was found empty on inspection, so depot rosters are the better route to A1 |

**The useful conclusion:** population A is **entirely ours to fix and needs no data from MTA** — a
tighter expiry removes a quarter of it outright, and the depot rosters that would address the rest
already arrive on a message bus we subscribe to. Population B is **the same missing crew data already
identified as the top ask**, so it needs no separate programme; it is one more way to quantify what that
access grant would buy.

---

## 5. Summary of asks

**From BusTech prod — access only, no development:**

1. **TDM crew API read access** — the single highest-value item (§4.1); would close most of the
   remaining trip-agreement gap.
2. **Cross-account read on the depot pull-out data** (currently denied) — supports the same gap.
3. **Schedule data for `BXM18` / `QM32`**, or confirmation of how production covers them (§4.6).

**Ours to do, in priority order:**

1. Confirm capacity at PM peak (§4.4) — today.
1b. **Tighten the publication expiry 300 s → 120 s** to match BusTech prod's measured cutoff, and **consume the
   depot rosters we already receive** to filter out-of-service buses (§4.11). Both are entirely ours and
   need no MTA access; together they address the buses we carry that production doesn't.
2. Add a prediction-freshness metric and alarm (§4.9), and a schedule-data quality gate in the build
   (§4.10). Both are small and both close process risks that have already bitten once.
3. Publish next-trip predictions to match production's feed shape (§4.3).
4. Re-measure the history effect in late August, when weekday coverage should be substantially filled
   (§4.2), and decide on the weekend approach.
5. Build the ground-truth scoring harness (§4.8) — the only way to move from "comparable to production"
   to a statement about which is more accurate.

---

## 6. How to verify any of this

Every figure is reproducible from public feeds plus our archived history. The tooling and procedure are
documented in [`COMPARISON-RUNBOOK.md`](COMPARISON-RUNBOOK.md); briefly:

| claim | how it was measured |
|---|---|
| coverage, per-route coverage, freshness | `probe-coverage.py` — live snapshot of both feeds |
| trip agreement, prediction agreement | `compare-feeds.py` — 4 snapshots 60 s apart, ~150k matched predictions |
| the cross-day figures in §2.2/§2.3 | `compare-archives.py` — replays both feeds from archive at an *identical* clock window, holding the fleet fixed, 1.4–2.3 M predictions |
| no-systematic-bias claim | `analyze-bias.py` — removes two known comparison artifacts before measuring |
| history effect and timeline | `analyze-history-depth.py` |

**One methodological note, because it changes numbers materially:** day-to-day comparisons must use an
identical clock window and hold the fleet composition fixed. Time of day and fleet changes move these
metrics more than real engine changes do, and comparing across them has produced wrong conclusions in
this work more than once. The §2.3 figures are from fixed-window, fixed-fleet measurements for that
reason.
