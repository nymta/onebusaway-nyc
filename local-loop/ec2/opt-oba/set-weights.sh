#!/usr/bin/env bash
# Read desired weights and POST /api/weight, retrying until predictions is ready.
# OBA_PREDICTIONS_WEIGHTS (env-local.sh, "S/H/R" or "S/H/R/T") overrides the shared
# SSM value, so a per-instance experiment doesn't move the fleet's shared weights.
source /opt/oba/env-common.sh
W=${OBA_PREDICTIONS_WEIGHTS:-$(gp /oba/predictions/weights)}; W=${W:-20/40/40}
IFS=/ read -r S H R T <<< "$W"; T=${T:-0}
echo "target weights SCHEDULE=$S HISTORICAL=$H RECENT=$R TRAFFIC=$T"
for i in $(seq 1 120); do
  code=$(curl -s -o /tmp/wt.out -w '%{http_code}' -X POST \
    "http://localhost:8082/api/weight?SCHEDULE=$S&HISTORICAL=$H&RECENT=$R&TRAFFIC=$T" || echo 000)
  if [ "$code" = "200" ]; then echo "applied ($code): $(cat /tmp/wt.out)"; exit 0; fi
  sleep 5
done
echo "WARN: could not apply weights after retries (last code=$code)"; exit 0
