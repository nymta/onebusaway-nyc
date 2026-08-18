#!/usr/bin/env bash
# Summarise the last replay from /tmp/replay-ie.log. Run after replay.sh finishes.
#
# The two numbers that matter are the driver's dispatched count and the count of records the filter
# skipped as out of order. They should agree with what the observer printed: dispatched minus skipped
# equals published. A run with 279 dispatched and 255 skipped yielded 24.
set -uo pipefail
LOG="${1:-/tmp/replay-ie.log}"
[ -f "$LOG" ] || { echo "no log at $LOG" >&2; exit 2; }

echo "== driver =="
grep -E "replay: (starting|bundle ready|dispatched|done|engine still busy)" "$LOG" \
  | sed 's/^.*ReplayFileInputTask.java:[0-9]*\] : /  /' || echo "  no replay lines"

echo
echo "== records the filter refused =="
printf "  out-of-order (VehicleInferenceInstance:198) : %s\n" "$(grep -c 'out-of-order record' "$LOG")"
printf "  missing previous observation (:245)         : %s\n" "$(grep -c 'missing previous observation' "$LOG")"

echo
echo "== engine =="
printf "  stripes created   : %s\n" "$(grep -oE 'Creating [0-9]+ single-thread stripes' "$LOG" | tail -1 || echo '-')"
printf "  bundle            : %s\n" "$(grep -oE 'Found local bundle [^ ]+' "$LOG" | tail -1 || echo 'NONE FOUND')"
printf "  output queue      : %s\n" "$(grep -oE 'Inference output queue is sending to .*' "$LOG" | tail -1 || echo '-')"

echo
echo "== failures =="
grep -cE "Could not resolve placeholder|Context initialization failed|No valid and active bundles" "$LOG" \
  | xargs printf "  startup errors    : %s\n"
grep -m3 -E "Could not resolve placeholder|No valid and active bundles" "$LOG" | sed 's/^/    /' || true
