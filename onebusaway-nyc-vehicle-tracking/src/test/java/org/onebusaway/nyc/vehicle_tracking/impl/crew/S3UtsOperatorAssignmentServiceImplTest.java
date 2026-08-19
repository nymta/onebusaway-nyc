package org.onebusaway.nyc.vehicle_tracking.impl.crew;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assume.assumeTrue;

import java.io.File;
import java.net.URL;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.Test;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.gtfs.model.calendar.ServiceDate;
import org.onebusaway.nyc.transit_data_federation.model.tdm.OperatorAssignmentItem;

public class S3UtsOperatorAssignmentServiceImplTest {

  private static final AgencyAndId OPERATOR = new AgencyAndId("MTA NYCT", "706005");

  @Test
  public void refreshFromLocalCisFile() throws Exception {
    ServiceDate fixtureDate = ServiceDate.parseString("20120727");
    S3UtsOperatorAssignmentServiceImpl service = new S3UtsOperatorAssignmentServiceImpl();
    service.setLocalCisFile(fixture("CIS_20120727.txt"));
    service.setApplicableServiceDatesForTest(java.util.Collections.singleton(fixtureDate));
    service.refreshData();

    OperatorAssignmentItem item = service.getOperatorAssignmentItemForServiceDate(
        fixtureDate, new AgencyAndId("MTA NYCT", "706005"));
    assertNotNull(item);
    assertEquals("MISC-CSTT-455", item.getRunId());
  }

  @Test
  public void liveS3Download() throws Exception {
    String accessKey = System.getenv("AWS_ACCESS_KEY_ID");
    String secretKey = System.getenv("AWS_SECRET_ACCESS_KEY");
    assumeTrue(accessKey != null && secretKey != null && accessKey.length() > 2);

    S3UtsOperatorAssignmentServiceImpl service = new S3UtsOperatorAssignmentServiceImpl();
    service.refreshData();

    java.util.Collection<OperatorAssignmentItem> today =
        service.getOperatorsForServiceDate(new ServiceDate(new java.util.Date()));
    assertNotNull(today);
    org.junit.Assert.assertTrue("expected non-empty roster from s3://mtabuscis-uts-archive/latest/CIS.txt",
        today.size() > 1000);
  }

  /**
   * A date with no CIS rows (e.g. tomorrow, not yet published) must be cached empty by the
   * scheduled refresh, so lookups never trigger on-miss refreshes. Regression test for the
   * nightly 00:00 UTC inference stall.
   */
  @Test
  public void applicableDateWithNoRowsIsCachedEmpty() throws Exception {
    ServiceDate fixtureDate = ServiceDate.parseString("20120727");
    ServiceDate emptyDate = ServiceDate.parseString("20120728");

    AtomicInteger refreshes = new AtomicInteger();
    S3UtsOperatorAssignmentServiceImpl service = countingService(refreshes);
    service.setLocalCisFile(fixture("CIS_20120727.txt"));
    service.setApplicableServiceDatesForTest(
        new HashSet<ServiceDate>(Arrays.asList(fixtureDate, emptyDate)));
    service.refreshData();
    assertEquals(1, refreshes.get());

    assertNull(service.getOperatorAssignmentItemForServiceDate(emptyDate, OPERATOR));
    assertNull(service.getOperatorAssignmentItemForServiceDate(emptyDate, OPERATOR));
    assertEquals("empty applicable date must not trigger on-miss refreshes", 1, refreshes.get());

    assertNotNull(service.getOperatorsForServiceDate(emptyDate));
    assertTrue(service.getOperatorsForServiceDate(emptyDate).isEmpty());

    assertNotNull(service.getOperatorAssignmentItemForServiceDate(fixtureDate, OPERATOR));
  }

  /** A date outside the applicable window costs one on-miss refresh, then is negative-cached. */
  @Test
  public void unknownDateIsNegativeCachedAfterOneRefresh() throws Exception {
    ServiceDate fixtureDate = ServiceDate.parseString("20120727");
    ServiceDate unknownDate = ServiceDate.parseString("20130101");

    AtomicInteger refreshes = new AtomicInteger();
    S3UtsOperatorAssignmentServiceImpl service = countingService(refreshes);
    service.setLocalCisFile(fixture("CIS_20120727.txt"));
    service.setApplicableServiceDatesForTest(Collections.singleton(fixtureDate));
    service.refreshData();
    assertEquals(1, refreshes.get());

    assertNull(service.getOperatorAssignmentItemForServiceDate(unknownDate, OPERATOR));
    assertEquals(2, refreshes.get());

    assertNull(service.getOperatorAssignmentItemForServiceDate(unknownDate, OPERATOR));
    assertNull(service.getOperatorAssignmentItemForServiceDate(unknownDate, OPERATOR));
    assertEquals("unknown date must be negative-cached after one refresh", 2, refreshes.get());
  }

  @Test
  public void concurrentMissesTriggerAtMostOneRefresh() throws Exception {
    final ServiceDate unknownDate = ServiceDate.parseString("20130101");

    AtomicInteger refreshes = new AtomicInteger();
    final S3UtsOperatorAssignmentServiceImpl service = countingService(refreshes);
    service.setLocalCisFile(fixture("CIS_20120727.txt"));
    service.setApplicableServiceDatesForTest(
        Collections.singleton(ServiceDate.parseString("20120727")));
    service.refreshData();
    assertEquals(1, refreshes.get());

    ExecutorService pool = Executors.newFixedThreadPool(8);
    try {
      Callable<OperatorAssignmentItem> lookup = new Callable<OperatorAssignmentItem>() {
        @Override
        public OperatorAssignmentItem call() throws Exception {
          return service.getOperatorAssignmentItemForServiceDate(unknownDate, OPERATOR);
        }
      };
      List<Future<OperatorAssignmentItem>> results =
          pool.invokeAll(Collections.nCopies(32, lookup));
      for (Future<OperatorAssignmentItem> result : results) {
        assertNull(result.get());
      }
    } finally {
      pool.shutdownNow();
    }
    assertEquals("concurrent misses must collapse to a single refresh", 2, refreshes.get());
  }

  private static S3UtsOperatorAssignmentServiceImpl countingService(final AtomicInteger refreshes) {
    return new S3UtsOperatorAssignmentServiceImpl() {
      @Override
      public void refreshData() {
        refreshes.incrementAndGet();
        super.refreshData();
      }
    };
  }

  private File fixture(String name) throws Exception {
    URL url = getClass().getResource("/uts/" + name);
    assertNotNull("missing test fixture " + name, url);
    return new File(url.toURI());
  }
}
