# /opt/oba/env-local.sh  --  oba-nyc-fused-gps  (i-0a45277b7b11ea8be)
#
# The fused-GPS instance. Was oba-nyc-cadence30 (26 s deadband on nyct.bustech.gps, prefix
# v2-26s-deadband) until the 2026-09-01 cutover; v2-26s-deadband keeps its history but takes
# no new uploads.

# Deadband pinned to oba-nyc-prod's values so the ONLY variable against it is the input feed.
# Set explicitly rather than inherited from the branch defaults, so a later change to those
# defaults cannot move two variables at once mid-experiment.
export OBA_DEADBAND_MIN_METERS=10
export OBA_DEADBAND_MIN_INTERVAL_SEC=7
export OBA_DEADBAND_MAX_AGE_SEC=30

# Input feed: fused GPS. A stream at ~747 msg/s, full rate -- NOT pre-decimated, so the deadband
# above does not double-filter (unlike oba-nyc-filtered). Requires the parametrized
# run-inference.sh from ds/ec2-deploy; on the retired timothy/ec2-30s script this line was inert.
export OBA_RMQ_STREAM_NAME=nyct.bus.fused-gps

export OBA_ARCHIVER_S3_PREFIX=v4-fused-gps

# The publisher belongs to oba-nyc-prod alone (it holds the allowlisted Elastic IP).
export OBA_CSPUB_ENABLED=0
