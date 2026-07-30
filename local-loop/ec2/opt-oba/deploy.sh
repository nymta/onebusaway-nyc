#!/usr/bin/env bash
# Invoked by the GitHub Action via SSM (runs as root). Modes: deploy | set-weights.
set -uo pipefail
export AWS_DEFAULT_REGION=us-east-1
JH=/usr/lib/jvm/java-11-amazon-corretto
ACTION="${1:-deploy}"
run_oba(){ runuser -u oba -- bash -lc "$1"; }

case "$ACTION" in
  set-weights)
    S="${2:?}"; H="${3:?}"; R="${4:?}"
    if [ "$((S+H+R))" -ne 100 ]; then echo "ERROR weights must sum to 100 (got $S/$H/$R)"; exit 1; fi
    aws ssm put-parameter --name /oba/predictions/weights --type String --overwrite --value "$S/$H/$R" >/dev/null
    code=$(curl -s -o /tmp/wt.out -w '%{http_code}' -X POST "http://localhost:8082/api/weight?SCHEDULE=$S&HISTORICAL=$H&RECENT=$R" || echo 000)
    echo "set-weights $S/$H/$R -> http $code: $(cat /tmp/wt.out 2>/dev/null)"
    [ "$code" = "200" ]
    ;;
  deploy)
    # Defaults track what is actually deployed. They used to point at a branch the host was not on, so
    # invoking without an explicit ref silently switched branches.
    REF="${2:-timothy/ec2-oba-fixes}"
    PRED_REF="${3:-timothy/ec2-oba-config-fixes}"
    echo "== git pull (main ref=$REF, predictions ref=$PRED_REF) =="
    run_oba "cd /opt/oba/onebusaway-nyc && GIT_SSH_COMMAND='ssh -F ~/.ssh/config' git fetch -q --all && git checkout -q '$REF' && git pull -q --ff-only && echo main @ \$(git rev-parse --short HEAD)"
    # The predictions repo needs a checkout too, not just a pull: it was pinned to whatever branch the
    # host clone happened to be on, so a change pushed to any other branch would never be deployed.
    run_oba "cd /opt/oba/onebusaway-nyc-predictions && GIT_SSH_COMMAND='ssh -F ~/.ssh/config' git fetch -q --all && git checkout -q '$PRED_REF' && git pull -q --ff-only && echo pred @ \$(git rev-parse --short HEAD)"
    echo "== rebuild (broker + 3 webapps) =="
    # onebusaway-nyc-gtfsrt (the feed-building library) must be listed explicitly: there is no -am here,
    # so a change to it would otherwise not be rebuilt and the webapp would link the stale jar from ~/.m2.
    run_oba "export JAVA_HOME=$JH PATH=$JH/bin:\$PATH MAVEN_OPTS=-Xmx4g; cd /opt/oba/onebusaway-nyc && mvn -B -q -pl onebusaway-nyc-queue-broker,onebusaway-nyc-vehicle-tracking,onebusaway-nyc-vehicle-tracking-webapp,onebusaway-nyc-gtfsrt,onebusaway-nyc-gtfsrt-webapp -DskipTests -Dlicense.skip=true install"
    run_oba "export JAVA_HOME=$JH PATH=$JH/bin:\$PATH MAVEN_OPTS=-Xmx4g; cd /opt/oba/onebusaway-nyc-predictions && mvn -B -q -pl onebusaway-nyc-predictions-webapp -DskipTests -Dlicense.skip=true install"
    echo "== restart services =="
    systemctl restart oba-broker oba-inference oba-predictions oba-gtfsrt
    sleep 20
    echo "== status =="; systemctl is-active oba-broker oba-inference oba-predictions oba-gtfsrt || true
    echo "== smoke (best-effort; apps may still be warming) =="
    curl -s -o /dev/null -w 'gtfsrt /tripUpdates http=%{http_code}\n' 'http://localhost:8083/tripUpdates?debug=true' || true
    ;;
  *) echo "usage: deploy.sh [deploy [<main-ref> [<predictions-ref>]] | set-weights S H R]"; exit 2;;
esac
