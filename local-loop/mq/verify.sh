#!/usr/bin/env bash
# Connect to the RabbitMQ stream and verify the payload format.
#   ./verify.sh [-n 20] [--offset last]
# Requires mq_env.sh (copy from mq_env.sh.example and fill in). Read-only.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/mq_env.sh" ] || { echo "missing $HERE/mq_env.sh — cp mq_env.sh.example mq_env.sh and fill it in"; exit 2; }
source "$HERE/mq_env.sh"
python3 -c 'import pika, certifi' 2>/dev/null || python3 -m pip install --user --quiet pika certifi
exec python3 "$HERE/verify_stream.py" "$@"
