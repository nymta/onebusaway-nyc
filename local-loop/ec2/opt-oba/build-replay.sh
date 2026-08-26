#!/usr/bin/env bash
# Rebuilds the modules run-replay.sh actually needs, with the right JDK on PATH automatically -
# env-common.sh sets JAVA_HOME, so nothing here has to be remembered/exported by hand before a build.
set -uo pipefail
source /opt/oba/env-common.sh
export MAVEN_OPTS="${MAVEN_OPTS:--Xmx4g}"

cd "$MAIN"
mvn -B -pl onebusaway-nyc-util,onebusaway-nyc-transit-data-federation,onebusaway-nyc-tdm-adapters,onebusaway-nyc-vehicle-tracking,onebusaway-nyc-vehicle-tracking-webapp \
  -DskipTests -Dlicense.skip=true install
