#!/usr/bin/env python3
# Subscribe to the predictions engine's GTFS-RT "time" queue and print arriving TripUpdates.
# Usage: obs_time.py [port=5568] [topic=time] [seconds=90]
# Needs: pip install --user pyzmq gtfs-realtime-bindings
import sys, time
import zmq

PORT = sys.argv[1] if len(sys.argv) > 1 else "5568"
TOPIC = sys.argv[2] if len(sys.argv) > 2 else "time"
SECONDS = int(sys.argv[3]) if len(sys.argv) > 3 else 90

decode = None
try:
    from google.transit import gtfs_realtime_pb2 as decode
    print("[obs] GTFS-RT decoding available", flush=True)
except Exception as e:
    print(f"[obs] no gtfs-realtime bindings ({e}); showing raw payloads", flush=True)

ctx = zmq.Context()
s = ctx.socket(zmq.SUB)
s.connect(f"tcp://localhost:{PORT}")
s.setsockopt(zmq.SUBSCRIBE, TOPIC.encode())
s.setsockopt(zmq.RCVTIMEO, 2000)
print(f"[obs] SUB tcp://localhost:{PORT} topic={TOPIC!r}; listening {SECONDS}s ...", flush=True)

n = 0
deadline = time.time() + SECONDS
while time.time() < deadline:
    try:
        parts = s.recv_multipart()
    except zmq.Again:
        continue
    n += 1
    topic = parts[0]
    payload = parts[-1]
    print(f"[obs] #{n} topic={topic!r} payloadBytes={len(payload)}", flush=True)
    if decode is not None:
        try:
            fm = decode.FeedMessage()
            fm.ParseFromString(payload)
            print(f"      FeedMessage: entities={len(fm.entity)} ts={fm.header.timestamp}", flush=True)
            for e in fm.entity[:5]:
                if e.HasField('trip_update'):
                    tu = e.trip_update
                    print(f"      trip_update trip={tu.trip.trip_id} route={tu.trip.route_id} vehicle={tu.vehicle.id} stops={len(tu.stop_time_update)}", flush=True)
                    for stu in tu.stop_time_update[:4]:
                        at = stu.arrival.time if stu.HasField('arrival') else 0
                        dt = stu.departure.time if stu.HasField('departure') else 0
                        print(f"          stop={stu.stop_id} arr={at} dep={dt}", flush=True)
        except Exception as ex:
            print(f"      (decode failed: {ex}; first 60 bytes: {payload[:60]!r})", flush=True)
print(f"[obs] done; received {n} message(s)", flush=True)
