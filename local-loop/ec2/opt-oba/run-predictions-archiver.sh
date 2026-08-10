#!/usr/bin/env bash
# Archive predictions ZMQ (:5568) to hourly queuePredictions zips on S3.
set -euo pipefail
source /opt/oba/env-common.sh

export OBA_ARCHIVER_DIR="${OBA_ARCHIVER_DIR:-/data/predictions-archive}"
# Dedicated single-purpose bucket for sharing with D&A
export OBA_ARCHIVER_S3_BUCKET="${OBA_ARCHIVER_S3_BUCKET:-oba-ec2-predictions}"
export OBA_ARCHIVER_S3_PREFIX="${OBA_ARCHIVER_S3_PREFIX-}"
export OBA_ARCHIVER_UPLOAD="${OBA_ARCHIVER_UPLOAD:-true}"
# Force prod's single-namespace stopId prefix (`MTA_<id>`) over the bundle's per-agency
# `MTA NYCT_` / `MTABC_` — the only format difference from prod's archive. Empty = archive verbatim.
export OBA_ARCHIVER_STOP_ID_AGENCY="${OBA_ARCHIVER_STOP_ID_AGENCY-MTA}"

# Optional SSM-backed static creds for the archive bucket. Unset in this deployment, so the
# AWS CLI uses the instance profile (oba-nyc-ec2-role).
if aws ssm get-parameter --name /oba/predictions/s3-archive/access_key_id --query 'Parameter.Name' --output text >/dev/null 2>&1; then
  export AWS_ACCESS_KEY_ID
  export AWS_SECRET_ACCESS_KEY
  AWS_ACCESS_KEY_ID="$(gp /oba/predictions/s3-archive/access_key_id)"
  AWS_SECRET_ACCESS_KEY="$(gp /oba/predictions/s3-archive/secret_access_key)"
fi

mkdir -p "$OBA_ARCHIVER_DIR"

# Install deps once (pyzmq + gtfs-realtime-bindings); safe to re-run.
python3 -m pip install --user -q pyzmq gtfs-realtime-bindings protobuf 2>/dev/null || \
  python3 -m pip install -q pyzmq gtfs-realtime-bindings protobuf

exec python3 /opt/oba/predictions-archiver.py
