# Archive fixed-window comparison — ours vs prod GTFS-RT

- generated 2026-07-30 11:44:50 EDT ; delta convention **ours − prod, seconds** (positive = we predict LATER)
- fleet: **NYCT**
- snapshots paired by GTFS-RT `header.timestamp` within 10 s; prod polls deduped by header ts

## Volume

| date | snapshot pairs | median pair skew | avg veh ours | avg veh prod | avg overlap | trip pairs | stop-pred deltas |
|---|---|---|---|---|---|---|---|
| 2026-07-27 | 48 | 5 s | 2610 | 2664 | 2575 | 123577 | 2251065 |

## Trip matching

| date | same trip_id | same route/diff trip | direction flip | diff route |
|---|---|---|---|---|
| 2026-07-27 | 97.2% | 2.4% | 0.3% | 0.05% |

## ETA deltas by horizon (median / MAE, seconds)

| date | ALL | 0-5 min | 5-15 min | 15-30 min | 30+ min | local | express |
|---|---|---|---|---|---|---|---|
| 2026-07-27 | -5 / 38 | -2 / 13 | -4 / 21 | -6 / 33 | -13 / 65 | -4 / 30 | -32 / 119 |

## Agreement rate — share of stop predictions within N of prod (the plain-English metric)

| date | within 30 s | within 1 min | within 3 min | within 5 min |
|---|---|---|---|---|
| 2026-07-27 | 65% | **85%** | **98%** | 99% |

### Agreement rate by how far ahead the prediction looks

| date | horizon | avg difference | within 1 min | within 3 min |
|---|---|---|---|---|
| 2026-07-27 | 0-5 min | 13 s | **99%** | 100% |
| 2026-07-27 | 5-15 min | 21 s | **96%** | 100% |
| 2026-07-27 | 15-30 min | 33 s | **88%** | 99% |
| 2026-07-27 | 30+ min | 65 s | **68%** | 94% |

