#!/usr/bin/env python3
"""Verify our GTFS-RT feed's ids join to the public GTFS (the C6 pick).

The comparison against MTA's official arrivals hinges on being able to join our feed to the
public GTFS. This checks that the bare ids we emit (trip_id / route_id / stop_id) are present
in the same GTFS zips the bundle was built from. Vehicle ids aren't in static GTFS, so we only
report their count/shape (they derive from the shared BusTech AVL, assumed to match MTA's).

Usage:
    python3 verify-parity.py
Env overrides:
    OBA_GTFSRT_BASE  (default http://ec2-52-70-255-34.compute-1.amazonaws.com)
    OBA_GTFS_DIR     (default "/Users/ranajays/Downloads/GTFS + STIF Files C6")
Needs: gtfs-realtime-bindings (already on this Mac).
"""
import csv, glob, io, os, sys, urllib.request, zipfile
from google.transit import gtfs_realtime_pb2

BASE = os.environ.get("OBA_GTFSRT_BASE", "http://ec2-52-70-255-34.compute-1.amazonaws.com")
GTFS_DIR = os.environ.get("OBA_GTFS_DIR", "/Users/ranajays/Downloads/GTFS + STIF Files C6")
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parity-report.md")


def fetch(path):
    with urllib.request.urlopen(BASE + path, timeout=25) as r:
        return r.read()


def collect_feed_ids():
    trips, routes, stops, vehicles = set(), set(), set(), set()
    trip_route = {}   # feed trip_id -> route_id (to cross-check vs GTFS)
    n_entities = 0
    for path in ("/tripUpdates", "/vehiclePositions"):
        msg = gtfs_realtime_pb2.FeedMessage()
        msg.ParseFromString(fetch(path))
        for e in msg.entity:
            n_entities += 1
            if e.HasField("trip_update"):
                t = e.trip_update.trip
                if t.trip_id: trips.add(t.trip_id); trip_route.setdefault(t.trip_id, t.route_id or None)
                if t.route_id: routes.add(t.route_id)
                if e.trip_update.vehicle.id: vehicles.add(e.trip_update.vehicle.id)
                for stu in e.trip_update.stop_time_update:
                    if stu.stop_id: stops.add(stu.stop_id)
            if e.HasField("vehicle"):
                t = e.vehicle.trip
                if t.trip_id: trips.add(t.trip_id); trip_route.setdefault(t.trip_id, t.route_id or None)
                if t.route_id: routes.add(t.route_id)
                if e.vehicle.vehicle.id: vehicles.add(e.vehicle.vehicle.id)
                if e.vehicle.stop_id: stops.add(e.vehicle.stop_id)
    return trips, routes, stops, vehicles, trip_route, n_entities


def load_gtfs():
    trip_route, routes, stops = {}, set(), set()
    trip_dir = {}
    zips = sorted(glob.glob(os.path.join(GTFS_DIR, "GTFS_*.zip")))
    if not zips:
        sys.exit("no GTFS_*.zip found in %r" % GTFS_DIR)
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            names = set(zf.namelist())
            with zf.open("trips.txt") as f:
                for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
                    trip_route[row["trip_id"]] = row.get("route_id")
                    trip_dir[row["trip_id"]] = row.get("direction_id")
            if "routes.txt" in names:
                with zf.open("routes.txt") as f:
                    for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
                        routes.add(row["route_id"])
            if "stops.txt" in names:
                with zf.open("stops.txt") as f:
                    for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
                        stops.add(row["stop_id"])
    return trip_route, routes, stops, trip_dir, [os.path.basename(z) for z in zips]


def cov(feed_set, gtfs_set):
    present = feed_set & gtfs_set
    missing = sorted(feed_set - gtfs_set)
    p = 100.0 * len(present) / len(feed_set) if feed_set else 0.0
    return len(feed_set), len(present), len(missing), p, missing


def main():
    print("fetching feed from %s ..." % BASE)
    f_trips, f_routes, f_stops, f_vehicles, f_trip_route, n_ent = collect_feed_ids()
    print("loading GTFS from %s ..." % GTFS_DIR)
    g_trip_route, g_routes, g_stops, g_trip_dir, zips = load_gtfs()
    g_trips = set(g_trip_route)

    lines = []
    def out(s=""):
        print(s); lines.append(s)

    out("# GTFS-RT ↔ public-GTFS trip_id parity report")
    out()
    out("- feed base: `%s`" % BASE)
    out("- GTFS zips (%d): %s" % (len(zips), ", ".join(zips)))
    out("- feed entities sampled: %d" % n_ent)
    out()
    out("| id type | in feed | matched in GTFS | missing | coverage |")
    out("|---|---|---|---|---|")
    results = {}
    for label, fset, gset in (("trip_id", f_trips, g_trips),
                              ("route_id", f_routes, g_routes),
                              ("stop_id", f_stops, g_stops)):
        tot, pres, miss, p, missing = cov(fset, gset)
        results[label] = (tot, pres, miss, p, missing)
        out("| %s | %d | %d | %d | **%.1f%%** |" % (label, tot, pres, miss, p))
    out("| vehicle_id | %d | n/a (not in static GTFS) | | |" % len(f_vehicles))
    out()

    # trip -> route consistency (for trips present in GTFS)
    mismatch_route = []
    for tid, frid in f_trip_route.items():
        grid = g_trip_route.get(tid)
        if grid is not None and frid is not None and grid != frid:
            mismatch_route.append((tid, frid, grid))
    out("**trip_id → route_id consistency:** %d of %d feed trips are in GTFS with a matching route_id; %d mismatches."
        % (len([t for t in f_trip_route if t in g_trip_route]), len(f_trip_route), len(mismatch_route)))
    out()

    for label in ("trip_id", "route_id", "stop_id"):
        tot, pres, miss, p, missing = results[label]
        if missing:
            out("**%d %s(s) missing from GTFS** (up to 10 shown):" % (miss, label))
            for m in missing[:10]:
                out("  - `%s`" % m)
            out()
    if mismatch_route:
        out("**route mismatches (up to 10):**")
        for tid, frid, grid in mismatch_route[:10]:
            out("  - `%s`: feed route=%s, gtfs route=%s" % (tid, frid, grid))
        out()

    verdict = "PASS" if (results["trip_id"][3] >= 99.0 and results["route_id"][3] >= 99.0
                         and results["stop_id"][3] >= 99.0 and not mismatch_route) else "REVIEW"
    out("## VERDICT: %s" % verdict)
    out("- Feed sample: vehicle_id form e.g. `%s`" % (sorted(f_vehicles)[0] if f_vehicles else "n/a"))
    out("- trip_id form e.g. `%s`" % (sorted(f_trips)[0] if f_trips else "n/a"))

    with open(REPORT, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\nreport written -> %s" % REPORT)
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
