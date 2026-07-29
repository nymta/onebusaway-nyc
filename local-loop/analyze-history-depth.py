#!/usr/bin/env python3
"""Does a WARM Mongo history bucket make our ETAs agree more with prod? (history-depth A/B)

Background: the prognosticator blends schedule/recent/historical 20/40/40 per link, and the
historical component comes from Mongo `AggregateLinkTimes` — one doc per
{routeId, headId, tailId, timeOfDay, scheduleType} holding up to `historicalComponentRecordCount`
(300) traversals plus a median. `timeOfDay` is the **UTC hour** (verified 2026-07-28 against the raw
LinkTravelTimes timestamps). Where a link's bucket is empty the historical 40% silently reverts to
schedule, so warm-up should show up as better agreement on links whose bucket is deep.

Method: over an archived window (so the clock hour matches the bucket hour we classified), for every
trip-matched vehicle, take stops that are ADJACENT IN BOTH feeds' stop lists and compute the
per-link difference
    (ours[i] - ours[i-1]) - (prod[i] - prod[i-1])   ==   our link traversal - prod's
which isolates one link instead of the accumulated offset that stop-level deltas carry. Each link is
then labelled by its Mongo bucket depth (deep / shallow / other) and only counted if its predicted
crossing time falls in the UTC hour whose buckets were loaded.

Two controls matter, both applied here:
  * DEDUPE. Snapshots are 10 s apart, so the same (vehicle, trip, link) is re-measured dozens of
    times; raw counts overstate the sample by ~2 orders of magnitude. Only the FIRST observation of
    each (vehicle, trip, head, tail) is kept.
  * LINK DURATION. Deep buckets sit on high-frequency routes whose links are short (some medians are
    3-5 s), and a short link cannot disagree by much in absolute terms. Results are therefore also
    stratified by prod's link traversal time, and reported as a relative error.

Usage:
    python3 analyze-history-depth.py <date> <win0> <n_windows> <utc_hour> <deep.csv> <shallow.csv>
    e.g. python3 analyze-history-depth.py 2026-07-28 07-00 3 11 deep-h11.csv shallow-h11.csv
"""
import base64, csv, gzip, io, json, os, statistics, subprocess, sys
from collections import defaultdict
from google.transit import gtfs_realtime_pb2

CACHE = os.environ.get("ARCHIVE_CACHE", "/tmp/oba-archive-cache")
EXPRESS = ("SIM", "BXM", "QM", "BM", "X")
DUR_BINS = [(0, 20), (20, 45), (45, 90), (90, 180), (180, 1e9)]
CLASSES = ("deep (n>=50)", "shallow (n<=5)", "other/unknown depth", "ALL")


def s3_get(prefix, date, win):
    key = "%s/%s/%s.b64.gz" % (prefix, date, win)
    local = os.path.join(CACHE, key.replace("/", "__"))
    if not os.path.exists(local):
        os.makedirs(CACHE, exist_ok=True)
        r = subprocess.run(["aws", "s3", "cp", "s3://mtalirr/data-archiver/" + key, local, "--quiet"],
                           capture_output=True)
        if r.returncode:
            return None
    return local


def snapshots(path):
    out = {}
    raw = gzip.decompress(open(path, "rb").read())
    for line in io.BytesIO(raw):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            m = gtfs_realtime_pb2.FeedMessage()
            m.ParseFromString(base64.b64decode(rec["b"]))
        except Exception:
            continue
        if m.header.timestamp and m.header.timestamp not in out:
            out[m.header.timestamp] = m
    return out


def norm(i):
    return i.split("_", 1)[1] if "_" in i and not i.split("_")[0].isdigit() else i


def parse_tu(msg):
    """vehicle -> [ {trip, route, dir, stops=[(stop, time), ...] ordered} ]"""
    out = defaultdict(list)
    for e in msg.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        vid = norm(tu.vehicle.id) if tu.vehicle.id else None
        stops = []
        for stu in tu.stop_time_update:
            t = stu.arrival.time if stu.HasField("arrival") and stu.arrival.time else (
                stu.departure.time if stu.HasField("departure") and stu.departure.time else 0)
            if stu.stop_id and t:
                stops.append((norm(stu.stop_id), t))
        stops.sort(key=lambda s: s[1])
        if vid and stops:
            out[vid].append(dict(trip=norm(tu.trip.trip_id), route=norm(tu.trip.route_id),
                                 dir=tu.trip.direction_id if tu.trip.HasField("direction_id") else None,
                                 stops=stops))
    for recs in out.values():
        recs.sort(key=lambda r: r["stops"][0][1])
    return dict(out)


def load_depths(path):
    d = {}
    with open(path) as fh:
        for row in csv.reader(fh):
            if len(row) == 4:
                d[(row[0], row[1], row[2])] = int(row[3])
    return d


def stats(v):
    if not v:
        return None
    a = sorted(v)
    n = len(a)
    return dict(n=n, med=statistics.median(a), mean=statistics.fmean(a),
                mae=statistics.fmean(abs(x) for x in a),
                p90abs=sorted(abs(x) for x in a)[int(.9 * n)],
                w15=100.0 * sum(1 for x in a if abs(x) <= 15) / n,
                w30=100.0 * sum(1 for x in a if abs(x) <= 30) / n)


def row(lbl, s):
    return ("| %-26s | %7d | %+6.0f | %+6.0f | %6.1f | %6.0f | %5.0f%% | %5.0f%% |"
            % (lbl, s["n"], s["med"], s["mean"], s["mae"], s["p90abs"], s["w15"], s["w30"])
            if s else "| %-26s | (none) |" % lbl)


def main():
    date, win0, nwin, utc_hour = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    deep = load_depths(sys.argv[5])
    shallow = load_depths(sys.argv[6])
    print("deep links loaded: %d ; shallow: %d ; target UTC hour: %d" % (len(deep), len(shallow), utc_hour))

    hh, mm = (int(x) for x in win0.split("-"))
    wins = ["%02d-%02d" % (((hh * 60 + mm + 5 * i) // 60) % 24, (hh * 60 + mm + 5 * i) % 60)
            for i in range(nwin)]

    link_delta = defaultdict(list)     # class -> per-link differences (s)
    stop_delta = defaultdict(list)     # class -> stop-level absolute deltas (s)
    per_window = defaultdict(list)     # (route, dir) -> per-link diffs, for the deep windows
    depth_vs_delta = []                # (n, abs per-link diff)
    dur_cell = defaultdict(list)       # (class, duration bin) -> diffs
    rel_err = defaultdict(list)        # class -> |diff| / prod link time
    seen = set()                       # (vehicle, trip, head, tail) dedupe
    n_pairs = 0

    for w in wins:
        op, pp = s3_get("obaEc2TripUpdates", date, w), s3_get("busGtfsRt", date, w)
        if not op or not pp:
            print("  missing archive for %s" % w)
            continue
        osn, psn = snapshots(op), snapshots(pp)
        pts_all = sorted(psn)
        print("  %s: ours %d snaps, prod %d unique" % (w, len(osn), len(pts_all)))
        for ots in sorted(osn):
            pts = min(pts_all, key=lambda t: abs(t - ots))
            if abs(pts - ots) > 10:
                continue
            ours, prod = parse_tu(osn[ots]), parse_tu(psn[pts])
            now = max(ots, pts)
            for vid in set(ours) & set(prod):
                o = ours[vid][0]
                p = next((r for r in prod[vid] if r["trip"] == o["trip"]), None)
                if p is None:
                    continue          # trip mismatch or rollover: excluded by design
                n_pairs += 1
                pidx = {s: i for i, (s, _) in enumerate(p["stops"])}
                ptime = dict(p["stops"])
                for i in range(1, len(o["stops"])):
                    h_stop, h_t = o["stops"][i - 1]
                    t_stop, t_t = o["stops"][i]
                    if h_stop not in pidx or t_stop not in pidx:
                        continue
                    if pidx[t_stop] - pidx[h_stop] != 1:
                        continue      # not the same single link in both feeds
                    if t_t < now or ptime[t_stop] < now:
                        continue
                    # only links whose crossing falls in the classified UTC hour
                    import time as _t
                    if _t.gmtime(ptime[t_stop]).tm_hour != utc_hour:
                        continue
                    key = (o["route"], h_stop, t_stop)
                    dedupe = (vid, o["trip"], h_stop, t_stop)
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    ours_link = t_t - h_t
                    prod_link = ptime[t_stop] - ptime[h_stop]
                    diff = ours_link - prod_link
                    if key in deep:
                        cls = "deep (n>=50)"
                        depth_vs_delta.append((deep[key], abs(diff)))
                        per_window[(o["route"], o["dir"])].append(diff)
                    elif key in shallow:
                        cls = "shallow (n<=5)"
                        depth_vs_delta.append((shallow[key], abs(diff)))
                    else:
                        cls = "other/unknown depth"
                    link_delta[cls].append(diff)
                    link_delta["ALL"].append(diff)
                    stop_delta[cls].append(t_t - ptime[t_stop])
                    stop_delta["ALL"].append(t_t - ptime[t_stop])
                    db_ = next((i for i, (lo, hi) in enumerate(DUR_BINS) if lo <= prod_link < hi), None)
                    if db_ is not None:
                        dur_cell[(cls, db_)].append(diff)
                    if prod_link >= 20:
                        rel_err[cls].append(100.0 * abs(diff) / prod_link)

    print("\ntrip-matched vehicle-snapshots used: %d" % n_pairs)
    hdr = ("| %-26s | %7s | %6s | %6s | %6s | %6s | %5s | %5s |"
           % ("class", "n", "median", "mean", "MAE", "p90|.|", "<=15s", "<=30s"))
    print("\n=== A. PER-LINK difference (our link traversal - prod's), seconds ===")
    print(hdr)
    print("|%s|%s|%s|%s|%s|%s|%s|%s|" % ("-" * 28, "-" * 9, "-" * 8, "-" * 8, "-" * 8, "-" * 8, "-" * 7, "-" * 7))
    for c in ("deep (n>=50)", "shallow (n<=5)", "other/unknown depth", "ALL"):
        s = stats(link_delta.get(c, []))
        if s:
            print(row(c, s))

    print("\n=== B. STOP-LEVEL delta at the same stops (ours - prod), seconds ===")
    print(hdr)
    print("|%s|%s|%s|%s|%s|%s|%s|%s|" % ("-" * 28, "-" * 9, "-" * 8, "-" * 8, "-" * 8, "-" * 8, "-" * 7, "-" * 7))
    for c in ("deep (n>=50)", "shallow (n<=5)", "other/unknown depth", "ALL"):
        s = stats(stop_delta.get(c, []))
        if s:
            print(row(c, s))

    print("\n=== C. THE CONTROL: MAE of per-link difference by prod link duration ===")
    print("(deep vs shallow within the same duration band — removes the 'deep links are short' confound)")
    print("| %-16s | %18s | %18s | %18s |" % ("prod link time", "deep (n>=50)", "shallow (n<=5)", "other"))
    print("|%s|%s|%s|%s|" % ("-" * 18, "-" * 20, "-" * 20, "-" * 20))
    for i, (lo, hi) in enumerate(DUR_BINS):
        cells = []
        for c in ("deep (n>=50)", "shallow (n<=5)", "other/unknown depth"):
            v = dur_cell.get((c, i), [])
            cells.append("%.1f s (n=%d)" % (statistics.fmean(abs(x) for x in v), len(v))
                         if len(v) >= 25 else "-")
        print("| %-16s | %18s | %18s | %18s |"
              % ("%d-%s s" % (lo, "inf" if hi > 1e8 else int(hi)), cells[0], cells[1], cells[2]))
    print("\nrelative error |diff|/prod_link_time, links >=20 s only:")
    for c in CLASSES:
        v = rel_err.get(c, [])
        if len(v) >= 25:
            print("  %-22s median %5.1f%%  mean %5.1f%%  (n=%d)"
                  % (c, statistics.median(v), statistics.fmean(v), len(v)))

    if depth_vs_delta:
        print("\n=== D. |per-link difference| vs bucket depth ===")
        for lo, hi in ((1, 6), (6, 20), (20, 50), (50, 80), (80, 200)):
            sub = [d for n, d in depth_vs_delta if lo <= n < hi]
            if len(sub) >= 25:
                print("  depth %3d-%-3d : n=%5d  median |diff| %5.1f s  mean %5.1f s"
                      % (lo, hi - 1, len(sub), statistics.median(sub), statistics.fmean(sub)))

    print("\n=== E. route-direction windows that have deep history (per-link diff, n>=25 obs) ===")
    rows = [(statistics.fmean(abs(x) for x in v), rt, d, len(v), statistics.median(v))
            for (rt, d), v in per_window.items() if len(v) >= 25]
    rows.sort()
    print("  best agreement:")
    for mae, rt, d, n, med in rows[:12]:
        print("    %-7s dir=%s  n=%4d  median %+5.1f s  MAE %5.1f s" % (rt, d, n, med, mae))
    print("  worst agreement:")
    for mae, rt, d, n, med in rows[-8:]:
        print("    %-7s dir=%s  n=%4d  median %+5.1f s  MAE %5.1f s" % (rt, d, n, med, mae))
    allmae = [r[0] for r in rows]
    if allmae:
        print("  across %d deep route-directions: median MAE %.1f s, worst %.1f s"
              % (len(rows), statistics.median(allmae), max(allmae)))


if __name__ == "__main__":
    main()
