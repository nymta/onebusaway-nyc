/**
 * Copyright (C) 2011 Metropolitan Transportation Authority
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *         http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package org.onebusaway.nyc.vehicle_tracking.impl.clock;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;
import java.util.concurrent.atomic.AtomicLong;

/**
 * A {@link Clock} whose instant is settable, so historical data can be replayed on data-time
 * instead of wall-clock time.
 *
 * <p>Why this exists: the ingest path stamps {@code timeReceived} with "now", and
 * {@code RecordLibrary.getBestTimestamp} prefers that received time whenever it differs from the
 * device time by more than 30 minutes - which is guaranteed for archived data. The particle filter
 * then derives its elapsed time from wall-clock arrival spacing rather than from the real interval
 * between fixes. Replaying a 5-minute archive bucket in seconds therefore trips the 3-second
 * per-vehicle minimum in {@code isValidRecord} (dropping most records) and gives the survivors an
 * elapsed time far shorter than the distance travelled implies, which reads as impossible speed.
 *
 * <p>A replay driver sets this clock to each record's own timestamp immediately before dispatching
 * that record, so "now" equals record time and the engine behaves as it does in production.
 *
 * <p>Wired as bean {@code obaClock} under the {@code replay} Spring profile; every other profile
 * gets {@code Clock.systemUTC()}. Reads are lock-free, so a single driver thread may advance the
 * clock while inference threads read it.
 */
public class MutableClock extends Clock {

  /** Shared with every {@link #withZone} view, so re-zoning does not fork the time source. */
  private final AtomicLong millis;

  private final ZoneId zone;

  public MutableClock() {
    this(new AtomicLong(0L), ZoneId.systemDefault());
  }

  private MutableClock(AtomicLong millis, ZoneId zone) {
    this.millis = millis;
    this.zone = zone;
  }

  /** Advance (or rewind) virtual time. Call this before dispatching the record it belongs to. */
  public void setMillis(long epochMillis) {
    millis.set(epochMillis);
  }

  /**
   * Move the clock forward to {@code epochMillis}, ignoring the call if it is already past that.
   *
   * <p>Replay follows the order records arrived at the broker, which is what the live engine consumed.
   * That order is not perfectly sorted - one archived bucket had 36 records of 266,112 arriving out of
   * sequence - and a clock that went backwards would make the engine see time reverse. Use this rather
   * than {@link #setMillis} when following a data stream; {@code setMillis} stays absolute so a driver
   * can still position the clock before a run.
   */
  public void advanceTo(long epochMillis) {
    millis.accumulateAndGet(epochMillis, Math::max);
  }

  public void setInstant(Instant instant) {
    millis.set(instant.toEpochMilli());
  }

  @Override
  public long millis() {
    return millis.get();
  }

  @Override
  public Instant instant() {
    return Instant.ofEpochMilli(millis.get());
  }

  @Override
  public ZoneId getZone() {
    return zone;
  }

  @Override
  public Clock withZone(ZoneId zone) {
    return new MutableClock(this.millis, zone);
  }

  @Override
  public String toString() {
    return "MutableClock[" + Instant.ofEpochMilli(millis.get()) + "," + zone + "]";
  }
}
