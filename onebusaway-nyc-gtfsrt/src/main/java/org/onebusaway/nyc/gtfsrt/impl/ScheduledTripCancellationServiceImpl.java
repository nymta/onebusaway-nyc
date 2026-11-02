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
package org.onebusaway.nyc.gtfsrt.impl;

import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.gtfsrt.service.ScheduledTripCancellationService;
import org.onebusaway.nyc.util.configuration.ConfigurationService;
import org.onebusaway.transit_data_federation.services.blocks.BlockCalendarService;
import org.onebusaway.transit_data_federation.services.blocks.BlockInstance;
import org.onebusaway.transit_data_federation.services.transit_graph.BlockTripEntry;
import org.onebusaway.transit_data_federation.services.transit_graph.StopTimeEntry;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Caches, per vehicle, the trips it was matched to at the latest feed build that included it, for a TTL. From a
 * trip's threshold (origin departure + offset: first-trip offset for the block's first trip, later-trip offset
 * otherwise; negative = before) until its last scheduled stop time, a trip with no cache entry is CANCELED. Once a
 * due trip is found in the cache it is released for good. Nothing is cancelled during a startup grace.
 */
@Component
public class ScheduledTripCancellationServiceImpl implements ScheduledTripCancellationService {

    static final String ENABLED_KEY = "gtfsrt.cancelUnmatchedTrips.enabled";
    static final String FIRST_TRIP_OFFSET_KEY = "gtfsrt.cancelUnmatchedTrips.firstTripOffsetMinutes";
    static final String LATER_TRIP_OFFSET_KEY = "gtfsrt.cancelUnmatchedTrips.laterTripOffsetMinutes";
    static final String STARTUP_GRACE_KEY = "gtfsrt.cancelUnmatchedTrips.startupGraceMinutes";
    static final String MATCH_TTL_KEY = "gtfsrt.cancelUnmatchedTrips.matchTtlMinutes";
    static final int DEFAULT_FIRST_TRIP_OFFSET_MINUTES = 5;
    static final int DEFAULT_LATER_TRIP_OFFSET_MINUTES = -10;
    static final int DEFAULT_STARTUP_GRACE_MINUTES = 5;
    static final int DEFAULT_MATCH_TTL_MINUTES = 10;

    private static final class Match {
        final Set<String> tripIds;
        final long matchedAt;

        Match(Set<String> tripIds, long matchedAt) {
            this.tripIds = tripIds;
            this.matchedAt = matchedAt;
        }
    }

    // vehicle id -> trips it was matched to at the latest feed build that included it
    private final Map<String, Match> _matchesByVehicle = new ConcurrentHashMap<String, Match>();
    // trip id + service date -> last scheduled stop time; released trips are never cancelled again
    private final Map<String, Long> _released = new ConcurrentHashMap<String, Long>();
    private volatile Long _startTime = null;

    private BlockCalendarService _blockCalendarService;
    private ConfigurationService _configurationService;

    @Autowired
    public void setBlockCalendarService(BlockCalendarService blockCalendarService) {
        _blockCalendarService = blockCalendarService;
    }

    @Autowired
    public void setConfigurationService(ConfigurationService configurationService) {
        _configurationService = configurationService;
    }

    @Override
    public List<TripToCancel> getTripsToCancel(long time, Map<String, Set<String>> matchedTripIdsByVehicle,
                                               Set<String> excludedTripIds) {
        if (!Boolean.TRUE.equals(_configurationService.getConfigurationValueAsBoolean(ENABLED_KEY, Boolean.FALSE))) {
            // switching the rule back on starts over: new grace period, empty cache, nothing released
            _startTime = null;
            _matchesByVehicle.clear();
            _released.clear();
            return Collections.emptyList();
        }
        if (_startTime == null) {
            _startTime = time;
        }
        Set<String> cachedTripIds = updateCache(time, matchedTripIdsByVehicle);

        int graceMinutes = _configurationService.getConfigurationValueAsInteger(STARTUP_GRACE_KEY,
                DEFAULT_STARTUP_GRACE_MINUTES);
        if (time < _startTime + graceMinutes * 60000L) {
            return Collections.emptyList();
        }
        int firstOffsetMinutes = _configurationService.getConfigurationValueAsInteger(FIRST_TRIP_OFFSET_KEY,
                DEFAULT_FIRST_TRIP_OFFSET_MINUTES);
        int laterOffsetMinutes = _configurationService.getConfigurationValueAsInteger(LATER_TRIP_OFFSET_KEY,
                DEFAULT_LATER_TRIP_OFFSET_MINUTES);

        // a due trip has not finished and departs within the lead, so its block is active in [time, time + lead]
        long leadMillis = Math.max(0, -Math.min(firstOffsetMinutes, laterOffsetMinutes)) * 60000L;

        List<TripToCancel> tripsToCancel = new ArrayList<TripToCancel>();
        for (BlockInstance block : _blockCalendarService.getActiveBlocksInTimeRange(time, time + leadMillis)) {
            List<BlockTripEntry> trips = block.getBlock().getTrips();
            for (int i = 0; i < trips.size(); i++) {
                BlockTripEntry blockTrip = trips.get(i);
                String tripId = AgencyAndId.convertToString(blockTrip.getTrip().getId());
                List<StopTimeEntry> stopTimes = blockTrip.getTrip().getStopTimes();
                long originDeparture = block.getServiceDate() + stopTimes.get(0).getDepartureTime() * 1000L;
                long lastArrival = block.getServiceDate() + stopTimes.get(stopTimes.size() - 1).getArrivalTime() * 1000L;
                int offsetMinutes = i == 0 ? firstOffsetMinutes : laterOffsetMinutes;
                if (!isDue(time, originDeparture, lastArrival, offsetMinutes) || excludedTripIds.contains(tripId)) {
                    continue;
                }
                String key = tripId + "@" + block.getServiceDate();
                if (_released.containsKey(key)) {
                    continue;
                }
                if (cachedTripIds.contains(tripId)) {
                    _released.put(key, lastArrival);
                    continue;
                }
                tripsToCancel.add(new TripToCancel(tripId, block.getServiceDate()));
            }
        }
        _released.values().removeIf(lastArrival -> lastArrival < time);
        return tripsToCancel;
    }

    private Set<String> updateCache(long time, Map<String, Set<String>> matchedTripIdsByVehicle) {
        for (Map.Entry<String, Set<String>> entry : matchedTripIdsByVehicle.entrySet()) {
            _matchesByVehicle.put(entry.getKey(), new Match(new HashSet<String>(entry.getValue()), time));
        }
        long ttlMillis = _configurationService.getConfigurationValueAsInteger(MATCH_TTL_KEY,
                DEFAULT_MATCH_TTL_MINUTES) * 60000L;
        _matchesByVehicle.values().removeIf(match -> time - match.matchedAt > ttlMillis);
        Set<String> cachedTripIds = new HashSet<String>();
        for (Match match : _matchesByVehicle.values()) {
            cachedTripIds.addAll(match.tripIds);
        }
        return cachedTripIds;
    }

    static boolean isDue(long time, long originDeparture, long lastArrival, int offsetMinutes) {
        return time >= originDeparture + offsetMinutes * 60000L && time <= lastArrival;
    }
}
