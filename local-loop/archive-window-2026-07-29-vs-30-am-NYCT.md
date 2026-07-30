# Archive fixed-window comparison — ours vs prod GTFS-RT

- generated 2026-07-30 10:15:29 EDT ; delta convention **ours − prod, seconds** (positive = we predict LATER)
- fleet: **NYCT**
- snapshots paired by GTFS-RT `header.timestamp` within 10 s; prod polls deduped by header ts

## Volume

| date | snapshot pairs | median pair skew | avg veh ours | avg veh prod | avg overlap | trip pairs | stop-pred deltas |
|---|---|---|---|---|---|---|---|
| 2026-07-29 | 25 | 6 s | 2567 | 2615 | 2535 | 63366 | 1102906 |
| 2026-07-30 | 23 | 2 s | 2561 | 2589 | 2515 | 57850 | 980469 |

## Trip matching

| date | same trip_id | same route/diff trip | direction flip | diff route |
|---|---|---|---|---|
| 2026-07-29 | 97.4% | 2.2% | 0.4% | 0.01% |
| 2026-07-30 | 97.8% | 1.6% | 0.6% | 0.01% |

## ETA deltas by horizon (median / MAE, seconds)

| date | ALL | 0-5 min | 5-15 min | 15-30 min | 30+ min | local | express |
|---|---|---|---|---|---|---|---|
| 2026-07-29 | -9 / 31 | -2 / 11 | -6 / 19 | -12 / 30 | -24 / 53 | -9 / 28 | -24 / 83 |
| 2026-07-30 | -16 / 42 | -6 / 25 | -12 / 31 | -17 / 40 | -33 / 64 | -15 / 39 | -40 / 107 |

