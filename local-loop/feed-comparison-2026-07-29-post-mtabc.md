# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-29 17:31:01 EDT   ·   4 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 17:27:59 | 3363 | 4 s | 3413 | 37 s | 3313 (99% of ours) | 54878 |
| 2 | 17:28:59 | 3346 | 2 s | 3387 | 13 s | 3283 (98% of ours) | 54931 |
| 3 | 17:30:00 | 3349 | 9 s | 3397 | 19 s | 3294 (98% of ours) | 55129 |
| 4 | 17:31:01 | 3365 | 6 s | 3397 | 37 s | 3317 (99% of ours) | 55122 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 12947 | 98.0% |
| same route, different trip_id | 213 | 1.6% |
| same route, different direction | 47 | 0.4% |
| different route | 0 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `7071` S46: ours `C6-Weekday-SDon-101300_MISC_345` vs MTA `C6-Weekday-SDon-102000_S4696_59`
- `7934` B49: ours `C6-Weekday-SDon-096400_B49_531` vs MTA `C6-Weekday-SDon-094500_B49_530`
- `7816` B52: ours `C6-Weekday-SDon-098200_B52_530` vs MTA `C6-Weekday-SDon-105600_B52_530`
- `7881` Q54: ours `C6-Weekday-SDon-103100_B7_6` vs MTA `C6-Weekday-SDon-101900_Q54_927`
- `7288` Q14: ours `C6-Weekday-SDon-100000_Q14_812` vs MTA `C6-Weekday-SDon-099000_Q14_814`
- `351` B70: ours `C6-Weekday-SDon-101000_B70_714` vs MTA `C6-Weekday-SDon-098200_B63_670`
- `366` B9: ours `C6-Weekday-SDon-105700_B9_230` vs MTA `C6-Weekday-SDon-101300_B9_221`
- `5970` BX19: ours `C6-Weekday-SDon-097200_BX19_345` vs MTA `C6-Weekday-SDon-096500_BX19_337`
- `2462` X28: ours `C6-Weekday-SDon-101900_X2737_701` vs MTA `C6-Weekday-SDon-101000_X2838_827`
- `2660` SIM34: ours `C6-Weekday-SDon-100500_SIM34_215` vs MTA `C6-Weekday-SDon-102900_MISC_319`
- `7748` Q54: ours `C6-Weekday-SDon-098300_Q54_926` vs MTA `C6-Weekday-SDon-097100_Q54_916`
- `7966` B46: ours `C6-Weekday-SDon-099400_B46_443` vs MTA `C6-Weekday-SDon-100600_B46_444`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 220060 | -11 | -10 | 49 | -75 / +49 | 78% | 96% | 98% |
| 0-5 min | 32970 | -5 | -3 | 23 | -38 / +30 | 95% | 100% | 100% |
| 5-15 min | 54321 | -8 | -8 | 31 | -53 / +36 | 89% | 99% | 100% |
| 15-30 min | 59355 | -12 | -12 | 43 | -69 / +47 | 80% | 98% | 99% |
| 30+ min | 73414 | -21 | -14 | 79 | -118 / +87 | 61% | 91% | 96% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 193637 | -11 | -10 | 38 | -64 / +42 | 83% | 99% | 100% |
| express | 26423 | -16 | -11 | 131 | -218 / +217 | 41% | 75% | 89% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 15 / mean 17.3, MTA median 14 / mean 17.2
- TripUpdates per vehicle: ours mean 1.00, MTA mean 1.79 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (0): —
- routes seen only in MTA (0): —
- avg vehicles/round: ours 3356, MTA 3398, overlap 3302

