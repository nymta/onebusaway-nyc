# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-30 11:41:45 EDT   ·   4 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 11:38:40 | 2238 | 5 s | 2255 | 17 s | 2195 (98% of ours) | 38540 |
| 2 | 11:39:42 | 2247 | 2 s | 2252 | 26 s | 2187 (97% of ours) | 38577 |
| 3 | 11:40:43 | 2253 | 11 s | 2268 | 14 s | 2213 (98% of ours) | 39283 |
| 4 | 11:41:44 | 2258 | 9 s | 2272 | 22 s | 2215 (98% of ours) | 39150 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 8695 | 98.7% |
| same route, different trip_id | 97 | 1.1% |
| same route, different direction | 18 | 0.2% |
| different route | 0 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `8700` S78: ours `C6-Weekday-SDon-064500_S78_245` vs MTA `C6-Weekday-SDon-063000_MISC_158`
- `7148` B14: ours `C6-Weekday-SDon-066000_B17_752` vs MTA `C6-Weekday-SDon-061500_B14_707`
- `8837` Q1: ours `C6-Weekday-SDon-067700_MISC_196` vs MTA `C6-Weekday-SDon-066000_MISC_174`
- `4885` B74: ours `C6-Weekday-SDon-066500_B74_603` vs MTA `C6-Weekday-SDon-071000_B74_603`
- `8959` Q58: ours `C6-Weekday-SDon-069700_MISC_622` vs MTA `C6-Weekday-SDon-068800_MISC_684`
- `7958` B41: ours `C6-Weekday-SDon-066600_B41_214` vs MTA `C6-Weekday-SDon-060600_B41_232`
- `4737` Q44+: ours `C6-Weekday-SDon-066700_SBS44_913` vs MTA `C6-Weekday-SDon-065900_SBS44_946`
- `7858` Q54: ours `C6-Weekday-SDon-068600_Q54_906` vs MTA `C6-Weekday-SDon-067600_Q54_928`
- `8025` Q88: ours `C6-Weekday-SDon-067800_MISC_200` vs MTA `C6-Weekday-SDon-066800_MISC_116`
- `8722` Q48: ours `C6-Weekday-SDon-066000_Q48_652` vs MTA `C6-Weekday-SDon-064000_Q48_651`
- `7286` B60: ours `C6-Weekday-SDon-069200_B13_107` vs MTA `C6-Weekday-SDon-062300_Q55_958`
- `422` B63: ours `C6-Weekday-SDon-063500_B63_656` vs MTA `C6-Weekday-SDon-062300_B63_663`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 155550 | +1 | -5 | 57 | -92 / +80 | 68% | 95% | 99% |
| 0-5 min | 25092 | +0 | +1 | 20 | -31 / +32 | 96% | 100% | 100% |
| 5-15 min | 40856 | +0 | -1 | 36 | -56 / +56 | 83% | 99% | 100% |
| 15-30 min | 43071 | +2 | -1 | 56 | -87 / +84 | 65% | 97% | 99% |
| 30+ min | 46531 | +0 | -15 | 96 | -172 / +121 | 44% | 87% | 96% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 148491 | +1 | -4 | 54 | -87 / +78 | 70% | 96% | 99% |
| express | 7059 | -9 | -28 | 132 | -253 / +174 | 39% | 73% | 89% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 14 / mean 17.8, MTA median 15 / mean 18.0
- TripUpdates per vehicle: ours mean 1.00, MTA mean 1.84 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (0): —
- routes seen only in MTA (1): SIM8
- avg vehicles/round: ours 2249, MTA 2262, overlap 2202

