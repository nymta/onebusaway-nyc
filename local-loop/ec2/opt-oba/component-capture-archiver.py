#!/usr/bin/env python3
"""Upload closed component-capture CSV files to S3 and delete the local copies.

PredictionsFilePublisher (Java) writes predictions.componentCapture.outputFile and rotates
onto a new, uniquely-timestamped file roughly every hour -- see PredictionsFilePublisher.java's
openForNewPeriod(). This never touches the newest file (still being written); every older one
it finds is gzipped, uploaded, and deleted locally. Run on a timer (oba-component-capture-
archiver.timer), not as a daemon -- there is no stream to subscribe to, just a directory to
sweep periodically.

Environment:
  OBA_COMPONENT_CAPTURE_DIR     default /data/predictions-archive
  OBA_COMPONENT_CAPTURE_GLOB    default component-capture.csv.*
  OBA_ARCHIVER_S3_BUCKET        default oba-ec2-predictions (same bucket as predictions-archiver.py)
  OBA_ARCHIVER_S3_PREFIX        no default -- same per-instance prefix as predictions-archiver.py;
                                 refuses to run without one for the same reason that script does
                                 (no host token in the object name -- two instances sharing a
                                 prefix would silently overwrite each other's uploads).
  OBA_ARCHIVER_S3_RETRIES       default 3
  OBA_COMPONENT_CAPTURE_UPLOAD  default true (false = local files only, for testing)
"""
from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LOG = logging.getLogger("component-capture-archiver")

CAPTURE_DIR = Path(os.environ.get("OBA_COMPONENT_CAPTURE_DIR", "/data/predictions-archive"))
CAPTURE_GLOB = os.environ.get("OBA_COMPONENT_CAPTURE_GLOB", "component-capture.csv.*")
S3_BUCKET = os.environ.get("OBA_ARCHIVER_S3_BUCKET", "oba-ec2-predictions")
S3_PREFIX = os.environ.get("OBA_ARCHIVER_S3_PREFIX", "").strip("/")
S3_RETRIES = int(os.environ.get("OBA_ARCHIVER_S3_RETRIES", "3"))
UPLOAD_ENABLED = os.environ.get("OBA_COMPONENT_CAPTURE_UPLOAD", "true").lower() not in ("0", "false", "no")


def s3_key_for(gz_name: str) -> str:
    prefix = f"{S3_PREFIX}/component-capture/" if S3_PREFIX else "component-capture/"
    return f"{prefix}{gz_name}"


def gzip_file(path: Path) -> Path:
    gz_path = path.with_name(path.name + ".gz")
    with path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return gz_path


def upload_and_remove(path: Path) -> None:
    gz_path = gzip_file(path)
    if not UPLOAD_ENABLED:
        LOG.info("upload disabled; keeping %s (original left in place)", gz_path)
        return

    dest = f"s3://{S3_BUCKET}/{s3_key_for(gz_path.name)}"
    last_error: Exception | None = None
    for attempt in range(S3_RETRIES):
        try:
            subprocess.run(
                ["aws", "s3", "cp", str(gz_path), dest, "--only-show-errors"],
                check=True, capture_output=True, text=True,
            )
            LOG.info("uploaded %s -> %s", gz_path.name, dest)
            gz_path.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            delay = 2 ** attempt
            LOG.warning(
                "upload failed (attempt %d/%d) for %s: %s; retry in %ds",
                attempt + 1, S3_RETRIES, gz_path.name,
                (exc.stderr or str(exc)).strip()[:200], delay,
            )
            time.sleep(delay)
    # Leave the original .csv for next run's retry; don't leave a half-uploaded .gz beside it.
    gz_path.unlink(missing_ok=True)
    LOG.error("giving up on %s after %d attempts: %s", path.name, S3_RETRIES, last_error)


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    if not UPLOAD_ENABLED and not S3_PREFIX:
        pass  # prefix only matters when actually uploading
    elif UPLOAD_ENABLED and not S3_PREFIX and os.environ.get("OBA_ARCHIVER_ALLOW_ROOT") != "1":
        LOG.error(
            "OBA_ARCHIVER_S3_PREFIX is empty -- set it in /opt/oba/env-local.sh, matching "
            "predictions-archiver.py's prefix for this instance. Override with "
            "OBA_ARCHIVER_ALLOW_ROOT=1 only to deliberately write to the bucket root."
        )
        return 1

    if not CAPTURE_DIR.exists():
        LOG.info("%s does not exist yet; nothing to do", CAPTURE_DIR)
        return 0

    files = sorted(CAPTURE_DIR.glob(CAPTURE_GLOB), key=lambda p: p.stat().st_mtime)
    if not files:
        LOG.info("no component-capture files found in %s", CAPTURE_DIR)
        return 0

    # Never touch the newest file -- PredictionsFilePublisher may still be writing it.
    newest = files[-1]
    closed = files[:-1]
    LOG.info("%d closed file(s) to archive, leaving %s (currently being written)", len(closed), newest.name)
    for path in closed:
        try:
            upload_and_remove(path)
        except Exception as exc:
            LOG.error("failed to archive %s: %s", path.name, exc)
    return 0


if __name__ == "__main__":
    sys.exit(run())
