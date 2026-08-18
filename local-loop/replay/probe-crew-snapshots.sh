#!/usr/bin/env bash
# Which service date does each hourly UTS snapshot actually contain?
#
# Usage: probe-crew-snapshots.sh [YYYY-MM-DD] [hour ...]
#   default date: 2026-07-28 (the determinism fixture's date)
#   default hours: every hour present in the prefix
#
# Answers the question the code cares about: UtsCrewAssignmentParser filters on the CSV's DATE
# column, not on the object key, so the prefix date and the content date need not agree.
#
# Reads OBA_UTS_ACCESS_KEY_ID / OBA_UTS_SECRET_ACCESS_KEY if set, otherwise resolves them from SSM
# with the default profile, same as test-uts-crew.sh. Credentials stay in this process.
set -uo pipefail

DATE="${1:-2026-07-28}"; [ $# -gt 0 ] && shift || true
BUCKET="${OBA_CREW_BUCKET:-mtabuscis-uts-archive}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

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

KEYS=$(aws s3api list-objects-v2 --bucket "$BUCKET" --prefix "$DATE/" \
         --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' | grep '/crew_' | sort)
[ -n "$KEYS" ] || { echo "no crew_* objects under s3://$BUCKET/$DATE/" >&2; exit 2; }

if [ $# -gt 0 ]; then
  WANT=""
  for h in "$@"; do WANT="$WANT$(printf '%s\n' "$KEYS" | grep "_${h}\.csv$")"$'\n'; done
  KEYS=$(printf '%s' "$WANT" | grep . | sort)
fi

echo "s3://$BUCKET/$DATE/  ($(printf '%s\n' "$KEYS" | grep -c .) crew snapshots)"
echo
printf '%-34s %-22s %9s  %s\n' "KEY" "LASTMODIFIED (UTC)" "ROWS" "DATE COLUMN -> COUNT"
printf '%-34s %-22s %9s  %s\n' "---" "------------------" "----" "--------------------"

while IFS= read -r key; do
  [ -n "$key" ] || continue
  # LastModified from the API is an explicit instant, so it settles the timezone question that the
  # filename cannot.
  lm=$(aws s3api head-object --bucket "$BUCKET" --key "$key" \
         --query LastModified --output text 2>/dev/null)
  body=$(aws s3 cp "s3://$BUCKET/$key" - 2>/dev/null)
  rows=$(printf '%s\n' "$body" | tail -n +2 | grep -c .)
  # DATE is field 7 per the header DEPOT,AUTH_ID,PASS_NUMBER,ROUTE,RUN_NUMBER,SERV_ID,DATE,TIMESTAMP;
  # resolved by name rather than position in case the column order changes.
  dates=$(printf '%s\n' "$body" | awk -F',' '
    NR==1 { for (i=1; i<=NF; i++) if ($i=="DATE") c=i; next }
    c && $c != "" { n[$c]++ }
    END { for (d in n) printf "%s=%d ", d, n[d] }' | tr ' ' '\n' | sort | tr '\n' ' ')
  printf '%-34s %-22s %9s  %s\n' "${key#$DATE/}" "$lm" "$rows" "$dates"
done <<< "$KEYS"

echo
echo "If DATE == $DATE on every row, the prefix is the service date and as-of selection is simple."
echo "If it flips partway down, the prefix is the generation date and the roster for $DATE begins"
echo "at the first snapshot whose DATE column says $DATE."
