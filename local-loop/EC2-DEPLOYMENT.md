# OBA-NYC whole-MTA GTFS-RT — EC2 production deployment

Live deployment of the inference → predictions loop (see `RUNBOOK.md` for the local POC and
`PRODUCTIONIZING.md` for the original scoping) on a single EC2 host, serving **GTFS-RT arrival
predictions for the entire MTA bus network**. The purpose is an **A/B test**: our predictions run on
the same codebase and the same raw-GPS AVL source (the ~5 s BusTech feed) that MTA's own
production OBA-NYC uses, so we can compare our arrival predictions against MTA's official GTFS-RT.

Stood up 2026-07-21. **Experimental, best-effort, single host — not an official feed.**

---

## 1. Public endpoints (URLs)

Base host: **`http://ec2-52-70-255-34.compute-1.amazonaws.com`** (Elastic IP `52.70.255.34`, us-east-1). HTTP only — **no TLS yet**.

| URL | Content |
|---|---|
| `…/tripUpdates` | GTFS-RT **TripUpdates** — arrival/departure predictions (the feed for the comparison). ~1.4 MB protobuf. |
| `…/vehiclePositions` | GTFS-RT **VehiclePositions** — inferred bus positions (useful for ground truth/debugging). ~270 KB. |
| `…/alerts` | GTFS-RT Alerts — **empty** (we don't ingest service alerts). |

- **GET/HEAD only.** POST and every other path return 403/404 (see §7). Rate-limited to ~10 req/s per IP.
- **Refresh cadence:** the feed is cached and rebuilt every **~10 s**; poll at ~15–30 s.
- The hostname is stable (derived from the Elastic IP, which survives stop/start/resize).

---

## 2. What a consumer should know

- **Format:** standard **GTFS-realtime v2** protobuf (`google.transit.gtfs_realtime`). Fetch the binary body and decode with any gtfs-realtime bindings (e.g. `gtfs-realtime-bindings`). `?debug=true` is **not** available through the proxy (stripped).
- **ID namespace:** **bare GTFS ids** — `route_id` like `M15`, `BxM1`, `SIM4C`; `stop_id` like `404856`; `trip_id` = bundle trip id. Two agencies: **MTA NYCT** (local, 5 boroughs) and **MTABC** (MTA Bus Company: express + former private lines).
- **✅ trip_id parity (comparison gate — VERIFIED 100% vs public GTFS, 2026-07-21):** emitted `trip_id`s are STIF-style (e.g. `MV_C6-Weekday-SDon-055700_M3_314`) and **are the public-GTFS `trip_id`s** — a live-feed sample matched **2288/2288 `trip_id`, 235/235 `route_id`, 9974/9974 `stop_id`** in the C6 GTFS with **0 trip→route mismatches**. **Join the comparison on `(trip_id, stop_id)`.** (Verified against the public GTFS the bundle was built from; parity with MTA's *official real-time* feed is a separate harness step. Re-check after each pick with `local-loop/verify-parity.py`.)
- **Coverage:** ✅ **whole MTA bus network as of 2026-07-29** — **MTA NYCT** (~210 local routes `B*`, `BX*`, `M*`, `Q*`, `S*` + NYCT express `SIM1C/3C/4C/33C`, `X27/X28`) **and MTABC** (former private lines `Q06`–`Q115`, `B100/B103`, `BX23`… plus MTABC express `QM*`, `BM*`, `BxM*`, `SIM8/10`…). Measured against MTA's own feed the same day: **0 routes present in theirs and missing from ours**, 3,356 vs 3,398 vehicles (NYCT 98.4% / MTABC 97.0% overlap). Two MTABC routes (`BXM18`, `QM32`) are still absent. Historically MTABC was entirely missing — its STIF was not in the bundle, so there was no DSC→trip map or run data and inference generated zero candidate blocks; the STIF was added and the bundle rebuilt on 2026-07-29. **MTABC predictions are schedule-dominant until Mongo history accumulates** (see Prediction weights below).
- **Freshness:** the BusTech source is **~5.6 s/bus and is shared with MTA** — we do not have finer GPS, we down-sample it less (deadband → ~11 s sampling for a moving bus, ~21 s fleet-mean; MTA down-samples to ~30 s). The number that matters end-to-end: the fix each prediction is anchored to runs **~11–19 s old vs MTA's ~42–48 s**, i.e. **~2.4× fresher**. ⚠️ **History:** on 2026-07-29 the whole-MTA fleet (+30%) pushed inference past peak capacity and that anchor age hit **69 s at peak** — worse than MTA's — roughly doubling near-term ETA error. **Resized to c7i.12xlarge on 2026-07-30 to fix it** (`FINDINGS-SUMMARY.md` §2d); off-peak is verified healthy, **peak still to be confirmed**. Note this failure was invisible in `FeedStalenessSeconds`, which tracks `header.timestamp` (~5 s throughout) rather than `vehicle.timestamp`.
- **Prediction weights:** **20 % schedule / 40 % historical / 40 % recent**. Historical + recent need Mongo **warm-up (days→weeks)**; until history accumulates the output is closer to schedule-dominant.
- **Schedule pick:** built from the **C6** pick (effective ~2026-06-28). Must be refreshed each pick (~quarterly) or trips go stale.

---

## 3. Architecture (topology)

```
BusTech RabbitMQ (whole-MTA AVL, ~5 s/vehicle, amqps)
  │  RabbitMqInputQueueListenerTask (in-process AMQP; creds from SSM; acceptAllVehicles=true)
  │  + ingestion DEADBAND (drop redundant near-stationary fixes — see §4)
  ▼
INFERENCE  vehicle-tracking-webapp :8081   (particle filter, one per vehicle — the CPU-heavy stage)
  │  PUB inferred locations ─connect→ SimpleBroker (SUB bind :5566 / PUB bind :5567)
  │                                          fan-out :5567 ─┬─► PREDICTIONS   :8082
  │                                                         └─► GTFS-RT app   :8083
PREDICTIONS predictions-webapp :8082  → writes observed link times to Mongo (LinkTravelTimes, AggregateLinkTimes)
  │  PUB GTFS-RT "time" (bind :5568) ─┬─► inference loop-back consumer (feeds predictions into the TDS)
  │                                   └─► GTFS-RT app timeInputQueue
GTFS-RT app gtfsrt-webapp :8083  (builds TripUpdates/VehiclePositions from the TDS, cached ~10 s)
  │
PREDICTIONS ARCHIVER predictions-archiver  (SUB :5568 → hourly queuePredictions_*.zip → S3)
  ▼
nginx :80  (GET-only allowlist reverse proxy) ──► public internet
```

All app ports (8081/8082/8083, broker 5566/5567, predictions-time 5568, Mongo 27017) are **localhost/VPC-internal**. The **only** inbound is `:80` → nginx.

---

## 4. Key architectural decisions (and why)

1. **Keep ZeroMQ + the queue-broker internally; RabbitMQ only at ingestion.** The one RabbitMQ touchpoint is `RabbitMqInputQueueListenerTask` (in-process AMQP, no Python bridge). Everything downstream is standard OBA-NYC ZeroMQ → minimal new code.
2. **`SimpleBroker` fan-out.** Inference's inferred-location stream must reach **both** predictions and the gtfsrt-webapp, so the broker (SUB :5566 / PUB :5567) fans it out; predictions + gtfsrt both SUB-connect to :5567.
3. **20/40/40 weights via runtime API, not code.** The shipped predictions webapp uses a Dummy config (defaults 100/0/0). A health-gated `oba-weights` unit reads SSM `/oba/predictions/weights` and `POST /api/weight`s it after predictions is up; re-applied on every restart (the override resets on restart). Tunable via the GitHub Action `set-weights` mode.
4. **Whole-MTA single bundle.** One transit-data bundle covering 5 NYCT borough GTFS + `GTFS_MTABC` (agency `MTABC`), with NYCT STIF (no MTABC STIF was provided — **live comparison 2026-07-22 disproved the assumption that MTABC would match from its GTFS alone**: with no MTABC STIF there is no DSC sign-code map or run data, so inference generates zero candidate blocks for MTABC vehicles and none ever match — see `feed-comparison-report.md`). Built with `FederatedTransitDataBundleCreatorMain`; lives in `/data/oba-bundle/2026C6_wholeMTA` (~675 MB).
5. **Single host, compute-optimized.** The workload is **CPU-bound** (particle filter), not memory-bound → c-family (c7i) not r-family. Currently **c7i.12xlarge** (48 vCPU; upsized from 8xlarge/32 vCPU on 2026-07-30 — §9).
6. **Ingestion deadband to fit whole-network on one box.** `InputServiceImpl.passesDeadband` (default off; enabled via `-Doba.deadband.*`) processes a fix only if the bus **moved ≥ `minMeters`** since the last kept fix, with a `minIntervalSec` rate cap + `maxAgeSec` staleness failsafe. This drops redundant near-stationary pings while keeping fine cadence when moving. **Current: `minMeters=10, minIntervalSec=7, maxAgeSec=30`** ("7 s while moving, ~30 s while stopped"; widened from 5 s on 2026-07-22 after a day of metrics showed the 5 s peak pinned at the shed ceiling — see §9). Measured effect: 889 fixes/s in → **286/s admitted (32%)**, i.e. ~11 s sampling for a moving bus and ~21 s fleet-mean — **not 4× finer than MTA's ~30 s as previously claimed here, closer to 1.4× on cadence and 2.4× on published anchor age**. Avoided a partitioned multi-box fleet.
7. **SSM-only admin, no SSH.** Security group has no port 22; all admin via AWS SSM (Session Manager / Send-Command). Secrets in SSM Parameter Store. Nothing tied to a changing source IP.
8. **nginx GET-only allowlist in front.** The gtfsrt-webapp also exposes injection POSTs (`/input/*`) and Hessian remoting (`/transit-data-service`, `/configuration-service`) on :8083. nginx forwards **only** GET/HEAD on the 3 read feeds and 404s the rest, so those stay unreachable from the internet.
9. **Deploy via GitHub Action → SSM (no inbound SSH).** OIDC-assumed IAM role runs `/opt/oba/deploy.sh` over SSM.
10. **Stale-fix load-shedding (peak safety net).** `VehicleLocationInferenceServiceImpl.ProcessingTask` skips the particle-filter update for a queued fix whose **queue-age** (`now − timeReceived`) exceeds `-Doba.shed.maxAgeSec` (default 0 = off; **50 s here**). Under peak overload it drains the stale tail so the engine stays caught up to real time and never OOMs — trading coverage (some fixes skipped) for freshness. Complements the deadband (which trims inflow); shedding drops the backlog inflow still can't cover. Like the deadband, it is **default-off in code and enabled only via the launch flags** on this host. (Validated 2026-07-21: at a 5 s test threshold, backlog fell 3.8k→2.2k while feed age held ~8 s.)

---

## 5. Infrastructure inventory (AWS acct 032610139471, us-east-1)

- **EC2:** `i-0386b6bb8338b2f67` (`oba-nyc-prod`, **c7i.12xlarge** 48 vCPU/96 GB — upsized 2026-07-30, Amazon Linux 2023). **EIP 52.70.255.34.** 30 GB gp3 root + **500 GB gp3** data volume at `/data` (Mongo + bundle; `DeleteOnTermination=false`).
- **Security group** `sg-0fca012b2afe2a1ee`: inbound **:80 from 0.0.0.0/0** only. Everything else closed.
- **IAM:** instance profile `oba-nyc-ec2-profile` / role `oba-nyc-ec2-role` (SSM core + read `/oba/*` + write `/oba/predictions/weights` + read the S3 bundle bucket). Deploy role `oba-nyc-gha-deploy` (GitHub OIDC → `ssm:SendCommand` on this instance).
- **SSM Parameter Store:** `/oba/rabbitmq/*` (feed creds — SecureString user/pass), `/oba/predictions/weights` = `20/40/40`.
- **S3:** `oba-nyc-bundles-032610139471` (bundle transfer: `2026Jun_Manhattan_C6/`, `wholeMTA-C6-src/`).
- **Repos** (push to `nymta` only): `nymta/onebusaway-nyc` @ `rsen/ec2-productionize-gtfs-rt`; `nymta/onebusaway-nyc-predictions` @ `rsen/local-gps-predictions`. Cloned on the host under `/opt/oba` via per-repo read-only deploy keys.

---

## 6. Host layout & operations (all via SSM)

Host `/opt/oba` (user `oba`): the two repo clones, wrapper scripts `run-{broker,inference,predictions,gtfsrt}.sh`, `set-weights.sh`, `deploy.sh`, `monitor.sh`, `env-common.sh`. **A committed snapshot of all host config (scripts, units, nginx, bundle config) lives in [`local-loop/ec2/`](ec2/).**

**systemd units** (Restart=always): `oba-broker` → `oba-inference` → `oba-predictions` → `oba-gtfsrt` → `oba-predictions-archiver`, plus oneshot `oba-weights` (PartOf predictions) and **`oba-monitor.timer`** (every 2 min → CloudWatch, see §8). Mongo runs as the `oba-mongo` Docker container (`mongo:4.4`, WT cache 6 GB, bound 127.0.0.1). `nginx` is a system service.

- **Deploy:** GitHub Action `.github/workflows/deploy.yml` (`workflow_dispatch`, modes `deploy` | `set-weights`) → OIDC role → `aws ssm send-command` runs `/opt/oba/deploy.sh`. `deploy.sh deploy [<main-ref> [<predictions-ref>]]` git-pulls/checks out both repos, rebuilds broker + vehicle-tracking(+webapp) + gtfsrt(+webapp) + predictions-common(+webapp), restarts, smoke-checks.
- **⚠️ Restart the WHOLE chain, in order. Do not restart `oba-gtfsrt` on its own.** Measured the hard way on 2026-07-30: restarting only `oba-predictions` + `oba-gtfsrt` (to avoid re-converging the fleet mid-peak) left gtfsrt subscribed to the broker but **receiving nothing** — it logged `queue listening on localhost:5567` and a started ReadThread, yet published **0 entities for ~9 minutes** while inference was demonstrably healthy the whole time (`shedStaleTotal=0`, 4,955 active vehicles). Restarting `oba-broker` → `oba-inference` → `oba-predictions` → `oba-gtfsrt` in order restored it in ~2 min. The subscription does not survive a gtfsrt-only restart, so **the "less disruptive" partial restart is actually more disruptive** — a full-chain restart costs ~2 min plus fleet re-convergence, the partial one cost ~10 min of empty feed. `deploy.sh` already does the full chain; do not hand-optimise it.
- **Tune weights:** `deploy.sh set-weights S H R` (validates sum=100, updates the SSM param, live-POSTs) — no restart.
- **Tune the deadband:** edit `-Doba.deadband.*` in `/opt/oba/run-inference.sh`, `systemctl restart oba-inference` (no rebuild). Widen `minMeters`→15 or `minIntervalSec`→7 to shed load; lower for finer cadence.
- **Tune load-shedding:** edit `-Doba.shed.maxAgeSec` in `/opt/oba/run-inference.sh` + `systemctl restart oba-inference`. Lower (e.g. 30 s) → tighter freshness cap + more shedding; `0` disables it.
- **Predictions S3 archiver:** `oba-predictions-archiver` taps ZMQ `:5568` and uploads hourly `queuePredictions_*.zip` to `s3://mtalirr/oba-ec2-predictions/`. Optional SSM creds: `/oba/predictions/s3-archive/{access_key_id,secret_access_key}`. See [`local-loop/ec2/README.md`](ec2/README.md).
- **Resize instance:** stop → `modify-instance-attribute --instance-type` → start (EBS + EIP persist, so the public hostname is unchanged). **Measured 2026-07-30: only ~1 min 50 s of feed outage** (EC2 stop→running 53 s; feed serving 46 s after `running`; full fleet coverage back ~6 min later) — much cheaper than the 10–20 min previously assumed here, so an overnight window is *not* required, though a midday trough still minimises the re-convergence cost. **Stop the OBA units and `docker stop oba-mongo` first** so Mongo closes cleanly (it is 9+ days of irreplaceable prediction history). Heaps are **not** required to change — the workload is CPU-bound and the current 52 GB committed simply gains headroom — though Mongo's WT cache 6 → 12 GB is worth taking. ⚠️ **The inference thread pool is `2 + vCPUs × 20`, so it auto-grows to 962 threads on a 12xlarge**, keeping the 20× oversubscription and putting more threads on the unsynchronised O(n²) `_inferenceProcessingThreads` prune — capacity may therefore scale sublinearly. **Planned 2026-07-30 → c7i.12xlarge; see `FINDINGS-SUMMARY.md` §2d for the full runbook.**
- **Refresh the bundle** (new pick): upload GTFS+STIF → S3, pull to `/data/bundle-src`, author `bundle-*.xml`, build with `FederatedTransitDataBundleCreatorMain`, move the new bundle into `/data/oba-bundle`, archive the old one, restart.
- **Interactive admin:** `aws ssm start-session --target i-0386b6bb8338b2f67` (needs the session-manager-plugin locally) or scripted `aws ssm send-command`.

---

## 7. Security posture

- Only `:80` is internet-facing → nginx. nginx (`/etc/nginx/conf.d/oba-gtfsrt.conf`) proxies **only** `GET`/`HEAD` on `/tripUpdates`, `/vehiclePositions`, `/alerts` with **hardcoded backend paths** (client query string dropped → neutralizes `?debug=` text dumps and `?time=` cache-bypass DoS), rate-limited 10 r/s/IP. Everything else → 404; non-GET → 403.
- The app's sensitive endpoints (`POST /input/*` injection, Hessian `/transit-data-service` + `/configuration-service`) are **live on :8083 but unreachable** — the SG never opens 8083 and nginx never forwards to them.
- No inbound SSH. Admin only via SSM (IAM-authenticated). Secrets in SSM Parameter Store, never in git.

---

## 8. Monitoring & dashboard

**CloudWatch dashboard:** https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards/dashboard/oba-nyc-prod

`/opt/oba/monitor.sh` runs every ~2 min (systemd `oba-monitor.timer`) and publishes custom metrics to CloudWatch namespace **`OBA/Prod`** (dimension `InstanceId`):

| metric | meaning | alarm threshold |
|---|---|---|
| `ServicesDown` | # of {broker,inference,predictions,gtfsrt} + mongo + nginx not up | ≥ 1 (missing data → breaching) |
| `FeedStalenessSeconds` | `now − GTFS-RT header.timestamp` (feed age) | > 120 |
| `TripUpdateEntities` | entity count in `/tripUpdates` (feed populated) | < 100 |
| `DataDiskUsedPct` | `/data` used % (Mongo growth) | > 85 |
| `InferenceBacklogThreads` | inference queue backlog (divergence signal) | > 25000 |
| `InferenceShedFixes` | stale fixes dropped per ~2-min interval (load-shed volume; §4.10) | > 10000 |
| `CPUUtilization` (AWS/EC2, built-in) | instance CPU | > 90% for 15 min |

- **7 alarms** (name prefix `oba-`) are defined but currently have **no notification action** — they only drive red/green status on the dashboard. Wiring email/Slack is a one-liner later: create an SNS topic and `aws cloudwatch put-metric-alarm --alarm-actions <topic-arn>`.
- Signals used (there is no built-in `/health`): GTFS-RT `header.timestamp` (= last successful 10 s refresh), `systemctl is-active`, the mongo container state, and the inference WARN "outstanding threads not reaped" line via `journalctl`.

> **Live observation (2026-07-21):** the dashboard first showed the 5 s-while-moving deadband **diverging at PM peak** (~4,840 vehicles: `InferenceBacklogThreads` 23.7k→24.2k, load ~28/32) — fits off-peak (~3,600) but not peak on 32 vCPU. **Load-shedding (§4.10) was then added to cap it:** at peak the queue drains stale fixes instead of growing unbounded, holding lag ≤ ~50 s and preventing OOM. Watch `InferenceShedFixes` (drop volume) alongside `InferenceBacklogThreads` at peak.

---

## 9. Known limitations / caveats

- **trip_id parity** with public GTFS is **verified 100%** (see §2); ✅ parity with MTA's *official real-time* feed verified 2026-07-22 (`compare-feeds.py`): 97.3% identical trip_id on shared vehicles, median prediction delta +2 s, MAE 53 s.
- **No MTABC coverage** (see §2 ⚠️): the whole MTABC fleet (~440 vehicles, 74 routes) is missing until MTABC STIF is obtained and the bundle rebuilt.
- **Single host = SPOF** (fine for an experiment).
- **HTTP only** (no TLS) and **publicly readable**.
- **On-demand pricing** (~$1,564/mo since the 2026-07-30 upsize to c7i.12xlarge; was ~$1,042 at 8xlarge; no commitment).
- **Warm-up:** historical/recent weights need days–weeks of Mongo data before they meaningfully help.
- **Mongo disk growth:** `LinkTravelTimes` archives every observation → `/data` will fill over time (no TTL yet).
- **Peak headroom — exceeded 2026-07-29, addressed by the 2026-07-30 resize; PM-peak confirmation still pending.** Adding the MTABC fleet on 07-29 took the peak fleet from ~2,670 to ~3,480 TripUpdates (+30%) and pushed inference past capacity: CPU 57 → 74%, `InferenceBacklogThreads` 1.5k → 15k, and `InferenceShedFixes` went from **0 across all recorded history** to ~3.9k per 2-min interval at every peak. Shedding did its job (bounded queue, no OOM) but the resulting **anchor age ~69 s was worse than MTA's own feed**, costing ~2× near-term ETA accuracy. Measured with `simulate-deadband.py` (replaying the real AVL archive): inbound **889 fixes/s**, deadband admits **286 fixes/s**, and the 32-core sustainable ceiling was **~254–270 fixes/s** — over by ~6–12%. **Resolved by resizing to c7i.12xlarge (48 vCPU) on 2026-07-30**, which raises the estimated ceiling to **~380–405 fixes/s** and left the deadband at 7 s so no GPS cadence was given up. Off-peak is verified healthy (anchor age 16–19 s vs MTA's 39–48 s); **the PM peak has not yet been measured** — check `InferenceShedFixes` returns to 0 and `analyze-anchor-age.py` reads ~15 s.
  - **If shedding returns at peak, do not resize again.** Two software inefficiencies remain and neither scales with cores: the inference thread pool is `2 + vCPUs × 20` (**962 threads on 48 vCPU** — a 20× oversubscription for CPU-bound work, Spring-settable only), and `InferenceBacklogThreads` is `_inferenceProcessingThreads.size()`, an unsynchronised `ArrayList<Future>` of *every* submitted task pruned only above 3,000 via O(n) removals — so past 3,000 it runs a full scan on every registration (~500/s). Fixing those raises the ceiling for free. **Also note that metric is a saturation flag, not a queue depth** (healthy = 0–3,000 sawtooth).
  - **Deadband tuning reference** (always test a candidate with `simulate-deadband.py` first — demand falls in *steps*, because the rate cap quantizes against the 5.6 s source: 7 → 10 s cuts only 2.4%, while 7 → 12 s cuts 17%). Anchor age ≈ half the sampling interval + ~4 s transport, so cadence changes are cheap in freshness terms. Current 7 s admits 286/s; **5 s admits 405/s and would realise the full 5.6 s source rate for moving buses** — attractive but at the top of the new ceiling, so only after the PM peak is confirmed healthy. Loosening to 12–15 s admits 226–236/s if headroom is ever needed again. Changing `oba.shed.maxAgeSec` does **not** change how much is dropped (in steady state that is arrival minus service rate) — only how long survivors wait.
- **Monitoring gap (open):** no metric observes *anchor* age (`vehicle.timestamp`), only *feed* age (`header.timestamp`) — which is why the above ran a full day unnoticed. Add `PositionAnchorAgeSeconds` to `monitor.sh` with an alarm at > 45 s.
- **Host config** is **now committed** to [`local-loop/ec2/`](ec2/) (scripts, systemd units, nginx, bundle config; secrets excluded) — no longer host-only.
- **GitHub Action:** `workflow_dispatch` only becomes runnable once `deploy.yml` is on the repo **default branch**.

See §9 of the memory note (`obanyc-ec2-production-deployment`) for the current live tuning values and command recipes.

---

## 10. Cost snapshot (us-east-1, on-demand, 730 h/mo)

| Item | ~$/mo |
|---|---|
| c7i.12xlarge (48 vCPU) — **current since 2026-07-30** | ~$1,564 |
| c7i.8xlarge (32 vCPU) — previous | ~$1,042 |
| 500 GB gp3 + EIP | ~$44 |
| **Total (current, on-demand)** | **~$1,086** |
| 1-yr RI on the instance instead | ~$690 + $44 = **~$734** |
| Downsize to c7i.4xlarge (needs the 10 s-while-moving deadband) | ~$521 + $44 |

The ingestion deadband is what makes a single small box viable; without it, full-5 s whole-network needs ~48–64 vCPU (~$1,560–2,085/mo) or a partitioned Spot/Graviton fleet.

---

## 11. Follow-ups

**Independent of the TLS / IP-lockdown / RI decisions — these matter regardless:**
- ✅ **Verify trip_id parity** — done (100% vs public GTFS; `local-loop/verify-parity.py`, §2).
- ✅ **Basic monitoring** — done (CloudWatch `OBA/Prod` metrics + `oba-nyc-prod` dashboard + alarms; §8).
- ✅ **Commit the host config** — done ([`local-loop/ec2/`](ec2/)).
- **Obtain MTABC STIF + rebuild the bundle** — without it the entire MTABC fleet never matches (§2 ⚠️, `feed-comparison-report.md`).
- **Config parity vs prod (verified against a prod TDM config dump, 2026-07-23):** prod prediction weights are **20/40/40 — identical to ours**, and every other `predictions.*` tuning key matches the defaults we run, **except `predictions.PredictionLevel=NEXT_TRIP`** (we run the default `CURRENT_TRIP`; this is why MTA emits ~1.86 TripUpdates/vehicle — current + next trip — vs our 1.00). Set `PredictionLevel=NEXT_TRIP` to match prod feed shape. Prod also consumes sources we don't: **CAPI cancelled trips** (`http://capi.prod.obanyc.com/api/canceled-trips.json`; inference skips cancelled trips when matching), a **ghost-bus/unassigned-vehicle filter** (`https://bustrek.mta.info/api/ghostbus/records`), **APC occupancy** (`https://apc-bustrek-store.s3.amazonaws.com/latestdata.json` — publicly readable), and TDM assignment feeds: depot = `https://fleetview.mtabuscis.net/api/legacy/prservices`, vehicle pull-in/out = `s3://automatic-dob-uploads/yardtrek/runs-last-24-hours.json` (needs a cross-account S3 grant; crew assignment remains a UTS `CIS_*` file drop).
- **Build the comparison/scoring harness + ground truth** (PRODUCTIONIZING §5). First step done: `compare-feeds.py` does live pattern comparison vs MTA's official feed (`http://gtfsrt.prod.obanyc.com/tripUpdates`, public, no key); ground-truth scoring still open.
- **Mongo growth:** add a TTL index / archival on `LinkTravelTimes` before `/data` fills.
- **Bundle-refresh pipeline** for the next pick (~quarterly STIF).
- **Put `deploy.yml` on the default branch** so the GitHub Action is dispatchable.
- **Alerting (optional):** wire the §8 alarms to an SNS topic → email/Slack if you want push notifications.
- Let Mongo **warm up** before trusting historical/recent weighting.

**If we DON'T add TLS (stay HTTP):**
- Fine for a server-side poller (our harness). Data is public, so confidentiality isn't the issue; the gap is integrity/authenticity — a network MITM could tamper with the feed (low likelihood, but it would corrupt an accuracy comparison). Browser/webapp consumers hit mixed-content blocking. **Follow-up:** none required for a curl-based harness; keep the easy TLS path in reserve (Caddy + a free `sslip.io` hostname → auto Let's Encrypt, or a domain you own → EIP).

**If we DON'T lock down source IPs (stay world-open):**
- **Egress/bandwidth:** `/tripUpdates` is ~1.4 MB; each poll = ~1.4 MB out. Our own harness (~2–4 polls/min) ≈ 250–500 GB/mo ≈ ~$15–40/mo. If the URL is discovered and widely polled, egress can balloon (per-IP rate-limit caps a single client, not the total). **Follow-up:** watch `/var/log/nginx/access.log` + CloudWatch egress; be ready to restrict `:80` to the harness CIDR (one SG command) if abused.
- **Patching:** nginx + the OS are now internet-facing. **Follow-up:** enable automatic security updates (`dnf-automatic`) / patch periodically. (The app's dangerous endpoints are shielded, but nginx itself is the exposed surface.)

**If we DON'T buy a Reserved Instance (stay on-demand):**
- Paying ~$350/mo more than a 1-yr RI (~$1,042 vs ~$690) for full flexibility (resize/stop anytime, no commitment). **Follow-up:** if this runs beyond ~3–4 months, an RI (or a more flexible Compute Savings Plan) pays back — revisit. Alternatively **stop the instance** when not actively collecting (halts compute cost; EBS/EIP persist ~$44/mo) — but that pauses Mongo warm-up and takes the public feed down.
