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
package org.onebusaway.nyc.gtfsrt.tests;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.mockito.Matchers.any;
import static org.mockito.Matchers.anyLong;
import static org.mockito.Matchers.anyString;
import static org.mockito.Mockito.doNothing;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

import org.junit.Assume;
import org.junit.Test;
import org.mockito.invocation.InvocationOnMock;
import org.mockito.stubbing.Answer;
import org.onebusaway.nyc.gtfsrt.impl.TripUpdateFeedBuilderImpl;
import org.onebusaway.nyc.gtfsrt.impl.TripUpdateServiceImpl;
import org.onebusaway.nyc.gtfsrt.service.TripUpdateFeedBuilder;
import org.onebusaway.nyc.gtfsrt.util.GtfsRealtimeLibrary;
import org.onebusaway.nyc.presentation.service.realtime.PresentationService;
import org.onebusaway.nyc.transit_data.services.NycTransitDataService;
import org.onebusaway.nyc.transit_data_federation.impl.predictions.PredictionCancelledTripsSource;
import org.onebusaway.nyc.util.configuration.ConfigurationService;
import org.onebusaway.realtime.api.TimepointPredictionRecord;
import org.onebusaway.transit_data.model.ListBean;
import org.onebusaway.transit_data.model.RouteBean;
import org.onebusaway.transit_data.model.VehicleStatusBean;
import org.onebusaway.transit_data.model.trip_mods.TripModificationDiff;
import org.onebusaway.transit_data.model.trips.CancelledTripBean;
import org.onebusaway.transit_data.model.trips.TripBean;
import org.onebusaway.transit_data.model.trips.TripDetailsBean;
import org.onebusaway.transit_data.model.trips.TripForVehicleQueryBean;
import org.onebusaway.transit_data.model.trips.TripStatusBean;
import org.onebusaway.transit_data_federation.impl.CancelledTripServiceImpl;

import com.google.transit.realtime.GtfsRealtime.FeedEntity;
import com.google.transit.realtime.GtfsRealtime.FeedMessage;
import com.google.transit.realtime.GtfsRealtime.TripDescriptor;
import com.google.transit.realtime.GtfsRealtime.TripUpdate;

/**
 * Replays a recorded predictions-queue stream through the real cancel path: each FULL_DATASET
 * cancellation message goes into {@link PredictionCancelledTripsSource} backed by the real
 * cancelled-trip service, and the GTFS-RT trip-update service is then asked for its entities.
 * The CANCELED entities must mirror the message exactly: the same trips, each with its own start
 * date and a POSIX-seconds timestamp. A trip missing from the next message must be gone from the
 * feed, and the time-to-live must empty the feed once the producer goes quiet.
 *
 * Input is the length-delimited FeedMessage stream a replay writes; predictions messages in it are
 * skipped, as the queue consumer skips them. Skipped unless -Drecorded.cancellations.pb=/path/to/stream.pb
 * is set. With -Drecorded.cancellations.out=/path/spans.csv it also writes one row per cancelled
 * trip instance (trip, start date, cancel instant, un-cancel instant or blank) as the TDS saw them,
 * for comparison with the producer-side timeline.
 */
public class RecordedCancellationFeedTest {

  private static final long TTL_MS = 90_000L;

  @Test
  public void feedMirrorsEveryRecordedCancellationMessage() throws Exception {
    String path = System.getProperty("recorded.cancellations.pb");
    Assume.assumeTrue(path != null && !path.isEmpty()); // set -Drecorded.cancellations.pb to run
    String out = System.getProperty("recorded.cancellations.out");

    CancelledTripServiceImpl cancelledTrips = new CancelledTripServiceImpl();
    PredictionCancelledTripsSource source = new PredictionCancelledTripsSource();
    source.setCancelledTripService(cancelledTrips);

    final Map<String, String> routeOfTrip = new HashMap<String, String>();
    final Map<String, String> directionOfTrip = new HashMap<String, String>();
    NycTransitDataService tds = mock(NycTransitDataService.class);
    when(tds.getAgenciesWithCoverage()).thenReturn(UnitTestSupport.agenciesWithCoverage("MTA NYCT"));
    when(tds.getTripDetailsForVehicleAndTime(any(TripForVehicleQueryBean.class))).thenReturn(new TripDetailsBean());
    when(tds.getAllVehiclesForAgency(anyString(), anyLong())).thenReturn(
        new ListBean<VehicleStatusBean>(new ArrayList<VehicleStatusBean>(), false));
    final CancelledTripServiceImpl realService = cancelledTrips;
    when(tds.getAllCancelledTrips()).thenAnswer(new Answer<Object>() {
      public Object answer(InvocationOnMock invocation) {
        return realService.getAllCancelledTrips();
      }
    });
    when(tds.getTrip(anyString())).thenAnswer(new Answer<TripBean>() {
      public TripBean answer(InvocationOnMock invocation) {
        String id = (String) invocation.getArguments()[0];
        TripBean trip = new TripBean();
        trip.setId(id);
        RouteBean.Builder route = RouteBean.builder();
        route.setId(routeOfTrip.containsKey(id) ? routeOfTrip.get(id) : "MTA NYCT_?");
        trip.setRoute(route.create());
        trip.setDirectionId(directionOfTrip.containsKey(id) ? directionOfTrip.get(id) : "0");
        return trip;
      }
    });
    PresentationService presentation = mock(PresentationService.class);
    when(presentation.include(any(TripStatusBean.class))).thenReturn(Boolean.TRUE);
    doNothing().when(presentation).setTime(any(Long.class));

    TripUpdateServiceImpl service = new TripUpdateServiceImpl();
    service.setFeedBuilder(feedBuilder());
    service.setTransitDataService(tds);
    service.setPresentationService(presentation);

    Map<String, long[]> spans = new TreeMap<String, long[]>(); // trip@date#cancelMs -> {cancelMs, uncancelMs or -1}
    Set<String> active = new HashSet<String>();
    int messages = 0, cancellationMessages = 0, maxSet = 0;
    long lastTs = -1;
    InputStream in = new BufferedInputStream(new FileInputStream(path), 1 << 20);
    try {
      FeedMessage msg;
      while ((msg = FeedMessage.parseDelimitedFrom(in)) != null) {
        messages++;
        if (!PredictionCancelledTripsSource.isCancellationMessage(msg)) {
          continue;
        }
        cancellationMessages++;
        long ts = msg.getHeader().getTimestamp(); // the engine stamps headers in milliseconds
        lastTs = ts;
        Set<String> expected = new HashSet<String>();
        for (FeedEntity e : msg.getEntityList()) {
          TripDescriptor td = e.getTripUpdate().getTrip();
          if (td.getScheduleRelationship() == TripDescriptor.ScheduleRelationship.CANCELED) {
            expected.add(bare(td.getTripId()) + "@" + td.getStartDate());
            routeOfTrip.put(td.getTripId(), td.getRouteId());
            directionOfTrip.put(td.getTripId(), String.valueOf(td.getDirectionId()));
          }
        }
        source.update(msg, ts);
        Set<String> got = canceledKeys(service.getEntities(ts), ts);
        assertEquals("message at " + ts + " vs feed", expected, got);
        maxSet = Math.max(maxSet, got.size());
        for (String k : expected) {
          if (active.add(k)) {
            spans.put(k + "#" + ts, new long[] { ts, -1 });
          }
        }
        for (String k : new HashSet<String>(active)) {
          if (!expected.contains(k)) {
            active.remove(k);
            closeSpan(spans, k, ts);
          }
        }
      }
    } finally {
      in.close();
    }
    assertTrue("no cancellation messages in " + path, cancellationMessages > 0);

    // a silent producer: the set must drop out of the feed after the time-to-live
    source.expireIfStale(lastTs + TTL_MS + 1000);
    assertEquals(new HashSet<String>(), canceledKeys(service.getEntities(lastTs + TTL_MS + 1000), lastTs + TTL_MS + 1000));

    System.out.println("recorded cancellation feed: " + messages + " messages, " + cancellationMessages
        + " cancellation messages, " + spans.size() + " cancelled trip instances, peak set " + maxSet);
    if (out != null && !out.isEmpty()) {
      PrintWriter w = new PrintWriter(out, "UTF-8");
      try {
        w.println("trip_id,start_date,cancel_ms,uncancel_ms");
        for (Map.Entry<String, long[]> e : spans.entrySet()) {
          String key = e.getKey().substring(0, e.getKey().lastIndexOf('#'));
          String trip = key.substring(0, key.lastIndexOf('@'));
          String date = key.substring(key.lastIndexOf('@') + 1);
          w.println(trip + "," + date + "," + e.getValue()[0] + "," + (e.getValue()[1] < 0 ? "" : String.valueOf(e.getValue()[1])));
        }
      } finally {
        w.close();
      }
    }
  }

  /** GTFS-RT entities carry the bare trip id (the feed builder drops the agency, as it does for predictions). */
  private static String bare(String tripId) {
    int i = tripId.indexOf('_');
    return tripId.startsWith("MTA NYCT_") || tripId.startsWith("MTABC_") ? tripId.substring(i + 1) : tripId;
  }

  private static void closeSpan(Map<String, long[]> spans, String key, long ts) {
    long[] open = null;
    for (Map.Entry<String, long[]> e : spans.entrySet()) {
      if (e.getKey().startsWith(key + "#") && e.getValue()[1] < 0) {
        open = e.getValue();
      }
    }
    if (open != null) {
      open[1] = ts;
    }
  }

  /** CANCELED entities as trip@startDate, checking each has a seconds timestamp and a yyyyMMdd start date. */
  private static Set<String> canceledKeys(List<FeedEntity.Builder> entities, long nowMs) {
    Set<String> keys = new HashSet<String>();
    for (FeedEntity.Builder e : entities) {
      TripUpdate tu = e.getTripUpdate();
      if (tu.getTrip().getScheduleRelationship() != TripDescriptor.ScheduleRelationship.CANCELED) {
        continue;
      }
      assertTrue("timestamp must be POSIX seconds: " + tu.getTimestamp(), tu.getTimestamp() < 100_000_000_000L);
      assertEquals(8, tu.getTrip().getStartDate().length());
      keys.add(bare(tu.getTrip().getTripId()) + "@" + tu.getTrip().getStartDate());
    }
    return keys;
  }

  private static TripUpdateFeedBuilder feedBuilder() {
    return new TripUpdateFeedBuilder() {
      public TripUpdate.Builder makeTripUpdate(TripBean trip, VehicleStatusBean vehicle, Collection<TimepointPredictionRecord> records, TripModificationDiff diff) {
        return TripUpdate.newBuilder().setTrip(TripDescriptor.newBuilder().setTripId(trip.getId()));
      }
      public TripUpdate.Builder makeTripUpdate(TripBean trip, VehicleStatusBean vehicle, Collection<TimepointPredictionRecord> records) {
        return TripUpdate.newBuilder().setTrip(TripDescriptor.newBuilder().setTripId(trip.getId()));
      }
      public TripUpdate.Builder makeCanceledTrip(TripBean trip) {
        TripStatusBean bean = new TripStatusBean();
        bean.setServiceDate(System.currentTimeMillis());
        return GtfsRealtimeLibrary.makeCanceledTrip(trip, bean, null);
      }
      public TripUpdate.Builder makeCanceledTrip(TripBean trip, CancelledTripBean cancelled) {
        return new TripUpdateFeedBuilderImpl().makeCanceledTrip(trip, cancelled);
      }
    };
  }
}
