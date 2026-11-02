# /opt/oba/env-local.sh  --  oba-nyc-cancel
#
# 30 s BusTech queue with no deadband (same input as oba-nyc-filtered), CS prod historicals loaded into
# Mongo at launch, and CANCELED trip updates for scheduled trips with no matched vehicle.
export OBA_DEADBAND_ENABLED=false
export OBA_RMQ_STREAM_NAME=nyct.bustech.gps-filtered
export OBA_ARCHIVER_S3_PREFIX=v6-cancel
export OBA_GTFSRT_CANCEL_UNMATCHED=true

# The publisher belongs to oba-nyc-prod alone (it holds the allowlisted Elastic IP).
export OBA_CSPUB_ENABLED=0
