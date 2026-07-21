#!/usr/bin/env bash
# Read desired weights from SSM (S/H/R) and POST /api/weight, retrying until predictions is ready.
source /opt/oba/env-common.sh
W=$(gp /oba/predictions/weights); W=${W:-20/40/40}
S=${W%%/*}; rest=${W#*/}; H=${rest%%/*}; R=${rest##*/}
echo "target weights SCHEDULE=$S HISTORICAL=$H RECENT=$R"
for i in $(seq 1 120); do
  code=$(curl -s -o /tmp/wt.out -w '%{http_code}' -X POST \
    "http://localhost:8082/api/weight?SCHEDULE=$S&HISTORICAL=$H&RECENT=$R" || echo 000)
  if [ "$code" = "200" ]; then echo "applied ($code): $(cat /tmp/wt.out)"; exit 0; fi
  sleep 5
done
echo "WARN: could not apply weights after retries (last code=$code)"; exit 0
