#!/usr/bin/env bash
# Run one fixture twice with per-record tracing on, then report where the two runs first differ.
#
# Usage: replay-trace-diff.sh [fixture.jsonl]     (default: fixture-one.jsonl, one vehicle)
#
# Each traced line carries the record handed to inference, the number of random draws that record
# consumed, and the values inference produced. Comparing them says which stage introduced a
# difference:
#
#   in= differs      -> the problem is upstream of the filter; nothing inside it is worth examining
#   draws= differ    -> something before the draw diverged (candidate count, for example)
#   only out= differs-> same input, same number of draws, different values: the streams are not
#                       seeded as intended, or an order-dependent sum is involved
#
# Single-threaded by default, so thread interleaving is not a variable. Set OBA_INFERENCE_THREADS
# to test the concurrent path.
set -uo pipefail
# HERE is this script's directory; LOOP is the harness root one level up, which holds env.sh.
HERE="$(cd "$(dirname "$0")" && pwd)"
LOOP="$(cd "$HERE/.." && pwd)"; source "$LOOP/env.sh"

FIXTURE="${1:-$MAIN_REPO/.context/replay-sample/fixture-one.jsonl}"
[ -f "$FIXTURE" ] || { echo "no such fixture: $FIXTURE" >&2; exit 2; }

if port_up "$IE_PORT"; then
  echo "inference is up on :$IE_PORT; stop it first" >&2; exit 2
fi

run() {  # $1 = label
  local trace="/tmp/replay-trace-$1.txt" log="/tmp/replay-ie-$1.log"
  rm -f "$trace"
  ( cd "$MAIN_REPO" && "$MVN" -f onebusaway-nyc-vehicle-tracking-webapp/pom.xml \
      -P local-ie-testing -DskipTests -B \
      -Die.listener=ReplayFileInputTask \
      -Dspring.profiles.active=replay \
      -Dreplay.file="$FIXTURE" \
      -Doba.inference.seed="${OBA_INFERENCE_SEED:-20260807}" \
      -Doba.inference.threads="${OBA_INFERENCE_THREADS:-1}" \
      -Doba.inference.trace="$trace" \
      -Doba.inference.trace.callers=true \
      -Dreplay.exitWhenDone=true \
      -Die.output.queue=DummyOutputQueueSenderServiceImpl \
      -DtimePredictions.status=ENABLED \
      -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
      -Dbundle.location="$BUNDLE_PARENT" \
      -Djetty.http.port="$IE_PORT" \
      "$JETTY":run ) > "$log" 2>&1
  echo "  run $1: $(wc -l < "$trace" 2>/dev/null | tr -d ' ') traced records"
}

echo "fixture: $FIXTURE ($(wc -l < "$FIXTURE" | tr -d ' ') records), threads=${OBA_INFERENCE_THREADS:-0}, tracing on"
echo "No observer needed - output goes to the dummy sender; the trace is the measurement."
echo
run a
run b
echo
echo "== first differing traced record =="
python3 - /tmp/replay-trace-a.txt /tmp/replay-trace-b.txt <<'EOF'
import re, sys, collections

# Keyed on (veh, t), not on line position. Multi-threaded, the stripes publish concurrently, so the
# two files interleave vehicles differently and a positional comparison reports every line as
# different. Each vehicle's own sequence is what has to be reproducible.
def parts(l):
    d = {}
    for k in ("veh", "t", "in", "draws", "catBy", "locBy", "out"):
        m = re.search(r'(?:^| )%s=(\[[^\]]*\]|[^ ]+)' % k, l)
        d[k] = m.group(1) if m else None
    return d

def load(p):
    by_key = {}
    order = []
    dupes = 0
    for l in open(p):
        if not l.startswith("veh="):
            continue
        d = parts(l.rstrip("\n"))
        key = (d["veh"], d["t"])
        if key in by_key:
            dupes += 1
        by_key[key] = d
        order.append(key)
    return by_key, order, dupes

A, orderA, dupA = load(sys.argv[1])
B, orderB, dupB = load(sys.argv[2])
print("  records: A=%d B=%d" % (len(A), len(B)))
if dupA or dupB:
    print("  WARNING duplicate (veh,t) keys: A=%d B=%d - comparison ambiguous for those" % (dupA, dupB))

onlyA = sorted(set(A) - set(B))
onlyB = sorted(set(B) - set(A))
if onlyA or onlyB:
    print("  only in A: %d   only in B: %d" % (len(onlyA), len(onlyB)))
    for k in (onlyA + onlyB)[:5]:
        print("     %s @ %s" % k)

# Report in run A's emission order, so "first" means first produced rather than lowest key.
groups = ("in", "draws", "catBy", "locBy", "out")
tally = collections.Counter()
first = None
for key in orderA:
    if key not in B:
        continue
    x, y = A[key], B[key]
    diff = [g for g in groups if x[g] != y[g]]
    if diff:
        tally.update(diff)
        if first is None:
            first = (key, x, y, diff)

print("  records differing: %d of %d" % (sum(1 for k in orderA if k in B and any(A[k][g] != B[k][g] for g in groups)), len(A)))
if tally:
    print("  by group: " + ", ".join("%s=%d" % kv for kv in tally.most_common()))

if first:
    key, x, y, diff = first
    print()
    print("== first differing record (A's emission order) ==")
    print("  veh=%s t=%s" % key)
    for g in groups:
        if g in diff:
            print("    %-6s DIFFERS" % g)
            print("      A %s" % x[g])
            print("      B %s" % y[g])
        else:
            print("    %-6s same" % g)
else:
    print()
    print("  traces are identical for every shared (veh,t).")
EOF
