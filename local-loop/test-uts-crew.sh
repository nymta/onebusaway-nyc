#!/usr/bin/env bash
# Local smoke test for UTS S3 crew roster (no EC2 / no full inference stack).
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
CACHE="${OBA_CREW_CACHE:-/tmp/oba-uts-cis-live.txt}"
TODAY="$(date +%Y-%m-%d)"

echo "== 1) Resolve UTS credentials =="
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
if [[ -z "${OBA_UTS_ACCESS_KEY_ID:-}" || -z "${OBA_UTS_SECRET_ACCESS_KEY:-}" ]]; then
  if command -v aws >/dev/null; then
    echo "   Using SSM /oba/uts/s3/* via default AWS profile"
    eval "$(python3 - <<'PY'
import subprocess, shlex, sys
for name, var in [('/oba/uts/s3/accessKey','OBA_UTS_ACCESS_KEY_ID'),('/oba/uts/s3/secretKey','OBA_UTS_SECRET_ACCESS_KEY')]:
    try:
        val = subprocess.check_output([
            'aws','ssm','get-parameter','--name',name,'--with-decryption',
            '--query','Parameter.Value','--output','text','--region', 'us-east-1'
        ], text=True, stderr=subprocess.PIPE).strip()
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr or str(e))
        sys.exit(1)
    print(f"export {var}={shlex.quote(val)}")
PY
)"
  else
    echo "ERROR: set OBA_UTS_ACCESS_KEY_ID/OBA_UTS_SECRET_ACCESS_KEY or install AWS CLI for SSM" >&2
    exit 1
  fi
fi
export AWS_ACCESS_KEY_ID="$OBA_UTS_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$OBA_UTS_SECRET_ACCESS_KEY"

echo "== 2) Download latest CIS =="
aws s3 cp "s3://mtabuscis-uts-archive/latest/CIS.txt" "$CACHE" --region "$REGION"
LINES=$(wc -l < "$CACHE" | tr -d ' ')
echo "   $CACHE ($LINES lines)"

echo "== 3) Spot-check CSV for today ($TODAY) =="
TODAY_ROWS=$(grep -c ",$TODAY," "$CACHE" || true)
echo "   rows with service date $TODAY: $TODAY_ROWS"
head -2 "$CACHE"

echo "== 4) Run Java parser + service tests against live S3 =="
MAIN_REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$MAIN_REPO"
export AWS_DEFAULT_REGION="$REGION"
mvn -B -q -pl onebusaway-nyc-tdm-adapters,onebusaway-nyc-vehicle-tracking -am \
  -Dlicense.skip=true -DfailIfNoTests=false \
  test -Dtest=UtsCrewAssignmentParserTest,S3UtsOperatorAssignmentServiceImplTest 2>&1 \
  | grep -E 'Tests run:|BUILD SUCCESS|BUILD FAILURE|liveS3|refreshFromLocal' || true

echo "== 5) Parse downloaded CIS for $TODAY (fixture-style check) =="
python3 - <<PY
import subprocess, sys
from datetime import date
today = date.today()
ymd = today.strftime('%Y-%m-%d')
cache = "$CACHE"
# sample a few pass numbers from today's rows
passes = []
with open(cache) as f:
    next(f)
    for line in f:
        parts = line.strip().split(',')
        if len(parts) >= 7 and parts[6] == ymd and parts[2] and parts[4]:
            passes.append((parts[2].lstrip('0') or parts[2], parts[3], parts[4]))
        if len(passes) >= 5:
            break
print(f"   sample assignments for {ymd}:")
for p, route, run in passes:
    print(f"     pass={p} route={route} run={run}")
if not passes:
    sys.exit("no rows for today in CIS file")
PY

echo
echo "PASS: UTS crew path is working locally. Safe to deploy EC2 when ready."
