# Productionizing the OBA-NYC prediction loop — scoping

**Goal.** Stand up this inference → predictions loop on a standalone server that publishes **GTFS-RT for the
whole MTA bus network** (NYCT + MTA Bus Company, local **and express**), so a third-party observer (us) can
compare our arrival predictions against MTA's official GTFS-RT and measure which is more accurate.

This is a scoping document, not an implementation. It builds on the working local POC in `RUNBOOK.md` +
`mq/README.md`.

---

## 0. The key reframe + the central hypothesis

**MTA Bus Time *is* OneBusAway-NYC.** MTA's official bus GTFS-RT is the output of MTA's production OBA-NYC.
So this is not "our system vs a different system" — it is an **A/B test of prediction configurations running
on the same codebase and the same AVL source** (the BusTech / Cambridge Systematics raw-GPS RabbitMQ feed we
already consume is the same feed MTA ingests).

**Central hypothesis (your note #2):** the BusTech feed delivers a GPS ping **every ~5 s**, but MTA's
published pipeline effectively works off **~30 s** updates. Finer input should yield more accurate inferred
positions and travel times → better predictions, *even at identical prediction weights*.

**Important nuance (confirmed in the code — see §4):** the size of that 5 s-vs-30 s advantage is **coupled to
the weighting** (§3). Under the default **schedule-only** weights (100/0/0), finer GPS only improves the
*near-term* part of a prediction (how far the bus is into its current inter-stop segment); the downstream
stops are still schedule-driven and unaffected. The full value of 5 s pings is realized only once the
**recent/historical** weights are non-zero, because those components are built from the lattice's observed
link travel times, whose accuracy is exactly what finer GPS improves. So the two levers — **ping frequency ×
weighting** — must be tested together, and to *attribute* a difference to ping frequency we should run a
**control arm that down-samples our own feed to 30 s** (see §5).

A second-order effect that's always on: the **inference** particle filter also gets 6× more observations, so
map-matching/positioning converges faster and tracks tighter regardless of weights.

---

## 1. Target architecture (minimize changes to this repo → keep ZeroMQ + broker)

Per your decision #3, keep the native ZeroMQ transport and the existing `onebusaway-nyc-queue-broker`
(`SimpleBroker`) for all *internal* legs. The **only** RabbitMQ touchpoint is the AVL ingestion leg, handled
by the `RabbitMqInputQueueListenerTask` we already built. Everything downstream is the standard OBA-NYC
topology, so new code is minimal.

```
BusTech RabbitMQ (whole-MTA AVL, ~5 s pings)
  │  (RabbitMqInputQueueListenerTask — the one RabbitMQ touchpoint; already built)
  ▼
INFERENCE  (vehicle-tracking-webapp; optionally N instances partitioned by depot)
  │  PUB inferred locations ──► onebusaway-nyc-queue-broker (SimpleBroker, ZeroMQ) ──► SUB
  ▼
PREDICTIONS  (predictions-webapp: lattice → prognosticator; Mongo for history)
  │  PUB GTFS-RT "time" ──► broker ──┬─► loop-back into INFERENCE TDS (TimepointPredictionRecords)  [already wired]
  │                                  └─► (any other consumer)
  ▼
onebusaway-nyc-gtfsrt-webapp  (reads the TDS)  ──►  HTTPS /tripUpdates (+ /vehiclePositions) for ALL routes
                                                          ▲
                       OUR comparison harness polls this + MTA's official GTFS-RT, scores both vs actual arrivals
```

What already exists and is reused (low/no new code):
- `RabbitMqInputQueueListenerTask` (built; selectable via `ie.listener`, config `inference-engine.rabbitmq.*`).
- `onebusaway-nyc-queue-broker` (`SimpleBroker`) for the 556x ZeroMQ fan-in/out legs.
- The **loop-back** (predictions → inference TDS) we already wired.
- `onebusaway-nyc-gtfsrt-lib` + `onebusaway-nyc-gtfsrt-webapp` — the GTFS-RT HTTP producer. `GtfsRealtimeLibrary.id()`
  emits **bare ids** (stop `401689`, route `M15`, trip = bundle trip id) — same namespace as the public GTFS,
  which is what makes a third-party join possible.

New/changed for production is mostly **assembly, data, and ops**, not core algorithm code.

---

## 2. Workstreams (gap from the POC → what's needed)

| # | Workstream | Gap from POC | Effort |
|---|---|---|---|
| **A** | **Whole-MTA bundle (incl. MTABC + Express)** | POC = one hand-built Manhattan B6 bundle | **L** |
| **B** | **Scale & ingestion** | POC = 4 DSCs on a laptop | **M–L** |
| **C** | **Serve the feed + id alignment** | POC emits to a local ZMQ topic only | **S–M** |
| **D** | **Prediction quality (weighting + history)** | POC = schedule-only, empty Mongo | **M** + warm-up time |
| **E** | **Deploy / ops** | POC = detached laptop processes | **M** |
| **F** | **Comparison harness + ground truth (ours)** | not built | **M–L** |

### A. Whole-MTA bundle, including MTA Bus Company + Express (decision #1)
- Need GTFS **and** STIF for **NYCT bus** *and* **MTA Bus Company** (`MTABC` — the operator for the former
  private lines and the **express** routes: BxM/QM/BM/SIM/X). The bundle builder (`GtfsBundles` with multiple
  `GtfsBundle` entries) supports multiple agencies; we'd add one `GtfsBundle` (GTFS zip + `stifPath`) per agency,
  each with its own `NotInServiceDSCs`.
- STIF is proprietary and re-issued per **pick** (~quarterly). Need a recurring **acquire → build → validate →
  hot-swap** pipeline keyed to pick changeovers (the active bundle must cover "today"; `BundleManagementService`
  supports discovery/reassign). This is the largest data-ops item and a recurring obligation, not one-time.
- Multiple agencies mean multiple id namespaces (NYCT vs MTABC stop/route/trip ids) — carry that through to §C/§F.

### B. Scale & ingestion
- Whole network ≈ **~5,800 buses ≈ ~190 msg/s** sustained (higher with 5 s pings); the inference engine keeps a
  **particle-filter instance per vehicle** → thousands concurrent. POC ran a handful.
- Deploy `RabbitMqInputQueueListenerTask` instead of the Python bridge (in-process, TLS, auto-recovery).
- **Scaling model:** either one large vertically-sized inference JVM, or the standard OBA-NYC approach —
  **partition by depot** across N inference instances (`depot.partition.key`), all PUB-ing to the broker, with
  predictions consuming the aggregate. Keeping the broker (decision #3) is what makes this fan-in clean. With
  partitioning we can use real depot assignment instead of the POC's `acceptAllVehicles` bypass, or keep
  accept-all and shard by a vehicle-id hash.
- Size heap/CPU for thousands of filters; the `:5563` listener has no throttle (see §4), so 5 s input is pure
  added load on inference + lattice.

### C. Serve the feed + ID alignment (the comparability linchpin)
- Stand up `onebusaway-nyc-gtfsrt-webapp` against the TDS that the loop-back feeds; expose HTTPS **TripUpdates**
  (and **VehiclePositions** for ground-truth/debugging), ~30 s refresh, cacheable, access-controlled.
- **ID alignment is the gate for a valid comparison.** Stop and route ids already look aligned to the public
  GTFS (bare). The risk is **trip_id**: NYC bus trips are STIF-influenced, and the prognosticator's own raw
  `:5568` feed showed STIF-style trip labels. Serving via the **TDS + gtfsrt-webapp** path (bare GTFS ids)
  rather than the raw prognosticator feed is what should make trip_ids comparable — **but this must be verified**
  (confirm emitted `trip_id` == public-GTFS `trip_id` for both agencies). If it doesn't hold, add a mapping
  layer or drop the comparison granularity to `(route, direction, vehicle, stop)`.
- Confirm whether MTA publishes one combined bus GTFS-RT or per-agency feeds, and match our output structure.

### D. Prediction quality — weighting + history (decision #1 future tuning; details in §3)
- POC is schedule-only (empty Mongo, weights 100/0/0). To beat production we need **recent/historical weighting
  ON**, which requires Mongo **seeded + continuously updated** by the lattice/persistence pipeline, weights that
  **sum to 100** (validation rejects otherwise), and a **warm-up period** before history is usable (cold start
  behaves like schedule-only). See §3 for exactly how to turn this on and what data it needs.

### E. Deploy / ops
- Containerize (systemd/k8s) so services **auto-restart** — this removes the laptop "detached jetty dies when
  the task is reaped" fragility entirely. Config/secrets management (RabbitMQ creds, Mongo), health checks, JVM
  sizing, Mongo persistence + sizing (≤5.0 for driver 2.14, or upgrade the driver), broker process, bundle
  hot-swap without downtime, monitoring/alerting on feed staleness + queue depth + bundle expiry + JVM/GC.
- Replace POC shortcuts: `acceptAllVehicles`, `MethodInvokingFactoryBean` config seeding, `DummyConfigurationService`,
  `MonitoringServiceNoOpImpl`. **However** — prediction weights and many keys normally come from a **TDM**
  (component `"predictions"`, polled every 5 min). A TDM-less deployment must provide those keys another way
  (seed the ConfigurationService as we do locally, stand up a minimal TDM, or use the runtime `setPredictionWeights`
  API). Reconcile the 2.44.39 ↔ predictions-`develop` version skew into pinned builds. Single host = SPOF (fine for an experiment).

### F. Comparison harness + ground truth (ours — decision #2)
See §5. This is a real build, not a config, and the **ground-truth arrival dataset is the hard part**.

---

## 3. How the prediction weighting actually works (your note #1)

Predictions are a **per-link weighted blend** (a "link" = an ordered pair of consecutive stops). For each link
the engine computes a traversal time from up to three components and sums them weighted; arrival times then
**chain forward** stop by stop (`arrival[n] = arrival[n-1] + predictedLinkTravelTime[n]`; the first, in-progress
link is interpolated from the live position).

### The three components (each is a *traversal time* for the link, in ms)
- **SCHEDULE** — the scheduled duration over the link (tail scheduled departure − head scheduled departure).
  Always present; acts as the **fallback/filler**.
- **RECENT** — median of the **last N observed traversals** of that exact stop-pair (default **N=5**), in-memory,
  not time-of-day or route segmented, lost on restart.
- **HISTORICAL** — median observed traversal for that link keyed by **(route, head, tail, time-of-day bucket,
  schedule type)**, read from Mongo via an in-memory cache.

### The blend (`PredictionsCalculatorServiceImpl`)
Weighted sum: `predictedLinkTime = Σ weight_i × componentTime_i`. **Schedule absorbs any leftover percentage**,
including when recent/historical data is *missing* for a link. So:
- Weights **100/0/0** → pure schedule.
- If you set, say, **40/30/30** but a link has no historical record yet, that 30% silently reverts to schedule
  for that link. Cold start ≈ schedule-only until data accumulates.

### Where weights live + how to change them
Config component `"predictions"`, keys (defaults): `predictions.componentWeightSchedule` (**100**),
`predictions.componentWeightRecent` (**0**), `predictions.componentWeightHistorical` (**0**)
— read in `PredictionsWeightsServiceImpl` (`@Refreshable`, hot-reload). **Validation requires the three to sum
to exactly 100**, else it reverts to 100/0/0. Normally sourced from the **TDM**; in a TDM-less deploy, seed
these keys the way we seed the others (or use the `setPredictionWeights` API).

### Where the data comes from (lattice → caches/Mongo)
- The **lattice** (`ArrivalDepartureTimes*`) turns the inferred-position stream into **observed stop
  arrival/departure times** and per-link traversal times by **constant-speed interpolation between consecutive
  position reports**, using spatial `delta`(before)/`epsilon`(after) windows per stop.
- Each validated link traversal is fed to: the **recent** ring buffer (in-memory, last 5) **and** Mongo
  **`AggregateLinkTimes`** — one doc per `{route, head, tail, timeOfDay, scheduleType}` holding the last
  **300** traversals (`$slice`) and a precomputed median. A separate `LinkTravelTimeRecord` collection archives
  every individual observation.
- At prediction time the prognosticator reads in-memory caches, not Mongo directly: a `HistoricalCacheUpdater`
  pre-loads the next **4 hours** of (time-of-day, schedule-type) buckets (only **2 h** at startup) and rotates
  them as the day advances.

### Tuning knobs (all config component `"predictions"`)
- Weights: `componentWeight{Schedule,Recent,Historical}` (must total 100).
- Data depth: `recentComponentRecordCount` (5), `historicalComponentRecordCount` (300),
  `prognosticatorHistoricalCacheSize` (4 h), `minuteIntervalsInDay` (60 → hourly buckets; smaller = finer
  buckets but needs more data to fill each).
- Lattice quality/staleness: `latticeOldDataThreshold` (180 s), `latticeResetVehicleIfDistanceExceeded` (1000 m),
  stop windows `beforeStopDeltaThreshold` (50 m) / `afterStopEpsilonThreshold` (25 m), etc.
- Prediction horizon: `PredictionLevel` (`CURRENT_TRIP` default, or `LAYOVER`/`NEXT_TRIP`).

### Warm-up implication
`historical > 0` does nothing for a link until its `(tod, scheduleType)` bucket is populated **and** loaded in
the ≤4 h cache window; `recent > 0` needs ≥1 prior observation of that stop-pair since process start. Plan for
**days-to-weeks** of data collection (start the lattice persisting early, before flipping weights) so the
history is meaningful across times-of-day and weekday/weekend schedule types.

---

## 4. The 5 s-vs-30 s effect, mechanically (your note #2)

Confirmed in code: **there is no time-based sampling, throttle, or near-duplicate dedupe** anywhere in the
predictions pipeline — every inferred location is processed (only bounded-queue overflow drops anything). So
finer GPS flows straight through. Its effect:

- **Lattice interpolation** assumes **constant speed between two consecutive position reports**. Shorter gaps
  (5 s vs 30 s) span less distance, so the back-computed instant the bus crossed each stop is more accurate when
  speed actually varies → **more accurate observed link travel times**.
- More updates → more chances to catch a bus **inside** a stop's `[delta, epsilon]` window, giving
  **non-interpolated, exact-timestamp** arrivals.
- Better observed link times → better **recent** and **historical** medians.

But (the nuance from §0): under **schedule-only** weights the downstream predicted link times come from the
*schedule*, so finer GPS only sharpens the **first (in-progress) link** interpolation and the inference
position — not the rest of the trip. **The compounding benefit needs recent/historical weights > 0.**

**To isolate ping-frequency from algorithm/weights**, run arms:
1. Ours @ 5 s, weights = best estimate of MTA's (or schedule-only) — vs MTA official.
2. **Control:** ours @ **30 s** (down-sample the bridge/listener input) — same weights. Arm 1 − Arm 2 ≈ the
   pure value of 5 s pings.
3. Ours @ 5 s, weights with history ON — the "tuned" upside.
(Down-sampling to 30 s is a small bridge/listener change — e.g. forward at most one fix per vehicle per 30 s.)

---

## 5. The comparison methodology (we own scoring — decision #2)

We build and run the scoring; we don't just expose a feed. Components:

**Collection.** A poller fetches **our** GTFS-RT and **MTA's official** GTFS-RT TripUpdates on a fixed cadence
(e.g. every 15–30 s) and persists timestamped snapshots. Also persist VehiclePositions / the raw AVL for ground
truth.

**Ground truth (the hard part).** "Accuracy" needs **actual** arrival times. Two viable sources, applied
**identically to both feeds** for fairness:
- *Independent detector* (preferred for neutrality): detect each vehicle's actual stop crossings from the 5 s
  AVL (position passes within a threshold of a stop). A few hundred lines; no dependence on either prediction
  engine.
- *Reuse the lattice's observed arrivals* (`ArrivalDepartureTimes`) as ground truth — cheaper (already computed)
  and fair if applied to both feeds, but uses our system's interpolation; document the choice.

**Metrics.** For every (stop, trip/vehicle) and each snapshot, error = `predicted_arrival − actual_arrival`.
Report **MAE / RMSE** and **% within ±1/3/5 min**, bucketed by **prediction horizon** (e.g. 1/3/5/10 min before
actual), **route**, **agency** (NYCT vs MTABC vs express), and **time-of-day**. Compare our distribution vs MTA's
on the **matched set** of (stop, trip/vehicle, horizon) tuples present in both feeds (coverage-normalized);
handle added/cancelled trips and stops present in one feed only.

**Join key.** Depends on §C trip_id parity. Best case: join on `(trip_id, stop_id)`. Fallback:
`(route_id, direction, vehicle_id, stop_id)`. Vehicle id parity should hold (both derive from the same BusTech
vehicle ids).

**Fairness checklist.** Same AVL source (✓, both BusTech). Same bundle/GTFS version for the id join. Same ground
truth for both feeds. Note confounders: MTA production has full UTS/depot context; our accept-all path may
map-match some vehicles differently. The 30 s control arm (§4) is what cleanly isolates ping frequency.

---

## 6. Critical path & phasing

1. **Whole-MTA bundle incl. MTABC + Express** (A) + STIF pipeline — longest pole; unblocks everything.
2. **Scale ingestion** (B) — deploy the native RabbitMQ listener + broker; size/partition for full volume.
3. **Serve + prove trip_id parity** (C) — gtfsrt-webapp up; **verify id alignment** (gate for a valid comparison).
4. **Start lattice persistence early** (D) so the **warm-up clock runs in parallel** with everything else.
5. **Comparison harness + ground truth** (F) — can be built against the POC feed before full scale.
6. **Flip on recent/historical weights** (D) once history is warm; run the arms in §4.
7. **Ops hardening** (E) throughout.

## 7. Top risks / unknowns
- **trip_id parity** with public GTFS (both agencies) — de-risk first; without it the comparison needs a mapping
  or coarser granularity.
- **Ground-truth arrivals** — no credible accuracy claim without it; it's a build.
- **Warm-up latency** — weeks of data before historical weighting can plausibly win.
- **Full-volume stability** — thousands of particle filters + 5 s pings; needs load testing + likely depot partitioning.
- **STIF pipeline** for two agencies, recurring per pick.
- **MTA's actual config/ping cadence is unknown** — hence the down-sampled control arm to isolate ping frequency.

## 8. Decisions locked + still open
**Locked (this session):**
1. Scope = whole MTA incl. **MTABC + Express**.
2. **We own** the scoring/ground-truth harness.
3. **Keep ZeroMQ + broker** internally; RabbitMQ only at the AVL ingestion leg (minimize repo changes).
4. Sustained whole-feed AVL consumption + publicly hosting GTFS-RT is **cleared**.

**Still worth deciding later:**
- One large inference JVM vs depot-partitioned instances (drives B effort + whether to drop `acceptAllVehicles`).
- TDM-less config (seed keys) vs stand up a minimal TDM (cleaner for weight tuning + bundle management).
- Independent ground-truth detector vs reusing the lattice's observed arrivals.
- Whether to serve one combined bus feed or per-agency feeds (match MTA's structure).
