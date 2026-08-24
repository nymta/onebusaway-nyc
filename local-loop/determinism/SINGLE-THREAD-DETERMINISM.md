# Making replay reproducible

> Note: This doc was written before we caught a subtle locking "bug" that caused multithreaded OBA to effectively run
> single-threaded. When running truly multi-threaded, determinism became much harder. This is documented in
> [MULTITHREAD-DETERMINISM.md](MULTITHREAD-DETERMINISM.md).

Replay works by running archived bus data through the inference engine twice and comparing the two
outputs. That only tells you something if identical input produces identical output. It did not.

This file explains why, what changed, and what to watch out for. It assumes no prior knowledge of the
inference engine.

---

## The problem

The engine cannot know which trip a bus is running, so it guesses. For each bus it holds 200
**particles** — 200 complete guesses, each naming a block, a trip, a position along that trip, and a
phase such as "in progress" or "deadheading". When a GPS record arrives it:

1. lists the candidate positions the bus could plausibly be at,
2. scores each candidate against the record,
3. draws 200 new particles at random, weighted by those scores,
4. publishes the answer from the winning particle.

Give it the same records twice and the answers moved. Positions differed by a few metres on about a
third of records, and occasionally it reported a different trip.

Two separate causes, both about step 3.

### Cause 1: all buses shared one random number generator

Every bus drew from the same generator. With several buses being processed on different threads at the
same time, a draw taken for bus A consumed a number bus B would otherwise have used. So the sequence
of numbers each bus saw depended on how the threads happened to interleave — which is timing, and
timing is not repeatable.

Seeding does not help. A fixed starting point consumed in a varying order still varies.

### Cause 2: a random draw picks by position, and the positions moved

`CategoricalDist` is the weighted picker. It holds candidates in an array, generates one random
number, and walks the array until the cumulative probability passes it. **If you shuffle the array,
the same random number lands on a different candidate.**

The candidates were held in Java's `HashSet` and `HashMap`. Those iterate in an order derived from
each object's `hashCode()`. When a class does not define `hashCode()`, Java supplies a default derived
from the object's memory address — and addresses differ every time the JVM starts. So the same
candidates came out in a different order on each run.

Three things were feeding an address-derived hash code into the chain. Two were OBA classes that never
defined `hashCode()`. The third was less obvious and is worth stating plainly:

> **In Java, an enum value's `hashCode()` is based on its memory address, not on its name or its
> position in the enum.** This is fixed in the JDK — `Enum.hashCode()` is `final` and cannot be
> overridden. Any class that folds an enum's hash code into its own inherits the instability.

Two OBA classes did exactly that, with the bus's phase.

---

## The change

Nine files in the engine. Everything else lives in `local-loop/` and is test tooling, not shipped
behaviour.

### Needed for determinism

| File | Lines | What it does |
| --- | --- | --- |
| `InferenceRng.java` | 269 (new) | Gives every bus its own random number generator, seeded from one global seed mixed with the vehicle id. Fixes cause 1. |
| `ParticleFactoryImpl.java` | 34 | Routes the engine's draws to `InferenceRng` instead of the shared generator. |
| `CategoricalDist.java` | 6 | Same, for the two draws taken by the weighted picker. |
| `VehicleLocationInferenceServiceImpl.java` | 28 | Marks which bus a thread is working on, so a draw reaches the right generator. Reads the seed from `-Doba.inference.seed`. |
| `VehicleState.java` | 3 | Stops folding the enum's address-derived hash into its own. Uses the phase's **name** instead. Fixes part of cause 2. |
| `JourneyState.java` | 4 | Same fix, second site. |
| `BlockConfigurationEntryImpl.java` | 401 (new) | Copy of an upstream class with a `hashCode()` added. See caveats. |
| `TripEntryImpl.java` | 177 (new) | Same. |

Why per-bus generators rather than per-thread: each bus is already pinned to one thread, so per-thread
would be reproducible for a single process. But a bus lands on a different thread when the fleet is
split differently, so per-thread seeding gives one answer for a whole-fleet run and another for the
same bus inside a shard. Keying on the vehicle makes the result independent of how the work is divided.

### Useful, but not about determinism

| File | Lines | What it does |
| --- | --- | --- |
| `ReplayFileInputTask.java` | 16 | `-Dreplay.exitWhenDone` makes the engine exit after the last record instead of sitting in Jetty forever, so a two-run comparison can be unattended. Exits non-zero if output was still draining, so a truncated run is detectable. |
| `VehicleLocationInferenceServiceImpl.java` | (part of the 28) | `-Doba.inference.threads` overrides the thread count at launch. Setting it to 1 removes threading as a variable, which is how the two causes above were separated. |

Both are inert unless the property is set.

### Deliberately reverted

Two real defects were found and fixed during this work, then backed out to keep the engine identical
to production OBA where determinism does not require a change. Both are recorded in
[OBA-BUGS.md](../OBA-BUGS.md) as defects 2 and 3:

- `computeBestState` compares partial sums against a running maximum, so a trip group can lose to a
  rival that happened to be visited earlier despite a higher final total.
- The same method uses `Double.MIN_VALUE` as a "most negative" sentinel. It is the smallest
  **positive** double.

Neither is needed for reproducibility, so neither is in the commit.

---

## The road not taken: insertion-ordered collections

The first attempt did not touch hash codes at all. Java's `LinkedHashSet` and `LinkedHashMap` iterate
in the order items were added rather than by hash code, so swapping `HashSet` for `LinkedHashSet`
sidesteps the unstable hash entirely.

It works. About 36 collections were changed and replay became reproducible.

It was then removed in favour of the three hash fixes, and every one of the 36 was verified
unnecessary by rerunning the comparison. Five reasons it is the worse approach:

**It treats the symptom.** The hash codes are still unstable; the change only avoids looking at them.
The bug is still there, one collection away.

**Any new collection reintroduces the bug.** Someone adding an ordinary `HashSet` on the candidate
path silently breaks reproducibility. Nothing fails to compile, no test fails unless someone happens
to run the two-run comparison, and the symptom is a few metres of position difference — easy to
attribute to something else.

**It relocates the requirement instead of removing it.** `CategoricalDist` cannot use an
insertion-ordered container, because its probability map is a Trove primitive map with no ordered
variant. It needed a parallel list recording insertion order. That made it reproducible *only if the
sequence feeding it was reproducible*, which required the other collections to stay ordered. So the
guarantee became a chain, and every link had to hold.

**The requirement is spread across many files and cannot be enforced.** The 36 sites sat in six
classes. Keeping them correct means either remembering, or a build rule that pattern-matches on
constructor names — which is a heuristic, not a proof.

**Insertion order is not a canonical order.** It depends on the code path that filled the collection.
Two paths that produce the same set of candidates in different sequences give different results —
reproducibly, but arbitrarily. A content-derived hash gives the same order for the same content
regardless of how it was assembled.

By contrast, the three hash fixes are a bounded, finished job. Once a class's hash code is derived
from its content, **every** collection holding it is reproducible, including ones written later. That
was confirmed by deleting all 36 substitutions and watching determinism hold.

---

## Caveats

### The two copied files are the weakest part

`BlockConfigurationEntryImpl` and `TripEntryImpl` belong to `onebusaway-transit-data-federation`, a
library this repository depends on. We could not edit them in place, so each is copied into this
repository under the same package and class name. Java finds ours first, so ours is the one that runs.
This is called shadowing.

What to know:

- Each copy differs from the original **only** by the added `hashCode()` method. That was checked by
  diffing against the library source, and the check is worth repeating if either file is touched.
- If the library version is ever upgraded, our copies silently stay at the old version. There is no
  warning. In practice this code is not actively developed, so the risk is small — but a version bump
  is the moment to re-check.
- **These copies must not be on the classpath of a bundle build.** `TripEntryImpl`'s id field is
  assigned during a bundle build. Hashing an object and then changing the field it hashes on corrupts
  any collection holding it. During inference this cannot happen, because the bundle is loaded and then
  only read.
- The clean fix is to patch the library fork and build it. That is currently blocked for an unrelated
  reason: the branch we can build is missing 48 classes the engine needs at runtime.

### The output is now different from stock OBA

Changing a hash code changes iteration order, which changes which candidate a draw selects. So replay
produces reproducible answers, **not** the same answers stock OBA would produce from the same input.

This matters if you compare replay output against production to judge accuracy. The differences are
small — a few metres — but they are real, and they are not evidence of a bug in either direction.

### We added `hashCode()` without `equals()`

That combination is legal and deliberate. `equals()` decides whether two objects are treated as the
same entry in a set; `hashCode()` only decides which bucket they land in. Leaving `equals()` alone
means set membership is unchanged, so the only thing that moved is order. Adding `equals()` would also
start collapsing candidates that currently coexist, which is a behaviour change and a separate
decision. See defect 5 in [OBA-BUGS.md](../OBA-BUGS.md).

### `name()` rather than `ordinal()`

The enum fix uses `phase.name().hashCode()`. String hash codes are specified by the Java language, so
they are the same in every JVM. `ordinal()` would be cheaper and equally stable, but it changes
meaning if someone reorders the enum constants, whereas renaming a constant is a more visible edit.

### Two ordering mechanisms are dormant, not removed

Both remain in the code and both are harmless while hash order is stable. They are where a future
regression would surface:

- Java's sort is **stable**, so wherever two items compare equal it keeps the order it was given. Any
  sort used to canonicalise order therefore only canonicalises as far as its comparator distinguishes.
- `computeBestState` collects the winning group's particles into a `TreeSet`, which treats "compares
  equal" as "duplicate" and keeps only the first one inserted. Several classes compare on fewer fields
  than they hash, so particles that differ in a published field can compare equal, and which one
  survives depends on insertion order. See defect 4 in [OBA-BUGS.md](../OBA-BUGS.md).

### `InferenceRng` has a shared fallback

If a draw happens on a thread that has not been told which bus it is working on, it falls back to one
generator shared by all threads — the original bug, silently. Today no code path reaches it: every draw
site is inside the particle filter, and the filter is only driven from one place, which sets the bus
first. It is a trap for future code rather than a current defect. A counter on that path, asserted
zero after a replay, would make it visible.

### How far this is verified

Determinism is confirmed on a 270-record, 5-vehicle fixture, multi-threaded, repeatedly. That is small.
A real five-minute archive bucket is about 266,000 records and 4,900 vehicles, and has not been
replayed — it needs a compressed reader and a whole-bucket sort first.

---

## Tooling

Paths are relative to `local-loop/`.

| Path | Purpose |
| --- | --- |
| `determinism/replay-determinism.sh` | Runs one fixture twice in two fresh JVMs and compares. This is the test. |
| `determinism/replay-trace-diff.sh` | Runs a fixture twice with per-record tracing and reports where the two runs first diverge. Requires the tracing patch below. |
| `determinism/compare-replay-runs.py` | Field-by-field comparison, keyed on (vehicle, timestamp) so concurrent publication order does not matter. |
| `determinism/patches/hash-probe.patch` | Instrumentation that found the three hash holes. Not part of the build. |
| `determinism/patches/inference-trace.patch` | Per-record `in=` / `draws=` / `out=` tracing. Required by `replay-trace-diff.sh`. |
| `determinism/patches/gate-probe.patch` | Logs the timestamps that decide whether a record reaches the particle filter. |
| `replay/fixtures/determinism-270.jsonl` | The committed fixture, with a manifest recording its checksum, the bundle it was cut against, and how to regenerate it. Operator badge numbers are replaced with surrogates. |
| `replay/scrub-fixture.py` | Does that replacement. |
| `replay/cut-vehicles.py` | Cuts N vehicles out of an archived bucket, preserving arrival order, for scaling tests. |

The scripts under `determinism/` read `env.sh` and the observer from `local-loop/`, and the fixtures from
`local-loop/replay/fixtures/`, so both directories must stay direct subdirectories of the harness root.

All three patches carry headers explaining what each measurement answers and which mistakes to avoid
when reading the output. Those headers are the more valuable half.
