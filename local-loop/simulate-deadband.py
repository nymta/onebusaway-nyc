#!/usr/bin/env python3
"""Offline what-if for the ingestion deadband: replay archived raw AVL and count the load it admits.

Why: the inference host is capacity-limited at peak (see FINDINGS-SUMMARY §2c) and the first proposed
remedy is widening the deadband so peak DEMAND falls back under sustainable throughput. That question
is answerable without touching the host and without waiting for the next peak, because
(a) the raw BusTech AVL stream is archived at s3://mtalirr/data-archiver/bustechGps/, and
(b) `InputServiceImpl.passesDeadband` is a pure per-vehicle function of (lat, lon, timeReported).

So this replays a real peak window and reports admitted fixes/s for a set of candidate settings.
Compare the result against the measured ceiling (~430-480 fixes/s = 32 cores / ~75 ms per fix) to
predict whether a setting removes the queue. It measures the DEMAND side only — it says nothing about
per-fix cost, which needs the host.

The keep/drop logic mirrors InputServiceImpl.java:183-216 exactly (rate cap, staleness failsafe,
distance-since-last-KEPT-fix), so numbers transfer directly.

Usage:
    python3 simulate-deadband.py 2026-07-30 08-45 [n_windows]
Env: ARCHIVE_CACHE (default /tmp/oba-archive-cache)
"""
import gzip, io, json, math, os, statistics, subprocess, sys
from collections import defaultdict

BUCKET = "s3://mtalirr/data-archiver"
PREFIX = "bustechGps"
CACHE = os.environ.get("ARCHIVE_CACHE", "/tmp/oba-archive-cache")

# (minMeters, minIntervalSec, maxAgeSec); the first row is what the host runs today.
CANDIDATES = [
    (10, 7, 30),
    (10, 5, 30),
    (10, 3, 30),
    (5, 5, 30),
    (10, 5, 20),
    (0, 5, 30),
    (10, 12, 30),
    (10, 15, 30),
]
# Sustainable throughput, DERIVED not assumed: at peak the queue is pinned and shedding runs
# ~16-32 fixes/s, so in equilibrium capacity = admitted demand (286/s, measured below) minus the
# shed rate => ~254-270 fixes/s. That implies ~114-126 ms of real per-fix cost at peak, well above
# the 75 ms seen off-peak, which is consistent with peak fixes being more expensive to infer.
CAPACITY_LO, CAPACITY_HI = 380, 405


def s3_get(date, window):
    key = "%s/%s/%s.jsonl.gz" % (PREFIX, date, window)
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
    return ["%02d-%02d" % (((hh * 60 + mm + 5 * i) // 60) % 24, (hh * 60 + mm + 5 * i) % 60)
            for i in range(n)]


def parse_iso_millis(s):
    """'2026-07-30T12:44:58.0-00:00' -> epoch ms. Hand-rolled: the format is fixed and this is hot."""
    try:
        date, rest = s.split("T", 1)
        y, mo, d = (int(x) for x in date.split("-"))
        tz_sign = 1
        for i, ch in enumerate(rest):
            if ch in "+-" and i > 0:
                clock, tz = rest[:i], rest[i:]
                tz_sign = -1 if ch == "-" else 1
                break
        else:
            clock, tz = rest.rstrip("Z"), "+00:00"
        hh, mi, sec = clock.split(":")
        secs = float(sec)
        tzh, tzm = (int(x) for x in tz[1:].split(":"))
        offset = tz_sign * (tzh * 3600 + tzm * 60)
        import calendar
        base = calendar.timegm((y, mo, d, int(hh), int(mi), 0, 0, 0, 0))
        return int((base + secs - offset) * 1000)
    except Exception:
        return None


def dist_m(lat1_u, lon1_u, lat2_u, lon2_u):
    lat1, lon1 = lat1_u / 1e6, lon1_u / 1e6
    lat2, lon2 = lat2_u / 1e6, lon2_u / 1e6
    d_lat, d_lon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


def load_fixes(date, windows):
    """-> [(key, timeReported_ms, lat_u, lon_u, timeReceived_ms)] sorted by RECEIPT time.

    Two clocks matter and they are not interchangeable. `timeReported` is the bus's own clock and some
    buses report garbage (epoch-adjacent or far-future values), so it cannot be used to measure rates.
    `timeReceived` is stamped server-side and is what the load-shedder uses. The deadband, however,
    keys off timeReported (InputServiceImpl:192), so we replay with that and measure rates with the
    other, exactly as production behaves.
    """
    fixes, seen = [], set()
    for w in windows:
        path = s3_get(date, w)
        if not path:
            continue
        with open(path, "rb") as fh:
            raw = gzip.decompress(fh.read())
        n0 = len(fixes)
        for line in io.BytesIO(raw):
            line = line.strip()
            if not line:
                continue
            try:
                env = json.loads(json.loads(line)["b"])["RealtimeEnvelope"]
                if env["UUID"] in seen:
                    continue
                seen.add(env["UUID"])
                r = env["CcLocationReport"]
                v = r["vehicle"]
                ts = parse_iso_millis(r["time-reported"])
                rec = int(env["timeReceived"])
                if ts is None or not rec:
                    continue
                fixes.append(("%s_%s" % (v["agencydesignator"], v["vehicle-id"]),
                              ts, r["latitude"], r["longitude"], rec))
            except Exception:
                continue
        print("  %s %s: +%d fixes" % (date, w, len(fixes) - n0))
    fixes.sort(key=lambda f: f[4])
    return fixes


def simulate(fixes, min_meters, min_interval_s, max_age_s):
    """Exact port of InputServiceImpl.passesDeadband, plus a tally of WHICH clause decided each fix.

    The reason breakdown is the point: if most admissions come from the staleness failsafe or the
    distance test rather than from clearing the rate cap, then widening `minIntervalSec` cannot reduce
    load much, however large you make it.
    """
    min_interval_ms, max_age_ms = min_interval_s * 1000, max_age_s * 1000
    last = {}
    kept = 0
    why = defaultdict(int)
    kept_per_vehicle = defaultdict(int)
    for key, now, lat, lon, _rec in fixes:
        prev = last.get(key)
        if prev is None:
            keep, reason = True, "first_seen"
        else:
            age = now - prev[2]
            if age < 0:
                keep, reason = True, "keep_clock_went_backwards"
            elif age < min_interval_ms:
                keep, reason = False, "drop_rate_cap"
            elif age >= max_age_ms:
                keep, reason = True, "keep_staleness_failsafe"
            elif dist_m(prev[0], prev[1], lat, lon) >= min_meters:
                keep, reason = True, "keep_moved"
            else:
                keep, reason = False, "drop_not_moved"
        why[reason] += 1
        if keep:
            last[key] = (lat, lon, now)
            kept += 1
            kept_per_vehicle[key] += 1
    return kept, kept_per_vehicle, why


def main():
    date = sys.argv[1]
    start = sys.argv[2] if len(sys.argv) > 2 else "08-45"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    windows = expand_windows(start, n)
    print("raw AVL replay: %s windows %s" % (date, ", ".join(windows)))
    fixes = load_fixes(date, windows)
    if not fixes:
        sys.exit("no fixes loaded")
    span_s = (fixes[-1][4] - fixes[0][4]) / 1000.0        # receipt clock, not the buses' clocks
    vehicles = len({f[0] for f in fixes})
    bad_clock = sum(1 for f in fixes if abs(f[1] - f[4]) > 3600_000)
    print("\ninbound: %d fixes over %.0f s from %d vehicles = **%.0f fixes/s** "
          "(%.1f s mean cadence/vehicle)"
          % (len(fixes), span_s, vehicles, len(fixes) / span_s, span_s / (len(fixes) / vehicles)))
    print("(%d fixes, %.1f%%, report a clock >1 h from receipt time — the deadband keys off the bus "
          "clock, so these mostly bypass it)" % (bad_clock, 100.0 * bad_clock / len(fixes)))
    print("measured sustainable throughput: ~%d-%d fixes/s\n" % (CAPACITY_LO, CAPACITY_HI))
    print("| minMeters | minIntervalSec | maxAgeSec | admitted fixes/s | vs inbound | "
          "mean cadence/veh | verdict |")
    print("|---|---|---|---|---|---|---|")
    rows = []
    for mm, mi, ma in CANDIDATES:
        kept, per_veh, why = simulate(fixes, mm, mi, ma)
        rate = kept / span_s
        cadence = statistics.mean([span_s / c for c in per_veh.values() if c]) if per_veh else 0
        if rate > CAPACITY_HI:
            verdict = "**over capacity** — queue forms"
        elif rate > CAPACITY_LO:
            verdict = "marginal"
        else:
            verdict = "**under capacity** — queue drains"
        tag = " *(today)*" if (mm, mi, ma) == CANDIDATES[0] else ""
        print("| %d%s | %d | %d | **%.0f** | %.0f%% | %.1f s | %s |"
              % (mm, tag, mi, ma, rate, 100.0 * kept / len(fixes), cadence, verdict))
        rows.append(((mm, mi, ma), why, kept))
    print("\n### Why each fix was admitted or dropped (this is what limits the deadband)\n")
    print("| setting | first seen | kept: moved | kept: staleness failsafe | kept: clock back | "
          "dropped: rate cap | dropped: not moved |")
    print("|---|---|---|---|---|---|---|")
    for (mm, mi, ma), why, kept in rows:
        tot = sum(why.values())
        f = lambda k: "%.1f%%" % (100.0 * why.get(k, 0) / tot)
        print("| %d m / %d s / %d s | %s | **%s** | **%s** | %s | %s | %s |"
              % (mm, mi, ma, f("first_seen"), f("keep_moved"), f("keep_staleness_failsafe"),
                 f("keep_clock_went_backwards"), f("drop_rate_cap"), f("drop_not_moved")))
    print("\nDemand side only: this does not model per-fix CPU cost, which needs the host.")


if __name__ == "__main__":
    main()
