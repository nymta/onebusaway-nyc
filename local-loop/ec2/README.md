# EC2 host config (committed snapshot)

On-host configuration for the whole-MTA GTFS-RT deployment (see `../EC2-DEPLOYMENT.md`),
committed for reproducibility. Captured from EC2 `i-0386b6bb8338b2f67` as it runs.

**No secrets in here.** RabbitMQ feed credentials are fetched at runtime from **SSM Parameter
Store** (`/oba/rabbitmq/*`, plus `/rabbitmq/data-pusher/prod/password` for the publisher below) by
`env-common.sh`'s `gp()` helper; the per-repo GitHub **deploy keys**
live only in `~oba/.ssh` on the host and are deliberately excluded.

## File → host path

| repo path | host path | role |
|---|---|---|
| `opt-oba/env-common.sh` | `/opt/oba/env-common.sh` | shared env + `gp()` (SSM param fetch) |
| `opt-oba/run-{broker,inference,predictions,gtfsrt,predictions-archiver}.sh` | `/opt/oba/` | service launchers (invoked by the systemd units) |
| `opt-oba/predictions-archiver.py` | `/opt/oba/predictions-archiver.py` | ZMQ :5568 → hourly `queuePredictions_*.zip` → S3 |
| `opt-oba/cs-gps-publisher.py` | `/opt/oba/cs-gps-publisher.py` | ZMQ :5564 → RabbitMQ `nyct.bustech.gps-filtered` (opt-in; see below) |
| `opt-oba/run-cs-gps-publisher.sh` | `/opt/oba/run-cs-gps-publisher.sh` | launcher for the above |
| `opt-oba/set-weights.sh` | `/opt/oba/set-weights.sh` | reads SSM `/oba/predictions/weights`, POSTs `/api/weight` |
| `opt-oba/deploy.sh` | `/opt/oba/deploy.sh` | GitHub-Action target (modes `deploy` / `set-weights`) |
| `opt-oba/monitor.sh` | `/opt/oba/monitor.sh` | emits `OBA/Prod` CloudWatch metrics (run by the timer) |
| `systemd/oba-*.service`, `oba-monitor.timer` | `/etc/systemd/system/` | units (Restart=always; ordered broker→inference→predictions→gtfsrt→archiver) |
| `nginx/nginx.conf` | `/etc/nginx/nginx.conf` | minimal http config (no stock server) |
| `nginx/conf.d/oba-gtfsrt.conf` | `/etc/nginx/conf.d/oba-gtfsrt.conf` | GET-only allowlist reverse proxy (:80 → :8083) |
| `bundle/bundle-wholeMTA.xml` | `/data/bundle-src/wholeMTA/bundle-wholeMTA.xml` | whole-MTA bundle-builder config (6 GTFS + NYCT STIF) |

**Not captured** (host-only / external): the two git clones under `/opt/oba`, the built bundle at
`/data/oba-bundle`, Mongo data at `/data/mongo`, deploy keys, and SSM params.

## Rebuild a host
1. Provision per `../EC2-DEPLOYMENT.md` §5 (EC2 + EIP + `/data` volume + SG :80 + IAM instance profile + SSM params).
2. Install toolchain (Corretto 11, Maven, git, Docker, nginx); clone the two repos into `/opt/oba` via read-only deploy keys; start `mongo:4.4`.
3. Drop these files into place, `chmod +x /opt/oba/*.sh`.
4. Build the bundle (`FederatedTransitDataBundleCreatorMain` + `bundle-wholeMTA.xml`) into `/data/oba-bundle`.
5. `systemctl daemon-reload && systemctl enable --now oba-broker oba-inference oba-predictions oba-gtfsrt oba-predictions-archiver oba-monitor.timer nginx`. Add `oba-cs-gps-publisher` only on a host with an allowlisted Elastic IP (see below).

### Predictions S3 archiver (`predictions-archiver.py`)

Reproduces prod's `queuePredictions` historical archive from our own instance, so the two can be
compared offline hour-for-hour.

- **Source:** the internal ZMQ predictions stream (`127.0.0.1:5568`, topic `time`), **not** the public
  `/tripUpdates` feed. A passive `zmq.SUB`, so it cannot slow or drop anything upstream.
- **Per message:** parse the last frame as a GTFS-RT `FeedMessage`, normalise stop ids, append one JSON
  line to `/data/predictions-archive/queuePredictions_<UTC hour>.json`. Records are
  `incrementality=DIFFERENTIAL`, so a consumer must replay and merge them to get a snapshot.
- **Hourly rotation:** on the first message of a new UTC hour, zip to
  `queuePredictions_YYYY-MM-DD_HH-00-00.zip` (prod's layout) and upload to
  `s3://mtalirr/oba-ec2-predictions/`.
- **stopId normalisation:** the stream emits per-agency stop ids (`MTA NYCT_401964`, `MTABC_501531`)
  where prod's archive uses one `MTA_<id>` namespace — the only format difference between the two.
  `normalize_stop_ids()` re-prefixes to `OBA_ARCHIVER_STOP_ID_AGENCY` (default `MTA`; empty = verbatim).
  Lossless, since the numeric ids are one global namespace, and it also stops the 581 stops served by
  both an NYCT and an MTA Bus route being published under two different ids.
- **Restart safety:** on `SIGTERM` it skips the zip/upload and leaves the partial hour on disk; the next
  start re-opens it in append mode. That hour mixes conventions if the stop-id setting changed —
  restart early in an hour and treat it as suspect.
- **Credentials:** optional SSM params `/oba/predictions/s3-archive/{access_key_id,secret_access_key}`;
  otherwise the instance profile (`oba-nyc-ec2-role`, inline policy `oba-s3-predictions-archive-write`).
- **Health:** `journalctl -u oba-predictions-archiver` logs a stats line every
  `OBA_ARCHIVER_LOG_STATS_SEC` (60 s). **Local-only test:** `OBA_ARCHIVER_UPLOAD=false`.

### CS filtered-AVL publisher (`cs-gps-publisher.py`)

Republishes the **~28 s upstream-filtered** AVL feed onto RabbitMQ so hosts without an allowlisted
Elastic IP can consume it. Deployed 2026-08-20 on `i-0386b6bb8338b2f67`.

- **Why it exists:** `queue.staging.obanyc.com:5564` is source-IP allowlisted to **Elastic IPs only**
  — prod and `runner-al23` connect instantly, every ephemeral-IP host times out. Rather than move an
  EIP (prod's is load-bearing: data-archiver polls its hostname for the `obaEc2*` feeds), prod
  subscribes and fans out over the broker. The publisher only dials **out**, so no inbound SG rule.
- **Path:** ZMQ SUB (topic `bhs_queue`) → exchange **and** stream queue `nyct.bustech.gps-filtered`
  (`x-max-age=5m`, matching `nyct.bustech.gps`) → data-archiver feed `bustechGpsFiltered` →
  `s3://mtalirr/data-archiver/bustechGpsFiltered/` → `run-replay.sh --prefix`.
- **Fidelity:** bodies are republished **verbatim**; the envelope is parsed only to carry
  `timeReceived` into the AMQP timestamp, which is what the archiver buckets its 5-minute slots on.
- **Identity:** publishes as `data-pusher` (the cluster's write-side user). `data-archiver` is
  read-only (`write=''`) and *cannot* publish.
- **Opt-in per host:** `OBA_CSPUB_ENABLED=1` in `/opt/oba/env-local.sh`. `deploy.sh` installs the
  unit everywhere but only enables it where that flag is set — elsewhere it would just retry a
  connection that always times out.
- **Health:** `journalctl -u oba-cs-gps-publisher` logs a stats line every 60 s; expect
  `received==published`, `dropped=0`, `failures=0` at ~160 msg/s. ~16 MB RSS, unit capped at
  `MemoryMax=2G` / `CPUQuota=200%` so it cannot threaten the reference deployment on the same host.
- **Caveat:** `x-max-age=5m` means an archiver outage over 5 minutes is a permanent hole — the source
  is a PUB socket with no offset to rewind.

## Per-host overrides (`/opt/oba/env-local.sh`)

Not installed by `deploy.sh`, so each host keeps its own values across deploys; absent = stock
behavior. Beyond the deadband/archiver-prefix/monitor keys: `OBA_CSPUB_ENABLED`,
`OBA_DEADBAND_ENABLED` (set `false` on an arm fed the already-filtered queue, or the gate
double-filters) and `OBA_RMQ_STREAM_NAME`.

## Current tuning captured (this commit)
- inference `-Xmx30g` + ingestion deadband `minMeters=10 / minIntervalSec=7 / maxAgeSec=30` ("7 s-while-moving"; widened from 5 s on 2026-07-22) + stale-fix load-shedding `oba.shed.maxAgeSec=50` — `run-inference.sh`
- predictions `-Xmx10g`, gtfsrt `-Xmx6g`, Mongo WT cache 6 GB
- prediction weights `20/40/40` (SSM `/oba/predictions/weights`)
- **Caveat:** at AM/PM rush (~4,800+ vehicles) 32 vCPU can't hold true 5 s — hence the 7 s deadband + `oba.shed.maxAgeSec=50` load-shedding backstop (bounds lag, no OOM). Tune `minIntervalSec` / `shed.maxAgeSec` in `run-inference.sh` (+ `systemctl restart oba-inference`) or resize up. Tracked by the `oba-nyc-prod` CloudWatch dashboard.
