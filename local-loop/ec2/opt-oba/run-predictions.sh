#!/usr/bin/env bash
set -euo pipefail
source /opt/oba/env-common.sh
export MAVEN_OPTS="-Xmx10g"
cd "$PRED/onebusaway-nyc-predictions-webapp"
exec mvn -B -DskipTests -Dlicense.skip=true \
  -Dbundle.location="$BUNDLE" -Dbundle.mode.standalone=true -Dtdm.host= \
  -Dmongohost=localhost -Dmongoport=27017 -Dmongouser= -Dmongopwd= \
  -DCloudWatchKey=x -DCloudWatchSecret=x \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Djetty.http.port=8082 \
  "$JETTY"
