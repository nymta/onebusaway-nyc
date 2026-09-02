# Running replay

How to kick off a run and watch it, on a laptop or EC2, sharded or not. For what changed and why, see
[WORKLOG.md](WORKLOG.md); for the sharding tradeoffs themselves, see [SHARDING.md](SHARDING.md).

## Local

```bash
local-loop/build.sh                  # once per code change
local-loop/replay/preflight.sh       # checks all three credential surfaces
local-loop/replay/replay-stream.sh \
  --prefix s3://mtalirr/data-archiver/bustechGps/ \
  --from 2026-07-28/08-45 --to 2026-07-28/08-45 \
  -- -Dreplay.routeFilter='^M[0-9]'          # optional extras after --
```

Or from a local fixture: `local-loop/replay/replay.sh <fixture.jsonl>`. Both run in the foreground
(Ctrl-C ends the run - each replay needs a fresh JVM). Start `local-loop/observe-inferred.sh` first,
or skip it and write to a local spool instead (`-Doba.replay.output.dir`, see the flags below).
Two-run reproducibility check: `local-loop/replay/determinism/replay-determinism.sh [fixture] -- <extra -D args>`.

## EC2, non-sharded

```bash
/opt/oba/run-replay.sh --from D[/HH-MM] --to D[/HH-MM] [--tag NAME] [-D... extra args]
```

One process, uploads its own output to `s3://ds-oba/replay/inference-outputs/<label>/` as it runs.

## EC2, sharded

Three processes: two (or more) `--shard` invocations plus the merge coordinator, which is what actually
uploads.

```bash
# In tmux session 1
python3 local-loop/replay/merge-shard-output.py --label RUN_NAME
# In tmux session 2
/opt/oba/run-replay.sh --tag RUN_NAME --shard 0/2 --from D[/HH-MM] --to D[/HH-MM]
# In tmux session 3
/opt/oba/run-replay.sh --tag RUN_NAME --shard 1/2 --from D[/HH-MM] --to D[/HH-MM]
```

> Note from Marcos: I considered swapping out the 3-tmux-sessions approach with something more legit like systemd,
> but this worked for me and I didn't really have an issue with it and so didn't. But future reader, feel free!

Requires `numactl` and a bundle already split into per-shard shadow directories
(`/data/oba-bundle-shard{i}`, set up once per box - see `run-replay.sh`'s own `--shard` handling).

The coordinator exits on its own once every shard has finished (each writes a `REPLAY_DONE` sentinel on
exit, success or failure) - no need to `Ctrl-C` it. If you do stop it early, `SIGTERM`/`SIGINT` triggers
the same finalize-and-exit path, uploading whatever's ready regardless of the normal lag rule. It's safe
to kill and rerun at any point: state is persisted per run (`merge-state-<run name>.json`), and every
write is a full overwrite keyed by source file, never an append, so nothing duplicates on retry.

## Flags

### `replay.sh` / `replay-stream.sh` (local)

Env var overrides, not flags: `OBA_PF_DEBUG`, `OBA_DEADBAND_ENABLED/_MIN_METERS/_MIN_INTERVAL_SEC/_MAX_AGE_SEC`,
`OBA_INFERENCE_THREADS`, `OBA_CREW_SNAPSHOT_DIR` (default `/tmp/uts-snapshots`), `OBA_IE_OUTPUT_QUEUE`
(set to `S3OutputQueueSenderServiceImpl` to spool locally instead of publishing to an observer) on
`replay.sh`; `REPLAY_OUT_S3`, `REPLAY_OUT_DIR`, `REPLAY_SKIP_CREW_FETCH=1`, `REPLAY_EXIT_WHEN_DONE=false`
(keeps Jetty up) on `replay-stream.sh`.

### `run-replay.sh`

| Flag | Effect |
| --- | --- |
| `--from D[/HH-MM]`, `--to D[/HH-MM]` | Window bounds, `YYYY-MM-DD` or ET 5-minute slot start |
| `--shard i/n` | This run gets 1/n of the fleet, NUMA-pinned; needs a merge coordinator (see above) |
| `--tag NAME` | Prefixes the run's label (and `OUT_DIR`/`OUT_S3`/CloudWatch `RunId`) - `merge-shard-output.py --label` auto-discovers shards by this same prefix |
| `--prefix s3://...` | Source bucket/prefix, default `s3://mtalirr/data-archiver/bustechGps/` |
| `--limit N` | Cap the number of source objects fed in |
| `s3://bucket/key.jsonl.gz ...` | Explicit source objects instead of `--prefix`/`--from`/`--to` |
| `-D... extra args` | Passed straight to the JVM; see below |

| Env var | Effect |
| --- | --- |
| `REPLAY_OUT_S3` | Output bucket/prefix; `none` keeps the spool local only |
| `REPLAY_CREW_DIR` | Prefetched UTS snapshot dir (default `/data/uts-snapshots`); unset dir disables crew |
| `REPLAY_THREADS` | Inference stripe/pool thread count (default: this box's vCPUs, minus 2, split across shards) |
| `REPLAY_CLOUDWATCH` | `0` skips pushing `replay-monitor.log` to CloudWatch (default: on) |
| `REPLAY_DEADBAND_MIN_METERS` | Ingestion deadband min movement to keep a fix (default `0`) |
| `REPLAY_DEADBAND_MIN_INTERVAL_SEC` | Ingestion deadband min time between kept fixes (default `7`) |
| `REPLAY_DEADBAND_MAX_AGE_SEC` | Ingestion deadband max time before a fix is kept regardless of movement (default `30`) |
| `REPLAY_BUNDLE_ROOT` | Directory to use instead of `/data/oba-bundle` (default) - must hold exactly one bundle; also what `--shard` symlinks from into each shard's shadow dir |

### JVM properties (`-D`, extra args on any of the scripts above)

| Property | Effect |
| --- | --- |
| `-Dspring.profiles.active=replay` | Swaps in `MutableClock` and `GatedTaskScheduler` (already set by every script above) |
| `-Dreplay.file=<path>` | Input for `ReplayFileInputTask` (file or FIFO; already set by every script above) |
| `-Dreplay.exitWhenDone=true` | Exit after the input drains (already on by default) |
| `-Dreplay.maxOutstanding=N` | Reader lead bound, default 2000; 0 disables |
| `-Dreplay.routeFilter=<regex>` | Replay only vehicles serving matching routes |
| `-Dreplay.vehicleShard=i/n` | Set automatically by `run-replay.sh --shard`; not meant to be passed directly |
| `-Doba.crew.snapshotDir=<dir>` | Roster from prefetched snapshots, as-of the replay clock |
| `-Doba.crew.disabled=true` | Skip UTS entirely, when no snapshot directory is available |
| `-Doba.replay.tasks=A,B` | Background task domains to allow; empty blocks everything, `ALL` allows all |
| `-Doba.inference.threads=N` | Stripe/pool thread count; `1` removes threading as a variable |
| `-Doba.inference.sharedPool=false` | Opt out of the default shared-pool dispatch back into fixed per-vehicle stripes - see WORKLOG.md, 2026-08-26 |
| `-Doba.inference.seed=N` | Per-vehicle RNG seed, for reproducibility |
| `-Doba.shed.maxAgeSec=N` | Drop queued fixes older than this; off by default in replay (it's a live-recency valve) |
| `-Doba.particle.count=N` | Particles per vehicle, default 200 |
| `-Doba.replay.output.dir` / `.rollMinutes` / `.gzip` | Output spool location, part size (virtual minutes), compression |

## Monitoring

- **Per-process progress**: `replay-monitor.log` in each `OUT_DIR` (`pct_complete`, `speed`,
  `stripes=active/total`, ETA), or `tail -f engine.log` for the raw stripe/inference warnings.
- **CloudWatch**: `OBA/Replay` namespace, dimensioned by `RunId` (the full generated label, including
  `-shard-{i}-of-{n}` for a sharded run - group by it on a dashboard to see both shards on one chart).
  `AWS/EC2` widgets need a literal `InstanceId` in their `WHERE` clause; there's no cross-namespace join.
- **Merge coordinator's own log**: `ingested`/`uploaded` lines directly show which shard is ahead in
  virtual time (compare the nominal timestamps in the file paths being ingested) and how large the
  scratch backlog is getting (fragment counts per upload climb as one shard outpaces the other - see
  WORKLOG's 2026-08-26 entry). `du -sh /data/replay-out/merge-scratch-<run name>/` for the actual size.
- **Disk**: sharded runs hold a real, if normally bounded, scratch backlog - buckets can't close until
  the *slower* shard catches up. Not a concern under normal multi-hour drift, worth checking
  (`df -h /data`) on a run running long enough for that drift to compound.
