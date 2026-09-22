#!/usr/bin/env bash
# Upload closed component-capture CSV files to S3 (see component-capture-archiver.py).
# Run on a timer (oba-component-capture-archiver.timer), not as a daemon.
set -euo pipefail
source /opt/oba/env-common.sh

export OBA_COMPONENT_CAPTURE_DIR="${OBA_COMPONENT_CAPTURE_DIR:-/data/predictions-archive}"
export OBA_ARCHIVER_S3_BUCKET="${OBA_ARCHIVER_S3_BUCKET:-oba-ec2-predictions}"
export OBA_ARCHIVER_S3_PREFIX="${OBA_ARCHIVER_S3_PREFIX-}"

mkdir -p "$OBA_COMPONENT_CAPTURE_DIR"

exec python3 /opt/oba/component-capture-archiver.py
