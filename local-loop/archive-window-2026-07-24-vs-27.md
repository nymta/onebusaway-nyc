# Archive fixed-window comparison — ours vs prod GTFS-RT

- generated 2026-07-27 17:36:10 EDT ; delta convention **ours − prod, seconds** (positive = we predict LATER)
- snapshots paired by GTFS-RT `header.timestamp` within 10 s; prod polls deduped by header ts

## Volume

| date | snapshot pairs | median pair skew | avg veh ours | avg veh prod | avg overlap | trip pairs | stop-pred deltas |
|---|---|---|---|---|---|---|---|
| 2026-07-24 | 31 | 5 s | 2562 | 3403 | 2525 | 78289 | 1418564 |
| 2026-07-27 | 48 | 5 s | 2610 | 3440 | 2575 | 123577 | 2251065 |

## Trip matching

| date | same trip_id | same route/diff trip | direction flip | diff route |
|---|---|---|---|---|
| 2026-07-24 | 96.6% | 3.1% | 0.3% | 0.02% |
| 2026-07-27 | 97.2% | 2.4% | 0.3% | 0.05% |

## ETA deltas by horizon (median / MAE, seconds)

| date | ALL | 0-5 min | 5-15 min | 15-30 min | 30+ min | local | express |
|---|---|---|---|---|---|---|---|
| 2026-07-24 | -6 / 37 | -2 / 13 | -4 / 21 | -8 / 35 | -16 / 60 | -5 / 32 | -29 / 88 |
| 2026-07-27 | -5 / 38 | -2 / 13 | -4 / 21 | -6 / 33 | -13 / 65 | -4 / 30 | -32 / 119 |

