/**
 * Copyright (C) 2026 Metropolitan Transportation Authority
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
package org.onebusaway.nyc.util.replay;

import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.EnumSet;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.Trigger;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;

/**
 * Drops scheduled tasks whose {@link Replayable} domains are not enabled, so a replay does not run
 * background work on wall-clock time.
 *
 * <p>Enable domains with {@code -Doba.replay.tasks=INFERENCE_INPUT,CONFIG}, or {@code ALL}. An
 * unannotated task is dropped: a task added later has to be classified deliberately.
 *
 * <p>Subclasses ThreadPoolTaskScheduler rather than implementing TaskScheduler because every consumer
 * in this codebase injects the concrete type.
 */
public class GatedTaskScheduler extends ThreadPoolTaskScheduler {

  private static final long serialVersionUID = 1L;

  private static final Logger _log = LoggerFactory.getLogger(GatedTaskScheduler.class);

  public static final String ENABLED_PROPERTY = "oba.replay.tasks";

  private final Set<ReplayDomain> _enabled;
  private final Set<String> _reported = ConcurrentHashMap.newKeySet();

  public GatedTaskScheduler() {
    this(System.getProperty(ENABLED_PROPERTY, ""));
  }

  GatedTaskScheduler(String spec) {
    _enabled = parse(spec);
    _log.warn("Scheduled tasks gated for replay; enabled domains={}", _enabled);
  }

  private static Set<ReplayDomain> parse(String spec) {
    if (spec == null || spec.trim().isEmpty()) {
      return Collections.emptySet();
    }
    if ("ALL".equalsIgnoreCase(spec.trim())) {
      return EnumSet.allOf(ReplayDomain.class);
    }
    Set<ReplayDomain> out = EnumSet.noneOf(ReplayDomain.class);
    for (String token : spec.split(",")) {
      String name = token.trim();
      if (name.isEmpty()) {
        continue;
      }
      try {
        out.add(ReplayDomain.valueOf(name.toUpperCase()));
      } catch (IllegalArgumentException e) {
        _log.error("Unknown {} value '{}'; ignoring", ENABLED_PROPERTY, name);
      }
    }
    return out;
  }

  Set<ReplayDomain> getEnabledDomains() {
    return Collections.unmodifiableSet(_enabled);
  }

  boolean isAllowed(Runnable task) {
    if (task == null) {
      return false;
    }
    Replayable annotation = task.getClass().getAnnotation(Replayable.class);
    if (annotation == null) {
      report(task, "is not annotated @Replayable");
      return false;
    }
    for (ReplayDomain domain : annotation.value()) {
      if (_enabled.contains(domain)) {
        return true;
      }
    }
    report(task, "domains " + Arrays.toString(annotation.value()) + " are not enabled");
    return false;
  }

  private void report(Runnable task, String why) {
    String name = task.getClass().getName();
    if (_reported.add(name)) {
      _log.warn("replay: not scheduling {} - {}", name, why);
    }
  }

  @Override
  public ScheduledFuture<?> schedule(Runnable task, Trigger trigger) {
    return isAllowed(task) ? super.schedule(task, trigger) : dropped();
  }

  @Override
  public ScheduledFuture<?> schedule(Runnable task, Date startTime) {
    return isAllowed(task) ? super.schedule(task, startTime) : dropped();
  }

  @Override
  public ScheduledFuture<?> scheduleAtFixedRate(Runnable task, Date startTime, long period) {
    return isAllowed(task) ? super.scheduleAtFixedRate(task, startTime, period) : dropped();
  }

  @Override
  public ScheduledFuture<?> scheduleAtFixedRate(Runnable task, long period) {
    return isAllowed(task) ? super.scheduleAtFixedRate(task, period) : dropped();
  }

  @Override
  public ScheduledFuture<?> scheduleWithFixedDelay(Runnable task, Date startTime, long delay) {
    return isAllowed(task) ? super.scheduleWithFixedDelay(task, startTime, delay) : dropped();
  }

  @Override
  public ScheduledFuture<?> scheduleWithFixedDelay(Runnable task, long delay) {
    return isAllowed(task) ? super.scheduleWithFixedDelay(task, delay) : dropped();
  }

  private static ScheduledFuture<?> dropped() {
    return new DroppedFuture();
  }

  /**
   * Callers keep the future and cancel it in destroy() or before rescheduling, so a dropped task
   * still has to return something that tolerates cancel().
   */
  private static final class DroppedFuture implements ScheduledFuture<Object> {

    @Override
    public long getDelay(TimeUnit unit) {
      return 0L;
    }

    @Override
    public int compareTo(java.util.concurrent.Delayed other) {
      return other == this ? 0 : Long.compare(0L, other.getDelay(TimeUnit.MILLISECONDS));
    }

    @Override
    public boolean cancel(boolean mayInterruptIfRunning) {
      return false;
    }

    @Override
    public boolean isCancelled() {
      return false;
    }

    @Override
    public boolean isDone() {
      return true;
    }

    @Override
    public Object get() {
      return null;
    }

    @Override
    public Object get(long timeout, TimeUnit unit) {
      return null;
    }
  }
}
