package org.onebusaway.nyc.vehicle_tracking.impl.crew;

import java.io.File;
import java.time.Clock;
import java.util.Calendar;
import java.util.Collection;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ScheduledFuture;

import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;

import org.joda.time.DateMidnight;
import org.joda.time.format.DateTimeFormatter;
import org.joda.time.format.ISODateTimeFormat;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.gtfs.model.calendar.ServiceDate;
import org.onebusaway.nyc.transit_data_federation.model.tdm.OperatorAssignmentItem;
import org.onebusaway.nyc.transit_data_federation.services.tdm.OperatorAssignmentService;
import org.onebusaway.nyc.transit_data_manager.adapters.api.processes.UtsCrewAssignmentParser;
import org.onebusaway.nyc.transit_data_manager.adapters.output.model.json.OperatorAssignment;
import org.onebusaway.nyc.util.replay.ReplayDomain;
import org.onebusaway.nyc.util.replay.Replayable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;

/**
 * Operator-assignment service backed by the UTS CIS file in S3 (latest/CIS.txt).
 * Functionally equivalent to TDM's crew API for inference trip matching.
 */
public class S3UtsOperatorAssignmentServiceImpl implements OperatorAssignmentService {

  private static Logger _log = LoggerFactory.getLogger(S3UtsOperatorAssignmentServiceImpl.class);

  private static final DateTimeFormatter UPDATED_DATE_FORMATTER = ISODateTimeFormat.dateTimeNoMillis();
  private static final long MAX_SERVICE_DATE_DELTA = 1000L * 60 * 60 * 24 * 2;

  private volatile Map<ServiceDate, HashMap<String, OperatorAssignmentItem>> _serviceDateToOperatorListMap =
      new HashMap<ServiceDate, HashMap<String, OperatorAssignmentItem>>();

  private final Object _refreshLock = new Object();

  private volatile Date _lastS3Modified = null;
  private File _localCisFile;
  private Set<ServiceDate> _testServiceDates;

  /** Directory of prefetched archive snapshots. Set for replay; unset in production. */
  private static final String SNAPSHOT_DIR_PROPERTY = "oba.crew.snapshotDir";

  private volatile CrewSnapshotIndex _snapshotIndex;

  /** The snapshot whose contents are in the map, so a change of snapshot means a reload is due. */
  private volatile File _activeSnapshot;

  private S3UtsCrewAssignmentFetcher _fetcher = new S3UtsCrewAssignmentFetcher();

  private ScheduledFuture<?> _updateTask = null;

  @Autowired
  private ThreadPoolTaskScheduler _taskScheduler;

  /**
   * Bean obaClock: Clock.systemUTC() in every profile except replay, MutableClock under replay. Not
   * required, so tests can construct this class without a Spring context.
   */
  @Autowired(required = false)
  private Clock _clock;

  /** Visible for tests. */
  void setFetcher(S3UtsCrewAssignmentFetcher fetcher) {
    _fetcher = fetcher;
  }

  /** Load roster from a local CIS file instead of S3 (tests). */
  void setLocalCisFile(File localCisFile) {
    _localCisFile = localCisFile;
  }

  /** Override applicable service dates (tests with historical CIS fixtures). */
  void setApplicableServiceDatesForTest(Set<ServiceDate> dates) {
    _testServiceDates = dates;
  }

  @PostConstruct
  private void startUpdateProcess() {
    String snapshotDir = System.getProperty(SNAPSHOT_DIR_PROPERTY, "").trim();
    if (!snapshotDir.isEmpty()) {
      try {
        _snapshotIndex = new CrewSnapshotIndex(new File(snapshotDir));
      } catch (Exception e) {
        throw new IllegalStateException(SNAPSHOT_DIR_PROPERTY + "=" + snapshotDir + " is unusable", e);
      }
      // No startup fetch and no timer: the roster is a function of the clock, reloaded by getMap()
      // when the clock crosses into the next snapshot.
      _log.info("UTS roster follows the replay clock over {} snapshots in {}",
          _snapshotIndex.size(), snapshotDir);
      return;
    }
    refreshData();
    int seconds = 30 * 60;
    String interval = System.getProperty("oba.crew.refreshIntervalSec");
    if (interval != null) {
      try {
        seconds = Integer.parseInt(interval);
      } catch (NumberFormatException e) {
        _log.warn("Invalid oba.crew.refreshIntervalSec={}", interval);
      }
    }
    _log.info("UTS S3 crew refresh interval={}s", seconds);
    _updateTask = _taskScheduler.scheduleWithFixedDelay(new CrewRefreshTask(), seconds * 1000L);
  }

  @Replayable(ReplayDomain.INFERENCE_INPUT)
  private class CrewRefreshTask implements Runnable {
    @Override
    public void run() {
      refreshData();
    }
  }

  @PreDestroy
  public void destroy() {
    if (_updateTask != null) {
      _updateTask.cancel(true);
    }
  }

  // Skips UTS entirely: both the scheduled refresh and the cache-miss refresh in getMap(). A miss has
  // no negative caching, so a roster the bucket cannot supply is re-fetched on every lookup.
  private static final boolean DISABLED = Boolean.getBoolean("oba.crew.disabled");

  public void refreshData() {
    if (DISABLED)
      return;
    synchronized (_refreshLock) {
      refreshDataLocked();
    }
  }

  private void refreshDataLocked() {
    try {
      File cisFile = resolveCisFile();
      if (cisFile == null || !cisFile.exists()) {
        _log.error("UTS CIS file unavailable; operator assignments not refreshed");
        return;
      }

      // Marked before parsing: a parse failure must not leave the snapshot due, or every subsequent
      // record retries it.
      _activeSnapshot = cisFile;

      Map<ServiceDate, HashMap<String, OperatorAssignmentItem>> updated =
          new HashMap<ServiceDate, HashMap<String, OperatorAssignmentItem>>();

      for (ServiceDate serviceDate : applicableServiceDates()) {
        DateMidnight midnight = new DateMidnight(serviceDate.getYear(), serviceDate.getMonth(),
            serviceDate.getDay());
        HashMap<String, OperatorAssignment> parsed =
            UtsCrewAssignmentParser.loadForServiceDate(cisFile, midnight);
        HashMap<String, OperatorAssignmentItem> map = toItems(parsed);
        if (map != null && !map.isEmpty()) {
          updated.put(serviceDate, map);
          _log.info("Loaded {} UTS operator assignments for serviceDate={}", map.size(), serviceDate);
        }
      }

      _serviceDateToOperatorListMap = updated;
    } catch (Exception e) {
      _log.error("UTS crew refresh failed: {}", e.getMessage(), e);
    }
  }

  /** The snapshot that was current at the clock's instant, or null outside snapshot mode. */
  private File snapshotDue() {
    CrewSnapshotIndex index = _snapshotIndex;
    if (index == null) {
      return null;
    }
    return index.asOf(_clock != null ? _clock.millis() : System.currentTimeMillis());
  }

  private boolean reloadDue() {
    File due = snapshotDue();
    return due != null && !due.equals(_activeSnapshot);
  }

  private HashMap<String, OperatorAssignmentItem> toItems(HashMap<String, OperatorAssignment> parsed) {
    HashMap<String, OperatorAssignmentItem> output = new HashMap<String, OperatorAssignmentItem>();
    for (Map.Entry<String, OperatorAssignment> entry : parsed.entrySet()) {
      OperatorAssignmentItem item = toItem(entry.getValue());
      if (item != null) {
        output.put(entry.getKey(), item);
      }
    }
    return output;
  }

  private static OperatorAssignmentItem toItem(OperatorAssignment oa) {
    if (oa.getPassId() == null || oa.getAgencyId() == null) {
      return null;
    }
    OperatorAssignmentItem item = new OperatorAssignmentItem();
    item.setAgencyId(oa.getAgencyId());
    item.setPassId(oa.getPassId());
    item.setRunRoute(oa.getRunRoute());
    item.setRunNumber(oa.getRunNumber());
    item.setRunId(oa.getRunId());
    item.setDepot(oa.getDepot());
    if (oa.getServiceDate() != null) {
      try {
        item.setServiceDate(ServiceDate.parseString(oa.getServiceDate().replace("-", "")));
      } catch (java.text.ParseException e) {
        _log.warn("Could not parse service date {} for pass {}", oa.getServiceDate(), oa.getPassId());
      }
    }
    if (oa.getUpdated() != null) {
      item.setUpdated(UPDATED_DATE_FORMATTER.parseDateTime(oa.getUpdated()));
    }
    return item;
  }

  private File resolveCisFile() throws Exception {
    if (_localCisFile != null) {
      return _localCisFile;
    }
    if (_snapshotIndex != null) {
      return snapshotDue();
    }
    String cachePath = System.getProperty("oba.crew.cacheFile", "/tmp/oba-uts-cis.csv");
    File target = new File(cachePath);
    File downloaded = _fetcher.downloadIfChanged(_lastS3Modified, target);
    _lastS3Modified = _fetcher.getLastModified();
    return downloaded;
  }

  /**
   * "Now" for service-date purposes. Reads the injected clock rather than the wall clock, so that a
   * replay of archived data loads the roster for the dates the records carry. Falls back to the wall
   * clock when the field is unset, which is the case for tests that construct this class directly.
   */
  private Calendar nowCalendar() {
    Calendar cal = Calendar.getInstance();
    if (_clock != null) {
      cal.setTimeInMillis(_clock.millis());
    }
    return cal;
  }

  private Set<ServiceDate> applicableServiceDates() {
    if (_testServiceDates != null) {
      return _testServiceDates;
    }
    Set<ServiceDate> dates = new HashSet<ServiceDate>();
    Calendar cal = nowCalendar();
    dates.add(new ServiceDate(cal.getTime()));
    cal.add(Calendar.DAY_OF_YEAR, -1);
    dates.add(new ServiceDate(cal.getTime()));
    cal.add(Calendar.DAY_OF_YEAR, 2);
    dates.add(new ServiceDate(cal.getTime()));
    return dates;
  }

  boolean isApplicable(ServiceDate serviceDate) {
    if (serviceDate == null) {
      return false;
    }
    Calendar cal = nowCalendar();
    ServiceDate now = new ServiceDate(cal.get(Calendar.YEAR), cal.get(Calendar.MONTH) + 1,
        cal.get(Calendar.DAY_OF_MONTH));
    return Math.abs(now.getAsDate().getTime() - serviceDate.getAsDate().getTime()) < MAX_SERVICE_DATE_DELTA;
  }

  @Override
  public Collection<OperatorAssignmentItem> getOperatorsForServiceDate(ServiceDate serviceDate)
      throws Exception {
    HashMap<String, OperatorAssignmentItem> list = getMap(serviceDate);
    return list != null ? list.values() : null;
  }

  @Override
  public OperatorAssignmentItem getOperatorAssignmentItemForServiceDate(ServiceDate serviceDate,
      AgencyAndId operatorId) throws Exception {
    HashMap<String, OperatorAssignmentItem> list = getMap(serviceDate);
    if (list == null) {
      throw new Exception("Operator service is temporarily not available.");
    }
    return list.get(operatorId.toString());
  }

  private HashMap<String, OperatorAssignmentItem> getMap(ServiceDate serviceDate) throws Exception {
    if (serviceDate == null) {
      return null;
    }
    HashMap<String, OperatorAssignmentItem> list = _serviceDateToOperatorListMap.get(serviceDate);
    if (list != null && !reloadDue()) {
      return list;
    }
    synchronized (_refreshLock) {
      if (reloadDue()) {
        refreshDataLocked();
      }
      list = _serviceDateToOperatorListMap.get(serviceDate);
      if (list != null) {
        return list;
      }
      if (DISABLED)
        return null;
      if (_snapshotIndex != null) {
        // Already loaded the snapshot the clock selects, so this date is genuinely absent from it.
        // Re-reading would repeat the parse on every record.
        return null;
      }
      refreshData();
      return _serviceDateToOperatorListMap.get(serviceDate);
    }
  }
}
