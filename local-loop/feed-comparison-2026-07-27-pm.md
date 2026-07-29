# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-27 17:13:49 EDT   ·   4 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 17:10:48 | 2588 | 9 s | 3410 | 2 s | 2552 (99% of ours) | 46802 |
| 2 | 17:11:48 | 2586 | 8 s | 3412 | 20 s | 2540 (98% of ours) | 46970 |
| 3 | 17:12:49 | 2601 | 6 s | 3419 | 26 s | 2548 (98% of ours) | 46582 |
| 4 | 17:13:49 | 2608 | 3 s | 3415 | 22 s | 2557 (98% of ours) | 46867 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 9903 | 97.1% |
| same route, different trip_id | 262 | 2.6% |
| same route, different direction | 31 | 0.3% |
| different route | 1 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `7083` S53: ours `C6-Weekday-SDon-097300_MISC_370` vs MTA `C6-Weekday-SDon-096500_MISC_368`
- `2687` SIM1: ours `C6-Weekday-SDon-098000_SIM1_576` vs MTA `C6-Weekday-SDon-096700_MISC_747`
- `5456` M86+: ours `C6-Weekday-SDon-102700_SBS86_966` vs MTA `C6-Weekday-SDon-103100_SBS86_964`
- `7271` B13: ours `C6-Weekday-SDon-099000_B13_121` vs MTA `C6-Weekday-SDon-098000_B60_733`
- `6113` M103: ours `C6-Weekday-SDon-094500_M101_133` vs MTA `C6-Weekday-SDon-092100_M101_70`
- `4611` B46: ours `C6-Weekday-SDon-101800_B46_445` vs MTA `C6-Weekday-SDon-100600_B46_444`
- `7422` Q61: ours `C6-Weekday-SDon-103800_MISC_707` vs MTA `C6-Weekday-SDon-097500_MISC_652`
- `8389` BX9: ours `C6-Weekday-SDon-099600_BX9_631` vs MTA `C6-Weekday-SDon-099000_BX9_609`
- `5829` M101: ours `C6-Weekday-SDon-096000_M101_82` vs MTA `C6-Weekday-SDon-095200_M101_60`
- `8869` Q98: ours `C6-Weekday-SDon-103000_Q98_807` vs MTA `C6-Weekday-SDon-091800_Q98_807`
- `7322` B46: ours `C6-Weekday-SDon-097100_B46_436` vs MTA `C6-Weekday-SDon-098000_B46_437`
- `9678` BX28: ours `C6-Weekday-SDon-096000_BX238_640` vs MTA `C6-Weekday-SDon-094800_BX238_617`

Different-route examples (vehicle: ours route/trip vs MTA route/trip):
- `9583`: ours M1 `C6-Weekday-SDon-092900_M1_134` vs MTA M7 `C6-Weekday-SDon-092600_M7_208`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 187221 | -4 | -6 | 37 | -57 / +44 | 85% | 98% | 99% |
| 0-5 min | 27534 | -1 | -1 | 16 | -26 / +23 | 98% | 100% | 100% |
| 5-15 min | 45012 | -3 | -3 | 23 | -37 / +32 | 95% | 100% | 100% |
| 15-30 min | 50574 | -5 | -5 | 34 | -53 / +44 | 87% | 99% | 100% |
| 30+ min | 64101 | -10 | -11 | 59 | -89 / +68 | 70% | 95% | 99% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 172433 | -3 | -5 | 32 | -51 / +42 | 88% | 99% | 100% |
| express | 14788 | -27 | -17 | 97 | -174 / +101 | 53% | 84% | 95% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 16 / mean 18.5, MTA median 16 / mean 18.7
- TripUpdates per vehicle: ours mean 1.00, MTA mean 1.82 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (0): —
- routes seen only in MTA (92): B1, B100, B103, BM1, BM2, BM3, BM4, BM5, BX23, BXM1, BXM10, BXM11, BXM18, BXM2, BXM3, BXM4, BXM6, BXM7, BXM8, BXM9, Q06, Q07, Q08, Q09, Q10, Q100, Q101, Q102, Q103, Q104, Q11, Q110, Q111, Q112, Q113, Q114, Q115, Q18, Q19, Q22, Q23, Q25, Q26, Q28, Q32, Q33, Q35, Q37, Q40, Q41, Q47, Q49, Q50, Q51, Q52+, Q53+, Q60, Q63, Q64, Q65, Q66, Q69, Q70+, Q72, Q74, Q80, QM1, QM10, QM11, QM12, QM15, QM16, QM17, QM18, QM2, QM20, QM21, QM24, QM25, QM31, QM32, QM34, QM35, QM4, QM40, QM42, QM44, QM5, QM6, QM65, QM7, QM8
- avg vehicles/round: ours 2596, MTA 3414, overlap 2549

