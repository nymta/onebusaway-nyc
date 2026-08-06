/**
 * Copyright (C) 2017 Cambridge Systematics, Inc.
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
package org.onebusaway.nyc.gtfsrt.impl;

import com.google.transit.realtime.GtfsRealtime.*;
import org.onebusaway.nyc.gtfsrt.service.FeedMessageService;
import org.onebusaway.nyc.presentation.service.realtime.PresentationService;
import org.onebusaway.transit_data.model.AgencyWithCoverageBean;
import org.onebusaway.transit_data.model.ListBean;
import org.onebusaway.transit_data.model.VehicleStatusBean;
import org.onebusaway.transit_data.model.trips.TripDetailsBean;
import org.onebusaway.transit_data.model.trips.TripDetailsInclusionBean;
import org.onebusaway.transit_data.model.trips.TripForVehicleQueryBean;
import org.onebusaway.transit_data.services.TransitDataService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Date;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Includes shared logic for FeedMessageServices. Maintains a background thread
 * that periodically refreshes a cached FeedMessage. Callers receive the last
 * successfully built message immediately without blocking on data retrieval.
 */
public abstract class AbstractFeedMessageService implements FeedMessageService {

    private static final Logger _log = LoggerFactory.getLogger(AbstractFeedMessageService.class);

    public static final int DEFAULT_REFRESH_INTERVAL_SECONDS = 10;

    /**
     * Publication age cut-off in seconds: a vehicle whose last update is older is left out of the feed.
     * Default 0 = disabled, following the default-off convention of the ingestion deadband and
     * stale-fix shedding.
     */
    public static final String MAX_VEHICLE_AGE_PROPERTY = "oba.feed.maxVehicleAgeSec";

    private int _refreshIntervalSeconds = DEFAULT_REFRESH_INTERVAL_SECONDS;

    private long _maxVehicleAgeMillis = 0L;

    private volatile FeedMessage _cachedMessage = null;

    private ScheduledExecutorService _scheduler;

    public void setRefreshIntervalSeconds(int seconds) {
        _refreshIntervalSeconds = seconds;
    }

    /** Seconds; 0 or negative disables the cut-off. */
    public void setMaxVehicleAgeSeconds(int seconds) {
        _maxVehicleAgeMillis = seconds > 0 ? seconds * 1000L : 0L;
    }

    @PostConstruct
    public void init() {
        if (_maxVehicleAgeMillis == 0L) {
            try {
                setMaxVehicleAgeSeconds(Integer.parseInt(System.getProperty(MAX_VEHICLE_AGE_PROPERTY, "0").trim()));
            } catch (NumberFormatException e) {
                _log.warn("{} is not an integer; publication age cut-off stays disabled", MAX_VEHICLE_AGE_PROPERTY);
            }
        }
        if (_maxVehicleAgeMillis > 0L) {
            _log.info("{}: omitting vehicles whose last update is older than {} s ({})",
                    getClass().getSimpleName(), _maxVehicleAgeMillis / 1000, MAX_VEHICLE_AGE_PROPERTY);
        }
        _scheduler = Executors.newSingleThreadScheduledExecutor();
        _scheduler.scheduleWithFixedDelay(this::refresh, 0, _refreshIntervalSeconds, TimeUnit.SECONDS);
    }

    @PreDestroy
    public void destroy() {
        if (_scheduler != null) {
            _scheduler.shutdownNow();
        }
    }

    /**
     * Returns the last successfully cached FeedMessage, or an empty feed if
     * no successful refresh has completed yet.
     */
    @Override
    public FeedMessage getFeedMessage() {
        FeedMessage cached = _cachedMessage;
        if (cached == null) {
            return buildEmptyFeedMessage();
        }
        return cached;
    }

    /**
     * If a specific time is provided (debug/replay use), builds the feed
     * in real-time at that timestamp. Otherwise returns the cached message.
     */
    @Override
    public FeedMessage getFeedMessage(Long requestTime) {
        if (requestTime != null && requestTime > 0) {
            return buildFeedMessage(requestTime);
        }
        return getFeedMessage();
    }

    /**
     * Called by the background thread on each refresh cycle. Builds the feed
     * and updates the cache only if the result is non-null.
     */
    private void refresh() {
        try {
            List<FeedEntity.Builder> entities = getEntities(System.currentTimeMillis());
            if (entities == null) {
                _log.warn("{}: getEntities returned null, skipping cache update", getClass().getSimpleName());
                return;
            }
            FeedMessage message = buildFeedMessage(entities, System.currentTimeMillis());
            _cachedMessage = message;
        } catch (Exception e) {
            _log.error("{}: error during feed refresh, retaining previous cache", getClass().getSimpleName(), e);
        }
    }

    private FeedMessage buildFeedMessage(long time) {
        List<FeedEntity.Builder> entities = getEntities(time);
        if (entities == null) {
            return buildEmptyFeedMessage();
        }
        return buildFeedMessage(entities, time);
    }

    private FeedMessage buildFeedMessage(List<FeedEntity.Builder> entities, long time) {
        FeedMessage.Builder builder = FeedMessage.newBuilder();
        for (FeedEntity.Builder entity : entities) {
            if (entity != null) {
                try {
                    builder.addEntity(entity);
                } catch (Exception ex) {
                    _log.error("Unable to process entity {}", entity.getId(), ex);
                }
            }
        }
        FeedHeader.Builder header = FeedHeader.newBuilder();
        header.setGtfsRealtimeVersion("1.0");
        header.setTimestamp(time / 1000);
        header.setIncrementality(FeedHeader.Incrementality.FULL_DATASET);
        builder.setHeader(header);
        return builder.build();
    }

    private FeedMessage buildEmptyFeedMessage() {
        FeedMessage.Builder builder = FeedMessage.newBuilder();
        FeedHeader.Builder header = FeedHeader.newBuilder();
        header.setGtfsRealtimeVersion("1.0");
        header.setTimestamp(System.currentTimeMillis() / 1000);
        header.setIncrementality(FeedHeader.Incrementality.FULL_DATASET);
        builder.setHeader(header);
        return builder.build();
    }

    public Collection<VehicleStatusBean> getAllVehicles(TransitDataService tds, PresentationService ps, long time) {
        List<VehicleStatusBean> vehicles = new ArrayList<VehicleStatusBean>();
        int nStale = 0;
        for (AgencyWithCoverageBean bean : tds.getAgenciesWithCoverage()) {
            String agency = bean.getAgency().getId();
            ListBean<VehicleStatusBean> lb = tds.getAllVehiclesForAgency(agency, time);
            for (VehicleStatusBean vsb : lb.getList()) {
                if (isStale(vsb, time)) {
                    nStale++;
                } else if (includeVehicle(tds, ps, vsb, time)) {
                    vehicles.add(vsb);
                }
            }
        }
        if (nStale > 0) {
            _log.info("{}: omitted {} vehicle(s) with an update older than {} s",
                    getClass().getSimpleName(), nStale, _maxVehicleAgeMillis / 1000);
        }
        return vehicles;
    }

    /**
     * True when the vehicle's last update predates the cut-off. Judged on getLastUpdateTime(),
     * the same value the feeds publish as their entity timestamp, so a consumer never sees an entity
     * older than the cut-off it was filtered on.
     */
    public boolean isStale(VehicleStatusBean vehicleStatus, long time) {
        if (_maxVehicleAgeMillis <= 0L || vehicleStatus == null) {
            return false;
        }
        long lastUpdate = vehicleStatus.getLastUpdateTime();
        if (lastUpdate <= 0L) {
            return false;   // no timestamp to judge by; leave existing behaviour untouched
        }
        return (time - lastUpdate) > _maxVehicleAgeMillis;
    }

    public boolean includeVehicle(TransitDataService tds, PresentationService presentationService,
                                  VehicleStatusBean vehicleStatus, long time) {
        if (isStale(vehicleStatus, time)) {
            return Boolean.FALSE;
        }
        TripDetailsBean tripDetails = getTripForVehicle(tds, vehicleStatus, time);
        presentationService.setTime(time);
        if (tripDetails == null || !presentationService.include(tripDetails.getStatus()) || vehicleStatus.getTrip() == null) {
            return Boolean.FALSE;
        }
        return Boolean.TRUE;
    }

    private TripDetailsBean getTripForVehicle(TransitDataService tds, VehicleStatusBean vehicleStatus, long time) {
        TripForVehicleQueryBean query = new TripForVehicleQueryBean();
        query.setTime(new Date(time));
        query.setVehicleId(vehicleStatus.getVehicleId());

        TripDetailsInclusionBean inclusion = new TripDetailsInclusionBean();
        inclusion.setIncludeTripStatus(true);
        inclusion.setIncludeTripBean(true);
        query.setInclusion(inclusion);

        return tds.getTripDetailsForVehicleAndTime(query);
    }

    public abstract List<FeedEntity.Builder> getEntities(long time);
}
