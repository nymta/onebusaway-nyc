#!/usr/bin/env bash
set -euo pipefail
source /opt/oba/env-common.sh
export MAVEN_OPTS="-Xmx6g"
mkdir -p /data/oba/gtfsrt-appdb
cd "$MAIN/onebusaway-nyc-gtfsrt-webapp"
# Publication age cut-off, matching the 120 s ceiling of the production feed. Without it a bus stays
# in the feed indefinitely after its last AVL report.
exec mvn -B -P local-ie-testing -DskipTests -Dlicense.skip=true \
  -Dbundle.location="$BUNDLE" -Dbundle.mode.standalone=true -Dtdm.host= \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Doba.feed.maxVehicleAgeSec=120 \
  -Djetty.http.port=8083 \
  "$JETTY"
