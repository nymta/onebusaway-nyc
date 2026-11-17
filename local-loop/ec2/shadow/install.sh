#!/usr/bin/env bash
# install.sh <commit>: build the shadow engine at <commit> under /data/shadow and start it (run as root on the arm).
# The other files of this directory come from the S3 deploy prefix; re-running rebuilds at the new commit and restarts.
set -euo pipefail
COMMIT=${1:?commit}
S=/data/shadow; P=s3://ds-oba/replay/prediction/dispatcher-actions/shadow/oba-nyc-filtered
for port in 5578 5579 5580 8092; do
  if ss -ltn | awk '{print $4}' | grep -q ":$port\$" && ! systemctl is-active -q oba-shadow-predictions; then
    echo "port $port in use by something else; refusing" >&2; exit 1
  fi
done
mkdir -p $S/logs $S/spans $S/predictions-archive; chown -R oba:oba $S
aws s3 cp $P/deploy/ $S/ --recursive --no-progress --only-show-errors
[ -s $S/TimePoints.csv ] || aws s3 cp s3://ds-oba/replay/prediction/dispatcher-actions/TimePoints.csv $S/TimePoints.csv --no-progress --only-show-errors
chmod +x $S/*.sh; chown -R oba:oba $S
# its own bundle copy: the arm's webapp holds the lock on the HSQLDB file in its bundle directory
if [ ! -d $S/bundle ]; then
  rsync -a --exclude '*.lck' --exclude '*.tmp' /data/oba-bundle/ $S/bundle/ && chown -R oba:oba $S/bundle
fi
H=$(getent passwd oba | cut -d: -f6)
sudo -u oba bash -c "
set -euo pipefail
cd $S
[ -d m2 ] || cp -a $H/.m2/repository m2
[ -d onebusaway-nyc-predictions ] || git clone -q /opt/oba/onebusaway-nyc-predictions onebusaway-nyc-predictions
cd onebusaway-nyc-predictions
git remote set-url origin \$(git -C /opt/oba/onebusaway-nyc-predictions remote get-url origin)
GIT_SSH_COMMAND='ssh -F $H/.ssh/config' git fetch -q origin timothy/dispatcher-actions
git checkout -q -B shadow $COMMIT
git rev-parse HEAD > $S/COMMIT
export JAVA_HOME=/usr/lib/jvm/java-11-amazon-corretto PATH=/usr/lib/jvm/java-11-amazon-corretto/bin:\$PATH MAVEN_OPTS=-Xmx4g
mvn -B -q -Dmaven.repo.local=$S/m2 -pl onebusaway-nyc-predictions-webapp -am -DskipTests -Dlicense.skip=true install
"
cp $S/oba-shadow-*.service $S/oba-shadow-sync.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q oba-shadow-predictions oba-shadow-archiver oba-shadow-sync.timer
systemctl restart oba-shadow-predictions; sleep 5; systemctl restart oba-shadow-archiver; systemctl start oba-shadow-sync.timer
echo "shadow at $(cat $S/COMMIT) started"
