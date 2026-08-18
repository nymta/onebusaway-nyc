#!/usr/bin/env python3
"""How far out of order is the archive, and how big a reorder buffer does replay need?

Replay advances MutableClock to each record's own timestamp, and setMillis is a plain set, not a
max, so the clock follows whatever order records arrive in. Feeding it records out of order makes
time go backwards. A whole-span sort is not possible once replay covers days, so the question is
what window a streaming reorder buffer needs.

Lateness is the number that answers it: for each record, how far behind the newest timestamp seen
so far is it. A buffer of W seconds fixes every record whose lateness is at most W, so the residual
table below is just the tail of the lateness distribution read backwards.

Also checks the assumption replay already relies on - that each vehicle's own records are in order -
because isValidRecord compares per vehicle, not globally, and rejects anything older than that
vehicle's last seen record.

Streams. Holds one timestamp per vehicle and a histogram, nothing else, so span length does not
matter.

Usage:
  measure-lateness.py <bucket.jsonl.gz> [more.jsonl.gz ...]
  aws s3 cp s3://.../08-45.jsonl.gz - | measure-lateness.py -

Accepts archived lines, {"ts": <ms>, "b": "<envelope json>"}, and already-extracted envelope lines.
"""
import collections
import gzip
import json
import sys


def records(paths):
    """Yield (timeReceived, vehicleKey) in file order, streaming."""
    for path in paths:
        if path == "-":
            fh = gzip.open(sys.stdin.buffer, "rt") if _looks_gzipped(sys.stdin.buffer) \
                else sys.stdin
        elif path.endswith(".gz"):
            fh = gzip.open(path, "rt")
        else:
            fh = open(path)
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    outer = json.loads(line)
                    # Archived form wraps the envelope in a string; a fixture holds it directly.
                    env = json.loads(outer["b"])["RealtimeEnvelope"] if "b" in outer \
                        else outer["RealtimeEnvelope"]
                    veh = env["CcLocationReport"]["vehicle"]
                    yield int(env["timeReceived"]), (veh.get("agencydesignator"),
                                                    veh.get("vehicle-id"))
                except (ValueError, KeyError, TypeError):
                    continue


def _looks_gzipped(buf):
    head = buf.peek(2)[:2]
    return head == b"\x1f\x8b"


def main():
    paths = sys.argv[1:]
    if not paths:
        sys.exit(__doc__.strip())

    total = 0
    out_of_order = 0
    running_max = None
    first_ts = last_ts = None
    lateness_ms = []                       # one entry per out-of-order record only
    per_vehicle_last = {}
    per_vehicle_backwards = collections.Counter()
    vehicles = set()

    for ts, veh in records(paths):
        total += 1
        vehicles.add(veh)
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts

        if running_max is None or ts >= running_max:
            running_max = ts
        else:
            out_of_order += 1
            lateness_ms.append(running_max - ts)

        prev = per_vehicle_last.get(veh)
        if prev is not None and ts < prev:
            per_vehicle_backwards[veh] += 1
        per_vehicle_last[veh] = max(ts, prev) if prev is not None else ts

    if total == 0:
        sys.exit("no parseable records")

    print("records                 : %d" % total)
    print("vehicles                : %d" % len(vehicles))
    print("data span               : %.1fs" % ((last_ts - first_ts) / 1000.0))
    print("out of order (global)   : %d  (%.2f%%)" % (out_of_order, 100.0 * out_of_order / total))
    print()

    if not lateness_ms:
        print("Every record arrived in timestamp order. No reorder buffer needed.")
    else:
        lateness_ms.sort()

        def pct(p):
            return lateness_ms[min(len(lateness_ms) - 1, int(p * len(lateness_ms)))]

        print("lateness of the out-of-order records, in seconds")
        print("  p50 %.3f   p90 %.3f   p99 %.3f   p99.9 %.3f   max %.3f"
              % (pct(.50) / 1000.0, pct(.90) / 1000.0, pct(.99) / 1000.0,
                 pct(.999) / 1000.0, lateness_ms[-1] / 1000.0))
        print()
        print("residual after a reorder buffer of W seconds")
        print("  %-6s %12s %10s" % ("W", "still late", "of total"))
        for w in (0, 1, 2, 5, 10, 30, 60, 120, 300):
            still = sum(1 for x in lateness_ms if x > w * 1000)
            print("  %-6s %12d %9.4f%%" % (w, still, 100.0 * still / total))

    print()
    if per_vehicle_backwards:
        n = sum(per_vehicle_backwards.values())
        print("PER-VEHICLE ORDER IS NOT CLEAN: %d records across %d vehicles arrive before that"
              % (n, len(per_vehicle_backwards)))
        print("vehicle's previous record. isValidRecord will reject those regardless of buffering.")
        for veh, c in per_vehicle_backwards.most_common(5):
            print("    %-22s %d" % (str(veh), c))
    else:
        print("Per-vehicle order is clean: no vehicle's record arrives before its own previous one.")
        print("So a reorder buffer only has to satisfy the clock, not isValidRecord.")


if __name__ == "__main__":
    main()
