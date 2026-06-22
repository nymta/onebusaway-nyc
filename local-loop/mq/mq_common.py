#!/usr/bin/env python3
"""Shared RabbitMQ-stream connection helpers for the OBA-NYC harness + bridge.

The BusTech raw-GPS feed is a RabbitMQ *stream* consumed over AMQP 0.9.1 (same as
helium-backend). Streams require: a positive basic_qos prefetch, and an
``x-stream-offset`` argument on basic_consume. Message bodies are the raw
``{"RealtimeEnvelope": {...}}`` JSON bytes — identical to what OBA's inference
input listener (InputServiceImpl.deserializeMessage) already parses.
"""
import os
import ssl
import sys

try:
    import pika
except ImportError:
    sys.stderr.write(
        "pika is not installed. Run:  python3 -m pip install --user pika\n")
    raise


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def load_config():
    """Read connection details from the environment (set by sourcing mq_env.sh)."""
    cfg = {
        "host": env("OBA_MQ_HOST"),
        "port": int(env("OBA_MQ_PORT", "5671")),
        "vhost": env("OBA_MQ_VHOST", "/"),
        "user": env("OBA_MQ_USER"),
        "password": env("OBA_MQ_PASSWORD"),
        "tls": env("OBA_MQ_TLS", "true").lower() == "true",
        "tls_insecure": env("OBA_MQ_TLS_INSECURE", "false").lower() == "true",
        "stream": env("OBA_MQ_STREAM"),
        "offset": env("OBA_MQ_OFFSET", "last"),
    }
    missing = [k for k in ("host", "user", "password", "stream") if not cfg[k]]
    if missing:
        sys.stderr.write(
            "Missing connection detail(s): %s\n"
            "Copy mq_env.sh.example -> mq_env.sh, fill it in, then:  source mq_env.sh\n"
            % ", ".join("OBA_MQ_" + m.upper() for m in missing))
        sys.exit(2)
    return cfg


def _ssl_options(cfg, host):
    if cfg["tls_insecure"]:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        # Prefer certifi's CA bundle — many Python installs (esp. macOS) can't find the
        # system root store, which yields "unable to get local issuer certificate".
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
    return pika.SSLOptions(ctx, server_hostname=host)


def open_connection(cfg, log=print):
    """Open a BlockingConnection, trying each comma-separated host in turn."""
    hosts = [h.strip() for h in cfg["host"].split(",") if h.strip()]
    params = []
    for h in hosts:
        # allow "host:port" entries to override the shared port
        if ":" in h:
            host, port = h.rsplit(":", 1)
            port = int(port)
        else:
            host, port = h, cfg["port"]
        params.append(pika.ConnectionParameters(
            host=host, port=port, virtual_host=cfg["vhost"],
            credentials=pika.PlainCredentials(cfg["user"], cfg["password"]),
            ssl_options=_ssl_options(cfg, host) if cfg["tls"] else None,
            heartbeat=60, blocked_connection_timeout=30,
            connection_attempts=1, socket_timeout=15, stack_timeout=20))
    scheme = "amqps" if cfg["tls"] else "amqp"
    log("[mq] connecting to %s://%s%s (vhost=%s) ..."
        % (scheme, cfg["host"], "" if ":" in cfg["host"] else ":%d" % cfg["port"], cfg["vhost"]))
    return pika.BlockingConnection(params)


def offset_arg(offset):
    """Coerce OBA_MQ_OFFSET into an x-stream-offset value.

    Plain integer -> stream offset (int). Anything else passes through as a
    string: 'first' | 'last' | 'next' | interval like '1h'/'7D'.
    """
    try:
        return int(offset)
    except (TypeError, ValueError):
        return offset


def consume_stream(channel, stream, offset, prefetch, on_message):
    """Subscribe to a stream and dispatch each delivery to on_message(body: bytes).

    on_message returns True to keep going, False to stop (the consumer is then
    cancelled). Every message is manually acked.
    """
    channel.basic_qos(prefetch_count=max(1, prefetch))

    def _cb(ch, method, properties, body):
        ch.basic_ack(method.delivery_tag)
        if on_message(body) is False:
            ch.basic_cancel(method.consumer_tag)

    channel.basic_consume(
        queue=stream, on_message_callback=_cb, auto_ack=False,
        arguments={"x-stream-offset": offset_arg(offset)})
    channel.start_consuming()
