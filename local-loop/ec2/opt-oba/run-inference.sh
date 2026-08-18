#!/usr/bin/env bash
set -euo pipefail
source /opt/oba/env-common.sh
# -Duser.timezone: Amazon Linux defaults to UTC, a developer Mac to Eastern. Several places build a
# civil date in the JVM default zone and compare it against another value built the same way - the UTS
# roster match in ImporterOperatorAssignmentData:79-82 among them - so the zone decides which civil day
# a record belongs to, and an unpinned zone makes EC2 and local disagree. America/New_York is the zone
# the bundle itself carries.
export MAVEN_OPTS="-Xmx30g -Duser.timezone=America/New_York"
cd "$MAIN/onebusaway-nyc-vehicle-tracking-webapp"
exec mvn -B -P local-ie-testing -DskipTests -Dlicense.skip=true \
  -Die.listener=RabbitMqInputQueueListenerTask \
  -Die.output.queue=OutputQueueSenderServiceImpl \
  -DtimePredictions.status=ENABLED \
  -Dorg.onebusaway.nyc.tdm.bundle.batchmode=true \
  -Dbundle.location="$BUNDLE" \
  -Djetty.http.port=8081 \
  -Doba.deadband.enabled=true -Doba.deadband.minMeters=10 -Doba.deadband.minIntervalSec=7 -Doba.deadband.maxAgeSec=30 \
  -Doba.shed.maxAgeSec=50 \
  -Doba.rmq.addresses="$(gp /oba/rabbitmq/addresses)" \
  -Doba.rmq.username="$(gp /oba/rabbitmq/username)" \
  -Doba.rmq.password="$(gp /oba/rabbitmq/password)" \
  -Doba.rmq.streamName="$(gp /oba/rabbitmq/streamName)" \
  -Doba.rmq.virtualHost="$(gp /oba/rabbitmq/virtualHost)" \
  -Doba.rmq.ssl="$(gp /oba/rabbitmq/ssl)" \
  -Doba.rmq.offset="$(gp /oba/rabbitmq/offset)" \
  -Doba.rmq.prefetch="$(gp /oba/rabbitmq/prefetch)" \
  -Doba.crew.service.class=org.onebusaway.nyc.vehicle_tracking.impl.crew.S3UtsOperatorAssignmentServiceImpl \
  -Doba.crew.s3.accessKey="$(gp /oba/uts/s3/accessKey)" \
  -Doba.crew.s3.secretKey="$(gp /oba/uts/s3/secretKey)" \
  "$JETTY"
