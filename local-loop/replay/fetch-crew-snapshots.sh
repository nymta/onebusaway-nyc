#!/usr/bin/env bash
# Prefetch the UTS crew snapshots a replay window needs, so the run itself makes no S3 calls.
#
# Usage: fetch-crew-snapshots.sh <first-utc-date> [last-utc-date] [-o outdir]
#   dates are YYYY-MM-DD and name bucket prefixes, which are UTC generation dates
#   default outdir: $OBA_CREW_SNAPSHOTS or /var/lib/obanyc/uts-snapshots
#
# Then point the engine at it:
#   -Doba.crew.snapshotDir=<outdir>
# The engine picks the newest snapshot at or before the replay clock, so it needs the day BEFORE the
# window too: a replay starting at 00:05Z resolves to the previous day's 23:10Z snapshot. This script
# fetches that extra day for you.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOOP="$(cd "$HERE/.." && pwd)"

BUCKET="${OBA_CREW_BUCKET:-mtabuscis-uts-archive}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
OUT="${OBA_CREW_SNAPSHOTS:-/var/lib/obanyc/uts-snapshots}"

FIRST=""; LAST=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) OUT="$2"; shift 2 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *)  if [ -z "$FIRST" ]; then FIRST="$1"; elif [ -z "$LAST" ]; then LAST="$1"; fi; shift ;;
  esac
done
[ -n "$FIRST" ] || { sed -n '2,14p' "$0"; exit 2; }
LAST="${LAST:-$FIRST}"

date_ok() { [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; }
date_ok "$FIRST" || { echo "bad date: $FIRST" >&2; exit 2; }
date_ok "$LAST"  || { echo "bad date: $LAST" >&2; exit 2; }

# One day earlier than asked, for the as-of lookback at the start of the window.
FROM="$(date -j -v-1d -f %Y-%m-%d "$FIRST" +%Y-%m-%d 2>/dev/null)" \
  || FROM="$(date -d "$FIRST -1 day" +%Y-%m-%d)"

# Lazy: a window whose days are all cached needs no credentials at all.
CREDS_DONE=0
ensure_creds() {
  [ "$CREDS_DONE" = "1" ] && return 0
  if [[ -z "${OBA_UTS_ACCESS_KEY_ID:-}" || -z "${OBA_UTS_SECRET_ACCESS_KEY:-}" ]]; then
    echo "resolving UTS credentials from SSM via default profile"
    unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
    AK=$(aws ssm get-parameter --name /oba/uts/s3/accessKey --with-decryption \
           --query Parameter.Value --output text --region "$REGION") || exit 1
    SK=$(aws ssm get-parameter --name /oba/uts/s3/secretKey --with-decryption \
           --query Parameter.Value --output text --region "$REGION") || exit 1
    OBA_UTS_ACCESS_KEY_ID="$AK"; OBA_UTS_SECRET_ACCESS_KEY="$SK"; unset AK SK
  fi
  unset AWS_PROFILE AWS_SESSION_TOKEN
  export AWS_ACCESS_KEY_ID="$OBA_UTS_ACCESS_KEY_ID"
  export AWS_SECRET_ACCESS_KEY="$OBA_UTS_SECRET_ACCESS_KEY"
  export AWS_DEFAULT_REGION="$REGION"
  CREDS_DONE=1
}

mkdir -p "$OUT" || { echo "cannot create $OUT" >&2; exit 2; }
echo "bucket : s3://$BUCKET"
echo "window : $FROM .. $LAST  (lookback day $FROM included)"
echo "outdir : $OUT"
echo

CUR="$FROM"; DAYS=0; FILES=0; FAILED_DAYS=0
while :; do
  if [ -n "$(find "$OUT/$CUR" -name 'crew_*.csv' -print -quit 2>/dev/null)" ]; then
    # Already fetched; a re-run for an overlapping window costs nothing.
    n=$(find "$OUT/$CUR" -name 'crew_*.csv' | wc -l | tr -d ' ')
    echo "  $CUR  cached ($n snapshots)"
    FILES=$((FILES + n)); DAYS=$((DAYS + 1))
  else
    # crew_* only: popi_* is pull-out data, which nothing on the inference path reads today.
    ensure_creds
    if aws s3 cp "s3://$BUCKET/$CUR/" "$OUT/$CUR/" --recursive \
      --exclude '*' --include 'crew_*.csv' --only-show-errors; then
      n=$(find "$OUT/$CUR" -name 'crew_*.csv' 2>/dev/null | wc -l | tr -d ' ')
      if [ "$n" = "0" ]; then
        echo "  $CUR  no crew snapshots"
        rmdir "$OUT/$CUR" 2>/dev/null
      else
        echo "  $CUR  $n snapshots"
        FILES=$((FILES + n)); DAYS=$((DAYS + 1))
      fi
    else
      # A real S3/network failure, not "this day has no snapshots" - aws s3 cp --recursive still
      # exits 0 when a day is legitimately empty, so a nonzero exit here means the copy itself broke.
      echo "  $CUR  FETCH FAILED (s3 cp returned nonzero)" >&2
      FAILED_DAYS=$((FAILED_DAYS + 1))
    fi
  fi
  [ "$CUR" = "$LAST" ] && break
  # Computed into a fresh variable: assigning the first attempt directly to CUR clobbers it with ""
  # on the platform where that attempt fails, and the fallback then reads the clobbered value.
  NEXT="$(date -d "$CUR +1 day" +%Y-%m-%d 2>/dev/null \
    || date -j -v+1d -f %Y-%m-%d "$CUR" +%Y-%m-%d)"
  CUR="$NEXT"
done

[ "$FAILED_DAYS" -eq 0 ] || { echo "$FAILED_DAYS day(s) failed to fetch; aborting rather than replay on a partial roster" >&2; exit 1; }
[ "$FILES" -gt 0 ] || { echo "nothing fetched" >&2; exit 2; }

# A manifest so a replay result can be tied to the roster that produced it, like the fixture's.
MAN="$OUT/manifest.json"
python3 - "$OUT" "$BUCKET" "$FROM" "$LAST" > "$MAN" <<'PY'
import hashlib, json, os, sys
out, bucket, first, last = sys.argv[1:5]
files = []
for root, _, names in os.walk(out):
    for n in sorted(names):
        if n.startswith("crew_") and n.endswith(".csv"):
            p = os.path.join(root, n)
            with open(p, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            files.append({"key": os.path.relpath(p, out), "sha256": digest,
                          "bytes": os.path.getsize(p)})
print(json.dumps({"bucket": bucket, "utc_dates": [first, last],
                  "snapshots": len(files), "files": files}, indent=2))
PY

echo
echo "$FILES snapshots over $DAYS day(s); manifest $MAN"
echo "du: $(du -sh "$OUT" | cut -f1)"
echo
echo "Run with:  -Doba.crew.snapshotDir=$OUT"
echo "The engine selects as-of the replay clock, so no S3 call happens during the run."
