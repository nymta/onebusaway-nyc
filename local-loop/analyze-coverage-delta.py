#!/usr/bin/env python3
"""Explain the vehicle-count difference between our feed and prod's, instead of just reporting it.

The headline "we track 3,290 buses vs prod's 3,321" is a NET figure and hides the real structure: there
are buses only prod has AND buses only we have, and they have different causes. This splits the two
populations and characterises each, so the delta can be attributed rather than guessed at.

The diagnostic questions it answers:

  OURS-ONLY (we publish, prod doesn't)
    - Are they stale? If their position age skews old while prod's live buses are fresh, the cause is a
      publication TTL difference: we keep a vehicle in the feed longer after its last report than prod.
    - Are they in prod's TripUpdates? If not in EITHER prod feed, prod is suppressing them entirely
      (e.g. unassigned/ghost-bus filtering), not merely disagreeing about them.

  PROD-ONLY (prod publishes, we don't)
    - Are they fresh in prod's feed? If prod has a current position and we have nothing, the bus is
      reporting and our inference is not placing it — a real coverage gap.
    - Which routes/agency? Concentration on a few routes means a schedule-data gap (the B1/MTABC
      signature); a flat spread across many routes means per-vehicle inference failures.

Usage: python3 analyze-coverage-delta.py [rounds] [interval]     # default 2 rounds, 30 s
Env overrides: OURS_BASE, MTA_BASE.
"""
import os, statistics, sys, time, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from google.transit import gtfs_realtime_pb2

OURS = os.environ.get("OURS_BASE", "http://ec2-52-70-255-34.compute-1.amazonaws.com")
MTA = os.environ.get("MTA_BASE", "http://gtfsrt.prod.obanyc.com")


def fetch(url):
    m = gtfs_realtime_pb2.FeedMessage()
    with urllib.request.urlopen(url, timeout=30) as r:
        m.ParseFromString(r.read())
    return m


def parse_vp(msg, now):
    """-> {vehicle_id: dict(route, age)}"""
    out = {}
    for e in msg.entity:
        if not e.HasField("vehicle") or not e.vehicle.vehicle.id:
            continue
        v = e.vehicle
        out[v.vehicle.id] = dict(route=v.trip.route_id.split("_")[-1],
                                 age=(now - v.timestamp) if v.timestamp else None)
    return out


def parse_tu_vehicles(msg):
    return {e.trip_update.vehicle.id for e in msg.entity
            if e.HasField("trip_update") and e.trip_update.vehicle.id}


def agency(vid):
    return "MTABC" if vid.startswith("MTABC") else "NYCT"


def q(sorted_vals, frac):
    if not sorted_vals:
        return float("nan")
    return sorted_vals[min(len(sorted_vals) - 1, int(frac * len(sorted_vals)))]


def describe(ages):
    a = sorted(x for x in ages if x is not None)
    if not a:
        return "no timestamps"
    return ("median %3.0f s, p90 %3.0f s, max %4.0f s"
            % (statistics.median(a), a[min(len(a) - 1, int(.9 * len(a)))], a[-1]))


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    ours_only_ages, prod_only_ages = [], []
    ours_only_not_in_prod_tu = 0
    ours_only_total = prod_only_total = 0
    prod_only_routes, prod_only_agency = Counter(), Counter()
    ours_only_routes, ours_only_agency = Counter(), Counter()
    both_ages_ours, both_ages_prod = [], []
    counts = defaultdict(list)

    for r in range(rounds):
        if r:
            time.sleep(interval)
        with ThreadPoolExecutor(4) as ex:
            ovp, otu, mvp, mtu = list(ex.map(fetch, [OURS + "/vehiclePositions", OURS + "/tripUpdates",
                                                     MTA + "/vehiclePositions", MTA + "/tripUpdates"]))
        now = time.time()
        ov, mv = parse_vp(ovp, now), parse_vp(mvp, now)
        m_tu = parse_tu_vehicles(mtu)

        both = set(ov) & set(mv)
        ours_only = set(ov) - set(mv)
        prod_only = set(mv) - set(ov)
        counts["ours"].append(len(ov)); counts["prod"].append(len(mv))
        counts["both"].append(len(both)); counts["ours_only"].append(len(ours_only))
        counts["prod_only"].append(len(prod_only))

        ours_only_total += len(ours_only)
        prod_only_total += len(prod_only)
        for v in ours_only:
            ours_only_ages.append(ov[v]["age"])
            ours_only_agency[agency(v)] += 1
            ours_only_routes[ov[v]["route"] or "(none)"] += 1
            if v not in m_tu:
                ours_only_not_in_prod_tu += 1
        for v in prod_only:
            prod_only_ages.append(mv[v]["age"])
            prod_only_agency[agency(v)] += 1
            prod_only_routes[mv[v]["route"] or "(none)"] += 1
        for v in both:
            both_ages_ours.append(ov[v]["age"]); both_ages_prod.append(mv[v]["age"])
        print("round %d: ours %d, prod %d, both %d | ours-only %d, prod-only %d"
              % (r + 1, len(ov), len(mv), len(both), len(ours_only), len(prod_only)))

    avg = lambda k: statistics.fmean(counts[k])
    print("\n%s" % time.strftime("%Y-%m-%d %H:%M:%S %Z"))
    print("=" * 78)
    print("NET difference: ours %.0f vs prod %.0f  =  %+.0f" % (avg("ours"), avg("prod"),
                                                                avg("ours") - avg("prod")))
    print("...but that net hides two separate populations:")
    print("   in both feeds : %.0f" % avg("both"))
    print("   OURS only     : %.0f   (we publish, prod does not)" % avg("ours_only"))
    print("   PROD only     : %.0f   (prod publishes, we do not)" % avg("prod_only"))
    print("=" * 78)

    print("\n--- baseline: position age of buses BOTH feeds have ---")
    print("   ours: %s" % describe(both_ages_ours))
    print("   prod: %s" % describe(both_ages_prod))

    print("\n--- OURS-ONLY: why does prod not have them? ---")
    print("   their age in OUR feed: %s" % describe(ours_only_ages))
    print("   absent from prod's TripUpdates too: %d of %d (%.0f%%)"
          % (ours_only_not_in_prod_tu, ours_only_total,
             100.0 * ours_only_not_in_prod_tu / max(1, ours_only_total)))
    print("   by agency: %s" % dict(ours_only_agency))
    print("   top routes: %s" % ours_only_routes.most_common(8))
    # Decide the reading from the data rather than asserting one: a stale tail means a publication
    # expiry difference, whereas an age profile matching the shared baseline exhausts that explanation
    # and leaves buses prod is deliberately suppressing.
    oo = sorted(x for x in ours_only_ages if x is not None)
    sh = sorted(x for x in both_ages_ours if x is not None)
    if oo and sh:
        oo_p90, sh_p90 = q(oo, 0.90), q(sh, 0.90)
        if oo_p90 > 2 * sh_p90:
            print("   READ: their p90 (%.0f s) is far above the shared baseline (%.0f s) => a publication"
                  % (oo_p90, sh_p90))
            print("         expiry difference: we keep a bus in the feed longer after its last report.")
        else:
            print("   READ: their age profile matches the shared baseline (p90 %.0f s vs %.0f s), so"
                  % (oo_p90, sh_p90))
            print("         staleness is EXHAUSTED as an explanation. These are normally-reporting buses")
            print("         prod suppresses \u2014 assignment/depot filtering we lack, not stale records.")

    print("\n--- PROD-ONLY: why do we not have them? ---")
    print("   their age in PROD's feed: %s" % describe(prod_only_ages))
    print("   by agency: %s" % dict(prod_only_agency))
    print("   top routes: %s" % prod_only_routes.most_common(8))
    n_routes = len(prod_only_routes)
    top_share = (100.0 * sum(c for _, c in prod_only_routes.most_common(5))
                 / max(1, prod_only_total))
    print("   spread over %d routes; top 5 routes hold %.0f%% of them" % (n_routes, top_share))
    print("   READ: fresh in prod + concentrated on few routes => schedule-data gap for those routes.")
    print("         fresh in prod + spread thin over many routes => per-vehicle inference misses")
    print("         (no usable sign code / run, just pulled out, or geometry that will not snap).")


if __name__ == "__main__":
    main()
