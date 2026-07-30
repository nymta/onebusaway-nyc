> **This is the running log** — one dated section per run, appended over time, oldest first.
> For the consolidated picture (all discrepancy classes, root causes, actions) read
> [`FINDINGS-SUMMARY.md`](FINDINGS-SUMMARY.md); to re-run anything see
> [`COMPARISON-RUNBOOK.md`](COMPARISON-RUNBOOK.md).

# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-22 12:25:56 EDT   ·   4 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 12:22:54 | 1721 | 7 s | 2175 | 23 s | 1686 (98% of ours) | 32065 |
| 2 | 12:23:55 | 1717 | 5 s | 2171 | 31 s | 1685 (98% of ours) | 31844 |
| 3 | 12:24:55 | 1717 | 5 s | 2180 | 19 s | 1693 (99% of ours) | 32256 |
| 4 | 12:25:56 | 1717 | 4 s | 2181 | 27 s | 1692 (99% of ours) | 31875 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 6575 | 97.3% |
| same route, different trip_id | 157 | 2.3% |
| same route, different direction | 23 | 0.3% |
| different route | 1 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `7174` B6: ours `C6-Weekday-SDon-074600_B6_230` vs MTA `C6-Weekday-SDon-074600_B6_257`
- `9483` M103: ours `C6-Weekday-SDon-072100_M101_54` vs MTA `C6-Weekday-SDon-072900_M101_67`
- `5827` M103: ours `C6-Weekday-SDon-072100_M101_54` vs MTA `C6-Weekday-SDon-070500_M101_72`
- `8473` Q27: ours `C6-Weekday-SDon-070400_Q27_403` vs MTA `C6-Weekday-SDon-069400_MISC_194`
- `7219` B48: ours `C6-Weekday-SDon-072800_B48_415` vs MTA `C6-Weekday-SDon-074800_B48_403`
- `8585` S59: ours `C6-Weekday-SDon-073600_MISC_720` vs MTA `C6-Weekday-SDon-071600_S59_444`
- `8164` S78: ours `C6-Weekday-SDon-072500_S78_249` vs MTA `C6-Weekday-SDon-071000_S78_236`
- `739` BX21: ours `C6-Weekday-SDon-069100_BX21_424` vs MTA `C6-Weekday-SDon-067100_BX21_423`
- `257` B4: ours `C6-Weekday-SDon-071800_B4_3` vs MTA `C6-Weekday-SDon-068800_B4_14`
- `9522` B12: ours `C6-Weekday-SDon-071100_B15_136` vs MTA `C6-Weekday-SDon-071700_B83_308`
- `7337` B46: ours `C6-Weekday-SDon-065700_B46_410` vs MTA `C6-Weekday-SDon-074800_B46_410`
- `8837` Q1: ours `C6-Weekday-SDon-072800_MISC_184` vs MTA `C6-Weekday-SDon-070800_MISC_174`

Different-route examples (vehicle: ours route/trip vs MTA route/trip):
- `5806`: ours BX2 `C6-Weekday-SDon-067200_BX1_110` vs MTA BX1 `C6-Weekday-SDon-076600_BX1_125`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 128040 | +2 | +4 | 53 | -75 / +89 | 70% | 96% | 99% |
| 0-5 min | 19804 | +0 | +1 | 22 | -33 / +33 | 96% | 100% | 100% |
| 5-15 min | 32608 | -1 | +1 | 37 | -53 / +58 | 83% | 99% | 100% |
| 15-30 min | 35925 | +3 | +5 | 55 | -77 / +91 | 66% | 98% | 99% |
| 30+ min | 39703 | +10 | +7 | 81 | -123 / +125 | 49% | 91% | 98% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 124928 | +2 | +3 | 52 | -73 / +87 | 70% | 97% | 99% |
| express | 3112 | -2 | +18 | 113 | -148 / +234 | 38% | 79% | 93% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 16 / mean 19.1, MTA median 16 / mean 19.4
- TripUpdates per vehicle: ours mean 1.00, MTA mean 1.86 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (0): —
- routes seen only in MTA (74): B1, B100, B103, BM1, BM2, BM3, BM4, BM5, BX23, BX95, BXM1, BXM10, BXM11, BXM2, BXM3, BXM4, BXM6, BXM7, BXM8, BXM9, Q06, Q07, Q08, Q09, Q10, Q100, Q101, Q102, Q103, Q104, Q11, Q110, Q111, Q112, Q113, Q114, Q115, Q18, Q19, Q22, Q23, Q25, Q26, Q28, Q32, Q33, Q35, Q37, Q40, Q41, Q47, Q49, Q50, Q51, Q52+, Q53+, Q60, Q63, Q64, Q65, Q66, Q69, Q70+, Q72, Q74, Q80, QM15, QM2, QM20, QM4, QM5, QM6, SIM10, SIM8
- avg vehicles/round: ours 1718, MTA 2177, overlap 1689

## Vehicle coverage (VehiclePositions probe, same session)

- ours 1,774 vehicles vs MTA 2,210 → overlap 1,736; **MTA-only = 479 (412 MTABC + 67 NYCT)**, ours-only = 35.
- **Our feed contains ZERO MTABC vehicles.** All 74 routes missing from our feed are MTABC-operated
  (former private lines Q06–Q115/B100/B103/BX23, express QM/BM/BXM/SIM8/SIM10). The only express we
  serve are the NYCT-operated SIM1C/SIM3C/SIM4C/SIM33C/X27/X28.
- Position freshness: ours median 9 s / p90 28 s vs MTA median 28 s / p90 39 s — the 5 s-vs-30 s
  cadence advantage is visible end-to-end.

## Root cause of the MTABC gap (code-confirmed)

The bundle was built with MTABC GTFS but **no MTABC STIF**, so MTABC has no DSC→trip sign-code map and
no run/operator data. Every MTABC DSC is classified unknown (`hasValidDsc=false`), which zeroes out all
three candidate-block sources in inference:

1. Snap generation is skipped entirely unless `hasRunResults() || hasValidDsc()`
   (`BlocksFromObservationServiceImpl.java:196-207`).
2. Even if reached, default-on `_requireDSCImpliedRoutes` + `_requireRunMatchesForNullDSC`
   (`BlockStateService.java:115,122,489-506`) discard every spatial snap (empty implied-route set,
   empty valid-run set). No config on this branch overrides either flag.
3. DSC/run candidate generation returns early with no trip ids
   (`BlocksFromObservationServiceImpl.java:357-373`).

`DscLikelihood` is NOT the blocker (unknown DSC scores neutral 1.0), and ingestion admits MTABC fine
(`acceptAllVehicles=true` bypasses the depot filter). The bundle's MTABC geometry IS in the spatial
index — candidates are only killed by the STIF-derived-data gates. **Fix: obtain MTABC STIF and rebuild
the bundle** (or relax the two BlockStateService filters, at accuracy risk).

## Verdict

- **Trip matching parity is excellent** where we cover: 97.3% identical trip_id; the ~2.3%
  same-route/different-trip cases are mostly adjacent scheduled trips (off-by-one on the same block),
  an expected inference ambiguity, plus rare direction flips (0.3%) and one route confusion (BX1/BX2).
- **Prediction times track MTA closely** despite zero historical warm-up: median delta +2 s, MAE 53 s,
  96% within ±3 min; deltas widen with horizon (MAE 22 s at 0–5 min → 81 s at 30+ min) as expected.
  No systematic bias (mean +4 s). Express (NYCT SIM/X) deltas are noisier (MAE 113 s, n=3.1k).
- **The one major issue is coverage, not quality: the entire MTABC fleet (~20% of vehicles, 74 routes)
  is absent** — see root cause above.
---

# Discrepancy deep-dive (2026-07-22 follow-up)

Method: `analyze-discrepancies.py` (3 rounds, 45 s apart; 5,179 vehicle-pairs, 5,027 trip-matched
TripUpdates) + code trace of the inference likelihood rules and the gtfsrt-webapp build path.
Question: are the discrepancies code-logic artifacts, cold-history effects, or expected noise?

## A. Same-route / different-trip (142 pairs, 2.7%) — cause: missing run/operator (UTS) data

**Data signature.** The two engines never disagree between *simultaneous* departures — the
start-time gap between our trip and MTA's is median **15 min** (0 cases < 1 min, max 60). In
**92/142** cases the trip we chose appears in MTA's feed **on a different vehicle** — i.e. MTA knows
which bus runs which departure; we're guessing between adjacent departures. Gaps go both ways
(we're ahead of MTA about as often as behind).

**Code mechanism (confirmed).** MTA production feeds inference UTS operator→run assignments; we have
none. Without them, three of the four trip-selection likelihood rules go flat:
- `RunLikelihood.java:66-68` → `NO_RUN_INFO` = 1.0 for every hypothesis;
- `BlockLikelihood.java:83-84` → `NO_BLOCK_INFO` = 1.0;
- `RunTransitionLikelihood.java:127-133` → constant (its discriminating branches need run data).

The only remaining separator between same-route candidates is `ScheduleLikelihood` — and with no
formal run it always uses the **informal** prior, a heavy-tailed Student-T (dof 2) with scale
≈ **9 min** (`ScheduleLikelihood.java:42-43,166-168`). Two departures 10–20 min apart differ by only
~1–2 scale units → a soft preference, and hypotheses for same-start trips are *exactly* tied (same
geometry ⇒ identical Gps/Edge/Dsc scores) → particle-resampling coin flip. So a ~2–3% adjacent-trip
disagreement rate is the expected behavior of this codebase without UTS, not a bug.

**Not a snapshot-timing artifact (checked 2026-07-22):** two paired snapshots 90 s apart showed 80%
of disagreeing vehicles still disagreeing with the *identical* trip pair (63/69), vs ~20% churn at
the edges (trip-boundary rollovers). The join also already tolerates rollover skew: MTA publishes
current+next trip per vehicle and we count a match against any of them. Persistent disagreement +
15-min departure gaps + reassignment-to-other-vehicles = assignment difference, not query timing.
(Note: the per-round pooling above re-counts persistent disagreements each round; unique vehicles
are ~⅓ of the 142 pairs.)

**Impact is limited:** the vehicle's position, route, and near-term stop ETAs are still right; what's
off is which scheduled departure label the predictions hang on.

## B. Direction flips (7 unique vehicles, 0.3%) — cause: layover ambiguity + direction-blind DSC

**Data signature.** These are not corridor-confusion: in most cases both feeds picked the **same run**
(`…B38_209` vs `…B38_209`; `…SBS15_421` vs `…SBS15_421`) with trips 32–137 min apart — i.e. adjacent
trips in the same block, opposite directions, around a terminal/layover. One engine has advanced to
the return trip and the other hasn't.

**Code mechanism (confirmed).** Wrong-direction snaps are rejected only when the bus has *moved*:
`BlockStateService.java:600-616` skips the ≥135° orientation test when `distanceMoved` is below the
cutoff, and a NaN observed heading is treated as agreeing (`:609-613`). A bus sitting at a terminal is
therefore never direction-filtered, and DSC can't break the tie because the DSC→route mapping drops
direction entirely (`DestinationSignCodeServiceImpl.java:136-159` reduces a DSC to one
route-collection id). With run rules flat (§A), both directions ride on equal-weight hypotheses until
the bus moves far enough. Transient and self-correcting once underway.

## C. Route confusion (1 pair in ~6,800, ~0.01%) — cause: corridor-shared DSC, soft route preference

Ours BX2 vs MTA BX1 — routes that overlap on the Grand Concourse. `DscLikelihood` only hard-rejects
when the candidate's route isn't implied by the sign at all (`DSC_NO_ROUTE_MATCH` = 0,
`DscLikelihood.java:165-166`); an exact-DSC trip scores 13/30 vs 8/30 for a same-corridor
route-implied trip (`:144-164`) — a 13:8 *preference*, not a rejection — and if the two routes share
the sign code both score 13/30. On the shared segment geometry is identical, so nothing separates
them until the shapes diverge. At 1 case in ~6,800 pairs this is noise.

## D. ETA deltas > 3 min — cause: engine-side link-time differences, dominated by cold history

**Data signature (trip-matched TUs only, so no matching artifacts):**
- 364/5,027 TUs (7.2%) have ≥1 stop off by >3 min; but the *shape* is decisive: **297 (82%) are
  `drift_grows_downstream`** (agree at the bus's next stops, diverge cumulatively along the trip),
  61 mid-trip bumps, and only **6** disagree from the very first stop. Position/deviation tracking
  agrees; downstream *link travel times* differ.
- 92% of the big-delta stop-predictions sit at **15+ min horizon** (2,751 of 3,351 at 30+ min).
- Concentrated on **long trunk/express corridors** — M101/M7/M3/M102/M4, SIM1C/3C/4C, B15/B46 —
  where 40–80 links give small per-link differences room to compound.
- **No systematic bias**: end-of-trip delta median +3 s, p10/p90 ≈ ±100 s; among >3 min end-deltas,
  48 we-earlier vs 39 we-later.

**Code mechanism.** The gtfsrt-webapp is a verified pass-through — one `StopTimeUpdate` per
`TimepointPredictionRecord`, times copied verbatim, no schedule extrapolation
(`TripUpdateFeedBuilderImpl.java:118-120`, `GtfsRealtimeLibrary.java:120-132`) — so all long-horizon
behavior comes from the predictions engine. Per PRODUCTIONIZING.md §3, that engine predicts each
link's traversal time as a weighted blend (ours 20/40/40 schedule/historical/recent) and chains
`arrival[n] = arrival[n-1] + linkTime[n]`, with any missing historical/recent component **silently
reverting to schedule per-link**. With Mongo one day old, our effective blend is ~schedule-dominant
(recent kicks in per stop-pair after ≥1 observed traversal since restart; historical needs populated
time-of-day buckets). MTA production runs the same chaining with **years of warmed historical link
medians** (and unknown weights). Two different link-time estimates compounded over 40+ links is
exactly the observed drift — **so yes, this class is primarily the historical-data gap**, plus
whatever weight-config difference exists. Which engine is *right* at 30+ min is unknowable from this
comparison — that requires the ground-truth scoring harness (PRODUCTIONIZING §5).

## What would improve alignment

Alignment to MTA is a proxy, not the goal (the A/B test *wants* our predictions to differ where we're
better) — but removing artifacts, in impact order:

1. **Get UTS operator/run assignment data into inference** (the TDM `OperatorAssignmentService`
   feed, or depot assignments). Directly removes most of class A and B (~2.6% of vehicles) by making
   Run/Block likelihoods informative and enabling the formal schedule prior. This is the confounder
   PRODUCTIONIZING §5's fairness checklist already flags.
2. **Let Mongo warm up** (days–weeks), then re-run `compare-feeds.py` and watch the 15–30/30+ min MAE
   buckets — the class-D drift attributable to cold history should shrink; what remains is genuine
   config/cadence difference. (Track over time; today's baseline: MAE 55 s / 81 s.)
3. **MTABC STIF** (coverage — see root-cause section above; unrelated to these four classes).
4. Not recommended: tuning `ScheduleLikelihood`'s informal precision or the DSC route-match ratios to
   force agreement — that optimizes toward MTA's answers rather than correctness, and the remaining
   disagreement classes are small and self-correcting.
5. No action in the gtfsrt layer — verified pass-through; nothing there can cause or fix ETA drift.
---

# Re-run 2026-07-23 10:23–10:26 EDT (full output: `feed-comparison-2026-07-23.md`)

4 rounds × 60 s; 7,437 vehicle-pairs; ~137k matched stop-predictions. Baseline = 2026-07-22 12:22 EDT.

| metric | 2026-07-22 | 2026-07-23 | trend |
|---|---|---|---|
| same trip_id | 97.3% | **98.1%** | ↑ |
| same route, diff trip | 2.3% | 1.6% | ↓ |
| direction flips | 0.3% | 0.2% | ↓ |
| different route | 1 pair | 0 | ↓ |
| ETA MAE — ALL | 53 s | **45 s** | ↓ 15% |
| ETA MAE — 0–5 min | 22 s | 17 s | ↓ |
| ETA MAE — 5–15 min | 37 s | 30 s | ↓ |
| ETA MAE — 15–30 min | 55 s | 47 s | ↓ |
| ETA MAE — 30+ min | 81 s | 73 s | ↓ |
| ≤1 min agreement (0–5 min bucket) | 96% | 98% | ↑ |
| NYCT vehicle overlap | 98–99% | 99% | = |
| MTABC coverage | 0 | 0 (no bundle change) | = |

- Every horizon bucket improved ~10–15% — consistent with Mongo warm-up day 2 (more links with
  recent/historical data), **but time-of-day confounds it** (baseline was midday, this run late-AM);
  the archive-based utility comparing fixed clock windows is the clean way to track this.
- Slight positive shift at long horizon (30+ median +14 s vs +10 s: we now predict *later* than MTA)
  and express skew (+69 s median, n=2.9k, SI express in AM service) — watch, don't act.
- One route seen only in OURS this run (`Q90`, single vehicle): valid C6 GTFS route id (parity was
  100%); MTA simply had that vehicle elsewhere/idle at the time. Not a defect signature.
- MTABC gap unchanged, as expected — no MTABC STIF/bundle rebuild yet (75 routes absent).
---

# Re-run 2026-07-24 17:47–17:50 EDT — PM PEAK (full output: `feed-comparison-2026-07-24.md`)

4 rounds × 60 s; 10,007 vehicle-pairs; ~181k matched stop-predictions. First run sampled at PM peak
(load-shedding regime, ~3,400 MTA vehicles). History day 3.

| metric | 07-22 midday | 07-23 AM | **07-24 PM peak** |
|---|---|---|---|
| same trip_id | 97.3% | 98.1% | 96.5% |
| same route, diff trip | 2.3% | 1.6% | 3.4% |
| direction flips | 0.3% | 0.2% | 0.2% |
| ETA MAE — ALL | 53 s | 45 s | **41 s** |
| ETA MAE — 0–5 min | 22 s | 17 s | 16 s |
| ETA MAE — 5–15 min | 37 s | 30 s | 24 s |
| ETA MAE — 15–30 min | 55 s | 47 s | 36 s |
| ETA MAE — 30+ min | 81 s | 73 s | 70 s |
| ETA median — 30+ min | +10 s | +14 s | **−13 s** |
| NYCT vehicle overlap | 98–99% | 99% | 98–99% (holds under peak shedding) |
| vehicles ours / MTA | 1718 / 2177 | 1882 / 2382 | 2538 / 3406 |

- **ETA agreement improved again in every bucket** — at the hardest time of day (peak traffic + our
  load-shedding active). MAE trend over 3 days: 53 → 45 → 41 s. Time-of-day still confounds
  day-over-day deltas; the archive utility comparing fixed windows will clean this up.
- **Trip matching dipped at peak (96.5%)** — expected, not a regression: peak means more layovers,
  reliefs, and closely-spaced adjacent departures, exactly the regime where missing run-assignment
  data (discrepancy A) bites hardest. Direction flips stayed at 0.2%.
- **Sign flip at long horizon: we now predict EARLIER than MTA at PM peak** (30+ min median −13 s,
  mean −23 s; express −27 s median / −68 s mean, p10 −207 s). MTA's deep historical medians encode
  peak-traffic slowness that our 3-day history doesn't yet; whether their pessimism or our optimism
  is more accurate needs the ground-truth harness. Watch whether this narrows as peak-hour Mongo
  buckets warm.
- MTABC gap larger at peak (~850 vehicles, 93 routes — QM/BXM express concentrated here). Unchanged
  cause (no MTABC STIF).
- Coverage plumbing note: both our feeds are now archived to S3 (`obaEc2TripUpdates` /
  `obaEc2VehiclePositions`, 10 s cadence, validated 2026-07-24) — future comparisons can run over
  fixed archived windows instead of live sampling.
---

# Re-run 2026-07-27 08:45–08:48 EDT — Monday AM peak, history day 6 (full output: `feed-comparison-2026-07-27.md`)

First run with weekend schedule-type data in Mongo. 9,850 vehicle-pairs; ~167k matched stop-predictions.

| metric | 07-22 mid | 07-23 AM | 07-24 PM pk | **07-27 AM pk** |
|---|---|---|---|---|
| same trip_id | 97.3% | 98.1% | 96.5% | 96.7% |
| ETA MAE — ALL | 53 s | 45 s | 41 s | **37 s** |
| ETA MAE — 0–5 min | 22 | 17 | 16 | 18 |
| ETA MAE — 15–30 min | 55 | 47 | 36 | 36 |
| ETA MAE — 30+ min | 81 | 73 | 70 | **59** |
| ETA median — ALL | +2 | +4 | −4 | **−7** |
| ETA median — 30+ min | +10 | +14 | −13 | **−18** |
| express MAE | 113 | 109 | 108 | **76** |
| NYCT overlap | 98–99% | 99% | 98–99% | 98% (97.3% of MTA NYCT fleet, AM peak) |
| MTABC coverage | 0 | 0 | 0 | 0 (702 veh, 90 routes — unchanged cause) |

**Findings:**

1. **Agreement keeps tightening as history warms** — MAE 53→45→41→37 s over 6 days, with the
   biggest gains exactly where cold history hurt most (30+ min: 81→59 s; express: 113→76 s).
   Peak-hour trip matching is stable at ~96.5-96.7% (the known UTS-gap level).
2. **NEW: a systematic early-bias is growing at long horizons** (ours − MTA): overall median
   +2 → +4 → −4 → −7 s; at 30+ min +10 → +14 → −13 → **−18 s** (p10 −97 s). We increasingly predict
   arrivals *earlier* than prod, and the shift correlates with our history warming, not with cold-start
   schedule fallback. Candidate root causes, not yet adjudicable without ground truth:
   - **(a) measurement effect (favors us):** our link medians come from 5 s interpolation; prod's
     from ~30 s. Coarser interpolation smears stop-crossing times and can inflate link medians —
     ours may simply be tighter estimates of the same reality.
   - **(b) sample-depth effect (favors prod):** 6 days of our data vs years of theirs — our medians
     may under-represent congestion tails (esp. peak buckets with ~6 samples/weekday-hour), biasing
     fast; should shrink over coming weeks if so.
   - **(c) policy difference:** prod's blend/level (NEXT_TRIP etc.) may deliberately lean
     conservative for rider experience ("better early-predicted than missed-bus").
   Distinguishing (a) from (b): (b) predicts the bias shrinks as our buckets deepen; (a) predicts it
   persists. Track weekly. Definitive answer = the ground-truth scoring harness (PRODUCTIONIZING §5).
3. Route confusion remains negligible and of the known corridor-variant kind (single vehicle:
   BX18B ours vs BX18A prod, same `BX18` trip token — branch variants of one route).
4. Express MAE improved 30% this run — SI express AM inbound now has a week of link history.

**Process note:** comparison tooling is now documented for future agents in
[`COMPARISON-RUNBOOK.md`](COMPARISON-RUNBOOK.md) (incl. the new `probe-coverage.py`); both feeds
are archived in S3 for fixed-window (confounder-free) comparisons.
---

# Re-run 2026-07-27 17:10–17:14 EDT — PM PEAK, history day 6 (full output: `feed-comparison-2026-07-27-pm.md`)

Like-for-like against the 07-24 PM-peak run. 10,197 vehicle-pairs; ~187k matched stop-predictions.
**Full categorisation, root causes and fixes: [`discrepancy-investigation-2026-07-27-pm.md`](discrepancy-investigation-2026-07-27-pm.md).**

| metric | 07-22 mid | 07-23 AM | 07-24 PM pk | 07-27 AM pk | **07-27 PM pk** |
|---|---|---|---|---|---|
| same trip_id | 97.3% | 98.1% | 96.5% | 96.7% | **97.1%** |
| ETA MAE — ALL | 53 | 45 | 41 | 37 | **37** |
| ETA MAE — 0–5 min | 22 | 17 | 16 | 18 | 16 |
| ETA MAE — 15–30 min | 55 | 47 | 36 | 36 | 34 |
| ETA MAE — 30+ min | 81 | 73 | 70 | 59 | **59** |
| ETA median — ALL | +2 | +4 | −4 | −7 | **−4** |
| ETA median — 30+ min | +10 | +14 | −13 | −18 | **−10** |
| express MAE | 113 | 109 | 108 | 76 | 97 (but see finding 7) |
| NYCT overlap | 98–99% | 99% | 98–99% | 98% | 97.4% |
| MTABC coverage | 0 | 0 | 0 | 0 | 0 (792 veh, 91 routes) |

**Findings:**

1. **The early-bias question is resolved as mostly a measurement artifact — and it is not growing.**
   PM-peak vs PM-peak the overall median is flat (−4 s) and 30+ min narrowed (−13 → −10 s).
   Cross-tabbing delta by inter-feed *position distance* × horizon shows that for local buses whose
   position both feeds agree on within 50 m, the bias is ~zero at **every** horizon (+3/+2/+0/−3 s);
   it appears only as the anchors diverge (150–400 m: −16/−17/−22/−30 s), with a magnitude matching
   prod's ~22 s older VehiclePositions. Hypothesis (a) confirmed, but as a *position-anchor* effect,
   not a link-median interpolation effect; (c) is ruled out (per-route signs go both ways:
   SIM7 −338 s … M11 +94 s).
2. **The genuine residual is express-only.** 30+ min: local median −8 s vs **express −67 s**, and
   express stays at −70 s even in the position-agreeing stratum. Worst: SIM7, SIM1, QM63, SIM10 —
   PM-peak outbound highway corridors where prod has years of congestion history and we have ~6
   weekday samples/bucket. Hypothesis (b); should shrink as peak buckets deepen.
3. **NEW DEFECT — route B1 (NYCT) is ~0% covered in our feed, all day** (prod 10–15 vehicles; ours 0
   at every sampled window 06:00–17:10 except 2 stuck on one trip at 08:00). This is the runbook's
   "alarming" condition. It is route-scoped, not vehicle/ingestion-scoped: buses 7163/7165/7563
   rotate between B1 and B6/B36/B64 and are present in our feed 18/19 windows on the other routes,
   absent 25/27 windows on B1. B1 trips *are* in the bundle (we emitted a real B1 trip id), so the
   cause is the MTABC mechanism scoped to one route — missing/unmatched STIF-derived DSC/run data
   for B1. Needs a host-side bundle check (SSM) then a rebuild. Present on 07-24 too, hidden inside
   the MTABC route list.
4. **NEW comparison artifact — rollover joins.** 35 of 7,442 trip-matched pairs (0.5%) match our
   *current* trip against prod's *next* trip (prod emits current+next, we emit current only), giving
   near-constant whole-trip offsets: 30+ min mean −146 s / MAE 193 s vs −7 s / 56 s for clean pairs.
   This is what inflated `offset_from_start` to 18.6% of big-delta TUs (from 1.6% on 07-22). Fix:
   join only prod's current TripUpdate, and/or set `PredictionLevel=NEXT_TRIP`.
5. Trip-assignment classes all improved or held: same-route/diff-trip 2.6% (was 3.4% at PM peak;
   137/196 have our trip on another vehicle in prod — UTS gap), direction flips 0.3%, route
   confusion 1 pair in 7,659. Big-delta TUs down to 3.5% of matched TUs (from 7.2% on 07-22).
6. Host was healthy and **not shedding** (`InferenceShedFixes` = 0 across the window, backlog
   1.1–2.0k, feed staleness 3–7 s), unlike 07-24 PM — so none of the above is load-induced.

7. **Fixed-window cross-day check (new tooling) — trip matching and local ETAs genuinely improved,
   express regressed.** Replaying the S3 archives for the identical 17:25–17:35 window on 07-24 vs
   07-27 (1.4 M / 2.3 M stop-prediction deltas): same trip_id 96.6% → **97.2%**, local MAE 32 → 30 s,
   30+ min median −16 → −13 s (bias not growing, confirmed confounder-free), but **express MAE
   88 → 119 s** and bias −29 → −32 s. The live pairing had suggested express *improved* (108 → 97 s);
   that was a time-of-day artifact of sampling 17:47 vs 17:10. Use the fixed-window numbers for
   day-over-day trends. Residual confounder: Fri vs Mon — needs a Mon-vs-Mon window next week.

**Process note:** added [`compare-archives.py`](compare-archives.py) — replays the S3 archives for
the same wall-clock window across dates (snapshots paired by `header.timestamp`), removing the
time-of-day confounder. Validated against 07-24 17:30 (reproduces that day's live numbers); output
in `archive-window-2026-07-24-vs-27.md`. `probe-coverage.py` now prints a **per-route NYCT coverage
gate** (flags routes below 20% of prod's fleet) — the check that would have caught B1 on 07-24.

**Addendum 2026-07-28 (history day 7) — Mongo fill state + depth A/B**
(detail: `discrepancy-investigation-2026-07-27-pm.md` §9–10, chart `history-fill-2026-07-28.png`):

- 904,289 `AggregateLinkTimes` buckets / 8.04 M observations, mean depth 8.9. **Nothing is at the 300
  cap — deepest bucket anywhere is 157**; 59% of buckets hold ≤5 traversals, only 0.6% hold ≥50.
  Weekend history is ~5× shallower than weekday. Deepest hours are local 07:00–08:00 and 18:00.
- **`timeOfDay` is the UTC hour**, verified against raw `LinkTravelTimes` timestamps. Harmless within
  a season; re-check at the 2026-11-01 DST change.
- **Depth does buy agreement with prod**, controlled for link duration: per-link MAE **5.1–5.6 s on
  deep (n≥50) links vs 12.4–19.8 s on shallow (n≤5)** and 5.6–6.2 s overall; stop-level 21–27 s vs
  25–40 s overall. The gain is only ~8–10% against the overall average (most links already have some
  history) but 2.4–3.5× against genuinely shallow buckets, and it concentrates on links >180 s.
- **This explains the express residual (finding 2/7 above):** express is 6× over-represented among
  shallow links (17.3% vs 2.8% of deep) and has the longest links — the two conditions that cost the
  most. Supports hypothesis (b), sample depth, with direct evidence rather than inference.
- **Time-to-fill** (§11, measured per bucket against 4.89 elapsed weekday-equivalents): a bucket gains
  one entry per bus that traverses that directed stop-pair in that UTC hour on that day-type, so
  rate ≈ 60/headway_min and **weekend buckets accrue only 1 day/week vs 5**. To the 300 cap: 39% of
  weekday buckets within 13 weeks, 75% within 26; but 58% of Saturday and 69% of Sunday buckets need
  2+ years. **300 is a rolling `$slice` window, not a target** — the accuracy gain saturates near
  depth 50, and **80% of weekday buckets reach 50 within 4 weeks**, so re-measure the weekday A/B in
  late August. Weekend needs a coarser key, not patience. New tooling: `analyze-history-depth.py`,
  `make-history-fill-chart.py`.
---

# B1 root cause, 2026-07-28 (host investigation via SSM)

Supersedes the B1 root-cause hypothesis in the 07-27 PM section (finding 3), which was **wrong**: it
is not a missing or unmatched STIF slice. Full write-up in [`FINDINGS-SUMMARY.md`](FINDINGS-SUMMARY.md) §4B.

**Inputs all verify clean.** B1 STIF present (`stif.b_0001__.426581.wkd.*`, Ulmer Park); DSCs 4010
(118 recs) / 4011 (120) / 4012 (5) declared; 243 coded weekday records vs 243 GTFS weekday-SDon B1
trips; origin times overlap 234/234; STIF stop ids resolve in `stops.txt`; run numbers 37/37 present;
and the buses broadcast **4010/4011** under the correct agency `MTA NYCT/2008`, codes used by no other
vehicles.

**The defect is in the built bundle.** Deserialising `{TripsForDSCIndices,DSCForTripIndices}.obj`:
B1 has **124 of 928 trips (13.4%)** carrying a DSC, vs **100%** for B6 (1807), B9 (748), B38 (1220).
**DSC 4010 is absent from the bundle entirely**, 4012 too, and **4011 maps to only 7 trips**; 117 of
B1's 124 mapped trips carry other routes' interline codes. `StifTripLoader` silently dropped ~87% of
B1's trips at build time.

**Runtime consequence** (`BlocksFromObservationServiceImpl.java:353-373`): DSC trips are added only
`if (... hasValidDsc())`. A bus signed 4010 has an unknown DSC → no DSC trips, and default-on
`_requireDSCImpliedRoutes` then discards every spatial snap (empty implied-route set) — the same
end-state as the MTABC gap, reached differently. A bus signed 4011 can only match those 7 trips, which
is exactly why the only B1 output ever seen was two vehicles pinned to `046400_B1_2`.

**Scope — and the feed gate is a valid proxy.** Per-route DSC coverage over all 188 routes with ≥100
trips: **B1 13.4%, Q84 50.0%, Q30 52.0%**, M101 81.5%, everything else 96-100%. Only one route below
50%. Those three are precisely the three NYCT routes the new per-route coverage gate flagged
independently (0%, 43%, 43%).

**Still open:** which loader condition dropped them. The four STIF diagnostic CSVs name every dropped
trip and reason but were not retained, and log4j was never initialised for the build (`No appenders
could be found`), so the loader's warnings went nowhere. **One rebuild with logging on answers it.**
Then add a per-route DSC-coverage build gate.

**Also documented this session:** the full list of prod data sources our instance lacks, with
endpoints and consumption sites — `FINDINGS-SUMMARY.md` §5b. Our deployed stubs are
`DummyVehiclePulloutService`, `DummyUnassignedVehicleServiceImpl` (inference) and
`DummyConfigurationServiceImpl` (predictions).
---

# Re-run 2026-07-29 17:28–17:31 EDT — PM PEAK, first run with the full network (full output: `feed-comparison-2026-07-29-post-mtabc.md`)

First comparison after deploying both coverage fixes (dual-key STIF matcher + MTABC STIF). 13,207
vehicle-pairs; ~220k matched stop-predictions. ~3 h post-restart, so caches were warm.

| metric | 07-24 PM pk | 07-27 PM pk | **07-29 PM pk** |
|---|---|---|---|
| same trip_id | 96.5% | 97.1% | **98.0%** |
| same route, diff trip | 3.4% | 2.6% | 1.6% |
| different route | 2 | 1 | **0** |
| ETA MAE — ALL | 41 | 37 | 49 — but **36 for NYCT alone** |
| ETA MAE — 30+ min | 70 | 59 | 79 — **55 for NYCT alone** |
| express MAE | 108 | 97 | 131 (sample doubled: MTABC express included) |
| vehicles ours / prod | 2538 / 3406 | 2596 / 3414 | **3356 / 3398** |
| routes only in MTA | 93 | 92 | **0** |

**Findings:**

1. **Network parity reached.** Zero routes missing (was 92), 3,356 vehicles vs prod's 3,398 (99%),
   NYCT 98.4% / MTABC 97.0% coverage. Trip matching hit **98.0% at PM peak** — the best figure recorded
   in any run, and previously PM peak was the *worst* window (96.5%).
2. **The ETA "regression" is entirely the new MTABC fleet, which has zero history.** By agency:
   NYCT MAE 36 s overall / 55 s at 30+ min, versus MTABC 86 s / 149 s. NYCT actually **improved** on
   07-27 PM peak (37 → 36, 59 → 55). Report the two agencies separately until MTABC warms, or the
   blend will mask NYCT's progress.
3. **Express MAE 97 → 131 is a composition change, not a degradation** — the express sample nearly
   doubled (15k → 26k deltas) as MTABC's QM/BM/BXM joined with no link history.
4. Direction flips 0.4%, same-route/diff-trip down to 1.6% (from 2.6%), route confusion **0**.
5. Two ~40 s feed outages were the whole cost of the deploy (38 s and 44 s, both measured, gtfsrt-only).

**Caveats:** this is a step change, so rows before 07-29 are not comparable on coverage or blended ETA.
Two MTABC routes (`BXM18`, `QM32`) and 15 MTABC vehicles remain absent. The fleet is ~36% larger, so
the deadband/shedding thresholds still need validating across a full PM peak.

> **Superseded in part by the 07-30 run below.** That last caveat was the right one: the larger fleet
> did break the peak thresholds. Findings 2 and 3 above were derived from *live* runs at different
> windows; measured at a fixed clock window with the agency held fixed, NYCT did **not** improve on
> 07-29 — it regressed (MAE 38 → 46 s, near-term 13 → 28 s). See "Correcting the 07-29 reading" below.

---

# Re-run 2026-07-30 08:47–08:51 EDT — AM PEAK, day 1 of the full network (full output: `feed-comparison-2026-07-30.md`)

Coverage held everywhere and trip matching set a record — and the run surfaced a **new, unrelated
regression: our position anchors are now staler than prod's**, which is the first time that has been
true and which inverts the assumption the F2 bias control was built on.

| metric | 07-27 AM pk | 07-29 PM pk | **07-30 AM pk** |
|---|---|---|---|
| same trip_id | 96.7% | 98.0% | **98.4%** (best recorded) |
| same route, diff trip | 3.0% | 1.6% | **1.2%** |
| ETA MAE — ALL | 37 | 49 | 44 |
| ETA MAE — 0-5 min | 18 | 23 | 23 |
| ETA MAE — 30+ min | 59 | 79 | 72 |
| ETA median — 30+ min | −18 | −21 | **−28** |
| express MAE | 76 | 131 | 104 |
| vehicles ours / prod | 2596 / 3414 | 3356 / 3398 | 3290 / 3321 |
| routes only in MTA | 90 | **0** | **0** |
| **our position age (median)** | ~13–16 s | (not sampled) | **65–74 s** |
| prod position age (median) | ~37 s | ~37 s | 30–36 s |

Coverage probe: NYCT **97.7–98.3%**, MTABC **98.0%**, per-route gate **silent** across 234 routes
(worst: QM64 67% and S54 67%, both on prod fleets of 3–6, i.e. noise). B1 is gone from the gap list.
`B84` appeared in our feed but not prod's — prod simply had no B84 bus running in the window, the
benign direction of that check.

## 1. The headline: coverage and trip matching are genuinely good

98.4% same-`trip_id` is the best figure in the series, at a peak window, and same-route/different-trip
fell to 1.2% (from 2.6–3.4% before the fixes). Holding both the clock window and the agency fixed —
NYCT only, 08:45, ~2,560 vehicles both days — matching went **97.4% → 97.8%**, so this is a real gain
from the dual-key matcher and not a composition effect. Rollover joins also fell to **0.2%** of pairs
(17 of 9,428), from 0.5%.

## 2. The new problem: our position anchor aged 4.6× overnight

`probe-coverage.py` reported our position age at **72 s median / 89 s p90** against prod's **32 s** —
reversing the 2–3× freshness advantage that has held in every prior run. Four consecutive probes
confirmed it (65–74 s). `analyze-bias.py` §4 measures the same thing independently: **prod VP age −
our VP age = −39 s** (local), where it was **+22 to +25 s** on 07-27.

Replaying the archives at one fixed window across days (`analyze-anchor-age.py`, new) dates it exactly
to the 07-29 deploy, and **NYCT-only** — so it is not the new MTABC fleet arriving with a slower
cadence:

| window | 07-24 | 07-27 | 07-28 | 07-29 | 07-30 |
|---|---|---|---|---|---|
| 08:45, NYCT only (~2,600 veh) | – | 16 s | 15 s | 15 s | **69 s** |
| 17:25, both agencies | 15 s | 15 s | 15 s | **69 s** | – |
| feed *header* age, same windows | 5–6 s | 5 s | 4–5 s | 5 s | 6 s |

MTABC's own anchor age on 07-30 is **68 s** — identical to NYCT's, confirming it is platform-wide.

**The distinction that matters: the feed is published on time; the data inside it is old.** Header age
never moved (4–6 s), which is exactly what `FeedStalenessSeconds` alarms on — so the existing
monitoring is structurally blind to this failure, and a 4.6× freshness regression ran for a day
unnoticed.

## 3. Cause: the coverage win pushed the box past its peak capacity

Config drift is ruled out — verified on the host, the flags are still
`deadband minMeters=10 minIntervalSec=7 maxAgeSec=30` and `shed maxAgeSec=50`, and inference last
restarted at the deploy itself (07-29 18:16 UTC). What changed is load, from CloudWatch at AM peak
(08:00 hour):

| | 07-27 | 07-28 | 07-29 | **07-30** |
|---|---|---|---|---|
| `TripUpdateEntities` | 2,602 | 2,651 | 2,671 | **3,482** (+30%) |
| `CPUUtilization` avg | 54% | 51% | 57% | **74%** |
| `InferenceBacklogThreads` avg | 1,397 | 1,545 | 1,490 | **14,994** (max 15,244) |
| `InferenceShedFixes` per 2 min | **0** | **0** | **0** | **3,869** (max 4,612) |

`InferenceShedFixes` had been **exactly 0 for the entire recorded history** and now fires at every
peak (2,745/interval at 07-29 PM, 3,869 at 07-30 AM), going quiet overnight when load falls. So
load-shedding is working precisely as designed — it bounds the queue (~15k, well under the 25k alarm)
and prevents OOM — but the lag it settles at now exceeds prod's freshness. Dropping a vehicle's
queued fixes lengthens that vehicle's *effective* update interval, which is what the anchor age
measures.

## 4. What the staleness costs, with the fleet held fixed

NYCT only, identical clock windows, ~1–2 M stop-prediction deltas per row (so MTABC's cold history
cannot be the explanation — the fleet is the same one, at the same size, on both dates):

| window / date | ALL | 0-5 min | 5-15 min | 15-30 min | 30+ min | local | express | same trip |
|---|---|---|---|---|---|---|---|---|
| **08:45** 07-29 (pre) | −9 / 31 | −2 / **11** | −6 / 19 | −12 / 30 | −24 / 53 | −9 / 28 | −24 / 83 | 97.4% |
| **08:45** 07-30 (post) | −16 / 42 | −6 / **25** | −12 / 31 | −17 / 40 | −33 / 64 | −15 / 39 | −40 / 107 | 97.8% |
| **17:25** 07-24 (pre) | −6 / 37 | −2 / **13** | −4 / 21 | −8 / 35 | −16 / 60 | −5 / 32 | −29 / 88 | 96.6% |
| **17:25** 07-27 (pre) | −5 / 38 | −2 / **13** | −4 / 21 | −6 / 33 | −13 / 65 | −4 / 30 | −32 / 119 | 97.2% |
| **17:25** 07-29 (post) | −15 / 46 | −6 / **28** | −11 / 34 | −16 / 42 | −26 / 66 | −15 / 41 | −18 / 101 | 96.9% |

Two things stand out. **The near-term bucket roughly doubled at both windows** (11 → 25, 13 → 28) —
the one movement the runbook names as unambiguously alarming, and the signature you would predict from
a stale anchor, since a 0–5 min ETA is almost entirely determined by where the bus is now. And **the
30+ bucket barely moved** (65 → 66 at PM), so this is a freshness regression, not a link-time one.

## 5. Correcting the 07-29 reading

The 07-29 entry recorded "NYCT improved 37 → 36 s" and "record 98.0% trip match" from live runs. At a
fixed window with the agency held fixed, neither survives:

- **NYCT ETA regressed on 07-29**, 38 → 46 s overall and 13 → 28 s near-term. The live comparison
  looked flat only because it compared a 17:28 sample against a 17:10 one from a different day.
- **The 98.0% was the MTABC blend.** MTABC matches better than NYCT (07-30 AM: 98.3% both agencies vs
  97.8% NYCT only) because its longer headways leave fewer adjacent departures to confuse. NYCT-only
  PM matching went 97.2% → 96.9%.

The MTABC-cold-history explanation for the blended ETA rise was *also* true — both effects were
present on 07-29, and the live method could not separate them. This is measurement lesson #1 landing
a second time, plus a new one: **hold the agency fixed across 2026-07-29.**

## 6. What this does to the F2 bias control

F2 explained the long-horizon "early bias" as prod's anchor being ~22 s older than ours. That premise
is now false — ours is ~39 s older than prod's — yet the bias is still negative and *deeper*
(30+ median −28 s, the most negative recorded). So the mechanism as written cannot be the whole story.
The distance-stratified control (`analyze-bias.py` §5), local, median delta by inter-feed position
distance × horizon:

| local | 0-5 | 5-15 | 15-30 | 30+ |
|---|---|---|---|---|
| 0–50 m (07-27) | +3 | +2 | +0 | **−3** |
| 0–50 m (**07-30**) | +0 | −1 | −6 | **−22** |
| 50–150 m | −7 | −11 | −15 | −28 |
| 150–400 m | −15 | −18 | −23 | −39 |
| 400 m+ | −26 | −41 | −45 | −103 |

The near-term rows are still ~0 where the feeds agree on position, but the 30+ residual that the
control used to remove is now **−22 s**. Meanwhile **express in the position-agreeing stratum is now
+3/+4/+12/+0** — the −41…−70 s express residual that F3 called "the one real engine gap" is not
visible in this run. Route-level 30+ medians have inverted with it: worst are now Brooklyn/Queens
locals (B16 −150 s, Q25 −120, B9 −103, Q60 −99) and the most positive is **SIM1C +66 s**, where
express (SIM7/SIM1/QM63) used to hold the negative end.

**Treat all of §6 as unresolved rather than as a new finding.** Under a 69 s anchor the strata no
longer mean what they meant: "the two feeds agree within 50 m" now selects disproportionately for
slow and stationary buses, because that is where a 39 s anchor difference produces little distance.
The control is confounded by the very regression it is trying to control for, so the F2/F3 split
cannot be re-derived until freshness is restored.
