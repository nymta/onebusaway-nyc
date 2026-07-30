# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-30 08:50:32 EDT   ·   4 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 08:47:30 | 3321 | 9 s | 3338 | 30 s | 3264 (98% of ours) | 50755 |
| 2 | 08:48:31 | 3296 | 6 s | 3323 | 5 s | 3231 (98% of ours) | 49804 |
| 3 | 08:49:31 | 3272 | 3 s | 3312 | 23 s | 3226 (99% of ours) | 49417 |
| 4 | 08:50:32 | 3269 | 10 s | 3311 | 30 s | 3225 (99% of ours) | 49233 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 12739 | 98.4% |
| same route, different trip_id | 153 | 1.2% |
| same route, different direction | 52 | 0.4% |
| different route | 2 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `7416` Q31: ours `C6-Weekday-SDon-047300_MISC_661` vs MTA `C6-Weekday-SDon-046400_MISC_625`
- `4873` B6: ours `C6-Weekday-SDon-049500_B36_413` vs MTA `C6-Weekday-SDon-050100_X2737_718`
- `6119` B46+: ours `C6-Weekday-SDon-049900_SBS46_705` vs MTA `C6-Weekday-SDon-049600_SBS46_726`
- `7831` B57: ours `C6-Weekday-SDon-047700_B57_504` vs MTA `C6-Weekday-SDon-046200_B57_503`
- `6210` M15+: ours `C6-Weekday-SDon-044700_SBS15_436` vs MTA `C6-Weekday-SDon-053200_SBS15_436`
- `8073` Q27: ours `C6-Weekday-SDon-048400_MISC_192` vs MTA `C6-Weekday-SDon-047900_MISC_188`
- `4785` B38: ours `C6-Weekday-SDon-049500_B38_210` vs MTA `C6-Weekday-SDon-048700_B38_207`
- `2466` X38: ours `C6-Weekday-SDon-045900_X2838_802` vs MTA `C6-Weekday-SDon-046700_X2737_727`
- `424` B69: ours `C6-Weekday-SDon-045900_B6769_923` vs MTA `C6-Weekday-SDon-047900_B6769_908`
- `8893` S93: ours `C6-Weekday-SDon-052200_MISC_294` vs MTA `C6-Weekday-SDon-053300_MISC_305`
- `7434` Q38: ours `C6-Weekday-SDon-048500_MISC_630` vs MTA `C6-Weekday-SDon-051800_MISC_630`
- `5943` BX4: ours `C6-Weekday-SDon-052800_BX44A_715` vs MTA `C6-Weekday-SDon-051300_BX402_25`

Different-route examples (vehicle: ours route/trip vs MTA route/trip):
- `8396`: ours BX18A `C6-Weekday-SDon-048000_BX18_959` vs MTA BX18B `C6-Weekday-SDon-052500_BX18_959`
- `8396`: ours BX18A `C6-Weekday-SDon-048000_BX18_959` vs MTA BX18B `C6-Weekday-SDon-052500_BX18_959`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 199209 | -13 | -16 | 44 | -76 / +40 | 80% | 98% | 99% |
| 0-5 min | 33933 | -6 | -5 | 23 | -39 / +31 | 96% | 100% | 100% |
| 5-15 min | 54763 | -10 | -10 | 31 | -54 / +36 | 89% | 100% | 100% |
| 15-30 min | 56449 | -14 | -16 | 42 | -73 / +42 | 80% | 99% | 99% |
| 30+ min | 54064 | -28 | -28 | 72 | -116 / +56 | 60% | 93% | 97% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 183153 | -13 | -17 | 39 | -72 / +36 | 82% | 99% | 100% |
| express | 16056 | -13 | +0 | 104 | -135 / +190 | 52% | 83% | 93% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 12 / mean 16.0, MTA median 12 / mean 15.9
- TripUpdates per vehicle: ours mean 1.00, MTA mean 1.70 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (1): B84
- routes seen only in MTA (0): —
- avg vehicles/round: ours 3290, MTA 3321, overlap 3236

