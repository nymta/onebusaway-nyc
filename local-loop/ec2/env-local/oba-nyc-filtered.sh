# /opt/oba/env-local.sh  --  oba-nyc-filtered  (i-0d78f39b83d961f5c)
#
# The 28 s input-parity instance. It consumes BusTech's already-filtered queue, so any ingestion
# deadband here would double-filter a stream that is filtered upstream -- hence OFF, unlike
# oba-nyc-prod and oba-nyc-fused-gps which keep theirs.
export OBA_DEADBAND_ENABLED=false
export OBA_RMQ_STREAM_NAME=nyct.bustech.gps-filtered
export OBA_ARCHIVER_S3_PREFIX=v3-filtered

# The publisher belongs to oba-nyc-prod alone (it holds the allowlisted Elastic IP).
export OBA_CSPUB_ENABLED=0
