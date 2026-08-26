# Running replay on EC2

How to kick off a run and watch it, sharded or not. For what changed and why, see
[WORKLOG.md](WORKLOG.md); for the sharding tradeoffs themselves, see [SHARDING.md](SHARDING.md).

## Non-sharded

```bash
/opt/oba/run-replay.sh --from D[/HH-MM] --to D[/HH-MM] [--tag NAME] [-D... extra args]
```

One process, uploads its own output to `s3://ds-oba/replay/inference-outputs/<label>/` as it runs.

## Sharded

Three processes: two (or more) `--shard` invocations plus the merge coordinator, which is what actually
uploads.

```bash
# In tmux pane 1
python3 local-loop/replay/merge-shard-output.py --label RUN_NAME
# In tmux pane 2
/opt/oba/run-replay.sh --label RUN_NAME --shard 0/2 --from D[/HH-MM] --to D[/HH-MM]
# In tmux pane 3
/opt/oba/run-replay.sh --label RUN_NAME --shard 1/2 --from D[/HH-MM] --to D[/HH-MM]
```

Requires `numactl` and a bundle already split into per-shard shadow directories
(`/data/oba-bundle-shard{i}`, set up once per box - see `run-replay.sh`'s own `--shard` handling).

The coordinator exits on its own once every shard has finished (each writes a `REPLAY_DONE` sentinel on
exit, success or failure) - no need to `Ctrl-C` it. If you do stop it early, `SIGTERM`/`SIGINT` triggers
the same finalize-and-exit path, uploading whatever's ready regardless of the normal lag rule. It's safe
to kill and rerun at any point: state is persisted per run (`merge-state-<run name>.json`), and every
write is a full overwrite keyed by source file, never an append, so nothing duplicates on retry.

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
