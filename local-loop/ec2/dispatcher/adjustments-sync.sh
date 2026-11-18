#!/usr/bin/env bash
# Hourly: the engine's decision logs and skip-span file to S3, for the daily scoring.
set -euo pipefail
source /opt/oba/env-common.sh
P=s3://ds-oba/replay/prediction/dispatcher-actions/live/${OBA_ARCHIVER_S3_PREFIX:?}
aws s3 sync /data/adjustments/logs/ "$P/logs/" --no-progress --only-show-errors
aws s3 cp /data/adjustments/skip-spans.csv "$P/spans/skip-spans.csv" --no-progress --only-show-errors || true
( cd /opt/oba/onebusaway-nyc-predictions && git rev-parse HEAD ) | aws s3 cp - "$P/COMMIT" --no-progress --only-show-errors || true
