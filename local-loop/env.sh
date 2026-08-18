#!/usr/bin/env bash
# Shared environment + helpers for the local OBA-NYC inference->predictions loop.
# Sourced by the other scripts. Safe to edit paths here in one place.

# --- toolchain ---
# JDK 11 is required: Spring 5.2 and the old Jetty break on 17+, and the WAR plugin's XStream 1.4.4
# needs deep reflection into java.util that 16+ denies. Do NOT resolve this with
# `/usr/libexec/java_home -v 11` — Homebrew's openjdk@11 is not registered with it, so that returns
# whatever JDK happens to be newest.
CORRETTO11=/usr/lib/jvm/java-11-amazon-corretto
DEFAULT_JAVA_HOME=/opt/homebrew/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home
[ -d "$CORRETTO11" ] && DEFAULT_JAVA_HOME="$CORRETTO11"
export JAVA_HOME="${OBA_JAVA_HOME:-$DEFAULT_JAVA_HOME}"
export PATH="$JAVA_HOME/bin:$PATH"        # so any forked `java` is JDK 11 too
# The python helpers (obs_time.py, mq/*.py) call bare `python3`, so the virtualenv holding pyzmq +
# gtfs-realtime-bindings has to be on PATH. Note mq/verify.sh, mq/start-bridge.sh and mq/view.sh
# auto-install with `pip install --user`, which errors inside a venv — pre-install pika and certifi
# there so that fallback never fires.
VENV="${OBA_VENV:-/Users/marcosacosta/marcos/code/.venv}"
[ -d "$VENV" ] && export PATH="$VENV/bin:$PATH"
# 4g was sized for the runbook's 116 MB Manhattan bundle. The whole-MTA C6 bundle is ~675 MB on disk
# and its transit graph is several times that in heap, on top of an inference thread pool of
# 2 + cores*20 (202 threads on a 10-core machine, since numberOfProcessingThreads is not config-wired).
# Pin the JVM timezone to the agency zone the bundle carries (ServiceId records
# timeZone=America/New_York). Several places build a civil date with the JVM default zone and then
# compare it against another value built the same way - the UTS roster match in
# ImporterOperatorAssignmentData:79-82 is one, and Joda's DateMidnight.equals compares chronology as
# well as instant, so both sides have to agree. Left unset, this is Eastern on a developer Mac and UTC
# on Amazon Linux, which silently shifts which civil day a record belongs to. Pinning it makes local
# and EC2 identical; do not "fix" one side of such a comparison in code, which would make the
# chronologies differ and the equality always fail.
export TZ="America/New_York"
export MAVEN_OPTS="${OBA_MAVEN_OPTS:--Xmx8g} -Duser.timezone=America/New_York"
export MVN="$(command -v mvn)"
export JETTY="org.eclipse.jetty:jetty-maven-plugin:9.4.51.v20230217"   # Servlet 3.1 (repo's mortbay Jetty 6 breaks Spring 5.2)

# --- repos / bundle ---
# Everything below is EXPORTED, not just assigned. These are used interactively as well as by the
# scripts - for example when launching the inference webapp by hand for a replay run - and an unset
# variable there fails in a way that does not name itself: bundle.location feeds the HSQLDB JDBC URL
# (jdbc:hsqldb:file:${bundle.location}/org_onebusaway_database), so an empty value surfaces as
# "Access to DialectResolutionInfo cannot be null when 'hibernate.dialect' not set", several hundred
# lines into a Hibernate stack trace.
#
# MAIN_REPO derives from this file's location (local-loop/ sits at the main-repo root).
#
# ${BASH_SOURCE[0]} is bash-only. Sourced from zsh it expands to nothing, so dirname yields "." and
# MAIN_REPO silently becomes the PARENT of the current directory. Nothing complains: bundle.location
# then points at a directory that does not exist, HSQLDB creates it, the bundle scan finds no bundle
# there, and the engine sits logging "Bundle is not ready" forever. zsh sets $0 to the sourced file,
# so it covers that case. The check below is the important part - a wrong root must fail loudly.
export MAIN_REPO="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
if [ ! -f "$MAIN_REPO/pom.xml" ]; then
  echo "env.sh: MAIN_REPO=$MAIN_REPO has no pom.xml, so it is not the repo root." >&2
  echo "env.sh: set OBA_MAIN_REPO explicitly, or source this file from the repo root." >&2
  MAIN_REPO="${OBA_MAIN_REPO:-$MAIN_REPO}"
fi
# External predictions checkout + built-bundle parent. Edit the defaults here; the OBA_* env vars
# stay as one-off overrides.
# BUNDLE_PARENT is a PARENT dir and must contain exactly ONE bundle — standalone discovery picks
# whatever it finds, so leaving an old or failed build alongside makes the active bundle ambiguous.
export PRED_REPO="${OBA_PRED_REPO:-/Users/marcosacosta/marcos/code/onebusaway-nyc-predictions}"
export BUNDLE_PARENT="${OBA_BUNDLE_PARENT:-$MAIN_REPO/.context/wholeMTA-c6/transit-data-bundle}"   # parent dir; holds the current pick's bundle (e.g. 2026C6_wholeMTA_v2)

# --- ports ---
export IE_PORT=8081      # inference webapp (HTTP)
export PRED_PORT=8082    # predictions webapp (HTTP, /api)
export Q_IN=5566         # inferred-locations queue: predictions BINDS, inference CONNECTS  (topic inference_queue)
export Q_OUT=5568        # GTFS-RT time predictions:  predictions BINDS, consumers CONNECT (topic time)
export IE_INPUT_PORT="${IE_INPUT_PORT:-5563}"   # raw-GPS input queue: mq bridge BINDS (PUB), inference CONNECTS (SUB, topic bustech)

# --- logs ---
export IE_LOG=/tmp/oba-ie-jetty.log
export PRED_LOG=/tmp/oba-pred-jetty.log
export BRIDGE_LOG=/tmp/oba-mq-bridge.log
export MONGO_NAME=oba-mongo

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
