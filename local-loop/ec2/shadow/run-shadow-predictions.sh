#!/usr/bin/env bash
# The shadow predictions webapp: the arm's run-predictions.sh with its own ports, Mongo database, Maven repository,
# log config and bundle copy, plus the dispatcher-actions flags. Why each is separate: README.md.
set -euo pipefail
source /opt/oba/env-common.sh
S=/data/shadow
export MAVEN_OPTS="-Xmx8g"
cd "$S/onebusaway-nyc-predictions/onebusaway-nyc-predictions-webapp"
exec mvn -B -o -Dmaven.repo.local="$S/m2" -DskipTests -Dlicense.skip=true \
  -Dbundle.location="$S/bundle" -Dbundle.mode.standalone=true -Dtdm.host= \
  -Dmongohost=localhost -Dmongoport=27017 -Dmongouser= -Dmongopwd= \
  -Dmongodbname=onebusaway-nyc-predictions-shadow \
  -DCloudWatchKey=- -DCloudWatchSecret=- \
  -Dlog4j.configurationFile="$S/log4j2-shadow.xml" \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Doba.config.predictions.PredictionLevel=NEXT_TRIP \
  -Djetty.http.port=8092 \
  -Dpredictions.gtfsQueuePort=5578 -Dpredictions.latticeQueuePort=5579 -Dpredictions.debugQueuePort=5580 \
  -Dadjustments.source=bustrek,yardtrek \
  -Dadjustments.yardtrek.roleArn=arn:aws:iam::032610139471:role/ds-bustrek-yardtrek-s3-read \
  -Dadjustments.timepointsFile="$S/TimePoints.csv" \
  -Dadjustments.skipSpansFile="$S/spans/skip-spans.csv" \
  -Dadjustments.dwellClear=true \
  -Dadjustments.cancel.corroboratedWaitMinutes=0 \
  "$JETTY"
