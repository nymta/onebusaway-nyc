#!/usr/bin/env bash
# Inject a GPS track for ONE vehicle.
#   inject.sh "<vehicleId>" <dsc> <lat,lon> [<lat,lon> ...]
# Sends a sequence of fixes 45s apart in device time (ending ~now), 2s apart in wall-clock,
# so the particle filter converges and the predictions update per fix.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"
VID="$1"; DSC="$2"; shift 2
VID_ENC="${VID// /%20}"; coords=("$@"); n=${#coords[@]}
base=$(date +%s)
for i in "${!coords[@]}"; do
  IFS=',' read -r lat lon <<< "${coords[$i]}"
  ts=$(( base + (i-(n-1))*45 ))
  t=$(TZ=America/New_York date -r "$ts" '+%Y-%m-%d %H:%M:%S'); enc="${t/ /%20}"
  code=$(curl -s -o /dev/null -m 8 -w "%{http_code}" \
    "http://localhost:$IE_PORT/update-vehicle-location.do?vehicleId=$VID_ENC&lat=$lat&lon=$lon&dsc=$DSC&time=$enc")
  printf '  %-16s [%d/%d] %s,%s @ %s -> %s\n' "$VID" "$((i+1))" "$n" "$lat" "$lon" "$t" "$code"
  sleep 2
done
