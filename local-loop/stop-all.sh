#!/usr/bin/env bash
# Stop the webapps (by listening port). Pass --mongo to also remove the Mongo container.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"
for p in "$IE_PORT" "$PRED_PORT"; do
  pids=$(lsof -ti:"$p" 2>/dev/null || true)
  [ -n "$pids" ] && { echo "killing :$p ($pids)"; kill -9 $pids 2>/dev/null || true; }
done
pkill -f "jetty-maven-plugin.*vehicle-tracking-webapp" 2>/dev/null || true
pkill -f "jetty-maven-plugin.*predictions-webapp" 2>/dev/null || true
if [ "${1:-}" = "--mongo" ]; then docker rm -f "$MONGO_NAME" >/dev/null 2>&1 && echo "mongo removed"; fi
echo "stopped (mongo left running unless --mongo)."
