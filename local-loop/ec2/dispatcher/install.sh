#!/usr/bin/env bash
# install.sh: give a quarantined clone of oba-nyc-filtered the identity of the dispatcher-actions instance (run as
# root on the new instance, once). The files of this directory and env-local/oba-nyc-dispatcher.sh come from the S3
# deploy prefix. It starts nothing: the deploy that follows builds both repos and starts the chain.
set -euo pipefail
P=s3://ds-oba/replay/prediction/dispatcher-actions/live/v7-dispatcher-actions
[ -f /opt/oba/CLONE-QUARANTINED ] || { echo "no /opt/oba/CLONE-QUARANTINED: is this the quarantined clone?" >&2; exit 1; }
if systemctl is-active -q oba-predictions || systemctl is-active -q oba-inference; then echo "oba units are running; refusing" >&2; exit 1; fi
D=/opt/oba/dispatcher; mkdir -p $D /data/adjustments/logs
aws s3 cp $P/deploy/ $D/ --recursive --no-progress --only-show-errors
install -o oba -g oba -m 644 $D/oba-nyc-dispatcher.sh /opt/oba/env-local.sh
install -m 644 $D/log4j2-predictions-dispatcher.xml /opt/oba/log4j2-predictions-dispatcher.xml
install -m 755 $D/adjustments-sync.sh /opt/oba/adjustments-sync.sh
install -m 644 $D/oba-adjustments-sync.service $D/oba-adjustments-sync.timer /etc/systemd/system/
# the timepoint table the engine resolves Dark To ranges with: the one the shadow used, else the S3 copy
if [ ! -s /data/TimePoints.csv ]; then
  if [ -s /data/shadow/TimePoints.csv ]; then cp /data/shadow/TimePoints.csv /data/TimePoints.csv
  else aws s3 cp s3://ds-oba/replay/prediction/dispatcher-actions/TimePoints.csv /data/TimePoints.csv --no-progress --only-show-errors; fi
fi
chown -R oba:oba /data/adjustments /data/TimePoints.csv
# units that belong to oba-nyc-filtered alone must never run here
for u in oba-shadow-predictions oba-shadow-archiver oba-shadow-sync.timer oba-shadow-sync oba-bustrek-live-archiver oba-yardtrek-live-archiver oba-cs-gps-publisher; do
  systemctl disable --now $u 2>/dev/null || true
done
systemctl daemon-reload
systemctl enable -q oba-broker oba-inference oba-predictions oba-gtfsrt oba-predictions-archiver oba-inference-archiver oba-weights oba-monitor.timer oba-adjustments-sync.timer
echo "identity installed: $(grep -c '^export' /opt/oba/env-local.sh) exports. Next: /opt/oba/deploy.sh deploy timothy/dispatcher-actions timothy/dispatcher-actions"
