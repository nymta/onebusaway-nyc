package org.onebusaway.nyc.transit_data_federation.impl.predictions;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;

import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.util.configuration.ConfigurationService;
import org.onebusaway.transit_data.model.trips.CancelledTripBean;
import org.onebusaway.transit_data_federation.services.CancelledTripService;
import org.onebusaway.util.AgencyAndIdLibrary;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;

import com.google.transit.realtime.GtfsRealtime.FeedEntity;
import com.google.transit.realtime.GtfsRealtime.FeedHeader.Incrementality;
import com.google.transit.realtime.GtfsRealtime.FeedMessage;
import com.google.transit.realtime.GtfsRealtime.TripDescriptor;
import com.google.transit.realtime.GtfsRealtime.TripUpdate;

/**
 * Feeds the cancelled-trip service from the predictions queue instead of the polled CAPI list.
 *
 * On the same queue as its predictions, the predictions engine publishes FULL_DATASET messages
 * whose entities are bare CANCELED trip updates (trip, route, direction, start date; no vehicle,
 * no stop times). Each one is the complete set of cancelled trips at that moment, so it replaces
 * the service's map outright, an empty message included: a trip that is no longer cancelled is
 * simply absent. If no such message arrives within the time-to-live the set is dropped, so a
 * silent producer cannot leave trips cancelled.
 *
 * The inference engine's own cancelled-trip cache is deliberately not fed from here: it keeps
 * matching buses onto a cancelled trip, which is how the producer sees the trip come back.
 * While {@code tds.enableCapi} is true the CAPI poller owns the set and this source does nothing.
 */
public class PredictionCancelledTripsSource {

	private static final Logger _log = LoggerFactory.getLogger(PredictionCancelledTripsSource.class);

	static final String TTL_KEY = "tds.predictionCancelTtlSeconds";
	static final int DEFAULT_TTL_SECONDS = 90;
	static final String CAPI_ENABLED_KEY = "tds.enableCapi";

	private CancelledTripService _cancelledTripService;
	private ConfigurationService _configurationService;
	private ScheduledExecutorService _executor;

	private long _lastUpdateMs = 0;
	private int _size = 0;

	@Autowired
	public void setCancelledTripService(CancelledTripService cancelledTripService) {
		_cancelledTripService = cancelledTripService;
	}

	@Autowired(required = false)
	public void setConfigurationService(ConfigurationService configurationService) {
		_configurationService = configurationService;
	}

	@PostConstruct
	public void start() {
		_executor = Executors.newSingleThreadScheduledExecutor();
		_executor.scheduleAtFixedRate(this::expireTick, 15, 15, TimeUnit.SECONDS);
		_log.info("cancelled trips are read from the predictions queue (ttl " + ttlMs() / 1000 + " s)");
	}

	/** One expiry check; a throw would silently cancel every later run, so it is logged instead. */
	void expireTick() {
		try {
			expireIfStale(System.currentTimeMillis());
		} catch (Throwable t) {
			_log.error("cancelled-trip expiry check failed", t);
		}
	}

	@PreDestroy
	public void stop() {
		if (_executor != null) {
			_executor.shutdownNow();
		}
	}

	/**
	 * True for a message that carries the cancelled set: the engine marks those, and only those,
	 * FULL_DATASET. The header field must be present, since an unset field also reads as FULL_DATASET
	 * (the protobuf default).
	 */
	public static boolean isCancellationMessage(FeedMessage message) {
		return message.hasHeader() && message.getHeader().hasIncrementality()
				&& message.getHeader().getIncrementality() == Incrementality.FULL_DATASET;
	}

	/** Replaces the cancelled set with the message's CANCELED entities. */
	public synchronized void update(FeedMessage message, long nowMs) {
		if (capiEnabled()) {
			return; // CAPI owns the set; two writers would overwrite each other's map
		}
		// the service keeps this map as its live set and shares it across threads, as CAPI's is
		Map<AgencyAndId, CancelledTripBean> map = new ConcurrentHashMap<AgencyAndId, CancelledTripBean>();
		for (FeedEntity entity : message.getEntityList()) {
			if (!entity.hasTripUpdate()) {
				continue;
			}
			TripUpdate tu = entity.getTripUpdate();
			TripDescriptor trip = tu.getTrip();
			if (trip.getScheduleRelationship() != TripDescriptor.ScheduleRelationship.CANCELED || trip.getTripId().isEmpty()) {
				continue;
			}
			CancelledTripBean bean = new CancelledTripBean();
			bean.setTrip(trip.getTripId());
			bean.setStatus("canceled");
			if (trip.hasRouteId()) {
				bean.setRouteId(trip.getRouteId());
				bean.setRoute(AgencyAndIdLibrary.convertFromString(trip.getRouteId()).getId()); // bare id, as the CAPI feed fills it
			}
			if (trip.hasStartDate()) {
				bean.setServiceDate(isoDate(trip.getStartDate()));
			}
			bean.setTimestamp(tu.hasTimestamp() ? tu.getTimestamp() * 1000L : nowMs);
			map.put(AgencyAndIdLibrary.convertFromString(trip.getTripId()), bean);
		}
		_cancelledTripService.updateCancelledTrips(map);
		_lastUpdateMs = nowMs;
		if (map.size() != _size) {
			_log.info("cancelled trips: " + map.size() + " (was " + _size + ")");
		}
		_size = map.size();
	}

	/** Drops the set when the producer has been silent longer than the time-to-live. */
	public synchronized void expireIfStale(long nowMs) {
		if (_size > 0 && !capiEnabled() && nowMs - _lastUpdateMs > ttlMs()) {
			_log.warn("no cancellation message for " + (nowMs - _lastUpdateMs) / 1000 + " s: dropping " + _size + " cancelled trips");
			_cancelledTripService.updateCancelledTrips(new ConcurrentHashMap<AgencyAndId, CancelledTripBean>());
			_size = 0;
		}
	}

	/** True while the CAPI poller (CancelledTripHttpListenerTask) owns the set. */
	boolean capiEnabled() {
		return _configurationService != null
				&& Boolean.TRUE.equals(_configurationService.getConfigurationValueAsBoolean(CAPI_ENABLED_KEY, Boolean.FALSE));
	}

	long ttlMs() {
		int ttl = DEFAULT_TTL_SECONDS;
		if (_configurationService != null) {
			Integer v = _configurationService.getConfigurationValueAsInteger(TTL_KEY, DEFAULT_TTL_SECONDS);
			if (v != null && v > 0) {
				ttl = v;
			}
		}
		return ttl * 1000L;
	}

	/** GTFS-RT start_date (yyyyMMdd) to the bean's yyyy-MM-dd; an unparseable date is passed through. */
	static String isoDate(String startDate) {
		try {
			return LocalDate.parse(startDate, DateTimeFormatter.BASIC_ISO_DATE).toString();
		} catch (DateTimeParseException e) {
			return startDate;
		}
	}
}
