#!/usr/bin/env python3
"""Archive this host's published GTFS-RT feeds to hourly zips on S3.

Polls the gtfsrt webapp (default http://127.0.0.1:8083) for /tripUpdates and /vehiclePositions,
stores each response's raw protobuf bytes unchanged, and uploads one zip per UTC hour:

  s3://<bucket>/<prefix>/gtfsrt/gtfsrt_YYYY-MM-DD_HH-00-00.zip
      tripUpdates/YYYYMMDDTHHMMSSZ.pb
      vehiclePositions/YYYYMMDDTHHMMSSZ.pb

This is the feed as consumers see it, including entities the gtfsrt webapp adds itself (CANCELED
trips), which the predictions archiver's ZMQ stream does not contain.

Polls land in <dir>/<hour>/<feed>/ as they arrive. Every hour directory other than the current one
is zipped and uploaded, so a restart resumes the current hour and uploads any hour left behind.

Environment:
  OBA_GTFSRT_ARCHIVER_URL        default http://127.0.0.1:8083
  OBA_GTFSRT_ARCHIVER_FEEDS      default tripUpdates,vehiclePositions
  OBA_GTFSRT_ARCHIVER_POLL_SEC   default 30
  OBA_GTFSRT_ARCHIVER_DIR        default /data/gtfsrt-archive
  OBA_ARCHIVER_S3_BUCKET         default oba-ec2-predictions
  OBA_ARCHIVER_S3_PREFIX         required (one per instance; see run-gtfsrt-archiver.sh)
  OBA_ARCHIVER_UPLOAD            default true (false = local files only)
  OBA_ARCHIVER_S3_RETRIES        default 3
  OBA_ARCHIVER_LOG_STATS_SEC     default 60
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from google.transit import gtfs_realtime_pb2

LOG = logging.getLogger("gtfsrt-archiver")

BASE_URL = os.environ.get("OBA_GTFSRT_ARCHIVER_URL", "http://127.0.0.1:8083").rstrip("/")
FEEDS = [f for f in os.environ.get("OBA_GTFSRT_ARCHIVER_FEEDS", "tripUpdates,vehiclePositions").split(",") if f]
POLL_SEC = float(os.environ.get("OBA_GTFSRT_ARCHIVER_POLL_SEC", "30"))
ARCHIVE_DIR = Path(os.environ.get("OBA_GTFSRT_ARCHIVER_DIR", "/data/gtfsrt-archive"))
S3_BUCKET = os.environ.get("OBA_ARCHIVER_S3_BUCKET", "oba-ec2-predictions")
S3_PREFIX = os.environ.get("OBA_ARCHIVER_S3_PREFIX", "").strip("/")
UPLOAD_ENABLED = os.environ.get("OBA_ARCHIVER_UPLOAD", "true").lower() not in ("0", "false", "no")
S3_RETRIES = int(os.environ.get("OBA_ARCHIVER_S3_RETRIES", "3"))
LOG_STATS_SEC = int(os.environ.get("OBA_ARCHIVER_LOG_STATS_SEC", "60"))
HTTP_TIMEOUT_SEC = 20


def hour_stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d_%H-00-00")


def zip_name(hour: str) -> str:
    return f"gtfsrt_{hour}.zip"


def s3_dest(name: str) -> str:
    return f"s3://{S3_BUCKET}/{S3_PREFIX}/gtfsrt/{name}"


def fetch(feed: str) -> bytes:
    with urllib.request.urlopen(f"{BASE_URL}/{feed}", timeout=HTTP_TIMEOUT_SEC) as resp:
        return resp.read()


def summarize(payload: bytes) -> tuple[int, int]:
    """(entities, CANCELED trip updates) in one feed message."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.ParseFromString(payload)
    canceled = sum(
        1 for e in msg.entity
        if e.HasField("trip_update")
        and e.trip_update.trip.schedule_relationship
        == gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.CANCELED
    )
    return len(msg.entity), canceled


def zip_hour(hour_dir: Path) -> Path | None:
    files = sorted(p for p in hour_dir.rglob("*.pb") if p.is_file())
    if not files:
        shutil.rmtree(hour_dir, ignore_errors=True)
        return None
    zip_path = ARCHIVE_DIR / zip_name(hour_dir.name)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=str(path.relative_to(hour_dir)))
    shutil.rmtree(hour_dir)
    LOG.info("closed hour %s: %d polls -> %s", hour_dir.name, len(files), zip_path.name)
    return zip_path


def upload(zip_path: Path) -> None:
    if not UPLOAD_ENABLED:
        LOG.info("upload disabled; keeping %s", zip_path)
        return
    dest = s3_dest(zip_path.name)
    for attempt in range(S3_RETRIES):
        try:
            subprocess.run(["aws", "s3", "cp", str(zip_path), dest, "--only-show-errors"],
                           check=True, capture_output=True, text=True)
            LOG.info("uploaded %s -> %s", zip_path.name, dest)
            zip_path.unlink(missing_ok=True)
            return
        except subprocess.CalledProcessError as exc:
            LOG.warning("upload failed (attempt %d/%d) for %s: %s", attempt + 1, S3_RETRIES,
                        zip_path.name, (exc.stderr or str(exc)).strip()[:200])
            time.sleep(2 ** attempt)
    LOG.error("upload failed for %s; kept on disk for the next pass", zip_path.name)


def close_finished_hours(current_hour: str) -> None:
    for hour_dir in sorted(p for p in ARCHIVE_DIR.iterdir() if p.is_dir() and p.name != current_hour):
        zip_hour(hour_dir)
    for zip_path in sorted(ARCHIVE_DIR.glob("gtfsrt_*.zip")):
        upload(zip_path)


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stop = False

    def handle_signal(signum, _frame):
        nonlocal stop
        LOG.info("received signal %s; current hour stays on disk and resumes on restart", signum)
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    LOG.info("polling %s %s every %.0fs -> %s", BASE_URL, FEEDS, POLL_SEC,
             s3_dest("") if UPLOAD_ENABLED else ARCHIVE_DIR)

    current_hour = hour_stamp(datetime.now(timezone.utc))
    close_finished_hours(current_hour)

    polls = failures = 0
    last = {}
    last_stats = time.monotonic()
    next_poll = time.monotonic()
    while not stop:
        now = datetime.now(timezone.utc)
        hour = hour_stamp(now)
        if hour != current_hour:
            current_hour = hour
            close_finished_hours(current_hour)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        for feed in FEEDS:
            try:
                payload = fetch(feed)
            except Exception as exc:
                failures += 1
                LOG.warning("fetch %s failed: %s", feed, exc)
                continue
            feed_dir = ARCHIVE_DIR / current_hour / feed
            feed_dir.mkdir(parents=True, exist_ok=True)
            (feed_dir / f"{stamp}.pb").write_bytes(payload)
            polls += 1
            try:
                last[feed] = summarize(payload)
            except Exception as exc:
                LOG.warning("%s at %s did not parse as a FeedMessage (stored anyway): %s", feed, stamp, exc)

        if time.monotonic() - last_stats >= LOG_STATS_SEC:
            detail = ", ".join(f"{feed} entities={n} canceled={c}" for feed, (n, c) in sorted(last.items()))
            LOG.info("stats: %d polls stored, %d fetch failures this session; latest %s", polls, failures, detail)
            last_stats = time.monotonic()

        next_poll += POLL_SEC
        while not stop and time.monotonic() < next_poll:
            time.sleep(min(1.0, next_poll - time.monotonic()))
        if time.monotonic() - next_poll > POLL_SEC:
            next_poll = time.monotonic()

    LOG.info("stopped (%d polls stored this session)", polls)
    return 0


if __name__ == "__main__":
    sys.exit(run())
