#!/usr/bin/env bash
# Replay an archived AVL fixture through the inference engine on data time.
#
# Usage: replay.sh [fixture.jsonl] [extra mvn args...]
#   default fixture: .context/replay-sample/fixture-local.jsonl
#
# Anything after the fixture goes to mvn, which is how per-run JVM properties are set. An optional "--"
# may separate them; it is stripped rather than forwarded. The property you usually want:
#   -Doba.crew.disabled=true   skip the UTS crew fetch. Without it, a run with no S3 credentials (or one
#                              replaying a date the bucket has no roster for) retries the fetch on every
#                              lookup. See EXTERNAL-DEPENDENCIES.md entry 3.
#
# Runs in the FOREGROUND, tee'd to /tmp/replay-ie.log, so Ctrl-C ends the run. That is deliberate:
# each replay needs a fresh JVM. Two pieces of per-vehicle state survive a run - the last timestamp
# in isValidRecord (a re-run would see timeDiff 0 and drop every record as under the 3 s minimum) and
# the particle filter itself, which would continue the old track rather than start clean.
#
# Start ../observe-inferred.sh FIRST. It binds :5566; without it the output is published to nobody.
# Or skip the observer entirely: OBA_IE_OUTPUT_QUEUE=S3OutputQueueSenderServiceImpl writes NDJSON to
# a spool dir instead of publishing (see -Doba.replay.output.* in that class).
set -euo pipefail
# HERE is this script's directory; LOOP is the harness root one level up, which holds env.sh.
HERE="$(cd "$(dirname "$0")" && pwd)"
LOOP="$(cd "$HERE/.." && pwd)"; source "$LOOP/env.sh"

# The first argument is the fixture unless it is the "--" separator or an option, so extra mvn args can
# be passed without naming a fixture. What is left goes to mvn.
FIXTURE="$MAIN_REPO/.context/replay-sample/fixture-local.jsonl"
if [ $# -gt 0 ] && [ "$1" != "--" ] && [ "${1#-}" = "$1" ]; then FIXTURE="$1"; shift; fi
# "--" must never reach mvn: mvn reads it as end-of-options, which turns every later -D and the jetty
# goal itself into lifecycle phases and fails the build.
[ "${1:-}" = "--" ] && shift || true
LOG=/tmp/replay-ie.log

{ [ -f "$FIXTURE" ] || [ -p "$FIXTURE" ]; } || { echo "no such fixture: $FIXTURE" >&2; exit 2; }
[ -d "$BUNDLE_PARENT" ] || { echo "BUNDLE_PARENT does not exist: $BUNDLE_PARENT" >&2; exit 2; }

# One bundle only: discovery is standalone, so a second directory makes the choice ambiguous.
BUNDLES=$(find "$BUNDLE_PARENT" -maxdepth 2 -name CalendarServiceData.obj 2>/dev/null | wc -l | tr -d ' ')
[ "$BUNDLES" = "1" ] || { echo "expected exactly 1 bundle under $BUNDLE_PARENT, found $BUNDLES" >&2; exit 2; }

if port_up "$IE_PORT"; then
  echo "inference is already up on :$IE_PORT. Stop it first - a replay needs a fresh JVM." >&2
  exit 2
fi
if [ "${OBA_IE_OUTPUT_QUEUE:-OutputQueueSenderServiceImpl}" = "OutputQueueSenderServiceImpl" ] \
    && ! port_up "$Q_IN"; then
  echo "WARNING: nothing is bound to :$Q_IN, so inferred locations will go nowhere."
  echo "         Run $LOOP/observe-inferred.sh in another terminal first, then re-run this."
  echo
fi

# Counting lines would block forever on a FIFO, which is how replay-stream.sh feeds this.
if [ -p "$FIXTURE" ]; then
  echo "fixture : $FIXTURE  (fifo, streamed)"
else
  echo "fixture : $FIXTURE  ($(wc -l < "$FIXTURE" | tr -d ' ') records)"
fi
echo "bundle  : $BUNDLE_PARENT"
echo "log     : $LOG"
echo "Bundle load takes a few minutes before the replay starts."
echo

# UTS crew roster, as-of the replay clock. Prefetch with fetch-crew-snapshots.sh; without a snapshot
# directory the lookups are disabled rather than left to hammer S3 per record.
CREW_DIR="${OBA_CREW_SNAPSHOT_DIR:-/tmp/uts-snapshots}"
if [ -d "$CREW_DIR" ]; then
  CREW_ARGS=(-Doba.crew.snapshotDir="$CREW_DIR")
else
  echo "WARNING: no UTS snapshots at $CREW_DIR (set OBA_CREW_SNAPSHOT_DIR); crew lookups disabled"
  CREW_ARGS=(-Doba.crew.disabled=true)
fi

cd "$MAIN_REPO"
# Leave 2 cores free for the reader thread and JVM/OS overhead; never go below 1 stripe. The
# stripe default (~20x cores) is pure oversubscription for this CPU-bound, all-cores-busy workload.
THREADS_DEFAULT=$(getconf _NPROCESSORS_ONLN)
[ "$THREADS_DEFAULT" -gt 3 ] && THREADS_DEFAULT=$((THREADS_DEFAULT - 2))

# Replay defaults, each overridable by env var. Deadband values are production's
# (ec2/opt-oba/run-inference.sh); particle.filter.debug overrides the local-ie-testing pom profile,
# which is the only profile that turns it on.
"$MVN" -f onebusaway-nyc-vehicle-tracking-webapp/pom.xml \
  -P local-ie-testing -DskipTests -B \
  -Die.listener=ReplayFileInputTask \
  -Dspring.profiles.active=replay \
  -Dreplay.file="$FIXTURE" \
  -Doba.inference.seed="${OBA_INFERENCE_SEED:-20260807}" \
  -Doba.inference.threads="${OBA_INFERENCE_THREADS:-$THREADS_DEFAULT}" \
  -Die.output.queue="${OBA_IE_OUTPUT_QUEUE:-OutputQueueSenderServiceImpl}" \
  -Dparticle.filter.debug="${OBA_PF_DEBUG:-false}" \
  -Doba.deadband.enabled="${OBA_DEADBAND_ENABLED:-true}" \
  -Doba.deadband.minMeters="${OBA_DEADBAND_MIN_METERS:-10}" \
  -Doba.deadband.minIntervalSec="${OBA_DEADBAND_MIN_INTERVAL_SEC:-7}" \
  -Doba.deadband.maxAgeSec="${OBA_DEADBAND_MAX_AGE_SEC:-30}" \
  "${CREW_ARGS[@]}" \
  -DtimePredictions.status=ENABLED \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Dbundle.location="$BUNDLE_PARENT" \
  -Djetty.http.port="$IE_PORT" \
  "$@" \
  "$JETTY":run 2>&1 | tee "$LOG"
