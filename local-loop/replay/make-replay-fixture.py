#!/usr/bin/env python3
"""Cut a small, replay-ready fixture out of an archived bustechGps bucket.

The archive stores one JSON object per line, {"ts": <broker ms>, "b": "<envelope JSON string>"}.
The value of "b" is already the {"RealtimeEnvelope": ...} document that OBA's
InputServiceImpl.deserializeMessage parses, so this writes those strings through byte-for-byte and
never re-serialises them. Re-encoding would risk changing key order or number formatting, and the
point of a fixture is that two runs see identical input.

Vehicles are ranked so the fixture actually exercises inference:

  * destSignCode present in the built bundle - an unknown DSC makes hasValidDsc false, which zeroes
    all three candidate-block sources, so the vehicle never appears in the feed at all. Validated
    against the bundle's own csv/dsc_statistics.csv rather than against the STIF.
  * a single DSC for the whole window - a bus changing sign code mid-window is a harder case.
  * distance actually travelled - a parked bus infers DEADHEAD, which is correct but proves nothing.
  * enough records - the particle filter needs a sequence to converge, not one fix.

Records are written in ascending (timeReceived, UUID) order. Globally the archive is ~33% out of
order on timeReceived, so a replay driver advancing a monotonic clock must sort; per vehicle it is
already clean, which is what isValidRecord actually compares.

Usage:
  make-replay-fixture.py <sample.jsonl.gz> [-n VEHICLES] [-o OUT] [--dsc-stats FILE]
"""
import argparse
import collections
import gzip
import json
import math
import os
import sys

MIN_RECORDS = 30          # below this the filter has too little to converge on
MIN_METRES = 300          # below this the bus is parked or crawling
MAX_BAD_FRAC = 0.05       # reject a vehicle whose fixes are mostly unusable

# Mean speed bounds, km/h. Measured over one 5-minute peak bucket, the fleet sits at p50 9.4,
# p90 21.3, p99 75.0. Thirteen of 4,640 vehicles exceed 100 km/h and are not buses: their positions
# and their own reported speed field agree with each other, so the records are internally consistent
# junk rather than a decoding error. Three of them outranked every real bus on distance travelled
# before this bound existed. The upper bound keeps genuine express running on a highway; the lower
# one skips buses that barely moved.
MIN_KMH = 5.0
MAX_KMH = 80.0

# Roughly the five boroughs plus a margin. Used only to exclude junk from the distance score, not to
# filter the fixture: the engine sees these records in production too and its own teleport/stale
# resets deal with them. In one 5-minute bucket, 1.5% of fixes carry latitude or longitude of exactly
# 0 (no GPS fix), and a further 0.2% fall outside the city - including ~220 records written at 1e5
# scale rather than 1e6, and a handful that decode to Minnesota.
NYC_BOX = (40_000_000, 41_500_000, -74_500_000, -73_000_000)


def usable(lat_udeg, lon_udeg):
    if not lat_udeg or not lon_udeg:
        return False
    lo_lat, hi_lat, lo_lon, hi_lon = NYC_BOX
    return lo_lat <= lat_udeg <= hi_lat and lo_lon <= lon_udeg <= hi_lon


def haversine_m(a, b):
    (lat1, lon1), (lat2, lon2) = a, b
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def load_valid_dscs(path):
    """dsc_statistics.csv is emitted by the bundle build: dsc,agency_id,trips_in_stif,routes."""
    if not path or not os.path.exists(path):
        return None
    valid = set()
    with open(path) as fh:
        next(fh, None)
        for line in fh:
            parts = line.rstrip("\n").split(",")
            if len(parts) >= 2:
                valid.add((parts[0].strip(), parts[1].strip()))
    return valid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample")
    ap.add_argument("-n", "--vehicles", type=int, default=5)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--dsc-stats", default=None,
                    help="bundle csv/dsc_statistics.csv, to reject DSCs the bundle cannot match")
    ap.add_argument("--min-kmh", type=float, default=MIN_KMH)
    ap.add_argument("--max-kmh", type=float, default=MAX_KMH,
                    help="mean-speed ceiling. Default %.0f keeps express; pass ~30 for local buses, "
                         "which the fleet-wide p90 of 21 km/h says are typical." % MAX_KMH)
    args = ap.parse_args()
    min_kmh, max_kmh = args.min_kmh, args.max_kmh

    valid_dscs = load_valid_dscs(args.dsc_stats)
    if valid_dscs is None:
        print("WARNING: no dsc_statistics.csv given; cannot check DSCs against the bundle",
              file=sys.stderr)

    # per vehicle: the raw "b" strings plus just enough decoded state to rank it
    records = collections.defaultdict(list)      # vid -> [(timeReceived, uuid, raw_b)]
    dscs = collections.defaultdict(collections.Counter)
    points = collections.defaultdict(list)
    agency = {}
    malformed = 0

    opener = gzip.open if args.sample.endswith(".gz") else open
    with opener(args.sample, "rt") as fh:
        for line in fh:
            try:
                outer = json.loads(line)
                raw = outer["b"]
                env = json.loads(raw)["RealtimeEnvelope"]
                rpt = env["CcLocationReport"]
                veh = rpt["vehicle"]
                vid = "%s_%s" % (veh["agencydesignator"], veh["vehicle-id"])
                tr = int(env["timeReceived"])
            except (ValueError, KeyError, TypeError):
                malformed += 1
                continue
            records[vid].append((tr, env.get("UUID", ""), raw))
            dscs[vid][str(rpt.get("destSignCode", ""))] += 1
            # keep the timestamp with the point: file order is ~33% out of order globally, so the
            # distance sum has to be computed over time-sorted points, not file-sorted ones
            points[vid].append((tr, rpt["latitude"], rpt["longitude"]))
            agency[vid] = veh["agencydesignator"]

    # rank
    scored = []
    for vid, recs in records.items():
        pts = sorted(points[vid])                       # by timeReceived
        good = [(tr, la / 1e6, lo / 1e6) for tr, la, lo in pts if usable(la, lo)]
        bad = len(pts) - len(good)
        if len(good) < 2:
            continue
        dist = sum(haversine_m(good[i - 1][1:], good[i][1:]) for i in range(1, len(good)))
        span_s = (good[-1][0] - good[0][0]) / 1000.0
        kmh = (dist / span_s * 3.6) if span_s > 0 else 0.0
        dsc, dsc_n = dscs[vid].most_common(1)[0]
        stable = dsc_n / len(recs)
        known = None if valid_dscs is None else ((dsc, agency[vid]) in valid_dscs)
        if len(recs) < MIN_RECORDS or dist < MIN_METRES:
            continue
        if bad / len(pts) > MAX_BAD_FRAC:
            continue
        if not (min_kmh <= kmh <= max_kmh):
            continue
        if known is False:
            continue
        scored.append((stable, dist, len(recs), vid, dsc, known, bad, kmh))
    # prefer a stable DSC, then distance travelled; both descending
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)

    if not scored:
        print("no vehicle met the thresholds", file=sys.stderr)
        return 1

    chosen = scored[:args.vehicles]
    out = args.out or os.path.join(os.path.dirname(args.sample) or ".", "fixture.jsonl")

    picked = []
    for row in chosen:
        picked.extend(records[row[3]])
    # The driver advances a monotonic clock, so the fixture must be globally ascending. Ties broken
    # on UUID so the order is a property of the data, not of dict or filesystem iteration - two runs
    # over the same bucket have to produce the same file for the double-run diff to mean anything.
    picked.sort(key=lambda r: (r[0], r[1]))       # (timeReceived, UUID)

    with open(out, "w") as fh:
        for _tr, _uuid, raw in picked:
            fh.write(raw.rstrip("\n") + "\n")

    print("read %d vehicles (%d malformed lines skipped)" % (len(records), malformed))
    print("%-18s %6s %6s %8s %7s %6s %5s %s"
          % ("vehicle", "recs", "dsc", "metres", "km/h", "dsc%", "bad", "in bundle"))
    for stable, dist, n, vid, dsc, known, bad, kmh in chosen:
        print("%-18s %6d %6s %8.0f %7.1f %5.0f%% %5d %s"
              % (vid, n, dsc, dist, kmh, stable * 100, bad,
                 "?" if known is None else ("yes" if known else "no")))
    span = (picked[-1][0] - picked[0][0]) / 1000.0
    print("\nwrote %s" % out)
    print("  %d records, %d vehicles, %.1f s of data time" % (len(picked), len(chosen), span))
    print("  replayed unpaced this is the whole point: 299 s of data time in well under a second")
    return 0


if __name__ == "__main__":
    sys.exit(main())
