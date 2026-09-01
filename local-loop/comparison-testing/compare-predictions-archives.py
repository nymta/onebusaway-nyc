#!/usr/bin/env python3
"""Compare OBA EC2 vs BusTech prod predictions from queuePredictions hourly archives.

Both archives are NDJSON inside queuePredictions_YYYY-MM-DD_HH-00-00.zip: one differential
GTFS-RT FeedMessage per line (internal ZMQ :5568 stream, not the public /tripUpdates feed).

Because the streams are DIFFERENTIAL and published at different cadences, this tool:
  1. Replays each archive chronologically, merging entity updates into running state.
  2. Samples merged state every SAMPLE_INTERVAL_S wall-clock seconds.
  3. Pairs ours/prod samples by header timestamp (nearest within PAIR_TOL_S).
  4. Reports trip-matching and stop-prediction deltas (same metrics as compare-feeds.py).

Usage:
    # Same UTC hour from S3 (defaults: oba-ec2-predictions / obanyc-historical-predictions):
    python3 compare-predictions-archives.py 2026-08-04 19

    # Local zip files:
    python3 compare-predictions-archives.py ours.zip prod.zip

    # Quick smoke (fewer snapshot pairs):
    SAMPLE_INTERVAL_S=120 python3 compare-predictions-archives.py 2026-08-04 19

Env:
    OURS_BUCKET / PROD_BUCKET / OURS_PREFIX / PROD_PREFIX
    ARCHIVE_CACHE          local cache dir for S3 downloads
    AWS_PROFILE            for ours (oba-ec2-predictions) if not using default chain
    BUSTECH_AWS_ACCESS_KEY_ID / BUSTECH_AWS_SECRET_ACCESS_KEY  for prod bucket
    AGENCY=NYCT|MTABC      restrict comparisons to one agency
    REPORT_OUT             write markdown report path
    SAMPLE_INTERVAL_S      default 60
    PAIR_TOL_S             default 10
    MAX_PAIRS              cap snapshot pairs (default 0 = no cap)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
import re
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from typing import Any, Iterator

QUEUE_PREDICTIONS_RE = re.compile(
    r"^queuePredictions_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})\.zip$"
)

# Ours lives in a dedicated bucket (shared with D&A), one prefix per arm: v1 = the primary
# (~11 s deadband), v2-26s-deadband, v3-filtered. Two earlier locations are dead and both fail
# by silently reporting "no archive found" rather than erroring, so check the prefix first:
# s3://mtalirr/oba-ec2-predictions/ stopped receiving uploads 2026-08-10, and the primary
# archived to this bucket's ROOT until 2026-08-21 (moved to v1/, root objects deleted).
OURS_BUCKET = os.environ.get("OURS_BUCKET", "oba-ec2-predictions")
PROD_BUCKET = os.environ.get("PROD_BUCKET", "obanyc-historical-predictions")
OURS_PREFIX = os.environ.get("OURS_PREFIX", "v1")
PROD_PREFIX = os.environ.get("PROD_PREFIX", "")
CACHE = os.environ.get("ARCHIVE_CACHE", "/tmp/oba-predictions-archive-cache")
SAMPLE_INTERVAL_S = int(os.environ.get("SAMPLE_INTERVAL_S", "60"))
PAIR_TOL_S = int(os.environ.get("PAIR_TOL_S", "10"))
MAX_PAIRS = int(os.environ.get("MAX_PAIRS", "0"))
AGENCY = os.environ.get("AGENCY")
EXPRESS_PREFIXES = ("SIM", "BXM", "QM", "BM", "X")
REPORT = os.environ.get(
    "REPORT_OUT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "predictions-archive-comparison.md"),
)


def norm(ident: str) -> str:
    return ident.split("_", 1)[1] if "_" in ident and not ident.split("_")[0].isdigit() else ident


def is_express(route_id: str) -> bool:
    return route_id.upper().startswith(EXPRESS_PREFIXES)


def agency_of(vehicle_id: str) -> str:
    return "MTABC" if vehicle_id.startswith("MTABC") else "NYCT"


def pct(a: float, b: float) -> float:
    return 100.0 * a / b if b else 0.0


def delta_stats(deltas: list[float]) -> dict[str, float] | None:
    if not deltas:
        return None
    a = sorted(deltas)
    n = len(a)
    return dict(
        n=n,
        median=statistics.median(a),
        mean=statistics.fmean(a),
        mae=statistics.fmean(abs(d) for d in a),
        p10=a[int(0.10 * n)],
        p90=a[min(n - 1, int(0.90 * n))],
        w30=pct(sum(1 for d in a if abs(d) <= 30), n),
        w60=pct(sum(1 for d in a if abs(d) <= 60), n),
        w180=pct(sum(1 for d in a if abs(d) <= 180), n),
        w300=pct(sum(1 for d in a if abs(d) <= 300), n),
    )


def hour_zip_name(date: str, hour: int) -> str:
    return f"queuePredictions_{date}_{hour:02d}-00-00.zip"


def hour_zip_candidates(date: str, hour: int) -> list[str]:
    """Candidate object names for a logical UTC hour (prod uses mixed -00-00 / -59-59 suffixes).

    Prod's `HH-59-59` is named for the timestamp one second BEFORE the hour it covers, so
    `..._12-59-59.zip` holds 13:00-14:00. Verified against header ts ranges: our 2026-08-12
    `11-00-00` spans 10:59:35->12:00:02 while prod's `11-59-59` spans 11:59:14->13:00:01.
    So `(hour-1)-59-59` must be tried BEFORE `hour-59-59` -- the reverse order silently pairs
    against the NEXT hour whenever both objects exist, which collapses the shared sample grid
    (2 shared points instead of ~60) while still emitting a plausible-looking report.
    """
    names = [hour_zip_name(date, hour)]
    # The `-59-59` object belongs to the PREVIOUS DAY when hour is 00, so the date has to roll back
    # with the hour. Keeping `date` fixed and only wrapping the hour asks for `{date}_23-59-59`,
    # which is the object covering the NEXT day's hour 00 -- a silent 24 h error that still produces
    # a plausible report on any day where that object already exists.
    prev = datetime.strptime(f"{date} {hour:02d}", "%Y-%m-%d %H") - timedelta(hours=1)
    names.append(f"queuePredictions_{prev.strftime('%Y-%m-%d')}_{prev.hour:02d}-59-59.zip")
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def logical_hour_of_name(name: str) -> datetime | None:
    """The absolute UTC hour this object covers, derived from its name.

    We publish `HH-00-00`, named for the hour it contains. BusTech mostly publishes `HH-59-59`,
    named one second BEFORE the hour it contains -- so `23-59-59` belongs to hour 00 of the FOLLOWING
    day. Returning an absolute datetime rather than an hour-of-day is what keeps that date rollover
    from having to be special-cased (and silently mishandled) by every caller.
    """
    m = QUEUE_PREDICTIONS_RE.match(name)
    if not m:
        return None
    hh, mm, ss = (int(m.group(i)) for i in (2, 3, 4))
    base = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(hours=hh)
    # `HH-59-59` is one second short of the hour it covers; every other suffix -- the canonical
    # `HH-00-00` and mid-hour restarts like `11-02-59` -- is named within the hour it covers.
    return base + timedelta(hours=1) if (mm, ss) == (59, 59) else base


def logical_hour_from_name(name: str, requested_date: str, requested_hour: int) -> bool:
    """True if this zip name covers exactly the requested UTC hour."""
    want = datetime.strptime(f"{requested_date} {requested_hour:02d}", "%Y-%m-%d %H").replace(
        tzinfo=timezone.utc
    )
    return logical_hour_of_name(name) == want


def s3_env(bucket: str, profile: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if profile:
        env["AWS_PROFILE"] = profile
        env.pop("AWS_ACCESS_KEY_ID", None)
        env.pop("AWS_SECRET_ACCESS_KEY", None)
        env.pop("AWS_SESSION_TOKEN", None)
    elif bucket == PROD_BUCKET:
        ak = os.environ.get("BUSTECH_AWS_ACCESS_KEY_ID")
        sk = os.environ.get("BUSTECH_AWS_SECRET_ACCESS_KEY")
        if ak and sk:
            env["AWS_ACCESS_KEY_ID"] = ak
            env["AWS_SECRET_ACCESS_KEY"] = sk
            env.pop("AWS_PROFILE", None)
            env.pop("AWS_SESSION_TOKEN", None)
    return env


def s3_head_exists(bucket: str, key: str, env: dict[str, str]) -> bool:
    r = subprocess.run(
        ["aws", "s3api", "head-object", "--bucket", bucket, "--key", key],
        capture_output=True,
        text=True,
        env=env,
    )
    return r.returncode == 0


def s3_list_queue_predictions(bucket: str, prefix: str, date: str, env: dict[str, str]) -> list[str]:
    base = f"{prefix}/" if prefix else ""
    listing_prefix = f"{base}queuePredictions_{date}_"
    r = subprocess.run(
        ["aws", "s3", "ls", f"s3://{bucket}/{listing_prefix}"],
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0:
        return []
    names: list[str] = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        name = parts[-1]
        if name.endswith(".zip") and QUEUE_PREDICTIONS_RE.match(name):
            names.append(name)
    return sorted(names)


def resolve_hour_zip_key(bucket: str, prefix: str, date: str, hour: int, profile: str | None) -> str:
    env = s3_env(bucket, profile)
    for name in hour_zip_candidates(date, hour):
        key = f"{prefix}/{name}" if prefix else name
        if s3_head_exists(bucket, key, env):
            return key
    # An object covering this hour may be filed under the PREVIOUS date (BusTech's `23-59-59`), so
    # the fallback has to list both days or it can never find hour 00 by any name.
    prev_date = (
        datetime.strptime(f"{date} {hour:02d}", "%Y-%m-%d %H") - timedelta(hours=1)
    ).strftime("%Y-%m-%d")
    listed = s3_list_queue_predictions(bucket, prefix, date, env)
    if prev_date != date:
        listed += s3_list_queue_predictions(bucket, prefix, prev_date, env)
    matches = [n for n in listed if logical_hour_from_name(n, date, hour)]
    if not matches:
        tried = ", ".join(hour_zip_candidates(date, hour))
        raise RuntimeError(
            f"no queuePredictions zip for {date} hour {hour:02d} UTC in s3://{bucket}/"
            f"{prefix or ''} (tried {tried})"
        )
    # Prefer canonical -00-00, then (hour-1)-59-59, which is prod's name for THIS hour.
    # Ranking `hour-59-59` ahead of it selects the next hour's archive (see hour_zip_candidates).
    def rank(name: str) -> tuple[int, str]:
        # Every candidate here already covers the requested hour, so rank purely by naming form:
        # canonical `HH-00-00` first, then `HH-59-59`, then mid-hour restart names.
        m = QUEUE_PREDICTIONS_RE.match(name)
        assert m
        mm, ss = int(m.group(3)), int(m.group(4))
        return (0 if (mm, ss) == (0, 0) else 1 if (mm, ss) == (59, 59) else 2, name)

    chosen = sorted(matches, key=rank)[0]
    return f"{prefix}/{chosen}" if prefix else chosen


def s3_download(bucket: str, key: str, profile: str | None = None) -> str:
    os.makedirs(CACHE, exist_ok=True)
    local = os.path.join(CACHE, key.replace("/", "__"))
    if os.path.exists(local):
        return local
    dest = f"s3://{bucket}/{key}"
    env = s3_env(bucket, profile)
    r = subprocess.run(["aws", "s3", "cp", dest, local, "--quiet"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"failed to download {dest}: {r.stderr.strip()}")
    return local


def resolve_zip(path_or_date: str, hour: int | None, label: str) -> str:
    if path_or_date.endswith(".zip") and os.path.isfile(path_or_date):
        return path_or_date
    if hour is None:
        raise ValueError(f"expected hour for date {path_or_date}")
    if label == "ours":
        key = resolve_hour_zip_key(
            OURS_BUCKET,
            OURS_PREFIX,
            path_or_date,
            hour,
            os.environ.get("OURS_AWS_PROFILE", "default"),
        )
    else:
        key = resolve_hour_zip_key(PROD_BUCKET, PROD_PREFIX, path_or_date, hour, None)
    bucket = OURS_BUCKET if label == "ours" else PROD_BUCKET
    profile = os.environ.get("OURS_AWS_PROFILE", "default") if label == "ours" else None
    return s3_download(bucket, key, profile=profile)


def iter_feed_messages(zip_path: str) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(zip_path) as zf:
        inner = zf.namelist()[0]
        with zf.open(inner) as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                yield json.loads(raw)


def to_epoch_sec(value: Any) -> int:
    t = int(value)
    return t // 1000 if t > 1_000_000_000_000 else t


def header_ts_sec(msg: dict[str, Any]) -> int:
    return to_epoch_sec(msg["header"]["timestamp"])


class DifferentialState:
    """Merge differential FeedMessage entities into last-known state."""

    def __init__(self) -> None:
        self._entities: dict[str, dict[str, Any]] = {}
        self._last_seen: dict[str, int] = {}

    def apply(self, msg: dict[str, Any]) -> None:
        ts = header_ts_sec(msg)
        for entity in msg.get("entity") or []:
            eid = entity.get("id")
            if eid:
                self._entities[eid] = entity
                self._last_seen[eid] = ts
        if len(self._entities) > 50_000:
            self._prune(ts - 900)

    def _prune(self, min_ts: int) -> None:
        stale = [eid for eid, seen in self._last_seen.items() if seen < min_ts]
        for eid in stale:
            self._entities.pop(eid, None)
            self._last_seen.pop(eid, None)

    def trip_updates_by_vehicle(self, since_ts: int = 0) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for eid, entity in self._entities.items():
            if since_ts and self._last_seen.get(eid, 0) < since_ts:
                continue
            tu = entity.get("tripUpdate")
            if not tu:
                continue
            vehicle = tu.get("vehicle") or {}
            veh_raw = vehicle.get("id") or ""
            if not veh_raw:
                continue
            if AGENCY and agency_of(veh_raw) != AGENCY:
                continue
            veh = norm(veh_raw)
            trip = tu.get("trip") or {}
            stops: dict[str, int] = {}
            for stu in tu.get("stopTimeUpdate") or []:
                stop_id = stu.get("stopId")
                arrival = (stu.get("arrival") or {}).get("time")
                departure = (stu.get("departure") or {}).get("time")
                t = arrival or departure
                if stop_id and t:
                    stops[norm(stop_id)] = to_epoch_sec(t)
            rec = dict(
                trip=norm(trip.get("tripId") or ""),
                route=norm(trip.get("routeId") or ""),
                direction=trip.get("directionId"),
                stops=stops,
                entity_id=entity.get("id"),
            )
            out[veh].append(rec)
        for recs in out.values():
            recs.sort(key=lambda r: min(r["stops"].values()) if r["stops"] else float("inf"))
        return dict(out)


def grid_ts(ts_sec: int) -> int:
    """Align timestamps to a shared wall-clock grid so ours/prod snapshots are comparable."""
    return ts_sec - (ts_sec % SAMPLE_INTERVAL_S)


def iter_messages_sorted(zip_path: str) -> Iterator[dict[str, Any]]:
    """Replay messages in header-timestamp order without holding the full hour in RAM."""
    unsorted = tempfile.NamedTemporaryFile(mode="wb", suffix=".ndjson", delete=False)
    unsorted_path = unsorted.name
    sorted_path = f"{unsorted_path}.sorted"
    count = 0
    try:
        with unsorted:
            for msg in iter_feed_messages(zip_path):
                ts = header_ts_sec(msg)
                line = json.dumps(msg, separators=(",", ":")).encode("utf-8")
                unsorted.write(f"{ts:012d} ".encode("ascii") + line + b"\n")
                count += 1
        subprocess.run(["sort", "-n", unsorted_path, "-o", sorted_path], check=True)
        with open(sorted_path, "rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                _prefix, payload = raw.split(b" ", 1)
                yield json.loads(payload)
    finally:
        unsorted.close()
        for path in (unsorted_path, sorted_path):
            try:
                os.unlink(path)
            except OSError:
                pass
    if count == 0:
        return


def build_sampled_snapshots(zip_path: str, label: str) -> tuple[dict[int, dict], dict[str, int]]:
    state = DifferentialState()
    snapshots: dict[int, dict[str, list[dict[str, Any]]]] = {}
    stats = Counter()
    current_g: int | None = None
    print(f"  replaying {label}: {os.path.basename(zip_path)}", flush=True)
    for msg in iter_messages_sorted(zip_path):
        stats["lines"] += 1
        g = grid_ts(header_ts_sec(msg))
        stats["entities"] += len(msg.get("entity") or [])
        if current_g is not None and g != current_g:
            since = current_g - SAMPLE_INTERVAL_S * 2
            snapshots[current_g] = state.trip_updates_by_vehicle(since_ts=since)
        state.apply(msg)
        current_g = g
    if current_g is not None:
        since = current_g - SAMPLE_INTERVAL_S * 2
        snapshots[current_g] = state.trip_updates_by_vehicle(since_ts=since)
    print(
        f"    {stats['lines']:,} lines, {len(snapshots)} snapshots "
        f"(every {SAMPLE_INTERVAL_S}s), {stats['entities']:,} entity updates",
        flush=True,
    )
    return snapshots, dict(stats)


def compare_snapshots(
    ours_snaps: dict[int, dict],
    prod_snaps: dict[int, dict],
) -> dict[str, Any]:
    pooled: dict[str, list[float]] = defaultdict(list)
    pooled_class: dict[str, list[float]] = defaultdict(list)
    match = Counter()
    veh = Counter()
    skews: list[float] = []
    n_pairs = 0
    shared_grid = sorted(set(ours_snaps) & set(prod_snaps))

    for gts in shared_grid:
        n_pairs += 1
        if MAX_PAIRS and n_pairs > MAX_PAIRS:
            break
        skews.append(0.0)
        ours = ours_snaps[gts]
        prod = prod_snaps[gts]
        now = float(gts)
        both = set(ours) & set(prod)
        veh.update(ours=len(ours), prod=len(prod), both=len(both), snaps=1)
        for v in both:
            o = ours[v][0]
            prod_recs = prod[v]
            m = next((rec for rec in prod_recs if rec["trip"] == o["trip"]), prod_recs[0])
            if o["trip"] == m["trip"]:
                match["same_trip"] += 1
            elif o["route"] == m["route"]:
                if (
                    o["direction"] is not None
                    and m["direction"] is not None
                    and o["direction"] != m["direction"]
                ):
                    match["same_route_diff_direction"] += 1
                else:
                    match["same_route_diff_trip"] += 1
            else:
                match["diff_route"] += 1
                continue
            for stop, ot in o["stops"].items():
                mt = m["stops"].get(stop)
                if not mt or mt < now - 30 or ot < now - 30:
                    continue
                horizon = (mt - now) / 60.0
                bucket = (
                    "0-5 min"
                    if horizon < 5
                    else "5-15 min"
                    if horizon < 15
                    else "15-30 min"
                    if horizon < 30
                    else "30+ min"
                )
                d = float(ot - mt)
                pooled[bucket].append(d)
                pooled["ALL"].append(d)
                pooled_class["express" if is_express(o["route"]) else "local"].append(d)

    return dict(
        pooled=dict(pooled),
        pooled_class=dict(pooled_class),
        match=match,
        veh=veh,
        snap_pairs=n_pairs,
        skew=statistics.median(skews) if skews else float("nan"),
    )


def report(
    label: str,
    ours_path: str,
    prod_path: str,
    ours_stats: dict[str, int],
    prod_stats: dict[str, int],
    result: dict[str, Any],
) -> str:
    lines: list[str] = []

    def p(s: str = "") -> None:
        print(s)
        lines.append(s)

    p("# queuePredictions archive comparison — OBA EC2 vs BusTech prod")
    p()
    p(f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    p(f"- window: **{label}**")
    p(f"- ours: `{ours_path}` ({ours_stats.get('lines', 0):,} lines)")
    p(f"- prod: `{prod_path}` ({prod_stats.get('lines', 0):,} lines)")
    p(f"- fleet filter: **{AGENCY or 'both agencies (NYCT + MTABC)'}**")
    p(
        f"- method: replay differential NDJSON → merge entity state → sample on "
        f"{SAMPLE_INTERVAL_S}s UTC grid (shared bucket keys)"
    )
    p("- delta convention: **ours − prod, seconds** (positive = we predict LATER)")
    p()
    p("## Volume")
    p()
    s = max(1, result["veh"]["snaps"])
    p("| snapshot pairs | median pair skew | avg veh ours | avg veh prod | avg overlap | stop-pred deltas |")
    p("|---|---|---|---|---|---|")
    p(
        f"| {result['snap_pairs']} | {result['skew']:.0f} s | "
        f"{result['veh']['ours'] / s:.0f} | {result['veh']['prod'] / s:.0f} | "
        f"{result['veh']['both'] / s:.0f} | {len(result['pooled'].get('ALL', []))} |"
    )
    p()
    p("## Trip matching (vehicles in both feeds at paired snapshots)")
    p()
    tot = sum(result["match"].values())
    p("| classification | pairs | share |")
    p("|---|---|---|")
    for key, lbl in (
        ("same_trip", "same trip_id"),
        ("same_route_diff_trip", "same route, different trip_id"),
        ("same_route_diff_direction", "same route, different direction"),
        ("diff_route", "different route"),
    ):
        p(f"| {lbl} | {result['match'][key]} | {pct(result['match'][key], tot):.1f}% |")
    p()
    p("## ETA deltas by horizon (median / MAE, seconds)")
    p()
    p("| bucket | n | median | MAE | ≤1 min | ≤3 min |")
    p("|---|---|---|---|---|---|")
    for b in ("ALL", "0-5 min", "5-15 min", "15-30 min", "30+ min"):
        st = delta_stats(result["pooled"].get(b, []))
        if st:
            p(
                f"| {b} | {st['n']} | {st['median']:+.0f} | {st['mae']:.0f} | "
                f"{st['w60']:.0f}% | {st['w180']:.0f}% |"
            )
        else:
            p(f"| {b} | 0 | – | – | – | – |")
    p()
    p("## By service class")
    p()
    p("| class | n | median | MAE | ≤1 min | ≤3 min |")
    p("|---|---|---|---|---|---|")
    for c in ("local", "express"):
        st = delta_stats(result["pooled_class"].get(c, []))
        if st:
            p(
                f"| {c} | {st['n']} | {st['median']:+.0f} | {st['mae']:.0f} | "
                f"{st['w60']:.0f}% | {st['w180']:.0f}% |"
            )
        else:
            p(f"| {c} | 0 | – | – | – | – |")
    p()
    return "\n".join(lines) + "\n"


def main() -> None:
    args = sys.argv[1:]
    if len(args) == 2 and args[0].endswith(".zip"):
        ours_path, prod_path = args
        label = f"{os.path.basename(ours_path)} vs {os.path.basename(prod_path)}"
    elif len(args) >= 2:
        date, hour = args[0], int(args[1])
        label = f"{date} {hour:02d}:00 UTC"
        ours_path = resolve_zip(date, hour, "ours")
        prod_path = resolve_zip(date, hour, "prod")
    else:
        print(__doc__)
        sys.exit(2)

    print(f"Comparing {label}")
    ours_snaps, ours_stats = build_sampled_snapshots(ours_path, "ours")
    prod_snaps, prod_stats = build_sampled_snapshots(prod_path, "prod")
    result = compare_snapshots(ours_snaps, prod_snaps)
    text = report(label, ours_path, prod_path, ours_stats, prod_stats, result)
    with open(REPORT, "w") as fh:
        fh.write(text)
    print(f"\nreport written -> {REPORT}")


if __name__ == "__main__":
    main()
