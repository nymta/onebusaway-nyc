#!/usr/bin/env python3
"""Why does our queuePredictions archive hold ~1.7x more differential lines than prod's?

Tests the competing explanations directly, without merging state:

  1. **Publish cadence** — updates per vehicle per hour (ours vs prod). If we simply tick more often
     per bus, this ratio equals the line ratio and the fleet sizes match.
  2. **Fleet size** — distinct vehicles ever published.
  3. **Payload size** — stopTimeUpdates per entity (are our updates bigger or just more frequent?).
  4. **Inter-update interval** — per-vehicle seconds between consecutive updates (the deadband
     signature: prod should show a wider floor if it admits AVL less often).
  5. **Churn** — how often a re-publish actually changes the predicted times for the same
     (trip, stop). A high no-change rate means we re-emit unchanged predictions.

Usage:
    python3 analyze-predictions-cadence.py <ours.zip> <prod.zip> [label]
Env:
    REPORT_OUT   markdown output path
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import zipfile
from collections import Counter, defaultdict
from typing import Any

REPORT = os.environ.get("REPORT_OUT", "predictions-cadence.md")


def norm(i: str) -> str:
    return i.split("_", 1)[1] if "_" in i and not i.split("_")[0].isdigit() else i


def to_epoch_sec(v: Any) -> int:
    t = int(v)
    return t // 1000 if t > 1_000_000_000_000 else t


def scan(zip_path: str, label: str) -> dict[str, Any]:
    updates_per_veh: Counter = Counter()
    stu_per_entity: list[int] = []
    last_seen: dict[str, int] = {}
    intervals: list[int] = []
    # churn: last predicted time per (veh, trip, stop) -> did the re-publish change it?
    lastpred: dict[tuple[str, str, str], int] = {}
    changed = 0
    unchanged = 0
    delta_abs: list[int] = []
    lines = 0
    entities = 0
    print(f"  scanning {label}: {os.path.basename(zip_path)}", flush=True)
    z = zipfile.ZipFile(zip_path)
    with z.open(z.namelist()[0]) as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            msg = json.loads(raw)
            lines += 1
            hts = to_epoch_sec(msg["header"]["timestamp"])
            for e in msg.get("entity") or []:
                tu = e.get("tripUpdate")
                if not tu:
                    continue
                entities += 1
                vid = norm((tu.get("vehicle") or {}).get("id") or "")
                if not vid:
                    continue
                updates_per_veh[vid] += 1
                stus = tu.get("stopTimeUpdate") or []
                stu_per_entity.append(len(stus))
                prev = last_seen.get(vid)
                if prev is not None and 0 < hts - prev < 3600:
                    intervals.append(hts - prev)
                last_seen[vid] = hts
                trip = norm((tu.get("trip") or {}).get("tripId") or "")
                for stu in stus:
                    sid = stu.get("stopId")
                    t = (stu.get("arrival") or {}).get("time") or (stu.get("departure") or {}).get("time")
                    if not sid or not t:
                        continue
                    key = (vid, trip, norm(sid))
                    tsec = to_epoch_sec(t)
                    old = lastpred.get(key)
                    if old is not None:
                        if old == tsec:
                            unchanged += 1
                        else:
                            changed += 1
                            delta_abs.append(abs(tsec - old))
                    lastpred[key] = tsec
    iv = sorted(intervals)
    return dict(
        lines=lines,
        entities=entities,
        vehicles=len(updates_per_veh),
        upv=sorted(updates_per_veh.values()),
        stu_mean=statistics.fmean(stu_per_entity) if stu_per_entity else 0,
        stu_median=statistics.median(stu_per_entity) if stu_per_entity else 0,
        iv=iv,
        changed=changed,
        unchanged=unchanged,
        delta_abs=sorted(delta_abs),
    )


def q(a: list[int], p: float) -> float:
    return a[min(len(a) - 1, int(p * len(a)))] if a else 0.0


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    o = scan(sys.argv[1], "ours")
    pr = scan(sys.argv[2], "prod")
    label = sys.argv[3] if len(sys.argv) > 3 else "cadence"

    out: list[str] = []

    def p(s: str = "") -> None:
        print(s)
        out.append(s)

    def ratio(a: float, b: float) -> str:
        return f"{a / b:.2f}×" if b else "–"

    p(f"# Why the line-count gap? publish cadence — {label}")
    p()
    p(f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    p(f"- ours: `{os.path.basename(sys.argv[1])}`   prod: `{os.path.basename(sys.argv[2])}`")
    p()
    p("| metric | ours | prod | ratio |")
    p("|---|---|---|---|")
    p(f"| NDJSON lines | {o['lines']:,} | {pr['lines']:,} | {ratio(o['lines'], pr['lines'])} |")
    p(f"| tripUpdate entities | {o['entities']:,} | {pr['entities']:,} | {ratio(o['entities'], pr['entities'])} |")
    p(f"| **distinct vehicles** | {o['vehicles']:,} | {pr['vehicles']:,} | {ratio(o['vehicles'], pr['vehicles'])} |")
    p(f"| **updates / vehicle / hour (median)** | {statistics.median(o['upv']):.0f} | "
      f"{statistics.median(pr['upv']):.0f} | {ratio(statistics.median(o['upv']), statistics.median(pr['upv']))} |")
    p(f"| updates / vehicle / hour (mean) | {statistics.fmean(o['upv']):.1f} | "
      f"{statistics.fmean(pr['upv']):.1f} | {ratio(statistics.fmean(o['upv']), statistics.fmean(pr['upv']))} |")
    p(f"| stopTimeUpdates / entity (median) | {o['stu_median']:.0f} | {pr['stu_median']:.0f} | "
      f"{ratio(o['stu_median'], pr['stu_median'])} |")
    p(f"| stopTimeUpdates / entity (mean) | {o['stu_mean']:.1f} | {pr['stu_mean']:.1f} | "
      f"{ratio(o['stu_mean'], pr['stu_mean'])} |")
    p()
    p("**Per-vehicle seconds between consecutive updates** (the deadband / AVL-admission signature):")
    p()
    p("| percentile | ours | prod |")
    p("|---|---|---|")
    for lbl, pp in (("p10", .10), ("p25", .25), ("median", .50), ("p75", .75), ("p90", .90)):
        p(f"| {lbl} | {q(o['iv'], pp):.0f} s | {q(pr['iv'], pp):.0f} s |")
    p()
    p("**Churn — does a re-publish actually change the prediction?** (same vehicle+trip+stop, "
      "consecutive appearances)")
    p()
    tot_o = o["changed"] + o["unchanged"]
    tot_p = pr["changed"] + pr["unchanged"]
    p("| metric | ours | prod |")
    p("|---|---|---|")
    p(f"| re-publications compared | {tot_o:,} | {tot_p:,} |")
    p(f"| **value unchanged** | {o['unchanged']:,} ({100*o['unchanged']/max(1,tot_o):.1f}%) | "
      f"{pr['unchanged']:,} ({100*pr['unchanged']/max(1,tot_p):.1f}%) |")
    p(f"| value changed | {o['changed']:,} ({100*o['changed']/max(1,tot_o):.1f}%) | "
      f"{pr['changed']:,} ({100*pr['changed']/max(1,tot_p):.1f}%) |")
    p(f"| median change size (when changed) | {statistics.median(o['delta_abs']) if o['delta_abs'] else 0:.0f} s | "
      f"{statistics.median(pr['delta_abs']) if pr['delta_abs'] else 0:.0f} s |")
    p(f"| p90 change size | {q(o['delta_abs'], .9):.0f} s | {q(pr['delta_abs'], .9):.0f} s |")
    p()
    p("**Attribution.** line ratio = fleet ratio × updates-per-vehicle ratio (× entities-per-line, ~1):")
    p()
    fr = o["vehicles"] / max(1, pr["vehicles"])
    ur = statistics.fmean(o["upv"]) / max(1e-9, statistics.fmean(pr["upv"]))
    p(f"- fleet ratio **{fr:.2f}×** × updates/vehicle ratio **{ur:.2f}×** = **{fr*ur:.2f}×** "
      f"(observed entity ratio {o['entities']/max(1,pr['entities']):.2f}×)")
    p()
    text = "\n".join(out) + "\n"
    with open(REPORT, "w") as fh:
        fh.write(text)
    print(f"\nreport -> {REPORT}")


if __name__ == "__main__":
    main()
