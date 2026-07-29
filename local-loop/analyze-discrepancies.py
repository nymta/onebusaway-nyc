#!/usr/bin/env python3
"""Drill into the discrepancy classes found by compare-feeds.py.

For each snapshot round:
  A. same-route/different-trip pairs — parse the MTA trip_id start time (hundredths of
     minutes past midnight) to see HOW different the two trips are (same start, adjacent
     departure, etc.), and whether MTA gave our trip to a different vehicle.
  B. direction flips — examples with both trips' start times.
  C. different-route pairs — full detail.
  D. big ETA deltas (|ours − MTA| > 180 s, trip-matched vehicles only) — per-TripUpdate
     shape: delta at the FIRST shared stop vs the LAST (whole-trip offset vs accumulating
     drift), top routes/vehicles, horizon distribution.

Usage: python3 analyze-discrepancies.py [rounds] [interval_sec]   # default 2, 45
"""
import re, statistics, sys, time, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from google.transit import gtfs_realtime_pb2

OURS = "http://ec2-52-70-255-34.compute-1.amazonaws.com/tripUpdates"
MTA = "http://gtfsrt.prod.obanyc.com/tripUpdates"
TRIP_RE = re.compile(r"-(\d{6})_([^_]+)_(\d+)$")


def fetch(url):
    m = gtfs_realtime_pb2.FeedMessage()
    with urllib.request.urlopen(url, timeout=30) as r:
        m.ParseFromString(r.read())
    return m


def norm(ident):
    return ident.split("_", 1)[1] if "_" in ident and not ident.split("_")[0].isdigit() else ident


def parse_tu(msg):
    out = defaultdict(list)
    for e in msg.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        veh = norm(tu.vehicle.id) if tu.vehicle.id else None
        stops = []
        for stu in tu.stop_time_update:
            t = stu.arrival.time if stu.HasField("arrival") and stu.arrival.time else (
                stu.departure.time if stu.HasField("departure") and stu.departure.time else 0)
            if stu.stop_id and t:
                stops.append((norm(stu.stop_id), t))
        stops.sort(key=lambda s: s[1])
        rec = dict(trip=norm(tu.trip.trip_id), route=norm(tu.trip.route_id),
                   direction=tu.trip.direction_id if tu.trip.HasField("direction_id") else None,
                   stops=stops)
        if veh:
            out[veh].append(rec)
    for recs in out.values():
        recs.sort(key=lambda r: r["stops"][0][1] if r["stops"] else float("inf"))
    return dict(out)


def start_min(trip_id):
    """STIF-style trip ids encode origin departure as hundredths of minutes past midnight."""
    m = TRIP_RE.search(trip_id)
    return int(m.group(1)) / 100.0 if m else None


def run_suffix(trip_id):
    m = TRIP_RE.search(trip_id)
    return (m.group(2), m.group(3)) if m else (None, None)


def hhmm(minutes):
    return "%02d:%02d" % (int(minutes) // 60, int(minutes) % 60) if minutes is not None else "?"


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 45

    difftrip = []          # (veh, route, our_trip, mta_trip, dstart_min, mta_has_our_trip_on_other_veh)
    dirflips = []
    diffroute = []
    big = []               # dict per big-delta TU pair
    shape_counter = Counter()
    big_routes = Counter()
    big_horizon = Counter()
    all_pairs = 0
    matched_tu = 0

    for r in range(rounds):
        if r:
            time.sleep(interval)
        with ThreadPoolExecutor(2) as ex:
            ours_msg, mta_msg = list(ex.map(fetch, [OURS, MTA]))
        now = time.time()
        ours, mta = parse_tu(ours_msg), parse_tu(mta_msg)
        mta_trip_to_veh = {rec["trip"]: v for v, recs in mta.items() for rec in recs}

        for veh in set(ours) & set(mta):
            o = ours[veh][0]
            m = next((rec for rec in mta[veh] if rec["trip"] == o["trip"]), mta[veh][0])
            all_pairs += 1
            if o["trip"] != m["trip"]:
                so, sm = start_min(o["trip"]), start_min(m["trip"])
                d = (so - sm) if (so is not None and sm is not None) else None
                other_veh = mta_trip_to_veh.get(o["trip"])
                if o["route"] != m["route"]:
                    diffroute.append((veh, o["route"], o["trip"], m["route"], m["trip"], d, other_veh))
                elif o["direction"] is not None and m["direction"] is not None and o["direction"] != m["direction"]:
                    dirflips.append((veh, o["route"], o["trip"], m["trip"], d, other_veh))
                else:
                    difftrip.append((veh, o["route"], o["trip"], m["trip"], d, other_veh))
                continue
            # trip-matched: ETA delta shape along the trip
            matched_tu += 1
            mstops = dict(m["stops"])
            shared = [(i, sid, ot, mstops[sid]) for i, (sid, ot) in enumerate(o["stops"])
                      if sid in mstops and ot > now - 30 and mstops[sid] > now - 30]
            if len(shared) < 2:
                continue
            deltas = [ot - mt for _, _, ot, mt in shared]
            if max(abs(d) for d in deltas) <= 180:
                continue
            first, last = deltas[0], deltas[-1]
            n_big = sum(1 for d in deltas if abs(d) > 180)
            if abs(first) > 120:
                shape = "offset_from_start"
            elif abs(last) - abs(first) > 120:
                shape = "drift_grows_downstream"
            else:
                shape = "mid_trip_bump"
            shape_counter[shape] += 1
            big_routes[o["route"]] += 1
            for i, sid, ot, mt in shared:
                if abs(ot - mt) > 180:
                    h = (mt - now) / 60.0
                    big_horizon["0-5" if h < 5 else "5-15" if h < 15 else "15-30" if h < 30 else "30+"] += 1
            big.append(dict(veh=veh, route=o["route"], trip=o["trip"], n=len(shared),
                            n_big=n_big, first=first, last=last, shape=shape,
                            max_abs=max(deltas, key=abs)))
        print("round %d/%d done" % (r + 1, rounds))

    out = []
    p = lambda s="": (print(s), out.append(s))
    p("=== A. same-route / different-trip (%d pairs of %d total) ===" % (len(difftrip), all_pairs))
    dstarts = [abs(d) for *_, d, _ in difftrip if d is not None]
    if dstarts:
        p("start-time gap |ours−MTA| (min): median %.1f  mean %.1f  max %.0f ; same-start(<1min)=%d ; ≤10min=%d ; >20min=%d"
          % (statistics.median(dstarts), statistics.fmean(dstarts), max(dstarts),
             sum(1 for d in dstarts if d < 1), sum(1 for d in dstarts if d <= 10),
             sum(1 for d in dstarts if d > 20)))
    reassigned = sum(1 for *_, ov in difftrip if ov is not None)
    p("our trip exists in MTA feed on a DIFFERENT vehicle: %d/%d" % (reassigned, len(difftrip)))
    for veh, rt, ot, mt, d, ov in difftrip[:15]:
        p("  %s %s: ours %s(%s) vs MTA %s(%s) gap=%s min; our-trip-on-other-veh=%s"
          % (veh, rt, ot.split("-")[-1], hhmm(start_min(ot)), mt.split("-")[-1], hhmm(start_min(mt)),
             "%.0f" % d if d is not None else "?", ov))
    p()
    p("=== B. direction flips (%d) ===" % len(dirflips))
    for veh, rt, ot, mt, d, ov in dirflips[:10]:
        p("  %s %s: ours %s(%s) vs MTA %s(%s) gap=%s min; our-trip-on-other-veh=%s"
          % (veh, rt, ot.split("-")[-1], hhmm(start_min(ot)), mt.split("-")[-1], hhmm(start_min(mt)),
             "%.0f" % d if d is not None else "?", ov))
    p()
    p("=== C. different route (%d) ===" % len(diffroute))
    for veh, orr, ot, mr, mt, d, ov in diffroute[:10]:
        p("  %s: ours %s %s vs MTA %s %s; our-trip-on-other-veh=%s" % (veh, orr, ot, mr, mt, ov))
    p()
    p("=== D. big ETA deltas, trip-matched TUs (|delta|>180s in %d of %d matched TUs) ===" % (len(big), matched_tu))
    p("shape: %s" % dict(shape_counter))
    p("  offset_from_start   = already >2 min apart at the bus's NEXT stop (position/deviation disagreement)")
    p("  drift_grows_downstream = agree near-term, diverge along the trip (link-time / blend difference)")
    p("top routes: %s" % big_routes.most_common(12))
    p("big-delta stop-predictions by horizon (min): %s" % dict(big_horizon))
    ex = sorted(big, key=lambda b: -abs(b["max_abs"]))[:12]
    for b in ex:
        p("  %s %s %s: shared=%d big=%d first=%+.0fs last=%+.0fs max=%+.0fs shape=%s"
          % (b["veh"], b["route"], b["trip"].split("-")[-1], b["n"], b["n_big"],
             b["first"], b["last"], b["max_abs"], b["shape"]))
    with open("/Users/timothyshertzer/Documents/repos/onebusaway-nyc/local-loop/discrepancy-detail.txt", "w") as fh:
        fh.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
