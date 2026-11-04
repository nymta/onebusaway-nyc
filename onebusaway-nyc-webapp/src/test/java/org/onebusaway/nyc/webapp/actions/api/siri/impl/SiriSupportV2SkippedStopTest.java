package org.onebusaway.nyc.webapp.actions.api.siri.impl;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.mockito.Matchers.any;
import static org.mockito.Matchers.anyLong;
import static org.mockito.Matchers.anyString;
import static org.mockito.Matchers.anyDouble;
import static org.mockito.Matchers.anyInt;
import static org.mockito.Matchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.junit.Before;
import org.junit.Test;
import org.onebusaway.geospatial.model.CoordinatePoint;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.presentation.impl.realtime.SiriSupportPredictionTimepointRecord;
import org.onebusaway.nyc.presentation.service.realtime.PresentationService;
import org.onebusaway.nyc.transit_data.services.NycTransitDataService;
import org.onebusaway.nyc.webapp.actions.api.siri.impl.SiriSupportV2.OnwardCallsMode;
import org.onebusaway.nyc.webapp.actions.api.siri.model.DetailLevel;
import org.onebusaway.realtime.api.TimepointPredictionRecord;
import org.onebusaway.transit_data.model.AgencyBean;
import org.onebusaway.transit_data.model.RouteBean;
import org.onebusaway.transit_data.model.StopBean;
import org.onebusaway.transit_data.model.blocks.BlockConfigurationBean;
import org.onebusaway.transit_data.model.blocks.BlockInstanceBean;
import org.onebusaway.transit_data.model.blocks.BlockStopTimeBean;
import org.onebusaway.transit_data.model.blocks.BlockTripBean;
import org.onebusaway.transit_data.model.schedule.StopTimeBean;
import org.onebusaway.transit_data.model.trips.TripBean;
import org.onebusaway.transit_data.model.trips.TripStatusBean;

import uk.org.siri.siri_2.CallStatusEnumeration;
import uk.org.siri.siri_2.MonitoredVehicleJourneyStructure;
import uk.org.siri.siri_2.OnwardCallStructure;

/**
 * The siri_2 twin of SkippedStopSupportTest: a stop the predictions feed marks SKIPPED gets no
 * expected time, is marked CANCELLED, and is what the v2 realtime service strips when the request
 * did not ask for cancelled trips.
 */
public class SiriSupportV2SkippedStopTest {

  private static final String TRIP_ID = "MTA_TRIP";

  private NycTransitDataService tds;
  private PresentationService presentation;
  private TripBean trip;
  private TripStatusBean status;
  private long now;

  @Before
  public void before() {
    now = System.currentTimeMillis();
    trip = tripBean();
    status = new TripStatusBean();
    status.setLocation(new CoordinatePoint(40.7, -74.0));
    status.setPhase("IN_PROGRESS");
    status.setStatus("normal");
    status.setActiveTrip(trip);
    status.setDistanceAlongTrip(250); // stops 3, 4 and 5 ahead
    status.setVehicleId("MTA NYCT_1");
    StopBean next = new StopBean();
    next.setId("3");
    status.setNextStop(next);

    BlockConfigurationBean config = new BlockConfigurationBean();
    config.setBlockId("BLOCK");
    BlockTripBean blockTrip = new BlockTripBean();
    blockTrip.setTrip(trip);
    blockTrip.setDistanceAlongBlock(0);
    blockTrip.setBlockStopTimes(blockStopTimes());
    List<BlockTripBean> trips = new ArrayList<>();
    trips.add(blockTrip);
    config.setTrips(trips);
    BlockInstanceBean block = new BlockInstanceBean();
    block.setBlockConfiguration(config);

    tds = mock(NycTransitDataService.class);
    when(tds.getBlockInstance(eq("BLOCK"), anyLong())).thenReturn(block);
    when(tds.getLastVehicleOccupancyRecordForVehicleId(any(AgencyAndId.class))).thenReturn(null);
    when(tds.stopHasRevenueServiceOnRoute(anyString(), anyString(), anyString(), anyString())).thenReturn(true);

    presentation = mock(PresentationService.class);
    when(presentation.hasFormalBlockLevelMatch(any(TripStatusBean.class))).thenReturn(true);
    when(presentation.isOnDetour(any(TripStatusBean.class))).thenReturn(false);
    when(presentation.getPresentableDistance(anyDouble(), anyInt())).thenReturn("near");
  }

  @Test
  public void skippedStopsGetNoTimeAndAreMarkedCancelled() {
    MonitoredVehicleJourneyStructure journey = journey(predictions("3"));

    assertNull(journey.getMonitoredCall().getExpectedArrivalTime());
    assertNull(journey.getMonitoredCall().getExpectedDepartureTime());
    assertEquals(CallStatusEnumeration.CANCELLED, journey.getMonitoredCall().getArrivalStatus());
    assertTrue(SiriSupportV2.hasSkippedMonitoredCall(journey));

    List<OnwardCallStructure> calls = journey.getOnwardCalls().getOnwardCall();
    assertEquals(3, calls.size());
    assertEquals("3", calls.get(0).getStopPointRef().getValue());
    assertNull(calls.get(0).getExpectedArrivalTime());
    assertEquals(CallStatusEnumeration.CANCELLED, calls.get(0).getArrivalStatus());
    assertNotNull(calls.get(1).getExpectedArrivalTime());
    assertNull(calls.get(1).getArrivalStatus());
  }

  @Test
  public void removeSkippedOnwardCallsKeepsServedStops() {
    MonitoredVehicleJourneyStructure journey = journey(predictions("3", "4"));

    SiriSupportV2.removeSkippedOnwardCalls(journey);

    List<OnwardCallStructure> calls = journey.getOnwardCalls().getOnwardCall();
    assertEquals(1, calls.size());
    assertEquals("5", calls.get(0).getStopPointRef().getValue());
    assertNotNull(calls.get(0).getExpectedArrivalTime());
  }

  @Test
  public void noSkipLeavesJourneyUntouched() {
    MonitoredVehicleJourneyStructure journey = journey(predictions());

    assertFalse(SiriSupportV2.hasSkippedMonitoredCall(journey));
    assertNotNull(journey.getMonitoredCall().getExpectedArrivalTime());
    SiriSupportV2.removeSkippedOnwardCalls(journey);
    assertEquals(3, journey.getOnwardCalls().getOnwardCall().size());
  }

  private MonitoredVehicleJourneyStructure journey(Map<String, SiriSupportPredictionTimepointRecord> predictions) {
    MonitoredVehicleJourneyStructure journey = new MonitoredVehicleJourneyStructure();
    SiriSupportV2.fillMonitoredVehicleJourney(journey, trip, status, null, OnwardCallsMode.VEHICLE_MONITORING,
        presentation, tds, Integer.MAX_VALUE, predictions, DetailLevel.CALLS, now, null, false, false, false);
    return journey;
  }

  /** A prediction 5 minutes out at stops 3–5, with the given stops marked skipped instead. */
  private Map<String, SiriSupportPredictionTimepointRecord> predictions(String... skipped) {
    Map<String, SiriSupportPredictionTimepointRecord> map = new HashMap<>();
    for (String stopId : new String[] {"3", "4", "5"}) {
      TimepointPredictionRecord tpr = new TimepointPredictionRecord();
      tpr.setTimepointId(new AgencyAndId("MTA", stopId));
      boolean skip = false;
      for (String s : skipped) {
        skip |= s.equals(stopId);
      }
      if (skip) {
        tpr.setScheduleRealtionship(TimepointPredictionRecord.ScheduleRelationship.SKIPPED.getValue());
      } else {
        tpr.setTimepointPredictedArrivalTime(now + 300_000);
        tpr.setTimepointPredictedDepartureTime(now + 300_000);
      }
      SiriSupportPredictionTimepointRecord r = new SiriSupportPredictionTimepointRecord();
      r.setTripId(TRIP_ID);
      r.setStopId(stopId);
      r.setTimepointPredictionRecord(tpr);
      map.put(r.getKey(), r);
    }
    return map;
  }

  private static TripBean tripBean() {
    TripBean t = new TripBean();
    t.setId(TRIP_ID);
    t.setBlockId("BLOCK");
    RouteBean.Builder route = RouteBean.builder();
    route.setAgency(new AgencyBean());
    route.setId("MTA_ROUTE");
    t.setRoute(route.create());
    return t;
  }

  private static List<BlockStopTimeBean> blockStopTimes() {
    List<BlockStopTimeBean> out = new ArrayList<>();
    for (int i = 1; i < 6; i++) {
      StopBean stop = new StopBean();
      stop.setId(Integer.toString(i));
      StopTimeBean st = new StopTimeBean();
      st.setArrivalTime(36000 + 100 * i);
      st.setDepartureTime(36000 + 100 * i);
      st.setStop(stop);
      BlockStopTimeBean bst = new BlockStopTimeBean();
      bst.setDistanceAlongBlock(100 * i);
      bst.setStopTime(st);
      out.add(bst);
    }
    return out;
  }
}
