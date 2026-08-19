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

package org.onebusaway.nyc.vehicle_tracking.impl.queue;

import java.io.BufferedReader;
import java.io.FileReader;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Pattern;

import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;
import javax.servlet.ServletContext;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.queue.model.RealtimeEnvelope;
import org.onebusaway.nyc.transit_data_federation.services.bundle.BundleManagementService;
import org.onebusaway.nyc.transit_data_federation.services.nyc.DestinationSignCodeService;
import org.onebusaway.nyc.vehicle_tracking.impl.clock.MutableClock;
import org.onebusaway.nyc.vehicle_tracking.impl.inference.VehicleLocationInferenceServiceImpl;
import org.onebusaway.nyc.vehicle_tracking.services.queue.InputService;
import org.onebusaway.nyc.vehicle_tracking.services.queue.InputTask;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.web.context.ServletContextAware;

/**
 * Replays archived AVL from a local newline-delimited file on <em>data time</em>, advancing a
 * {@link MutableClock} to each record's own timestamp instead of pacing against the wall clock.
 *
 * <p>Input is one {@code {"RealtimeEnvelope": ...}} document per line, ascending by
 * {@code timeReceived} - the format {@code local-loop/make-replay-fixture.py} writes out of a
 * {@code bustechGps} archive bucket. This is deliberately a file reader, not an S3 reader: it keeps
 * credentials, object listing, gzip and global ordering out of the first end-to-end test.
 *
 * <p>Differences from {@link FileInputTask}, which this is modelled on. All three matter:
 *
 * <ol>
 * <li><b>No pacing.</b> FileInputTask sleeps until wall-clock elapsed time catches up with data
 *     elapsed time, so it replays at 1x by construction. Here the clock is data, not a deadline, so
 *     the run goes as fast as inference allows.
 * <li><b>The archive's {@code timeReceived} is preserved.</b> FileInputTask overwrites it with
 *     {@code now} before dispatch. Combined with the same overwrite in
 *     {@code VehicleLocationInferenceServiceImpl}, that makes the particle filter's elapsed time a
 *     measure of replay speed rather than of the real interval between fixes: the 3-second
 *     per-vehicle minimum in {@code isValidRecord} then rejects most records, and the survivors look
 *     like they travelled their true distance in a fraction of the time, which scores as impossible
 *     speed and collapses to DEADHEAD.
 * <li><b>Line reading is line-based.</b> FileInputTask reads character by character and consumes one
 *     extra character after each newline ("read last space"), which suits the SQL dump it was written
 *     for but would eat the leading brace of every subsequent JSON line here.
 * </ol>
 *
 * <p>Requires the {@code replay} Spring profile, because it injects {@link MutableClock} rather than
 * {@link java.time.Clock}. Under any other profile {@code obaClock} is {@code Clock.systemUTC()} and
 * startup fails to resolve this bean - which is the intent. Selecting a replay driver while the
 * engine still reads wall-clock time should be loud, not silent.
 *
 * <p>Not handled here, deliberately: the periodic refresh/monitoring tasks elsewhere in the engine
 * still fire on their own wall-clock timers. They are inert in a TDM-less deployment with a pinned
 * bundle, so they are left alone rather than driven from the virtual clock.
 *
 * <p>Select with {@code -Die.listener=ReplayFileInputTask -Dreplay.file=/path/to/fixture.jsonl}.
 */
public class ReplayFileInputTask implements ServletContextAware, InputTask {

  protected static Logger _log = LoggerFactory.getLogger(ReplayFileInputTask.class);

  /** Named, not class-based, so log4j2.xml can route it to its own file regardless of which class
   * calls it. One line per progress tick: virtual clock, wall elapsed, throughput, ETA, stripes. */
  private static final Logger _monitorLog = LoggerFactory.getLogger("replay.monitor");

  /** Virtual-time end of the declared --to window, epoch millis, or null if not passed (ETA omitted
   * rather than guessed). Set by run-replay.sh; a streaming FIFO has no other way to know how much
   * more data is coming. */
  private static final Long WINDOW_END_MILLIS = Long.getLong("oba.replay.window.endMillis");

  /** How long to wait for the transit graph before giving up. A whole-MTA bundle takes ~4 min. */
  private static final long BUNDLE_WAIT_MS = 15 * 60 * 1000;

  /** How long to wait for inference to finish the records already dispatched. */
  private static final long DRAIN_TIMEOUT_MS = 30 * 60 * 1000;

  /** How often the read loop reports its own progress. */
  private static final long READER_REPORT_MS = 15 * 1000;

  /**
   * The concrete type, not the interface: replay needs awaitIdle(), which is deliberately absent
   * from VehicleLocationInferenceService because nothing on the live path should wait for the engine
   * to go idle.
   */
  private VehicleLocationInferenceServiceImpl _vehicleLocationService;
  private InputService _inputService;
  private MutableClock _clock;
  private BundleManagementService _bundleManagementService;

  private String _filename;
  private String _depotPartitionKey;

  /**
   * Pending tasks the reader will allow before it waits. Kept under
   * BundleManagementServiceImpl.MAX_EXPECTED_THREADS (3000) so its Future-list sweep never triggers.
   * 0 disables the throttle. Override with -Dreplay.maxOutstanding.
   */
  private int _maxOutstanding = 2000;

  /**
   * -Dreplay.routeFilter=<regex> replays only vehicles serving matching routes, e.g. '^M[0-9]' for
   * Manhattan. A vehicle is admitted on its first record whose DSC maps (via the bundle) to routes
   * that ALL match, and keeps every later record so its track stays continuous - the same selection
   * filter-archive.py makes offline. Deadhead DSCs map to no routes, so a bus deadheading at the
   * window's start is admitted at its first in-service record.
   */
  private Pattern _routeFilter = null;

  private final Set<String> _admittedVehicles = new HashSet<String>();

  private DestinationSignCodeService _dscService;

  @Autowired
  public void setDestinationSignCodeService(DestinationSignCodeService dscService) {
    _dscService = dscService;
  }

  /** Reader thread only, so the admitted-vehicle set needs no synchronization. */
  private boolean passesRouteFilter(RealtimeEnvelope record) {
    if (_routeFilter == null)
      return true;
    final tcip_final_3_0_5_1.CcLocationReport m = record.getCcLocationReport();
    if (m == null || m.getVehicle() == null || m.getDestSignCode() == null)
      return false;
    final String agency = m.getVehicle().getAgencydesignator();
    final String vehicleKey = agency + "_" + m.getVehicle().getVehicleId();
    if (_admittedVehicles.contains(vehicleKey))
      return true;
    final Set<AgencyAndId> routes = _dscService.getRouteCollectionIdsForDestinationSignCode(
        m.getDestSignCode().toString(), agency);
    if (routes == null || routes.isEmpty())
      return false;
    for (AgencyAndId route : routes) {
      if (!_routeFilter.matcher(route.getId()).find())
        return false;
    }
    _admittedVehicles.add(vehicleKey);
    return true;
  }
  private ExecutorService _executorService = null;

  /** Reads the archive wrapper only. The envelope inside is still parsed by the input service. */
  private static final ObjectMapper WRAPPER = new ObjectMapper();

  @Autowired
  public void setVehicleLocationService(
      VehicleLocationInferenceServiceImpl vehicleLocationService) {
    _vehicleLocationService = vehicleLocationService;
  }

  /**
   * queueInputService, not fileInputService. The archive holds exactly what the queue delivered, so
   * the replay must apply the same byte handling production applies: {@code InputQueueServiceImpl}
   * renames two keys and nothing else. {@code FileInputServiceImpl} additionally brace-wraps the
   * line and runs {@code replaceFirst("UUID.*UUID", "UUID")}, which suits the SQL dump it exists for
   * and would silently rewrite records here.
   */
  @Autowired
  @Qualifier("queueInputService")
  public void setInputService(InputService inputService) {
    _inputService = inputService;
  }

  /** Concrete type on purpose: resolves only under the replay profile. See the class comment. */
  @Autowired
  public void setClock(MutableClock clock) {
    _clock = clock;
  }

  @Autowired
  public void setBundleManagementService(BundleManagementService bundleManagementService) {
    _bundleManagementService = bundleManagementService;
  }

  public void setFilename(String filename) {
    _filename = filename;
  }

  @Override
  public void setServletContext(ServletContext servletContext) {
    if (servletContext != null) {
      setDepotPartitionKey(servletContext.getInitParameter("depot.partition.key"));
      _log.info("servlet context provided depot.partition.key=" + _depotPartitionKey);
    }
  }

  public void setDepotPartitionKey(String depotPartitionKey) {
    _depotPartitionKey = depotPartitionKey;
  }

  public String getDepotPartitionKey() {
    return _depotPartitionKey;
  }

  @PostConstruct
  public void execute() {
    // Read from a system property rather than an XML property. bhsInputQueue is one bean shared by
    // every ${ie.listener}, so a filename property there would have to exist on all of them.
    if (_filename == null || _filename.isEmpty())
      _filename = System.getProperty("replay.file");

    String bound = System.getProperty("replay.maxOutstanding");
    if (bound != null) {
      try {
        _maxOutstanding = Integer.parseInt(bound.trim());
      } catch (NumberFormatException e) {
        _log.warn("Invalid replay.maxOutstanding={}; keeping {}", bound, _maxOutstanding);
      }
    }

    String routeFilter = System.getProperty("replay.routeFilter", "").trim();
    if (!routeFilter.isEmpty()) {
      _routeFilter = Pattern.compile(routeFilter);
      _log.warn("replay: route filter '" + routeFilter
          + "'; vehicles admitted on their first matching DSC");
    }

    _inputService.setDepotPartitionKey(_depotPartitionKey);
    // Single thread: replay has to be reproducible, and a shared clock cannot be advanced
    // meaningfully by several dispatchers at once.
    _executorService = Executors.newFixedThreadPool(1);
    _executorService.execute(new ReplayThread());
  }

  @PreDestroy
  public void destroy() {
    _executorService.shutdownNow();
  }

  /**
   * One line to the dedicated monitor log: virtual clock, wall elapsed, throughput both ways
   * (rec/s and its reciprocal ms/rec, both fleet-wide across every stripe, not a per-record cost),
   * stripe occupancy, and an ETA and percent-complete when the window's end is known. Called from
   * both the dispatch and drain progress ticks, so it is the one place that combines "how much has
   * actually been computed" with "how far the virtual clock has gotten."
   */
  private void logMonitor(long wallStart, long firstTs) {
    final long nowMs = System.currentTimeMillis();
    final long elapsedMs = nowMs - wallStart;
    final long virtualNow = _clock.millis();
    final long completed = _vehicleLocationService.stripesCompletedTaskCount();
    final double speedX = elapsedMs > 0 ? (virtualNow - firstTs) / (double) elapsedMs : 0.0;
    final double aggregateRecPerSec = elapsedMs > 0 ? completed * 1000.0 / elapsedMs : 0.0;
    final double aggregateMsPerRec = aggregateRecPerSec > 0 ? 1000.0 / aggregateRecPerSec : 0.0;

    String eta = "unknown";
    String pctComplete = "unknown";
    if (WINDOW_END_MILLIS != null) {
      final double windowMs = WINDOW_END_MILLIS - firstTs;
      if (windowMs > 0) {
        pctComplete = String.format("%.1f%%", 100.0 * (virtualNow - firstTs) / windowMs);
      }
      if (speedX > 0.0) {
        final long remainingVirtualMs = WINDOW_END_MILLIS - virtualNow;
        final long estRemainingWallMs = (long) (remainingVirtualMs / speedX);
        eta = java.time.Instant.ofEpochMilli(nowMs + estRemainingWallMs).toString();
      }
    }

    _monitorLog.warn(String.format(
        "virtual_clock=%d wall_elapsed_s=%.0f speed=%.2fx aggregate_rec_s=%.1f aggregate_ms_per_rec=%.2f "
            + "pct_complete=%s stripes=%d/%d eta=%s",
        virtualNow, elapsedMs / 1000.0, speedX, aggregateRecPerSec, aggregateMsPerRec, pctComplete,
        _vehicleLocationService.stripesActiveCount(),
        _vehicleLocationService.getNumberOfProcessingThreads(), eta));
  }

  private class ReplayThread implements Runnable {

    /** Block until the transit graph is loaded. Returns false if it never arrives. */
    private boolean awaitBundle() {
      long deadline = System.currentTimeMillis() + BUNDLE_WAIT_MS;
      boolean announced = false;
      while (System.currentTimeMillis() < deadline) {
        if (Boolean.TRUE.equals(_bundleManagementService.bundleIsReady())) {
          _log.warn("replay: bundle ready, beginning replay");
          return true;
        }
        if (!announced) {
          _log.warn("replay: waiting for the bundle to load before dispatching any record");
          announced = true;
        }
        try {
          Thread.sleep(2000);
        } catch (InterruptedException ie) {
          Thread.currentThread().interrupt();
          return false;
        }
      }
      _log.error("replay: bundle not ready after " + (BUNDLE_WAIT_MS / 1000) + "s; giving up");
      return false;
    }

    public void run() {
      if (_filename == null || _filename.isEmpty()) {
        _log.error("replay: no input file set (-Dreplay.file=...)");
        return;
      }

      // Wait for the transit graph. @PostConstruct fires while the bundle is still deserialising, and
      // dispatching into an unloaded graph just produces "Bundle is not ready" and burns memory
      // alongside the load. FileInputTask never had to handle this because its pacing loop delayed
      // the first record by however long the data offset implied.
      if (!awaitBundle())
        return;

      long read = 0, dispatched = 0, rejected = 0, unparsed = 0, outOfOrder = 0, wrapped = 0,
          filtered = 0;
      long firstTs = 0, lastTs = 0, prevTs = Long.MIN_VALUE;
      long wallStart = System.currentTimeMillis();
      long throttleMs = 0, throttleWaits = 0;
      long lastReadReport = wallStart;

      _log.warn("replay: starting from " + _filename + ", maxOutstanding=" + _maxOutstanding);

      try (BufferedReader reader = new BufferedReader(new FileReader(_filename))) {
        String line;
        while ((line = reader.readLine()) != null) {
          if (line.trim().isEmpty())
            continue;
          read++;

          /*
           * Archived lines are {"ts": <broker millis>, "b": "<envelope json>"}. The driver reads ts
           * and the engine never sees it: knowing when a record arrived is the driver's business,
           * exactly as it is for a live queue consumer, and the engine must stay unaware of it.
           *
           * ts is the broker's arrival time, which is what the live engine's wall clock would have
           * read. The envelope's own timeReceived is stamped upstream of the broker and trails it by
           * about 26ms, which is why 53% of records look out of order by that field while the file is
           * in arrival order to within 36 records of 266,112.
           *
           * A line without the wrapper is treated as a bare envelope, so fixtures still replay.
           */
          String payload = line;
          long arrivedTs = -1L;
          try {
            final JsonNode outer = WRAPPER.readTree(line);
            final JsonNode envelope = outer.get("b");
            if (envelope != null && outer.get("ts") != null) {
              payload = envelope.asText();
              arrivedTs = outer.get("ts").asLong();
              wrapped++;
            }
          } catch (Exception e) {
            // Not a wrapper. Fall through and let the input service judge the line.
          }

          RealtimeEnvelope record;
          try {
            record = _inputService.deserializeMessage(payload);
          } catch (Exception e) {
            unparsed++;
            continue;
          }
          if (record == null) {
            unparsed++;
            continue;
          }

          final long ts = arrivedTs >= 0 ? arrivedTs : record.getTimeReceived();
          if (ts < prevTs)
            outOfOrder++;      // counted, not corrected: replaying arrival order is the point
          prevTs = ts;
          if (firstTs == 0)
            firstTs = ts;
          lastTs = ts;

          // Before dispatch, so the ingest path's own "now" read stamps this record's arrival time.
          // Advanced for filtered records too: the clock is the stream's arrival time, not the
          // filtered subset's.
          _clock.advanceTo(ts);

          if (!passesRouteFilter(record)) {
            filtered++;
            continue;
          }

          if (_inputService.acceptMessage(record)) {
            _vehicleLocationService.handleRealtimeEnvelopeRecord(record);
            dispatched++;

            /*
             * Hold the reader back so the clock cannot outrun inference. Without this the loop reads
             * the whole file in seconds while the stripes are still near the start, which breaks two
             * things: the clock reaches the end of the window, so every queued record looks minutes
             * stale and load-shedding discards it; and the Future list in
             * BundleManagementServiceImpl grows past MAX_EXPECTED_THREADS, after which every dispatch
             * triggers an O(n) sweep of it.
             */
            while (_maxOutstanding > 0
                && _vehicleLocationService.getOutstandingTaskCount() > _maxOutstanding) {
              long t0 = System.currentTimeMillis();
              try {
                Thread.sleep(5);
              } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                break;
              }
              throttleMs += System.currentTimeMillis() - t0;
              throttleWaits++;
            }

            // The reader's own progress. Without it the throttle is invisible until the final line,
            // and a long run looks identical whether the reader is waiting or working.
            long nowMs = System.currentTimeMillis();
            if (nowMs - lastReadReport >= READER_REPORT_MS) {
              long elapsed = nowMs - wallStart;
              _log.warn(String.format(
                  "replay: read %d, dispatched %d, rejected %d, filtered " + filtered + " in %.0fs"
                      + " | throttled %.0fs (%.0f%%) | %d pending | active %d | %.0f rec/s dispatched",
                  read, dispatched, rejected, elapsed / 1000.0, throttleMs / 1000.0,
                  elapsed > 0 ? 100.0 * throttleMs / elapsed : 0.0,
                  _vehicleLocationService.getOutstandingTaskCount(),
                  _vehicleLocationService.stripesActiveCount(),
                  elapsed > 0 ? dispatched * 1000.0 / elapsed : 0.0));
              logMonitor(wallStart, firstTs);
              lastReadReport = nowMs;
            }
          } else {
            rejected++;
          }
        }
      } catch (Exception e) {
        _log.error("replay: failed after " + read + " records", e);
        return;
      }

      // Dispatch is not the run. submitForVehicle() only queues a task, so the read loop finishes
      // long before inference does - 279 records "took" 0.6 s while the particle filter was still
      // working. Wait for the stripes to drain, and report the two phases separately so neither can
      // be mistaken for a throughput figure.
      long dispatchMs = System.currentTimeMillis() - wallStart;
      int outstanding = _vehicleLocationService.getOutstandingTaskCount();
      _log.warn(String.format("replay: dispatched %d records in %.1fs; waiting for %d outstanding",
          dispatched, dispatchMs / 1000.0, outstanding));

      /*
       * Poll rather than block, so a long drain reports progress. A 5,585-record run took 19 minutes
       * with no output between dispatch and the final line, which is indistinguishable from a hang.
       *
       * Completed counts come from the stripe pools. Rate is measured over the last interval only, not
       * the whole run, because throughput changes as the JIT warms and as vehicles finish.
       */
      final long PROGRESS_MS = 15000;
      long drainStart = System.currentTimeMillis();
      long drainDeadline = drainStart + DRAIN_TIMEOUT_MS;
      long lastReport = drainStart;
      long lastCompleted = _vehicleLocationService.stripesCompletedTaskCount();
      boolean drained = false;

      while (System.currentTimeMillis() < drainDeadline) {
        if (_vehicleLocationService.awaitIdle(PROGRESS_MS)) {
          drained = true;
          break;
        }
        long nowMs = System.currentTimeMillis();
        long completed = _vehicleLocationService.stripesCompletedTaskCount();
        long remaining = _vehicleLocationService.getOutstandingTaskCount();
        double perSec = (completed - lastCompleted) * 1000.0 / Math.max(1, nowMs - lastReport);
        double pct = dispatched > 0 ? 100.0 * completed / dispatched : 0.0;
        String eta = perSec > 0.01
            ? String.format("%.0fs", remaining / perSec)
            : "unknown";
        _log.warn(String.format(
            "replay: %,d/%,d done (%.1f%%), %,d outstanding, %.1f rec/s, active %d, eta %s, elapsed %.0fs",
            completed, dispatched, pct, remaining, perSec,
            _vehicleLocationService.stripesActiveCount(), eta, (nowMs - drainStart) / 1000.0));
        logMonitor(wallStart, firstTs);
        lastReport = nowMs;
        lastCompleted = completed;
      }
      long wallMs = System.currentTimeMillis() - wallStart;
      double dataS = (lastTs - firstTs) / 1000.0;

      if (!drained)
        _log.error("replay: engine still busy after " + (DRAIN_TIMEOUT_MS / 1000)
            + "s; the timings below are a lower bound and output may be incomplete");

      // Counts are the reconciliation handle: input == dispatched + rejected + unparsed, and the
      // inferred-location output should account for every dispatched record minus whatever the
      // filter itself refused (VehicleInferenceInstance:198 and :245).
      _log.warn(String.format(
          "replay: done. read=%d wrapped=%d dispatched=%d rejected=%d filtered=%d%s"
              + " unparsed=%d outOfOrder=%d"
              + " | data span %.1fs | dispatch %.1fs (throttled %.1fs over %d waits),"
              + " drain %.1fs, total %.1fs wall (%.1fx) | %.0f rec/s inferred"
              + " | virtual clock now %d",
          read, wrapped, dispatched, rejected, filtered,
          _routeFilter == null ? "" : " (" + _admittedVehicles.size() + " vehicles admitted)",
          unparsed, outOfOrder, dataS,
          dispatchMs / 1000.0, throttleMs / 1000.0, throttleWaits,
          (wallMs - dispatchMs) / 1000.0, wallMs / 1000.0,
          wallMs > 0 ? dataS * 1000.0 / wallMs : 0.0,
          wallMs > 0 ? dispatched * 1000.0 / wallMs : 0.0, _clock.millis()));

      if (Boolean.getBoolean("replay.exitWhenDone")) {
        // A replay is a batch job inside a server: Jetty keeps the JVM alive after the last record,
        // so an unattended run would never finish. The linger covers the output side, where
        // OutputQueueSenderServiceImpl's SendThread may still hold records in a bounded buffer whose
        // depth is not exposed; exiting immediately would truncate the tail.
        long lingerMs = Long.getLong("replay.exitLingerMs", 5000L);
        _log.warn("replay: exitWhenDone set; flushing output for " + lingerMs + "ms then exiting");
        try {
          Thread.sleep(lingerMs);
        } catch (InterruptedException ie) {
          Thread.currentThread().interrupt();
        }
        _log.warn("replay: exiting with status " + (drained ? 0 : 1));
        System.exit(drained ? 0 : 1);
      }
    }
  }
}
