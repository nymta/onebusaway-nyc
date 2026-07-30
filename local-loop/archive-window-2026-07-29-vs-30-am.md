# Archive fixed-window comparison — ours vs prod GTFS-RT

- generated 2026-07-30 08:54:35 EDT ; delta convention **ours − prod, seconds** (positive = we predict LATER)
- snapshots paired by GTFS-RT `header.timestamp` within 10 s; prod polls deduped by header ts

## Volume

| date | snapshot pairs | median pair skew | avg veh ours | avg veh prod | avg overlap | trip pairs | stop-pred deltas |
|---|---|---|---|---|---|---|---|
| 2026-07-29 | 49 | 6 s | 2543 | 3322 | 2512 | 123104 | 2135762 |
| 2026-07-30 | 23 | 2 s | 3306 | 3338 | 3247 | 74677 | 1159913 |

## Trip matching

| date | same trip_id | same route/diff trip | direction flip | diff route |
|---|---|---|---|---|
| 2026-07-29 | 97.4% | 2.2% | 0.4% | 0.01% |
| 2026-07-30 | 98.3% | 1.2% | 0.5% | 0.01% |

## ETA deltas by horizon (median / MAE, seconds)

| date | ALL | 0-5 min | 5-15 min | 15-30 min | 30+ min | local | express |
|---|---|---|---|---|---|---|---|
| 2026-07-29 | -9 / 31 | -2 / 11 | -6 / 20 | -11 / 31 | -23 / 54 | -9 / 28 | -21 / 98 |
| 2026-07-30 | -15 / 47 | -6 / 26 | -11 / 34 | -16 / 45 | -30 / 75 | -15 / 42 | -15 / 107 |

