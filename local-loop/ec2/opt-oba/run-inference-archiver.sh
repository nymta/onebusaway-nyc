#!/usr/bin/env bash
# Archive the post-inference ZMQ stream (:5567) to hourly queueInference zips on S3.
# Passive SUB on the SimpleBroker fan-out; cannot affect inference or predictions.
set -euo pipefail
source /opt/oba/env-common.sh

export OBA_INF_ARCHIVER_DIR="${OBA_INF_ARCHIVER_DIR:-/data/inference-archive}"
export OBA_INF_ARCHIVER_S3_BUCKET="${OBA_INF_ARCHIVER_S3_BUCKET:-mtalirr}"
export OBA_INF_ARCHIVER_S3_PREFIX="${OBA_INF_ARCHIVER_S3_PREFIX:-oba-ec2-inference}"
export OBA_INF_ARCHIVER_UPLOAD="${OBA_INF_ARCHIVER_UPLOAD:-true}"

# Uploads use the instance profile (oba-nyc-ec2-role); its policy must allow
# s3:PutObject on s3://$OBA_INF_ARCHIVER_S3_BUCKET/$OBA_INF_ARCHIVER_S3_PREFIX/*.

mkdir -p "$OBA_INF_ARCHIVER_DIR"

# Install deps once (pyzmq only); safe to re-run.
python3 -m pip install --user -q pyzmq 2>/dev/null || \
  python3 -m pip install -q pyzmq

exec python3 /opt/oba/inference-archiver.py
