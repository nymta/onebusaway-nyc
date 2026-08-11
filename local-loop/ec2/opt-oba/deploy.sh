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
    # Defaults must track what is deployed, or a ref-less deploy switches branches under the
    # running service. Both repos deploy from ds/ec2-deploy.
    REF="${2:-ds/ec2-deploy}"
    PRED_REF="${3:-ds/ec2-deploy}"
    echo "== git pull (main ref=$REF, predictions ref=$PRED_REF) =="
    # Abort on a failed checkout/pull. Without this the script carries on and rebuilds, installs
    # and restarts from whatever tree the host happened to be on -- a silent deploy of the wrong code.
    run_oba "cd /opt/oba/onebusaway-nyc && GIT_SSH_COMMAND='ssh -F ~/.ssh/config' git fetch -q --all && git checkout -q '$REF' && git pull -q --ff-only && echo main @ \$(git rev-parse --short HEAD)" \
      || { echo "ERROR: main repo could not check out '$REF' -- aborting before rebuild"; exit 1; }
    # Checkout, not just pull: otherwise this repo stays on whatever branch the host clone is on.
    run_oba "cd /opt/oba/onebusaway-nyc-predictions && GIT_SSH_COMMAND='ssh -F ~/.ssh/config' git fetch -q --all && git checkout -q '$PRED_REF' && git pull -q --ff-only && echo pred @ \$(git rev-parse --short HEAD)" \
      || { echo "ERROR: predictions repo could not check out '$PRED_REF' -- aborting before rebuild"; exit 1; }
    echo "== rebuild (broker + 3 webapps) =="
    # -pl without -am builds only the modules named here, so every changed module must be listed
    # or the webapp links a stale jar from ~/.m2.
    run_oba "export JAVA_HOME=$JH PATH=$JH/bin:\$PATH MAVEN_OPTS=-Xmx4g; cd /opt/oba/onebusaway-nyc && mvn -B -q -pl onebusaway-nyc-tdm-adapters,onebusaway-nyc-queue-broker,onebusaway-nyc-vehicle-tracking,onebusaway-nyc-vehicle-tracking-webapp,onebusaway-nyc-gtfsrt,onebusaway-nyc-gtfsrt-webapp -DskipTests -Dlicense.skip=true install"
    # Same trap: without predictions-common, a stale jar missing a class the Spring context
    # references stops predictions from starting at all.
    run_oba "export JAVA_HOME=$JH PATH=$JH/bin:\$PATH MAVEN_OPTS=-Xmx4g; cd /opt/oba/onebusaway-nyc-predictions && mvn -B -q -pl onebusaway-nyc-predictions-common,onebusaway-nyc-predictions-webapp -DskipTests -Dlicense.skip=true install"
    echo "== install host scripts =="
    SRC=/opt/oba/onebusaway-nyc/local-loop/ec2
    for f in env-common.sh run-broker.sh run-inference.sh run-predictions.sh run-gtfsrt.sh deploy.sh monitor.sh set-weights.sh; do
      install -m 755 "$SRC/opt-oba/$f" "/opt/oba/$f"
    done
    install -m 644 "$SRC/opt-oba/log4j2-predictions.xml" /opt/oba/log4j2-predictions.xml
    echo "== install predictions archiver =="
    install -m 755 "$SRC/opt-oba/predictions-archiver.py" /opt/oba/predictions-archiver.py
    install -m 755 "$SRC/opt-oba/run-predictions-archiver.sh" /opt/oba/run-predictions-archiver.sh
    install -m 644 "$SRC/systemd/oba-predictions-archiver.service" /etc/systemd/system/oba-predictions-archiver.service
    echo "== predictions archiver host prereqs =="
    mkdir -p /data/predictions-archive
    chown oba:oba /data/predictions-archive
    if ! runuser -u oba -- python3 -m pip --version >/dev/null 2>&1; then
      dnf install -y python3-pip
    fi
    runuser -u oba -- python3 -m pip install --user -q pyzmq gtfs-realtime-bindings protobuf 2>/dev/null || \
      python3 -m pip install -q pyzmq gtfs-realtime-bindings protobuf
    systemctl daemon-reload
    systemctl enable oba-predictions-archiver.service
    echo "== restart services =="
    systemctl restart oba-broker oba-inference oba-predictions oba-gtfsrt oba-predictions-archiver
    sleep 20
    echo "== status =="; systemctl is-active oba-broker oba-inference oba-predictions oba-gtfsrt oba-predictions-archiver || true
    echo "== smoke (best-effort; apps may still be warming) =="
    curl -s -o /dev/null -w 'gtfsrt /tripUpdates http=%{http_code}\n' 'http://localhost:8083/tripUpdates?debug=true' || true
    ;;
  *) echo "usage: deploy.sh [deploy [<main-ref> [<predictions-ref>]] | set-weights S H R]"; exit 2;;
esac
