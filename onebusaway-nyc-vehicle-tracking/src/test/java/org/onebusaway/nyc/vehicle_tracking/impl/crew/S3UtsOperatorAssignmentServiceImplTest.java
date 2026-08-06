package org.onebusaway.nyc.vehicle_tracking.impl.crew;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assume.assumeTrue;

import java.io.File;
import java.net.URL;

import org.junit.Test;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.gtfs.model.calendar.ServiceDate;
import org.onebusaway.nyc.transit_data_federation.model.tdm.OperatorAssignmentItem;

public class S3UtsOperatorAssignmentServiceImplTest {

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

  private File fixture(String name) throws Exception {
    URL url = getClass().getResource("/uts/" + name);
    assertNotNull("missing test fixture " + name, url);
    return new File(url.toURI());
  }
}
