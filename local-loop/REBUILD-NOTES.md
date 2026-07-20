# Rebuilding inference + predictions — anatomy + modern alternatives

Scope: what a from-scratch system that **subsumes both repos** (OBA-NYC inference + the predictions engine) would
actually have to cover *beyond* the headline matcher/predictor, and whether there are **better ways** to build
each piece than OBA's specific implementation. Companion to `PRODUCTIONIZING.md` (deploying *this* repo) and
`TRIP-MATCHING.md` (how the current matcher works).

> **Verification status.** Part 1 (anatomy) is grounded in this repo's source (the exploration we did). Part 2
> (better ways): the **map-matching** and **bunching/headway** points are web-grounded (Wikipedia, cited);
> the **ML-prediction / products / stack** points are synthesized from domain knowledge (model cutoff ~early
> 2026) because the managed web-search tools were unavailable this session. Treat named methods/tools/products
> in Part 2 as well-established starting points to confirm with a proper cited literature pass later.

---

# Part 1 — Anatomy of B (inference) and C (predictions) beyond the headline

The matcher and the prediction blend are ~30–40% of the surface area. The rest:

## B. Inference — everything wrapped around the matcher

**1. Per-vehicle lifecycle & input hygiene.** Long-lived stateful instance per vehicle; most production
robustness lives here: drop out-of-order records, ~3 s min inter-record interval, future-timestamp cutoff, a
**~20-min auto-reset** after gaps, **teleport** reset (> ~1 km jump), **stale** reset (> ~3 min gap),
engine-off/missing-fix carry-forward. *Without it the filter thrashes on noise/gaps and never stabilizes at
scale.* Tedious, not deep — but real and easy to under-build.

**2. Run / operator inference (NYC-specific, high value).** Operator id → assigned run; reported run id →
**fuzzy matches** (drivers mistype); both expand to candidate routes/trips. **Formal vs informal** run
classification. *Runs are the second-strongest signal after DSC (and the only signal when the DSC is
invalid/out-of-service), and "formal" gates both a tighter schedule-deviation likelihood and whether schedule
deviation is even published downstream.* Medium effort; needs the operator-assignment + run data.

**3. Journey-phase state machine (the edge-case-heavy heart).** Phases `AT_BASE / DEADHEAD_* / LAYOVER_* /
IN_PROGRESS`, layover detection (stopped ≥ ~120 s within ~250 m of a terminal), deadhead (wrong-direction /
between-trips / out-of-service DSC / pull-out), detour detection, base/depot detection, `isLocationOnATrip`.
**Only `IN_PROGRESS` flows into the predictor** — wrong phase = suppressed real predictions or garbage for a
deadheading bus. Highest-effort part of B; the long tail (terminals, short-turns, expresses skipping stops,
detours) is where a quarter of tuning goes.

**4. Assigned-block prior.** Pullout/UTS → "this vehicle should be on block X" → likelihood boost.
Disambiguation on ambiguous corridors. Skippable (we bypassed it locally) at an accuracy cost.

**5. Output contract.** Inferred-location bean: phase, block id, service date, distance-along-block/-trip,
schedule deviation (only when formal), snapped lat/lon, detour→"deviated" remap, inferred DSC/run. Must be
faithful or C silently degrades.

*B's real time/risk:* the phase machine + run/block continuity (edge cases) and the unsexy lifecycle/reset
logic (scale robustness). The matcher math is bounded; these are open-ended.

## C. Predictions — everything around the weighted blend

**1. The lattice — the core, and where 5 s pings pay off.** Converts the position stream into *observed travel
times*: per-vehicle interpolation record; per-stop **delta(before)/actual/epsilon(after)** spatial windows
(≈50 m / 25 m); **constant-speed interpolation between consecutive fixes** to back-compute the instant each
stop window was crossed (a fix landing *inside* a window = exact, non-interpolated arrival); pairs consecutive
arrivals into **link traversal times**; monotonicity validation; reset thresholds (~3 min / ~1 km); volatile
per-vehicle cache + pruner. *This is the entire empirical signal behind recent+historical, and exactly where
5 s vs 30 s lands* (shorter gaps → smaller constant-speed error, more exact in-window arrivals). Medium-high;
the edge cases (multi-stop gaps, terminals/wrap-around, layovers, missed stops, turns) are the work.

**2. Observed-arrival semantics.** delta/actual/epsilon, arrival-vs-departure, exact-vs-interpolated — this *is*
your near-ground-truth arrival dataset (reusable for the accuracy comparison). Subtle and must stay consistent.

**3. Recent store.** Per-link ring buffer (last ~5), median, in-memory, lost on restart, not time-segmented. Easy.

**4. Historical store + bucketing.** Aggregate link times into **(route, head, tail, time-of-day bucket,
schedule-type)** keys (hourly buckets; weekday/Sat/Sun), keep last ~300 per bucket + a median, plus a raw
archive. Straightforward DB work, but the **warm-up** (weeks of data across all times-of-day/schedule-types
before a bucket is meaningful) is a planning fact, not code. Medium effort + calendar time.

**5. Historical cache management.** Pre-load the next ~4 h of buckets into memory (~2 h at startup), rotate as
the day advances — so predictions don't hit the DB per request. Operational, medium.

**6. The prognosticator blend + arrival chaining.** Per-link weighted sum with **schedule as filler**, the
in-progress **first-link interpolation** (the one part finer GPS helps even at schedule-only weights), forward
chaining stop-by-stop, prediction horizon (current/layover/next trip). The easy ~10%.

**7. Trip/stop-details services.** Re-derive each trip's stop sequence/geometry/distances to predict against —
i.e. C also leans hard on the shared **transit-data layer** (GTFS+STIF→graph/blocks/calendar/spatial).

*C's real time/risk:* the lattice (interpolation correctness + edge cases) and the historical
bucketing/cache/warm-up infra. The blend itself is trivial.

## The coupling
**C's accuracy is bounded by B's accuracy.** Wrong phase/trip in B → wrong observed link times → poisoned
recent/historical buckets (with lag, since bad data lingers in the 300-deep history). The matcher must be solid
*before* the historical layer is worth enabling. The **5 s advantage** materializes in exactly two places —
B's faster convergence/tighter position and C's lattice interpolation — and only *compounds* downstream once
recent/historical weights are non-zero (which needs the warm-up).

---

# Part 2 — Is there a better way to build each piece?

OBA-NYC's choices are ~2010-era and battle-tested but not state-of-the-art. By sub-problem:

## 2.1 Map-matching / trip assignment (B core)
- OBA uses a bespoke **Rao-Blackwellized particle filter**. Map-matching is a mature field; methods split into
  geometric, topological, probabilistic/**Hidden-Markov-Model**, and others, and into **real-time (online)** vs
  **offline** — online trades some accuracy for latency, and noisy GPS is best resolved by using the *history*
  of points to infer the route rather than nearest-segment snapping [Wikipedia, Map matching].
- **HMM + Viterbi** (Newson–Krumm lineage) is the canonical modern approach and, for *transit*, arguably a
  better engineering choice than a particle filter: discretize position-along-trip; emission = Gaussian on
  GPS-to-shape distance; transition = feasible along-route movement. Deterministic, debuggable, ~3–5
  interpretable parameters (vs a particle cloud). See `PRODUCTIONIZING.md` §"training" — it's parameter tuning,
  not data-hungry ML, and OBA's own `IN_PROGRESS` labels make near-free validation data.
- **Off-the-shelf matchers** (Valhalla **Meili**, **OSRM** `match`, **GraphHopper** map-matching) are HMM-based
  but match to *road networks (OSM)*, not GTFS trip shapes — **not directly usable**, but their HMM cores are
  good reference implementations to adapt.
- **Why the transit problem is easier** (so you don't need OBA's full machinery): candidates are a *small known
  set of trip shapes*, and **DSC + run hard-constrain** them. The residual problem is "which of a few trips +
  where on it + which direction," not open-world matching.
- **Better than OBA specifically:** fold the **journey phase** into the state space (`state = trip ×
  position-bin × phase`) so phase is inferred *jointly* rather than via post-hoc 120 s/250 m heuristics — more
  principled, at the cost of a larger state space.
- Learned/neural map-matchers (seq2seq/transformer) exist but are overkill and data-hungry for this constrained
  problem — not worth it.

## 2.2 Lifecycle / phase / state management (B)
- OBA runs a per-vehicle JVM object + ZeroMQ. A **streaming framework** (Apache **Flink**, **Kafka Streams**,
  or Python **Faust**) gives keyed-by-vehicle state, checkpointing, exactly-once, and horizontal scale for free —
  a far better operational story than per-vehicle instances + a hand-rolled broker, and it removes the
  detached-process fragility we hit locally.

## 2.3 Lattice / observed travel times (C)
- The constant-speed interpolation over map-matched distance-along-shape is the right abstraction, and the 5 s
  feed makes it good. Modern improvements are mostly engineering: light **Kalman/particle smoothing** of the
  position track before interpolation to denoise; streaming dedup/validation. Keep this conceptually; modernize
  the implementation. The clean output — **observed link travel times** — should be a first-class, stored
  artifact (it's both the predictor's training data *and* the comparison's ground truth).

## 2.4 Prediction algorithm (C core) — the biggest upgrade opportunity
OBA's per-link **weighted median of schedule/recent/historical** is interpretable but feature-poor: it ignores
upstream/downstream congestion, **headway/bunching**, weather, dwell-time patterns, and day-of-week
interactions. It is essentially a hand-crafted special case of what a learned model subsumes. Modern options
(roughly increasing cost/accuracy):
- **Kalman filter** — lightweight online travel-time/state estimation; strong for short horizons; classic.
- **Gradient-boosted trees (XGBoost / LightGBM)** on engineered features (time-of-day, day-of-week, recent
  link speeds, **upstream** travel times, **headway**, schedule deviation, segment, weather) — the pragmatic
  workhorse: strong accuracy, fast, modest data needs, ships in-process. *Most promising first upgrade.*
- **Deep learning** — LSTM/GRU/Seq2Seq for sequential travel time; **spatiotemporal Graph Neural Networks**
  (e.g. DCRNN, Graph WaveNet) model the network's spatial correlations and are state-of-the-art for
  traffic/travel-time forecasting — best for whole-network accuracy, highest data/infra cost.
- **Headway/bunching-aware** prediction for high-frequency routes: on frequent service, a late bus gets later
  (picks up extra riders) while the follower catches up, degrading headway and forming bunches [Wikipedia, Bus
  bunching]. Schedule-centric prediction handles this poorly; modeling **headway** directly matters more than
  schedule deviation on those routes.
- **Probabilistic / quantile** outputs (predict a distribution, not a point) — increasingly expected; can be
  surfaced as uncertainty.
- **Industry:** products like Swiftly, the Transit app, Moovit, and Google Maps use ML-based predictions
  blending real-time + historical (+ traffic/crowdsourced) data rather than schedule-deviation heuristics
  *(exact methodologies are proprietary — verify)*.

**Takeaway for the comparison goal:** the highest-leverage "better way" to actually beat MTA is to **swap the
predictor for a GBM** trained on the **5 s-derived observed link travel times** (plus headway/upstream
features), while **keeping OBA's matcher** (== MTA's). That isolates the win, is tractable (weeks once observed
travel times are flowing), and avoids re-paying the matcher + STIF-integration cost.

## 2.5 Overall architecture / stack
- OBA: JDK 11, Spring, ZeroMQ, per-vehicle particle filter, Mongo/HSQLDB, `.obj` bundles.
- Modern shape: a **streaming pipeline** (Kafka/Flink or Faust) with keyed per-vehicle state; GTFS/GTFS-RT libs;
  **observed link travel times as a clean interface** into a **feature/label store**; a columnar/timeseries
  store (Parquet/ClickHouse/Postgres+TimescaleDB) instead of Mongo median-aggregates; the predictor as an
  in-process GBM or a model server you can **retrain offline without touching the matcher**; containerized.
- The key architectural move: **separate online state-estimation (matching) from offline-trainable prediction**,
  with observed link travel times as the boundary. That turns the B↔C coupling from a liability into a clean
  seam and lets you iterate predictors (GBM → GNN) independently.

## 2.6 Build-vs-reuse
- The **generic OneBusAway** (`onebusaway-application-modules`) has its own, *simpler* prediction logic — not
  obviously better than the NYC prognosticator. Reuse buys little on the prediction side.
- Newer OSS GTFS-RT prediction engines may exist — **flag for a cited web pass** when search tooling is back.

---

## Bottom line
- Subsuming both repos is dominated by the **shared transit-data layer** (GTFS+STIF→graph/blocks/calendar/
  spatial), then B's **phase machine + lifecycle**, then C's **lattice + historical infra**. The matcher math
  and the prediction blend are the *tractable* parts.
- The clearest improvements over OBA: **HMM/Viterbi with phase folded into the state** (matching), a
  **streaming keyed-state runtime** (ops/scale), and — biggest — **a learned predictor (GBM, later GNN) with
  headway features** replacing the weighted-median blend.
- For the accuracy experiment specifically, the modular high-ROI path is: keep OBA's matcher, route its 5 s
  observed travel times into a GBM predictor, and A/B that against MTA — no rewrite of the hard parts required.

## Sources (web-grounded items)
- Map matching — methods (geometric/topological/HMM), real-time vs offline, history-based inference under GPS
  noise: <https://en.wikipedia.org/wiki/Map_matching>
- Bus bunching / headway degradation mechanism: <https://en.wikipedia.org/wiki/Bus_bunching>

*(ML-prediction methods, commercial-product methodologies, and modern OSS engines in Part 2 are from domain
knowledge pending a cited literature pass — WebSearch/WebFetch were unavailable this session.)*
