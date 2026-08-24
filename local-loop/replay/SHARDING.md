# Sharding replay across cores

Two ways to split a replay across more than one JVM have been tried: by **vehicle** (hash the fleet
across N processes) and by **time** (give each process a distinct calendar window). Vehicle-sharding
works today, with a real but explained ceiling. Time-sharding is blocked on an unrelated open problem.

## Sharding by vehicle: works, but leaves ~half the cores idle per shard

### Why it exists

A single JVM spanning both sockets of a two-socket box (48x, 192 vCPUs) doesn't scale like a bigger
single-socket box: per-task time rose from ~66-72ms (24x, one socket) to ~101-110ms (48x naive), for only
~1.28-1.36x aggregate throughput instead of ~2x. The cause is cross-socket memory latency against the
shared, mostly-read ~4.5GB transit graph every stripe's particle filter touches — nothing in the launch
flags does NUMA-aware allocation, so the heap lands first-touch on one socket and the other socket's
stripes pay a remote-access penalty on every read.

The fix: two NUMA-pinned JVMs (`numactl --cpunodebind=N --membind=N`), one per socket, each handling half
the fleet (`-Dreplay.vehicleShard=i/n` in `ReplayFileInputTask`, `run-replay.sh --shard i/n`). One real
bug on the way: both JVMs opened the same local HSQLDB scratch file at `${bundle.location}/...`
(wired in unconditionally by the container framework, nothing replay-specific), so the second process
lost the file-lock race and died. Fixed by giving each shard a shadow bundle directory — symlinks to the
real, large, read-only bundle data, so nothing is copied, but no scratch-db siblings to collide over.

### The result: NUMA fix confirmed, but each shard only fills ~40 of 94 stripes

Once fixed, per-task time recovered to ~50-63ms per shard — *better* than the single-socket baseline, not
just back to it. But combined throughput landed at ~1.5-1.6x over the single-JVM baseline, not the
~1.8-1.9x hoped for, because each shard only keeps ~40 of its 94 stripes concurrently busy (down from
~74-79/94 for a single-JVM full-fleet run).

**What this isn't:** not a hash-correlation bug between the shard filter and stripe assignment (checked —
`AgencyAndId.hashCode()` and the shard filter's hash are different formulas, not the same value reduced
two ways), and not a buffering-depth problem. That second one was the leading theory and was directly
tested: raising `-Dreplay.maxOutstanding` from 2000 to 4000 genuinely widened the reader's backlog (`tasks
pending` tracked the new ceiling exactly) but changed busy-stripe count not at all — still ~40-42/94
before and after, on live boxes, config otherwise identical.

**What it is:** the busy-stripe ceiling is set by how many vehicles are simultaneously reporting GPS at
any single instant, fleet-wide — not by how far ahead the reader has buffered. A single-JVM run of the
full fleet tops out around ~74-79/94 busy; that's roughly how many vehicles are live at once, this hour.
Splitting the fleet in half by vehicle hash necessarily halves that instantaneous count too, independent
of any reader setting — each shard lands around ~40/94 because that's about half of ~80, not because of
a fixable window size.

**Why it's still a net win despite that:** busy-stripe *count* isn't the same as throughput. Contention
over the shared transit graph scales with how many stripes are concurrently active, and that showed up
directly within a single run's own drain tail: with the vehicle population held constant (5,029 active
the whole time), per-task time fell from ~88-98ms to ~39-47ms purely as the concurrently-busy count
dropped from ~75 toward 1. Fewer, faster stripes beats more, slower ones — so each shard's ~40 busy
stripes each run measurably faster than they would alongside 74 competitors, and the combined throughput
still comes out ahead, just not at the ~2x that idle-core count alone would suggest.

## Sharding by time: blocked on determinism, not evaluated yet

The idea, to sidestep the ceiling above: instead of splitting the *fleet* (which halves how many vehicles
each shard sees live at once), split the *time window* — one process runs `[A, B)`, a second runs
`[B - buffer, C)` with the buffer period discarded from its output, both against the full fleet. Neither
shard's simultaneous-vehicle count would be reduced, so in principle this avoids the idle-stripe ceiling
above entirely.

The open question going in was whether a filter cold-started at `B - buffer` reconverges to the same
state a continuously-running filter would have by the time the real window starts at `B` — i.e., how
long a buffer is actually needed. That's testable with no code changes: `run-replay.sh --from`/`--to` for
both halves, trim the buffer out of the second half's own output by `recordTimestamp`, stitch the two
outputs together, and diff against a continuous baseline with `compare-replay-runs.py`.

Running that test surfaced a bigger problem before the boundary question could even be evaluated: a
continuous baseline and an unsharded, unbuffered run of the *same* window disagreed with each other on
~17% of records, diverging almost immediately rather than near any boundary. See
[determinism/DETERMINISM.md](determinism/DETERMINISM.md) — replay isn't currently verified deterministic
at production scale at all, with or without sharding. Any stitched-vs-continuous comparison is confounded
by that until it's resolved, so time-sharding's actual viability is still unknown, not ruled out.
