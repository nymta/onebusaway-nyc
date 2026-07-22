# EC2 host config (committed snapshot)

On-host configuration for the whole-MTA GTFS-RT deployment (see `../EC2-DEPLOYMENT.md`),
committed for reproducibility. Captured from EC2 `i-0386b6bb8338b2f67` as it runs.

**No secrets in here.** RabbitMQ feed credentials are fetched at runtime from **SSM Parameter
Store** (`/oba/rabbitmq/*`) by `env-common.sh`'s `gp()` helper; the per-repo GitHub **deploy keys**
live only in `~oba/.ssh` on the host and are deliberately excluded.

## File → host path

| repo path | host path | role |
|---|---|---|
| `opt-oba/env-common.sh` | `/opt/oba/env-common.sh` | shared env + `gp()` (SSM param fetch) |
| `opt-oba/run-{broker,inference,predictions,gtfsrt}.sh` | `/opt/oba/` | service launchers (invoked by the systemd units) |
| `opt-oba/set-weights.sh` | `/opt/oba/set-weights.sh` | reads SSM `/oba/predictions/weights`, POSTs `/api/weight` |
| `opt-oba/deploy.sh` | `/opt/oba/deploy.sh` | GitHub-Action target (modes `deploy` / `set-weights`) |
| `opt-oba/monitor.sh` | `/opt/oba/monitor.sh` | emits `OBA/Prod` CloudWatch metrics (run by the timer) |
| `systemd/oba-*.service`, `oba-monitor.timer` | `/etc/systemd/system/` | units (Restart=always; ordered broker→inference→predictions→gtfsrt) |
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
5. `systemctl daemon-reload && systemctl enable --now oba-broker oba-inference oba-predictions oba-gtfsrt oba-monitor.timer nginx`.

## Current tuning captured (this commit)
- inference `-Xmx30g` + ingestion deadband `minMeters=10 / minIntervalSec=7 / maxAgeSec=30` ("7 s-while-moving"; widened from 5 s on 2026-07-22) + stale-fix load-shedding `oba.shed.maxAgeSec=50` — `run-inference.sh`
- predictions `-Xmx10g`, gtfsrt `-Xmx6g`, Mongo WT cache 6 GB
- prediction weights `20/40/40` (SSM `/oba/predictions/weights`)
- **Caveat:** at AM/PM rush (~4,800+ vehicles) 32 vCPU can't hold true 5 s — hence the 7 s deadband + `oba.shed.maxAgeSec=50` load-shedding backstop (bounds lag, no OOM). Tune `minIntervalSec` / `shed.maxAgeSec` in `run-inference.sh` (+ `systemctl restart oba-inference`) or resize up. Tracked by the `oba-nyc-prod` CloudWatch dashboard.
