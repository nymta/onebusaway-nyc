#!/usr/bin/env bash
# Launch the quick local GTFS-RT map viewer, then open http://localhost:8090
#   ./view.sh
#   MAPBOX_TOKEN=pk.xxxx ./view.sh      # optional: Mapbox tiles instead of OpenStreetMap
# Ctrl-C to stop. Taps local ZMQ only (no broker creds needed); needs the loop + mq bridge running.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/../env.sh"
[ -f "$HERE/mq_env.sh" ] && source "$HERE/mq_env.sh"   # optional: picks up IE_INPUT_TOPIC/PORT (and MAPBOX_TOKEN if you put it there)
export OBA_Q_OUT="${Q_OUT:-5568}"
# GTFS for stop names/locations + route names (first GTFS*.zip in the bundle inputs; override via OBA_GTFS_ZIP)
export OBA_GTFS_ZIP="${OBA_GTFS_ZIP:-$(ls "$MAIN_REPO"/.context/manhattan-bundle/GTFS*.zip 2>/dev/null | head -1)}"
python3 -c 'import zmq, google.transit.gtfs_realtime_pb2' 2>/dev/null || python3 -m pip install --user --quiet pyzmq gtfs-realtime-bindings
echo "viewer: http://localhost:${VIEWER_PORT:-8090}  (tiles: ${MAPBOX_TOKEN:+Mapbox}${MAPBOX_TOKEN:-OpenStreetMap})"
exec python3 "$HERE/viewer.py"
