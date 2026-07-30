# EC2 OBA vs MTA prod GTFS-RT — comparison runbook (for future agents)

Everything needed to re-run the recurring feed comparison, interpret the numbers against prior
baselines, and know which discrepancies are already explained. Prior runs + deep-dives live in
[`feed-comparison-report.md`](feed-comparison-report.md) (running log; dated full outputs in
`feed-comparison-YYYY-MM-DD.md`).

> **New here? Read [`FINDINGS-SUMMARY.md`](FINDINGS-SUMMARY.md) first** — one document consolidating
> every finding to date: every discrepancy class with root causes and evidence, the history-fill
> state and timeline, the measurement controls, and the prioritised action list. This runbook is the
> *how to re-run it*; that one is the *what we know*.
>
> **State as of 2026-07-30:** coverage is at parity with prod and trip matching is the best recorded,
> but the 07-29 coverage fix pushed the host past its peak capacity and our position anchors are now
> **69 s old vs prod's 32 s**, which has roughly doubled near-term ETA error. That is class 10 below and
> the top action; ETA numbers do not compare across 2026-07-29.

## 1. What is being compared, and why

- **Ours:** `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates` (+ `/vehiclePositions`) —
  the experimental single-host OBA-NYC deployment (see `EC2-DEPLOYMENT.md`), same codebase and same
  BusTech AVL source as prod (**the same 5.6 s/bus stream — we do not have finer GPS, we process more of
  it; effective sampling ~11 s for a moving bus, ~21 s fleet-mean, published anchor age ~16 s vs prod's
  ~39–48 s — see `FINDINGS-SUMMARY.md` §1a**), no UTS run assignments, Mongo history warming
  since **2026-07-21** (MTABC only since 07-29), prediction weights 20/40/40 (verified identical to
  prod's TDM config). Both agencies have been in the bundle since 2026-07-29.
- **Prod ("BusTech prod"):** `http://gtfsrt.prod.obanyc.com/tripUpdates` (+ `/vehiclePositions`) — the
  incumbent production Bus Time GTFS-RT. **Public, no API key.** (**Naming:** both deployments are
  MTA's — "prod" is the vendor-operated production instance running the same OBA codebase. "MTA"
  elsewhere in these docs means the agency/data owners, not the production system.) Emits ~1.8 TripUpdates/vehicle (current + next trip;
  prod runs `predictions.PredictionLevel=NEXT_TRIP`, we run default `CURRENT_TRIP`).
- Both use bare GTFS ids and identical STIF-style `trip_id`s (verified 100% vs the C6 public GTFS);
  vehicles are `MTA NYCT_xxxx` / `MTABC_xxxx`. Join on trip/stop/vehicle ids directly.
- Goal: A/B test (processing more of the shared GPS stream + warmed history should eventually beat prod) — but first, demonstrate
  the baseline behaves like prod and track warm-up. Alignment to prod is a proxy, not the target.

## 2. How to run (all in `local-loop/`, needs `gtfs-realtime-bindings`)

```bash
# 1. Coverage/freshness probe (~10 s). CHECK THE POSITION AGES, not just coverage: ours should be
#    FRESHER than prod's. Since 2026-07-29 it is not (ours ~69 s vs prod ~32 s) — see class 10.
python3 probe-coverage.py

# 2. Main comparison: 4 snapshot rounds, 60 s apart (~4 min). ALWAYS set REPORT_OUT —
#    without it the script overwrites feed-comparison-report.md (the running log).
REPORT_OUT=feed-comparison-$(date +%F).md python3 compare-feeds.py 4 60

# 3. (When trip-level anomalies appear) discrepancy drill-down: classifies diff-trip pairs by
#    start-time gap / vehicle reassignment, and big ETA deltas by shape along the trip
python3 analyze-discrepancies.py 3 45          # writes discrepancy-detail.txt

# 3b. (Whenever a one-sided ETA bias shows up) decompose it before believing it: strips the
#     rollover-join artifact and stratifies by inter-feed position distance x horizon
python3 analyze-bias.py 3 45

# 4. Cross-day, same clock window (see below) — the reliable way to read day-over-day trends
# 5. Append a dated summary section (trend table vs prior baselines) to feed-comparison-report.md,
#    and a full categorised write-up as discrepancy-investigation-YYYY-MM-DD.md
```

Snapshot-timing artifacts are already handled: the join tolerates MTA's current+next TripUpdates,
and persistence was verified (disagreements persist ≥90 s → assignment differences, not timing).

**Archive-based, fixed clock window (preferred for cross-day trends):**

```bash
# same wall-clock window on two dates; args: dates, start window (5-min bucket), n windows
# AGENCY=NYCT is MANDATORY for any window spanning 2026-07-29 (MTABC joined that day, and it both
# lacks history and matches trips BETTER than NYCT — a blend moves for two unrelated reasons).
AGENCY=NYCT REPORT_OUT=archive-window-$(date +%F).md python3 compare-archives.py 2026-07-27,2026-07-29 17-25 2

# Anchor age: how old is the AVL fix behind each prediction? (NOT the same as feed age — see class 10)
AGENCY=NYCT python3 analyze-anchor-age.py 2026-07-27,2026-07-30 08-45 1
```

`compare-archives.py` replays the S3 archives below, dedupes prod's repeated polls by
`header.timestamp`, pairs our snapshot with prod's nearest within 10 s, and reports the same trip
matching / ETA-delta tables. This removes the time-of-day confounder that limits the live runs — use
it whenever comparing days. (Note: our archive starts 2026-07-24 ~17:25 ET; 07-25/26 are weekend.)

**Archive layout:** both feeds are archived to S3 —
prod at `s3://mtalirr/data-archiver/busGtfsRt/YYYY-MM-DD/HH-mm.b64.gz` (5 s polls, ~78% duplicate
snapshots — dedupe by GTFS-RT `header.timestamp`) and ours at
`s3://mtalirr/data-archiver/obaEc2TripUpdates/…` + `obaEc2VehiclePositions/…` (10 s, since
2026-07-24 ~17:25 ET). Lines are `{"ts":<ms>,"b":"<base64 protobuf>"}` in gzip (possibly
multi-member — Python `gzip` handles it). (`compare-archives.py` is the first cut of the archive
scoring utility on the Linear ticket; ground-truth scoring per `PRODUCTIONIZING.md` §5 is the step
after.)

**Fixed-window baseline — all rows NYCT-only** (the only clean ETA series; prefer Mon-vs-Mon):

| window / date | same trip_id | ALL med/MAE | **0-5 med/MAE** | 30+ med/MAE | local MAE | express med/MAE |
|---|---|---|---|---|---|---|
| 17:25 · 2026-07-24 (Fri) | 96.6% | −6 / 37 | −2 / **13** | −16 / 60 | 32 | −29 / 88 |
| 17:25 · 2026-07-27 (Mon) | 97.2% | −5 / 38 | −2 / **13** | −13 / 65 | 30 | −32 / 119 |
| 17:25 · 2026-07-29 (Wed) | 96.9% | −15 / 46 | −6 / **28** | −26 / 66 | 41 | −18 / 101 |
| 08:45 · 2026-07-29 (Wed) | 97.4% | −9 / 31 | −2 / **11** | −24 / 53 | 28 | −24 / 83 |
| 08:45 · 2026-07-30 (Thu) | 97.8% | −16 / 42 | −6 / **25** | −33 / 64 | 39 | −40 / 107 |

**The 07-29 rows are the first in the stale-anchor regime (class 10) — do not compare ETA numbers
across that boundary.** The 0-5 min column is broken out because it is the cleanest indicator of the
regression: it roughly doubled at both windows while 30+ barely moved.

## 3. Reading the results — baselines and what's already explained

Trend so far (live runs; **time-of-day confounds day-to-day deltas — read ETA trends off the
fixed-window table above, not this one**):

| metric | 07-22 midday | 07-23 AM | 07-24 PM peak | 07-27 AM peak | 07-27 PM peak | 07-29 PM peak | **07-30 AM peak** | expected trajectory |
|---|---|---|---|---|---|---|---|---|
| same trip_id | 97.3% | 98.1% | 96.5% | 96.7% | 97.1% | 98.0% | **98.4%** | best recorded; NYCT-only at a fixed window went 97.4 → 97.8%, so the gain is real. Flat-to-slow from here until UTS data lands |
| ETA MAE overall | 53 s | 45 s | 41 s | 37 s | 37 s | 49 s | 44 s | **not readable from this row** — mixes time of day, MTABC joining, and class 10. NYCT at a fixed window: 38 → 46 s (worse) |
| ETA MAE 30+ min | 81 s | 73 s | 70 s | 59 s | 59 s | 79 s | 72 s | ↓ as Mongo warms; barely moved across the class-10 boundary (65 → 66 s fixed-window), which is how we know the regression is near-term |
| ETA median 30+ min | +10 s | +14 s | −13 s | −18 s | −10 s | −21 s | **−28 s** | most negative recorded. **The old explanation (prod's anchor ~22 s older) is now inverted** — ours is ~39 s older — so this is unexplained pending the class-10 fix |
| NYCT vehicle overlap | 98–99% | 99% | 98–99% | 98% | 97.4% | 98.4% | **97.7–98.3%** (MTABC 98.0%) | ≥97% incl. peak |
| express MAE | 113 s | 109 s | 108 s | 76 s | 97 s | 131 s | 104 s | **live numbers are time-of-day noise — use the fixed-window run**, where NYCT express went 88 → 119 → 101 s. The 07-29 point is inside the stale-anchor regime, so the trend is not readable yet |
| **our position anchor age** | 13–19 s | 13–19 s | 15 s | 15 s | 15 s | **69 s** | **69 s** | should be ~15 s and 2–3× fresher than prod. **Currently 2× staler — class 10, the top open item** |

Known discrepancy classes — **all investigated, root causes confirmed** (full detail + code
citations in the deep-dive sections of `feed-comparison-report.md`):

1. ~~**MTABC fleet entirely absent from our feed**~~ — **FIXED 2026-07-29.** Was ~450-850 vehicles
   and ~75-93 routes (Q06-Q115, B100/B103, QM/BM/BXM, SIM8/10); cause was no MTABC STIF in the bundle
   → no DSC sign-code map/run data → zero candidate blocks. The STIF was added and the bundle rebuilt;
   routes-missing is now **0**. Two MTABC routes (`BXM18`, `QM32`) remain absent — that residue, or any
   NYCT route appearing in the missing list, is the regression signal now.
   **MTABC has no prediction history before 2026-07-29**, so split ETA metrics by agency for the next
   few weeks — a blended MAE will read as a regression while NYCT is in fact improving.
2. **Same-route/different-trip (~2-3%, worse at peak):** we lack UTS operator→run assignments
   (prod has them), so Run/Block likelihoods are flat and adjacent departures (median 15 min apart)
   are separated only by a ~9-min-scale schedule prior. Often MTA shows our trip on a different
   vehicle. Fix path: TDM crew API / YardTrek S3 access.
3. **Direction flips (~0.2-0.3%):** same run, adjacent opposite-direction trips at layovers —
   stationary buses bypass the 135° orientation gate and DSC is direction-blind. Self-correcting on
   movement; same UTS fix.
4. **ETA deltas >3 min (~7% of matched TripUpdates):** 82% grow-downstream drift at 15+ min horizon
   on long corridors (M101/M7/M3, SI express) — compounding per-link differences: prod has years of
   historical link medians, ours falls back toward schedule on cold links. Watch the 15-30/30+ MAE
   buckets shrink with warm-up. Sign flips by time of day (we were later than MTA midday, earlier at
   PM peak) — unbiased overall so far.
5. **TripUpdates/vehicle 1.00 vs prod ~1.8:** `PredictionLevel` CURRENT_TRIP vs NEXT_TRIP. **Not a
   config key — corrected 2026-07-30:** our `DummyConfigurationServiceImpl` returns the caller's default
   unconditionally, so no property or API can change it; it needs a code change + deploy
   (`FINDINGS-SUMMARY.md` §5 H). Until then, expect exactly this.
6. **A few vehicles only in OUR feed:** prod filters ghost buses via
   `bustrek.mta.info/api/ghostbus/records`; we don't.
7. **Route B1 (NYCT) ~0% covered — OPEN DEFECT, root-caused 2026-07-28.** Prod runs 10-15 B1 vehicles
   all day; we emit ~0. **It is a bundle-build defect, not a missing STIF.** The B1 STIF is present and
   every input verifies clean (DSCs 4010/4011/4012 declared; 243 coded weekday records = 243 GTFS
   weekday-SDon B1 trips; origin times 234/234; stop ids valid; run numbers 37/37; buses broadcast
   4010/4011 under the correct agency). But the built bundle's DSC index holds only **124 of 928 B1
   trips (13.4%)**, with **DSC 4010 absent entirely** and 4011 mapped to just **7** trips — so
   `hasValidDsc` fails and `_requireDSCImpliedRoutes` discards every snap (same end-state as class 1).
   Also affects **Q84 (50%)** and **Q30 (52%)**; every other route is 96-100%. **We build the bundle
   ourselves** (`FederatedTransitDataBundleCreatorMain` + `ec2/bundle/bundle-wholeMTA.xml`, manual, no
   script), so this is ours to fix. **Fix: give `multiCSVLogger` a `basePath` (it currently defaults to
   `java.io.tmpdir`, which is why the 4 STIF diagnostic CSVs were lost) + a log4j config, rebuild, and
   read `stif_trips_with_no_gtfs_match.csv` / `trips_with_duplicate_run_and_start_time.csv` /
   `trips_with_null_dscs.csv` / `stif_trip_layers_with_missing_route.csv`** — they name the drop reason
   per trip. Then add a per-route DSC-coverage build gate. Full evidence: `FINDINGS-SUMMARY.md` §4B-4C.
8. **Rollover join artifact (measurement, not feed).** Prod emits current+next TripUpdates; we emit
   current only. When we've rolled onto the next trip and prod hasn't, the trip_id join pairs our
   in-progress trip with prod's not-yet-started trip → near-constant whole-trip offsets (0.5% of
   pairs, but 30+ min mean −146 s / MAE 193 s vs −7 s / 56 s clean). **Discount `offset_from_start`
   cases until the join is restricted to prod's current TripUpdate** (or `PredictionLevel=NEXT_TRIP`
   is set).
9. **Position-anchor asymmetry — read class 10 first, the sign has flipped.** Through 07-28 prod's
   anchor was ~22 s *older* than ours (prod ~37 s, ours ~13 s), which reads as "we predict earlier",
   and stratifying by inter-feed position distance removed most of the local bias (0–50 m: ~0 at short
   horizons, −3…−13 s at 30+; −30…−45 s once anchors diverge 150–400 m), leaving an express-only
   residual of −41…−70 s. **Since 07-29 ours is ~39 s older than prod's and the bias got deeper rather
   than flipping, so that mechanism no longer explains it.** Still always run `analyze-bias.py` before
   attributing bias to the engine — but note its §5 control is currently confounded (a 69 s anchor makes
   "both feeds within 50 m" select for slow/stationary buses), so **do not quote a bias attribution
   until class 10 is fixed**.
10. **Our position anchor is 4.6× staler than it was, and staler than prod's — OPEN, TOP PRIORITY,
    found 2026-07-30.** Anchor age (`vehicle.timestamp` age) went **15 s → 69 s** at fixed windows,
    dated exactly to the 07-29 coverage deploy and present on NYCT alone, while prod sits at 30–36 s.
    Cause: the +30% fleet from the MTABC/dual-key fix pushed peak demand (**286 fixes/s** admitted after
    the deadband, measured by replaying the raw AVL archive) past sustainable throughput (**~254–270/s**,
    derived from demand minus shed rate), so a queue forms and `oba.shed.maxAgeSec=50` *pins* anchor age
    at ~50 s of queue wait + ~15 s baseline. At peak the thread pool is **642 of 642 busy** with per-fix latency
    **2,400 ms vs 75 ms off-peak**; only ~3% of fixes are actually shed, so the shed *volume* is not the
    cause — the queue wait is. `InferenceShedFixes` (= fixes dropped without running inference, delta per
    ~2 min) was **0 for all recorded history** and now runs ~3.9k/interval. **Cost: near-term ETA MAE roughly
    doubled** (0-5 min: 11 → 25 s AM, 13 → 28 s PM, fleet held fixed) while 30+ barely moved.
    **Crucially, feed *header* age never moved (4–6 s), so `FeedStalenessSeconds` and its alarm cannot
    see this** — use `analyze-anchor-age.py` or the probe's position-age line. Immediate fix is deadband
    `minIntervalSec` 7 → **12** (7 → 10 was measured to cut demand only 2.4% — the rate cap quantizes
    against the 5.6 s inbound cadence); it costs cadence, so the durable fix is thread-pool/accounting
    repairs or a resize. Use `simulate-deadband.py` to test any setting offline before applying it. See
    `FINDINGS-SUMMARY.md` §2c.

**What would actually be alarming**: NYCT coverage < 95%; trip agreement < 94% off-peak; a systematic
one-sided ETA bias (|median| > 30 s overall) **that survives the position-distance control (§9)**;
near-term bucket (0-5 min) MAE growing; **our position anchor age above ~30 s, or above prod's**;
routes in our feed absent from GTFS; our feed age > 30 s sustained. **A NYCT route in the missing list
is the condition B1 tripped — check per-route coverage, not just the aggregate**, since a single 0%
route is invisible inside a 97% NYCT total.

> **Two of these have now actually tripped** (07-30): near-term MAE roughly doubled, and anchor age
> hit 69 s. Both are class 10, and both were invisible to the CloudWatch alarms — the lesson being that
> "the feed is fresh" (`header.timestamp`) and "the data in the feed is fresh" (`vehicle.timestamp`)
> are different claims, and only the second one matters for accuracy.

## 3b. Mongo history depth (the warm-up driver) — how to query it

Admin is SSM-only; Mongo (`onebusaway-nyc-predictions`, collections `AggregateLinkTimes` +
`LinkTravelTimes`) listens on 127.0.0.1, so queries go through
`aws ssm send-command --document-name AWS-RunShellScript` running
`docker exec oba-mongo mongo --quiet onebusaway-nyc-predictions`. SSM caps stdout at ~24 KB — for
anything bigger, pipe through `gzip -9 | base64 -w0` on the host and decode locally, or filter to the
rows you need.

- One doc per `{routeId, headId, tailId, timeOfDay, scheduleType}`; `traversalTimes` is capped at
  `predictions.historicalComponentRecordCount` = **300**; `travelTime` is the precomputed median.
- **`timeOfDay` is the UTC hour** (verified 2026-07-28 against raw `LinkTravelTimes` timestamps).
  Local hour = `(timeOfDay − 4) mod 24` in EDT. Harmless within a season; shifts meaning at DST.
- `scheduleType`: 1 = weekday, 3 = Saturday, 4 = Sunday.
- **State at history day 7 (2026-07-28):** 904,289 buckets / 8.04 M observations, mean depth 8.9,
  **deepest bucket 157 — nothing is at the 300 cap**; 59% of buckets hold ≤5 traversals, 0.6% hold
  ≥50. Weekend ~5× shallower than weekday. See the chart + tables in
  `discrepancy-investigation-2026-07-27-pm.md` §9.
- **Depth does improve agreement with prod** (§10 there): per-link MAE 5.1–5.6 s on deep (n≥50) links
  vs 12.4–19.8 s on shallow (n≤5), surviving a link-duration control; the gain concentrates on links
  longer than 180 s, which is why express is the worst class. Re-run `analyze-history-depth.py`
  monthly to track.
- **Fill rate** (§11 there): one entry per bus traversing that directed stop-pair in that UTC hour on
  a matching day-type, so rate ≈ 60/headway_min, and **weekday buckets get 5 qualifying days/week
  while Saturday/Sunday get 1**. `weeks to N ≈ (N − depth) / (rate × days_per_week)`. **Don't wait for
  300** — it is a rolling `$slice` window and the gain saturates near depth 50, which 80% of weekday
  buckets reach within ~4 weeks (vs 13–26+ weeks for weekend). MTABC/B1 buckets never fill at all.

## 4. Confounders checklist (before attributing any change)

- **Time of day** (peak vs off-peak changes matching + delta profiles — compare like windows).
- **Agency composition.** MTABC entered our feed 2026-07-29 with zero history *and* a higher trip-match
  rate than NYCT, so any blended metric spanning that date moves for two unrelated reasons. Use
  `AGENCY=NYCT`. This is what made 07-29 read as "NYCT improved" when NYCT had regressed.
- **Anchor-age regime.** Everything before 07-29 was measured at ~15 s anchors, everything after at
  ~69 s (class 10). **ETA numbers are not comparable across that boundary**; coverage and trip matching
  are.
- **Load-shedding at peak** on our host (`InferenceShedFixes` metric; EC2-DEPLOYMENT §4.10) — **0
  through 07-28, nonzero at every peak since.** This is now the default state, not an exception.
- **Bundle pick changes** (~quarterly; re-run `verify-parity.py` after each rebuild).
- **Our config changes** (weights via `oba-weights`, PredictionLevel, deadband) — check
  `EC2-DEPLOYMENT.md` §11 + memory notes for current values.
- Prod-side incidents (their feed age spikes are visible in `header.timestamp`).

## 5. Where things live

| artifact | path |
|---|---|
| **consolidated findings (start here)** | `local-loop/FINDINGS-SUMMARY.md` |
| running findings log + deep-dives | `local-loop/feed-comparison-report.md` |
| dated raw outputs | `local-loop/feed-comparison-YYYY-MM-DD.md` |
| dated categorised investigations | `local-loop/discrepancy-investigation-YYYY-MM-DD.md` (latest: `-2026-07-27-pm.md`) |
| comparison tool / probe / drill-down | `local-loop/{compare-feeds,probe-coverage,analyze-discrepancies}.py` |
| ETA-bias decomposition (rollover + position control) | `local-loop/analyze-bias.py` |
| **position-anchor age across dates** (class 10) | `local-loop/analyze-anchor-age.py` |
| **explain the vehicle-count delta** (ours-only vs prod-only, with causes) | `local-loop/analyze-coverage-delta.py` |
| **offline deadband what-if** (replays archived raw AVL; test a setting before applying it) | `local-loop/simulate-deadband.py` |
| history-depth A/B (does a warm bucket help?) | `local-loop/analyze-history-depth.py` |
| history fill chart + its data block | `local-loop/make-history-fill-chart.py` → `history-fill-*.png` |
| fixed clock-window (archive) comparison | `local-loop/compare-archives.py` → `archive-window-*.md` |
| static-GTFS id parity gate | `local-loop/verify-parity.py` (re-run per pick) |
| EC2 host ops/monitoring | `local-loop/EC2-DEPLOYMENT.md` (+ CloudWatch dashboard `oba-nyc-prod`) |
| S3 archives (both feeds) | `s3://mtalirr/data-archiver/{busGtfsRt,obaEc2TripUpdates,obaEc2VehiclePositions}/` |
| archiver validation context | `data-archiver` repo, `STREAMING-VALIDATION.md` |
| prod OBA config reference | prod TDM dump analysis in `EC2-DEPLOYMENT.md` §11 (weights 20/40/40 verified; NEXT_TRIP; CAPI/ghostbus/APC/YardTrek endpoints) |
