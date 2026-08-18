#!/usr/bin/env bash
# Subscribe to the inference engine's inferred-location queue and print decoded beans.
# Usage: observe-inferred.sh [seconds] [capture.ndjson]   (default 900, no capture)
#
# Start this BEFORE replay/replay.sh. It BINDS :5566, because OutputQueueSenderServiceImpl connects rather
# than binds. It is therefore an alternative to the predictions webapp, not an addition - stop-all.sh
# first if the normal loop is up.
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"
exec python3 "$HERE/obs_inferred.py" "$Q_IN" inference_queue "${1:-900}" ${2:+"$2"}
