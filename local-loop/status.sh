#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"
echo "== OBA-NYC local loop status =="
docker exec "$MONGO_NAME" mongo --quiet --eval 'db.runCommand({ping:1}).ok' 2>/dev/null | grep -q 1 \
  && echo "  mongo        : UP   (docker $MONGO_NAME :27017)" || echo "  mongo        : DOWN"
port_up "$IE_PORT"   && echo "  inference    : UP   http://localhost:$IE_PORT/ (vehicle-locations.do -> $(http_code http://localhost:$IE_PORT/vehicle-locations.do))" || echo "  inference    : DOWN"
port_up "$PRED_PORT" && echo "  predictions  : UP   http://localhost:$PRED_PORT/api/" || echo "  predictions  : DOWN"
port_up "$Q_IN"      && echo "  queue $Q_IN   : bound (inferred locations,  topic inference_queue)" || echo "  queue $Q_IN   : down"
port_up "$Q_OUT"     && echo "  queue $Q_OUT   : bound (GTFS-RT predictions, topic time)" || echo "  queue $Q_OUT   : down"
grep -q "New bundle is now ready" "$IE_LOG"   2>/dev/null && echo "  ie bundle    : loaded" || echo "  ie bundle    : (not ready)"
grep -q "time prediction input queue listening" "$IE_LOG" 2>/dev/null && echo "  loop-back    : inference consuming predictions from :$Q_OUT" || echo "  loop-back    : (time consumer not yet listening)"
