#!/usr/bin/env python3
"""Observe the inference engine's inferred-location output on :5566 (Stage 1 of a replay).

Usage: obs_inferred.py [port=5566] [topic=inference_queue] [seconds=120] [outfile]

If outfile is given, every payload is also written there verbatim, one JSON document per line, in
arrival order. compare-replay-runs.py sorts that canonically before diffing - arrival order is not
reproducible, because the stripes publish concurrently, but each vehicle's own sequence is.

This BINDS the socket. OutputQueueSenderServiceImpl calls _socket.connect() on the PUB side
(despite logging "binding to ..."), so the subscriber has to be the one that binds. In the normal
loop the predictions webapp binds :5566; run this instead of predictions, not alongside it.

Messages are two ZeroMQ frames: the topic name, then the NycQueuedInferredLocationBean as JSON.

The acceptance test for the clock retrofit is the recordTimestamp column. Under the replay profile it
must show the archive's date - not today. If it shows today, the record is still being stamped from
the wall clock somewhere on the ingest path.
"""
import datetime as dt
import json
import os
import signal
import sys
import time

import zmq

PORT = sys.argv[1] if len(sys.argv) > 1 else "5566"
TOPIC = sys.argv[2] if len(sys.argv) > 2 else "inference_queue"
SECONDS = int(sys.argv[3]) if len(sys.argv) > 3 else 120
OUTFILE = sys.argv[4] if len(sys.argv) > 4 else None

ctx = zmq.Context()
s = ctx.socket(zmq.SUB)
s.bind("tcp://*:%s" % PORT)               # bind, not connect - see the module docstring
s.setsockopt(zmq.SUBSCRIBE, TOPIC.encode())
s.setsockopt(zmq.RCVTIMEO, 2000)
print("[obs] SUB bind tcp://*:%s topic=%r; listening %ds ..." % (PORT, TOPIC, SECONDS), flush=True)

# buffering=1 is line buffering, and it is load-bearing. The harness stops this process with a
# signal once the engine exits, so anything still in a userspace buffer is lost: with default
# buffering a 279-record run wrote only 252 lines, and the missing 27 looked like dropped ZeroMQ
# messages until the log showed all 279 had in fact arrived.
out = open(OUTFILE, "w", buffering=1) if OUTFILE else None
if out:
    print("[obs] writing payloads to %s (line buffered)" % OUTFILE, flush=True)


def _finish(_signum=None, _frame=None):
    """Close the capture on SIGTERM/SIGINT as well as on the normal timeout."""
    if out and not out.closed:
        out.flush()
        os.fsync(out.fileno())
        out.close()
    sys.exit(0)


signal.signal(signal.SIGTERM, _finish)
signal.signal(signal.SIGINT, _finish)

today = dt.date.today()
n = 0
phases = {}
dates = {}
deadline = time.time() + SECONDS

while time.time() < deadline:
    try:
        parts = s.recv_multipart()
    except zmq.Again:
        continue
    n += 1
    payload = parts[-1]

    if out:
        out.write(payload.decode("utf-8", "replace").replace("\n", " ") + "\n")

    try:
        rec = json.loads(payload)
    except ValueError:
        print("[obs] #%d unparsed, %d bytes: %r" % (n, len(payload), payload[:80]), flush=True)
        continue

    # the bean is published wrapped; accept either shape
    bean = rec.get("NycQueuedInferredLocationBean", rec) if isinstance(rec, dict) else rec

    ts = bean.get("recordTimestamp")
    when = dt.datetime.fromtimestamp(ts / 1000.0) if ts else None
    stamp = when.strftime("%Y-%m-%d %H:%M:%S") if when else "-"
    if when:
        dates[when.date()] = dates.get(when.date(), 0) + 1
    phase = bean.get("phase", "-")
    phases[phase] = phases.get(phase, 0) + 1

    print("[obs] #%-4d %-19s veh=%-16s phase=%-14s trip=%s" % (
        n, stamp, bean.get("vehicleId", "-"), phase, bean.get("inferredTripId") or "-"), flush=True)

if out:
    out.close()
    print("[obs] wrote %d payloads to %s" % (n, OUTFILE), flush=True)

print("\n[obs] %d message(s)" % n, flush=True)
if phases:
    print("[obs] phases: " + ", ".join("%s=%d" % kv for kv in sorted(phases.items())), flush=True)
if dates:
    print("[obs] recordTimestamp dates: "
          + ", ".join("%s=%d" % (d, c) for d, c in sorted(dates.items())), flush=True)
    if all(d == today for d in dates):
        print("[obs] FAIL: every timestamp is today - still on the wall clock", flush=True)
    elif any(d == today for d in dates):
        print("[obs] MIXED: some timestamps are today - a wall-clock read remains on the path",
              flush=True)
    else:
        print("[obs] PASS: no timestamp is today - the engine is running on data time", flush=True)
