package org.onebusaway.nyc.transit_data_manager.adapters.api.processes;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.net.URL;
import java.util.HashMap;

import org.joda.time.DateMidnight;
import org.junit.Test;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.transit_data_manager.adapters.output.model.json.OperatorAssignment;

public class UtsCrewAssignmentParserTest {

  @Test
  public void loadsAssignmentsForServiceDate() throws Exception {
    File cis = fixture("CIS_20120727.txt");
    DateMidnight date = new DateMidnight("2012-07-27");

    HashMap<String, OperatorAssignment> map = UtsCrewAssignmentParser.loadForServiceDate(cis, date);

    assertTrue(map.size() > 1000);
    OperatorAssignment item = map.get("MTA NYCT_706005");
    assertNotNull(item);
    assertEquals("455", item.getRunNumber());
    assertEquals("MISC", item.getRunRoute());
    assertEquals("MISC-CSTT-455", item.getRunId());
  }

  @Test
  public void dedupesDuplicatePassNumbers() throws Exception {
    File cis = fixture("CIS_20120730_1602.txt");
    DateMidnight date = new DateMidnight("2012-07-30");

    HashMap<String, OperatorAssignment> map = UtsCrewAssignmentParser.loadForServiceDate(cis, date);

    OperatorAssignment item = map.get("MTA NYCT_387009");
    assertNotNull(item);
    assertEquals("Q4420", item.getRunRoute());
  }

  @Test
  public void lookupKeyMatchesInferenceOperatorIdFormat() throws Exception {
    File cis = fixture("CIS_20120727.txt");
    DateMidnight date = new DateMidnight("2012-07-27");
    HashMap<String, OperatorAssignment> map = UtsCrewAssignmentParser.loadForServiceDate(cis, date);

    OperatorAssignment item = map.get("MTA NYCT_1663");
    assertNotNull(item);
    assertEquals("MTA NYCT", item.getAgencyId());
    assertEquals(new AgencyAndId("MTA NYCT", "1663").toString(), "MTA NYCT_1663");
  }

  private File fixture(String name) throws Exception {
    URL url = getClass().getResource("/uts/" + name);
    assertNotNull("missing test fixture " + name, url);
    return new File(url.toURI());
  }
}
