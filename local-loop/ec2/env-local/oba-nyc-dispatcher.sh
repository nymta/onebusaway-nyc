# /opt/oba/env-local.sh  --  oba-nyc-dispatcher  (instance id: not created yet)
#
# The dispatcher-actions instance (archive prefix v7-dispatcher-actions): oba-nyc-filtered's input and settings, with
# the predictions engine's dispatcher adjustments switched on. oba-nyc-filtered stays as its control, so the
# engine is the only difference between the two. Both repos run their timothy/dispatcher-actions branch here.
export OBA_DEADBAND_ENABLED=false
export OBA_RMQ_STREAM_NAME=nyct.bustech.gps-filtered
export OBA_ARCHIVER_S3_PREFIX=v7-dispatcher-actions

# The publisher belongs to oba-nyc-prod alone (it holds the allowlisted Elastic IP).
export OBA_CSPUB_ENABLED=0

# Dispatcher adjustments: live Bus Trek and Yard Trek snapshots, the dwell clear on, and a pre-start Bus Trek
# Open Trip/Run cancelled at the scheduled start when Yard Trek also shows the block open. Everything else is the
# engine's defaults (Bus Trek Dark To 100 m with a 5 min wait, Dark To from Depot and Yard Trek open blocks held,
# cancels after 5 min). TimePoints.csv is the bundle's outputs/TimePoints.csv, copied to /data once.
export OBA_PREDICTIONS_EXTRA_OPTS="-Dadjustments.source=bustrek,yardtrek \
 -Dadjustments.yardtrek.roleArn=arn:aws:iam::032610139471:role/ds-bustrek-yardtrek-s3-read \
 -Dadjustments.timepointsFile=/data/TimePoints.csv \
 -Dadjustments.skipSpansFile=/data/adjustments/skip-spans.csv \
 -Dadjustments.dwellClear=true \
 -Dadjustments.cancel.corroboratedWaitMinutes=0"
