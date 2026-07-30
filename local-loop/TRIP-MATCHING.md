# How OBA-NYC matches a vehicle to a trip

A reference for the inference engine's core question: *given a GPS ping, which trip is this bus running, and
where on it?* All code is in module **`onebusaway-nyc-vehicle-tracking`**, package
`org.onebusaway.nyc.vehicle_tracking.impl.inference` (and subpackages). Paths below abbreviate that as `…/impl/`.
Line numbers are approximate (this branch, 2.44.39) — use them as navigation hints.

> **The big idea:** there is no "trip matcher" function and no lookup table. Matching is a
> **Rao-Blackwellized particle filter**. It keeps ~200 competing hypotheses about what the bus is doing,
> scores each against every ping with a product of probabilistic rules, and resamples over time. The "matched
> trip" is simply the active trip on the most-probable surviving hypothesis. It is an inference *output*, and it
> sharpens as more pings arrive.

---

## The signals in one ping

Before any matching, each `NycRawLocationRecord` is turned into an `Observation` (`…/impl/Observation.java`)
carrying **three independent routing signals** plus context:

- **Position** — the projected lat/lon.
- **DSC** (destination sign code) — cleaned, plus the set of routes it implies (from the STIF sign-code map).
- **Run / operator** — operator id → assigned run (`OperatorAssignmentService`); reported run id → fuzzy
  matches (`RunService`); each expands to candidate routes/trips.
- Plus: any operator-**assigned block**, `atBase`/`outOfService`/`hasValidDsc` flags, the previous observation,
  and derived `timeDelta`/`orientation`/`distanceMoved`.

Entry point: `VehicleInferenceInstance.handleUpdate(record)` → `ParticleFilter.updateFilter(timestamp, obs)`
(`…/impl/inference/VehicleInferenceInstance.java`, `…/impl/particlefilter/ParticleFilter.java`).

---

## Step 1 — Propose candidate hypotheses

`BlocksFromObservationServiceImpl.determinePotentialBlockStatesForObservation(obs)` builds the candidate set
(a **block** = the full day's ordered sequence of trips one vehicle-run operates, on a specific service date).
It unions three sources (plus a `null` "not in service" candidate):

1. **DSC → trips → blocks.** `DestinationSignCodeService.getTripIdsForDestinationSignCode(dsc, agency)` → each
   trip's block, kept if active in roughly **[time − 30 min, time + 60 min]**. (Cancelled trips skipped.)
2. **Run → trips → blocks.** Trips for the operator-assigned run and every fuzzy run match.
3. **Spatial snap.** `BlockStateService` holds a JTS `STRtree` of every shape segment. It snaps the GPS point to
   any route geometry **within 60 m** (`_tripSearchRadius`), converts the snapped point to a
   *distance-along-block*, rejects opposite-direction matches (orientation diff ≥ 135°), and keeps the closest
   location per (trip, direction).

DSC/run also **filter** the spatial snaps: a snapped block-trip is dropped if its route isn't in the
observation's DSC-implied routes (`_requireDSCImpliedRoutes`, default on); when the DSC is invalid, snapping is
restricted to op-assigned/fuzzy runs only.

Each candidate becomes a `VehicleState` = `{ BlockStateObservation (block + ScheduledBlockLocation), JourneyState
(phase), MotionState, Observation }` (`…/impl/inference/state/`).

---

## Step 2 — Score each hypothesis (the sensor model)

Each hypothesis's weight is a **product of independent likelihood terms** (log-probabilities summed;
`SensorModelResult.addResultAsAnd`). Every term is a `@Component implements SensorModelRule` in
`…/impl/inference/likelihood/`:

| Rule | What it rewards / penalizes |
|---|---|
| `GpsLikelihood` | closeness of the hypothesis's on-route position to the actual GPS point (folded-normal, σ ≈ 22 m). Pulls the match onto the road. |
| `EdgeLikelihood` | moved the **right distance in the right heading** along the shape since last ping (Gaussian on Δ-distance + Von Mises on bearing vs. edge orientation). |
| `ScheduleLikelihood` | plausibility of the implied **schedule deviation** (Student-T; a tight distribution if the run is "formal"/assigned, looser otherwise; hard-truncated outside ≈[−30, +75] min). |
| `DscLikelihood` | **DSC ↔ trip consistency, with hard constraints.** In-progress with an out-of-service sign → prob **0**; sign whose route doesn't match the trip → **0**; exact DSC match → strongly rewarded. |
| `RunLikelihood` | run match: op-assigned > fuzzy match > no run info. |
| `RunTransitionLikelihood` | penalizes implausible run changes between consecutive states. |
| `BlockLikelihood` | hypothesis's block matches the operator-assigned block (if any). |
| `MovedLikelihood` / `NullStateLikelihood` / `NullLocationLikelihood` | consistency of the moved/not-moved latent; priors favoring "has a block" and on-route locations. |

**Why DSC dominates routing:** a position often snaps to several overlapping routes, but `DscLikelihood`'s hard
zeros eliminate every hypothesis whose route the sign doesn't serve. The DSC is the strongest disambiguator;
position + heading then pick *where on* the surviving trip and *which direction*.

This same rule set scores both initialization (`ParticleFactoryImpl`, 200 particles via a weighted
`CategoricalDist`) and transitions (`MotionModelImpl.move`, which also decides which candidates each parent may
transition to — snapped-only vs. the full DSC/run set when the DSC changes or the sample diversity drops).

---

## Step 3 — Filter over time (why a sequence matters)

`ParticleFilter.runSingleTimeStep` moves every particle, re-weights, and **resamples** (low-variance sampler)
when effective sample size drops below 0.75 × N. Bad hypotheses die; good ones multiply.

- A **single** fix is ambiguous; trip identity **converges as pings accumulate** — which is exactly why
  injecting a *sequence* of fixes is required to get an `IN_PROGRESS` match (see `RUNBOOK.md` §6).
- This is the crux of the **finer-sampling** accuracy hypothesis (see `PRODUCTIONIZING.md`; note the realised advantage is **~2.4× end-to-end, not 6×** — `FINDINGS-SUMMARY.md` §1a): more observations
  → faster convergence and a tighter position estimate → both *which* trip and *where on it* are sharper, and
  the lattice's observed travel times (which feed predictions) are more accurate.

---

## Step 4 — Read off the matched trip

Each timestep `ParticleFilter.computeBestState` does **not** just take the single highest-weight particle.
It buckets particles by `(active trip, phase)`, sums weight per bucket, picks the **most-probable bucket**, then
the top particle within it — a marginalized choice that's more stable than raw argmax.

The matched trip is then the **active trip at that particle's inferred distance-along-block**:

```
blockState.getBlockLocation().getActiveTrip().getTrip().getId()
   → NycQueuedInferredLocationBean.inferredTripId
```

(`VehicleInferenceInstance.getMostRecentParticleAsNycTestInferredLocationRecord` and
`getCurrentStateAsNycQueuedInferredLocationBean`.) Schedule deviation is reported only when the run is "formal";
distance-along-trip = `distanceAlongBlock − activeTrip.getDistanceAlongBlock()`.

---

## The phase qualifier — matched ≠ in service

A bus can match a block without actively *serving* a trip. The **journey phase**
(`JourneyStateTransitionModel.getJourneyState`, enum `EVehiclePhase`) decides:

- **`IN_PROGRESS`** — on a trip, inside the block, moving in service, valid (not out-of-service) DSC, not a
  detour. **This is "matched to a trip"** and is the only phase that flows downstream into the prognosticator as
  a real arrival prediction. (`isLocationOnATrip`: `tripDistFrom < distanceAlongBlock ≤ tripDistFrom + tripDistance`.)
- **`DEADHEAD_BEFORE/DURING/AFTER`** — repositioning with no passengers: pulling out, between trips,
  wrong-direction, terminal, or out-of-service DSC.
- **`LAYOVER_BEFORE/DURING/AFTER`** — stopped ≥ 120 s at/near a terminal/layover spot (within 250 m of a
  terminal stop).
- **`AT_BASE`** — at a depot.

So a `DEADHEAD` result (e.g. from coarse/teleporting inject data) means the filter *can* place the bus on a
block, but its motion / DSC / position don't look like active service.

---

## One-line summary

Observation (GPS + DSC + run) → candidate `(block, position)` hypotheses from DSC→trips ∪ run→trips ∪ 60 m
spatial snap → each scored by a product of likelihoods (with DSC/route as **hard constraints**) → particle
filter resamples over successive pings → the active trip on the most-probable `(trip, phase)` hypothesis is the
match, reported as `inferredTripId` when the phase is `IN_PROGRESS`.

---

## Key files
- `…/impl/inference/VehicleInferenceInstance.java` — per-vehicle driver; builds the observation; extracts results.
- `…/impl/inference/Observation.java`, `RunResults.java` — the observation + run-match results.
- `…/impl/inference/BlocksFromObservationServiceImpl.java` — candidate generation (DSC ∪ run ∪ snap).
- `…/impl/inference/BlockStateService.java` — spatial index + 60 m snapping → distance-along-block.
- `…/impl/inference/BlockStateSamplingStrategyImpl.java` — proposal distributions (schedule / transition).
- `…/impl/inference/likelihood/*.java` — the sensor-model rules (Gps/Edge/Schedule/Dsc/Run/Block/…).
- `…/impl/inference/MotionModelImpl.java`, `ParticleFactoryImpl.java` — transition + initialization scoring.
- `…/impl/inference/JourneyStateTransitionModel.java`, `state/JourneyState.java` — phase assignment.
- `…/impl/particlefilter/ParticleFilter.java`, `Particle.java` — the filter + best-state selection.
- `…/impl/inference/state/{VehicleState,BlockState,BlockStateObservation,ScheduledBlockLocation*}.java` — state.
- `onebusaway-nyc-transit-data-federation/.../nyc/DestinationSignCodeService.java` — DSC ↔ trip/route (from STIF).
