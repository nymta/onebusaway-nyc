# EC2 OBA vs MTA prod GTFS-RT — comparison runbook (for future agents)

Everything needed to re-run the recurring feed comparison, interpret the numbers against prior
baselines, and know which discrepancies are already explained. Prior runs + deep-dives live in
[`feed-comparison-report.md`](feed-comparison-report.md) (running log; dated full outputs in
`feed-comparison-YYYY-MM-DD.md`).

> **New here? Read [`FINDINGS-SUMMARY.md`](FINDINGS-SUMMARY.md) first** — one document consolidating
> every finding to date: all ten discrepancy classes with root causes and evidence, the history-fill
> state and timeline, the measurement controls, and the prioritised action list. This runbook is the
> *how to re-run it*; that one is the *what we know*.

## 1. What is being compared, and why

- **Ours:** `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates` (+ `/vehiclePositions`) —
  the experimental single-host OBA-NYC deployment (see `EC2-DEPLOYMENT.md`), same codebase and same
  BusTech AVL source as prod, but: 5 s GPS (vs prod's effective ~30 s), no UTS run assignments, no
  MTABC STIF in the bundle, Mongo history warming since **2026-07-21**, prediction weights 20/40/40
  (verified identical to prod's TDM config).
- **Prod:** `http://gtfsrt.prod.obanyc.com/tripUpdates` (+ `/vehiclePositions`) — MTA's official
  Bus Time GTFS-RT. **Public, no API key.** Emits ~1.8 TripUpdates/vehicle (current + next trip;
  prod runs `predictions.PredictionLevel=NEXT_TRIP`, we run default `CURRENT_TRIP`).
- Both use bare GTFS ids and identical STIF-style `trip_id`s (verified 100% vs the C6 public GTFS);
  vehicles are `MTA NYCT_xxxx` / `MTABC_xxxx`. Join on trip/stop/vehicle ids directly.
- Goal: A/B test (finer GPS + warmed history should eventually beat prod) — but first, demonstrate
  the baseline behaves like prod and track warm-up. Alignment to prod is a proxy, not the target.

## 2. How to run (all in `local-loop/`, needs `gtfs-realtime-bindings`)

```bash
# 1. Coverage/freshness probe (~10 s)
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
REPORT_OUT=archive-window-$(date +%F).md python3 compare-archives.py 2026-07-24,2026-07-27 17-25 2
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

**Fixed-window baseline** (17:25–17:35 ET, for the next cross-day run — prefer Mon-vs-Mon):

| date | same trip_id | ALL med/MAE | 30+ med/MAE | local MAE | express med/MAE |
|---|---|---|---|---|---|
| 2026-07-24 (Fri) | 96.6% | −6 / 37 | −16 / 60 | 32 | −29 / 88 |
| 2026-07-27 (Mon) | 97.2% | −5 / 38 | −13 / 65 | 30 | −32 / 119 |

## 3. Reading the results — baselines and what's already explained

Trend so far (live runs; **time-of-day confounds day-to-day deltas**):

| metric | 07-22 midday | 07-23 AM | 07-24 PM peak | 07-27 AM peak | 07-27 PM peak | expected trajectory |
|---|---|---|---|---|---|---|
| same trip_id | 97.3% | 98.1% | 96.5% | 96.7% | 97.1% | flat until UTS data lands (peak dips ~1-2 pts) |
| ETA MAE overall | 53 s | 45 s | 41 s | 37 s | 37 s | ↓ as Mongo warms |
| ETA MAE 30+ min | 81 s | 73 s | 70 s | 59 s | 59 s | ↓ most here |
| ETA median 30+ min | +10 s | +14 s | −13 s | −18 s | −10 s | **largely answered 07-27 PM**: flat PM-vs-PM, and the local component is a position-anchor artifact (prod's VP is ~22 s older). Residual is express-only — track SIM7/SIM1/QM63 |
| NYCT vehicle overlap | 98–99% | 99% | 98–99% | 98% | 97.4% | ≥97% incl. peak |
| express MAE | 113 s | 109 s | 108 s | 76 s | 97 s | **live numbers are time-of-day noise — use the fixed-window run.** At an identical 17:25-35 window, express MAE went 88 s (07-24) → 119 s (07-27): regressing, the one class not benefiting from warm-up |

Known discrepancy classes — **all investigated, root causes confirmed** (full detail + code
citations in the deep-dive sections of `feed-comparison-report.md`):

1. **MTABC fleet entirely absent from our feed** (~450-850 vehicles by time of day, ~75-93 routes:
   Q06-Q115, B100/B103, QM/BM/BXM, SIM8/10). Cause: no MTABC STIF in the bundle → no DSC sign-code
   map/run data → inference generates zero candidate blocks. Fix: obtain MTABC STIF, rebuild bundle.
   NOT a regression signal unless NYCT routes start appearing in the missing list.
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
5. **TripUpdates/vehicle 1.00 vs prod ~1.8:** `PredictionLevel` CURRENT_TRIP vs NEXT_TRIP — config
   gap, one seeded key to fix (see EC2-DEPLOYMENT.md §11). Until changed, expect exactly this.
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
9. **Prod's position anchor is ~22 s older than ours** (VP age: prod median ~37 s, ours ~13 s), which
   reads as "we predict earlier". **Stratify any bias claim by inter-feed position distance before
   attributing it to the engine** — run `analyze-bias.py` (§5 of its output is the control). Where both
   feeds place the bus within 50 m, the local bias falls to ~0 at short horizons and −3 to −13 s at
   30+ min, vs −30 to −45 s once the anchors diverge 150-400 m. The express residual survives the
   control (−41 to −70 s at 30+ min) and is the only part worth chasing.

**What would actually be alarming**: NYCT coverage < 95%; trip agreement < 94% off-peak; a systematic
one-sided ETA bias (|median| > 30 s overall) **that survives the position-distance control (§9)**;
near-term bucket (0-5 min) MAE growing; routes in our feed absent from GTFS; our feed age > 30 s
sustained. **A NYCT route in the missing list is the condition B1 tripped — check per-route coverage,
not just the aggregate**, since a single 0% route is invisible inside a 97% NYCT total.

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
- **Load-shedding at peak** on our host (`InferenceShedFixes` metric; EC2-DEPLOYMENT §4.10).
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
| history-depth A/B (does a warm bucket help?) | `local-loop/analyze-history-depth.py` |
| history fill chart + its data block | `local-loop/make-history-fill-chart.py` → `history-fill-*.png` |
| fixed clock-window (archive) comparison | `local-loop/compare-archives.py` → `archive-window-*.md` |
| static-GTFS id parity gate | `local-loop/verify-parity.py` (re-run per pick) |
| EC2 host ops/monitoring | `local-loop/EC2-DEPLOYMENT.md` (+ CloudWatch dashboard `oba-nyc-prod`) |
| S3 archives (both feeds) | `s3://mtalirr/data-archiver/{busGtfsRt,obaEc2TripUpdates,obaEc2VehiclePositions}/` |
| archiver validation context | `data-archiver` repo, `STREAMING-VALIDATION.md` |
| prod OBA config reference | prod TDM dump analysis in `EC2-DEPLOYMENT.md` §11 (weights 20/40/40 verified; NEXT_TRIP; CAPI/ghostbus/APC/YardTrek endpoints) |
