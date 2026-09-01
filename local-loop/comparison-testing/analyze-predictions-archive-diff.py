#!/usr/bin/env python3
"""Deep-dive comparison of OBA EC2 vs BusTech prod queuePredictions hourly archives.

Companion to compare-predictions-archives.py. Same replay/merge/sample method, but does the
whole job in ONE pass per side and additionally reports:

  * agency slices (NYCT / MTABC) without re-replaying the archives
  * per-route coverage residues (routes only in one feed, and prod-fleet-vs-ours per route)
  * concrete examples: diff-trip pairs, direction flips, large same-trip ETA outliers
  * ours-only / prod-only vehicle populations with route breakdown
  * structural/format parity counters (entities per line, empty lines, key sets)

Usage:
    python3 analyze-predictions-archive-diff.py <ours.zip> <prod.zip> [label]

Env:
    SAMPLE_INTERVAL_S   default 60
    REPORT_OUT          markdown output path
    MAX_PAIRS           cap snapshot pairs (0 = all)
"""
from __future__ import annotations

import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from typing import Any, Iterator

SAMPLE_INTERVAL_S = int(os.environ.get("SAMPLE_INTERVAL_S", "60"))
MAX_PAIRS = int(os.environ.get("MAX_PAIRS", "0"))
EXPRESS_PREFIXES = ("SIM", "BXM", "QM", "BM", "X")
REPORT = os.environ.get("REPORT_OUT", "predictions-archive-diff.md")
random.seed(7)


def norm(ident: str) -> str:
    return ident.split("_", 1)[1] if "_" in ident and not ident.split("_")[0].isdigit() else ident


def is_express(route_id: str) -> bool:
    return route_id.upper().startswith(EXPRESS_PREFIXES)


def agency_of(vehicle_raw: str) -> str:
    return "MTABC" if vehicle_raw.startswith("MTABC") else "NYCT"


def pct(a: float, b: float) -> float:
    return 100.0 * a / b if b else 0.0


def delta_stats(deltas: list[float]) -> dict[str, float] | None:
    if not deltas:
        return None
    a = sorted(deltas)
    n = len(a)
    return dict(
        n=n,
        median=statistics.median(a),
        mean=statistics.fmean(a),
        mae=statistics.fmean(abs(d) for d in a),
        p10=a[int(0.10 * n)],
        p90=a[min(n - 1, int(0.90 * n))],
        w30=pct(sum(1 for d in a if abs(d) <= 30), n),
        w60=pct(sum(1 for d in a if abs(d) <= 60), n),
        w180=pct(sum(1 for d in a if abs(d) <= 180), n),
        w300=pct(sum(1 for d in a if abs(d) <= 300), n),
    )


def iter_feed_messages(zip_path: str) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(zip_path) as zf:
        inner = zf.namelist()[0]
        with zf.open(inner) as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                yield json.loads(raw)


def to_epoch_sec(value: Any) -> int:
    t = int(value)
    return t // 1000 if t > 1_000_000_000_000 else t


def header_ts_sec(msg: dict[str, Any]) -> int:
    return to_epoch_sec(msg["header"]["timestamp"])


class DifferentialState:
    def __init__(self) -> None:
        self._entities: dict[str, dict[str, Any]] = {}
        self._last_seen: dict[str, int] = {}

    def apply(self, msg: dict[str, Any]) -> None:
        ts = header_ts_sec(msg)
        for entity in msg.get("entity") or []:
            eid = entity.get("id")
            if eid:
                self._entities[eid] = entity
                self._last_seen[eid] = ts
        if len(self._entities) > 50_000:
            self._prune(ts - 900)

    def _prune(self, min_ts: int) -> None:
        stale = [eid for eid, seen in self._last_seen.items() if seen < min_ts]
        for eid in stale:
            self._entities.pop(eid, None)
            self._last_seen.pop(eid, None)

    def trip_updates_by_vehicle(self, since_ts: int = 0) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for eid, entity in self._entities.items():
            if since_ts and self._last_seen.get(eid, 0) < since_ts:
                continue
            tu = entity.get("tripUpdate")
            if not tu:
                continue
            veh_raw = (tu.get("vehicle") or {}).get("id") or ""
            if not veh_raw:
                continue
            trip = tu.get("trip") or {}
            stops: dict[str, int] = {}
            for stu in tu.get("stopTimeUpdate") or []:
                stop_id = stu.get("stopId")
                t = (stu.get("arrival") or {}).get("time") or (stu.get("departure") or {}).get("time")
                if stop_id and t:
                    stops[norm(stop_id)] = to_epoch_sec(t)
            out[norm(veh_raw)].append(
                dict(
                    agency=agency_of(veh_raw),
                    trip=norm(trip.get("tripId") or ""),
                    route=norm(trip.get("routeId") or ""),
                    direction=trip.get("directionId"),
                    stops=stops,
                )
            )
        for recs in out.values():
            recs.sort(key=lambda r: min(r["stops"].values()) if r["stops"] else float("inf"))
        return dict(out)


def grid_ts(ts_sec: int) -> int:
    return ts_sec - (ts_sec % SAMPLE_INTERVAL_S)


def origin_secs(trip_id: str) -> int | None:
    """STIF-style NYCT trip ids embed the block origin time in hundredths of a minute.

    e.g. `WF_C6-Weekday-SDon-051300_SBS6_161` -> 051300 -> 513 min -> 08:33.
    Returns seconds past midnight, or None for MTABC-style ids that carry no origin time.
    """
    for part in trip_id.split("_"):
        if "-" in part:
            tail = part.rsplit("-", 1)[-1]
            if tail.isdigit() and len(tail) == 6:
                return int(tail) * 60 // 100
    return None


def iter_messages_sorted(zip_path: str) -> Iterator[dict[str, Any]]:
    unsorted = tempfile.NamedTemporaryFile(mode="wb", suffix=".ndjson", delete=False)
    unsorted_path = unsorted.name
    sorted_path = f"{unsorted_path}.sorted"
    try:
        with unsorted:
            for msg in iter_feed_messages(zip_path):
                ts = header_ts_sec(msg)
                line = json.dumps(msg, separators=(",", ":")).encode("utf-8")
                unsorted.write(f"{ts:012d} ".encode("ascii") + line + b"\n")
        subprocess.run(["sort", "-n", unsorted_path, "-o", sorted_path], check=True)
        with open(sorted_path, "rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                _prefix, payload = raw.split(b" ", 1)
                yield json.loads(payload)
    finally:
        unsorted.close()
        for path in (unsorted_path, sorted_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def build_sampled_snapshots(zip_path: str, label: str) -> tuple[dict[int, dict], dict[str, Any]]:
    state = DifferentialState()
    snapshots: dict[int, dict[str, list[dict[str, Any]]]] = {}
    stats: Counter = Counter()
    keysets: Counter = Counter()
    idconv: Counter = Counter()
    current_g: int | None = None
    ts_min = ts_max = None
    print(f"  replaying {label}: {os.path.basename(zip_path)}", flush=True)
    for msg in iter_messages_sorted(zip_path):
        ts = header_ts_sec(msg)
        ts_min = ts if ts_min is None else min(ts_min, ts)
        ts_max = ts if ts_max is None else max(ts_max, ts)
        stats["lines"] += 1
        n_ent = len(msg.get("entity") or [])
        stats["entities"] += n_ent
        if n_ent == 0:
            stats["empty_lines"] += 1
        if stats["lines"] <= 5000:
            keysets["|".join(sorted(msg.keys()))] += 1
            keysets["hdr:" + "|".join(sorted(msg["header"].keys()))] += 1
            for e in (msg.get("entity") or [])[:1]:
                keysets["ent:" + "|".join(sorted(e.keys()))] += 1
                tu = e.get("tripUpdate")
                if tu:
                    keysets["tu:" + "|".join(sorted(tu.keys()))] += 1
                    keysets["trip:" + "|".join(sorted((tu.get("trip") or {}).keys()))] += 1
                    for stu in (tu.get("stopTimeUpdate") or [])[:1]:
                        keysets["stu:" + "|".join(sorted(stu.keys()))] += 1
            stats["incr_" + str(msg["header"].get("incrementality"))] += 1
        # id-convention census (cheap, first 20k lines): raw prefix before the last "_"
        if stats["lines"] <= 20000:
            for e in (msg.get("entity") or []):
                tu = e.get("tripUpdate") or {}
                vid = (tu.get("vehicle") or {}).get("id") or ""
                if vid:
                    idconv["veh:" + vid.rsplit("_", 1)[0]] += 1
                for stu in (tu.get("stopTimeUpdate") or []):
                    sid = stu.get("stopId") or ""
                    if sid:
                        idconv["stop:" + sid.rsplit("_", 1)[0]] += 1
                tid = (tu.get("trip") or {}).get("tripId") or ""
                if tid:
                    idconv["trip:" + tid.split("_", 1)[0]] += 1
        g = grid_ts(ts)
        if current_g is not None and g != current_g:
            snapshots[current_g] = state.trip_updates_by_vehicle(since_ts=current_g - SAMPLE_INTERVAL_S * 2)
        state.apply(msg)
        current_g = g
    if current_g is not None:
        snapshots[current_g] = state.trip_updates_by_vehicle(since_ts=current_g - SAMPLE_INTERVAL_S * 2)
    meta = dict(stats)
    meta["keysets"] = dict(keysets)
    meta["idconv"] = dict(idconv)
    meta["ts_min"] = ts_min
    meta["ts_max"] = ts_max
    meta["snapshots"] = len(snapshots)
    print(
        f"    {meta.get('lines', 0):,} lines, {len(snapshots)} snapshots, "
        f"{meta.get('entities', 0):,} entity updates, "
        f"range {time.strftime('%H:%M:%S', time.gmtime(ts_min or 0))}-"
        f"{time.strftime('%H:%M:%S', time.gmtime(ts_max or 0))} UTC",
        flush=True,
    )
    return snapshots, meta


def compare(ours_snaps: dict[int, dict], prod_snaps: dict[int, dict]) -> dict[str, Any]:
    # deltas[(slice, bucket)] and class_deltas[(slice, class)]
    pooled: dict[tuple[str, str], list[float]] = defaultdict(list)
    cls: dict[tuple[str, str], list[float]] = defaultdict(list)
    match: dict[str, Counter] = defaultdict(Counter)
    veh: Counter = Counter()
    veh_by_agency: dict[str, Counter] = defaultdict(Counter)
    only_ours: Counter = Counter()
    only_prod: Counter = Counter()
    only_ours_routes: Counter = Counter()
    only_prod_routes: Counter = Counter()
    routes_ours: Counter = Counter()
    routes_prod: Counter = Counter()
    ex_diff_trip: list[dict] = []
    ex_dir_flip: list[dict] = []
    ex_outlier: list[dict] = []
    # full censuses (not capped like the example lists)
    out_route: Counter = Counter()
    out_veh: Counter = Counter()
    out_n = 0
    dt_total = 0
    dt_on_other_veh = 0
    dt_misc_ours = 0
    dt_misc_prod = 0
    dt_gaps: list[float] = []
    dt_stop_overlap: list[float] = []
    dt_route: Counter = Counter()
    stops_per_tu: dict[str, list[int]] = defaultdict(list)
    tu_per_veh: dict[str, list[int]] = defaultdict(list)

    shared = sorted(set(ours_snaps) & set(prod_snaps))
    n_pairs = 0
    mid_snapshot: dict[str, Any] = {}

    for gts in shared:
        n_pairs += 1
        if MAX_PAIRS and n_pairs > MAX_PAIRS:
            break
        ours, prod = ours_snaps[gts], prod_snaps[gts]
        now = float(gts)
        both = set(ours) & set(prod)
        veh.update(ours=len(ours), prod=len(prod), both=len(both), snaps=1)
        for v, recs in ours.items():
            ag = recs[0]["agency"]
            veh_by_agency[ag]["ours"] += 1
            tu_per_veh["ours"].append(len(recs))
            for r in recs:
                routes_ours[r["route"]] += 1
                stops_per_tu["ours"].append(len(r["stops"]))
            if v not in prod:
                only_ours[v] += 1
                only_ours_routes[recs[0]["route"]] += 1
        for v, recs in prod.items():
            ag = recs[0]["agency"]
            veh_by_agency[ag]["prod"] += 1
            tu_per_veh["prod"].append(len(recs))
            for r in recs:
                routes_prod[r["route"]] += 1
                stops_per_tu["prod"].append(len(r["stops"]))
            if v not in ours:
                only_prod[v] += 1
                only_prod_routes[recs[0]["route"]] += 1
        for v in both:
            veh_by_agency[ours[v][0]["agency"]]["both"] += 1

        # index prod trips -> vehicles, to test the "our trip is on another bus in prod" hypothesis
        prod_trip_to_veh: dict[str, str] = {}
        for pv, precs in prod.items():
            for r in precs:
                prod_trip_to_veh.setdefault(r["trip"], pv)

        for v in both:
            o = ours[v][0]
            prod_recs = prod[v]
            m = next((rec for rec in prod_recs if rec["trip"] == o["trip"]), prod_recs[0])
            ag = o["agency"]
            for sl in ("ALL", ag):
                if o["trip"] == m["trip"]:
                    match[sl]["same_trip"] += 1
                elif o["route"] == m["route"]:
                    if (
                        o["direction"] is not None
                        and m["direction"] is not None
                        and o["direction"] != m["direction"]
                    ):
                        match[sl]["same_route_diff_direction"] += 1
                    else:
                        match[sl]["same_route_diff_trip"] += 1
                else:
                    match[sl]["diff_route"] += 1
            if o["trip"] != m["trip"]:
                on_other = prod_trip_to_veh.get(o["trip"])
                og, mg = origin_secs(o["trip"]), origin_secs(m["trip"])
                gap = abs(og - mg) / 60.0 if (og is not None and mg is not None) else None
                so, sm = set(o["stops"]), set(m["stops"])
                overlap = pct(len(so & sm), len(so | sm)) if (so or sm) else 0.0
                rec = dict(
                    veh=v,
                    agency=ag,
                    our_route=o["route"],
                    prod_route=m["route"],
                    our_trip=o["trip"],
                    prod_trip=m["trip"],
                    our_trip_on_prod_veh=on_other,
                    start_gap_min=gap,
                    stop_overlap_pct=overlap,
                    ts=gts,
                )
                if o["route"] == m["route"]:
                    dt_total += 1
                    dt_route[o["route"]] += 1
                    if on_other:
                        dt_on_other_veh += 1
                    if "MISC" in o["trip"]:
                        dt_misc_ours += 1
                    if "MISC" in m["trip"]:
                        dt_misc_prod += 1
                    if gap is not None:
                        dt_gaps.append(gap)
                    dt_stop_overlap.append(overlap)
                    # no directionId in this archive: use near-disjoint stop sets as a proxy
                    if overlap < 10.0:
                        if len(ex_dir_flip) < 400:
                            ex_dir_flip.append(rec)
                    elif len(ex_diff_trip) < 800:
                        ex_diff_trip.append(rec)
                else:
                    continue

            for stop, ot in o["stops"].items():
                mt = m["stops"].get(stop)
                if not mt or mt < now - 30 or ot < now - 30:
                    continue
                horizon = (mt - now) / 60.0
                bucket = (
                    "0-5 min" if horizon < 5
                    else "5-15 min" if horizon < 15
                    else "15-30 min" if horizon < 30
                    else "30+ min"
                )
                d = float(ot - mt)
                for sl in ("ALL", ag):
                    pooled[(sl, bucket)].append(d)
                    pooled[(sl, "ALL")].append(d)
                    cls[(sl, "express" if is_express(o["route"]) else "local")].append(d)
                if o["trip"] == m["trip"] and abs(d) > 600:
                    out_n += 1
                    out_route[o["route"]] += 1
                    out_veh[v] += 1
                    if len(ex_outlier) < 400:
                        ex_outlier.append(
                            dict(
                                veh=v, route=o["route"], trip=o["trip"], stop=stop,
                                ours=ot, prod=mt, delta=d, horizon=horizon, ts=gts,
                            )
                        )

        if not mid_snapshot and n_pairs >= max(1, len(shared) // 2):
            mid_snapshot = dict(
                ts=gts,
                ours=len(ours),
                prod=len(prod),
                both=len(both),
                only_ours=sorted(set(ours) - set(prod))[:40],
                only_prod=sorted(set(prod) - set(ours))[:40],
                n_only_ours=len(set(ours) - set(prod)),
                n_only_prod=len(set(prod) - set(ours)),
            )

    return dict(
        pooled={k: v for k, v in pooled.items()},
        cls={k: v for k, v in cls.items()},
        match={k: dict(v) for k, v in match.items()},
        veh=dict(veh),
        veh_by_agency={k: dict(v) for k, v in veh_by_agency.items()},
        only_ours=only_ours, only_prod=only_prod,
        only_ours_routes=only_ours_routes, only_prod_routes=only_prod_routes,
        routes_ours=routes_ours, routes_prod=routes_prod,
        ex_diff_trip=ex_diff_trip, ex_dir_flip=ex_dir_flip, ex_outlier=ex_outlier,
        out_n=out_n, out_route=out_route, out_veh=out_veh,
        dt=dict(
            total=dt_total, on_other_veh=dt_on_other_veh,
            misc_ours=dt_misc_ours, misc_prod=dt_misc_prod,
            gaps=dt_gaps, stop_overlap=dt_stop_overlap, routes=dt_route,
        ),
        stops_per_tu={k: (statistics.median(v), statistics.fmean(v)) for k, v in stops_per_tu.items() if v},
        tu_per_veh={k: statistics.fmean(v) for k, v in tu_per_veh.items() if v},
        snap_pairs=n_pairs, mid=mid_snapshot, shared=len(shared),
    )


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    ours_path, prod_path = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else f"{os.path.basename(ours_path)} vs {os.path.basename(prod_path)}"

    ours_snaps, om = build_sampled_snapshots(ours_path, "ours")
    prod_snaps, pm = build_sampled_snapshots(prod_path, "prod")
    r = compare(ours_snaps, prod_snaps)

    out: list[str] = []

    def p(s: str = "") -> None:
        print(s)
        out.append(s)

    def utc(t: Any) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(t))) if t else "–"

    p(f"# queuePredictions archive deep-dive — {label}")
    p()
    p(f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    p(f"- ours: `{os.path.basename(ours_path)}`  ({om.get('lines',0):,} lines, {om.get('entities',0):,} entities)")
    p(f"- prod: `{os.path.basename(prod_path)}`  ({pm.get('lines',0):,} lines, {pm.get('entities',0):,} entities)")
    p(f"- ours header ts range: {utc(om['ts_min'])} → {utc(om['ts_max'])} UTC")
    p(f"- prod header ts range: {utc(pm['ts_min'])} → {utc(pm['ts_max'])} UTC")
    p(f"- sample grid: {SAMPLE_INTERVAL_S} s; shared grid points: **{r['shared']}**; compared: {r['snap_pairs']}")
    # A full hour on a 60 s grid yields ~60 shared points. Far fewer means the two archives cover
    # different clock hours -- every downstream percentage is then computed off a handful of
    # snapshots and looks plausible while being meaningless. Prod's HH-59-59 naming makes this
    # easy to hit, so say it loudly in the report rather than leaving it to be spotted.
    expected_points = 3600 // SAMPLE_INTERVAL_S
    if r["shared"] < expected_points // 2:
        p()
        p(f"> **WARNING — misaligned archives.** Only {r['shared']} shared grid points for a "
          f"{SAMPLE_INTERVAL_S} s grid (expected ~{expected_points}). The two files almost certainly "
          f"cover different hours; compare the header ts ranges above. Treat every figure below as invalid.")
    p("- delta convention: **ours − prod, seconds** (positive = we predict LATER)")
    p()

    p("## Structure / volume")
    p()
    p("| metric | ours | prod | ratio |")
    p("|---|---|---|---|")
    ol, pl = om.get("lines", 0), pm.get("lines", 0)
    oe, pe = om.get("entities", 0), pm.get("entities", 0)
    p(f"| NDJSON lines | {ol:,} | {pl:,} | {ol/pl:.2f}× |" if pl else "| NDJSON lines | – | – | – |")
    p(f"| entity updates | {oe:,} | {pe:,} | {oe/pe:.2f}× |" if pe else "")
    p(f"| entities / line | {oe/ol:.3f} | {pe/pl:.3f} | – |" if ol and pl else "")
    p(f"| empty-entity lines | {om.get('empty_lines',0):,} ({pct(om.get('empty_lines',0), ol):.2f}%) | "
      f"{pm.get('empty_lines',0):,} ({pct(pm.get('empty_lines',0), pl):.2f}%) | – |")
    p(f"| grid snapshots | {om['snapshots']} | {pm['snapshots']} | – |")
    p(f"| TripUpdates / vehicle | {r['tu_per_veh'].get('ours',0):.2f} | {r['tu_per_veh'].get('prod',0):.2f} | – |")
    so, sp = r["stops_per_tu"].get("ours", (0, 0)), r["stops_per_tu"].get("prod", (0, 0))
    p(f"| stops / TripUpdate (med/mean) | {so[0]:.0f} / {so[1]:.1f} | {sp[0]:.0f} / {sp[1]:.1f} | – |")
    p()
    p("Key sets (first 5k lines each):")
    p()
    for k in sorted(set(om["keysets"]) | set(pm["keysets"])):
        p(f"- `{k}` — ours {om['keysets'].get(k,0)}, prod {pm['keysets'].get(k,0)}")
    for k in om:
        if k.startswith("incr_"):
            p(f"- incrementality `{k[5:]}` — ours {om.get(k,0)}, prod {pm.get(k,0)}")
    p()
    p("**Raw id conventions** (first 20k lines; prefix before the final `_`) — a mismatch here means a")
    p("downstream consumer must translate, even though the numeric ids are identical:")
    p()
    p("| id | ours | prod | same? |")
    p("|---|---|---|---|")
    oi, pi = om["idconv"], pm["idconv"]
    for kind in ("veh", "stop", "trip"):
        ov = {k[len(kind)+1:]: c for k, c in oi.items() if k.startswith(kind + ":")}
        pv = {k[len(kind)+1:]: c for k, c in pi.items() if k.startswith(kind + ":")}
        same = "✅" if set(ov) == set(pv) else "❌"
        p(f"| {kind}Id prefix | {', '.join(f'`{k}` ({c:,})' for k, c in sorted(ov.items(), key=lambda x:-x[1])[:3])} | "
          f"{', '.join(f'`{k}` ({c:,})' for k, c in sorted(pv.items(), key=lambda x:-x[1])[:3])} | {same} |")
    p()

    p("## Fleet overlap (averaged over compared snapshots)")
    p()
    s = max(1, r["veh"].get("snaps", 1))
    p("| slice | avg veh ours | avg veh prod | avg overlap | overlap % of prod |")
    p("|---|---|---|---|---|")
    p(f"| ALL | {r['veh']['ours']/s:.0f} | {r['veh']['prod']/s:.0f} | {r['veh']['both']/s:.0f} | "
      f"{pct(r['veh']['both'], r['veh']['prod']):.1f}% |")
    for ag in ("NYCT", "MTABC"):
        a = r["veh_by_agency"].get(ag, {})
        if a:
            p(f"| {ag} | {a.get('ours',0)/s:.0f} | {a.get('prod',0)/s:.0f} | {a.get('both',0)/s:.0f} | "
              f"{pct(a.get('both',0), a.get('prod',0)):.1f}% |")
    p()
    mid = r["mid"]
    if mid:
        p(f"Mid-hour snapshot ({utc(mid['ts'])} UTC): ours {mid['ours']}, prod {mid['prod']}, "
          f"overlap {mid['both']}, **only ours {mid['n_only_ours']}**, **only prod {mid['n_only_prod']}**")
        p()
        p(f"- only ours (sample): {', '.join(mid['only_ours'][:20])}")
        p(f"- only prod (sample): {', '.join(mid['only_prod'][:20])}")
        p()
    p("Persistent one-sided vehicles (appearing in ≥50% of snapshots on one side only):")
    p()
    half = max(1, r["snap_pairs"] // 2)
    po = [v for v, c in r["only_ours"].items() if c >= half]
    pp = [v for v, c in r["only_prod"].items() if c >= half]
    p(f"- ours-only persistent: **{len(po)}** (of {len(r['only_ours'])} distinct ever-ours-only)")
    p(f"- prod-only persistent: **{len(pp)}** (of {len(r['only_prod'])} distinct ever-prod-only)")
    p()
    p("Top routes among one-sided vehicles (snapshot-weighted):")
    p()
    p("| ours-only route | n | prod-only route | n |")
    p("|---|---|---|---|")
    a = r["only_ours_routes"].most_common(12)
    b = r["only_prod_routes"].most_common(12)
    for i in range(max(len(a), len(b))):
        l = f"{a[i][0]} | {a[i][1]}" if i < len(a) else " | "
        rr = f"{b[i][0]} | {b[i][1]}" if i < len(b) else " | "
        p(f"| {l} | {rr} |")
    p()
    ro, rp = set(r["routes_ours"]), set(r["routes_prod"])
    p(f"- routes only in OURS ({len(ro-rp)}): {', '.join(sorted(ro-rp)) or '–'}")
    p(f"- routes only in PROD ({len(rp-ro)}): {', '.join(sorted(rp-ro)) or '–'}")
    p()

    p("## Trip matching")
    p()
    p("| slice | pairs | same trip_id | same route, diff trip | diff direction | diff route |")
    p("|---|---|---|---|---|---|")
    for sl in ("ALL", "NYCT", "MTABC"):
        m = r["match"].get(sl)
        if not m:
            continue
        tot = sum(m.values())
        p(f"| {sl} | {tot:,} | **{pct(m.get('same_trip',0),tot):.2f}%** | "
          f"{pct(m.get('same_route_diff_trip',0),tot):.2f}% ({m.get('same_route_diff_trip',0)}) | "
          f"{pct(m.get('same_route_diff_direction',0),tot):.2f}% ({m.get('same_route_diff_direction',0)}) | "
          f"{pct(m.get('diff_route',0),tot):.2f}% ({m.get('diff_route',0)}) |")
    p()

    p("## ETA deltas by horizon")
    p()
    for sl in ("ALL", "NYCT", "MTABC"):
        if not any(k[0] == sl for k in r["pooled"]):
            continue
        p(f"**{sl}**")
        p()
        p("| bucket | n | median | mean | MAE | p10/p90 | ≤30 s | ≤1 min | ≤3 min |")
        p("|---|---|---|---|---|---|---|---|---|")
        for b in ("ALL", "0-5 min", "5-15 min", "15-30 min", "30+ min"):
            st = delta_stats(r["pooled"].get((sl, b), []))
            if st:
                p(f"| {b} | {st['n']:,} | {st['median']:+.0f} | {st['mean']:+.0f} | {st['mae']:.0f} | "
                  f"{st['p10']:+.0f} / {st['p90']:+.0f} | {st['w30']:.0f}% | {st['w60']:.0f}% | {st['w180']:.0f}% |")
        p()
        p("| class | n | median | mean | MAE | ≤1 min | ≤3 min |")
        p("|---|---|---|---|---|---|---|")
        for c in ("local", "express"):
            st = delta_stats(r["cls"].get((sl, c), []))
            if st:
                p(f"| {c} | {st['n']:,} | {st['median']:+.0f} | {st['mean']:+.0f} | {st['mae']:.0f} | "
                  f"{st['w60']:.0f}% | {st['w180']:.0f}% |")
        p()

    p("## Examples")
    p()
    p(f"### Same route, different trip_id ({len(r['ex_diff_trip'])} captured, sample of 15)")
    p()
    p("| veh | agency | route | our trip | prod trip | our trip is on prod veh |")
    p("|---|---|---|---|---|---|")
    seen: set[str] = set()
    shown = 0
    for e in r["ex_diff_trip"]:
        if e["veh"] in seen:
            continue
        seen.add(e["veh"])
        p(f"| {e['veh']} | {e['agency']} | {e['our_route']} | `{e['our_trip']}` | `{e['prod_trip']}` | "
          f"{e['our_trip_on_prod_veh'] or '—'} |")
        shown += 1
        if shown >= 15:
            break
    p()
    dt = r["dt"]
    n = max(1, dt["total"])
    p(f"**Full census of all {dt['total']:,} same-route/different-trip pairs** (not just the sample above):")
    p()
    p("| property | value |")
    p("|---|---|")
    p(f"| our trip is on a DIFFERENT vehicle in prod | **{dt['on_other_veh']:,} ({pct(dt['on_other_veh'], n):.0f}%)** |")
    p(f"| our trip id is `MISC_*` | {dt['misc_ours']:,} ({pct(dt['misc_ours'], n):.0f}%) |")
    p(f"| prod trip id is `MISC_*` | {dt['misc_prod']:,} ({pct(dt['misc_prod'], n):.0f}%) |")
    if dt["gaps"]:
        g = sorted(dt["gaps"])
        p(f"| scheduled start gap (NYCT, n={len(g):,}) | median **{statistics.median(g):.0f} min**, "
          f"p10 {g[int(.1*len(g))]:.0f} / p90 {g[int(.9*len(g))]:.0f} min |")
        p(f"| start gap < 1 min (identical departure time) | {sum(1 for x in g if x < 1):,} "
          f"({pct(sum(1 for x in g if x < 1), len(g)):.1f}%) |")
    if dt["stop_overlap"]:
        so = sorted(dt["stop_overlap"])
        p(f"| stop-set overlap (Jaccard %) | median {statistics.median(so):.0f}%, "
          f"p10 {so[int(.1*len(so))]:.0f} / p90 {so[int(.9*len(so))]:.0f}% |")
        p(f"| **near-disjoint stops (<10%) — direction-flip proxy** | {sum(1 for x in so if x < 10):,} "
          f"({pct(sum(1 for x in so if x < 10), len(so)):.0f}% of mismatches) |")
    p()
    p(f"Top mismatch routes: {', '.join(f'{k} ({v})' for k, v in dt['routes'].most_common(10))}")
    p()
    p("> `directionId` is **absent from both archives**, so direction flips cannot be counted directly")
    p("> (the table above reports 0.00% by construction). The near-disjoint-stop-set share is the proxy.")
    p()
    p(f"### Likely direction flips (stop-set overlap <10%; {len(r['ex_dir_flip'])} captured, sample of 8)")
    p()
    p("| veh | route | our trip | prod trip | stop overlap |")
    p("|---|---|---|---|---|")
    for e in r["ex_dir_flip"][:8]:
        p(f"| {e['veh']} | {e['our_route']} | `{e['our_trip']}` | `{e['prod_trip']}` | {e['stop_overlap_pct']:.0f}% |")
    p()
    p(f"### Same-trip ETA outliers |Δ| > 10 min — **{r['out_n']:,} of "
      f"{len(r['pooled'].get(('ALL','ALL'), [])):,} pairs ({pct(r['out_n'], len(r['pooled'].get(('ALL','ALL'), []))):.3f}%)**")
    p()
    p("| veh | route | stop | horizon (min) | ours (UTC) | prod (UTC) | Δ (s) |")
    p("|---|---|---|---|---|---|---|")
    for e in sorted(r["ex_outlier"], key=lambda x: -abs(x["delta"]))[:12]:
        p(f"| {e['veh']} | {e['route']} | {e['stop']} | {e['horizon']:.0f} | "
          f"{utc(e['ours'])[11:]} | {utc(e['prod'])[11:]} | {e['delta']:+.0f} |")
    p()
    exp_n = sum(c for rt, c in r["out_route"].items() if is_express(rt))
    p(f"**Concentration:** {len(r['out_veh'])} distinct vehicles and {len(r['out_route'])} distinct routes "
      f"account for all {r['out_n']:,} outlier pairs; express share **{pct(exp_n, max(1, r['out_n'])):.0f}%**.")
    p()
    p(f"- top vehicles: {', '.join(f'{k} ({v})' for k, v in r['out_veh'].most_common(8))}")
    p(f"- top routes: {', '.join(f'{k} ({v})' for k, v in r['out_route'].most_common(8))}")
    p()

    text = "\n".join(out) + "\n"
    with open(REPORT, "w") as fh:
        fh.write(text)
    print(f"\nreport -> {REPORT}")


if __name__ == "__main__":
    main()
