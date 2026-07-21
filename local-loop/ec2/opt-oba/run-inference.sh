#!/usr/bin/env bash
set -euo pipefail
source /opt/oba/env-common.sh
export MAVEN_OPTS="-Xmx30g"
cd "$MAIN/onebusaway-nyc-vehicle-tracking-webapp"
exec mvn -B -P local-ie-testing -DskipTests -Dlicense.skip=true \
  -Die.listener=RabbitMqInputQueueListenerTask \
  -Die.output.queue=OutputQueueSenderServiceImpl \
  -DtimePredictions.status=ENABLED \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Dbundle.location="$BUNDLE" \
  -Djetty.http.port=8081 \
  -Doba.deadband.enabled=true -Doba.deadband.minMeters=10 -Doba.deadband.minIntervalSec=5 -Doba.deadband.maxAgeSec=30 \
  -Doba.rmq.addresses="$(gp /oba/rabbitmq/addresses)" \
  -Doba.rmq.username="$(gp /oba/rabbitmq/username)" \
  -Doba.rmq.password="$(gp /oba/rabbitmq/password)" \
  -Doba.rmq.streamName="$(gp /oba/rabbitmq/streamName)" \
  -Doba.rmq.virtualHost="$(gp /oba/rabbitmq/virtualHost)" \
  -Doba.rmq.ssl="$(gp /oba/rabbitmq/ssl)" \
  -Doba.rmq.offset="$(gp /oba/rabbitmq/offset)" \
  -Doba.rmq.prefetch="$(gp /oba/rabbitmq/prefetch)" \
  "$JETTY"
