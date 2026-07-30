# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison

- generated: 2026-07-30 16:36:46 EDT   ·   4 snapshot round(s), 60 s apart
- ours: `http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates`   ·   MTA: `http://gtfsrt.prod.obanyc.com/tripUpdates`
- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)

## Snapshots

| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |
|---|---|---|---|---|---|---|---|
| 1 | 16:33:40 | 3249 | 4 s | 3248 | 28 s | 3196 (98% of ours) | 55245 |
| 2 | 16:34:42 | 3279 | 3 s | 3273 | 15 s | 3234 (99% of ours) | 55979 |
| 3 | 16:35:44 | 3287 | 12 s | 3294 | 23 s | 3239 (99% of ours) | 55723 |
| 4 | 16:36:46 | 3282 | 10 s | 3277 | 12 s | 3241 (99% of ours) | 56201 |

## Trip matching (vehicles present in both feeds, per-round pairs pooled)

| classification | pairs | share |
|---|---|---|
| same trip_id | 12634 | 97.9% |
| same route, different trip_id | 242 | 1.9% |
| same route, different direction | 34 | 0.3% |
| different route | 0 | 0.0% |

Same-route/different-trip examples (vehicle, route, our trip, MTA trip):
- `5004` M9: ours `C6-Weekday-SDon-097200_M8_5` vs MTA `C6-Weekday-SDon-094800_M21_205`
- `7113` B12: ours `C6-Weekday-SDon-099000_B12_25` vs MTA `C6-Weekday-SDon-098300_B12_34`
- `1586` SIM6: ours `C6-Weekday-SDon-091500_MISC_745` vs MTA `C6-Weekday-SDon-088500_MISC_178`
- `9682` M4: ours `C6-Weekday-SDon-085800_M4_424` vs MTA `C6-Weekday-SDon-101000_M4_442`
- `7670` B82+: ours `C6-Weekday-SDon-093500_SBS82_927` vs MTA `C6-Weekday-SDon-092700_SBS82_912`
- `8864` Q58: ours `C6-Weekday-SDon-097800_Q58_426` vs MTA `C6-Weekday-SDon-097300_Q58_433`
- `7153` B42: ours `C6-Weekday-SDon-097800_B42_506` vs MTA `C6-Weekday-SDon-097200_B82_616`
- `9487` M7: ours `C6-Weekday-SDon-098600_M7_240` vs MTA `C6-Weekday-SDon-096600_M35_304`
- `5275` BX36: ours `C6-Weekday-SDon-095500_BX36_725` vs MTA `C6-Weekday-SDon-094800_BX36_752`
- `5770` BX22: ours `C6-Weekday-SDon-098500_BX22_432` vs MTA `C6-Weekday-SDon-097400_BX22_421`
- `8602` S79+: ours `C6-Weekday-SDon-098000_MISC_767` vs MTA `C6-Weekday-SDon-097400_MISC_780`
- `9756` M12: ours `C6-Weekday-SDon-094500_M12_101` vs MTA `C6-Weekday-SDon-099000_M12_101`

## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| ALL | 223148 | -6 | -3 | 45 | -64 / +50 | 81% | 97% | 99% |
| 0-5 min | 33118 | -1 | -2 | 16 | -27 / +23 | 98% | 100% | 100% |
| 5-15 min | 54273 | -5 | -3 | 26 | -42 / +33 | 93% | 100% | 100% |
| 15-30 min | 59864 | -9 | -6 | 40 | -61 / +47 | 83% | 98% | 99% |
| 30+ min | 75893 | -13 | -1 | 76 | -95 / +102 | 63% | 93% | 97% |

By service class (horizon pooled):

| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |
|---|---|---|---|---|---|---|---|---|
| local | 203656 | -7 | -9 | 35 | -60 / +39 | 85% | 99% | 100% |
| express | 19492 | +16 | +58 | 149 | -147 / +289 | 40% | 75% | 87% |

## Coverage

- stops predicted per TripUpdate (overlapping vehicles): ours median 15 / mean 17.5, MTA median 15 / mean 17.6
- TripUpdates per vehicle: ours mean 1.87, MTA mean 1.87 (MTA >1 = current + upcoming trips)
- routes seen only in OURS (0): —
- routes seen only in MTA (0): —
- avg vehicles/round: ours 3274, MTA 3273, overlap 3228

