#!/usr/bin/env bash
# The stock predictions archiver on the shadow output port, uploading to ds-oba. The exports come after
# env-common.sh because the host's env-local.sh sets the arm's own prefix, where shadow output must never land.
set -euo pipefail
source /opt/oba/env-common.sh
export OBA_ARCHIVER_ZMQ_PORT=5578
export OBA_ARCHIVER_DIR=/data/shadow/predictions-archive
export OBA_ARCHIVER_S3_BUCKET=ds-oba
export OBA_ARCHIVER_S3_PREFIX=replay/prediction/dispatcher-actions/shadow/oba-nyc-filtered/archive
export OBA_ARCHIVER_UPLOAD=true
export OBA_ARCHIVER_STOP_ID_AGENCY=MTA
mkdir -p "$OBA_ARCHIVER_DIR"
exec python3 /opt/oba/predictions-archiver.py
