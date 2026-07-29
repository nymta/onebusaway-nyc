#!/usr/bin/env python3
"""Render the Mongo history-fill chart (history-fill-2026-07-28.png).

Data source: aggregations over `AggregateLinkTimes` on the EC2 host (see
COMPARISON-RUNBOOK "History fill" section for the exact queries). Each doc is one
{routeId, headId, tailId, timeOfDay, scheduleType} bucket holding up to
historicalComponentRecordCount=300 traversals. `timeOfDay` is the **UTC hour**.

Edit the DATA block after a fresh aggregation and re-run to refresh the chart.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

# ---- DATA (snapshot 2026-07-28 ~09:00 ET, history day 7) -------------------------------
SNAPSHOT = "2026-07-28 09:00 ET · history day 7 · 904,289 buckets · 8.04 M observations"
CAP = 300

# depth histogram: (label, bucket count) from $bucket boundaries [1,2,3,4,5,6,8,11,16,21,31,51,76,101,151,301]
DEPTH_BINS = [("1", 90199), ("2", 161335), ("3", 123016), ("4", 90343), ("5", 68915),
              ("6-7", 66843), ("8-10", 59302), ("11-15", 61954), ("16-20", 59096),
              ("21-30", 80026), ("31-50", 37768), ("51-75", 5107), ("76-100", 359),
              ("101-150", 25), ("151-300", 1)]

# weekday (scheduleType=1) by UTC hour -> (avg depth, links with n>=50); index = UTC hour
BY_UTC_HOUR = {0: (19.04, 163), 1: (16.22, 65), 2: (14.26, 27), 3: (12.76, 1), 4: (10.87, 0),
               5: (8.23, 0), 6: (6.41, 0), 7: (6.60, 0), 8: (9.09, 8), 9: (14.44, 65),
               10: (22.01, 528), 11: (27.84, 1169), 12: (27.09, 1016), 13: (19.39, 151),
               14: (17.60, 60), 15: (17.09, 53), 16: (18.81, 108), 17: (21.41, 167),
               18: (21.97, 215), 19: (23.14, 315), 20: (23.58, 359), 21: (25.02, 545),
               22: (25.25, 646), 23: (22.30, 368)}
UTC_OFFSET = -4        # EDT

SCHED = [("Weekday", 18.96, 324255), ("Saturday", 3.47, 291961), ("Sunday", 3.04, 288073)]

# ---- style ----------------------------------------------------------------------------
SURFACE, INK, INK2, INK3 = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8983"
SERIES = "#2a78d6"      # categorical slot 1; validated >=3:1 on this surface
GRID = "#e6e5e0"


def bars(ax, labels, values, *, title, subtitle=None, ylabel=None, logy=False,
         annotate=(), rotate=0):
    n = len(values)
    ax.set_facecolor(SURFACE)
    # 2px surface gap between adjacent bars -> width 0.78 at this figure scale
    ax.bar(range(n), values, width=0.78, color=SERIES, linewidth=0, zorder=3)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.xaxis.grid(False)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="both", length=0, colors=INK2, labelsize=8)
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=rotate, ha="right" if rotate else "center", fontsize=7.5)
    if logy:
        ax.set_yscale("log")
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2, fontsize=8.5)
    ax.set_title(title, color=INK, fontsize=10.5, fontweight="bold", loc="left", pad=14 if subtitle else 6)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=INK2, fontsize=8, va="bottom")
    # selective direct labels only
    for i in annotate:
        v = values[i]
        txt = format(int(v), ",") if float(v).is_integer() else "%.1f" % v
        ax.annotate(txt, (i, v), textcoords="offset points", xytext=(0, 3),
                    ha="center", fontsize=7.5, color=INK, fontweight="bold", zorder=4)


fig, axes = plt.subplots(2, 2, figsize=(12.6, 7.4), facecolor=SURFACE)
fig.subplots_adjust(hspace=0.52, wspace=0.18, top=0.855, bottom=0.09, left=0.07, right=0.98)

fig.text(0.07, 0.955, "Mongo historical link-time buckets — fill progress", color=INK,
         fontsize=15, fontweight="bold")
fig.text(0.07, 0.925, SNAPSHOT, color=INK2, fontsize=9.5)
fig.text(0.07, 0.900,
         "Deepest bucket anywhere = 157 of the 300 cap (52%).  Mean depth 8.9.  "
         "Buckets at the cap: 0.", color=INK, fontsize=9.5, fontweight="bold")

# A. depth histogram
labels = [b[0] for b in DEPTH_BINS]
vals = [b[1] for b in DEPTH_BINS]
bars(axes[0][0], labels, vals, title="A · How deep is each bucket?",
     subtitle="buckets by number of stored traversals (log scale) — all schedule types",
     ylabel="buckets", logy=True, annotate=(1, 11, 12, 13, 14), rotate=45)

# B. weekday depth by local hour
hours = [(h + 24 + UTC_OFFSET) % 24 for h in range(24)]           # local hour per UTC hour
order = sorted(range(24), key=lambda h: (h + 24 + UTC_OFFSET) % 24)
b_labels = ["%02d" % ((h + 24 + UTC_OFFSET) % 24) for h in order]
b_vals = [BY_UTC_HOUR[h][0] for h in order]
bars(axes[0][1], b_labels, b_vals, title="B · Weekday depth by time of day",
     subtitle="mean traversals per bucket, by LOCAL hour (buckets are keyed on the UTC hour)",
     ylabel="mean traversals", annotate=(7, 18), rotate=0)

# C. deep links by local hour
c_vals = [BY_UTC_HOUR[h][1] for h in order]
bars(axes[1][0], b_labels, c_vals, title="C · Where the usable history is",
     subtitle="links with >=50 traversals (weekday), by LOCAL hour",
     ylabel="links with n>=50", annotate=(7, 8, 18), rotate=0)

# D. schedule type
d_labels = ["%s\n%s buckets" % (s[0], format(s[2], ",")) for s in SCHED]
d_vals = [s[1] for s in SCHED]
bars(axes[1][1], d_labels, d_vals, title="D · Weekday history is ~5x deeper than weekend",
     subtitle="mean traversals per bucket by schedule type",
     ylabel="mean traversals", annotate=(0, 1, 2))

fig.text(0.07, 0.030,
         "One bucket = {route, head stop, tail stop, hour, schedule type}; cap 300 "
         "(predictions.historicalComponentRecordCount).",
         color=INK3, fontsize=8)
fig.text(0.07, 0.010,
         "Where a bucket is empty, the historical 40% of the blend silently reverts to schedule for "
         "that link.", color=INK3, fontsize=8)

out = "/Users/timothyshertzer/Documents/repos/onebusaway-nyc/local-loop/history-fill-2026-07-28.png"
fig.savefig(out, dpi=170, facecolor=SURFACE)
print("wrote %s" % out)
