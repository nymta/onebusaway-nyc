#!/usr/bin/env python3
"""Quick vehicle-coverage + freshness probe: our EC2 OBA vs MTA prod, from VehiclePositions.

Prints vehicle counts, overlap, MTA-only breakdown by agency (the MTABC gap),
position-age medians (the 5s-vs-30s cadence signal), and a PER-ROUTE coverage gate.
Complements compare-feeds.py (which analyzes TripUpdates); this answers "who is
tracking which vehicles right now".

The per-route gate exists because a route at 0% coverage is invisible inside the
aggregate NYCT percentage — that is how the B1 gap (route absent from our feed all day,
2026-07-27) stayed hidden for three runs. Any NYCT route below ~20% with a real prod
fleet is a data/bundle gap, not prediction noise.

Usage: python3 probe-coverage.py
Env overrides: OURS_BASE, MTA_BASE.
"""
import os, time, urllib.request
from collections import Counter
from google.transit import gtfs_realtime_pb2

OURS = os.environ.get("OURS_BASE", "http://ec2-52-70-255-34.compute-1.amazonaws.com") + "/vehiclePositions"
MTA = os.environ.get("MTA_BASE", "http://gtfsrt.prod.obanyc.com") + "/vehiclePositions"


MIN_PROD_FLEET = 3       # ignore routes prod barely runs right now
GAP_THRESHOLD = 0.20     # below this share of prod's fleet = data gap, not noise


def vp(url):
    m = gtfs_realtime_pb2.FeedMessage()
    m.ParseFromString(urllib.request.urlopen(url, timeout=30).read())
    now = time.time()
    d, ages = {}, []
    for e in m.entity:
        if e.HasField("vehicle") and e.vehicle.vehicle.id:
            d[e.vehicle.vehicle.id] = e.vehicle.trip.route_id.split("_")[-1]
            if e.vehicle.timestamp:
                ages.append(now - e.vehicle.timestamp)
    ages.sort()
    med = ages[len(ages) // 2] if ages else -1
    p90 = ages[int(len(ages) * 0.9)] if ages else -1
    return d, now - m.header.timestamp, med, p90


def main():
    ours, oh, om, op = vp(OURS)
    mta, mh, mm, mp = vp(MTA)
    both = set(ours) & set(mta)
    mta_only = Counter(v.rsplit("_", 1)[0] for v in mta if v not in ours)
    ours_only = [v for v in ours if v not in mta]
    def agency(v):
        return "MTABC" if v.startswith("MTABC") else "NYCT"
    per_agency = {}
    for a in ("NYCT", "MTABC"):
        mta_a = {v for v in mta if agency(v) == a}
        ours_a = {v for v in ours if agency(v) == a}
        per_agency[a] = (len(mta_a & ours_a), len(mta_a), len(ours_a))
    print(time.strftime("%Y-%m-%d %H:%M:%S %Z"))
    print("ours: %d vehicles (header age %.0fs, pos age median %.0fs / p90 %.0fs)" % (len(ours), oh, om, op))
    print("MTA:  %d vehicles (header age %.0fs, pos age median %.0fs / p90 %.0fs)" % (len(mta), mh, mm, mp))
    print("overlap: %d  |  ours-only: %d" % (len(both), len(ours_only)))
    print("MTA-only by agency: %s" % dict(mta_only))
    # coverage must be computed per agency: dividing the whole overlap by MTA's NYCT-only
    # fleet overstates it (and exceeded 100% once MTABC coverage landed).
    for a, (ov, n_mta, n_ours) in per_agency.items():
        if n_mta:
            print("%-6s coverage: %5.1f%% of MTA's %s fleet  (%d of %d; we track %d)"
                  % (a, 100.0 * ov / n_mta, a, ov, n_mta, n_ours))
    print("(expected: both agencies ≥97%%; our pos ages ~2-3x fresher than MTA's)")
    route_gate(ours, mta)


def route_gate(ours, mta):
    """Per-route ours/MTA vehicle ratio for NYCT routes — catches whole-route data gaps."""
    o_by_route, m_by_route = Counter(), Counter()
    for v, r in ours.items():
        if r:
            o_by_route[r] += 1
    for v, r in mta.items():
        if r and v.startswith("MTA NYCT"):
            m_by_route[r] += 1
    rows = sorted(((o_by_route[r] / n, r, o_by_route[r], n)
                   for r, n in m_by_route.items() if n >= MIN_PROD_FLEET))
    gaps = [x for x in rows if x[0] < GAP_THRESHOLD]
    print("\nper-route NYCT coverage (prod fleet >= %d): %d routes checked, worst 8:"
          % (MIN_PROD_FLEET, len(rows)))
    for ratio, r, o, n in rows[:8]:
        print("   %-8s ours %3d / prod %3d = %3.0f%%%s"
              % (r, o, n, 100 * ratio, "   <-- GAP" if ratio < GAP_THRESHOLD else ""))
    if gaps:
        print("!! %d NYCT route(s) below %.0f%% — investigate as a bundle/STIF data gap "
              "(see COMPARISON-RUNBOOK class 7, the B1 case): %s"
              % (len(gaps), 100 * GAP_THRESHOLD, ", ".join(x[1] for x in gaps)))


if __name__ == "__main__":
    main()
