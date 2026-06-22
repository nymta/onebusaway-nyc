#!/usr/bin/env bash
# Drive THREE vehicles on three different Manhattan routes at the same time, to show
# per-vehicle predictions diverging. Coordinates are real on-route stops from the STIF.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"
echo "Injecting 3 vehicles concurrently:"
echo "  701 = M1  (DSC 1010, Madison Ave northbound)"
echo "  702 = M15 (DSC 1150, 1 Av northbound)"
echo "  703 = M42 (DSC 1420, 42 St crosstown W->E)"
echo
"$HERE/inject.sh" "MTA NYCT_701" 1010 \
  40.753349,-73.978759 40.755236,-73.977436 40.757739,-73.975556 40.761496,-73.972818 \
  40.762701,-73.971959 40.768812,-73.967483 40.772141,-73.965080 40.773450,-73.964142 &
"$HERE/inject.sh" "MTA NYCT_702" 1150 \
  40.741957,-73.974808 40.744157,-73.973165 40.745324,-73.972349 40.747191,-73.970988 \
  40.749251,-73.969332 40.750684,-73.968460 40.753591,-73.966311 40.755395,-73.964983 &
"$HERE/inject.sh" "MTA NYCT_703" 1420 \
  40.762648,-74.000920 40.760822,-73.998047 40.760542,-73.997928 40.759888,-73.995834 \
  40.759359,-73.995076 40.758670,-73.992979 40.757961,-73.991925 40.757575,-73.990464 &
wait
echo
echo "Injected. Watch decoded predictions with:  $HERE/observe.sh 90"
