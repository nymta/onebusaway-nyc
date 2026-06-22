#!/usr/bin/env bash
# Stop the RabbitMQ -> ZeroMQ bridge.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/../env.sh"
pids=$(lsof -ti:"$IE_INPUT_PORT" 2>/dev/null || true)
[ -n "$pids" ] && { echo "killing bridge :$IE_INPUT_PORT ($pids)"; kill $pids 2>/dev/null || true; sleep 1; }
pkill -f "[b]ridge.py" 2>/dev/null || true
echo "bridge: stopped."
