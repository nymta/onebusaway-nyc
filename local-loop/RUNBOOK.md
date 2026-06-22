# OBA-NYC Local Prediction Loop — Runbook

Run the full **MTA Bus Time** pipeline on a laptop, with **no broker, no cloud, no TDM**: feed
custom GPS for any vehicle and watch real-time **arrival/departure predictions** come out as GTFS-RT.

```
   you ──HTTP inject──▶  INFERENCE webapp  ──ZMQ 5566──▶  PREDICTIONS webapp  ──ZMQ 5568──▶  observer
   (lat,lon,dsc,time)    (particle filter,                (schedule prognosis,              (decoded
                          map-match to trip)               GTFS-RT TripUpdates)              TripUpdates)
                                ▲                                                                │
                                └──────────────── ZMQ 5568 (predictions back into TDS) ─────────┘
```

Everything below is **local-only** (a dedicated branch + a `.context/` working dir). Nothing here is meant to be committed to OBA-NYC.

---

## 1. What you can do (features delivered)

- **Inject arbitrary GPS per vehicle** (`vehicleId, lat, lon, dsc, time`) over HTTP and have the
  OBA-NYC **inference engine** (particle filter) map-match it to a real NYC trip/block/run, with a
  snapped position and schedule deviation (`IN_PROGRESS`, `DEADHEAD_*`, etc.).
- **Real arrival/departure predictions**: the **predictions engine** consumes each inferred location,
  runs its schedule-based prognosis against the bundle (+ Mongo for historical, empty = schedule-only),
  and emits **GTFS-RT `TripUpdate`s** with per-stop predicted times.
- **Multiple vehicles on multiple routes at once**, each producing its own diverging predictions
  (demonstrated: M1 on Madison Ave + M15 on 1 Av → distinct trips, stops, and times).
- **Real, current MTA data**: built from your Manhattan **B6 (Apr 2026) GTFS + STIF** pick (agency `MTA NYCT`).
- **Closed loop**: predictions are also fed **back into the inference TDS** (loop-back consumer), so the
  inference engine's transit-data service holds the live predictions (queryable by downstream OBA webapps).
- **Broker-less**: the two ZeroMQ legs rendezvous directly on localhost (no `queue-broker`).

---

## 2. Components & ports

| Component | What it is | Port(s) | How it's run |
|---|---|---|---|
| **Inference webapp** | `onebusaway-nyc-vehicle-tracking-webapp` (2.44.39) — particle-filter inference | HTTP **8081**; PUB→**5566**; SUB←**5568** | Eclipse Jetty 9 (`jetty-maven-plugin`) |
| **Predictions webapp** | `onebusaway-nyc-predictions-webapp` (`develop`, 1.4-SNAPSHOT) — prognosticator | HTTP **8082** `/api`; SUB-bind **5566**; PUB-bind **5568** | Eclipse Jetty 9 |
| **MongoDB** | predictions persistence (historical lattice) | **27017** | Docker `mongo:4.4` (container `oba-mongo`) |
| **Bundle** | compiled transit graph (`2026Apr_Manhattan_B6`) | — | loaded by both webapps from `.context/manhattan-bundle/transit-data-bundle/` |
| **Observer** | pyzmq GTFS-RT subscriber | connects 5568 | `obs_time.py` |

Queues (broker-less; **predictions binds, the other side connects**):
- **5566** `inference_queue` — inferred locations (inference PUB-connect → predictions SUB-bind).
- **5568** `time` — GTFS-RT predictions (predictions PUB-bind → observer + inference-loopback SUB-connect).
- 5569 `persistence_queue`, 5570 `predictions_debug` — predictions-side lattice/debug legs (bound, unused locally).

---

## 3. Prerequisites

- **JDK 11** (`/opt/homebrew/opt/openjdk@11/...`). JDK 17/21 break Spring 5.2 / old Jetty.
- **Maven** (resolves `repo.camsys-apps.com` **anonymously** — no settings.xml creds needed).
- **Docker Desktop** running (for Mongo).
- Repos already built into `~/.m2` (see §4). Branch `rsen/local-gps-predictions` (same branch name in both repos). The large/proprietary bits — the built bundle, GTFS+STIF — stay under the git-ignored `.context/`; only these scripts + runbook are version-controlled.
- `python3` + `pip install --user pyzmq gtfs-realtime-bindings` (for the observer).

---

## 4. One-time build (already done)

```bash
# main repo (inference + all OBA-NYC modules), JDK 11
cd /Users/ranajays/conductor/workspaces/onebusaway-nyc/san-francisco
JAVA_HOME=<jdk11> mvn -P skip-integration-tests -DskipTests -B -fae install

# predictions repo, develop branch
cd /Users/ranajays/Documents/git/onebusaway-nyc-predictions   # git checkout develop
JAVA_HOME=<jdk11> mvn -DskipTests -Dlicense.skip=true -B -fae install -pl '!onebusaway-nyc-predictions-integration-tests'
```
Version note: main stays at **2.44.39**, predictions on **develop** (parent 2.47.3-SNAPSHOT). The
`NycQueuedInferredLocationBean` JSON wire contract is identical across the two, so they interoperate unchanged.

### The bundle
Built from the Manhattan B6 GTFS+STIF you provided, via `FederatedTransitDataBundleCreatorMain`:
config `.context/manhattan-bundle/bundle-2026Apr_Manhattan_B6.xml` (one `GtfsBundle`, `defaultAgencyId=MTA NYCT`,
`StifImportTask` over `.context/manhattan-bundle/stif/`). Output:
`.context/manhattan-bundle/transit-data-bundle/2026Apr_Manhattan_B6/` (116 MB; `TransitGraph.obj`, `CalendarServiceData.obj`, indices).
Its GTFS calendar covers "today", so it activates with **no date hack**. To rebuild or swap a different pick, see §9.

---

## 5. Quick start

```bash
cd local-loop          # tracked at the main-repo root
./start-all.sh        # Mongo + predictions + inference (detached; idempotent; ~1-4 min)
./status.sh           # show what's up
./inject-multi.sh     # drive 3 vehicles on 3 routes
./observe.sh 90       # watch decoded GTFS-RT predictions on :5568 for 90s
./stop-all.sh         # stop webapps (add --mongo to also remove the container)
```

`start-all.sh` launches each webapp **detached** (`nohup … & disown`) so they survive the launching
shell exiting, waits until the queues bind / the bundle loads, then prints status.

---

## 6. Injecting GPS

`inject.sh "<vehicleId>" <dsc> <lat,lon> [<lat,lon> …]` sends a **sequence** of fixes (45 s apart in
device time, ending ~now; 2 s apart in wall-clock).

```bash
./inject.sh "MTA NYCT_701" 1010 \
  40.753349,-73.978759 40.755236,-73.977436 40.757739,-73.975556 40.761496,-73.972818
```

**Recipe for a fix that actually map-matches (IN_PROGRESS):**
1. **A sequence, not one point** — the particle filter needs successive observations to converge.
2. **Realistic spacing** — ~0.15–0.4 km per 45 s (≈12–30 km/h). Big jumps (>~1 km/fix) read as
   impossible speed → the filter rejects → `DEADHEAD`. (This is why coarse coords fail.)
3. **A real DSC** for the route — from the STIF **sign-code layer** (`35<dsc>  <route>  <text>` lines):
   `1010`=M1 Harlem-via-Madison, `1150`=M15 via 1 Av, `1420`=M42 crosstown, `2040`=M104 via Broadway, etc.
   ```bash
   grep '^35' .context/manhattan-bundle/stif/stif.m_0001__.*.wkd.open   # M1 sign codes
   ```
4. **On-route coordinates**, ideally mid-trip (a bus parked at a terminal infers `DEADHEAD_BEFORE`,
   which is correct). Pull them from STIF geography records (`15… <lat> <lon>`, divide by 1e6):
   ```bash
   grep 'MADISON AV' .context/manhattan-bundle/stif/stif.m_0001__.*.wkd.open | grep -oE '40[0-9]{6} +-7[34][0-9]{6}'
   ```
5. **Timestamps near now** (`getBestTimestamp` ignores device time >30 min from receive time; the loop
   honours injected `time` because of the controller patch in §8). `inject.sh` handles this automatically.

`inject-multi.sh` drives **701=M1 / 702=M15 / 703=M42** concurrently. Note: a vehicleId's particle
filter retains state, so to re-run cleanly use **fresh ids** or restart the inference webapp.

---

## 7. Observing predictions

```bash
./observe.sh 120                         # decoded TripUpdates from :5568
# or raw: python3 obs_time.py 5568 time 120
```
Each injected fix yields an updated `TripUpdate` for that vehicle, with the remaining-stop count
shrinking as it advances. Example (two vehicles, two routes):
```
trip=…M7_208  route=MTA NYCT_M1   vehicle=MTA NYCT_701  stops=36   stop=400031 arr=…  stop=400032 arr=…
trip=…M15_213 route=MTA NYCT_M15  vehicle=MTA NYCT_712  stops=29   stop=401699 arr=…  stop=401701 arr=…
```

**Loop-back (predictions → inference TDS):** the inference webapp also subscribes to `:5568` and ingests
the predictions into its transit-data service. Proof in its log:
```
TimeQueueListenerTask: time prediction input queue listening on localhost:5568, queue=time
QueueListenerTask:     timePrediction input queue: processed N messages …
```
The inference webapp itself has **no browsable predictions endpoint** (its TDS is exposed only over the
Hessian RPC `/transit-data-service`); a downstream `onebusaway-nyc-api-webapp` (SIRI) or
`onebusaway-nyc-gtfsrt-webapp` pointed at that TDS is what renders them in a public API/UI.

---

## 8. Local-only edits that make this work

| File | Change | Why |
|---|---|---|
| `pom.xml` (main, root) | managed `git-commit-id-plugin` `failOnNoGitDirectory=false` | this checkout is a git **worktree** (`.git` is a file); the 2014 plugin aborts otherwise |
| `…/vehicle-tracking-webapp/.../controllers/UpdateVehicleLocationController.java` | add `vlr.setTimeReceived(t)` | debug inject path never set it → `getBestTimestamp` collapsed every record to 0 ("out-of-order") |
| `…/vehicle-tracking-webapp/src/main/resources/data-sources.xml` | seed `inference-engine.outputQueue{Host=localhost,Port=5566,Name=inference_queue}` and `tds.timePredictionQueue{Host=localhost,OutputPort=5568,Name=time}` + `display.useTimePredictions=true` via `MethodInvokingFactoryBean.updateConfigurationMap` on bean **`configurationService`**, with `depends-on` | feed queue endpoints with no TDM; wire the loop-back consumer |
| `…/predictions-webapp/.../application-context-webapp.xml` | `Dummy`ConfigurationServiceImpl, `MonitoringServiceNoOpImpl`, and **bind** all 4 queue beans (5566/5568/5569/5570) | run with no TDM/AWS; broker-less means one side must bind (else `UnknownHostException: null`) |
| `…/predictions-integration-tests/pom.xml` | added a `graph-builder-execution-2026Apr_Manhattan_B6` exec (now superseded by direct `java` build) | bundle build scaffolding |

Run-time switches (no file edits): `-P local-ie-testing`, `-Die.output.queue=OutputQueueSenderServiceImpl`,
`-DtimePredictions.status=ENABLED`, `-Dbundle.location=…`, `-Dmongohost/port/user/pwd`, `-DCloudWatchKey/Secret=x`,
`-Dorg.onebusaway.nyc.tdm.bundle.batchmode=true`, and **Eclipse `jetty-maven-plugin:9.4.51`** (not the pom's mortbay Jetty 6).

---

## 9. Gotchas / why things are the way they are

- **Eclipse Jetty 9, not mortbay Jetty 6.** The pom's `maven-jetty-plugin` is Jetty 6 = Servlet 2.5;
  Spring 5.2 calls `HttpServletResponse.getStatus()` (Servlet 3.0) → every request 500s. Jetty 9.4 = Servlet 3.1.
  Both webapps serve at context root `/`.
- **Mongo 4.4, not 6.** `mongo-java-driver 2.14` uses the OP_QUERY wire protocol removed in Mongo 5.1.
  `mongo:4.4` is arm64-native and still supports it. Empty Mongo is fine (default weights = schedule 100).
- **Bundle date.** A bundle whose GTFS calendar covers today activates automatically. For an out-of-range
  bundle (e.g. the 2015 Staten Island test set) add `-Dorg.onebusaway.nyc.tdm.bundle.batchmode=true` and
  inject with in-range timestamps. (We pass batchmode anyway as a belt-and-suspenders.)
- **`500` on inject is harmless** — it's the JSP view failing to render under Jetty 9; the controller runs
  inference *before* the view, so the record is processed.
- **`DEADHEAD` instead of `IN_PROGRESS`** → bad inject data: coords off-route, fixes too far apart
  (impossible speed), DSC not valid for that route at that time, or vehicle at a terminal. See §6.
- **Webapps disappear** → they were killed; just `./start-all.sh` again (idempotent).
- **Rebuild/swap the bundle** (different borough/pick): drop GTFS+STIF under `.context/<name>/`, write a
  `bundle-<name>.xml` (1 `GtfsBundle` with `defaultAgencyId=MTA NYCT` + `StifImportTask`), then
  `java -Xmx4g -Dlog4j.configuration=file:/tmp/log4j.properties -cp "$(cat /tmp/bundle-cp.txt)" org.onebusaway.transit_data_federation.bundle.FederatedTransitDataBundleCreatorMain <bundle.xml> <outdir>`
  (classpath via `mvn -pl onebusaway-nyc-predictions-integration-tests dependency:build-classpath`).
  Do **not** nest the output dir inside the resources dir (infinite-recursion bug); don't pass
  `-additionalResourcesDirectory` pointing at a parent of the output.

---

## 10. File map (`local-loop/`, tracked at the main-repo root)

| File | Purpose |
|---|---|
| `env.sh` | shared paths/ports/helpers (edit paths here) |
| `start-all.sh` / `stop-all.sh` / `status.sh` | lifecycle |
| `inject.sh` | inject one vehicle's GPS track |
| `inject-multi.sh` | 3 vehicles / 3 routes at once |
| `observe.sh` / `obs_time.py` | decode the GTFS-RT prediction queue |
| `RUNBOOK.md` | this file |

Bundle + builder config live in `.context/manhattan-bundle/`. Webapp logs: `/tmp/oba-ie-jetty.log`,
`/tmp/oba-pred-jetty.log`.
