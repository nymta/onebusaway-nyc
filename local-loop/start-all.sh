#!/usr/bin/env bash
# Bring up the full broker-less loop: Mongo + predictions webapp + inference webapp.
# Webapps are launched DETACHED (setsid) so they survive the parent shell exiting.
# Idempotent: skips anything already up.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"

ensure_mongo || exit 1

# --- predictions webapp: BINDS Q_IN (SUB) and Q_OUT (PUB) ---
if port_up "$Q_IN" && port_up "$Q_OUT"; then
  echo "predictions: already up (queues $Q_IN/$Q_OUT bound)"
else
  echo "predictions: starting (Jetty 9 :$PRED_PORT, Mongo, Manhattan bundle)..."
  ( cd "$PRED_REPO" && nohup "$MVN" -f onebusaway-nyc-predictions-webapp/pom.xml \
      -DskipTests -Dlicense.skip=true -B \
      -Dbundle.location="$BUNDLE_PARENT" -Dbundle.mode.standalone=true -Dtdm.host= \
      -Dmongohost=localhost -Dmongoport=27017 -Dmongouser= -Dmongopwd= \
      -DCloudWatchKey=x -DCloudWatchSecret=x \
      -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
      -Djetty.http.port="$PRED_PORT" \
      "$JETTY":run > "$PRED_LOG" 2>&1 < /dev/null & disown ) 2>/dev/null
  wait_for "predictions queues ($Q_IN,$Q_OUT)" 420 "port_up $Q_IN && port_up $Q_OUT" \
    || { echo "  see $PRED_LOG"; exit 1; }
fi

# --- inference webapp: CONNECTS Q_IN (PUB out) and Q_OUT (SUB, loop-back into TDS) ---
if port_up "$IE_PORT"; then
  echo "inference: already up (:$IE_PORT)"
else
  echo "inference: starting (Jetty 9 :$IE_PORT, real output queue + time-prediction loop-back)..."
  ( cd "$MAIN_REPO" && nohup "$MVN" -f onebusaway-nyc-vehicle-tracking-webapp/pom.xml \
      -P local-ie-testing -DskipTests -B \
      -Die.output.queue=OutputQueueSenderServiceImpl \
      -DtimePredictions.status=ENABLED \
      -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
      -Dbundle.location="$BUNDLE_PARENT" \
      -Djetty.http.port="$IE_PORT" \
      "$JETTY":run > "$IE_LOG" 2>&1 < /dev/null & disown ) 2>/dev/null
  wait_for "inference bundle" 420 'grep -q "New bundle is now ready" "$IE_LOG"' \
    || { echo "  see $IE_LOG"; exit 1; }
fi

echo; "$HERE/status.sh"
echo; echo "Loop is up. Inject with:  $HERE/inject-multi.sh   (watch with: $HERE/observe.sh)"
