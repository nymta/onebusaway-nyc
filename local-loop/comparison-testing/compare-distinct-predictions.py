#!/usr/bin/env python3
"""Compare two prediction archives by DISTINCT predictions, not by emitted rows.

The archives hold DIFFERENTIAL GTFS-RT: one trip-update message per ingested AVL fix per vehicle.
So row and message counts scale with **publication cadence**, not with how much the model predicts.
An arm fed a 28 s input emits ~half the rows of one fed a ~16 s input while predicting exactly the
same things -- which is the confound the cadence-parity arm exists to remove (PLAN-cadence-parity.md).

This counts distinct (tripId, stopId) pairs as "predictions", reports the republication factor that
explains any row gap, and measures the per-vehicle publication interval on each side so the gap can
be attributed rather than guessed.

Usage:
  compare-distinct-predictions.py <YYYY-MM-DD_HH> [--a PREFIX] [--b PREFIX] [--bucket B]
                                  [--from-min M] [--to-min M] [--keep]

  --a / --b   archive prefixes inside the bucket, one per arm: v1 (oba-nyc-prod, ~11 s deadband),
              v2-26s-deadband, v3-filtered. Defaults: a=v3-filtered (the 28 s arm), b=v1.
              oba-nyc-prod archived to the bucket root until 2026-08-21; passing "" now finds
              nothing rather than erroring.
  --from-min / --to-min
              restrict to whole minutes past the hour, for windows where one side only covers
              part of the hour (a host that started mid-hour). Omit to use the whole hour.

Example:
  compare-distinct-predictions.py 2026-08-20_22
  compare-distinct-predictions.py 2026-08-20_21 --from-min 52 --to-min 59
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# The header timestamp sits in the first ~120 bytes of every line; matching it as bytes lets us skip
# out-of-window lines without paying for a full JSON parse (the prod side is ~3 GB uncompressed).
# Tolerate both separator styles: our archiver emits `"timestamp": "..."` (space after the colon,
# via MessageToJson) while BusTech prod emits `"timestamp":"..."`. A strict pattern matches ours and
# silently skips every prod line, which reports zeros rather than failing -- see the guard in scan().
TS_RE = re.compile(rb'"timestamp":\s*"?(\d+)"?')


def fetch(bucket: str, prefix: str, hour: str, dest: Path) -> Path:
    key = f"{prefix.strip('/') + '/' if prefix.strip('/') else ''}queuePredictions_{hour}-00-00.zip"
    out = dest / f"{(prefix.strip('/') or 'root').replace('/', '_')}.zip"
    uri = f"s3://{bucket}/{key}"
    print(f"  fetching {uri}", flush=True)
    subprocess.run(["aws", "s3", "cp", uri, str(out), "--only-show-errors"], check=True)
    return out


def scan(path: Path, lo_ms: int | None, hi_ms: int | None) -> dict:
    z = zipfile.ZipFile(path)
    member = z.namelist()[0]
    msgs = rows = 0
    pairs: set[tuple] = set()
    trips: set = set()
    stops: set = set()
    veh_ts: dict[str, list[int]] = defaultdict(list)
    with z.open(member) as fh:
        for raw in fh:
            m = TS_RE.search(raw, 0, 200)
            if not m:
                continue
            ts = int(m.group(1))
            if lo_ms is not None and not (lo_ms <= ts < hi_ms):
                continue
            msgs += 1
            for entity in json.loads(raw).get("entity", []):
                tu = entity.get("tripUpdate")
                if not tu:
                    continue
                trip = tu.get("trip", {}).get("tripId")
                if trip:
                    trips.add(trip)
                vehicle = tu.get("vehicle", {}).get("id")
                if vehicle:
                    veh_ts[vehicle].append(ts)
                for stu in tu.get("stopTimeUpdate", []):
                    rows += 1
                    stop = stu.get("stopId")
                    if stop:
                        stops.add(stop)
                        pairs.add((trip, stop))
    gaps: list[float] = []
    for times in veh_ts.values():
        times.sort()
        gaps += [(b - a) / 1000.0 for a, b in zip(times, times[1:]) if 0 < b - a < 600_000]
    gaps.sort()
    if msgs == 0:
        raise SystemExit(
            f"ERROR: matched 0 messages in {path.name}. Either the window excludes this archive "
            f"entirely, or the header-timestamp pattern did not match its JSON style. Refusing to "
            f"report zeros as a result."
        )
    return dict(msgs=msgs, rows=rows, pairs=len(pairs), trips=len(trips), stops=len(stops),
                vehicles=len(veh_ts), gaps=gaps)


def quantile(values: list[float], frac: float) -> float:
    return values[int(frac * (len(values) - 1))] if values else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hour", help="UTC hour as YYYY-MM-DD_HH")
    ap.add_argument("--a", default="v3-filtered")
    ap.add_argument("--b", default="v1")
    ap.add_argument("--bucket", default="oba-ec2-predictions")
    ap.add_argument("--from-min", type=int, default=None)
    ap.add_argument("--to-min", type=int, default=None)
    ap.add_argument("--keep", action="store_true", help="keep the downloaded zips")
    ap.add_argument("--local-a", help="use this local zip for side A instead of fetching")
    ap.add_argument("--local-b", help="use this local zip for side B instead of fetching")
    args = ap.parse_args()

    hour_start = datetime.strptime(args.hour, "%Y-%m-%d_%H").replace(tzinfo=timezone.utc)
    base = int(hour_start.timestamp() * 1000)
    lo = hi = None
    if args.from_min is not None or args.to_min is not None:
        lo = base + (args.from_min or 0) * 60_000
        hi = base + ((args.to_min + 1) if args.to_min is not None else 60) * 60_000
    minutes = ((hi - lo) if lo is not None else 3_600_000) / 60_000

    tmp = Path(tempfile.mkdtemp(prefix="predcmp-"))
    label_a = args.a or "(root)"
    label_b = args.b or "(root)"
    print(f"hour {args.hour} UTC, {minutes:.0f} minute(s)"
          f"{'' if lo is None else f' [min {args.from_min}..{args.to_min}]'}")
    zip_a = Path(args.local_a) if args.local_a else fetch(args.bucket, args.a, args.hour, tmp)
    zip_b = Path(args.local_b) if args.local_b else fetch(args.bucket, args.b, args.hour, tmp)
    print("  scanning...", flush=True)
    a = scan(zip_a, lo, hi)
    b = scan(zip_b, lo, hi)

    print(f"\n{'metric':44s} {label_a:>16s} {label_b:>16s} {'ratio':>8s}")
    print("-" * 88)
    for label, key in (("DISTINCT (trip,stop) predictions", "pairs"),
                       ("distinct trips", "trips"),
                       ("distinct vehicles", "vehicles"),
                       ("distinct stops", "stops"),
                       ("stopTimeUpdate ROWS emitted", "rows"),
                       ("trip-update messages", "msgs")):
        ratio = f"{a[key] / b[key]:.2f}x" if b[key] else "n/a"
        print(f"{label:44s} {a[key]:16,d} {b[key]:16,d} {ratio:>8s}")
    print("-" * 88)
    rep_a = a["rows"] / max(a["pairs"], 1)
    rep_b = b["rows"] / max(b["pairs"], 1)
    print(f"{'republications per distinct prediction':44s} {rep_a:16.2f} {rep_b:16.2f} "
          f"{rep_a / rep_b if rep_b else float('nan'):7.2f}x")
    print(f"{'distinct predictions per minute':44s} {a['pairs'] / minutes:16,.0f} "
          f"{b['pairs'] / minutes:16,.0f}")

    print("\nper-vehicle PUBLICATION interval, seconds (the cadence under test):")
    for label, d in ((label_a, a), (label_b, b)):
        g = d["gaps"]
        if not g:
            print(f"  {label:18s} (no repeat observations)")
            continue
        print(f"  {label:18s} n={len(g):8,d}  p25={quantile(g, .25):5.1f}  "
              f"median={quantile(g, .50):5.1f}  p75={quantile(g, .75):5.1f}  "
              f"mean={statistics.mean(g):5.1f}")

    ga, gb = a["gaps"], b["gaps"]
    if ga and gb:
        # If the row gap is purely cadence, the inverse mean-interval ratio predicts it exactly.
        predicted = statistics.mean(gb) / statistics.mean(ga)
        observed = a["msgs"] / max(b["msgs"], 1)
        print(f"\ncadence explains the volume gap: predicted {predicted:.2f}x vs "
              f"observed {observed:.2f}x messages"
              f"  ({'consistent' if abs(predicted - observed) < 0.05 else 'DISCREPANCY -- investigate'})")

    if not args.keep:
        for p, was_fetched in ((zip_a, not args.local_a), (zip_b, not args.local_b)):
            if was_fetched:
                p.unlink(missing_ok=True)
        tmp.rmdir()
    else:
        print(f"\nzips kept in {tmp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
