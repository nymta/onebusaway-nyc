#!/usr/bin/env bash
# Republish the CS ~28 s filtered AVL queue (ZMQ :5564) onto RabbitMQ.
# Only runs where egress is an allowlisted Elastic IP -- see cs-gps-publisher.py.
set -euo pipefail
source /opt/oba/env-common.sh

export OBA_CSPUB_ZMQ_HOST="${OBA_CSPUB_ZMQ_HOST:-queue.staging.obanyc.com}"
export OBA_CSPUB_ZMQ_PORT="${OBA_CSPUB_ZMQ_PORT:-5564}"
export OBA_CSPUB_ZMQ_TOPIC="${OBA_CSPUB_ZMQ_TOPIC:-bhs_queue}"
export OBA_CSPUB_EXCHANGE="${OBA_CSPUB_EXCHANGE:-nyct.bustech.gps-filtered}"

# Same CloudAMQP cluster the instances already consume from, so the transport config is shared.
export OBA_CSPUB_RMQ_ADDRESSES="${OBA_CSPUB_RMQ_ADDRESSES:-$(gp /oba/rabbitmq/addresses)}"
export OBA_CSPUB_RMQ_VHOST="${OBA_CSPUB_RMQ_VHOST:-$(gp /oba/rabbitmq/virtualHost)}"
export OBA_CSPUB_RMQ_SSL="${OBA_CSPUB_RMQ_SSL:-$(gp /oba/rabbitmq/ssl)}"

# Publish as data-pusher, the cluster's existing write-side identity (write='.*'). data-archiver is
# the read side and has write='' , so it can never publish. Reusing data-pusher means no broker
# permission change and no new credential; it needs parameter/rabbitmq/data-pusher/prod/* on the
# oba-ssm-param-read inline policy of oba-nyc-ec2-role.
export OBA_CSPUB_RMQ_USERNAME="${OBA_CSPUB_RMQ_USERNAME:-data-pusher}"
export OBA_CSPUB_RMQ_PASSWORD="${OBA_CSPUB_RMQ_PASSWORD:-$(gp /rabbitmq/data-pusher/prod/password)}"

# Install deps once (pyzmq + pika); safe to re-run.
python3 -m pip install --user -q pyzmq pika 2>/dev/null || python3 -m pip install -q pyzmq pika

exec python3 /opt/oba/cs-gps-publisher.py
