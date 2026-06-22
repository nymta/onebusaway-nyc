#!/usr/bin/env bash
# Start the RabbitMQ -> ZeroMQ bridge DETACHED (survives the parent shell).
# Forwards the BusTech stream onto tcp://*:$IE_INPUT_PORT for the inference engine to consume.
# Requires mq_env.sh (cp from mq_env.sh.example). Idempotent: skips if already bound.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/../env.sh"
[ -f "$HERE/mq_env.sh" ] || { echo "missing $HERE/mq_env.sh — cp mq_env.sh.example mq_env.sh and fill it in"; exit 2; }
source "$HERE/mq_env.sh"

if port_up "$IE_INPUT_PORT"; then
  echo "bridge: already up (PUB bound :$IE_INPUT_PORT)"; exit 0
fi
python3 -c 'import pika, zmq, certifi' 2>/dev/null || python3 -m pip install --user --quiet pika pyzmq certifi

echo "bridge: starting (stream '$OBA_MQ_STREAM' -> ZMQ PUB :$IE_INPUT_PORT topic '$IE_INPUT_TOPIC')..."
( cd "$HERE" && nohup python3 -u bridge.py > "$BRIDGE_LOG" 2>&1 < /dev/null & disown ) 2>/dev/null
wait_for "bridge PUB bind" 20 "port_up $IE_INPUT_PORT" || { echo "  see $BRIDGE_LOG"; exit 1; }
sleep 1; tail -n 4 "$BRIDGE_LOG" 2>/dev/null
echo "bridge: up. (inference must run the REAL listener with input keys seeded — see RUNBOOK 'Live RabbitMQ feed')"
