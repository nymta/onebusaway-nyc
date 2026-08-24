# Why multi-threaded determinism was hard in OBA

Replay compares two runs over the same archived data. That only means something if identical input
gives identical output. Single-threaded, it did. With four threads, it did not - until the last cause,
a mutable static field in `ScheduleLikelihood`, was found and fixed (see "What is settled" at the
bottom). The investigation is kept in full below because the causes were not concurrency bugs in the
usual sense, and the search itself is the useful part to remember.

This explains what made that hard and answers the obvious "why not just…" questions. It assumes no
prior knowledge of the inference engine.

---

## What the engine does, in one paragraph

The engine cannot know which trip a bus is running, so it guesses and keeps many guesses. For each bus
it holds 200 **particles** — 200 complete hypotheses, each naming a block, a trip, a position along
that trip, and a phase such as "in progress" or "deadheading". Each GPS record triggers a cycle: list
the candidate positions the bus could be at, score them, then draw 200 new particles at random,
weighted by those scores. The published answer comes from the winning particle.

---

## 1. The bar is high, the traps are scattered, and one slip is unrecoverable

**Determinism here is stricter than "computes the same values".** The engine is a randomised algorithm,
so it is deterministic only if **every random number reaches the same decision**. A draw is consumed by
a specific comparison at a specific point. Two runs can both be arithmetically correct, consume the same
numbers, and still disagree — if those numbers arrive at different comparisons. The sequence of
consumption is part of the answer.

**The things that break it do not look like concurrency bugs.** The ones actually found, none of which
resemble the others:

- Iteration order of ordinary `HashSet`s and `HashMap`s depended on memory addresses, because three
  classes on the path had no value-based `hashCode`. Two were upstream transit-graph classes; the third
  was `java.lang.Enum`, whose `hashCode` is `final` and address-derived, folded into `VehicleState` and
  `JourneyState`.
- Every bus drew from one shared random generator, so a draw taken for bus A consumed a number bus B
  would have used.
- `Collections.sort` is stable, so a sort added to impose order silently preserves the original order
  wherever its comparator returns 0 — and several comparators here compare on fewer fields than the
  matching `hashCode`.
- A lock placed around a network call, rather than around the state update it protected, serialised the
  entire engine.

There is no single place to look and no category of code to audit. Two were in a library, one was in the
JDK, one was in a sort that appeared to solve the problem it did not solve.

**One nondeterministic event is not a small error — it is total.** Several places take a draw only on
some branches:

- `BlockStateSamplingStrategyImpl:117` sits in an `else`; the branch above returns early when the parent
  particle's phase is `DEADHEAD_AFTER`, or `DEADHEAD_BEFORE`/`LAYOVER_BEFORE` with zero schedule
  deviation.
- `CategoricalDist.sample()` returns without drawing when the distribution has exactly one entry.
- `Random.nextGaussian()` uses rejection sampling, so one call consumes a variable number of underlying
  values.

So a single difference changes how many draws are consumed, the streams **desynchronise**, and every
later draw differs. Because each record's outcome also feeds the next record for that bus, the
divergence compounds instead of averaging out. In the current failure, 3 records' worth of state
produces 91 differing records out of 266.

**Nothing detects it.** Determinism was never a goal of this codebase, so no test asserts it and no
build step notices when it breaks. It is observable only by running the same input twice and comparing —
which means every hypothesis costs a build and two runs, and the symptom always appears far from the
cause. Instrumentation is the only way to see the event, and instrumentation changes the timing it is
trying to observe.

## 2. The work is finding every piece of shared state — and that search does not converge

The task itself is clear enough. Anything two threads touch must be either not shared, immutable, or
consumed in a way that does not depend on the order it was touched. Find all of it, fix each one, done.

Nothing about that is impossible. Every individual question has a definite answer, and each answer is
cheap to establish once you know to ask. The problem is that answering them does not visibly reduce
what is left.

### What has been checked and cleared

Established by reading the code:

| Candidate | Why it is not the cause |
| --- | --- |
| `ObservationCache` | Per-vehicle `LoadingCache`; mutations under `synchronized`, and a bus only runs on one stripe |
| JTS `STRtree` lazy build | `AbstractSTRtree.build()` is `synchronized` in 1.16.1 — no double build, no corruption |
| `BlockStateService`'s two maps | Written only inside `buildShapeSpatialIndex()` under `@PostConstruct`; read-only afterwards |
| `BlockStateSamplingStrategyImpl` | Stateless — two injected collaborators and one `static final` constant |
| `ExtendedCalendarServiceImpl` | Its map is startup-only; the rest goes through ehcache |
| `ScheduleLikelihood`'s static distributions | `StudentTDistribution` instances are read-only after construction; `sample()` only reads three doubles |
| ~~`ScheduleLikelihood`, fully~~ | **Wrong.** The distributions were cleared, but `POS_SCHED_DEV_CUTOFF` in the same class was a mutable `private static double`, written from each particle's own trip duration and read back a few lines later - every stripe hit the same field. This was the remaining cause. Fixed by making it a local variable, threaded through as a parameter instead of a shared field. |
| `RunServiceImpl`, `DestinationSignCodeServiceImpl` | No plain collection fields at all |
| `ShapePointsLibrary`, `VehicleStateLibrary`, `BlockCalendarServiceImpl`, `BaseLocationServiceImpl` | No mutable state beyond configuration |
| Every non-final static on the path | Configuration scalars, plus dead legacy RNG fields nothing reads |

Established by running two instrumented replays and comparing:

| Candidate | Measurement |
| --- | --- |
| The shared replay clock | `timeReceived` byte-identical on all 270 records |
| Record ordering within a bus | Identical sequence in both runs |
| `isValidRecord`'s three checks | 270 records reached `handleUpdate`; nothing dropped |
| `handleUpdate`'s early returns | None fired in either run |
| The crash-and-retry path | Zero occurrences |
| Candidate generation | Candidate counts identical where compared — 40/40, 9/9, 51/51 |
| `ParticleFilter.reset()` | Never called |
| All five callers of `resetVehicleLocation` | Bundle-change check never ran, load-shedding off, no exceptions |

Two causes were found this way and fixed — the shared random generator, and three classes whose
`hashCode` was address-derived. Two more were found that are real defects but do not explain the
divergence: static `DateFormat`/`NumberFormat` in `JourneyPhaseSummary`, used only in `toString()`, and
six accessors on `VehicleInferenceInstance` that read instance state without holding its monitor.

The divergence is still there. Roughly 3 records in 270.

### Why the list keeps growing instead of shrinking

**There is no completion criterion.** A thorough sweep that finds nothing and an incomplete sweep look
exactly alike. The only evidence that the search is finished is that two runs agree — which is the thing
being investigated, so it cannot be used as a check partway through.

**Two of the three causes found were not shared mutable state.** An audit framed as "find the shared
mutable fields" would have found one of them. Address-derived `hashCode`s made the JVM's memory
allocator the shared state; no field anywhere holds it. Stable-sort ties involve nothing shared and
nothing mutated — the consumer is order-sensitive. So the obvious search strategy has a known blind
spot, and the shape of the next cause is unknown by definition.

**Locking is not the fix.** Nine of `VehicleInferenceInstance`'s methods are already `synchronized` and
it does not help. A lock stops two threads corrupting state; it does not decide which arrives first, and
here the order is the answer. What has actually worked is partitioning the state — per-vehicle random
streams, one stripe per bus — and removing order sensitivity, by making hashes content-derived. Those
are design changes, not audit findings, and there is no list of them to work through.

### And a cycle can invalidate the cycles before it

The clearest illustration cost a day's results.

Our instance reads crew assignments from S3 rather than calling the TDM API, and with S3 access failing
that class made a network call inside a lock every thread needed, on every record. The four stripes ran
one at a time. The engine was configured for four threads, reported four threads, and was not
concurrent.

Nothing indicated it. Slowness is expected in a replay. The determinism comparisons passed, repeatedly,
and read as evidence that concurrent replay was reproducible — the 36 collection changes were removed on
that basis, and the conclusion written up. They were evidence of nothing, because the concurrency was not
happening. It surfaced only because disabling the fetch (`-Doba.crew.disabled=true`) produced a 20×
speed-up, 65 seconds to 3.1 seconds, which was too large to attribute to anything else. Divergence
appeared in the same run.

So a passing determinism test is worth only as much as the evidence that the threads were actually
running — and there is no natural symptom that distinguishes "concurrent and correct" from "not
concurrent at all". The lock itself is a plain bug, in [OBA-BUGS.md](../OBA-BUGS.md) along with the same
shape in the production TDM implementation.

### What this costs per attempt

A build and install, then two runs, then a comparison. Usually a new probe first, because the engine's
own accounting cannot distinguish "record dropped", "record produced no output" and "record produced
different output". And instrumentation participates in what it measures: one probe threw inside the
engine and altered the run it was watching; another sorted its output and so could not answer a question
about order.

None of that makes the bug unfindable. It makes each attempt cost enough, and eliminate little enough,
that the search feels like it has no end — which is the honest reason to stop and shard instead.

---

## "Why can't you just…"

### …run it single-threaded?

This is the strongest option, and it works — single-threaded replay is verified deterministic.

The cost is throughput. At roughly 63 records per second, one 5-minute bucket of 266,112 records takes
about 70 minutes per run, and determinism needs two runs. A day of data is 288 buckets, so about two
weeks on one thread.

**But it does not have to be one process.** Because each bus has its own random stream keyed on its
vehicle id, a bus's result does not depend on how the fleet is divided. The fleet can be split across
many single-threaded processes and the combined output matches a single-process run. Twenty processes
bring a day of data to under a day of wall time, and they can be spread across machines.

That is the practical path: single-threaded per process, parallelism by sharding. It sidesteps
in-process concurrency rather than trying to make it deterministic.

Treat 63 records per second as an order of magnitude. It comes from a 270-record fixture holding 5
vehicles; a real bucket has 4,939, which means about a thousand times as many particle filters live at
once, and the per-record cost under that memory pressure is unmeasured. Good enough to rule out
single-process replay and to size the sharding, not to plan a schedule.

### …just seed the random number generator?

Necessary, and already done — but not sufficient. A fixed starting point consumed in a varying order
still produces varying results. Seeding fixes which numbers exist, not which number reaches which
decision.

### …add locks to make it thread-safe?

Thread safety and determinism are different goals. A lock stops two threads corrupting shared state. It
does not fix which thread goes first, and here the order is what matters. Two threads can take a lock in
either order, both be correct, and produce different output.

The UTS lock is also a caution about cost: a lock around I/O rather than around a state update turned a
four-thread engine into a one-thread engine, and hid a bug for as long as it was there.

### …use insertion-ordered collections everywhere?

Tried, at about 36 sites, then removed in favour of three `hashCode` fixes. Reasons are in
[SINGLE-THREAD-DETERMINISM.md](SINGLE-THREAD-DETERMINISM.md): it treats the symptom, any newly added collection silently
reintroduces the problem, and it spreads one requirement across six files with nothing to enforce it.

### …sort the collections instead?

Java's sort is **stable**, so wherever the comparator returns 0 it preserves the order it was handed —
the unordered order you were trying to escape. A sort only canonicalises as far as its comparator
discriminates, and several comparators here compare on fewer fields than the matching `hashCode`.
`CategoricalDist` already contains a sort that does not fix its ordering problem for exactly this
reason.

### …allow a small tolerance when comparing runs?

The differences are not bounded, so there is no tolerance to set. Because draw counts desynchronise, an
initial difference of a few metres becomes a different trip a few records later. A tolerance wide enough
to absorb that is wide enough to hide the regressions replay exists to catch.

### …pin each vehicle to one thread?

Already done, and worth doing on its own — a shared pool was dropping 255 of 279 records because
out-of-order arrivals were rejected. It guarantees a bus's records are processed in arrival order. It
does nothing about the reasons above.

### …just find the remaining bug?

In progress. The current symptom is 3 records out of 270 where a vehicle's filter reads as fresh when it
should carry state. Measurement has eliminated the clock, record ordering, `isValidRecord`, the
early-return paths, the crash-and-retry path, candidate generation, and every caller of the reset and
eviction methods.

The slow part is visibility. Nothing in the logs marks the event, so each hypothesis needs a probe, a
build and two runs. Instrumentation also changes what it measures: one probe threw inside the engine and
altered the run it was watching, and a probe that sorted its output could not answer a question about
order.

---

## What is settled, and what is not

**Settled.** Iteration order no longer depends on memory addresses. Three fixes did it: value-based
`hashCode` on `BlockConfigurationEntryImpl` and `TripEntryImpl`, and not folding an enum's identity hash
into `VehicleState` and `JourneyState`. Verified by direct measurement — candidate sets and their
iteration order are identical across JVMs — which is why the 36 insertion-ordered collections could be
removed.

**Settled.** Each bus draws from its own random stream, so results do not depend on thread assignment or
on how the fleet is sharded.

**Settled.** Single-threaded replay is reproducible, repeatably.

**Settled.** Multi-threaded replay. The remaining cause was `ScheduleLikelihood`'s
`POS_SCHED_DEV_CUTOFF`, a mutable static field shared across all stripes (see the corrected table entry
above). Verified with `replay-determinism.sh` at `OBA_INFERENCE_THREADS` above 1: output now matches
the single-threaded baseline.

**Void.** Any earlier claim that multi-threaded replay was deterministic. Those runs were measured with
the UTS lock serialising inference, so the threads never genuinely overlapped. The `hashCode` work
remains correct; the multi-thread conclusion drawn alongside it does not.
