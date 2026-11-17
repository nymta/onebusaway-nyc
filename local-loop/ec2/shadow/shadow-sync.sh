#!/usr/bin/env bash
# Hourly: the adj logs and the span file to S3 beside the shadow archive.
set -euo pipefail
source /opt/oba/env-common.sh
P=s3://ds-oba/replay/prediction/dispatcher-actions/shadow/oba-nyc-filtered
aws s3 sync /data/shadow/logs/ "$P/logs/" --no-progress --only-show-errors
aws s3 cp /data/shadow/spans/skip-spans.csv "$P/spans/skip-spans.csv" --no-progress --only-show-errors || true
aws s3 cp /data/shadow/COMMIT "$P/COMMIT" --no-progress --only-show-errors
