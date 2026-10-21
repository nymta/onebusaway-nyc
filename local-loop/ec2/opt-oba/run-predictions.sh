#!/usr/bin/env bash
set -euo pipefail
source /opt/oba/env-common.sh
export MAVEN_OPTS="-Xmx10g"
cd "$PRED/onebusaway-nyc-predictions-webapp"
# Per-host options from env-local.sh; unset = not passed.
OPT=()
if [ -n "${OBA_COMPONENT_CAPTURE_FILE-}" ]; then
  OPT+=(-Dpredictions.componentCapture.outputFile="$OBA_COMPONENT_CAPTURE_FILE"
        -Dpredictions.componentCapture.overwriteOutputFile=false)
fi
if [ -n "${OBA_BIAS_TABLE_PATH-}" ]; then
  OPT+=(-Doba.config.predictions.biasCorrection.tablePath="$OBA_BIAS_TABLE_PATH")
  [ -n "${OBA_BIAS_MAX_CORRECTION_S-}" ] && OPT+=(-Doba.config.predictions.biasCorrection.maxCorrectionSeconds="$OBA_BIAS_MAX_CORRECTION_S")
  [ -n "${OBA_BIAS_MIN_HORIZON_S-}" ] && OPT+=(-Doba.config.predictions.biasCorrection.minHorizonSeconds="$OBA_BIAS_MIN_HORIZON_S")
fi
# predictions.* overrides, read by SystemPropertyConfigurationServiceImpl.
# We set PredictionLevel to NEXT_TRIP to match the config set by Prod TDM config.
exec mvn -B -DskipTests -Dlicense.skip=true \
  -Dbundle.location="$BUNDLE" -Dbundle.mode.standalone=true -Dtdm.host= \
  -Dmongohost=localhost -Dmongoport=27017 -Dmongouser= -Dmongopwd= \
  -DCloudWatchKey="$(gp /oba/uts/s3/accessKey)" \
  -DCloudWatchSecret="$(gp /oba/uts/s3/secretKey)" \
  -Dlog4j.configurationFile=/opt/oba/log4j2-predictions.xml \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Doba.config.predictions.PredictionLevel=NEXT_TRIP \
  -Djetty.http.port=8082 \
  ${OPT[@]+"${OPT[@]}"} \
  "$JETTY"
