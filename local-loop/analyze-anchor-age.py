#!/usr/bin/env python3
"""Position-anchor age of OUR feed at a fixed clock window, across dates (from the S3 archives).

Why this exists: every prediction is anchored to the last AVL fix the engine processed for that
vehicle, so `poll_time - vehicle.timestamp` is the age of the evidence behind the ETAs. Until
2026-07-29 ours ran 13-19 s vs prod's ~30-50 s, and that gap was the accepted explanation for the
apparent "we predict earlier" bias (FINDINGS-SUMMARY F2). On 2026-07-30 the live probe found ours
at 65-74 s — STALER than prod — so the sign of that artifact had flipped and the F2 control needed
re-deriving. This replays the archives at one wall-clock window on several dates to date the change
and tie it to a cause.

Note the distinction it makes: `header age` is how late the FEED is published (the
`FeedStalenessSeconds` CloudWatch metric watches this and stayed at ~5 s throughout), while
`anchor age` is how old the DATA in it is. Load-shedding degrades the second while leaving the
first healthy, so the existing monitoring is blind to it — this is the gap-filling measurement.

Usage:
    python3 analyze-anchor-age.py 2026-07-27,2026-07-30 08-45 [n_windows]
    AGENCY=NYCT python3 analyze-anchor-age.py ...        # hold the fleet fixed across 2026-07-29
Env: ARCHIVE_CACHE (default /tmp/oba-archive-cache), ARCHIVE_PREFIX (default obaEc2VehiclePositions)

Use AGENCY=NYCT for any window spanning 2026-07-29: MTABC joined our feed that day, so a fleet-wide
median could rise simply because a newly-included fleet reports on a slower cadence. Holding the
agency fixed distinguishes that from a platform-wide freshness change.
"""
import base64, gzip, io, json, os, statistics, subprocess, sys
from google.transit import gtfs_realtime_pb2

BUCKET = "s3://mtalirr/data-archiver"
PREFIX = os.environ.get("ARCHIVE_PREFIX", "obaEc2VehiclePositions")
CACHE = os.environ.get("ARCHIVE_CACHE", "/tmp/oba-archive-cache")
AGENCY = os.environ.get("AGENCY")        # None = both; "NYCT" or "MTABC"


def s3_get(date, window):
    key = "%s/%s/%s.b64.gz" % (PREFIX, date, window)
    local = os.path.join(CACHE, key.replace("/", "__"))
    if not os.path.exists(local):
        os.makedirs(CACHE, exist_ok=True)
        r = subprocess.run(["aws", "s3", "cp", "%s/%s" % (BUCKET, key), local, "--quiet"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  !! missing %s (%s)" % (key, r.stderr.strip()[:120]))
            return None
    return local


def expand_windows(start, n):
    hh, mm = (int(x) for x in start.split("-"))
    out = []
    for i in range(n):
        total = hh * 60 + mm + 5 * i
        out.append("%02d-%02d" % ((total // 60) % 24, total % 60))
    return out


def q(sorted_vals, frac):
    return sorted_vals[min(len(sorted_vals) - 1, int(frac * len(sorted_vals)))]


def scan(date, windows):
    """-> per-date anchor-age stats, deduped by header.timestamp (archives repeat polls)."""
    anchor, header, veh_counts, seen = [], [], [], set()
    for w in windows:
        path = s3_get(date, w)
        if not path:
            continue
        with open(path, "rb") as fh:
            raw = gzip.decompress(fh.read())
        for line in io.BytesIO(raw):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                msg = gtfs_realtime_pb2.FeedMessage()
                msg.ParseFromString(base64.b64decode(rec["b"]))
            except Exception:
                continue
            hts = msg.header.timestamp
            if not hts or hts in seen:
                continue
            seen.add(hts)
            poll = rec["ts"] / 1000.0
            header.append(poll - hts)
            n = 0
            for e in msg.entity:
                vid = e.vehicle.vehicle.id if e.HasField("vehicle") else ""
                if not vid or not e.vehicle.timestamp:
                    continue
                if AGENCY and ("MTABC" if vid.startswith("MTABC") else "NYCT") != AGENCY:
                    continue
                anchor.append(poll - e.vehicle.timestamp)
                n += 1
            veh_counts.append(n)
    if not anchor:
        return None
    anchor.sort()
    header.sort()
    return dict(date=date, snaps=len(seen), veh=statistics.fmean(veh_counts),
                a_med=statistics.median(anchor), a_p90=q(anchor, 0.90), a_p99=q(anchor, 0.99),
                h_med=statistics.median(header), n=len(anchor))


def main():
    dates = sys.argv[1].split(",")
    start = sys.argv[2] if len(sys.argv) > 2 else "08-45"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    windows = expand_windows(start, n)
    print("prefix %s ; windows %s ; fleet %s"
          % (PREFIX, ", ".join(windows), AGENCY if AGENCY else "both agencies"))
    rows = [r for r in (scan(d, windows) for d in dates) if r]
    print()
    print("| date | snaps | avg vehicles | ANCHOR age med | p90 | p99 | feed header age med |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print("| %s | %d | %.0f | **%.0f s** | %.0f s | %.0f s | %.0f s |"
              % (r["date"], r["snaps"], r["veh"], r["a_med"], r["a_p90"], r["a_p99"], r["h_med"]))
    print()
    print("anchor age = poll time - vehicle.timestamp (age of the AVL fix each prediction is built on)")
    print("header age = poll time - header.timestamp (publication lag; what FeedStalenessSeconds tracks)")


if __name__ == "__main__":
    main()
