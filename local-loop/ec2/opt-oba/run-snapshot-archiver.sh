#!/usr/bin/env bash
# Keep every version of a dispatcher system's live S3 snapshot. Each source overwrites one object
# in place and keeps no history, so every new version is copied, gzipped, to
#   s3://ds-oba/replay/prediction/dispatcher-actions/<name>-live/<UTC date>/<UTC time>Z.csv.gz
# named by the source object's LastModified (UTC keys, unlike data-archiver's ET slots).
#
#   run-snapshot-archiver.sh bustrek    Bus Trek's service-day rows, rewritten about once a minute
#   run-snapshot-archiver.sh yardtrek   Yard Trek's block assignments, rewritten about every 30 s
#
# Reading the source assumes ds-bustrek-yardtrek-s3-read (iam/oba-yardtrek-live-read.json grants
# oba-nyc-ec2-role the assume); writing ds-oba uses the instance profile directly. Defaults can be
# overridden with OBA_SNAP_{SRC_BUCKET,SRC_KEY,DST,POLL_SECONDS,ROLE_ARN}.
set -uo pipefail

NAME="${1:-}"
case "$NAME" in
  bustrek)  src_bucket=mtabustrekdataexports; src_key=bustrek-digital-services/bustrek_latest.csv; poll=15;;
  yardtrek) src_bucket=yardtrek-digital-services; src_key=yardtrek-blocks_latest.csv; poll=10;;
  *) echo "usage: $0 bustrek|yardtrek" >&2; exit 2;;
esac
# Polling well under the source's rewrite interval copies each version promptly and cannot skip one.
SRC_BUCKET="${OBA_SNAP_SRC_BUCKET:-$src_bucket}"
SRC_KEY="${OBA_SNAP_SRC_KEY:-$src_key}"
DST="${OBA_SNAP_DST:-s3://ds-oba/replay/prediction/dispatcher-actions/$NAME-live}"
POLL_SECONDS="${OBA_SNAP_POLL_SECONDS:-$poll}"
ROLE_ARN="${OBA_SNAP_ROLE_ARN:-arn:aws:iam::032610139471:role/ds-bustrek-yardtrek-s3-read}"

WORK="$(mktemp -d "/tmp/$NAME-live.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# A profile that assumes the read role from the instance's own credentials; the CLI refreshes it.
cat > "$WORK/aws-config" <<CFG
[default]
region = us-east-1
[profile src]
role_arn = $ROLE_ARN
credential_source = Ec2InstanceMetadata
region = us-east-1
CFG
export AWS_CONFIG_FILE="$WORK/aws-config"

log() { echo "$NAME-live: $*"; }
stamp_of() { date -u -d "$1" +%Y-%m-%dT%H-%M-%SZ; }

# A failure that repeats (an expired or revoked assume-role, say) is logged once per ERROR_LOG_SECONDS
# and slows the loop to BACKOFF_SECONDS until a poll succeeds again.
ERROR_LOG_SECONDS=600; BACKOFF_SECONDS=60
fails=0; last_error_log=0
fail() {
  errors=$((errors + 1)); fails=$((fails + 1))
  local now; now=$(date +%s)
  if [ "$fails" -eq 1 ] || [ $((now - last_error_log)) -ge "$ERROR_LOG_SECONDS" ]; then
    log "$* (failure $fails in a row)" >&2; last_error_log=$now
  fi
}

last_etag=""; copied=0; errors=0; last_report=$(date +%s); last=""
log "s3://$SRC_BUCKET/$SRC_KEY -> $DST every ${POLL_SECONDS}s"

while true; do
  if head="$(aws --profile src s3api head-object --bucket "$SRC_BUCKET" --key "$SRC_KEY" \
               --query '[ETag,LastModified]' --output text 2>&1)"; then
    if [ "$(cut -f1 <<<"$head")" = "$last_etag" ]; then
      fails=0
    # get-object can return a newer version than the head saw: name the copy by what was fetched
    elif ! out="$(aws --profile src s3api get-object --bucket "$SRC_BUCKET" --key "$SRC_KEY" \
                    --query '[ETag,LastModified]' --output text "$WORK/snap.csv" 2>&1)"; then
      fail "get failed: $out"
    elif ! stamp="$(stamp_of "$(cut -f2 <<<"$out")")" || [ -z "$stamp" ]; then
      fail "unparseable LastModified: $out"
    elif ! gzip -c "$WORK/snap.csv" > "$WORK/snap.csv.gz"; then
      rm -f "$WORK/snap.csv.gz"; fail "gzip failed for $stamp"
    elif key="$DST/${stamp:0:10}/$stamp.csv.gz"; ! aws s3 cp --quiet "$WORK/snap.csv.gz" "$key" --content-type application/gzip; then
      fail "upload failed for $key"
    else
      last_etag="$(cut -f1 <<<"$out")"; copied=$((copied + 1)); last=$stamp; fails=0
    fi
  else
    fail "head failed: $head"
  fi
  now=$(date +%s)
  if [ $((now - last_report)) -ge 600 ]; then
    log "copied=$copied errors=$errors last=${last:-none} in the last $(( (now - last_report) / 60 )) min"
    copied=0; errors=0; last_report=$now
  fi
  if [ "$fails" -ge 3 ]; then sleep "$BACKOFF_SECONDS"; else sleep "$POLL_SECONDS"; fi
done
