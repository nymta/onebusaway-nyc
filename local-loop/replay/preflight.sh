#!/usr/bin/env bash
# Check every credential surface a replay run touches, before spending a bundle load on finding out.
#
# Usage: preflight.sh   (uses the default AWS profile, same as the replay scripts)
#
#   1. GPS archive read   s3://mtalirr/data-archiver/bustechGps/   (feeder)
#   2. crew chain         SSM /oba/uts/s3/* -> s3://mtabuscis-uts-archive   (fetch-crew-snapshots.sh)
#   3. output write       s3://ds-oba/replay/inference-outputs/   (S3OutputQueueSenderServiceImpl)
set -uo pipefail
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
FAIL=0
ok()   { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n        %s\n' "$1" "$2"; FAIL=1; }

# 1. archive read AND list - the replay scripts need both (list for --prefix, get for the download)
if aws s3api head-object --bucket mtalirr \
     --key data-archiver/bustechGps/2026-07-28/08-45.jsonl.gz --region "$REGION" >/dev/null 2>&1; then
  ok "read s3://mtalirr/data-archiver/bustechGps/"
else
  bad "read s3://mtalirr/data-archiver/bustechGps/" \
      "default profile cannot read the archive; re-auth or check the profile"
fi
if aws s3api list-objects-v2 --bucket mtalirr --prefix data-archiver/bustechGps/ \
     --max-items 1 --region "$REGION" >/dev/null 2>&1; then
  ok "list s3://mtalirr/data-archiver/"
else
  bad "list s3://mtalirr/data-archiver/" \
      "s3:ListBucket on arn:aws:s3:::mtalirr (prefix data-archiver/*) is missing; --prefix runs will fail"
fi

# 2. crew chain: SSM parameters, then the UTS bucket with the keys they hold
AK=$(aws ssm get-parameter --name /oba/uts/s3/accessKey --with-decryption \
       --query Parameter.Value --output text --region "$REGION" 2>/dev/null)
SK=$(aws ssm get-parameter --name /oba/uts/s3/secretKey --with-decryption \
       --query Parameter.Value --output text --region "$REGION" 2>/dev/null)
if [ -n "$AK" ] && [ -n "$SK" ]; then
  ok "read SSM /oba/uts/s3/*"
  if AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_SESSION_TOKEN="" \
       aws s3api head-object --bucket mtabuscis-uts-archive --key latest/CIS.txt \
       --region "$REGION" >/dev/null 2>&1; then
    ok "read s3://mtabuscis-uts-archive with the UTS keys"
  else
    bad "read s3://mtabuscis-uts-archive with the UTS keys" "keys resolved but the bucket read failed"
  fi
else
  bad "read SSM /oba/uts/s3/*" \
      "cannot resolve UTS keys; crew fetch will fail (cached days still work)"
fi
unset AK SK

# 3. output write: put a marker where the sender will write, then remove it
MARKER="replay/inference-outputs/_preflight-$(date -u +%Y%m%dT%H%M%SZ)"
if printf 'preflight\n' | aws s3 cp - "s3://ds-oba/$MARKER" --region "$REGION" >/dev/null 2>&1; then
  ok "write s3://ds-oba/replay/inference-outputs/"
  aws s3 rm "s3://ds-oba/$MARKER" --region "$REGION" >/dev/null 2>&1 \
    || printf '        (marker %s left behind; delete failed)\n' "$MARKER"
else
  bad "write s3://ds-oba/replay/inference-outputs/" \
      "sender uploads will fail; parts stay in the local spool (REPLAY_OUT_S3=none silences this)"
fi

exit "$FAIL"
