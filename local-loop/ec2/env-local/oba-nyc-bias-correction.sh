# /opt/oba/env-local.sh  --  oba-nyc-bias-correction  (i-0a0d51b7c668935d1)
#
# Bias-correction arm. Input and deadband match oba-nyc-filtered, so the bias correction table is
# the only difference from that box. Needs predictions ref marcos/bias-correction.
export OBA_DEADBAND_ENABLED=false
export OBA_RMQ_STREAM_NAME=nyct.bustech.gps-filtered
export OBA_ARCHIVER_S3_PREFIX=v6-bias-correction

# Table from s3://ds-oba/bias-map/runs/<run>/bias_correction_table.csv. Unset = no correction.
# Max correction (300 s) and min horizon (180 s) use the engine defaults; OBA_BIAS_MAX_CORRECTION_S
# and OBA_BIAS_MIN_HORIZON_S override them.
export OBA_BIAS_TABLE_PATH=/data/bias-correction/bias_correction_table.csv

# Per-prediction CSV with uncorrected_arrival, bias_correction_ms and the components, uploaded
# every 15 min by oba-component-capture-archiver.timer.
export OBA_COMPONENT_CAPTURE_FILE=/data/predictions-archive/component-capture.csv

# The publisher belongs to oba-nyc-prod alone (it holds the allowlisted Elastic IP).
export OBA_CSPUB_ENABLED=0
