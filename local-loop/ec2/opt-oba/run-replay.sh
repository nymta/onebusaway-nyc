#!/usr/bin/env bash
# Replay archived bustechGps chunks from S3 through the inference engine, writing inferred output
# back to S3. Decoupled from the predictions engine: no ZMQ, no observer, no broker.
#
# Usage:
#   run-replay.sh [--from D[/HH-MM]] [--to D[/HH-MM]] [--limit N] [--shard i/n] [--tag NAME]
#                 [--prefix s3://...] [extra -D args...]  (D = YYYY-MM-DD, HH-MM = ET slot start)
#   run-replay.sh s3://BUCKET/key1.jsonl.gz [s3://BUCKET/key2.jsonl.gz ...] [extra -D args...]
#
# --prefix defaults to s3://mtalirr/data-archiver/bustechGps/, the one archive there is; only
# pass it to point at something else. Ignored if you give explicit s3:// source URIs instead.
#
# --shard i/n: runs only 1/n of the fleet (vehicle-hash partitioned), for splitting a run across N
# independent processes - e.g. one per NUMA node. When given, this run's own LABEL/OUT_DIR/OUT_S3/
# CloudWatch RunId get a "-shardI" suffix, its Jetty port is offset by i so N of these can run on one
# box at once, oba.inference.threads defaults to this box's share (REPLAY_THREADS still overrides),
# and the mvn invocation is wrapped in `numactl --cpunodebind=i --membind=i` (requires numactl
# installed). Omit --shard entirely for a plain, single-process run - nothing above applies.
# --tag NAME: prefixes NAME- onto this run's LABEL (and hence OUT_DIR/OUT_S3/CloudWatch RunId), so
# related runs are easy to pick out by eye rather than by timestamp alone.
#
# Environment:
#   REPLAY_OUT_S3     s3://bucket/prefix for inference output parts (default: none, spool stays local)
#   REPLAY_CREW_DIR   prefetched UTS snapshots (default /data/uts-snapshots; fetch with
#                     local-loop/replay/fetch-crew-snapshots.sh). Unset dir -> crew disabled.
#   REPLAY_THREADS    inference stripes (default: vCPU count minus 2, for the reader and the JVM)
#   REPLAY_CLOUDWATCH set to 0 to skip pushing replay-monitor.log to CloudWatch (default: on)
#
# Chunks are fed through a FIFO in the order given (--prefix sorts keys, chronological for the
# archive's naming), so multi-chunk windows replay as one continuous stream and nothing is staged
# on disk. The instance profile provides S3 credentials for both directions.
set -uo pipefail
source /opt/oba/env-common.sh
export MAVEN_OPTS="${REPLAY_MAVEN_OPTS:--Xmx30g} -Duser.timezone=America/New_York"

# Leave 2 vCPUs free for the reader thread and JVM/OS overhead; never go below 1 stripe.
NPROC=$(getconf _NPROCESSORS_ONLN)
REPLAY_THREADS_DEFAULT=$NPROC
[ "$REPLAY_THREADS_DEFAULT" -gt 3 ] && REPLAY_THREADS_DEFAULT=$((REPLAY_THREADS_DEFAULT - 2))

SOURCES=(); MVN_EXTRA=(); PREFIX=""; LIMIT=0; FROM=""; TO=""; TO_RAW=""; SHARD=""; TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --limit)  LIMIT="$2"; shift 2 ;;
    --from)   FROM="$2"; shift 2 ;;
    --to)     TO="$2"; TO_RAW="$2"; shift 2 ;;
    --shard)  SHARD="$2"; shift 2 ;;
    --tag)    TAG="$2"; shift 2 ;;
    s3://*)   SOURCES+=("$1"); shift ;;
    -D*)      MVN_EXTRA+=("$1"); shift ;;
    *) echo "unrecognized argument: $1" >&2; exit 2 ;;
  esac
done

# Everything in this block is a no-op unless --shard was actually given, so a plain invocation
# behaves exactly as before it existed.
SHARD_INDEX=""; SHARD_COUNT=""; NUMA_PREFIX=(); SHARD_ARGS=(); JETTY_PORT=8081
if [ -n "$SHARD" ]; then
  SHARD_INDEX="${SHARD%%/*}"; SHARD_COUNT="${SHARD#*/}"
  case "$SHARD_INDEX" in ''|*[!0-9]*) echo "ERROR: --shard index must be an integer, got '$SHARD_INDEX'" >&2; exit 2 ;; esac
  case "$SHARD_COUNT" in ''|*[!0-9]*) echo "ERROR: --shard count must be an integer, got '$SHARD_COUNT'" >&2; exit 2 ;; esac
  [ "$SHARD_INDEX" -lt "$SHARD_COUNT" ] || { echo "ERROR: --shard index $SHARD_INDEX out of range for count $SHARD_COUNT" >&2; exit 2; }
  command -v numactl >/dev/null 2>&1 || { echo "ERROR: --shard needs numactl (dnf install numactl); a shard run without NUMA pinning defeats the point" >&2; exit 2; }
  # Recompute from each shard's own share of the box's vCPUs, not by dividing the whole-box default -
  # dividing (NPROC-2) instead would leave only 1 free per shard's own pinned domain, not 2.
  REPLAY_THREADS_DEFAULT=$((NPROC / SHARD_COUNT))
  [ "$REPLAY_THREADS_DEFAULT" -gt 3 ] && REPLAY_THREADS_DEFAULT=$((REPLAY_THREADS_DEFAULT - 2))
  [ "$REPLAY_THREADS_DEFAULT" -ge 1 ] || REPLAY_THREADS_DEFAULT=1
  NUMA_PREFIX=(numactl "--cpunodebind=$SHARD_INDEX" "--membind=$SHARD_INDEX")
  SHARD_ARGS=(-Dreplay.vehicleShard="$SHARD_INDEX/$SHARD_COUNT")
  JETTY_PORT=$((8081 + SHARD_INDEX))

  # The webapp's Spring context always opens a local HSQLDB file at
  # ${bundle.location}/org_onebusaway_database, regardless of profile - it's wired in unconditionally,
  # not something replay itself touches. Two JVMs pointed at the same bundle.location race for that
  # file's lock and the loser fails at Hibernate init. Give this shard its own shadow bundle dir:
  # symlink the real (large, shared, read-only) pick directories in, so nothing gets copied, but leave
  # out the sibling org_onebusaway_database.* files so HSQLDB creates a fresh, private one here instead.
  SHARD_BUNDLE_DIR="/data/oba-bundle-shard${SHARD_INDEX}"
  mkdir -p "$SHARD_BUNDLE_DIR"
  for d in "$BUNDLE"/*/; do
    [ -d "$d" ] || continue
    b="$(basename "$d")"
    case "$b" in org_onebusaway_database*|onebusaway_nyc*) continue ;; esac
    ln -sfn "$d" "$SHARD_BUNDLE_DIR/$b"
  done
  BUNDLE="$SHARD_BUNDLE_DIR"
fi

# Only default it absent explicit s3:// source URIs too, or both would feed SOURCES at once.
[ -z "$PREFIX" ] && [ "${#SOURCES[@]}" -eq 0 ] && PREFIX="s3://mtalirr/data-archiver/bustechGps/"

if [ -n "$PREFIX" ]; then
  bucket="${PREFIX#s3://}"; bucket="${bucket%%/*}"
  key="${PREFIX#s3://$bucket/}"
  # Keys are <feed>/<YYYY-MM-DD>/HH-MM.jsonl.gz with the slot named by its ET start, so lexicographic
  # order is chronological and bounds are plain string comparisons.
  keys=$(aws s3api list-objects-v2 --bucket "$bucket" --prefix "$key" \
           --query 'Contents[].Key' --output text | tr '\t' '\n' \
           | grep -E '\.jsonl(\.gz)?$' | sort)
  [ -n "$keys" ] || { echo "no .jsonl objects under $PREFIX" >&2; exit 2; }
  if [ -n "$FROM" ] || [ -n "$TO" ]; then
    # Bounds are "YYYY-MM-DD" or "YYYY-MM-DD/HH-MM" (slot start, ET); date-only --to covers the day.
    case "$TO" in */*) : ;; ?*) TO="$TO/23-59" ;; esac
    keys=$(printf '%s\n' "$keys" | awk -F/ -v a="$FROM" -v b="$TO" '{
        f=$NF; sub(/\..*$/, "", f); c=$(NF-1)"/"f
      } (a=="" || c>=a) && (b=="" || c<=b)')
    [ -n "$keys" ] || { echo "no objects in [$FROM..$TO] under $PREFIX" >&2; exit 2; }
  fi
  [ "$LIMIT" -gt 0 ] && keys=$(printf '%s\n' "$keys" | head -n "$LIMIT")
  while IFS= read -r k; do SOURCES+=("s3://$bucket/$k"); done <<< "$keys"
fi
[ "${#SOURCES[@]}" -gt 0 ] || { sed -n '2,18p' "$0"; exit 2; }

# Run label from the window bounds, or from the first/last source when explicit URIs were given.
slot_of() {
  local u="${1%.jsonl.gz}"; u="${u%.jsonl}"
  local day; day="$(basename "$(dirname "$u")")"
  case "$day" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) echo "${day}_$(basename "$u")" ;;
    *) basename "$u" ;;
  esac
}
if [ -n "$FROM" ] || [ -n "$TO_RAW" ]; then
  A="${FROM:-open}"; B="${TO_RAW:-open}"
else
  A="$(slot_of "${SOURCES[0]}")"; B="$(slot_of "${SOURCES[${#SOURCES[@]}-1]}")"
fi
RUN_TS="$(TZ=America/New_York date +%Y%m%dT%H%M%SET)"
LABEL="${RUN_TS}-$(echo "$A" | tr / _)-to-$(echo "$B" | tr / _)"
[ -n "$TAG" ] && LABEL="${TAG}-${LABEL}"
[ -n "$SHARD_INDEX" ] && LABEL="${LABEL}-shard-${SHARD_INDEX}-of-${SHARD_COUNT}"

# Best-effort window end (ET slot -> UTC epoch millis) for the monitor log's ETA. A streaming FIFO
# has no other way to know how much more data is coming, so this is opt-in: left unset on any
# parse failure rather than risk a wrong ETA, and the monitor log just reports "unknown" instead.
WINDOW_END_ARG=()
end_slot="${TO:-$(echo "$B" | tr _ /)}"
case "$end_slot" in
  */*/*|open|"")
    : ;; # not a single "D/HH-MM" token (multi-segment, or no window given at all) - skip
  */*)
    # This locates the --to slot's own START; archive slots are a fixed 5 minutes wide, so add that
    # to land on the window's actual end instead of its start.
    end_date="${end_slot%%/*}"; end_hhmm="${end_slot#*/}"
    # GNU date reads a trailing "+5" right after a time as a UTC offset, not a relative adjustment
    # (silently landing 9 hours off) - the relative term must come first to parse as "add 5 minutes".
    end_epoch="$(TZ=America/New_York date -d "+5 minutes $end_date ${end_hhmm/-/:}:00" +%s 2>/dev/null \
      || TZ=America/New_York date -j -v+5M -f '%Y-%m-%d %H:%M:%S' "$end_date ${end_hhmm/-/:}:00" +%s 2>/dev/null)"
    [ -n "$end_epoch" ] && WINDOW_END_ARG=(-Doba.replay.window.endMillis="${end_epoch}000")
    ;;
esac

# Crew snapshots for the window, fetched if missing (cached days skipped). Slots are ET, crew
# prefixes UTC generation dates; ET evening slots cross into the next UTC day, so the range is
# [from .. to+1]. REPLAY_SKIP_CREW_FETCH=1 uses the dir as-is.
CREW_DIR="${REPLAY_CREW_DIR:-/data/uts-snapshots}"
if [ "${REPLAY_SKIP_CREW_FETCH:-0}" != "1" ]; then
  d1="${A%%[/_]*}"; d2="${B%%[/_]*}"; [ "$d2" = "open" ] && d2="$d1"
  case "$d1" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
      next_day() { date -d "$1 +1 day" +%Y-%m-%d 2>/dev/null || date -j -v+1d -f %Y-%m-%d "$1" +%Y-%m-%d; }
      "$MAIN/local-loop/replay/fetch-crew-snapshots.sh" "$d1" "$(next_day "$d2")" -o "$CREW_DIR" \
        || { echo "ERROR: crew fetch failed; aborting before a run on a partial roster" >&2; exit 1; }
      ;;
    *) echo "WARNING: no date in '$A' to derive crew window from; skipping crew fetch" >&2 ;;
  esac
fi
if [ -d "$CREW_DIR" ]; then
  CREW_ARGS=(-Doba.crew.snapshotDir="$CREW_DIR")
else
  echo "WARNING: no crew snapshots at $CREW_DIR; running with crew disabled" >&2
  CREW_ARGS=(-Doba.crew.disabled=true)
fi

OUT_DIR="${REPLAY_OUT_DIR:-/data/replay-out/$LABEL}"
mkdir -p "$OUT_DIR"
OUT_S3="${REPLAY_OUT_S3:-s3://ds-oba/replay/inference-outputs/$LABEL/}"
OUT_ARGS=(-Doba.replay.output.dir="$OUT_DIR")

# The engine spools parts and renames each on completion; the CLI uploads them (the engine's bundled
# AWS SDK is from 2012 and cannot sign for modern buckets). Rolling, so days-long runs do not fill
# the disk. REPLAY_OUT_S3=none keeps parts local.
upload_parts() {
  local f
  for f in "$OUT_DIR"/inferred-*.ndjson "$OUT_DIR"/inferred-*.ndjson.gz; do
    [ -f "$f" ] || continue
    aws s3 mv "$f" "$OUT_S3" --only-show-errors && echo "uploaded $(basename "$f")"
  done
}
UPLOAD_PID=""
if [ "$OUT_S3" != "none" ] && [ -z "$SHARD_INDEX" ]; then
  ( while :; do sleep 30; upload_parts; done ) > "$OUT_DIR/upload.log" 2>&1 &
  UPLOAD_PID=$!
elif [ -n "$SHARD_INDEX" ]; then
  # A sharded run's own output is wrongly-bucketed by construction (stragglers land in
  # whichever part happens to be open, not their true window) - local-loop/replay/
  # merge-shard-output.py fixes that while merging every shard's output together, so it
  # must be the only thing that uploads. Leave $OUT_DIR alone for it to read.
  echo "sharded run: not uploading; run merge-shard-output.py separately across all shards' OUT_DIRs" >&2
fi

# Reads replay-monitor.log's last line and pushes it to CloudWatch, dimensioned by this run's own
# label. CLI, not the engine's bundled 2012 SDK (no instance-role support in that SDK at all, only
# static keys - see EC2-SCALING-FINDINGS.md), so this needs no credentials beyond the instance role
# already used for S3. REPLAY_CLOUDWATCH=0 disables it.
push_cloudwatch_metrics() {
  local line rec_s ms_per_rec pct speed stripes active total metric_data dims disk_pct
  line="$(tail -n 1 "$OUT_DIR/replay-monitor.log" 2>/dev/null)" || return 0
  [ -n "$line" ] || return 0
  rec_s="$(echo "$line" | grep -oE 'aggregate_rec_s=[0-9.]+' | cut -d= -f2)"
  ms_per_rec="$(echo "$line" | grep -oE 'aggregate_ms_per_rec=[0-9.]+' | cut -d= -f2)"
  pct="$(echo "$line" | grep -oE 'pct_complete=[0-9.]+' | cut -d= -f2)"
  speed="$(echo "$line" | grep -oE 'speed=[0-9.]+' | cut -d= -f2)"
  stripes="$(echo "$line" | grep -oE 'stripes=[0-9]+/[0-9]+' | cut -d= -f2)"
  [ -n "$rec_s" ] && [ -n "$stripes" ] || return 0
  active="${stripes%%/*}"; total="${stripes#*/}"
  dims="{Name=RunId,Value=$LABEL}"
  metric_data=(
    "MetricName=AggregateRecPerSec,Value=${rec_s},Unit=Count/Second,Dimensions=[$dims]"
    "MetricName=AggregateMsPerRecord,Value=${ms_per_rec:-0},Unit=Milliseconds,Dimensions=[$dims]"
    "MetricName=ActiveStripes,Value=${active},Unit=Count,Dimensions=[$dims]"
    "MetricName=TotalStripes,Value=${total},Unit=Count,Dimensions=[$dims]"
  )
  [ -n "$pct" ] && metric_data+=("MetricName=PercentComplete,Value=${pct},Unit=Percent,Dimensions=[$dims]")
  # Virtual-time-covered / wall-time-elapsed, e.g. 4.0 for "4x realtime" - not comparable across runs
  # of very different length, since it's diluted by any drain-phase tail (clock frozen, wall time not).
  [ -n "$speed" ] && metric_data+=("MetricName=SpeedRealtime,Value=${speed},Unit=None,Dimensions=[$dims]")
  # /data holds the local spool before upload; a long run that outpaces the upload loop fills it silently.
  disk_pct="$(df -P /data 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5}')"
  [ -n "$disk_pct" ] && metric_data+=("MetricName=DataDiskUsedPct,Value=${disk_pct},Unit=Percent,Dimensions=[$dims]")
  aws cloudwatch put-metric-data --region "${AWS_DEFAULT_REGION:-us-east-1}" \
    --namespace "OBA/Replay" --metric-data "${metric_data[@]}"
}
CLOUDWATCH_PID=""
if [ "${REPLAY_CLOUDWATCH:-1}" != "0" ]; then
  ( while :; do sleep 60; push_cloudwatch_metrics; done ) > "$OUT_DIR/cloudwatch.log" 2>&1 &
  CLOUDWATCH_PID=$!
fi

FIFO="/tmp/oba-replay-$$.fifo"
rm -f "$FIFO"; mkfifo "$FIFO"
FEED_PID=""
cleanup() {
  [ -n "$FEED_PID" ] && kill "$FEED_PID" 2>/dev/null
  [ -n "$UPLOAD_PID" ] && kill "$UPLOAD_PID" 2>/dev/null
  [ -n "$CLOUDWATCH_PID" ] && kill "$CLOUDWATCH_PID" 2>/dev/null
  rm -f "$FIFO"
}
trap cleanup EXIT INT TERM

echo "sources : ${#SOURCES[@]} chunk(s)"
for s in "${SOURCES[@]}"; do echo "          $s"; done
echo "output  : $OUT_DIR$([ "$OUT_S3" != "none" ] && echo " -> $OUT_S3")"
echo

(
  for s in "${SOURCES[@]}"; do
    t0=$(date +%s)
    case "$s" in
      *.gz) aws s3 cp "$s" - | gzip -dc ;;
      *)    aws s3 cp "$s" - ;;
    esac || { echo "FEED FAILED on $s; aborting so the gap is not silent" >&2; exit 1; }
    echo "fed $s in $(( $(date +%s) - t0 ))s" >&2
  done
  echo "feed complete: ${#SOURCES[@]} object(s)" >&2
) > "$FIFO" 2> "$OUT_DIR/feed.log" &
FEED_PID=$!

cd "$MAIN/onebusaway-nyc-vehicle-tracking-webapp"
${NUMA_PREFIX[@]+"${NUMA_PREFIX[@]}"} \
mvn -B -P local-ie-testing -DskipTests -Dlicense.skip=true \
  -Dspring.profiles.active=replay \
  -Die.listener=ReplayFileInputTask \
  -Dreplay.file="$FIFO" \
  -Dreplay.exitWhenDone=true \
  -Die.output.queue=S3OutputQueueSenderServiceImpl \
  "${OUT_ARGS[@]}" \
  "${CREW_ARGS[@]}" \
  ${WINDOW_END_ARG[@]+"${WINDOW_END_ARG[@]}"} \
  ${SHARD_ARGS[@]+"${SHARD_ARGS[@]}"} \
  -Doba.inference.threads="${REPLAY_THREADS:-$REPLAY_THREADS_DEFAULT}" \
  -Dparticle.filter.debug=false \
  -DtimePredictions.status=ENABLED \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Dbundle.location="$BUNDLE" \
  -Doba.bundleSwitchTracking.disabled=true \
  `# replay never switches bundles, so this bookkeeping (capped at MAX_EXPECTED_THREADS=3000) is skipped` \
  -Djetty.http.port="$JETTY_PORT" \
  -Doba.deadband.enabled=true -Doba.deadband.minMeters=10 -Doba.deadband.minIntervalSec=7 -Doba.deadband.maxAgeSec=30 \
  ${MVN_EXTRA[@]+"${MVN_EXTRA[@]}"} \
  "$JETTY" 2>&1 | tee "$OUT_DIR/engine.log"
rc=${PIPESTATUS[0]}

[ -n "$UPLOAD_PID" ] && { kill "$UPLOAD_PID" 2>/dev/null; wait "$UPLOAD_PID" 2>/dev/null; }
wait "$FEED_PID" 2>/dev/null
if [ "$OUT_S3" != "none" ]; then
  echo; echo "== upload (final sweep) =="
  upload_parts | sed 's/^/  /'
fi
echo; echo "== feed =="; sed 's/^/  /' "$OUT_DIR/feed.log"
echo; grep -a "replay: done" "$OUT_DIR/engine.log" | tail -1
# merge-shard-output.py watches for this in every shard's OUT_DIR - it has no other way to know
# a shard has stopped producing new output, and would otherwise wait forever on the tail buckets
# that never get a "next" nominal file to prove they're safe to close. Written unconditionally,
# success or failure, since either way no more data is coming from this shard.
echo "$rc" > "$OUT_DIR/REPLAY_DONE"
exit "$rc"
