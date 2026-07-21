#!/usr/bin/env bash
set -euo pipefail
source /opt/oba/env-common.sh
export MAVEN_OPTS="-Xmx6g"
mkdir -p /data/oba/gtfsrt-appdb
cd "$MAIN/onebusaway-nyc-gtfsrt-webapp"
exec mvn -B -P local-ie-testing -DskipTests -Dlicense.skip=true \
  -Dbundle.location="$BUNDLE" -Dbundle.mode.standalone=true -Dtdm.host= \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Djetty.http.port=8083 \
  "$JETTY"
