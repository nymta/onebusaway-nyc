# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-23 10:26:08 EDT   ·   4 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 10:23:06 | 1903 | 11 s | 2394 | 11 s | 1882 (99% of ours) | 34850 |
| 2 | 10:24:06 | 1883 | 10 s | 2394 | 30 s | 1855 (99% of ours) | 34054 |
| 3 | 10:25:07 | 1877 | 10 s | 2374 | 18 s | 1855 (99% of ours) | 34183 |
| 4 | 10:26:08 | 1865 | 10 s | 2365 | 26 s | 1845 (99% of ours) | 33835 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 7299 | 98.1% |
| same route, different trip_id | 122 | 1.6% |
| same route, different direction | 16 | 0.2% |
| different route | 0 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `4796` B1: ours `C6-Weekday-SDon-061700_B1_18` vs MTA `C6-Weekday-SDon-056500_B1_20`
- `8869` Q58: ours `C6-Weekday-SDon-057700_MISC_604` vs MTA `C6-Weekday-SDon-056900_MISC_610`
- `8952` Q98: ours `C6-Weekday-SDon-059600_Q98_805` vs MTA `C6-Weekday-SDon-058400_MISC_622`
- `5969` M103: ours `C6-Weekday-SDon-052500_M101_57` vs MTA `C6-Weekday-SDon-057900_M101_63`
- `7155` B15: ours `C6-Weekday-SDon-058500_B15_113` vs MTA `C6-Weekday-SDon-058500_B15_117`
- `1663` SIM1C: ours `C6-Weekday-SDon-053800_SIM10_587` vs MTA `C6-Weekday-SDon-053000_MISC_755`
- `1003` BX6+: ours `C6-Weekday-SDon-056900_SBS6_161` vs MTA `C6-Weekday-SDon-058100_SBS6_154`
- `4744` B1: ours `C6-Weekday-SDon-061700_B1_18` vs MTA `C6-Weekday-SDon-057300_B1_4`
- `5900` M101: ours `C6-Weekday-SDon-052000_M101_41` vs MTA `C6-Weekday-SDon-053000_M101_62`
- `6101` M101: ours `C6-Weekday-SDon-062500_M101_61` vs MTA `C6-Weekday-SDon-061500_M101_30`
- `6067` M103: ours `C6-Weekday-SDon-060600_M101_85` vs MTA `C6-Weekday-SDon-061200_M101_84`
- `7074` S54: ours `C6-Weekday-SDon-060000_S54_121` vs MTA `C6-Weekday-SDon-060000_S54_120`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 136922 | +4 | +9 | 45 | -58 / +82 | 76% | 97% | 100% |
| 0-5 min | 22430 | +0 | +1 | 17 | -26 / +27 | 98% | 100% | 100% |
| 5-15 min | 35976 | +2 | +4 | 30 | -42 / +49 | 89% | 99% | 100% |
| 15-30 min | 38854 | +7 | +11 | 47 | -59 / +82 | 74% | 98% | 100% |
| 30+ min | 39662 | +14 | +17 | 73 | -96 / +129 | 53% | 93% | 99% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 134048 | +4 | +8 | 44 | -58 / +77 | 77% | 98% | 100% |
| express | 2874 | +69 | +82 | 109 | -36 / +253 | 41% | 79% | 96% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 15 / mean 18.6, MTA median 15 / mean 18.8
- TripUpdates per vehicle: ours mean 1.00, MTA mean 1.80 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (1): Q90
- routes seen only in MTA (75): B100, B103, BM1, BM2, BM3, BM4, BM5, BX23, BXM1, BXM10, BXM11, BXM2, BXM3, BXM4, BXM6, BXM7, BXM8, BXM9, Q06, Q07, Q08, Q09, Q10, Q100, Q101, Q102, Q103, Q104, Q11, Q110, Q111, Q112, Q113, Q114, Q115, Q18, Q19, Q22, Q23, Q25, Q26, Q28, Q32, Q33, Q35, Q37, Q40, Q41, Q47, Q49, Q50, Q51, Q52+, Q53+, Q60, Q63, Q64, Q65, Q66, Q69, Q70+, Q72, Q74, Q80, QM10, QM11, QM12, QM15, QM2, QM20, QM32, QM4, QM5, QM6, QM65
- avg vehicles/round: ours 1882, MTA 2382, overlap 1859

