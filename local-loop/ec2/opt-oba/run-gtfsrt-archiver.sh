#!/usr/bin/env bash
# Archive this host's published GTFS-RT feeds (:8083) to hourly zips under <prefix>/gtfsrt/ on S3.
set -euo pipefail
source /opt/oba/env-common.sh

export OBA_ARCHIVER_S3_BUCKET="${OBA_ARCHIVER_S3_BUCKET:-oba-ec2-predictions}"
export OBA_ARCHIVER_S3_PREFIX="${OBA_ARCHIVER_S3_PREFIX-}"
export OBA_ARCHIVER_UPLOAD="${OBA_ARCHIVER_UPLOAD:-true}"
export OBA_GTFSRT_ARCHIVER_DIR="${OBA_GTFSRT_ARCHIVER_DIR:-/data/gtfsrt-archive}"

# Same rule as the predictions archiver: hour keys carry no host token, so two hosts sharing a
# prefix overwrite each other's zips.
if [ "$OBA_ARCHIVER_UPLOAD" != "false" ] && [ -z "$OBA_ARCHIVER_S3_PREFIX" ]; then
  echo "FATAL: OBA_ARCHIVER_S3_PREFIX is empty -- set it in /opt/oba/env-local.sh" >&2
  exit 1
fi

mkdir -p "$OBA_GTFSRT_ARCHIVER_DIR"
exec python3 /opt/oba/gtfsrt-archiver.py
