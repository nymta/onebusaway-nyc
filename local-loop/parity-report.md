# GTFS-RT ↔ public-GTFS trip_id parity report

- feed base: `http://ec2-52-70-255-34.compute-1.amazonaws.com`
- GTFS zips (6): GTFS_MTABC_C6_REG_06282026.zip, GTFS_SURFACE_BX_C6_REG_06282026_06052026.zip, GTFS_SURFACE_B_C6_REG_06282026_06052026.zip, GTFS_SURFACE_M_C6_REG_06282026_06052026.zip, GTFS_SURFACE_Q_C6_REG_06282026_06052026.zip, GTFS_SURFACE_S_C6_REG_06282026_06052026.zip
- feed entities sampled: 4665

| id type | in feed | matched in GTFS | missing | coverage |
|---|---|---|---|---|
| trip_id | 2288 | 2288 | 0 | **100.0%** |
| route_id | 235 | 235 | 0 | **100.0%** |
| stop_id | 9974 | 9974 | 0 | **100.0%** |
| vehicle_id | 2363 | n/a (not in static GTFS) | | |

**trip_id → route_id consistency:** 2288 of 2288 feed trips are in GTFS with a matching route_id; 0 mismatches.

## VERDICT: PASS
- Feed sample: vehicle_id form e.g. `MTA NYCT_1001`
- trip_id form e.g. `CA_C6-Weekday-SDon-081000_MISC_305`
