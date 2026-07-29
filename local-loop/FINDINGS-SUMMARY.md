# EC2 OBA vs MTA production GTFS-RT — consolidated findings

**Single entry point** for everything learned from the feed-comparison work, 2026-07-22 → 2026-07-28
(6 live runs + 1 confounder-free archive run + a Mongo history study). Every discrepancy class, its
root cause, the evidence, and the fix.

This document *summarises*; it does not replace the sources:

| source | what it holds |
|---|---|
| [`COMPARISON-RUNBOOK.md`](COMPARISON-RUNBOOK.md) | how to re-run everything; baselines; confounder checklist |
| [`feed-comparison-report.md`](feed-comparison-report.md) | running log, one dated section per run + the 07-22 code deep-dive |
| [`discrepancy-investigation-2026-07-27-pm.md`](discrepancy-investigation-2026-07-27-pm.md) | the deepest single write-up (§1–11): B1, bias decomposition, history depth |
| `feed-comparison-YYYY-MM-DD*.md`, `archive-window-*.md`, `discrepancy-detail.txt` | raw tool output |
| [`EC2-DEPLOYMENT.md`](EC2-DEPLOYMENT.md), [`PRODUCTIONIZING.md`](PRODUCTIONIZING.md) | host/ops and the prediction-engine model |

---

## 1. What is being compared

- **Ours:** `http://ec2-52-70-255-34.compute-1.amazonaws.com/{tripUpdates,vehiclePositions}` — a
  single-host experimental OBA-NYC deployment. **Same codebase and same BusTech AVL feed as prod**,
  differing in four ways that matter: **5 s GPS** (vs prod's effective ~30 s), **no UTS operator→run
  assignments**, **no MTABC STIF in the bundle**, and **Mongo prediction history only since
  2026-07-21**. Prediction weights 20/40/40 — *verified identical to prod's TDM config*.
- **Prod:** `http://gtfsrt.prod.obanyc.com/...` — MTA's official Bus Time GTFS-RT. Public, no API key.
- Both emit bare GTFS ids and identical STIF-style `trip_id`s (**100% parity vs the C6 public GTFS**),
  so the feeds join directly on vehicle/trip/stop.
- **Goal:** an A/B test — finer GPS plus warmed history should eventually *beat* prod. Alignment with
  prod is therefore a **proxy for sanity, not the target**. Nothing here says which engine is more
  accurate; that needs the ground-truth harness (PRODUCTIONIZING §5).

---

## 2. Headline: the deployment behaves like prod, and is converging

| metric | 07-22 mid | 07-23 AM | 07-24 PM pk | 07-27 AM pk | 07-27 PM pk |
|---|---|---|---|---|---|
| same `trip_id` | 97.3% | 98.1% | 96.5% | 96.7% | **97.1%** |
| ETA MAE — ALL | 53 s | 45 s | 41 s | 37 s | **37 s** |
| — 0–5 min | 22 | 17 | 16 | 18 | 16 |
| — 15–30 min | 55 | 47 | 36 | 36 | 34 |
| — 30+ min | 81 | 73 | 70 | 59 | **59** |
| ETA median — ALL | +2 | +4 | −4 | −7 | −4 |
| ETA median — 30+ min | +10 | +14 | −13 | −18 | −10 |
| express MAE | 113 | 109 | 108 | 76 | 97 |
| NYCT vehicle coverage | 98–99% | 99% | 98–99% | 98% | 97.4% |

**Confounder-free cross-day check** (identical 17:25–17:35 clock window, replayed from the S3
archives — 1.4 M / 2.3 M stop-prediction deltas):

| | 07-24 (Fri) | 07-27 (Mon) |
|---|---|---|
| same `trip_id` | 96.6% | **97.2%** |
| ALL median / MAE | −6 / 37 | −5 / 38 |
| 30+ min | −16 / 60 | −13 / 65 |
| local MAE | 32 | **30** |
| express median / MAE | −29 / 88 | **−32 / 119** |

**Read:** trip matching and local ETAs genuinely improved; the long-horizon bias is flat to slightly
shrinking; **express regressed.** Positions are 2–3× fresher than prod's throughout (ours median
9–19 s vs prod 28–47 s). MAE 53 → 37 s over six days is real, but note the live runs' day-to-day
deltas are time-of-day contaminated — only the fixed-window table above is clean.

---

## 3. Discrepancy classes at a glance

| # | class | size (latest) | nature | root cause | fix |
|---|---|---|---|---|---|
| A | MTABC fleet absent | 792 veh, 91 routes | **coverage defect** | no MTABC STIF → no DSC/run data → zero candidate blocks | obtain MTABC STIF, rebuild bundle |
| B | **route B1 (NYCT) absent** (+ Q84/Q30/Q75 degraded) | 10–15 veh, all day | **coverage defect** | bundle build dropped 87% of B1 trips from the DSC index — GTFS/STIF disagree on non-revenue first/last stops | **fixed & verified locally** (dual-key index; 0 unmatched across all 5 boroughs + whole-MTA); needs a rebuild to deploy |
| C | same route, different trip | 2.6% of pairs | expected w/o data | no UTS run assignments → 3 of 4 likelihoods flat | TDM crew API / YardTrek |
| D | direction flips | 0.3% | transient | 135° gate skipped while stationary + DSC direction-blind | same UTS fix; self-corrects |
| E | route confusion | 0.01% | noise | shared-corridor DSC is a preference, not a rejection | none needed |
| F1 | rollover joins | 0.5% of pairs | **measurement artifact** | prod emits current+next TU, we emit current only | join prod's current TU; set `NEXT_TRIP` |
| F2 | long-horizon "early bias" (local) | median −3…−13 s | **measurement artifact** | prod's position anchor is ~22 s older than ours | none — control for it when measuring |
| F3 | express long-horizon bias | 30+ min median −67 s | **real engine gap** | thin history on long links | history warm-up; see §6 |
| G | vehicles only in our feed | 35–50 | benign | prod filters ghost buses; we don't | optional: consume ghostbus feed |
| H | 1.00 vs 1.82 TripUpdates/vehicle | — | config gap | `PredictionLevel=CURRENT_TRIP` vs prod's `NEXT_TRIP` | one seeded key |

Three of the ten are artifacts of *how we measure*, not of the feed. That distinction is the single
most important lesson of this work (§7).

---

## 4. Coverage defects (the only things actually broken)

### A. The entire MTABC fleet is missing

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

### B. Route B1 (NYCT) — found 2026-07-27, **root-caused on the host 2026-07-28**

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

### C. Same route, different trip — 2.6% (was 3.4% at the prior PM peak)

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

**F2 — position-anchor staleness (measurement artifact, and the bulk of the local bias).** Prod's
VehiclePositions are **~22–25 s older** than ours (prod median 37–50 s vs ours 13–19 s). A prediction
anchored to where the bus *was* reads later — i.e. we look "early". Cross-tabbing median delta by
inter-feed position distance × horizon (trip-matched, rollover excluded):

| local buses, distance between the two feeds' positions | 0–5 min | 5–15 | 15–30 | 30+ |
|---|---|---|---|---|
| 0–50 m (feeds agree on position) | **+3** | **+2** | **+0** | **−3** |
| 50–150 m | −4 | −6 | −10 | −15 |
| 150–400 m | −16 | −17 | −22 | −30 |
| 400 m+ | – | −56 | −80 | −60 |

Where both feeds place the bus within 50 m, the local bias is ~0 at short horizons and −3 to −13 s at
30+ min (two samples). It appears only as the anchors diverge, and the magnitude matches the mechanism:
150–400 m is roughly what a bus covers in ~22 s. **This is an artifact in our favour** — our
predictions are fresher, not faster. A uniformly optimistic engine would show its bias in the
position-agreeing stratum too, and it doesn't.

Also **ruled out: a deliberate policy difference.** Per-route 30+ min medians are two-sided —
SIM7 −338 s, SIM1 −218 s, QM63 −109 s at one end, **M11 +94 s, M5 +64 s, B15 +57 s, M4 +38 s** at the
other. A policy offset would be one-signed.

**F3 — the express residual (the one real engine gap).** At 30+ min: local median −8 s vs
**express −67 s**, and express stays at −41…−70 s *even in the position-agreeing stratum*, deepening to
−125 s at 150–400 m. Express MAE 88 → 119 s at a fixed window — the only class not improving. Cause
is established in §6.

### G / H. Two small known items

**G.** 35–50 vehicles appear in our VehiclePositions but not prod's; **none of them are in prod's
TripUpdates either**, and their position age skews old (p90 169 s). Prod filters unassigned/ghost buses
via `bustrek.mta.info/api/ghostbus/records`; we don't consume it. Benign.

**H.** 1.00 vs 1.82 TripUpdates/vehicle is entirely `PredictionLevel` (`CURRENT_TRIP` vs prod's
`NEXT_TRIP`) — one seeded config key, and fixing it also removes F1.

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

The biggest risk in this work is attributing a measurement artifact to the engine. Six controls, all
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

Also always check: host load-shedding state (`InferenceShedFixes`; it was **0** during the 07-27 PM
run, unlike 07-24), bundle pick changes (re-run `verify-parity.py`), and our own config drift.

---

## 8. Actions, in impact order

| # | action | class | blocker | effort |
|---|---|---|---|---|
| 1 | **Set `multiCSVLogger` `basePath` + a log4j config, rebuild** → the 4 STIF CSVs name why B1/Q84/Q30 trips were dropped; then fix and rebuild (§4C) | B | none — we own the build | S |
| 1b | **Add a build gate: per-route DSC coverage ≥90% fails the build** (would have caught all three routes pre-deploy) | B | none | S |
| 1c | **Script the bundle build** — currently a manual, unscripted, un-CI'd procedure (§4C) | B | none | S |
| 2 | Obtain **MTABC STIF**, rebuild (792 veh, 91 routes ≈ 20% of fleet) | A | external data | M |
| 3 | Obtain **UTS run/operator assignments** (TDM crew API / YardTrek S3) | C, D | access grant | M |
| 4 | Set `predictions.PredictionLevel=NEXT_TRIP` (feed-shape parity; also kills F1) | F1, H | none | XS |
| 5 | Restrict the ETA join to prod's current TripUpdate | F1 | see note | XS |
| 6 | Re-measure the weekday A/B **late August**, when ~80% of weekday buckets pass depth 50 | F3 | time | S |
| 7 | Decide on a coarser weekend bucket key (fold Sat+Sun / wider intervals) — needs its own A/B | F3 | design call | M |
| 8 | Keep the per-route coverage gate in every run | B | done | — |
| 9 | **Ground-truth scoring harness** — the only way to answer "who is right" | all | PRODUCTIONIZING §5 | L |
| 10 | Optional: consume the ghostbus feed to match prod's filtering | G | none | S |

**Note on #5:** deliberately not applied yet — it would redefine the headline MAE mid-series and break
comparability with the 07-22 → 07-27 baselines. Cost of leaving it is quantified (0.5% of pairs, <1 s
of ALL-bucket MAE). Bundle it with the next change that forces a new baseline; use `analyze-bias.py`
for artifact-free numbers meanwhile.

**Explicitly not recommended:** tuning `ScheduleLikelihood`'s informal precision or the DSC route-match
ratios to raise agreement with prod. That optimises toward prod's answers rather than correctness, and
the remaining disagreement classes are small and self-correcting.

---

## 9. What is still open

- **Which engine is more accurate** at long horizons — unanswerable by feed comparison; needs ground
  truth. Everything above measures *agreement*, and prod's own numbers carry the F2 staleness artifact.
- **B1: which loader condition dropped the trips.** The defect is pinned to the bundle's DSC index
  (§4B) and every input verified clean, but the specific drop reason needs the build's four STIF
  diagnostic CSVs, which were not retained. One rebuild answers it.
- **Does the read path use the same UTC hour convention** as the write path? Almost certainly yes
  (same clock), but the predictions repo is not cloned locally, so it was not code-verified. Re-check
  at the DST change.
- **Express trajectory** — regressing at a fixed window, and Fri-vs-Mon is a residual confounder.
  Needs a Mon-vs-Mon window.
- **Weekend accuracy** — structurally slow to warm; needs the §8 #7 decision.

---

## 10. Tooling built along the way

| script | purpose |
|---|---|
| `compare-feeds.py` | main live comparison → dated report |
| `probe-coverage.py` | coverage/freshness probe **+ per-route NYCT coverage gate** |
| `analyze-discrepancies.py` | classifies diff-trip pairs and big ETA deltas by shape |
| `analyze-bias.py` | strips rollover joins, stratifies bias by position distance × horizon |
| `compare-archives.py` | replays S3 archives for the **same clock window** across dates |
| `analyze-history-depth.py` | history-depth A/B (deep vs shallow buckets) |
| `make-history-fill-chart.py` | regenerates the history-fill chart |
| `verify-parity.py` | static-GTFS id parity gate (re-run per bundle pick) |
