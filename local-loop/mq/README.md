# Live RabbitMQ feed → inference engine

Connect to the **BusTech / Cambridge Systematics raw-GPS** feed (a RabbitMQ *stream*) and drive the
local inference → predictions loop from real vehicle positions instead of synthetic `inject.sh` tracks.

## Why this is simple

The feed's messages are **already the format OBA consumes**. Each message body is the JSON

```json
{ "RealtimeEnvelope": { "UUID": "...", "timeReceived": 1768183700658,
  "CcLocationReport": { "request-id": ..., "vehicle": {"vehicle-id":7485,"agency-id":2008,"agencydesignator":"MTA NYCT"},
    "time-reported": "2026-01-12T02:08:19.0-00:00", "latitude": 40895045, "longitude": -73880097,
    "direction": {"deg":12.5}, "speed":30, "destSignCode":3162, "localCcLocationReport": {"NMEA": {...}}, ... } } }
```

That is byte-for-byte the `RealtimeEnvelope` → `CcLocationReport` (TCIP) document the inference engine's
input listener already parses (`InputServiceImpl.deserializeMessage`). So **no payload translation** is
needed — only transport. helium-backend consumes the same feed (`BusVehicleReportRaw.fromByteArray`).

Transport gap: OBA's realtime input is **ZeroMQ**; the feed is a **RabbitMQ stream** consumed over
**AMQP 0.9.1** (TLS, `basic_qos`, `x-stream-offset`). These tools bridge the two.

## Files

| file | purpose |
|---|---|
| `mq_env.sh.example` | template for connection details — copy to `mq_env.sh` (git-ignored) and fill in |
| `mq_common.py` | shared pika stream-connection helpers (TLS, offset, qos, manual-ack consume) |
| `verify_stream.py` / `verify.sh` | **Phase A** — read N messages, validate against the RealtimeEnvelope contract, report coverage, capture `samples.jsonl` |
| `verify_with_oba.sh` | optional — replay `samples.jsonl` through OBA's *actual* Java deserializer (byte-level proof) |
| `bridge.py` / `start-bridge.sh` / `stop-bridge.sh` | **Phase B** — forward the stream onto the ZeroMQ input queue the inference engine consumes |
| `viewer.py` / `view.sh` | map viewer — taps :5563 (positions) + :5568 (predictions), resolves stop/route names from the GTFS bundle, serves a Leaflet map at :8090 |

## 1. Connect & verify the format (read-only)

```bash
cd local-loop/mq
cp mq_env.sh.example mq_env.sh      # then edit: host, user, password, stream name
./verify.sh -n 20                    # connects, validates 20 messages, prints a report, writes samples.jsonl
```

`PASS` + "no unknown CcLocationReport keys" means the feed matches what OBA expects. Any `UNKNOWN` key or
schema error is itemized (it would risk a strict-Jackson parse failure on the OBA side).

## 2. Continuously ingest into the loop (full fidelity)

```bash
./start-bridge.sh                    # RabbitMQ stream -> ZeroMQ PUB bind tcp://*:5563 (topic 'bustech')
../status.sh                         # bridge: UP, rising accepted count
python3 ../obs_time.py 5568 time 240 # GTFS-RT TripUpdates for the real vehicles now flowing through
./stop-bridge.sh
```

The bridge re-publishes each message **unchanged**, so bearing/speed/NMEA all reach the particle filter.
It requires the inference webapp to be started with the input queue wired and the depot filter relaxed
(`acceptAllVehicles=true`) — see `../RUNBOOK.md` → "Live RabbitMQ feed".

## 3. View it on a map

```bash
./view.sh                            # http://localhost:8090  (OpenStreetMap tiles)
MAPBOX_TOKEN=pk.xxxx ./view.sh        # Mapbox tiles instead (or put export MAPBOX_TOKEN=... in mq_env.sh)
```

`viewer.py` taps the position feed (:5563) and the GTFS-RT predictions (:5568), joins them by vehicle id, and
resolves stop/route ids to **names + coordinates** from the GTFS bundle (`OBA_GTFS_ZIP`, auto-discovered by
`view.sh`). The map shows a marker per vehicle; click one to plot its upcoming named stops, while the side
panel lists each vehicle's predicted arrivals with live countdowns. Markers need positions, so run the bridge first.

## Offset & volume

- `OBA_MQ_OFFSET` (or `--offset`): `last` (recent, default), `next` (only new), `first`, an integer offset,
  or an interval like `1h`/`7D`.
- The full feed is every NYC bus (~hundreds/sec). For a laptop, cap with `BRIDGE_MAX_RATE` and/or narrow with
  `BRIDGE_DSC_ALLOW` (best — the feed's `route-designator` is an internal code, so filter by destSignCode;
  e.g. `1010,1150,1420,2040` = M1/M15/M42/M104), `BRIDGE_AGENCY_ALLOW` (e.g. `MTA NYCT` to drop MTABC),
  `BRIDGE_VEHICLE_ALLOW`, or `BRIDGE_ROUTE_ALLOW` in `mq_env.sh`.
- Only vehicles whose DSC maps to a trip in the **loaded bundle** (the current Manhattan pick) will map-match and produce
  predictions; others are ingested but yield no `TripUpdate`.

## Security

`mq_env.sh` holds the broker password and is git-ignored; `samples.jsonl` holds live data and is git-ignored.
Never commit either. Only `*.example`, `*.py`, `*.sh`, and this README are tracked.
