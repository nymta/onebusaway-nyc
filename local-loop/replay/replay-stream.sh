#!/usr/bin/env bash
# Stream archived bustechGps buckets into the inference engine, in order, without staging them.
#
# Usage:
#   replay-stream.sh <source> [<source> ...] [-- extra mvn args...]
#   replay-stream.sh --prefix s3://bucket/path/ [--from D[/HH-MM]] [--to D[/HH-MM]] [--limit N]
#                    [-- extra mvn args...]
#   where D is YYYY-MM-DD and HH-MM is the 5-minute slot start in ET, e.g.
#   --prefix s3://mtalirr/data-archiver/bustechGps/ --from 2026-07-28/08-45 --to 2026-07-28/08-45
#
# A <source> is a local .jsonl/.jsonl.gz file or an s3://bucket/key. Sources are fed in the order
# given; --prefix lists a bucket prefix and sorts keys lexicographically, which is chronological for
# the archive's HH-MM naming.
#
# Download and decompression run here, not in the engine: the engine keeps one input path, network
# cost stays separately measurable, and the FIFO applies backpressure so a fast download cannot
# outrun inference or buffer a day of data in memory.
#
# Start ../observe-inferred.sh FIRST, as with replay.sh.
#
# Speed knobs worth varying for a benchmark:
#   -Doba.inference.threads=N   default is one stripe per core-ish; 1 to serialise
#   OBA_MAVEN_OPTS="-Xmx24g"    4,900 vehicles x 200 particles needs headroom
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOOP="$(cd "$HERE/.." && pwd)"; source "$LOOP/env.sh"

SOURCES=(); MVN_EXTRA=(); PREFIX=""; LIMIT=0; FROM=""; TO=""; TO_RAW=""; SEEN_SEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --) SEEN_SEP=1; shift ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    --to) TO="$2"; TO_RAW="$2"; shift 2 ;;
    *) if [ "$SEEN_SEP" = "1" ]; then MVN_EXTRA+=("$1"); else SOURCES+=("$1"); fi; shift ;;
  esac
done

if [ -n "$PREFIX" ]; then
  case "$PREFIX" in
    s3://*) : ;;
    *) echo "--prefix must be an s3:// URI" >&2; exit 2 ;;
  esac
  bucket="${PREFIX#s3://}"; bucket="${bucket%%/*}"
  key="${PREFIX#s3://$bucket/}"
  echo "listing s3://$bucket/$key ..."
  keys=$(aws s3api list-objects-v2 --bucket "$bucket" --prefix "$key" \
           --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' \
           | grep -E '\.jsonl(\.gz)?$' | sort)
  [ -n "$keys" ] || { echo "no .jsonl/.jsonl.gz objects under $PREFIX" >&2; exit 2; }
  # Keys end <YYYY-MM-DD>/<HH-MM>.jsonl.gz (slot start in ET), so "date" or "date/HH-MM" bounds are
  # string comparisons. A date-only --to is widened to the whole day.
  if [ -n "$FROM" ] || [ -n "$TO" ]; then
    case "$TO" in */*) : ;; ?*) TO="$TO/23-59" ;; esac
    keys=$(printf '%s\n' "$keys" | awk -F/ -v a="$FROM" -v b="$TO" '{
        f=$NF; sub(/\..*$/, "", f); c=$(NF-1)"/"f
      } (a=="" || c>=a) && (b=="" || c<=b)')
    [ -n "$keys" ] || { echo "no objects in [$FROM..$TO] under $PREFIX" >&2; exit 2; }
  fi
  [ "$LIMIT" -gt 0 ] && keys=$(printf '%s\n' "$keys" | head -n "$LIMIT")
  while IFS= read -r k; do [ -n "$k" ] && SOURCES+=("s3://$bucket/$k"); done <<< "$keys"
fi

[ "${#SOURCES[@]}" -gt 0 ] || { sed -n '2,20p' "$0"; exit 2; }

for s in "${SOURCES[@]}"; do
  case "$s" in
    s3://*) : ;;
    *) [ -f "$s" ] || { echo "no such file: $s" >&2; exit 2; } ;;
  esac
done

# Run label from the window bounds, or from the first/last source when explicit URIs were given.
# Names the spool directory and the S3 destination, so a run's output is findable by its window.
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
LABEL="$(echo "$A" | tr / _)-to-$(echo "$B" | tr / _)-$(date -u +%Y%m%dT%H%M%SZ)"

# Crew snapshots for the window, fetched into the snapshot dir if missing (cached days are skipped).
# Slots are named in ET; crew prefixes are UTC generation dates, and ET evening slots cross into the
# next UTC day, so the range is [from .. to+1] - the fetch script adds the lookback day itself.
# REPLAY_SKIP_CREW_FETCH=1 skips the fetch and uses the dir as-is.
export OBA_CREW_SNAPSHOT_DIR="${OBA_CREW_SNAPSHOT_DIR:-/tmp/uts-snapshots}"
if [ "${REPLAY_SKIP_CREW_FETCH:-0}" != "1" ]; then
  d1="${A%%[/_]*}"; d2="${B%%[/_]*}"; [ "$d2" = "open" ] && d2="$d1"
  case "$d1" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
      next_day() { date -j -v+1d -f %Y-%m-%d "$1" +%Y-%m-%d 2>/dev/null || date -d "$1 +1 day" +%Y-%m-%d; }
      "$HERE/fetch-crew-snapshots.sh" "$d1" "$(next_day "$d2")" -o "$OBA_CREW_SNAPSHOT_DIR" \
        || echo "WARNING: crew fetch failed; replay uses whatever is already in $OBA_CREW_SNAPSHOT_DIR"
      ;;
    *) echo "WARNING: no date in '$A' to derive crew window from; skipping crew fetch" ;;
  esac
fi

# Inference output: the engine spools NDJSON parts and renames each one on completion; this script
# uploads completed parts with the CLI (the engine's bundled AWS SDK is from 2012 and cannot sign for
# modern buckets). REPLAY_OUT_S3 overrides the destination; REPLAY_OUT_S3=none keeps parts local.
export OBA_IE_OUTPUT_QUEUE="${OBA_IE_OUTPUT_QUEUE:-S3OutputQueueSenderServiceImpl}"
OUT_DIR="${REPLAY_OUT_DIR:-/tmp/oba-replay-out/$LABEL}"
OUT_S3="${REPLAY_OUT_S3:-s3://ds-oba/replay/inference-outputs/$LABEL/}"
OUT_ARGS=(-Doba.replay.output.dir="$OUT_DIR")

upload_parts() {  # moves completed parts (no .open suffix); prints what it moved
  local f
  for f in "$OUT_DIR"/inferred-*.ndjson "$OUT_DIR"/inferred-*.ndjson.gz; do
    [ -f "$f" ] || continue
    aws s3 mv "$f" "$OUT_S3" --only-show-errors && echo "uploaded $(basename "$f")"
  done
}

FIFO="${OBA_REPLAY_FIFO:-/tmp/oba-replay-$$.fifo}"
FEEDLOG="/tmp/oba-replay-feed.log"
rm -f "$FIFO"; mkfifo "$FIFO" || { echo "cannot create fifo $FIFO" >&2; exit 2; }

FEED_PID=""; UPLOAD_PID=""
cleanup() {
  [ -n "$FEED_PID" ] && kill "$FEED_PID" 2>/dev/null
  [ -n "$UPLOAD_PID" ] && kill "$UPLOAD_PID" 2>/dev/null
  rm -f "$FIFO"
}
trap cleanup EXIT INT TERM

echo "sources : ${#SOURCES[@]}"
for s in "${SOURCES[@]}"; do echo "          $s"; done
echo "fifo    : $FIFO"
echo "feed log: $FEEDLOG"
echo "output  : $OUT_DIR"
[ "$OUT_S3" != "none" ] && echo "       -> $OUT_S3"
echo

# Feeder: data on stdout to the FIFO, progress on stderr to the log. Opening the FIFO for write blocks
# until the engine opens it for read, which is after the bundle loads, so nothing buffers during load.
(
  start=$(date +%s)
  for s in "${SOURCES[@]}"; do
    t0=$(date +%s)
    case "$s" in
      s3://*.gz) aws s3 cp "$s" - | gzip -dc ;;
      s3://*)    aws s3 cp "$s" - ;;
      *.gz)      gzip -dc "$s" ;;
      *)         cat "$s" ;;
    esac
    echo "fed $s in $(( $(date +%s) - t0 ))s" >&2
  done
  echo "feed complete in $(( $(date +%s) - start ))s" >&2
) > "$FIFO" 2> "$FEEDLOG" &
FEED_PID=$!

echo "Bundle load takes a few minutes before the first record is read."
echo
# Rolling uploader, so a multi-day run's completed parts leave the disk while the engine works.
if [ "$OUT_S3" != "none" ]; then
  ( while :; do sleep 20; upload_parts; done ) > /tmp/oba-replay-upload.log 2>&1 &
  UPLOAD_PID=$!
fi

"$HERE/replay.sh" "$FIFO" "${OUT_ARGS[@]}" ${MVN_EXTRA[@]+"${MVN_EXTRA[@]}"} \
  -Dreplay.exitWhenDone="${REPLAY_EXIT_WHEN_DONE:-true}"
rc=$?

[ -n "$UPLOAD_PID" ] && { kill "$UPLOAD_PID" 2>/dev/null; wait "$UPLOAD_PID" 2>/dev/null; }
wait "$FEED_PID" 2>/dev/null
echo
if [ "$OUT_S3" != "none" ]; then
  echo "== upload (final sweep) =="
  upload_parts | sed 's/^/  /'
  leftover=$(ls "$OUT_DIR" 2>/dev/null | grep -v '\.open$' | head -5)
  [ -z "$leftover" ] && echo "  all parts uploaded to $OUT_S3" \
                     || { echo "  NOT uploaded (kept in $OUT_DIR):"; echo "$leftover" | sed 's/^/    /'; }
fi
echo
echo "== feed =="
sed 's/^/  /' "$FEEDLOG" 2>/dev/null
exit $rc
