#!/usr/bin/env python3
"""Cut the first N vehicles out of an archived bucket, preserving arrival order.

For scaling tests. Unlike make-replay-fixture.py this applies no quality filter: it takes vehicles in
the order they first appear and keeps every record for them. That is deliberate. A filtered set is
what you want for a determinism fixture, because the filter guarantees the particle filter has enough
to converge on. It is the wrong thing for a timing test, because the full bucket contains buses whose
sign code is absent from the bundle, buses reporting latitude and longitude of zero, and buses that
never move - all of which cost less per record than a healthy bus. Filtering them out would make the
estimate optimistic.

Lines are written through byte for byte with the {"ts": ..., "b": ...} wrapper intact, in file order.
The archive is already in broker arrival order, so file order is the order the live engine consumed,
and no sorting is wanted.

Output carries operator badge numbers. Do not commit it. Run scrub-fixture.py first if you need to.

Usage:
  cut-vehicles.py <bucket.jsonl.gz> -n 100 -o /tmp/veh-100.jsonl
  aws s3 cp s3://.../08-45.jsonl.gz - | cut-vehicles.py - -n 500 -o /tmp/veh-500.jsonl
"""
import argparse
import collections
import gzip
import json
import sys


def open_source(path):
    if path == "-":
        buf = sys.stdin.buffer
        return gzip.open(buf, "rt") if buf.peek(2)[:2] == b"\x1f\x8b" else sys.stdin
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def vehicle_of(line):
    """(key, timeReceived) for a line, or None if it cannot be read."""
    try:
        outer = json.loads(line)
        env = json.loads(outer["b"])["RealtimeEnvelope"] if "b" in outer \
            else outer["RealtimeEnvelope"]
        v = env["CcLocationReport"]["vehicle"]
        return (v.get("agencydesignator"), v.get("vehicle-id")), int(env["timeReceived"])
    except (ValueError, KeyError, TypeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("-n", "--vehicles", type=int, default=100)
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    # Two passes would need the input twice, and stdin allows only one. So decide membership as
    # vehicles appear: the first N distinct keys are in, everything after is out.
    chosen = {}
    kept = []
    unreadable = 0
    total = 0

    with open_source(args.source) as fh:
        for line in fh:
            line = line.rstrip("\n").rstrip("\r")
            if not line.strip():
                continue
            total += 1
            got = vehicle_of(line)
            if got is None:
                unreadable += 1
                continue
            key, ts = got
            if key not in chosen:
                if len(chosen) >= args.vehicles:
                    continue
                chosen[key] = []
            chosen[key].append(ts)
            kept.append(line)

    if not kept:
        sys.exit("nothing selected")

    with open(args.out, "w") as out:
        out.write("\n".join(kept) + "\n")

    all_ts = [t for ts in chosen.values() for t in ts]
    counts = sorted(len(v) for v in chosen.values())
    print("read %d records, %d unreadable" % (total, unreadable))
    print("kept %d records for %d vehicles -> %s" % (len(kept), len(chosen), args.out))
    print("span %.1fs" % ((max(all_ts) - min(all_ts)) / 1000.0))
    print("records per vehicle: min %d  median %d  max %d"
          % (counts[0], counts[len(counts) // 2], counts[-1]))
    print("share of the source: %.2f%% of records" % (100.0 * len(kept) / max(1, total)))


if __name__ == "__main__":
    main()
