#!/usr/bin/env bash
# Shared environment + helpers for the local OBA-NYC inference->predictions loop.
# Sourced by the other scripts. Safe to edit paths here in one place.

# --- toolchain ---
export JAVA_HOME=/opt/homebrew/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"        # so any forked `java` is JDK 11 too
export MAVEN_OPTS="-Xmx4g"
MVN="$(command -v mvn)"
JETTY="org.eclipse.jetty:jetty-maven-plugin:9.4.51.v20230217"   # Servlet 3.1 (repo's mortbay Jetty 6 breaks Spring 5.2)

# --- repos / bundle ---
# MAIN_REPO derives from this file's location (local-loop/ sits at the main-repo root).
MAIN_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# External predictions checkout + built-bundle parent — override via env vars if yours differ.
PRED_REPO="${OBA_PRED_REPO:-/Users/ranajays/Documents/git/onebusaway-nyc-predictions}"
BUNDLE_PARENT="${OBA_BUNDLE_PARENT:-$MAIN_REPO/.context/manhattan-bundle/transit-data-bundle}"   # parent dir; holds 2026Apr_Manhattan_B6

# --- ports ---
IE_PORT=8081      # inference webapp (HTTP)
PRED_PORT=8082    # predictions webapp (HTTP, /api)
Q_IN=5566         # inferred-locations queue: predictions BINDS, inference CONNECTS  (topic inference_queue)
Q_OUT=5568        # GTFS-RT time predictions:  predictions BINDS, consumers CONNECT (topic time)
IE_INPUT_PORT="${IE_INPUT_PORT:-5563}"   # raw-GPS input queue: mq bridge BINDS (PUB), inference CONNECTS (SUB, topic bustech)

# --- logs ---
IE_LOG=/tmp/oba-ie-jetty.log
PRED_LOG=/tmp/oba-pred-jetty.log
BRIDGE_LOG=/tmp/oba-mq-bridge.log
MONGO_NAME=oba-mongo

port_up(){ lsof -ti:"$1" >/dev/null 2>&1; }

http_code(){ curl -s -o /dev/null -m 6 -w "%{http_code}" "$1" 2>/dev/null; }

wait_for(){ # <name> <timeout_s> <shell-condition>
  local name="$1" to="$2" cond="$3" i=0
  printf 'waiting for %s ' "$name"
  while [ "$i" -lt "$to" ]; do
    if eval "$cond" 2>/dev/null; then printf ' OK (%ss)\n' "$i"; return 0; fi
    sleep 3; i=$((i+3)); printf '.'
  done
  printf ' TIMEOUT (%ss)\n' "$to"; return 1
}

ensure_mongo(){
  if docker exec "$MONGO_NAME" mongo --quiet --eval 'db.runCommand({ping:1}).ok' 2>/dev/null | grep -q 1; then
    echo "mongo: up"; return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "mongo: ERROR Docker daemon is not running (open Docker Desktop) "; return 1
  fi
  echo "mongo: starting ${MONGO_NAME} (mongo:4.4 — driver 2.14 needs <=5.0; mongo:6 fails)"
  docker rm -f "$MONGO_NAME" >/dev/null 2>&1
  docker run -d --name "$MONGO_NAME" -p 27017:27017 mongo:4.4 >/dev/null 2>&1
  wait_for "mongo ping" 60 "docker exec $MONGO_NAME mongo --quiet --eval 'db.runCommand({ping:1}).ok' 2>/dev/null | grep -q 1"
}
