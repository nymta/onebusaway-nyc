#!/usr/bin/env python3
"""Bridge the RabbitMQ BusTech stream onto the inference engine's ZeroMQ input queue.

RabbitMQ stream (AMQP) --consume--> [optional filter/rate-cap] --PUB bind tcp://*:IE_INPUT_PORT-->
    OBA PartitionedInputQueueListenerTask (SUB connect) -> InputServiceImpl -> handleRealtimeEnvelopeRecord

Each message is forwarded UNCHANGED (full fidelity: bearing/speed/NMEA preserved). The ZeroMQ frame is
[topic, rawJsonBytes] — topic == inference-engine.inputQueueName seeded in data-sources.xml. The bridge
BINDS its PUB because OBA's base QueueListenerTask SUB always connects.
"""
import json
import os
import signal
import sys
import time

import zmq

import mq_common

PORT = int(mq_common.env("IE_INPUT_PORT", "5563"))
TOPIC = mq_common.env("IE_INPUT_TOPIC", "bustech")
MAX_RATE = float(mq_common.env("BRIDGE_MAX_RATE", "0"))           # msgs/sec; 0 = unlimited
VEH_ALLOW = set(x.strip() for x in (mq_common.env("BRIDGE_VEHICLE_ALLOW", "") or "").split(",") if x.strip())
ROUTE_ALLOW = set(x.strip() for x in (mq_common.env("BRIDGE_ROUTE_ALLOW", "") or "").split(",") if x.strip())
DSC_ALLOW = set(x.strip() for x in (mq_common.env("BRIDGE_DSC_ALLOW", "") or "").split(",") if x.strip())
FILTERING = bool(VEH_ALLOW or ROUTE_ALLOW or DSC_ALLOW)

_stop = False


def _sig(*_):
    global _stop
    _stop = True


def passes_filter(body):
    """True if the message should be forwarded. Only parses JSON when a filter is configured."""
    if not FILTERING:
        return True
    try:
        ccr = json.loads(body)["RealtimeEnvelope"]["CcLocationReport"]
    except Exception:
        return True  # unparseable here — let OBA be the judge of validity
    if VEH_ALLOW:
        v = ccr.get("vehicle", {})
        vid = "%s_%s" % (v.get("agencydesignator"), v.get("vehicle-id"))
        if vid not in VEH_ALLOW:
            return False
    if ROUTE_ALLOW:
        rid = ccr.get("routeID", {})
        if rid.get("route-designator") not in ROUTE_ALLOW:
            return False
    if DSC_ALLOW and str(ccr.get("destSignCode")) not in DSC_ALLOW:
        return False
    return True


def main():
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    cfg = mq_common.load_config()
    ctx = zmq.Context.instance()
    pub = ctx.socket(zmq.PUB)
    pub.setsockopt(zmq.SNDHWM, 100000)
    pub.bind("tcp://*:%d" % PORT)
    print("[bridge] ZeroMQ PUB bound tcp://*:%d topic=%r  (inference SUB connects here)" % (PORT, TOPIC), flush=True)
    if FILTERING:
        print("[bridge] filter: vehicles=%s routes=%s dscs=%s" % (
            sorted(VEH_ALLOW) or "*", sorted(ROUTE_ALLOW) or "*", sorted(DSC_ALLOW) or "*"), flush=True)
    if MAX_RATE > 0:
        print("[bridge] rate cap: %.0f msg/s" % MAX_RATE, flush=True)
    time.sleep(0.3)  # let the PUB settle before first sends

    topic_b = TOPIC.encode()
    fwd = skipped = 0
    min_interval = (1.0 / MAX_RATE) if MAX_RATE > 0 else 0.0
    last_send = [0.0]

    def on_message(body):
        nonlocal fwd, skipped
        if _stop:
            return False
        if not passes_filter(body):
            skipped += 1
            return True
        if min_interval:
            dt = time.time() - last_send[0]
            if dt < min_interval:
                time.sleep(min_interval - dt)
            last_send[0] = time.time()
        pub.send_multipart([topic_b, body])
        fwd += 1
        if fwd % 500 == 0:
            print("[bridge] forwarded=%d skipped=%d" % (fwd, skipped), flush=True)
        return not _stop

    backoff = 1.0
    while not _stop:
        conn = None
        try:
            conn = mq_common.open_connection(cfg)
            ch = conn.channel()
            print("[bridge] consuming stream %r at offset %r -> forwarding" % (cfg["stream"], cfg["offset"]), flush=True)
            backoff = 1.0
            mq_common.consume_stream(ch, cfg["stream"], cfg["offset"], 500, on_message)
        except Exception as e:
            if _stop:
                break
            print("[bridge] connection error: %s — reconnecting in %.0fs (forwarded=%d)" % (e, backoff, fwd),
                  file=sys.stderr, flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            try:
                if conn and conn.is_open:
                    conn.close()
            except Exception:
                pass

    print("[bridge] stopping (forwarded=%d skipped=%d)" % (fwd, skipped), flush=True)
    pub.close(0)
    ctx.term()


if __name__ == "__main__":
    main()
