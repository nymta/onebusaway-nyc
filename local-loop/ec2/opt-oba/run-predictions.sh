#!/usr/bin/env bash
set -euo pipefail
source /opt/oba/env-common.sh
export MAVEN_OPTS="-Xmx10g"
cd "$PRED/onebusaway-nyc-predictions-webapp"
# predictions.* config overrides, read by SystemPropertyConfigurationServiceImpl (there is no TDM here).
# NEXT_TRIP makes us publish each bus's next trip as well as its current one, matching production's feed
# shape: production emits ~1.86 prediction sets per bus to our 1.00, and 36% of its stop predictions are
# for trips we do not publish at all.
exec mvn -B -DskipTests -Dlicense.skip=true \
  -Dbundle.location="$BUNDLE" -Dbundle.mode.standalone=true -Dtdm.host= \
  -Dmongohost=localhost -Dmongoport=27017 -Dmongouser= -Dmongopwd= \
  -DCloudWatchKey=x -DCloudWatchSecret=x \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Doba.config.predictions.PredictionLevel=NEXT_TRIP \
  -Djetty.http.port=8082 \
  "$JETTY"
