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
package org.onebusaway.nyc.transit_data_federation.impl.predictions;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.mockito.Matchers.anyInt;
import static org.mockito.Matchers.anyMap;
import static org.mockito.Matchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.Collection;
import java.util.Map;

import org.junit.Before;
import org.junit.Test;
import org.mockito.AdditionalAnswers;
import org.mockito.ArgumentCaptor;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.util.configuration.ConfigurationService;
import org.onebusaway.realtime.api.TimepointPredictionRecord;
import org.onebusaway.transit_data.model.trips.CancelledTripBean;
import org.onebusaway.transit_data.services.TransitDataService;
import org.onebusaway.transit_data_federation.impl.CancelledTripServiceImpl;
import org.onebusaway.transit_data_federation.services.CancelledTripService;

import com.google.common.cache.CacheBuilder;
import com.google.transit.realtime.GtfsRealtime.FeedEntity;
import com.google.transit.realtime.GtfsRealtime.FeedHeader;
import com.google.transit.realtime.GtfsRealtime.FeedHeader.Incrementality;
import com.google.transit.realtime.GtfsRealtime.FeedMessage;
import com.google.transit.realtime.GtfsRealtime.TripDescriptor;
import com.google.transit.realtime.GtfsRealtime.TripUpdate;

public class PredictionCancelledTripsSourceTest {

  private static final long T0 = 1_758_000_000_000L;

  private CancelledTripService cancelled;
  private PredictionCancelledTripsSource source;
  private QueuePredictionIntegrationServiceImpl queue;

  @Before
  public void before() {
    cancelled = mock(CancelledTripService.class);
    source = new PredictionCancelledTripsSource();
    source.setCancelledTripService(cancelled);
    ConfigurationService config = mock(ConfigurationService.class);
    when(config.getConfigurationValueAsString(anyString(), anyString())).thenAnswer(AdditionalAnswers.returnsSecondArg());
    queue = new QueuePredictionIntegrationServiceImpl();
    queue.setConfigurationService(config);
    queue.setCancelledTripsSource(source);
  }

  static FeedMessage cancellations(long ts, String... trips) {
    FeedMessage.Builder m = FeedMessage.newBuilder();
    m.setHeader(FeedHeader.newBuilder().setGtfsRealtimeVersion("2.0").setTimestamp(ts).setIncrementality(Incrementality.FULL_DATASET));
    for (String t : trips) {
      TripDescriptor.Builder td = TripDescriptor.newBuilder().setTripId(t).setRouteId("MTA NYCT_B11").setDirectionId(1)
          .setStartDate("20250916").setScheduleRelationship(TripDescriptor.ScheduleRelationship.CANCELED);
      m.addEntity(FeedEntity.newBuilder().setId(t + "_20250916_CANCELED").setTripUpdate(TripUpdate.newBuilder().setTrip(td).setTimestamp(ts / 1000L)));
    }
    return m.build();
  }

  static FeedMessage predictions(long ts) {
    FeedMessage.Builder m = FeedMessage.newBuilder();
    m.setHeader(FeedHeader.newBuilder().setGtfsRealtimeVersion("2.0").setTimestamp(ts).setIncrementality(Incrementality.DIFFERENTIAL));
    return m.build();
  }

  @SuppressWarnings({ "unchecked", "rawtypes" })
  private Map<AgencyAndId, CancelledTripBean> lastUpdate(int calls) {
    ArgumentCaptor<Map> captor = ArgumentCaptor.forClass(Map.class);
    verify(cancelled, times(calls)).updateCancelledTrips(captor.capture());
    return (Map<AgencyAndId, CancelledTripBean>) captor.getValue();
  }

  @Test
  public void aFullDatasetMessageReplacesTheCancelledSet() {
    source.update(cancellations(T0, "MTA NYCT_T1", "MTA NYCT_T2"), T0);
    Map<AgencyAndId, CancelledTripBean> map = lastUpdate(1);
    assertEquals(2, map.size());
    CancelledTripBean b = map.get(new AgencyAndId("MTA NYCT", "T1"));
    assertEquals("MTA NYCT_T1", b.getTrip());
    assertEquals("canceled", b.getStatus());
    assertEquals("MTA NYCT_B11", b.getRouteId());
    assertEquals("2025-09-16", b.getServiceDate());
    assertEquals(T0 / 1000L * 1000L, b.getTimestamp());

    source.update(cancellations(T0 + 15_000, "MTA NYCT_T2"), T0 + 15_000);
    map = lastUpdate(2);
    assertEquals(1, map.size());
    assertFalse("T1 was un-cancelled by its absence", map.containsKey(new AgencyAndId("MTA NYCT", "T1")));

    source.update(cancellations(T0 + 30_000), T0 + 30_000);
    assertTrue("an empty full-dataset message clears everything", lastUpdate(3).isEmpty());
  }

  @Test
  public void aSilentProducerExpiresTheSet() {
    source.update(cancellations(T0, "MTA NYCT_T1"), T0);
    source.expireIfStale(T0 + 60_000);
    verify(cancelled, times(1)).updateCancelledTrips(anyMap());
    source.expireIfStale(T0 + 91_000);
    assertTrue(lastUpdate(2).isEmpty());
    source.expireIfStale(T0 + 200_000);
    verify(cancelled, times(2)).updateCancelledTrips(anyMap()); // nothing left to drop
  }

  @Test
  public void standsAsideWhileCapiIsEnabled() {
    ConfigurationService config = mock(ConfigurationService.class);
    when(config.getConfigurationValueAsBoolean("tds.enableCapi", Boolean.FALSE)).thenReturn(Boolean.TRUE);
    when(config.getConfigurationValueAsInteger(anyString(), anyInt())).thenAnswer(AdditionalAnswers.returnsSecondArg());
    source.setConfigurationService(config);
    queue.processResult(cancellations(T0, "MTA NYCT_T1"));
    source.expireIfStale(T0 + 200_000);
    verify(cancelled, never()).updateCancelledTrips(anyMap());

    when(config.getConfigurationValueAsBoolean("tds.enableCapi", Boolean.FALSE)).thenReturn(Boolean.FALSE);
    queue.processResult(cancellations(T0, "MTA NYCT_T1"));
    assertEquals("CAPI off: the queue owns the set again", 1, lastUpdate(1).size());
  }

  @Test
  public void theQueueRoutesCancellationMessagesAndIgnoresPredictionsHeaders() {
    queue.processResult(cancellations(T0, "MTA NYCT_T1"));
    assertEquals(1, lastUpdate(1).size());
    assertFalse(PredictionCancelledTripsSource.isCancellationMessage(predictions(T0)));
    assertTrue(PredictionCancelledTripsSource.isCancellationMessage(cancellations(T0)));
  }

  @Test
  public void anUnwiredQueueDropsCancellationMessages() {
    QueuePredictionIntegrationServiceImpl unwired = new QueuePredictionIntegrationServiceImpl();
    ConfigurationService config = mock(ConfigurationService.class);
    when(config.getConfigurationValueAsString(anyString(), anyString())).thenAnswer(AdditionalAnswers.returnsSecondArg());
    unwired.setConfigurationService(config);
    // no transit data service either: reaching the prediction path would throw
    unwired.processResult(cancellations(T0, "MTA NYCT_T1"));
  }

  @Test
  public void aMessageWithoutTheMarkerNeverTouchesTheSet() {
    // a DIFFERENTIAL message takes the prediction path, which needs a TDS and caches to run through
    queue.setTransitDataService(mock(TransitDataService.class));
    queue.setPredictionsCache(CacheBuilder.newBuilder().<String, Collection<TimepointPredictionRecord>>build());
    queue.setPredictionsByStopIdCache(CacheBuilder.newBuilder().<String, Map<String, TimepointPredictionRecord>>build());
    FeedMessage.Builder m = predictions(T0).toBuilder();
    m.addEntity(FeedEntity.newBuilder().setId("x").setTripUpdate(TripUpdate.newBuilder()
        .setTrip(TripDescriptor.newBuilder().setTripId("MTA NYCT_T1").setScheduleRelationship(TripDescriptor.ScheduleRelationship.CANCELED))));
    queue.processResult(m.build());
    verify(cancelled, never()).updateCancelledTrips(anyMap());
  }

  @Test
  public void cancelAndUnCancelRoundTripThroughTheRealService() {
    CancelledTripServiceImpl real = new CancelledTripServiceImpl();
    PredictionCancelledTripsSource src = new PredictionCancelledTripsSource();
    src.setCancelledTripService(real);
    QueuePredictionIntegrationServiceImpl q = new QueuePredictionIntegrationServiceImpl();
    ConfigurationService config = mock(ConfigurationService.class);
    when(config.getConfigurationValueAsString(anyString(), anyString())).thenAnswer(AdditionalAnswers.returnsSecondArg());
    q.setConfigurationService(config);
    q.setCancelledTripsSource(src);

    q.processResult(cancellations(T0, "MTA NYCT_T1", "MTA NYCT_T2"));
    assertTrue(real.isTripCancelled("MTA NYCT_T1"));
    assertTrue(real.isTripCancelled(new AgencyAndId("MTA NYCT", "T2")));
    assertEquals(2, real.getAllCancelledTrips().getList().size());

    q.processResult(cancellations(T0 + 15_000, "MTA NYCT_T2"));
    assertFalse("un-cancelled by absence from the next message", real.isTripCancelled("MTA NYCT_T1"));
    assertTrue(real.isTripCancelled("MTA NYCT_T2"));

    q.processResult(predictions(T0 + 20_000));
    assertTrue("a predictions message leaves the set alone", real.isTripCancelled("MTA NYCT_T2"));

    q.processResult(cancellations(T0 + 30_000));
    assertTrue(real.getAllCancelledTrips().getList().isEmpty());
  }
}
