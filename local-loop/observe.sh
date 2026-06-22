#!/usr/bin/env bash
# Subscribe to the GTFS-RT time-prediction queue and print decoded TripUpdates.
# Usage: observe.sh [seconds]   (default 90)
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"
exec python3 "$HERE/obs_time.py" "$Q_OUT" time "${1:-90}"
