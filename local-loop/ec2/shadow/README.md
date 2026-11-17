# Shadow dispatcher-actions engine (test stage 4)

A second predictions webapp beside an arm's own, built from the `timothy/dispatcher-actions` branch of
onebusaway-nyc-predictions. It reads the arm's inference stream and the live Bus Trek and Yard Trek snapshots and
publishes on ports nothing reads, so the engine's decisions can be scored with no feed affected. Why, the targets, the
daily scoring and the review results: onebusaway-nyc-predictions `replay/docs/DISPATCHER-ACTIONS-STATUS.md` §5.

Host: `oba-nyc-filtered` (v3). The S3 prefix in `install.sh`, `run-shadow-archiver.sh` and `shadow-sync.sh` names that
host; change all three to run it elsewhere.

## Layout

Everything lives under `/data/shadow`, so nothing the arm uses is touched:

| path | what |
|---|---|
| `onebusaway-nyc-predictions/` | the branch checkout (local branch `shadow`), pinned to the commit in `COMMIT` |
| `m2/` | its own Maven repository, seeded from the oba user's; the branch's `1.4-SNAPSHOT` jars must not land in `~/.m2`, where the arm's webapp would pick them up |
| `bundle/` | a copy of the arm's bundle: the webapp opens an HSQLDB file in the bundle directory, and the arm's webapp holds its lock |
| `TimePoints.csv` | `adjustments.timepointsFile`, fetched once by the installer |
| `logs/adj-<date>.log` | the engine's `adj` lines (adjustments package, INFO; today's is `adj-current.log`) |
| `spans/skip-spans.csv` | `adjustments.skipSpansFile`, appended across restarts |
| `predictions-archive/` | the shadow archiver's hourly zips before upload |

| unit | does |
|---|---|
| `oba-shadow-predictions` | the webapp (`run-shadow-predictions.sh`): input `:5567` (a connect, shared with the arm), output 5578 / 5579 / 5580 instead of 5568 / 5569 / 5570, jetty 8092, Mongo database `onebusaway-nyc-predictions-shadow`, CloudWatch off (`-DCloudWatchKey=-`) so the arm's metrics stay its own |
| `oba-shadow-archiver` | the stock predictions archiver on `:5578` (`run-shadow-archiver.sh`). It sets its bucket and prefix after `env-common.sh`, because the host's `env-local.sh` sets `OBA_ARCHIVER_S3_PREFIX` for the arm's own archive |
| `oba-shadow-sync.timer` | hourly: logs, span file and `COMMIT` to S3 (`shadow-sync.sh`) |

The engine flags are in `run-shadow-predictions.sh`: both live sources, the assume-role for their buckets, the dwell
clear on and the corroborated pre-start cancel at 0 (STATUS calls this the `all` set); everything else is the
commit's defaults. Change the set only at a restart, and record it in STATUS §5.

Outputs, under `s3://ds-oba/replay/prediction/dispatcher-actions/shadow/oba-nyc-filtered/`: `archive/` (hourly zips of
everything published, including the cancellation messages), `logs/`, `spans/skip-spans.csv`, `COMMIT`.

The shadow's input is archived by the arm's `oba-inference-archiver` to `s3://mtalirr/oba-ec2-inference/v3-filtered/`
(from 2026-11-05), so a replay of a shadow day runs on the same records.

## Install, update, stop

```bash
# from a laptop: publish this directory, then on the host as root
aws s3 sync local-loop/ec2/shadow/ s3://ds-oba/replay/prediction/dispatcher-actions/shadow/oba-nyc-filtered/deploy/
/data/shadow/install.sh <commit>      # first time: aws s3 cp …/deploy/install.sh . && bash install.sh <commit>
systemctl stop oba-shadow-predictions oba-shadow-archiver oba-shadow-sync.timer
```

`install.sh` refuses when another process holds a shadow port, fetches the files from `deploy/`, copies the bundle and
Maven repository on first use, checks the branch out at `<commit>`, builds the predictions webapp and (re)starts the
three units. Re-running it at a new commit is the update; the previous commit is the rollback.

## Checks

- `systemctl is-active oba-shadow-predictions oba-shadow-archiver`; `cat /data/shadow/COMMIT`.
- `grep 'adj summary' /data/shadow/logs/adj-current.log | tail -1`: every counter, logged hourly. `Adj.BT.STALE`,
  `Adj.YT.STALE` or `Adj.BT.FROZEN` mean a source stopped updating (Bus Trek's frozen alarm also fires overnight, when no
  rows change); `Adj.YT.NO_BLOCK_ID` above zero means Yard Trek dropped block ids and the open-block path is blind.
- A new hourly zip under `archive/` and a fresh `logs/` object in S3.
- The arm itself: `oba-predictions` still publishes on 5568 and its archive prefix receives only its own hours.
