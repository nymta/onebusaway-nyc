#!/usr/bin/env bash
set -euo pipefail
source /opt/oba/env-common.sh
export MAVEN_OPTS="-Xmx10g"
cd "$PRED/onebusaway-nyc-predictions-webapp"
# predictions.* overrides, read by SystemPropertyConfigurationServiceImpl.
# We set PredictionLevel to NEXT_TRIP to match the config set by Prod TDM config.
exec mvn -B -DskipTests -Dlicense.skip=true \
  -Dbundle.location="$BUNDLE" -Dbundle.mode.standalone=true -Dtdm.host= \
  -Dmongohost=localhost -Dmongoport=27017 -Dmongouser= -Dmongopwd= \
  -DCloudWatchKey=x -DCloudWatchSecret=x \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Doba.config.predictions.PredictionLevel=NEXT_TRIP \
  -Djetty.http.port=8082 \
  "$JETTY"
