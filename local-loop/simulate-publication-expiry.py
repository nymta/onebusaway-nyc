#!/usr/bin/env python3
"""Would a publication expiry remove the buses we publish and prod doesn't? Test it against the archives.

Population A (FINDINGS-SUMMARY-V2 §4.11) is the ~40-55 buses present in our VehiclePositions but not
prod's. The leading hypothesis is that we have NO age-based expiry when building the feed — we keep a bus
in it indefinitely after its last AVL report — while prod drops them at ~120 s (`vtw.maxActiveVehicleAgeSecs`
defaults to 120 and is inert in our deployment). This tests that hypothesis without shipping anything.

It answers three questions:

  1. What cutoff does prod actually appear to use? If their published position ages stop dead at some
     value rather than tailing off, that ceiling IS their expiry — measured, not assumed.
  2. How much of population A would a given cutoff remove?           (the benefit)
  3. How many buses prod DOES publish would we wrongly drop?          (the cost)

(2) and (3) together are the whole decision: an expiry is only worth shipping if it removes mostly
ours-only buses and almost no shared ones. Both feeds are archived with per-vehicle timestamps, so this
is directly measurable rather than a guess.

Usage: python3 simulate-publication-expiry.py 2026-07-30 08-45 [n_windows]
Env: ARCHIVE_CACHE (default /tmp/oba-archive-cache)
"""
import base64, gzip, io, json, os, statistics, subprocess, sys
from google.transit import gtfs_realtime_pb2

BUCKET = "s3://mtalirr/data-archiver"
OURS_PREFIX = "obaEc2VehiclePositions"
PROD_PREFIX = "busGtfsRt"                 # carries BOTH trip_update and vehicle entities
CACHE = os.environ.get("ARCHIVE_CACHE", "/tmp/oba-archive-cache")
PAIR_TOL_S = 15
THRESHOLDS = [60, 90, 120, 150, 180, 240, 300]


def s3_get(prefix, date, window):
    key = "%s/%s/%s.b64.gz" % (prefix, date, window)
    local = os.path.join(CACHE, key.replace("/", "__"))
    if not os.path.exists(local):
        os.makedirs(CACHE, exist_ok=True)
        r = subprocess.run(["aws", "s3", "cp", "%s/%s" % (BUCKET, key), local, "--quiet"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  !! missing %s" % key)
            return None
    return local


def expand_windows(start, n):
    hh, mm = (int(x) for x in start.split("-"))
    return ["%02d-%02d" % (((hh * 60 + mm + 5 * i) // 60) % 24, (hh * 60 + mm + 5 * i) % 60)
            for i in range(n)]


def snapshots(path):
    """-> {header_ts: FeedMessage}, deduped by header timestamp."""
    out = {}
    with open(path, "rb") as fh:
        raw = gzip.decompress(fh.read())
    for line in io.BytesIO(raw):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            m = gtfs_realtime_pb2.FeedMessage()
            m.ParseFromString(base64.b64decode(rec["b"]))
        except Exception:
            continue
        if m.header.timestamp and m.header.timestamp not in out:
            out[m.header.timestamp] = m
    return out


def vehicles_with_age(msg, ref_ts):
    """-> {vehicle_id: age_seconds} from vehicle entities, aged against the feed's own header."""
    out = {}
    for e in msg.entity:
        if e.HasField("vehicle") and e.vehicle.vehicle.id and e.vehicle.timestamp:
            out[e.vehicle.vehicle.id] = ref_ts - e.vehicle.timestamp
    return out


def pct(a, b):
    return 100.0 * a / b if b else 0.0


def q(sorted_vals, f):
    return sorted_vals[min(len(sorted_vals) - 1, int(f * len(sorted_vals)))] if sorted_vals else float("nan")


def main():
    date = sys.argv[1]
    start = sys.argv[2] if len(sys.argv) > 2 else "08-45"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    windows = expand_windows(start, n)
    print("windows: %s" % ", ".join(windows))

    prod_ages, shared_ages, ours_only_ages = [], [], []
    n_pairs = 0

    for w in windows:
        op, pp = s3_get(OURS_PREFIX, date, w), s3_get(PROD_PREFIX, date, w)
        if not op or not pp:
            continue
        osnaps, psnaps = snapshots(op), snapshots(pp)
        pts = sorted(psnaps)
        if not osnaps or not pts:
            continue
        print("  %s %s: ours %d snaps, prod %d snaps" % (date, w, len(osnaps), len(pts)))
        for ots in sorted(osnaps):
            p_ts = min(pts, key=lambda t: abs(t - ots))
            if abs(p_ts - ots) > PAIR_TOL_S:
                continue
            n_pairs += 1
            ours = vehicles_with_age(osnaps[ots], ots)
            prod = vehicles_with_age(psnaps[p_ts], p_ts)
            prod_ages.extend(prod.values())
            for vid, age in ours.items():
                (shared_ages if vid in prod else ours_only_ages).append(age)

    if not n_pairs:
        sys.exit("no paired snapshots")
    prod_ages.sort(); shared_ages.sort(); ours_only_ages.sort()
    print("\n%d paired snapshots\n" % n_pairs)

    print("=== 1. What cutoff does prod appear to use? (their published position ages) ===")
    print("   median %.0f s | p90 %.0f s | p99 %.0f s | p99.9 %.0f s | MAX %.0f s"
          % (statistics.median(prod_ages), q(prod_ages, .90), q(prod_ages, .99),
             q(prod_ages, .999), prod_ages[-1]))
    over = [(t, sum(1 for a in prod_ages if a > t)) for t in (120, 180, 300)]
    print("   prod vehicles older than: %s" % ", ".join("%ds=%d (%.3f%%)" % (t, c, pct(c, len(prod_ages)))
                                                        for t, c in over))
    print("   READ: a hard ceiling with ~nothing beyond it IS prod's expiry.\n")

    print("=== 2. Our two populations, by age ===")
    for lbl, a in (("shared with prod", shared_ages), ("OURS-ONLY", ours_only_ages)):
        print("   %-17s n=%6d  median %3.0f s | p90 %4.0f s | p99 %4.0f s | max %4.0f s"
              % (lbl, len(a), statistics.median(a), q(a, .90), q(a, .99), a[-1]))
    print()

    print("=== 3. Simulated expiry: benefit vs cost ===")
    print("| cutoff | ours-only removed | ours-only remaining | SHARED wrongly removed |")
    print("|---|---|---|---|")
    for t in THRESHOLDS:
        d_oo = sum(1 for a in ours_only_ages if a > t)
        d_sh = sum(1 for a in shared_ages if a > t)
        print("| %d s | %d of %d (**%.0f%%**) | %d (%.0f%% of today) | %d (%.2f%% of shared) |"
              % (t, d_oo, len(ours_only_ages), pct(d_oo, len(ours_only_ages)),
                 len(ours_only_ages) - d_oo, pct(len(ours_only_ages) - d_oo, len(ours_only_ages)),
                 d_sh, pct(d_sh, len(shared_ages))))
    print("\nA cutoff is worth shipping where 'ours-only removed' is high and 'shared wrongly removed'")
    print("is ~0. Per snapshot, divide counts by %d." % n_pairs)


if __name__ == "__main__":
    main()
