# Defects in production OBA

Found between 2026-08-07 and 2026-08-11 while building the replay harness, all in code that predates
that work.

Scope is deliberately narrow: everything here affects **production**. Defects that only matter when you
need repeatable output, or need "now" to mean something other than the wall clock, are in
[DETERMINISM.md](determinism/DETERMINISM.md) instead.

Each entry states how strongly it is established:

- **Measured** — a run demonstrates it.
- **Read** — established by reading the code. No run needed, but no run performed either.
- **Hypothesis** — plausible from the code, not yet checked. Treat as a question, not a finding.

"Fixed" means the fix is in our replay branch. "Reverted" means we fixed it, confirmed the fix, then
backed it out to keep parity with production OBA.

---

## 1. Every thread draws from one random generator, unsynchronised

**Measured. Fixed.** `ParticleFactoryImpl.LocalRandomDummy` holds its `MRG32k3a` in a **static** field,
so every instance and every thread returns the same generator, despite the class extending
`ThreadLocal`:

```java
static class LocalRandomDummy extends ThreadLocal<RandomStream> {
  private static MRG32k3a rng;          // static
  @Override public RandomStream get() { return rng; }
}
```

`MRG32k3a` keeps its generator state in two mutable instance arrays, `double[] Bg` and `double[] Ig`,
and `RandomStreamBase.nextDouble()` is **not synchronized**. So a draw is an unsynchronised
read-modify-write on state shared by every inference thread. Two threads can advance the state
concurrently and receive the same value, or interleave partial updates and leave it inconsistent.

This is the highest-volume path in the engine — roughly 200 particles × 176 candidates, about 35,000
draws per record — so at fleet scale every thread is hitting one unguarded object constantly.

Beyond the race, the vehicles are also statistically coupled. A particle filter's mathematics assumes
each vehicle's estimate is independent; drawing every vehicle's samples from one stream means the fleet
walks one interleaved sequence rather than thousands of independent ones. How much that degrades the
estimates is not something we measured.

## 2. `computeBestState` compares partial sums against a running maximum

**Read. Fixed, then reverted for parity.** `ParticleFilter.computeBestState` accumulates each trip and
phase group's probability with `adjustOrPutValue` and compares the **partial** sum against the best seen
so far, inside the same loop. Two consequences:

- a group can lose to a rival whose particles happened to be visited earlier, even when its own
  completed total is higher;
- an exact tie goes to whichever group was reached first.

Exact ties are common here, not hypothetical: without UTS run data three of the four trip-selection
likelihoods return a constant, so adjacent departures on one route routinely score identically. This is
a plausible contributor to the reported 1-2% same-route, different-trip disagreement against production.

The fix is to compare completed totals and break ties by a total order over the ids.

## 3. `Double.MIN_VALUE` used as a negative sentinel

**Read. Fixed, then reverted for parity.** Same method:

```java
double highestTripProb = Double.MIN_VALUE;
```

`Double.MIN_VALUE` is the smallest **positive** double, about 4.9e-324, not the most negative. The
intended sentinel is `Double.NEGATIVE_INFINITY`. Masked today because the `bestId == null` guard handles
the first iteration and the accumulated weights are non-negative, so it is latent rather than active.

## 4. `compareTo` is coarser than `hashCode`, and a `TreeSet` silently discards the difference

**Read; consequence unverified.** Several classes compare on fewer fields than they hash:

| Class | In `hashCode` but not `compareTo` |
| --- | --- |
| `BlockStateObservation` | `_obs`, `_isAtPotentialLayoverSpot`, `_isSnapped`, `_scheduleDeviation` |
| `MotionState` | `vehicleHasNotMoved` |
| `ScheduledBlockLocation` (via its comparator) | `distanceAlongBlock`, `orientation`, `location`, stop offsets, `inService`, `isSpooky` |

So two objects can be unequal, hash differently, and still compare 0.

`computeBestState` then does `bestParticles.addAll(...)` into a `TreeSet<Particle>` and takes `.first()`.
A `TreeSet` treats `compareTo == 0` as duplicate, so of several particles that compare equal **only the
first inserted is kept**.

The concrete risk: `_scheduleDeviation` is excluded from `compareTo` and *is* a published field. Two
particles differing only in schedule deviation compare 0, one is silently discarded, and the published
value depends on which arrived first. Whether such a pair actually occurs is unmeasured — counting them
per record would settle it.

## 5. `ScheduledBlockLocation` defines neither `hashCode` nor `equals`

**Read; consequence is a hypothesis.** It is a mutable bean with 14 setters and no `equals`, so equality
is reference identity. Candidate sets can therefore hold two entries that describe the same block
position, and their probability is counted twice.

Whether value-duplicates actually occur is **not measured**. Counting candidates per record that share
`(blockInstance, distanceAlongBlock, phase)` would settle it in about twenty minutes.

Note that `BlockState.hashCode` deliberately avoids calling `blockLocation.hashCode()`, pulling out
`getScheduledTime()` and the trip's `AgencyAndId` instead, with the comment *"don't want/need to change
OBA api, so do this..."*. Someone already routed around the missing method.

## 6. A shared thread pool does not preserve a vehicle's own record order

**Measured. Fixed** in an earlier commit. Records were submitted to one pool sized `2 + cores * 20`, so a
vehicle's records could be processed out of arrival order. `isValidRecord` rejects a record older than
the last one seen for that vehicle, so whenever record N+1 was processed before record N, record N was
discarded.

Replay made this dramatic — 255 of 279 records dropped — because a replay dispatches a vehicle's whole
window in under two seconds, so its records are all in flight at once. Production feeds arrive about five
seconds apart per vehicle, far longer than it takes to process one, so the window for reordering is
normally closed. It opens under backlog: any time the queue builds up and several of a vehicle's records
are dispatched together, records are silently discarded. There is no counter for it, so the loss would
not be visible.

Fixed by pinning each vehicle to a single-thread executor chosen by a hash of the vehicle id.

The pool size is worth a second look independently: `2 + cores * 20` is 202 threads on a 10-core machine
for CPU-bound work.

## 7. An operator-assignment cache miss fetches over the network inside a global lock

**Measured** (in a variant of this class — see the note at the end).
`OperatorAssignmentServiceImpl:239-252`, the TDM implementation production runs:

```java
list = _serviceDateToOperatorListMap.get(serviceDate);   // miss
if (list != null) return list.values();
synchronized (this._serviceDateToOperatorListMap) {      // :242  one lock, shared by all threads
  list = _serviceDateToOperatorListMap.get(serviceDate); // double-check, still miss
  list = getOperatorMapForServiceDate(serviceDate);      // :247  HTTP call to the TDM, inside the lock
  if (list == null)
    throw new Exception("Operator service is temporarily not available.");
  _serviceDateToOperatorListMap.put(serviceDate, list);
```

**This costs nothing in normal running.** A miss populates the map, and every later lookup returns at
`:239` without taking the lock. Service dates are few, so misses are rare.

It matters only when the fetch fails, and then it amplifies badly. A failure caches nothing, so the next
lookup repeats the whole sequence — there is no memory that the fetch just failed. Since every record's
operator lookup goes through this path, and the network call is made while holding a lock every thread
needs, an unreachable TDM stops being a slow dependency and becomes an engine-wide stall.

So this is a failure-isolation defect, not a throughput one. The exceptional case is exceptional; the
problem is that the engine handles it far worse than it needs to.

Two changes fix it independently: move the fetch outside the monitor and take the lock only to publish
the result, so one thread waits instead of all of them; and record a failure timestamp with a cooldown,
so a persistent failure is retried periodically rather than on every lookup.

**How this was measured.** Our own instance runs a different implementation,
`S3UtsOperatorAssignmentServiceImpl`, which Timothy added to read the roster from S3 instead of calling
the TDM API. Which one is used is chosen by `oba.crew.service.class`; the TDM class is the default and
the S3 class is a profile override. The S3 variant has the same lock-around-I/O shape, and with S3
returning Access Denied a 5,585-record replay produced 3,440 failed fetches and held the inference
threads at 28% CPU on a 10-core machine — queued on the monitor rather than computing. The measurement is
from the S3 variant; the defect above is in the production one.

---

## Not defects, but worth knowing

**Candidate sets legitimately contain `null`.** `MotionModelImpl` adds a possibly-null
`newParentBlockStateObs` to `transitions`, and `ParticleFactoryImpl` guards `if (blockState != null)`. Any
code iterating a candidate set must tolerate nulls.

**Device clocks on the buses are not trustworthy.** Measured over one 5-minute bucket, the gap between a
record's ingest timestamp and the device's own reported time has a p99 of 11 seconds and a maximum of
about 19.6 years. `RecordLibrary.getBestTimestamp` already falls back to the received time when the two
are more than 30 minutes apart, which is why this is listed here rather than as a defect — but any new
code that reads device time needs the same guard.
