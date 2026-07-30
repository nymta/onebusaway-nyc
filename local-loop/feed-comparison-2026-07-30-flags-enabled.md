# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-30 15:49:53 EDT   ·   3 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 15:47:48 | 3025 | 4 s | 3023 | 31 s | 2966 (98% of ours) | 52455 |
| 2 | 15:48:50 | 3045 | 13 s | 3043 | 19 s | 3001 (99% of ours) | 53151 |
| 3 | 15:49:52 | 3059 | 10 s | 3068 | 39 s | 3007 (98% of ours) | 52971 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 8798 | 98.0% |
| same route, different trip_id | 151 | 1.7% |
| same route, different direction | 22 | 0.2% |
| different route | 3 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `5004` M9: ours `C6-Weekday-SDon-089500_M9_59` vs MTA `C6-Weekday-SDon-088300_M21_205`
- `7150` B14: ours `C6-Weekday-SDon-090600_B17_766` vs MTA `C6-Weekday-SDon-089400_B14_714`
- `9774` M9: ours `C6-Weekday-SDon-094300_M9_61` vs MTA `C6-Weekday-SDon-093100_M22_257`
- `7114` B12: ours `C6-Weekday-SDon-089600_B12_27` vs MTA `C6-Weekday-SDon-090300_B83_308`
- `8753` BX28: ours `C6-Weekday-SDon-094600_BX256_507` vs MTA `C6-Weekday-SDon-093600_BX30_560`
- `772` B67: ours `C6-Weekday-SDon-088600_B6769_916` vs MTA `C6-Weekday-SDon-086200_B6769_920`
- `9907` M9: ours `C6-Weekday-SDon-092400_M9_65` vs MTA `C6-Weekday-SDon-091200_M22_258`
- `1238` BX40: ours `C6-Weekday-SDon-093800_BX402_47` vs MTA `C6-Weekday-SDon-092100_BX44A_712`
- `7227` B39: ours `C6-Weekday-SDon-093000_B39_301` vs MTA `C6-Weekday-SDon-096000_B39_301`
- `8290` S61: ours `C6-Weekday-SDon-088500_MISC_746` vs MTA `C6-Weekday-SDon-090000_S6191_462`
- `4873` B3: ours `C6-Weekday-SDon-091600_X2838_807` vs MTA `C6-Weekday-SDon-090800_X2838_804`
- `7626` Q67: ours `C6-Weekday-SDon-098700_Q29_718` vs MTA `C6-Weekday-SDon-093300_Q67_161`

Different-route examples (vehicle: ours route/trip vs MTA route/trip):
- `1641`: ours SIM3 `C6-Weekday-SDon-097000_SIM3_173` vs MTA SIM3C `C6-Weekday-SDon-081000_MISC_305`
- `1641`: ours SIM3 `C6-Weekday-SDon-097000_SIM3_173` vs MTA SIM3C `C6-Weekday-SDon-081000_MISC_305`
- `1641`: ours SIM3 `C6-Weekday-SDon-095500_SIM3_172` vs MTA SIM3C `C6-Weekday-SDon-081000_MISC_305`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 158577 | -12 | -20 | 69 | -126 / +71 | 64% | 93% | 98% |
| 0-5 min | 23107 | -1 | +0 | 25 | -40 / +35 | 94% | 100% | 100% |
| 5-15 min | 38513 | -7 | -8 | 43 | -74 / +54 | 77% | 99% | 100% |
| 15-30 min | 43188 | -15 | -17 | 64 | -116 / +76 | 61% | 96% | 99% |
| 30+ min | 53769 | -33 | -39 | 109 | -198 / +103 | 43% | 84% | 95% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 147044 | -12 | -21 | 62 | -122 / +64 | 65% | 95% | 99% |
| express | 11533 | +5 | -5 | 156 | -256 / +257 | 42% | 70% | 84% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 15 / mean 17.9, MTA median 15 / mean 18.1
- TripUpdates per vehicle: ours mean 1.89, MTA mean 1.89 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (0): —
- routes seen only in MTA (0): —
- avg vehicles/round: ours 3043, MTA 3045, overlap 2991

