# Replay determinism

Replay's value depends on identical input producing identical output. That has been verified at small
scale; at production scale it's unverified, not disproven - the one comparison run wasn't seed-controlled,
so it doesn't actually distinguish "working as intended" from "broken." This document explains both.

## What the engine does

For each bus the engine holds 200 **particles** — 200 complete hypotheses, each naming a block, a trip,
a position along that trip, and a phase such as "in progress" or "deadheading". Each GPS record triggers
a cycle: list the candidate positions the bus could be at, score them, then draw 200 new particles at
random, weighted by those scores. The published answer comes from the winning particle.

Determinism here is stricter than "computes the same values" — it's deterministic only if **every random
number reaches the same decision**. A draw is consumed by a specific comparison at a specific point; two
runs can consume the same numbers and still disagree if those numbers arrive at different comparisons.
One nondeterministic event is therefore not a small error: it desynchronizes the draw sequence, and every
later draw for that vehicle differs from then on.

## Settled: small-scale determinism (270-record fixture, 5 vehicles)

Fixed, verified, repeatable — both single-threaded and multi-threaded — via `replay-determinism.sh`
against the committed `replay/fixtures/determinism-270.jsonl` fixture. Four causes, found and fixed:

| File | What it does |
| --- | --- |
| `InferenceRng.java` (new) | Gives every bus its own random generator, seeded from a global seed mixed with the vehicle id. Without this, every bus drew from one shared generator, so thread interleaving (timing, not repeatable) decided which draw each bus got. |
| `CategoricalDist.java`, `ParticleFactoryImpl.java` | Route the engine's draws through `InferenceRng` instead of the shared generator. |
| `VehicleState.java`, `JourneyState.java` | Stop folding an enum's `hashCode()` into their own. Java's `Enum.hashCode()` is address-derived and `final` — any class that folds it in inherits an iteration order that changes every JVM start. Use the phase's **name** instead (JLS-specified, so stable across JVMs). |
| `BlockConfigurationEntryImpl.java`, `TripEntryImpl.java` (new, shadow upstream classes) | Add a content-based `hashCode()` — the upstream originals had none, so `HashSet`/`HashMap` iteration order over them was also address-derived. |
| `ScheduleLikelihood.java` | `POS_SCHED_DEV_CUTOFF` was a mutable `private static double`, written from each particle's own trip duration and read back a few lines later — every stripe hit the same field. Made local, threaded through as a parameter. This was the last cause; multi-threaded output matched the single-threaded baseline once it was fixed. |

Why per-*bus* generators rather than per-thread: a bus is already pinned to one thread, so per-thread
would be reproducible for a single process — but a bus lands on a different thread when the fleet is
split differently, so per-thread seeding gives one answer for a whole-fleet run and another for the same
bus inside a shard. Keying on the vehicle makes the result independent of how the work is divided.

**Also settled, same evidence:** each bus draws from its own stream, so results don't depend on thread
assignment or on how the fleet is sharded (as long as the causes above stay fixed) — the sharding
strategies in [SHARDING.md](../SHARDING.md) rely on this.

**Road not taken:** swapping `HashSet`/`HashMap` for insertion-ordered collections (~36 sites) also
worked, but was reverted in favor of the hash fixes: it treats the symptom (any new collection on the
candidate path silently reintroduces the bug, with no compile error and no failing test), it doesn't
remove the requirement so much as relocate it, and it can't be enforced across files. A content-derived
hash fixes it once, for every collection holding that content, including ones written later — confirmed
by deleting all 36 substitutions and watching determinism hold.

## Not settled: production-scale determinism (real archive data, ~5,000 vehicles/hour)

**2026-08-24: two independent JVM runs of the identical one-hour window diverged on ~17% of shared
records** - `continuous` (`08:00-09:30`) and `early` (`08:00-09:00`, same box, same config -
`maxOutstanding=2000`, 94 stripes, same code), compared over their shared `[08:00,09:00)` region with
`compare-replay-runs.py`: 172,194 of 995,992 shared records differed, first-differing-record for nearly
every vehicle within the first ~10-15 minutes. **But neither run set `-Doba.inference.seed`** (confirmed:
`run-replay.sh` never sets it itself, and the standard replay command doesn't add it as an extra arg), and
`InferenceRng.java:90-95` makes seed `0` deliberately non-reproducible - `new Random()` /
fresh `MRG32k3a()` per JVM start, independent of threading entirely. Two unseeded runs are *expected* to
diverge from the first record onward regardless of any thread-interleaving bug, which is exactly the
observed signature. The comparison wasn't seed-controlled, so it cannot distinguish that from the
harder desync bug described above - **production-scale determinism is unverified, not disproven.**

The actual test: rerun the same window twice with the *same* explicit `-Doba.inference.seed=N` both
times. If they still diverge, that proves the desync bug at scale; if they match, seed 0 was the whole
explanation and multithreaded determinism holds. Not yet done - and the exact `continuous`/`early` repro
details (commands, box) are not recoverable from this repo: this section originally cited a 2026-08-24
`replay/WORKLOG.md` entry for them, but no such entry exists (checked directly). Whoever picks this up
next starts from scratch on reproduction, with a same-seed comparison from the start.

## Why this is hard to chase

The search space is every source of shared mutable state reachable from concurrent particle-filter code
— not one bug, but however many independent ones happen to exist. The small-scale fix above needed four
unrelated causes fixed before the fixture passed (a shared RNG, two missing `hashCode()`s, one static
field); finding three of four and missing the fourth would have looked identical to finding none, since
a single leftover shared-state leak desyncs the draw sequence for every vehicle it touches from that
point on. None of this is visible by reading the code in isolation — a field looks safe until a second
call site under concurrency gets added later, or an upstream library's collection turns out to iterate in
address-derived order. Finding a case like that is empirical, not analytical: instrument, run under real
concurrency, check whether the specific decision at the specific point diverges, repeat.

On top of that, no test asserts determinism and no build step notices when it breaks — it's observable
only by running the same input twice and comparing, so every hypothesis costs a build and two runs, and
the symptom appears far from the cause (a draw a vehicle took in its first minute first shows up as a
different trip match forty minutes later). Two traps worth knowing about:

- **A passing comparison doesn't prove the threads ran concurrently.** For example, a lock that was placed around a
  network call (crew data over S3) rather than around the state it protected serialized all four
  "concurrent" stripes without anyone requesting that — the engine reported four threads and was not
  concurrent. Determinism comparisons passed repeatedly under this bug and were taken as evidence
  multi-threading was reproducible; they were evidence of nothing, since the threads never overlapped. It
  surfaced only because disabling the lock's cause produced an unrelated 20x speedup that was too large
  to attribute to anything else. (The bug itself is defect-tracked in [OBA-BUGS.md](../OBA-BUGS.md).)
- **Seeding, locking, sorting, and tolerances don't fix this class of bug.** Seeding fixes which numbers
  exist, not which decision consumes which number. Locks stop corruption, not ordering — two threads can
  take a lock in either order and both be "correct." Java's sort is stable, so it only canonicalizes as
  far as the comparator discriminates; several comparators here compare on fewer fields than the matching
  `hashCode()`, so a sort can silently preserve the unordered order it was meant to replace. And the
  differences aren't bounded (a few-meters difference can become a different trip a few records later),
  so there's no tolerance narrow enough to matter and wide enough to pass.

## Caveats that still apply

- **The two shadowed classes are the weakest part.** `BlockConfigurationEntryImpl`/`TripEntryImpl` copy
  an upstream library class verbatim plus a `hashCode()` — re-diff against the library source if either
  is touched, and never let them onto a bundle build's classpath (a bundle build mutates the field they
  hash on, which corrupts any collection holding them; this can't happen during inference, since the
  bundle is loaded once and only read afterward).
- **Replay's output now differs slightly from stock OBA's**, by design: changing a hash code changes
  iteration order, which changes which candidate a draw selects. Differences are small (a few meters)
  and are not evidence of a bug in either direction, but don't diff replay output against production
  expecting an exact match.
- **`InferenceRng` has a shared fallback** if a draw happens on a thread not yet told which bus it's
  working on — silently reproducing the original shared-generator bug. No current code path reaches it;
  it's a trap for future code, not a live defect.
- Two ordering mechanisms are dormant, not removed, and stay harmless only while hash order stays
  content-derived: Java's stable sort, and a `TreeSet` in `computeBestState` that treats "compares equal"
  as "duplicate" (defect 4 in `OBA-BUGS.md`).

## Tooling

Paths relative to `local-loop/`.

| Path | Purpose |
| --- | --- |
| `determinism/replay-determinism.sh` | Runs one fixture twice in two fresh JVMs and compares. The small-scale test. |
| `determinism/compare-replay-runs.py` | Field-by-field comparison keyed on `(vehicleId, recordTimestamp)`, so concurrent-publication order doesn't matter. Also what the production-scale comparison above used. |
| `determinism/replay-trace-diff.sh` | Runs a fixture twice with per-record tracing and reports where two runs first diverge. Needs `patches/inference-trace.patch`. |
| `determinism/patches/hash-probe.patch` | Instrumentation that found the three hash holes. Not part of the build. |
| `determinism/patches/gate-probe.patch` | Logs the timestamps that decide whether a record reaches the particle filter. |
| `replay/fixtures/determinism-270.jsonl` | The committed fixture (5 vehicles), with a manifest recording its checksum and source bundle. |
| `replay/scrub-fixture.py` | Replaces real operator badge numbers with surrogates for the committed fixture. |
| `replay/cut-vehicles.py` | Cuts N vehicles out of an archived bucket, preserving arrival order, for scaling tests. |
