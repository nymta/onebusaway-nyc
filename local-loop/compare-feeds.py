#!/usr/bin/env python3
"""Live side-by-side comparison: our EC2 GTFS-RT vs MTA's official OBA-NYC GTFS-RT.

Not the full scoring harness (no ground truth) — a structural/pattern check:
  1. Feed health: staleness, entity/trip/vehicle/route counts.
  2. Vehicle overlap (both feeds ride the same BusTech AVL, so vehicles should broadly match).
  3. TRIP MATCHING: for vehicles present in both feeds, do the two inference engines assign
     the same trip_id? Classified as: same trip / same route+direction but different trip /
     same route different direction / different route entirely.
  4. PREDICTION DELTAS: for (vehicle, trip, stop) tuples present in both, ours − MTA in
     seconds, bucketed by prediction horizon, plus local vs express split.
  5. Coverage: stops-per-trip depth, routes present in only one feed.

Usage:
    python3 compare-feeds.py [rounds] [interval_sec]     # default 3 rounds, 60 s apart
Env overrides: OURS_BASE, MTA_BASE.
Writes: feed-comparison-report.md next to this script.
"""
import os, statistics, sys, time, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from google.transit import gtfs_realtime_pb2

OURS_BASE = os.environ.get("OURS_BASE", "http://ec2-52-70-255-34.compute-1.amazonaws.com")
MTA_BASE = os.environ.get("MTA_BASE", "http://gtfsrt.prod.obanyc.com")
REPORT = os.environ.get("REPORT_OUT",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "feed-comparison-report.md"))
EXPRESS_PREFIXES = ("SIM", "BXM", "QM", "BM", "X")


def fetch_feed(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        msg = gtfs_realtime_pb2.FeedMessage()
        msg.ParseFromString(r.read())
        return msg


def norm(ident):
    """Strip an agency prefix like 'MTA NYCT_' / 'MTABC_' if present."""
    return ident.split("_", 1)[1] if "_" in ident and not ident.split("_")[0].isdigit() else ident


def is_express(route_id):
    return route_id.upper().startswith(EXPRESS_PREFIXES)


def parse_trip_updates(msg):
    """vehicle_norm -> [dict(trip, route, direction, stops={stop_id: pred_epoch}), ...]

    MTA's feed carries multiple TripUpdates per vehicle (current trip + upcoming trips);
    keep them all, sorted so the CURRENT trip (earliest first prediction) is first.
    """
    out = defaultdict(list)
    for e in msg.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        veh = norm(tu.vehicle.id) if tu.vehicle.id else None
        stops = {}
        for stu in tu.stop_time_update:
            t = stu.arrival.time if stu.HasField("arrival") and stu.arrival.time else (
                stu.departure.time if stu.HasField("departure") and stu.departure.time else 0)
            if stu.stop_id and t:
                stops[norm(stu.stop_id)] = t
        rec = dict(trip=norm(tu.trip.trip_id), route=norm(tu.trip.route_id),
                   direction=tu.trip.direction_id if tu.trip.HasField("direction_id") else None,
                   stops=stops, raw_vehicle=tu.vehicle.id, raw_trip=tu.trip.trip_id)
        if veh:
            out[veh].append(rec)
    for recs in out.values():
        recs.sort(key=lambda r: min(r["stops"].values()) if r["stops"] else float("inf"))
    return dict(out)


def snapshot():
    urls = [OURS_BASE + "/tripUpdates", MTA_BASE + "/tripUpdates"]
    with ThreadPoolExecutor(2) as ex:
        ours_msg, mta_msg = list(ex.map(fetch_feed, urls))
    now = time.time()
    return dict(now=now,
                ours=dict(msg=ours_msg, tu=parse_trip_updates(ours_msg),
                          age=now - ours_msg.header.timestamp),
                mta=dict(msg=mta_msg, tu=parse_trip_updates(mta_msg),
                         age=now - mta_msg.header.timestamp))


def pct(a, b):
    return 100.0 * a / b if b else 0.0


def delta_stats(deltas):
    if not deltas:
        return None
    a = sorted(deltas)
    n = len(a)
    return dict(n=n, median=statistics.median(a), mean=statistics.fmean(a),
                mae=statistics.fmean(abs(d) for d in a),
                p10=a[int(0.10 * n)], p90=a[min(n - 1, int(0.90 * n))],
                w60=pct(sum(1 for d in a if abs(d) <= 60), n),
                w180=pct(sum(1 for d in a if abs(d) <= 180), n),
                w300=pct(sum(1 for d in a if abs(d) <= 300), n))


def fmt_stats(label, s):
    if not s:
        return "| %s | 0 | – | – | – | – | – | – | – |" % label
    return ("| %s | %d | %+.0f | %+.0f | %.0f | %+.0f / %+.0f | %.0f%% | %.0f%% | %.0f%% |"
            % (label, s["n"], s["median"], s["mean"], s["mae"], s["p10"], s["p90"],
               s["w60"], s["w180"], s["w300"]))


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60

    round_summaries = []
    pooled = defaultdict(list)          # horizon bucket -> deltas (ours − mta, sec)
    pooled_class = defaultdict(list)    # 'local'/'express' -> deltas
    match_counter = Counter()
    diff_trip_examples = []
    diff_route_examples = []
    stop_depth = dict(ours=[], mta=[])
    tu_depth = dict(ours=[], mta=[])
    route_sets = dict(ours=set(), mta=set())
    veh_totals = Counter()

    for r in range(rounds):
        if r:
            time.sleep(interval)
        snap = snapshot()
        ours, mta, now = snap["ours"]["tu"], snap["mta"]["tu"], snap["now"]
        both = set(ours) & set(mta)
        veh_totals.update(ours=len(ours), mta=len(mta), both=len(both))
        route_sets["ours"] |= {r["route"] for recs in ours.values() for r in recs if r["route"]}
        route_sets["mta"] |= {r["route"] for recs in mta.values() for r in recs if r["route"]}
        tu_depth["ours"].extend(len(v) for v in ours.values())
        tu_depth["mta"].extend(len(v) for v in mta.values())

        n_delta_round = 0
        for veh in both:
            # our current trip vs MTA's set of trips for this vehicle: if our trip is among
            # them (even if not MTA's current), pair those; else pair current-vs-current
            o = ours[veh][0]
            m = next((rec for rec in mta[veh] if rec["trip"] == o["trip"]), mta[veh][0])
            stop_depth["ours"].append(len(o["stops"]))
            stop_depth["mta"].append(len(m["stops"]))
            if o["trip"] == m["trip"]:
                match_counter["same_trip"] += 1
            elif o["route"] == m["route"]:
                if o["direction"] is not None and m["direction"] is not None and o["direction"] != m["direction"]:
                    match_counter["same_route_diff_direction"] += 1
                else:
                    match_counter["same_route_diff_trip"] += 1
                if len(diff_trip_examples) < 12:
                    diff_trip_examples.append((veh, o["route"], o["trip"], m["trip"]))
            else:
                match_counter["diff_route"] += 1
                if len(diff_route_examples) < 12:
                    diff_route_examples.append((veh, o["route"], o["trip"], m["route"], m["trip"]))
                continue  # different route: prediction deltas meaningless
            # prediction deltas on shared future stops (same vehicle; trip may differ within route)
            for stop, ot in o["stops"].items():
                mt = m["stops"].get(stop)
                if not mt or mt < now - 30 or ot < now - 30:
                    continue
                horizon = (mt - now) / 60.0
                bucket = ("0-5 min" if horizon < 5 else "5-15 min" if horizon < 15
                          else "15-30 min" if horizon < 30 else "30+ min")
                d = ot - mt
                pooled[bucket].append(d)
                pooled["ALL"].append(d)
                pooled_class["express" if is_express(o["route"]) else "local"].append(d)
                n_delta_round += 1

        round_summaries.append(dict(
            t=time.strftime("%H:%M:%S", time.localtime(now)),
            ours_veh=len(ours), mta_veh=len(mta), both=len(both),
            ours_age=snap["ours"]["age"], mta_age=snap["mta"]["age"],
            deltas=n_delta_round))
        print("round %d/%d: ours=%d veh (age %.0fs)  mta=%d veh (age %.0fs)  overlap=%d  deltas=%d"
              % (r + 1, rounds, len(ours), snap["ours"]["age"], len(mta), snap["mta"]["age"],
                 len(both), n_delta_round))

    lines = []
    out = lambda s="": lines.append(s)
    out("# Our EC2 GTFS-RT vs MTA official GTFS-RT — pattern comparison")
    out()
    out("- generated: %s   ·   %d snapshot round(s), %d s apart" % (
        time.strftime("%Y-%m-%d %H:%M:%S %Z"), rounds, interval))
    out("- ours: `%s/tripUpdates`   ·   MTA: `%s/tripUpdates`" % (OURS_BASE, MTA_BASE))
    out("- delta convention: **ours − MTA, in seconds** (positive = we predict a LATER arrival)")
    out()
    out("## Snapshots")
    out()
    out("| round | time | ours vehicles | ours feed age | MTA vehicles | MTA feed age | vehicle overlap | matched stop-predictions |")
    out("|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(round_summaries, 1):
        out("| %d | %s | %d | %.0f s | %d | %.0f s | %d (%.0f%% of ours) | %d |"
            % (i, s["t"], s["ours_veh"], s["ours_age"], s["mta_veh"], s["mta_age"],
               s["both"], pct(s["both"], s["ours_veh"]), s["deltas"]))
    out()
    total_pairs = sum(match_counter.values())
    out("## Trip matching (vehicles present in both feeds, per-round pairs pooled)")
    out()
    out("| classification | pairs | share |")
    out("|---|---|---|")
    for key, label in (("same_trip", "same trip_id"),
                       ("same_route_diff_trip", "same route, different trip_id"),
                       ("same_route_diff_direction", "same route, different direction"),
                       ("diff_route", "different route")):
        out("| %s | %d | %.1f%% |" % (label, match_counter[key], pct(match_counter[key], total_pairs)))
    out()
    if diff_trip_examples:
        out("Same-route/different-trip examples (vehicle, route, our trip, MTA trip):")
        for veh, rt, ot, mt in diff_trip_examples:
            out("- `%s` %s: ours `%s` vs MTA `%s`" % (veh, rt, ot, mt))
        out()
    if diff_route_examples:
        out("Different-route examples (vehicle: ours route/trip vs MTA route/trip):")
        for veh, orr, ot, mr, mt in diff_route_examples:
            out("- `%s`: ours %s `%s` vs MTA %s `%s`" % (veh, orr, ot, mr, mt))
        out()
    out("## Prediction deltas (ours − MTA, seconds; same vehicle+stop, route agrees)")
    out()
    hdr = "| bucket | n | median | mean | MAE | p10/p90 | ≤1 min | ≤3 min | ≤5 min |"
    out(hdr)
    out("|---|---|---|---|---|---|---|---|---|")
    for b in ("ALL", "0-5 min", "5-15 min", "15-30 min", "30+ min"):
        out(fmt_stats(b, delta_stats(pooled.get(b, []))))
    out()
    out("By service class (horizon pooled):")
    out()
    out(hdr)
    out("|---|---|---|---|---|---|---|---|---|")
    for c in ("local", "express"):
        out(fmt_stats(c, delta_stats(pooled_class.get(c, []))))
    out()
    out("## Coverage")
    out()
    if stop_depth["ours"]:
        out("- stops predicted per TripUpdate (overlapping vehicles): ours median %d / mean %.1f, MTA median %d / mean %.1f"
            % (statistics.median(stop_depth["ours"]), statistics.fmean(stop_depth["ours"]),
               statistics.median(stop_depth["mta"]), statistics.fmean(stop_depth["mta"])))
    out("- TripUpdates per vehicle: ours mean %.2f, MTA mean %.2f (MTA >1 = current + upcoming trips)"
        % (statistics.fmean(tu_depth["ours"]), statistics.fmean(tu_depth["mta"])))
    only_ours = sorted(route_sets["ours"] - route_sets["mta"])
    only_mta = sorted(route_sets["mta"] - route_sets["ours"])
    out("- routes seen only in OURS (%d): %s" % (len(only_ours), ", ".join(only_ours) or "—"))
    out("- routes seen only in MTA (%d): %s" % (len(only_mta), ", ".join(only_mta) or "—"))
    out("- avg vehicles/round: ours %.0f, MTA %.0f, overlap %.0f"
        % (veh_totals["ours"] / rounds, veh_totals["mta"] / rounds, veh_totals["both"] / rounds))
    out()

    text = "\n".join(lines) + "\n"
    with open(REPORT, "w") as fh:
        fh.write(text)
    print("\nreport written -> %s" % REPORT)
    print(text)


if __name__ == "__main__":
    main()
