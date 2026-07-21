#!/usr/bin/env bash
# Emit OBA/Prod CloudWatch custom metrics. Run by oba-monitor.timer (~every 2 min).
set -uo pipefail
export AWS_DEFAULT_REGION=us-east-1
IID=i-0386b6bb8338b2f67
NS=OBA/Prod
now=$(date +%s)

# services down (systemd units + mongo container + nginx)
down=0
for u in oba-broker oba-inference oba-predictions oba-gtfsrt; do
  [ "$(systemctl is-active "$u" 2>/dev/null)" = "active" ] || down=$((down+1))
done
[ "$(docker inspect -f '{{.State.Running}}' oba-mongo 2>/dev/null)" = "true" ] || down=$((down+1))
[ "$(systemctl is-active nginx 2>/dev/null)" = "active" ] || down=$((down+1))

# feed staleness + entity count (localhost debug text carries header.timestamp)
dbg=$(curl -s --max-time 8 "http://localhost:8083/tripUpdates?debug=true" || true)
ts=$(printf '%s' "$dbg" | awk '/timestamp:/{print $2; exit}')
if [ -n "${ts:-}" ]; then stale=$(( now - ts )); else stale=999999; fi
ents=$(printf '%s' "$dbg" | grep -c "trip_update {")

# /data disk used %
disk=$(df --output=pcent /data 2>/dev/null | tail -1 | tr -dc '0-9'); [ -n "${disk:-}" ] || disk=0

# inference backlog: latest "N outstanding threads not reaped" in the last 6 min
backlog=$(journalctl -u oba-inference --since "-6 min" --no-pager 2>/dev/null \
          | grep -aoE "[0-9]+ outstanding threads not reaped" | tail -1 | grep -oE "^[0-9]+")

put(){ aws cloudwatch put-metric-data --namespace "$NS" --dimensions InstanceId="$IID" \
        --metric-name "$1" --unit "$2" --value "$3" >/dev/null 2>&1 || echo "WARN put $1 failed"; }
put ServicesDown         Count   "$down"
put FeedStalenessSeconds Seconds "$stale"
put TripUpdateEntities   Count   "$ents"
put DataDiskUsedPct      Percent "$disk"
[ -n "${backlog:-}" ] && put InferenceBacklogThreads Count "$backlog"

# stale-fix load-shedding: fixes shed since last run (delta of the cumulative shedStaleTotal counter)
shed_now=$(journalctl -u oba-inference --since "-6 min" --no-pager 2>/dev/null \
           | grep -aoE "shedStaleTotal=[0-9]+" | tail -1 | grep -oE "[0-9]+")
shed_delta="n/a"
if [ -n "${shed_now:-}" ]; then
  shed_prev=$(grep -oE "^[0-9]+" /opt/oba/.shed_last 2>/dev/null || true)
  if [ -n "${shed_prev:-}" ] && [ "$shed_now" -ge "$shed_prev" ]; then shed_delta=$((shed_now - shed_prev)); else shed_delta=$shed_now; fi
  echo "$shed_now" > /opt/oba/.shed_last
  put InferenceShedFixes Count "$shed_delta"
fi

echo "monitor: servicesDown=$down stale=${stale}s entities=$ents disk=${disk}% backlog=${backlog:-n/a} shedSinceLast=$shed_delta"
