#!/usr/bin/env python3
"""Republish the Cambridge Systematics ~28 s filtered AVL queue onto RabbitMQ.

Subscribes to the ZeroMQ PUB feed at queue.staging.obanyc.com:5564 (topic "bhs_queue") and
publishes each message body **verbatim** to a RabbitMQ exchange, so that:

  * a TDM-less OBA host can consume it with the stock RabbitMqInputQueueListenerTask -- only
    inference-engine.rabbitmq.streamName changes, keeping the cadence instance one variable away
    from the other instances; and
  * data-archiver can archive it to s3://mtalirr/data-archiver/<feed>/ , which is what
    run-replay.sh reads (replay is fed from S3, never from a queue).

Only hosts whose egress is an allowlisted Elastic IP can reach the source queue -- oba-nyc-prod
(52.70.255.34) is one, ephemeral-IP hosts time out -- which is why this runs on prod and fans out
over RabbitMQ rather than each instance subscribing directly.

The source is a ZMTP/1.0-era PUB socket, but libzmq 4.x negotiates with it natively (verified
pyzmq 27.2 / libzmq 4.3.5 against this endpoint), so this is a plain zmq.SUB.

Bodies are passed through untouched -- no dedupe, no downsampling, no reformatting -- so the
archived lines stay byte-identical to what the source published and replay stays faithful. The
envelope is parsed only to lift timeReceived for the AMQP timestamp, which is what data-archiver
buckets its 5-minute S3 slots on; without it the slot boundary would follow our publish time.

Environment:
  OBA_CSPUB_ZMQ_HOST        default queue.staging.obanyc.com
  OBA_CSPUB_ZMQ_PORT        default 5564
  OBA_CSPUB_ZMQ_TOPIC       default bhs_queue
  OBA_CSPUB_RMQ_ADDRESSES   host:port[,host:port...] (required)
  OBA_CSPUB_RMQ_USERNAME    (required)
  OBA_CSPUB_RMQ_PASSWORD    (required)
  OBA_CSPUB_RMQ_VHOST       default /
  OBA_CSPUB_RMQ_SSL         default true
  OBA_CSPUB_EXCHANGE        default nyct.bustech.gps-filtered
  OBA_CSPUB_CONFIRMS        default true (per-message broker ack; ~145 msg/s is well inside the
                            ~500+/s a confirmed blocking channel sustains, and a silent publish
                            loss would show up as an unexplained hole in the replay archive)
  OBA_CSPUB_BUFFER_MAX      default 50000 messages (~5 min at 150/s) held across a broker blip
  OBA_CSPUB_LOG_STATS_SEC   default 60
"""
from __future__ import annotations

import json
import logging
import os
import random
import signal
import ssl
import sys
import time
from collections import deque

import pika
import zmq

LOG = logging.getLogger("cs-gps-publisher")

ZMQ_HOST = os.environ.get("OBA_CSPUB_ZMQ_HOST", "queue.staging.obanyc.com")
ZMQ_PORT = os.environ.get("OBA_CSPUB_ZMQ_PORT", "5564")
ZMQ_TOPIC = os.environ.get("OBA_CSPUB_ZMQ_TOPIC", "bhs_queue")
RMQ_ADDRESSES = os.environ.get("OBA_CSPUB_RMQ_ADDRESSES", "")
RMQ_USERNAME = os.environ.get("OBA_CSPUB_RMQ_USERNAME", "")
RMQ_PASSWORD = os.environ.get("OBA_CSPUB_RMQ_PASSWORD", "")
RMQ_VHOST = os.environ.get("OBA_CSPUB_RMQ_VHOST", "/")
RMQ_SSL = os.environ.get("OBA_CSPUB_RMQ_SSL", "true").lower() not in ("0", "false", "no")
EXCHANGE = os.environ.get("OBA_CSPUB_EXCHANGE", "nyct.bustech.gps-filtered")
CONFIRMS = os.environ.get("OBA_CSPUB_CONFIRMS", "true").lower() not in ("0", "false", "no")
BUFFER_MAX = int(os.environ.get("OBA_CSPUB_BUFFER_MAX", "50000"))
LOG_STATS_SEC = int(os.environ.get("OBA_CSPUB_LOG_STATS_SEC", "60"))


def connection_parameters() -> list[pika.ConnectionParameters]:
    """One ConnectionParameters per address, shuffled.

    pika tries the list in order and BlockingConnection retries from the top on reconnect, so
    shuffling spreads publishers across the CloudAMQP cluster nodes the way data-pusher's
    spring.rabbitmq.address-shuffle-mode=RANDOM does.
    """
    creds = pika.PlainCredentials(RMQ_USERNAME, RMQ_PASSWORD)
    ssl_options = pika.SSLOptions(ssl.create_default_context()) if RMQ_SSL else None
    params = []
    for address in (a.strip() for a in RMQ_ADDRESSES.split(",") if a.strip()):
        host, _, port = address.partition(":")
        params.append(
            pika.ConnectionParameters(
                host=host,
                port=int(port) if port else (5671 if RMQ_SSL else 5672),
                virtual_host=RMQ_VHOST,
                credentials=creds,
                ssl_options=ssl_options,
                heartbeat=60,
                blocked_connection_timeout=300,
                connection_attempts=1,
            )
        )
    random.shuffle(params)
    return params


def received_at_ms(body: bytes) -> int | None:
    """RealtimeEnvelope.timeReceived, or None if this is not a body we recognise.

    Used only for the AMQP timestamp; an unparseable body is still published verbatim.
    """
    try:
        return int(json.loads(body.decode("utf8"))["RealtimeEnvelope"]["timeReceived"])
    except Exception:
        return None


class Publisher:
    """A RabbitMQ channel that reconnects, and reports failures to the caller."""

    def __init__(self) -> None:
        self._connection = None
        self._channel = None
        self.connects = 0

    @property
    def connected(self) -> bool:
        return self._channel is not None

    def connect(self) -> None:
        self.close()
        self._connection = pika.BlockingConnection(connection_parameters())
        self._channel = self._connection.channel()
        if CONFIRMS:
            self._channel.confirm_delivery()
        self.connects += 1
        LOG.info("connected to rabbitmq: exchange=%s confirms=%s", EXCHANGE, CONFIRMS)

    def publish(self, body: bytes, ts_ms: int | None) -> None:
        """Publish one body. Raises if the broker did not take it."""
        if self._channel is None:
            raise pika.exceptions.AMQPConnectionError("not connected")
        properties = pika.BasicProperties(content_type="application/json")
        if ts_ms is not None:
            # data-archiver buckets its 5-minute S3 slots on the broker timestamp and falls back to
            # its own wall clock, so carrying the source's timeReceived keeps slot boundaries -- and
            # therefore replay windows -- aligned with the source rather than with our publish time.
            properties.timestamp = ts_ms // 1000
            properties.headers = {"timestamp_in_ms": ts_ms}
        # Fanout-style: empty routing key, matching data-pusher's convertAndSend(exchange, "", body).
        self._channel.basic_publish(exchange=EXCHANGE, routing_key="", body=body, properties=properties)

    def close(self) -> None:
        try:
            if self._connection is not None and self._connection.is_open:
                self._connection.close()
        except Exception:
            pass
        self._connection = None
        self._channel = None


def run() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout
    )
    # pika narrates every connection step at INFO, which buries the stats line this is operated by.
    logging.getLogger("pika").setLevel(logging.WARNING)
    missing = [
        name
        for name, value in (
            ("OBA_CSPUB_RMQ_ADDRESSES", RMQ_ADDRESSES),
            ("OBA_CSPUB_RMQ_USERNAME", RMQ_USERNAME),
            ("OBA_CSPUB_RMQ_PASSWORD", RMQ_PASSWORD),
        )
        if not value
    ]
    if missing:
        LOG.error("missing required environment: %s", ", ".join(missing))
        return 2

    stop = False

    def handle_signal(signum, _frame):
        nonlocal stop
        LOG.info("received signal %s; shutting down", signum)
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    endpoint = f"tcp://{ZMQ_HOST}:{ZMQ_PORT}"
    sub.connect(endpoint)
    sub.setsockopt(zmq.SUBSCRIBE, ZMQ_TOPIC.encode())
    sub.setsockopt(zmq.RCVTIMEO, 2000)
    LOG.info("listening %s topic=%r -> exchange=%s", endpoint, ZMQ_TOPIC, EXCHANGE)

    publisher = Publisher()
    # Absorbs a broker blip instead of losing it: the source is a PUB socket with no offset, so
    # anything dropped here is gone for good and leaves a hole in the replay archive.
    pending: deque[tuple[bytes, int | None]] = deque()
    received = published = dropped = failures = 0
    received_at_last_stats = 0
    last_stats = time.monotonic()
    retry_at = 0.0

    while not stop:
        try:
            parts = sub.recv_multipart()
        except zmq.Again:
            parts = None
        except zmq.ZMQError as exc:
            LOG.error("zmq error: %s", exc)
            time.sleep(1)
            continue

        if parts is not None:
            body = parts[-1]
            received += 1
            if len(pending) >= BUFFER_MAX:
                pending.popleft()
                dropped += 1
            pending.append((body, received_at_ms(body)))

        now = time.monotonic()
        if pending and now >= retry_at:
            try:
                if not publisher.connected:
                    publisher.connect()
                while pending:
                    body, ts_ms = pending[0]
                    publisher.publish(body, ts_ms)
                    pending.popleft()
                    published += 1
            except Exception as exc:
                failures += 1
                publisher.close()
                # Back off, but never long enough to fill the buffer from a standing start.
                retry_at = now + 5
                LOG.warning("publish failed (%s); %d buffered, retrying in 5s", exc, len(pending))

        if now - last_stats >= LOG_STATS_SEC:
            LOG.info(
                "stats: received=%d published=%d buffered=%d dropped=%d failures=%d connects=%d (%.0f msg/s in)",
                received,
                published,
                len(pending),
                dropped,
                failures,
                publisher.connects,
                (received - received_at_last_stats) / (now - last_stats),
            )
            received_at_last_stats = received
            last_stats = now

    LOG.info("draining %d buffered message(s) before exit", len(pending))
    try:
        if pending and not publisher.connected:
            publisher.connect()
        while pending:
            body, ts_ms = pending[0]
            publisher.publish(body, ts_ms)
            pending.popleft()
            published += 1
    except Exception as exc:
        LOG.error("could not drain %d message(s) on shutdown: %s", len(pending), exc)

    publisher.close()
    sub.close()
    ctx.term()
    LOG.info("stopped (received=%d published=%d dropped=%d)", received, published, dropped)
    return 0


if __name__ == "__main__":
    sys.exit(run())
