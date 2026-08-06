#!/usr/bin/env bash
#
# Build a transit-data bundle, replacing the hand-typed java invocation that made builds
# unreproducible and their diagnostics easy to lose.
#
# Usage:
#   build-bundle.sh                                  # -> /data/oba-bundle-staging/build-<date>
#   build-bundle.sh <bundle.xml> <output-dir>        # explicit
#   HEAP=12g build-bundle.sh ...                     # override heap (default 6g)
#   NICE=0 build-bundle.sh ...                       # don't nice it (default: nice 10 + idle io)
#
set -euo pipefail

MAIN=org.onebusaway.transit_data_federation.bundle.FederatedTransitDataBundleCreatorMain
# Standalone bundle discovery requires exactly one bundle under bundle.location, so building here
# would make the choice ambiguous. Refused unless ALLOW_LIVE=1: stage elsewhere, verify, then swap.
LIVE_PARENT=/data/oba-bundle
CP_FILE=${CP_FILE:-/opt/oba/bundle-classpath.txt}
LOG4J=${LOG4J:-/opt/oba/log4j-bundle.properties}
HEAP=${HEAP:-6g}
NICE=${NICE:-10}

BUNDLE_XML=${1:-/data/bundle-src/wholeMTA/bundle-wholeMTA.xml}
OUT_DIR=${2:-/data/oba-bundle-staging/build-$(date +%Y%m%d-%H%M)}

case "$OUT_DIR" in
  "$LIVE_PARENT"|"$LIVE_PARENT"/*)
    if [ "${ALLOW_LIVE:-0}" != "1" ]; then
      echo "REFUSING to build inside $LIVE_PARENT — the services discover their bundle there, and a" >&2
      echo "second bundle directory would make that ambiguous on the next restart." >&2
      echo "Stage the build elsewhere, verify it, then swap. (ALLOW_LIVE=1 overrides.)" >&2
      exit 2
    fi ;;
esac

if [ ! -s "$CP_FILE" ]; then
  echo "Classpath file $CP_FILE missing/empty." >&2
  echo "Recreate it from the recorded build (keeps the classpath identical to the shipped bundle):" >&2
  echo "  grep -oE 'java\\.class\\.path=[^,]*' /data/oba-bundle/build-wholeMTA.log \\" >&2
  echo "    | head -1 | sed 's/^java\\.class\\.path=//' > $CP_FILE" >&2
  exit 2
fi

[ -f "$BUNDLE_XML" ] || { echo "no such bundle config: $BUNDLE_XML" >&2; exit 2; }
mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/build.log"

JAVA_OPTS=(-Xmx"$HEAP" -XX:+UseG1GC)
[ -f "$LOG4J" ] && JAVA_OPTS+=(-Dlog4j.configuration=file:"$LOG4J") \
  || echo "WARNING: $LOG4J not found — build will run with log4j unconfigured and task warnings will be LOST" >&2

RUNNER=()
if [ "$NICE" != "0" ]; then
  RUNNER=(nice -n "$NICE")
  command -v ionice >/dev/null && RUNNER+=(ionice -c3)
fi

echo "bundle config : $BUNDLE_XML"
echo "output dir    : $OUT_DIR"
echo "heap / nice   : $HEAP / $NICE"
echo "log           : $LOG"
echo "csv diagnostics expected in: $OUT_DIR/csv/"
echo

"${RUNNER[@]}" java "${JAVA_OPTS[@]}" -cp "$(cat "$CP_FILE")" "$MAIN" "$BUNDLE_XML" "$OUT_DIR" \
  > "$LOG" 2>&1
rc=$?

echo "exit=$rc"
echo "--- diagnostics written:"
ls -la "$OUT_DIR/csv/" 2>/dev/null || echo "  (none — check that multiCSVLogger has a basePath property)"
echo "--- STIF warnings in the log:"
grep -c "gtfs trip not found for" "$LOG" 2>/dev/null || true
exit $rc
