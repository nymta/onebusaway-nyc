#!/usr/bin/env bash
source /opt/oba/env-common.sh
exec java -cp "$MAIN/onebusaway-nyc-queue-broker/target/onebusaway-nyc-queue-broker-jar-with-dependencies.jar" \
  org.onebusaway.nyc.queue_broker.SimpleBroker 5566 5567
