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
package org.onebusaway.nyc.presentation.impl.realtime;

import org.junit.Before;
import org.junit.Test;
import org.onebusaway.geospatial.model.CoordinatePoint;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.presentation.impl.realtime.siri.OnwardCallsMode;
import org.onebusaway.nyc.presentation.impl.realtime.siri.SiriBuilderServiceHelperImpl;
import org.onebusaway.nyc.presentation.impl.realtime.siri.SiriMonitoredCallBuilderServiceImpl;
import org.onebusaway.nyc.presentation.impl.realtime.siri.SiriMonitoredVehicleJourneyBuilderServiceImpl;
import org.onebusaway.nyc.presentation.impl.realtime.siri.SiriOnwardCallsBuilderServiceImpl;
import org.onebusaway.nyc.presentation.service.realtime.PresentationService;
import org.onebusaway.nyc.siri.support.SiriDistanceExtension;
import org.onebusaway.nyc.transit_data.services.NycTransitDataService;
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
import uk.org.siri.siri.OnwardCallStructure;
import uk.org.siri.siri.ProgressStatusEnumeration;
import uk.org.siri.siri.VehicleActivityStructure.MonitoredVehicleJourney;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;

import static org.junit.Assert.*;
import static org.mockito.Matchers.*;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * A stop the predictions feed marks SKIPPED must never get an expected time (the builders would
 * render the record's unknown time as "now"), is marked CANCELLED, and is what the realtime
 * services strip when the request did not ask for cancelled service.
 */
public class SkippedStopSupportTest {

  private static final String TRIP_ID = "TRIP";

  private SiriMonitoredVehicleJourneyBuilderServiceImpl mvjService;
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
    status.setDistanceAlongTrip(250);//stops 3, 4 and 5 ahead
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

    NycTransitDataService tds = mock(NycTransitDataService.class);
    when(tds.getBlockInstance(eq("BLOCK"), anyLong())).thenReturn(block);
    when(tds.getLastVehicleOccupancyRecordForVehicleId(any(AgencyAndId.class))).thenReturn(null);
    when(tds.stopHasRevenueServiceOnRoute(anyString(), anyString(), anyString(), anyString())).thenReturn(true);
    when(tds.getTripModificationDiff(any(AgencyAndId.class), anyString())).thenReturn(null);

    PresentationService presentation = mock(PresentationService.class);
    when(presentation.hasFormalBlockLevelMatch(any(TripStatusBean.class))).thenReturn(true);
    when(presentation.isOnDetour(any(TripStatusBean.class))).thenReturn(false);
    when(presentation.getPresentableDistance(any(SiriDistanceExtension.class))).thenReturn("near");

    SiriBuilderServiceHelperImpl helper = new SiriBuilderServiceHelperImpl();
    helper.setNycTransitDataService(tds);

    SiriMonitoredCallBuilderServiceImpl monitoredCalls = new SiriMonitoredCallBuilderServiceImpl();
    monitoredCalls.setNycTransitDataService(tds);
    monitoredCalls.setPresentationService(presentation);
    monitoredCalls.setSiriBuilderServiceHelper(helper);

    SiriOnwardCallsBuilderServiceImpl onwardCalls = new SiriOnwardCallsBuilderServiceImpl();
    onwardCalls.setNycTransitDataService(tds);
    onwardCalls.setPresentationService(presentation);
    onwardCalls.setSiriBuilderServiceHelper(helper);

    mvjService = new SiriMonitoredVehicleJourneyBuilderServiceImpl();
    mvjService.setNycTransitDataService(tds);
    mvjService.setPresentationService(presentation);
    mvjService.setMonitoredCallBuilderService(monitoredCalls);
    mvjService.setSiriOnwardCallsBuilderService(onwardCalls);
  }

  @Test
  public void skippedStopsGetNoTimeAndAreMarkedCancelled() {
    MonitoredVehicleJourney journey = journey(predictions("3"));

    assertNull(journey.getMonitoredCall().getExpectedArrivalTime());
    assertNull(journey.getMonitoredCall().getExpectedDepartureTime());
    assertEquals(ProgressStatusEnumeration.CANCELLED, journey.getMonitoredCall().getArrivalStatus());
    assertTrue(SkippedStopSupport.hasSkippedMonitoredCall(journey));

    List<OnwardCallStructure> calls = journey.getOnwardCalls().getOnwardCall();
    assertEquals(3, calls.size());
    assertEquals("3", calls.get(0).getStopPointRef().getValue());
    assertNull(calls.get(0).getExpectedArrivalTime());
    assertEquals(ProgressStatusEnumeration.CANCELLED, calls.get(0).getArrivalStatus());
    assertNotNull(calls.get(1).getExpectedArrivalTime());
    assertNull(calls.get(1).getArrivalStatus());
  }

  @Test
  public void removeSkippedOnwardCallsKeepsServedStops() {
    MonitoredVehicleJourney journey = journey(predictions("3", "4"));

    SkippedStopSupport.removeSkippedOnwardCalls(journey);

    List<OnwardCallStructure> calls = journey.getOnwardCalls().getOnwardCall();
    assertEquals(1, calls.size());
    assertEquals("5", calls.get(0).getStopPointRef().getValue());
    assertNotNull(calls.get(0).getExpectedArrivalTime());
  }

  @Test
  public void noSkipLeavesJourneyUntouched() {
    MonitoredVehicleJourney journey = journey(predictions());

    assertFalse(SkippedStopSupport.hasSkippedMonitoredCall(journey));
    assertNotNull(journey.getMonitoredCall().getExpectedArrivalTime());
    SkippedStopSupport.removeSkippedOnwardCalls(journey);
    assertEquals(3, journey.getOnwardCalls().getOnwardCall().size());
  }

  private MonitoredVehicleJourney journey(Map<String, SiriSupportPredictionTimepointRecord> predictions) {
    return mvjService.makeMonitoredVehicleJourney(trip, status, null, predictions,
            OnwardCallsMode.VEHICLE_MONITORING, Integer.MAX_VALUE, now, false, false, false, new HashSet<>());
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
    route.setId("ROUTE");
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
