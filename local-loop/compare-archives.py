#!/usr/bin/env python3
"""Fixed-clock-window comparison from the S3 archives (removes the time-of-day confounder).

Live `compare-feeds.py` runs are sampled whenever an agent happens to run them, so day-over-day
deltas mix warm-up with time of day. This reads the archived protobufs for the SAME wall-clock
window on different dates and reports the same trip-matching / ETA-delta metrics, so trends are
comparable across days.

Archive layout (gzip, one JSON line per poll: {"ts": <ms>, "b": "<base64 protobuf>"}):
  ours: s3://mtalirr/data-archiver/obaEc2TripUpdates/YYYY-MM-DD/HH-MM.b64.gz   (10 s cadence)
  prod: s3://mtalirr/data-archiver/busGtfsRt/YYYY-MM-DD/HH-MM.b64.gz           (5 s, ~78% dupes)
Files are 5-minute buckets named by their start minute (00-00, 00-05, ...).

Usage:
    python3 compare-archives.py 2026-07-24 17-45 [n_windows]     # one date
    python3 compare-archives.py 2026-07-24,2026-07-27 17-45 3    # compare dates, same window
    AGENCY=NYCT python3 compare-archives.py ...                  # restrict to one agency
Snapshots are paired across feeds by GTFS-RT header.timestamp (nearest within PAIR_TOL_S).

Set AGENCY=NYCT (or MTABC) whenever a comparison window straddles 2026-07-29: MTABC entered our
feed that day with zero prediction history, so a blended MAE mixes a real platform change with the
arrival of a cold-history fleet. Holding the agency fixed separates the two.
"""
import base64, gzip, io, json, os, statistics, subprocess, sys, time
from collections import Counter, defaultdict
from google.transit import gtfs_realtime_pb2

BUCKET = "s3://mtalirr/data-archiver"
OURS_PREFIX = "obaEc2TripUpdates"
PROD_PREFIX = "busGtfsRt"
CACHE = os.environ.get("ARCHIVE_CACHE", "/tmp/oba-archive-cache")
PAIR_TOL_S = 10          # max header.timestamp skew when pairing our snapshot with prod's
                         # (prod dedupes to a ~23 s effective cadence, so some skew is unavoidable;
                         #  the same tolerance on both dates keeps cross-day comparisons fair)
EXPRESS_PREFIXES = ("SIM", "BXM", "QM", "BM", "X")
AGENCY = os.environ.get("AGENCY")        # None = both; "NYCT" or "MTABC" to hold the fleet fixed


def s3_get(prefix, date, window):
    """Download (and cache) one 5-minute archive file; returns local path or None."""
    key = "%s/%s/%s.b64.gz" % (prefix, date, window)
    local = os.path.join(CACHE, key.replace("/", "__"))
    if not os.path.exists(local):
        os.makedirs(CACHE, exist_ok=True)
        r = subprocess.run(["aws", "s3", "cp", "%s/%s" % (BUCKET, key), local, "--quiet"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  !! missing %s (%s)" % (key, r.stderr.strip()[:120]))
            return None
    return local


def load_snapshots(path):
    """-> {header_ts: FeedMessage}, deduped by header.timestamp (prod repeats ~78% of polls)."""
    snaps = {}
    with open(path, "rb") as fh:
        raw = gzip.decompress(fh.read())          # handles multi-member gzip
    for line in io.BytesIO(raw):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            msg = gtfs_realtime_pb2.FeedMessage()
            msg.ParseFromString(base64.b64decode(rec["b"]))
        except Exception:
            continue
        ts = msg.header.timestamp
        if ts and ts not in snaps:
            snaps[ts] = msg
    return snaps


def norm(ident):
    return ident.split("_", 1)[1] if "_" in ident and not ident.split("_")[0].isdigit() else ident


def is_express(route_id):
    return route_id.upper().startswith(EXPRESS_PREFIXES)


def agency_of(vehicle_id):
    """Both feeds label vehicles `MTABC_xxxx` or `MTA NYCT_xxxx`; norm() drops that, so read it first."""
    return "MTABC" if vehicle_id.startswith("MTABC") else "NYCT"


def parse_trip_updates(msg):
    out = defaultdict(list)
    for e in msg.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        if not tu.vehicle.id or (AGENCY and agency_of(tu.vehicle.id) != AGENCY):
            continue
        veh = norm(tu.vehicle.id)
        stops = {}
        for stu in tu.stop_time_update:
            t = stu.arrival.time if stu.HasField("arrival") and stu.arrival.time else (
                stu.departure.time if stu.HasField("departure") and stu.departure.time else 0)
            if stu.stop_id and t:
                stops[norm(stu.stop_id)] = t
        rec = dict(trip=norm(tu.trip.trip_id), route=norm(tu.trip.route_id),
                   direction=tu.trip.direction_id if tu.trip.HasField("direction_id") else None,
                   stops=stops)
        out[veh].append(rec)
    for recs in out.values():
        recs.sort(key=lambda r: min(r["stops"].values()) if r["stops"] else float("inf"))
    return dict(out)


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
                w30=pct(sum(1 for d in a if abs(d) <= 30), n),
                w60=pct(sum(1 for d in a if abs(d) <= 60), n),
                w180=pct(sum(1 for d in a if abs(d) <= 180), n))


def compare_window(date, windows):
    """Run the comparison over one date across the given 5-min windows; returns a result dict."""
    pooled = defaultdict(list)
    pooled_class = defaultdict(list)
    match = Counter()
    veh = Counter()
    n_pairs_snap = 0
    skews = []

    for w in windows:
        ours_path, prod_path = s3_get(OURS_PREFIX, date, w), s3_get(PROD_PREFIX, date, w)
        if not ours_path or not prod_path:
            continue
        ours_snaps, prod_snaps = load_snapshots(ours_path), load_snapshots(prod_path)
        prod_ts = sorted(prod_snaps)
        if not ours_snaps or not prod_ts:
            continue
        print("  %s %s: ours %d snaps, prod %d unique snaps" % (date, w, len(ours_snaps), len(prod_ts)))

        # pair each of our snapshots with the nearest prod snapshot by header timestamp
        for ots in sorted(ours_snaps):
            pts = min(prod_ts, key=lambda t: abs(t - ots))
            if abs(pts - ots) > PAIR_TOL_S:
                continue
            n_pairs_snap += 1
            skews.append(abs(pts - ots))
            ours = parse_trip_updates(ours_snaps[ots])
            mta = parse_trip_updates(prod_snaps[pts])
            now = max(ots, pts)
            both = set(ours) & set(mta)
            veh.update(ours=len(ours), mta=len(mta), both=len(both), snaps=1)
            for v in both:
                o = ours[v][0]
                m = next((rec for rec in mta[v] if rec["trip"] == o["trip"]), mta[v][0])
                if o["trip"] == m["trip"]:
                    match["same_trip"] += 1
                elif o["route"] == m["route"]:
                    if (o["direction"] is not None and m["direction"] is not None
                            and o["direction"] != m["direction"]):
                        match["same_route_diff_direction"] += 1
                    else:
                        match["same_route_diff_trip"] += 1
                else:
                    match["diff_route"] += 1
                    continue
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
    return dict(date=date, pooled=pooled, pooled_class=pooled_class, match=match,
                veh=veh, snap_pairs=n_pairs_snap,
                skew=statistics.median(skews) if skews else float("nan"))


def expand_windows(start, n):
    """'17-45', 3 -> ['17-45','17-50','17-55'] (archive files are 5-minute buckets)."""
    hh, mm = (int(x) for x in start.split("-"))
    out = []
    for i in range(n):
        total = hh * 60 + mm + 5 * i
        out.append("%02d-%02d" % ((total // 60) % 24, total % 60))
    return out


def report(results):
    lines = []
    p = lambda s="": (print(s), lines.append(s))
    p("# Archive fixed-window comparison — ours vs prod GTFS-RT")
    p()
    p("- generated %s ; delta convention **ours − prod, seconds** (positive = we predict LATER)" %
      time.strftime("%Y-%m-%d %H:%M:%S %Z"))
    p("- fleet: **%s**" % (AGENCY if AGENCY else "both agencies (NYCT + MTABC)"))
    p("- snapshots paired by GTFS-RT `header.timestamp` within %d s; prod polls deduped by header ts" % PAIR_TOL_S)
    p()
    p("## Volume")
    p()
    p("| date | snapshot pairs | median pair skew | avg veh ours | avg veh prod | avg overlap | trip pairs | stop-pred deltas |")
    p("|---|---|---|---|---|---|---|---|")
    for r in results:
        s = max(1, r["veh"]["snaps"])
        p("| %s | %d | %.0f s | %.0f | %.0f | %.0f | %d | %d |" %
          (r["date"], r["snap_pairs"], r["skew"], r["veh"]["ours"] / s, r["veh"]["mta"] / s,
           r["veh"]["both"] / s, sum(r["match"].values()), len(r["pooled"]["ALL"])))
    p()
    p("## Trip matching")
    p()
    p("| date | same trip_id | same route/diff trip | direction flip | diff route |")
    p("|---|---|---|---|---|")
    for r in results:
        tot = sum(r["match"].values())
        p("| %s | %.1f%% | %.1f%% | %.1f%% | %.2f%% |" %
          (r["date"], pct(r["match"]["same_trip"], tot), pct(r["match"]["same_route_diff_trip"], tot),
           pct(r["match"]["same_route_diff_direction"], tot), pct(r["match"]["diff_route"], tot)))
    p()
    p("## ETA deltas by horizon (median / MAE, seconds)")
    p()
    p("| date | " + " | ".join(b for b in ("ALL", "0-5 min", "5-15 min", "15-30 min", "30+ min")) +
      " | local | express |")
    p("|---|---|---|---|---|---|---|---|")
    for r in results:
        cells = []
        for b in ("ALL", "0-5 min", "5-15 min", "15-30 min", "30+ min"):
            s = delta_stats(r["pooled"].get(b, []))
            cells.append("%+.0f / %.0f" % (s["median"], s["mae"]) if s else "–")
        for c in ("local", "express"):
            s = delta_stats(r["pooled_class"].get(c, []))
            cells.append("%+.0f / %.0f" % (s["median"], s["mae"]) if s else "–")
        p("| %s | %s |" % (r["date"], " | ".join(cells)))
    p()
    p("## Agreement rate — share of stop predictions within N of prod (the plain-English metric)")
    p()
    p("| date | within 30 s | within 1 min | within 3 min | within 5 min |")
    p("|---|---|---|---|---|")
    for r in results:
        s = delta_stats(r["pooled"].get("ALL", []))
        p("| %s | %.0f%% | **%.0f%%** | **%.0f%%** | %.0f%% |"
          % (r["date"], s["w30"], s["w60"], s["w180"],
             pct(sum(1 for d in r["pooled"]["ALL"] if abs(d) <= 300), s["n"]))
          if s else "| %s | – |" % r["date"])
    p()
    p("### Agreement rate by how far ahead the prediction looks")
    p()
    p("| date | horizon | avg difference | within 1 min | within 3 min |")
    p("|---|---|---|---|---|")
    for r in results:
        for b in ("0-5 min", "5-15 min", "15-30 min", "30+ min"):
            s = delta_stats(r["pooled"].get(b, []))
            if s:
                p("| %s | %s | %.0f s | **%.0f%%** | %.0f%% |"
                  % (r["date"], b, s["mae"], s["w60"], s["w180"]))
    p()
    return "\n".join(lines) + "\n"


def main():
    dates = sys.argv[1].split(",") if len(sys.argv) > 1 else [time.strftime("%Y-%m-%d")]
    start = sys.argv[2] if len(sys.argv) > 2 else "17-45"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    windows = expand_windows(start, n)
    print("windows: %s" % ", ".join(windows))
    results = [compare_window(d, windows) for d in dates]
    text = report(results)
    out = os.environ.get("REPORT_OUT")
    if out:
        with open(out, "w") as fh:
            fh.write(text)
        print("written -> %s" % out)


if __name__ == "__main__":
    main()
