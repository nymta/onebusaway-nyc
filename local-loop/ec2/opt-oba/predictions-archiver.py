#!/usr/bin/env python3
"""Archive OBA predictions ZMQ stream to hourly queuePredictions zips on S3.

Subscribes to the internal predictions GTFS-RT queue (default tcp://127.0.0.1:5568,
topic "time"), converts each differential FeedMessage protobuf to JSON (one object per
line), rotates on UTC hour boundaries, and uploads:

  queuePredictions_YYYY-MM-DD_HH-00-00.zip

to S3 — matching the BusTech obanyc-historical-predictions layout.

Environment:
  OBA_ARCHIVER_ZMQ_HOST     default 127.0.0.1
  OBA_ARCHIVER_ZMQ_PORT     default 5568
  OBA_ARCHIVER_ZMQ_TOPIC    default time
  OBA_ARCHIVER_DIR          default /data/predictions-archive
  OBA_ARCHIVER_S3_BUCKET    default mtalirr
  OBA_ARCHIVER_S3_PREFIX    default oba-ec2-predictions
  OBA_ARCHIVER_UPLOAD       default true (false = local files only)
  OBA_ARCHIVER_S3_RETRIES   default 3
  OBA_ARCHIVER_LOG_STATS_SEC  default 60
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import zmq
from google.protobuf.json_format import MessageToJson
from google.transit import gtfs_realtime_pb2

LOG = logging.getLogger("predictions-archiver")

ZMQ_HOST = os.environ.get("OBA_ARCHIVER_ZMQ_HOST", "127.0.0.1")
ZMQ_PORT = os.environ.get("OBA_ARCHIVER_ZMQ_PORT", "5568")
ZMQ_TOPIC = os.environ.get("OBA_ARCHIVER_ZMQ_TOPIC", "time")
ARCHIVE_DIR = Path(os.environ.get("OBA_ARCHIVER_DIR", "/data/predictions-archive"))
S3_BUCKET = os.environ.get("OBA_ARCHIVER_S3_BUCKET", "mtalirr")
S3_PREFIX = os.environ.get("OBA_ARCHIVER_S3_PREFIX", "oba-ec2-predictions").strip("/")
UPLOAD_ENABLED = os.environ.get("OBA_ARCHIVER_UPLOAD", "true").lower() not in ("0", "false", "no")
S3_RETRIES = int(os.environ.get("OBA_ARCHIVER_S3_RETRIES", "3"))
LOG_STATS_SEC = int(os.environ.get("OBA_ARCHIVER_LOG_STATS_SEC", "60"))


def hour_stamp(dt: datetime) -> str:
    """UTC hour label used in queuePredictions object names."""
    return dt.strftime("%Y-%m-%d_%H-00-00")


def base_name(stamp: str) -> str:
    return f"queuePredictions_{stamp}"


class HourlyArchive:
    def __init__(self, archive_dir: Path) -> None:
        self.archive_dir = archive_dir
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.hour_key: str | None = None
        self.json_path: Path | None = None
        self.json_file = None
        self.line_count = 0
        self._open_for_current_hour()

    def _open_for_current_hour(self) -> None:
        now = datetime.now(timezone.utc)
        key = hour_stamp(now)
        path = self.archive_dir / f"{base_name(key)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.hour_key = key
        self.json_path = path
        self.json_file = path.open("a", encoding="utf-8")
        self.line_count = 0
        if path.exists() and path.stat().st_size > 0:
            with path.open("r", encoding="utf-8") as existing:
                self.line_count = sum(1 for line in existing if line.strip())
        LOG.info(
            "opened archive %s (append=%s, lines=%d)",
            path,
            path.exists() and path.stat().st_size > 0,
            self.line_count,
        )

    def append(self, feed_message: gtfs_realtime_pb2.FeedMessage) -> Path | None:
        now = datetime.now(timezone.utc)
        current_key = hour_stamp(now)
        finished_zip: Path | None = None
        if current_key != self.hour_key:
            finished_zip = self.rotate(current_key)
        line = MessageToJson(
            feed_message,
            preserving_proto_field_name=False,
            indent=None,
        )
        assert self.json_file is not None
        self.json_file.write(line)
        self.json_file.write("\n")
        self.json_file.flush()
        self.line_count += 1
        return finished_zip

    def rotate(self, next_hour_key: str | None = None) -> Path | None:
        if self.json_file is not None:
            self.json_file.close()
            self.json_file = None

        finished_json = self.json_path
        finished_key = self.hour_key
        finished_lines = self.line_count

        if next_hour_key is not None:
            now = datetime.now(timezone.utc)
            key = next_hour_key
            path = self.archive_dir / f"{base_name(key)}.json"
            self.hour_key = key
            self.json_path = path
            self.json_file = path.open("a", encoding="utf-8")
            self.line_count = 0
            LOG.info("opened archive %s", path)

        if finished_json is None or finished_key is None or finished_lines == 0:
            if finished_json and finished_json.exists() and finished_lines == 0:
                finished_json.unlink(missing_ok=True)
            return None

        zip_path = finished_json.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(finished_json, arcname=finished_json.name)
        finished_json.unlink(missing_ok=True)
        LOG.info("closed hour %s: %d lines -> %s", finished_key, finished_lines, zip_path.name)
        return zip_path

    def close(self, finalize: bool = True) -> list[Path]:
        zip_paths: list[Path] = []
        if not finalize:
            if self.json_file is not None:
                self.json_file.close()
                self.json_file = None
            LOG.info("shutdown without finalizing partial hour %s (%d lines)", self.hour_key, self.line_count)
            return zip_paths
        if self.line_count > 0 and self.json_path is not None:
            zip_path = self.rotate(next_hour_key=None)
            if zip_path is not None:
                zip_paths.append(zip_path)
        elif self.json_file is not None:
            self.json_file.close()
            self.json_file = None
        return zip_paths


def s3_key_for_zip(zip_name: str) -> str:
    if S3_PREFIX:
        return f"{S3_PREFIX}/{zip_name}"
    return zip_name


def upload_zip(zip_path: Path) -> None:
    if not UPLOAD_ENABLED:
        LOG.info("upload disabled; keeping %s", zip_path)
        return

    dest = f"s3://{S3_BUCKET}/{s3_key_for_zip(zip_path.name)}"
    last_error: Exception | None = None
    for attempt in range(S3_RETRIES):
        try:
            subprocess.run(
                ["aws", "s3", "cp", str(zip_path), dest, "--only-show-errors"],
                check=True,
                capture_output=True,
                text=True,
            )
            LOG.info("uploaded %s -> %s", zip_path.name, dest)
            zip_path.unlink(missing_ok=True)
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            delay = 2 ** attempt
            LOG.warning(
                "upload failed (attempt %d/%d) for %s: %s; retry in %ds",
                attempt + 1,
                S3_RETRIES,
                zip_path.name,
                (exc.stderr or str(exc)).strip()[:200],
                delay,
            )
            time.sleep(delay)
    raise RuntimeError(f"upload failed for {zip_path.name}") from last_error


def upload_pending(archive_dir: Path) -> None:
    for zip_path in sorted(archive_dir.glob("queuePredictions_*.zip")):
        LOG.info("retrying pending upload %s", zip_path.name)
        upload_zip(zip_path)


def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    archive = HourlyArchive(ARCHIVE_DIR)
    stop = False

    def handle_signal(signum, _frame):
        nonlocal stop
        LOG.info("received signal %s; shutting down", signum)
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    endpoint = f"tcp://{ZMQ_HOST}:{ZMQ_PORT}"
    sub.connect(endpoint)
    sub.setsockopt(zmq.SUBSCRIBE, ZMQ_TOPIC.encode())
    sub.setsockopt(zmq.RCVTIMEO, 2000)
    LOG.info(
        "listening %s topic=%r dir=%s bucket=s3://%s upload=%s",
        endpoint,
        ZMQ_TOPIC,
        ARCHIVE_DIR,
        S3_BUCKET,
        UPLOAD_ENABLED,
    )

    upload_pending(ARCHIVE_DIR)

    msg_count = 0
    last_stats = time.monotonic()

    while not stop:
        try:
            parts = sub.recv_multipart()
        except zmq.Again:
            continue
        except zmq.ZMQError as exc:
            LOG.error("zmq error: %s", exc)
            time.sleep(1)
            continue

        payload = parts[-1]
        try:
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(payload)
            finished_zip = archive.append(feed)
            if finished_zip is not None:
                try:
                    upload_zip(finished_zip)
                except Exception as exc:
                    LOG.error("hour upload failed for %s: %s", finished_zip.name, exc)
            msg_count += 1
        except Exception as exc:
            LOG.warning("skipped message (%d bytes): %s", len(payload), exc)
            continue

        now = time.monotonic()
        if now - last_stats >= LOG_STATS_SEC:
            LOG.info("stats: %d messages this session, %d lines this hour", msg_count, archive.line_count)
            last_stats = now

    if stop:
        LOG.info("shutting down on signal; leaving partial hour on disk (no upload)")
    else:
        LOG.info("shutting down; finalizing open hour")
    for zip_path in archive.close(finalize=not stop):
        try:
            upload_zip(zip_path)
        except Exception as exc:
            LOG.error("final upload failed for %s: %s", zip_path.name, exc)

    upload_pending(ARCHIVE_DIR)
    sub.close()
    ctx.term()
    LOG.info("stopped (%d messages archived this session)", msg_count)
    return 0


if __name__ == "__main__":
    sys.exit(run())
