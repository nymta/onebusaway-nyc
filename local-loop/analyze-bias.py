#!/usr/bin/env python3
"""Decompose the ETA-delta bias (ours - prod) before attributing it to prediction quality.

Run this whenever a run shows a one-sided ETA bias. Established 2026-07-27: most of the apparent
"we predict earlier than prod" signal is NOT an engine difference. Two contaminants dominate:

  1. ROLLOVER ARTIFACT: prod publishes current+next TripUpdates. If our engine has already
     rolled the vehicle onto the NEXT trip while prod still has it on the current one, the
     trip_id join matches our in-progress trip against prod's not-yet-started trip. All
     ETAs then differ by a near-constant offset that measures rollover timing, not
     prediction quality. Detect: matched prod record index > 0.
  2. POSITION LAG: prod's VehiclePosition is ~30 s stale vs our ~10 s. On express/highway
     runs that is hundreds of metres of progress, which shifts every downstream ETA.
     Detect: correlate (prod VP age - our VP age) and inter-feed position distance with
     the first-stop delta, split local vs express.

Also recomputes headline delta stats with rollover pairs EXCLUDED, to see how much of the
reported long-horizon early bias is real.

Interpreting the position-distance table (§4): if the bias is ~zero in the 0-100 m stratum, the
feeds agree wherever they agree on where the bus IS, and the wider strata are measuring prod's
staler anchor rather than our optimism. A bias that survives at 0-100 m and grows with horizon is
a genuine link-time difference — as of 2026-07-27 that residual is express-only.

Usage: python3 analyze-bias.py [rounds] [interval]      # default 3 rounds, 45 s
"""
import math, statistics, sys, time, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from google.transit import gtfs_realtime_pb2

OURS = "http://ec2-52-70-255-34.compute-1.amazonaws.com"
MTA = "http://gtfsrt.prod.obanyc.com"
EXPRESS = ("SIM", "BXM", "QM", "BM", "X")
DIST_BINS = [(0, 50), (50, 150), (150, 400), (400, 1e9)]
HORIZONS = ["0-5", "5-15", "15-30", "30+"]


def fetch(u):
    m = gtfs_realtime_pb2.FeedMessage()
    with urllib.request.urlopen(u, timeout=30) as r:
        m.ParseFromString(r.read())
    return m


def norm(i):
    return i.split("_", 1)[1] if "_" in i and not i.split("_")[0].isdigit() else i


def parse_vp(msg):
    out = {}
    for e in msg.entity:
        if not e.HasField("vehicle"):
            continue
        v = e.vehicle
        vid = norm(v.vehicle.id) if v.vehicle.id else None
        if vid:
            out[vid] = dict(lat=v.position.latitude, lon=v.position.longitude, ts=v.timestamp,
                            speed=v.position.speed if v.position.HasField("speed") else None)
    return out


def parse_tu(msg):
    out = defaultdict(list)
    for e in msg.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        vid = norm(tu.vehicle.id) if tu.vehicle.id else None
        stops = {}
        for stu in tu.stop_time_update:
            t = stu.arrival.time if stu.HasField("arrival") and stu.arrival.time else (
                stu.departure.time if stu.HasField("departure") and stu.departure.time else 0)
            if stu.stop_id and t:
                stops[norm(stu.stop_id)] = t
        if vid:
            out[vid].append(dict(trip=norm(tu.trip.trip_id), route=norm(tu.trip.route_id),
                                 stops=stops))
    for recs in out.values():
        recs.sort(key=lambda r: min(r["stops"].values()) if r["stops"] else float("inf"))
    return dict(out)


def hav(a, b, c, d):
    R = 6371000
    p1, p2, dp, dl = math.radians(a), math.radians(c), math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def is_exp(r):
    return r.upper().startswith(EXPRESS)


def stats(d):
    if not d:
        return None
    a = sorted(d)
    n = len(a)
    return dict(n=n, med=statistics.median(a), mean=statistics.fmean(a),
                mae=statistics.fmean(abs(x) for x in a), p10=a[int(.1 * n)], p90=a[min(n - 1, int(.9 * n))])


def line(lbl, s):
    return ("| %-28s | %7d | %+6.0f | %+6.0f | %5.0f | %+6.0f / %+6.0f |" %
            (lbl, s["n"], s["med"], s["mean"], s["mae"], s["p10"], s["p90"])) if s else "| %-28s | – |" % lbl


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 45

    pooled = defaultdict(list)       # key -> deltas
    roll_offsets = []                # (route, offset_at_first_stop, express?)
    roll_vehicles = set()
    matched_vehicles = set()
    pos_rows = []                    # (route, express, dt_age, dist, first_delta)
    cross = defaultdict(list)        # (class, dist_bin, horizon) -> deltas
    route_30 = defaultdict(list)     # route -> 30+ min deltas
    n_matched_prod_current = 0
    n_matched_prod_next = 0
    route_roll = Counter()

    for r in range(rounds):
        if r:
            time.sleep(interval)
        with ThreadPoolExecutor(4) as ex:
            otu, ovp, mtu, mvp = list(ex.map(fetch, [OURS + "/tripUpdates", OURS + "/vehiclePositions",
                                                     MTA + "/tripUpdates", MTA + "/vehiclePositions"]))
        now = time.time()
        ou, ov, mu, mv = parse_tu(otu), parse_vp(ovp), parse_tu(mtu), parse_vp(mvp)

        for vid in set(ou) & set(mu):
            o = ou[vid][0]
            idx = next((i for i, rec in enumerate(mu[vid]) if rec["trip"] == o["trip"]), None)
            if idx is None:
                continue                      # trip mismatch: handled by analyze-discrepancies.py
            m = mu[vid][idx]
            matched_vehicles.add(vid)
            rollover = idx > 0
            if rollover:
                n_matched_prod_next += 1
                roll_vehicles.add(vid)
                route_roll[o["route"]] += 1
            else:
                n_matched_prod_current += 1

            shared = [(s, t, m["stops"][s]) for s, t in o["stops"].items()
                      if s in m["stops"] and t > now - 30 and m["stops"][s] > now - 30]
            if not shared:
                continue
            shared.sort(key=lambda x: x[2])
            first_delta = shared[0][1] - shared[0][2]
            if rollover:
                roll_offsets.append((o["route"], first_delta, is_exp(o["route"])))
            if vid in ov and vid in mv and not rollover:
                pos_rows.append((o["route"], is_exp(o["route"]),
                                 (now - mv[vid]["ts"]) - (now - ov[vid]["ts"]),
                                 hav(ov[vid]["lat"], ov[vid]["lon"], mv[vid]["lat"], mv[vid]["lon"]),
                                 first_delta))
            dist = (hav(ov[vid]["lat"], ov[vid]["lon"], mv[vid]["lat"], mv[vid]["lon"])
                    if (vid in ov and vid in mv) else None)
            for s, ot, mt in shared:
                h = (mt - now) / 60.0
                b = "0-5" if h < 5 else "5-15" if h < 15 else "15-30" if h < 30 else "30+"
                d = ot - mt
                tag = "ROLL" if rollover else "CUR"
                pooled["ALL|" + tag].append(d)
                pooled["%s|%s" % (b, tag)].append(d)
                if not rollover:
                    cls = "EXP" if is_exp(o["route"]) else "LOC"
                    pooled["%s|%s" % (b, cls)].append(d)
                    pooled["ALL|" + cls].append(d)
                    if dist is not None:
                        db = next(i for i, (lo, hi) in enumerate(DIST_BINS) if lo <= dist < hi)
                        cross[(cls, db, b)].append(d)
                    if b == "30+":
                        route_30[o["route"]].append(d)
        print("round %d/%d" % (r + 1, rounds))

    print()
    print("=== 1. Rollover artifact (our current trip = prod's NEXT trip) ===")
    tot = n_matched_prod_current + n_matched_prod_next
    print("trip-matched pairs: %d ; matched against prod's CURRENT trip: %d (%.1f%%) ; "
          "against prod's NEXT/later trip: %d (%.1f%%)"
          % (tot, n_matched_prod_current, 100.0 * n_matched_prod_current / tot,
             n_matched_prod_next, 100.0 * n_matched_prod_next / tot))
    print("unique vehicles affected: %d of %d matched" % (len(roll_vehicles), len(matched_vehicles)))
    if roll_offsets:
        offs = [o for _, o, _ in roll_offsets]
        print("first-stop offset on rollover pairs: median %+.0f s, mean %+.0f s, p10 %+.0f, p90 %+.0f, |>180s| = %d/%d"
              % (statistics.median(offs), statistics.fmean(offs), sorted(offs)[int(.1 * len(offs))],
                 sorted(offs)[int(.9 * len(offs))], sum(1 for o in offs if abs(o) > 180), len(offs)))
        print("top routes: %s" % route_roll.most_common(10))
    print()
    print("=== 2. Delta stats with vs without rollover pairs ===")
    print("| %-28s | %7s | %6s | %6s | %5s | %15s |" % ("bucket", "n", "median", "mean", "MAE", "p10 / p90"))
    print("|%s|%s|%s|%s|%s|%s|" % ("-" * 30, "-" * 9, "-" * 8, "-" * 8, "-" * 7, "-" * 17))
    for b in ("ALL", "0-5", "5-15", "15-30", "30+"):
        for tag, lbl in (("CUR", "prod-current only"), ("ROLL", "ROLLOVER pairs")):
            s = stats(pooled.get("%s|%s" % (b, tag), []))
            if s:
                print(line("%s %s" % (b, lbl), s))
    print()
    print("=== 3. Local vs express (rollover pairs excluded) ===")
    for b in ("ALL", "0-5", "5-15", "15-30", "30+"):
        for tag in ("LOC", "EXP"):
            s = stats(pooled.get("%s|%s" % (b, tag), []))
            if s:
                print(line("%s %s" % (b, tag), s))
    print()
    print("=== 4. Position staleness vs first-stop delta (non-rollover) ===")
    for lbl, sel in (("local", lambda r: not r[1]), ("express", lambda r: r[1])):
        rows = [r for r in pos_rows if sel(r)]
        if not rows:
            continue
        ages = [r[2] for r in rows]
        dists = [r[3] for r in rows]
        print("%s: n=%d ; prod VP age − our VP age: median %+.0f s ; inter-feed position distance: "
              "median %.0f m, p90 %.0f m" % (lbl, len(rows), statistics.median(ages),
                                             statistics.median(dists), sorted(dists)[int(.9 * len(dists))]))
        # bucket first-stop delta by position distance
        for lo, hi in ((0, 100), (100, 300), (300, 1000), (1000, 1e9)):
            sub = [r[4] for r in rows if lo <= r[3] < hi]
            if len(sub) > 20:
                print("   dist %5s-%-6s m: n=%5d  median delta %+6.0f s  MAE %5.0f s"
                      % (lo, hi if hi < 1e9 else "inf", len(sub), statistics.median(sub),
                         statistics.fmean(abs(x) for x in sub)))
    print()
    print("=== 5. THE CONTROL: median delta by class x position distance x horizon ===")
    print("bias ~0 in the 0-50 m row  => prod's staler anchor, not our optimism")
    print("bias surviving at 0-50 m and growing with horizon => real link-time difference")
    for cls, lbl in (("LOC", "local"), ("EXP", "express")):
        print("\n--- %s ---" % lbl)
        print("%-14s%s" % ("dist bin", "".join("%14s" % h for h in HORIZONS)))
        for i, (lo, hi) in enumerate(DIST_BINS):
            row = ""
            for h in HORIZONS:
                v = cross[(cls, i, h)]
                row += "%14s" % ("%+.0f (n=%d)" % (statistics.median(v), len(v)) if len(v) > 30 else "-")
            print("%-14s%s" % ("%d-%s m" % (lo, "inf" if hi > 1e8 else int(hi)), row))
    print()
    print("=== 6. 30+ min bias by route (n>=300) — one-sided would suggest policy, two-sided links ===")
    rows = sorted(((statistics.median(v), rt, len(v)) for rt, v in route_30.items() if len(v) >= 300))
    for m_, rt, n in rows[:8]:
        print("  %-8s median %+6.0f s  (n=%d)" % (rt, m_, n))
    print("   ...")
    for m_, rt, n in rows[-5:]:
        print("  %-8s median %+6.0f s  (n=%d)" % (rt, m_, n))


if __name__ == "__main__":
    main()
