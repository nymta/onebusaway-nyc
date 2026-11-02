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

import org.junit.Before;
import org.junit.Test;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.gtfsrt.service.ScheduledTripCancellationService.TripToCancel;
import org.onebusaway.nyc.util.configuration.ConfigurationService;
import org.onebusaway.transit_data_federation.services.blocks.BlockCalendarService;
import org.onebusaway.transit_data_federation.services.blocks.BlockInstance;
import org.onebusaway.transit_data_federation.services.transit_graph.BlockConfigurationEntry;
import org.onebusaway.transit_data_federation.services.transit_graph.BlockTripEntry;
import org.onebusaway.transit_data_federation.services.transit_graph.StopTimeEntry;
import org.onebusaway.transit_data_federation.services.transit_graph.TripEntry;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.mockito.Matchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

public class ScheduledTripCancellationServiceImplTest {

    private static final long SERVICE_DATE = 1790222400000L; // 2026-09-24 00:00 ET
    private static final long MINUTE = 60000L;

    private ScheduledTripCancellationServiceImpl service;
    private BlockCalendarService blockCalendar;
    private ConfigurationService config;

    @Before
    public void setup() {
        blockCalendar = mock(BlockCalendarService.class);
        config = mock(ConfigurationService.class);
        when(config.getConfigurationValueAsBoolean(ScheduledTripCancellationServiceImpl.ENABLED_KEY, Boolean.FALSE))
                .thenReturn(Boolean.TRUE);
        when(config.getConfigurationValueAsInteger(ScheduledTripCancellationServiceImpl.FIRST_TRIP_OFFSET_KEY, 5)).thenReturn(5);
        when(config.getConfigurationValueAsInteger(ScheduledTripCancellationServiceImpl.LATER_TRIP_OFFSET_KEY, -10)).thenReturn(-10);
        when(config.getConfigurationValueAsInteger(ScheduledTripCancellationServiceImpl.STARTUP_GRACE_KEY, 5)).thenReturn(0);
        when(config.getConfigurationValueAsInteger(ScheduledTripCancellationServiceImpl.MATCH_TTL_KEY, 10)).thenReturn(10);
        service =new ScheduledTripCancellationServiceImpl();
        service.setBlockCalendarService(blockCalendar);
        service.setConfigurationService(config);
    }

    // block of two trips: first 08:00-08:40, second 09:00-09:40 (seconds after service-date midnight)
    private void oneBlock() {
        BlockInstance block = block(trip("MTA NYCT_first", 8 * 3600, 8 * 3600 + 2400),
                trip("MTA NYCT_second", 9 * 3600, 9 * 3600 + 2400));
        when(blockCalendar.getActiveBlocksInTimeRange(anyLong(), anyLong())).thenReturn(Collections.singletonList(block));
    }

    private long at(int hour, int minute, int second) {
        return SERVICE_DATE + (hour * 3600 + minute * 60 + second) * 1000L;
    }

    @Test
    public void testDisabledByDefault() {
        when(config.getConfigurationValueAsBoolean(ScheduledTripCancellationServiceImpl.ENABLED_KEY, Boolean.FALSE))
                .thenReturn(Boolean.FALSE);
        assertTrue(ids(at(8, 30, 0)).isEmpty());
        verify(blockCalendar, never()).getActiveBlocksInTimeRange(anyLong(), anyLong());
    }

    @Test
    public void testFirstTripCancelledFiveMinutesAfterDeparture() {
        oneBlock();
        assertEquals(Collections.emptyList(), ids(at(8, 4, 59)));
        assertEquals(Collections.singletonList("MTA NYCT_first"), ids(at(8, 5, 0)));
    }

    @Test
    public void testLaterTripCancelledTenMinutesBeforeDeparture() {
        oneBlock();
        assertFalse(ids(at(8, 49, 59)).contains("MTA NYCT_second"));
        assertTrue(ids(at(8, 50, 0)).contains("MTA NYCT_second"));
    }

    @Test
    public void testCancellationEndsAtLastScheduledStopTime() {
        oneBlock();
        assertTrue(ids(at(8, 40, 0)).contains("MTA NYCT_first"));
        assertFalse(ids(at(8, 40, 1)).contains("MTA NYCT_first"));
    }

    @Test
    public void testCancellationCarriesServiceDate() {
        oneBlock();
        List<TripToCancel> trips = service.getTripsToCancel(at(8, 5, 0),
                Collections.<String, Set<String>>emptyMap(), Collections.<String>emptySet());
        assertEquals(1, trips.size());
        assertEquals(SERVICE_DATE, trips.get(0).getServiceDate());
    }

    @Test
    public void testMatchedTripNotCancelled() {
        oneBlock();
        assertTrue(ids(at(8, 50, 0), "v1", "MTA NYCT_second").isEmpty());
    }

    @Test
    public void testExcludedTripNotCancelled() {
        oneBlock();
        List<TripToCancel> trips = service.getTripsToCancel(at(8, 5, 0),
                Collections.<String, Set<String>>emptyMap(), Collections.singleton("MTA NYCT_first"));
        assertTrue(trips.isEmpty());
    }

    @Test
    public void testQueriesBlocksActiveUpToTheLaterTripLead() {
        when(blockCalendar.getActiveBlocksInTimeRange(anyLong(), anyLong())).thenReturn(Collections.<BlockInstance>emptyList());
        long now = at(8, 0, 0);
        ids(now);
        verify(blockCalendar).getActiveBlocksInTimeRange(now, now + 10 * MINUTE);
    }

    @Test
    public void testMatchWithinTtlBeforeThresholdPreventsCancellation() {
        oneBlock();
        ids(at(7, 55, 1), "v1", "MTA NYCT_first");
        assertFalse(ids(at(8, 5, 0)).contains("MTA NYCT_first"));
        assertFalse(ids(at(8, 30, 0)).contains("MTA NYCT_first"));
    }

    @Test
    public void testMatchOlderThanTtlExpires() {
        oneBlock();
        ids(at(7, 54, 59), "v1", "MTA NYCT_first");
        assertTrue(ids(at(8, 5, 0)).contains("MTA NYCT_first"));
    }

    @Test
    public void testVehicleLatestMatchReplacesEarlierTrips() {
        oneBlock();
        ids(at(8, 0, 0), "v1", "MTA NYCT_first");
        assertTrue(ids(at(8, 5, 0), "v1", "MTA NYCT_other").contains("MTA NYCT_first"));
    }

    @Test
    public void testAbsentVehicleKeepsItsLatestMatchUntilTtl() {
        oneBlock();
        ids(at(7, 56, 0), "v1", "MTA NYCT_first");
        ids(at(8, 0, 0), "v2", "MTA NYCT_other");
        assertFalse(ids(at(8, 5, 0)).contains("MTA NYCT_first"));
    }

    @Test
    public void testOffsetsComeFromConfiguration() {
        when(config.getConfigurationValueAsInteger(ScheduledTripCancellationServiceImpl.FIRST_TRIP_OFFSET_KEY, 5)).thenReturn(0);
        when(config.getConfigurationValueAsInteger(ScheduledTripCancellationServiceImpl.LATER_TRIP_OFFSET_KEY, -10)).thenReturn(-20);
        oneBlock();
        assertEquals(Arrays.asList("MTA NYCT_first", "MTA NYCT_second"), ids(at(8, 40, 0)));
    }

    @Test
    public void testCancellationLiftsWhenMatchedAndStaysLifted() {
        oneBlock();
        assertTrue(ids(at(8, 5, 0)).contains("MTA NYCT_first"));
        assertFalse(ids(at(8, 6, 0), "v1", "MTA NYCT_first").contains("MTA NYCT_first"));
        assertFalse(ids(at(8, 30, 0)).contains("MTA NYCT_first"));
    }

    @Test
    public void testNoDecisionsDuringStartupGrace() {
        when(config.getConfigurationValueAsInteger(ScheduledTripCancellationServiceImpl.STARTUP_GRACE_KEY, 5)).thenReturn(5);
        oneBlock();
        assertTrue(ids(at(8, 6, 0)).isEmpty());
        assertTrue(ids(at(8, 10, 59)).isEmpty());
        assertEquals(Collections.singletonList("MTA NYCT_first"), ids(at(8, 11, 0)));
    }

    @Test
    public void testSwitchingBackOnRestartsGraceAndForgetsDecisions() {
        when(config.getConfigurationValueAsInteger(ScheduledTripCancellationServiceImpl.STARTUP_GRACE_KEY, 5)).thenReturn(5);
        oneBlock();
        ids(at(8, 0, 0));
        assertTrue(ids(at(8, 6, 0)).contains("MTA NYCT_first"));
        when(config.getConfigurationValueAsBoolean(ScheduledTripCancellationServiceImpl.ENABLED_KEY, Boolean.FALSE))
                .thenReturn(Boolean.FALSE);
        ids(at(8, 7, 0));
        when(config.getConfigurationValueAsBoolean(ScheduledTripCancellationServiceImpl.ENABLED_KEY, Boolean.FALSE))
                .thenReturn(Boolean.TRUE);
        assertTrue(ids(at(8, 8, 0)).isEmpty());
        assertTrue(ids(at(8, 13, 0)).contains("MTA NYCT_first"));
    }

    private Map<String, Set<String>> vehicle(String vehicleId, String... tripIds) {
        return Collections.<String, Set<String>>singletonMap(vehicleId, new HashSet<>(Arrays.asList(tripIds)));
    }

    /** Trip ids cancelled at {@code time}; with arguments, the first is a vehicle id and the rest its matched trips. */
    private List<String> ids(long time, String... vehicleAndTrips) {
        Map<String, Set<String>> matched = vehicleAndTrips.length == 0
                ? Collections.<String, Set<String>>emptyMap()
                : vehicle(vehicleAndTrips[0], Arrays.copyOfRange(vehicleAndTrips, 1, vehicleAndTrips.length));
        List<String> out = new ArrayList<>();
        for (TripToCancel trip : service.getTripsToCancel(time, matched, Collections.<String>emptySet())) {
            out.add(trip.getTripId());
        }
        return out;
    }

    private BlockInstance block(BlockTripEntry... trips) {
        BlockConfigurationEntry config = mock(BlockConfigurationEntry.class);
        when(config.getTrips()).thenReturn(Arrays.asList(trips));
        return new BlockInstance(config, SERVICE_DATE);
    }

    private BlockTripEntry trip(String id, int departureSeconds, int lastArrivalSeconds) {
        StopTimeEntry origin = mock(StopTimeEntry.class);
        when(origin.getDepartureTime()).thenReturn(departureSeconds);
        StopTimeEntry last = mock(StopTimeEntry.class);
        when(last.getArrivalTime()).thenReturn(lastArrivalSeconds);
        TripEntry trip = mock(TripEntry.class);
        when(trip.getId()).thenReturn(AgencyAndId.convertFromString(id));
        when(trip.getStopTimes()).thenReturn(Arrays.asList(origin, last));
        BlockTripEntry blockTrip = mock(BlockTripEntry.class);
        when(blockTrip.getTrip()).thenReturn(trip);
        return blockTrip;
    }
}
