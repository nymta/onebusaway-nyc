#!/usr/bin/env bash
# Run the same fixture twice in two fresh JVMs and report whether the output is identical.
#
# Usage: replay-determinism.sh [fixture.jsonl] [extra mvn args...]
#   default fixture: local-loop/replay/fixtures/determinism-270.jsonl, which is committed, so this runs on
#     a fresh clone as soon as a bundle is built
#   EXPECT_BUNDLE=<name>  fail unless the loaded bundle has this directory name. Defaults to the
#     bundle named in the fixture's sibling .manifest.json, if there is one.
#   OBA_INFERENCE_SEED, OBA_INFERENCE_THREADS  as passed to the engine
#
# Extra arguments go to mvn, unchanged, on BOTH runs - which is the point, since a property set on only
# one half would invalidate the comparison. An optional "--" may separate them from the fixture:
#   replay-determinism.sh -- -Doba.crew.disabled=true
#   replay-determinism.sh my.jsonl -Doba.crew.disabled=true
# Skipping the UTS crew fetch is worth doing on any determinism run: the committed fixture's operator
# designators are CRC32 surrogates (replay/scrub-fixture.py), so no lookup can match a real assignment,
# and a failed fetch is retried on every lookup.
#
# For JVM-level flags rather than properties (heap, GC), use OBA_MAVEN_OPTS, and repeat the heap setting -
# env.sh treats that variable as a replacement for its -Xmx8g default, not an addition.
#
# Each run gets its own JVM because per-vehicle state survives: the last-timestamp map in
# isValidRecord, and the particle filters themselves.
#
# Both halves are automated by -Dreplay.exitWhenDone=true, which makes the engine exit after the
# stripes drain instead of sitting in Jetty forever.
set -uo pipefail
# HERE is this script's directory; LOOP is the harness root one level up, which holds env.sh, the
# observer and the fixtures. Keeping them separate lets the determinism scripts live in their own
# directory without duplicating the harness.
HERE="$(cd "$(dirname "$0")" && pwd)"
LOOP="$(cd "$HERE/.." && pwd)"; source "$LOOP/env.sh"

# The first argument is the fixture unless it is the "--" separator or an option, so extra mvn args can
# be passed without naming a fixture. What is left goes to mvn.
FIXTURE="$LOOP/replay/fixtures/determinism-270.jsonl"
if [ $# -gt 0 ] && [ "$1" != "--" ] && [ "${1#-}" = "$1" ]; then FIXTURE="$1"; shift; fi
[ "${1:-}" = "--" ] && shift || true
# "--" must never reach mvn: mvn reads it as end-of-options, which turns every later -D and the jetty
# goal itself into lifecycle phases and fails the build.
MVN_EXTRA=("$@")
[ -f "$FIXTURE" ] || { echo "no such fixture: $FIXTURE" >&2; exit 2; }

# A fixture may carry a sibling manifest recording the bundle it was cut against and its own
# checksum. Where it does, the bundle it names becomes the default EXPECT_BUNDLE, so the pairing is
# enforced without anyone having to remember it.
MANIFEST="${FIXTURE%.jsonl}.manifest.json"
FIX_SHA_FULL="$(shasum -a 256 "$FIXTURE" | cut -d' ' -f1)"
if [ -f "$MANIFEST" ]; then
  MAN_BUNDLE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("bundle",""))' "$MANIFEST" 2>/dev/null)"
  MAN_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("sha256",""))' "$MANIFEST" 2>/dev/null)"
  [ -n "${EXPECT_BUNDLE:-}" ] || EXPECT_BUNDLE="$MAN_BUNDLE"
  if [ -n "$MAN_SHA" ] && [ "$MAN_SHA" != "$FIX_SHA_FULL" ]; then
    echo "WARNING: fixture does not match its manifest checksum. Results are not comparable with"
    echo "         earlier runs of $(basename "$FIXTURE"). Update the manifest if this is intended."
    echo
  fi
fi

# A determinism result only means something together with the fixture, the bundle and the seed that
# produced it. Assert what can be asserted, then print all of it, so no result is ambiguous later.
[ -d "$BUNDLE_PARENT" ] || { echo "no bundle parent: $BUNDLE_PARENT" >&2; exit 2; }
# Identified by a bundle artefact, not by being the only directory: HSQLDB leaves its own .tmp
# directories alongside the bundle.
BUNDLE_NAME="$(find "$BUNDLE_PARENT" -mindepth 2 -maxdepth 2 -name TransitGraph.obj \
  -exec dirname {} \; | xargs -n1 basename 2>/dev/null)"
if [ "$(printf '%s\n' "$BUNDLE_NAME" | grep -c .)" -ne 1 ]; then
  echo "expected exactly one bundle (a directory containing TransitGraph.obj) under" >&2
  echo "$BUNDLE_PARENT, found:" >&2
  printf '  %s\n' "${BUNDLE_NAME:-<none>}" >&2
  exit 2
fi
if [ -n "${EXPECT_BUNDLE:-}" ] && [ "$EXPECT_BUNDLE" != "$BUNDLE_NAME" ]; then
  echo "bundle mismatch: EXPECT_BUNDLE=$EXPECT_BUNDLE, found $BUNDLE_NAME" >&2
  exit 2
fi

FIX_RECORDS="$(wc -l < "$FIXTURE" | tr -d ' ')"
FIX_SHA="$(shasum -a 256 "$FIXTURE" | cut -c1-12)"
FIX_VEHICLES="$(python3 -c '
import json, sys
seen = set()
for line in open(sys.argv[1]):
    line = line.strip()
    if not line:
        continue
    outer = json.loads(line)
    # Archived lines wrap the envelope as a string under "b"; fixtures hold it directly.
    env = json.loads(outer["b"])["RealtimeEnvelope"] if "b" in outer else outer["RealtimeEnvelope"]
    v = env["CcLocationReport"]["vehicle"]
    seen.add((v.get("agencydesignator"), v.get("vehicle-id")))
print(len(seen))
' "$FIXTURE" 2>/dev/null)"
[ -n "$FIX_VEHICLES" ] || FIX_VEHICLES="?"

OBS_SECONDS="${OBS_SECONDS:-1200}"
A=/tmp/replay-run-a.ndjson
B=/tmp/replay-run-b.ndjson

if port_up "$IE_PORT" || port_up "$Q_IN"; then
  echo "ports $IE_PORT or $Q_IN are in use. Run stop-all.sh and stop any observer first." >&2
  exit 2
fi

run_once() {  # $1 = label, $2 = capture file, $3 = log
  local label="$1" capture="$2" log="$3"
  echo "== run $label =="
  rm -f "$capture"

  # Observer first: it BINDS :5566, and a PUB that connects before anyone is listening drops.
  python3 "$LOOP/obs_inferred.py" "$Q_IN" inference_queue "$OBS_SECONDS" "$capture" \
    > "/tmp/replay-obs-$label.log" 2>&1 &
  local obs_pid=$!
  sleep 3

  ( cd "$MAIN_REPO" && "$MVN" -f onebusaway-nyc-vehicle-tracking-webapp/pom.xml \
      -P local-ie-testing -DskipTests -B \
      -Die.listener=ReplayFileInputTask \
      -Dspring.profiles.active=replay \
      -Dreplay.file="$FIXTURE" \
      -Doba.inference.seed="${OBA_INFERENCE_SEED:-20260807}" \
      -Doba.inference.threads="${OBA_INFERENCE_THREADS:-0}" \
      -Dreplay.exitWhenDone=true \
      -Die.output.queue=OutputQueueSenderServiceImpl \
      -DtimePredictions.status=ENABLED \
      -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
      -Dbundle.location="$BUNDLE_PARENT" \
      -Djetty.http.port="$IE_PORT" \
      ${MVN_EXTRA[@]+"${MVN_EXTRA[@]}"} \
      "$JETTY":run ) > "$log" 2>&1
  local rc=$?

  # The engine has exited, so nothing more will be published; stop waiting out the observer timeout.
  # Give it a moment to drain whatever is already in the socket, then signal it - obs_inferred.py
  # handles SIGTERM by flushing and closing the capture.
  sleep 3
  kill -TERM "$obs_pid" 2>/dev/null
  wait "$obs_pid" 2>/dev/null

  grep -E "replay: done" "$log" | sed 's/^.*ReplayFileInputTask.java:[0-9]*\] : /  /' || true
  echo "  captured $(wc -l < "$capture" 2>/dev/null | tr -d ' ') records (engine exit $rc)"
  echo
}

echo "fixture: $FIXTURE"
echo "         $FIX_RECORDS records, $FIX_VEHICLES vehicles, sha256 $FIX_SHA"
echo "bundle:  $BUNDLE_NAME"
echo "seed:    ${OBA_INFERENCE_SEED:-20260807}   threads: ${OBA_INFERENCE_THREADS:-0}"
# Printed because it changes the result: a determinism verdict means nothing without the properties the
# two runs were given.
[ "${#MVN_EXTRA[@]}" -gt 0 ] && echo "mvn:     ${MVN_EXTRA[*]}"
if [ "$FIX_VEHICLES" = "1" ]; then
  echo
  echo "WARNING: one vehicle. A vehicle hashes to a single stripe, so this run does not exercise"
  echo "         cross-thread ordering whatever OBA_INFERENCE_THREADS is set to."
fi
echo
echo "Each run loads the bundle first, so allow a few minutes per run."
echo
run_once a "$A" /tmp/replay-ie-a.log
run_once b "$B" /tmp/replay-ie-b.log

echo "== comparison =="
python3 "$HERE/compare-replay-runs.py" "$A" "$B"
