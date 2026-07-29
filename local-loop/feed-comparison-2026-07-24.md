# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-24 17:50:20 EDT   ·   4 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 17:47:17 | 2540 | 1 s | 3410 | 18 s | 2502 (99% of ours) | 45106 |
| 2 | 17:48:18 | 2549 | 10 s | 3413 | 5 s | 2518 (99% of ours) | 45648 |
| 3 | 17:49:19 | 2534 | 8 s | 3408 | 22 s | 2504 (99% of ours) | 44973 |
| 4 | 17:50:19 | 2529 | 7 s | 3391 | 30 s | 2483 (98% of ours) | 44941 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 9653 | 96.5% |
| same route, different trip_id | 336 | 3.4% |
| same route, different direction | 16 | 0.2% |
| different route | 2 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `8593` S79+: ours `C6-Weekday-SDon-105000_MISC_780` vs MTA `C6-Weekday-SDon-104400_MISC_765`
- `7101` B15: ours `C6-Weekday-SDon-098800_B15_139` vs MTA `C6-Weekday-SDon-104300_B15_141`
- `7792` B45: ours `C6-Weekday-SDon-102000_B45_816` vs MTA `C6-Weekday-SDon-095800_B45_809`
- `2504` X28: ours `C6-Weekday-SDon-101000_X2838_827` vs MTA `C6-Weekday-SDon-102600_X2838_803`
- `8313` S94: ours `C6-Weekday-SDon-105200_S4494_414` vs MTA `C6-Weekday-SDon-105000_S4494_405`
- `6047` M103: ours `C6-Weekday-SDon-105800_M101_133` vs MTA `C6-Weekday-SDon-096100_M101_76`
- `8318` S93: ours `C6-Weekday-SDon-106300_MISC_322` vs MTA `C6-Weekday-SDon-107300_MISC_375`
- `7577` BX11: ours `C6-Weekday-SDon-104700_BX11_224` vs MTA `C6-Weekday-SDon-103700_BX11_237`
- `7193` Q24: ours `C6-Weekday-SDon-099600_Q24_424` vs MTA `C6-Weekday-SDon-098400_B25_235`
- `5949` M103: ours `C6-Weekday-SDon-094500_M101_133` vs MTA `C6-Weekday-SDon-100100_M101_100`
- `5836` M103: ours `C6-Weekday-SDon-096200_M101_65` vs MTA `C6-Weekday-SDon-097400_M101_116`
- `4849` Q24: ours `C6-Weekday-SDon-103100_Q24_421` vs MTA `C6-Weekday-SDon-104300_B15_156`

Different-route examples (vehicle: ours route/trip vs MTA route/trip):
- `8203`: ours S90 `C6-Weekday-SDon-108000_S4090_20` vs MTA S40 `C6-Weekday-SDon-103500_S4090_13`
- `8203`: ours S90 `C6-Weekday-SDon-108000_S4090_20` vs MTA S40 `C6-Weekday-SDon-103500_S4090_13`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 180668 | -4 | -10 | 41 | -62 / +46 | 83% | 98% | 99% |
| 0-5 min | 26992 | -1 | -1 | 16 | -27 / +24 | 98% | 100% | 100% |
| 5-15 min | 44493 | -2 | -2 | 24 | -38 / +34 | 94% | 100% | 100% |
| 15-30 min | 48812 | -4 | -5 | 36 | -55 / +48 | 85% | 99% | 100% |
| 30+ min | 60371 | -13 | -23 | 70 | -110 / +74 | 66% | 94% | 98% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 164945 | -3 | -4 | 35 | -53 / +46 | 86% | 99% | 99% |
| express | 15723 | -27 | -68 | 108 | -207 / +42 | 54% | 83% | 94% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 15 / mean 18.2, MTA median 15 / mean 18.4
- TripUpdates per vehicle: ours mean 1.00, MTA mean 1.75 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (0): —
- routes seen only in MTA (93): B1, B100, B103, BM1, BM2, BM3, BM4, BM5, BX23, BXM1, BXM10, BXM11, BXM18, BXM2, BXM3, BXM4, BXM6, BXM7, BXM8, BXM9, Q06, Q07, Q08, Q09, Q10, Q100, Q101, Q102, Q103, Q104, Q11, Q110, Q111, Q112, Q113, Q114, Q115, Q18, Q19, Q22, Q23, Q25, Q26, Q28, Q32, Q33, Q35, Q37, Q40, Q41, Q47, Q49, Q50, Q51, Q52+, Q53+, Q60, Q63, Q64, Q65, Q66, Q69, Q70+, Q72, Q74, Q80, QM1, QM10, QM11, QM12, QM15, QM16, QM17, QM18, QM2, QM20, QM21, QM24, QM25, QM31, QM32, QM34, QM35, QM36, QM4, QM40, QM42, QM44, QM5, QM6, QM65, QM7, QM8
- avg vehicles/round: ours 2538, MTA 3406, overlap 2502

