package org.onebusaway.nyc.transit_data_federation.impl.predictions;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.mockito.Matchers.any;
import static org.mockito.Matchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.junit.Assume;
import org.junit.Test;
import org.mockito.AdditionalAnswers;
import org.mockito.invocation.InvocationOnMock;
import org.mockito.stubbing.Answer;
import org.onebusaway.nyc.util.configuration.ConfigurationService;
import org.onebusaway.realtime.api.TimepointPredictionRecord;
import org.onebusaway.transit_data.model.ListBean;
import org.onebusaway.transit_data.model.StopBean;
import org.onebusaway.transit_data.model.TripStopTimeBean;
import org.onebusaway.transit_data.model.TripStopTimesBean;
import org.onebusaway.transit_data.model.trips.TripDetailsBean;
import org.onebusaway.transit_data.model.trips.TripDetailsQueryBean;
import org.onebusaway.transit_data.model.trips.TripStatusBean;
import org.onebusaway.transit_data.services.TransitDataService;

import com.google.common.cache.CacheBuilder;
import com.google.transit.realtime.GtfsRealtime.FeedEntity;
import com.google.transit.realtime.GtfsRealtime.FeedMessage;
import com.google.transit.realtime.GtfsRealtime.TripUpdate;

/**
 * Replays a recorded predictions-queue stream through the real queue consumer and checks the empty-entity contract:
 * whenever the engine publishes a vehicle's trip with no stop times (a bus leaving service, or a trip it withholds
 * because the trip is cancelled), the consumer's cached times for that vehicle and trip are gone right after the
 * message, not left until the cache expires. The schedule lookup is stubbed from the stream itself (every stop a trip
 * was ever predicted at), so every predicted stop is kept as the real transit data service would keep it.
 *
 * Input is the length-delimited FeedMessage stream a replay writes. Skipped unless
 * -Drecorded.predictions.pb=/path/to/stream.pb is set.
 */
public class RecordedEmptyEntityTest {

  @Test
  public void everyEmptyEntityClearsTheCachedTimes() throws Exception {
    String path = System.getProperty("recorded.predictions.pb");
    Assume.assumeTrue(path != null && !path.isEmpty()); // set -Drecorded.predictions.pb to run

    final Map<String, Set<String>> stopsOfTrip = new HashMap<String, Set<String>>();
    List<FeedMessage> messages = new ArrayList<FeedMessage>();
    try (InputStream in = new BufferedInputStream(new FileInputStream(path), 1 << 20)) {
      FeedMessage m;
      while ((m = FeedMessage.parseDelimitedFrom(in)) != null) {
        if (PredictionCancelledTripsSource.isCancellationMessage(m)) {
          continue;
        }
        messages.add(m);
        for (FeedEntity e : m.getEntityList()) {
          TripUpdate tu = e.getTripUpdate();
          Set<String> stops = stopsOfTrip.get(tu.getTrip().getTripId());
          if (stops == null) {
            stopsOfTrip.put(tu.getTrip().getTripId(), stops = new LinkedHashSet<String>());
          }
          for (TripUpdate.StopTimeUpdate stu : tu.getStopTimeUpdateList()) {
            stops.add(stu.getStopId());
          }
        }
      }
    }

    TransitDataService tds = mock(TransitDataService.class);
    when(tds.getTripDetails(any(TripDetailsQueryBean.class))).thenAnswer(new Answer<ListBean<TripDetailsBean>>() {
      public ListBean<TripDetailsBean> answer(InvocationOnMock inv) {
        Set<String> stops = stopsOfTrip.get(((TripDetailsQueryBean) inv.getArguments()[0]).getTripId());
        if (stops == null) {
          return null;
        }
        TripDetailsBean bean = new TripDetailsBean();
        TripStatusBean status = new TripStatusBean();
        status.setServiceDate(0);
        bean.setStatus(status);
        bean.setSchedule(new TripStopTimesBean());
        for (String id : stops) {
          TripStopTimeBean st = new TripStopTimeBean();
          st.setStop(new StopBean());
          st.getStop().setId(id);
          bean.getSchedule().addStopTime(st);
        }
        ListBean<TripDetailsBean> list = new ListBean<TripDetailsBean>();
        List<TripDetailsBean> one = new ArrayList<TripDetailsBean>();
        one.add(bean);
        list.setList(one);
        return list;
      }
    });
    ConfigurationService config = mock(ConfigurationService.class);
    when(config.getConfigurationValueAsString(anyString(), anyString())).thenAnswer(AdditionalAnswers.returnsSecondArg());
    QueuePredictionIntegrationServiceImpl service = new QueuePredictionIntegrationServiceImpl();
    service.setConfigurationService(config);
    service.setTransitDataService(tds);
    service.setPredictionsByStopIdCache(CacheBuilder.newBuilder().<String, Map<String, TimepointPredictionRecord>>build());
    service.setPredictionsCache(CacheBuilder.newBuilder().<String, Collection<TimepointPredictionRecord>>build());

    int empty = 0, replacedStale = 0, predicted = 0;
    for (FeedMessage m : messages) {
      List<String[]> emptyKeys = new ArrayList<String[]>();
      for (FeedEntity e : m.getEntityList()) {
        TripUpdate tu = e.getTripUpdate();
        if (tu.getStopTimeUpdateCount() == 0) {
          String[] key = { tu.getVehicle().getId(), tu.getTrip().getTripId() };
          Collection<TimepointPredictionRecord> before = service.getPredictionRecordsForVehicleAndTrip(key[0], key[1]);
          if (before != null && !before.isEmpty()) {
            replacedStale++;
          }
          emptyKeys.add(key);
        }
      }
      service.processResult(m);
      for (String[] key : emptyKeys) {
        empty++;
        Collection<TimepointPredictionRecord> after = service.getPredictionRecordsForVehicleAndTrip(key[0], key[1]);
        assertTrue("cached times left for " + key[0] + " on " + key[1], after == null || after.isEmpty());
      }
      FeedEntity first = m.getEntityCount() > 0 ? m.getEntity(0) : null;
      if (first != null && first.getTripUpdate().getStopTimeUpdateCount() > 0) {
        TripUpdate tu = first.getTripUpdate();
        assertEquals(tu.getStopTimeUpdateCount(),
            service.getPredictionRecordsForVehicleAndTrip(tu.getVehicle().getId(), tu.getTrip().getTripId()).size());
        predicted++;
      }
    }
    System.out.println("recorded empty entities: " + messages.size() + " messages, " + predicted + " predicted trips checked, "
        + empty + " empty entities, " + replacedStale + " of them replaced cached times");
    assertTrue("the stream carries no empty entity", empty > 0);
  }
}
