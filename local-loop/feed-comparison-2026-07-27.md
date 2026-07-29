# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-27 08:48:02 EDT   ·   4 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 08:45:01 | 2499 | 5 s | 3253 | 30 s | 2465 (99% of ours) | 41369 |
| 2 | 08:46:01 | 2495 | 4 s | 3236 | 16 s | 2467 (99% of ours) | 41867 |
| 3 | 08:47:02 | 2503 | 2 s | 3228 | 23 s | 2463 (98% of ours) | 42008 |
| 4 | 08:48:02 | 2493 | 10 s | 3217 | 30 s | 2455 (98% of ours) | 41783 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 9526 | 96.7% |
| same route, different trip_id | 297 | 3.0% |
| same route, different direction | 23 | 0.2% |
| different route | 4 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `8399` S93: ours `C6-Weekday-SDon-052200_MISC_294` vs MTA `C6-Weekday-SDon-053300_MISC_305`
- `7160` B6: ours `C6-Weekday-SDon-051900_B6_247` vs MTA `C6-Weekday-SDon-052500_B6_249`
- `8796` Q58: ours `C6-Weekday-SDon-046700_Q58_415` vs MTA `C6-Weekday-SDon-046200_MISC_616`
- `5969` M103: ours `C6-Weekday-SDon-047100_M101_63` vs MTA `C6-Weekday-SDon-047900_M101_25`
- `7302` Q54: ours `C6-Weekday-SDon-043800_Q54_923` vs MTA `C6-Weekday-SDon-044500_Q54_906`
- `1206` BX15: ours `C6-Weekday-SDon-050600_BX15_818` vs MTA `C6-Weekday-SDon-052000_BX15_805`
- `7242` B62: ours `C6-Weekday-SDon-047000_B62_616` vs MTA `C6-Weekday-SDon-047800_B62_617`
- `7006` B41: ours `C6-Weekday-SDon-049300_B41_228` vs MTA `C6-Weekday-SDon-048000_B41_227`
- `7017` S48: ours `C6-Weekday-SDon-048400_MISC_247` vs MTA `C6-Weekday-SDon-049600_MISC_285`
- `8085` Q3: ours `C6-Weekday-SDon-051400_MISC_836` vs MTA `C6-Weekday-SDon-053400_MISC_753`
- `8084` Q86: ours `C6-Weekday-SDon-051700_Q86_655` vs MTA `C6-Weekday-SDon-050200_MISC_823`
- `2690` SIM5: ours `C6-Weekday-SDon-047300_SIM11_601` vs MTA `C6-Weekday-SDon-048300_MISC_715`

Different-route examples (vehicle: ours route/trip vs MTA route/trip):
- `8368`: ours BX18B `C6-Weekday-SDon-051500_BX18_958` vs MTA BX18A `C6-Weekday-SDon-052000_BX18_952`
- `8368`: ours BX18B `C6-Weekday-SDon-051500_BX18_958` vs MTA BX18A `C6-Weekday-SDon-052000_BX18_952`
- `8368`: ours BX18B `C6-Weekday-SDon-051500_BX18_958` vs MTA BX18A `C6-Weekday-SDon-052000_BX18_952`
- `8368`: ours BX18B `C6-Weekday-SDon-051500_BX18_958` vs MTA BX18A `C6-Weekday-SDon-052000_BX18_952`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 167027 | -7 | -9 | 37 | -62 / +35 | 86% | 99% | 99% |
| 0-5 min | 28059 | +0 | +0 | 18 | -29 / +26 | 98% | 100% | 100% |
| 5-15 min | 44902 | -4 | -5 | 25 | -43 / +30 | 94% | 100% | 100% |
| 15-30 min | 46483 | -10 | -12 | 36 | -61 / +35 | 87% | 99% | 99% |
| 30+ min | 47583 | -18 | -17 | 59 | -97 / +51 | 70% | 97% | 98% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 160302 | -7 | -11 | 35 | -61 / +33 | 87% | 99% | 99% |
| express | 6725 | +0 | +18 | 76 | -96 / +134 | 64% | 91% | 95% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 13 / mean 17.2, MTA median 13 / mean 17.4
- TripUpdates per vehicle: ours mean 1.00, MTA mean 1.72 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (0): —
- routes seen only in MTA (90): B100, B103, BM1, BM2, BM3, BM4, BM5, BX23, BXM1, BXM10, BXM11, BXM18, BXM2, BXM3, BXM4, BXM6, BXM7, BXM8, BXM9, Q06, Q07, Q08, Q09, Q10, Q100, Q101, Q102, Q103, Q104, Q11, Q110, Q111, Q112, Q113, Q114, Q115, Q18, Q19, Q22, Q23, Q25, Q26, Q28, Q32, Q33, Q35, Q37, Q40, Q41, Q47, Q49, Q50, Q51, Q52+, Q53+, Q60, Q63, Q64, Q65, Q66, Q69, Q70+, Q72, Q74, Q80, QM1, QM10, QM11, QM12, QM15, QM16, QM17, QM18, QM2, QM20, QM21, QM24, QM31, QM32, QM34, QM35, QM36, QM4, QM42, QM44, QM5, QM6, QM65, QM7, QM8
- avg vehicles/round: ours 2498, MTA 3234, overlap 2462

