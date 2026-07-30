/**
 * Copyright (C) 2011 Metropolitan Transportation Authority
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

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Collections;
import java.util.List;

import org.junit.After;
import org.junit.Test;
import org.onebusaway.nyc.gtfsrt.impl.AbstractFeedMessageService;
import org.onebusaway.transit_data.model.VehicleStatusBean;

import com.google.transit.realtime.GtfsRealtime.FeedEntity;

/**
 * Covers the publication age cut-off shared by the TripUpdate and VehiclePosition feeds.
 *
 * Motivation: buses were being published for up to 300 s after their last radio report, where the
 * reference production feed stops at a measured 120 s. Roughly a quarter of the vehicles we published
 * and production did not were explained by that difference.
 */
public class FeedVehicleExpiryTest {

    /** Minimal concrete subclass; getEntities is never exercised here. */
    private static class TestFeedMessageService extends AbstractFeedMessageService {
        @Override
        public List<FeedEntity.Builder> getEntities(long time) {
            return Collections.emptyList();
        }
    }

    private static final long NOW = 1_700_000_000_000L;

    private final TestFeedMessageService _service = new TestFeedMessageService();

    @After
    public void clearProperty() {
        System.clearProperty(AbstractFeedMessageService.MAX_VEHICLE_AGE_PROPERTY);
    }

    private VehicleStatusBean vehicleLastUpdatedSecondsAgo(long secondsAgo) {
        VehicleStatusBean vehicle = new VehicleStatusBean();
        vehicle.setVehicleId("MTA NYCT_1234");
        vehicle.setLastUpdateTime(NOW - (secondsAgo * 1000L));
        return vehicle;
    }

    /**
     * The no-regression property: disabled by default, so deploying this change alters nothing until
     * the cut-off is explicitly configured.
     */
    @Test
    public void byDefaultNoVehicleIsEverConsideredStale() {
        assertFalse(_service.isStale(vehicleLastUpdatedSecondsAgo(1), NOW));
        assertFalse(_service.isStale(vehicleLastUpdatedSecondsAgo(300), NOW));
        assertFalse(_service.isStale(vehicleLastUpdatedSecondsAgo(86400), NOW));
    }

    @Test
    public void withA120SecondCutOffOnlyOlderVehiclesAreExcluded() {
        _service.setMaxVehicleAgeSeconds(120);
        assertFalse(_service.isStale(vehicleLastUpdatedSecondsAgo(0), NOW));
        assertFalse(_service.isStale(vehicleLastUpdatedSecondsAgo(119), NOW));
        assertFalse("the boundary itself is retained", _service.isStale(vehicleLastUpdatedSecondsAgo(120), NOW));
        assertTrue(_service.isStale(vehicleLastUpdatedSecondsAgo(121), NOW));
        assertTrue(_service.isStale(vehicleLastUpdatedSecondsAgo(300), NOW));
    }

    /** A vehicle with no usable timestamp keeps its prior treatment rather than being dropped. */
    @Test
    public void aVehicleWithoutATimestampIsNotExcluded() {
        _service.setMaxVehicleAgeSeconds(120);
        VehicleStatusBean noTimestamp = new VehicleStatusBean();
        noTimestamp.setVehicleId("MTA NYCT_5678");
        assertFalse(_service.isStale(noTimestamp, NOW));
        assertFalse(_service.isStale(null, NOW));
    }

    /** A clock skew putting the update slightly in the future must not read as stale. */
    @Test
    public void aFutureTimestampIsNotStale() {
        _service.setMaxVehicleAgeSeconds(120);
        assertFalse(_service.isStale(vehicleLastUpdatedSecondsAgo(-30), NOW));
    }

    @Test
    public void zeroOrNegativeDisablesTheCutOff() {
        _service.setMaxVehicleAgeSeconds(120);
        _service.setMaxVehicleAgeSeconds(0);
        assertFalse(_service.isStale(vehicleLastUpdatedSecondsAgo(3600), NOW));
        _service.setMaxVehicleAgeSeconds(-1);
        assertFalse(_service.isStale(vehicleLastUpdatedSecondsAgo(3600), NOW));
    }

    @Test
    public void theCutOffCanBeSuppliedAsASystemProperty() {
        System.setProperty(AbstractFeedMessageService.MAX_VEHICLE_AGE_PROPERTY, "120");
        TestFeedMessageService configured = new TestFeedMessageService();
        try {
            configured.init();
            assertTrue(configured.isStale(vehicleLastUpdatedSecondsAgo(150), NOW));
            assertFalse(configured.isStale(vehicleLastUpdatedSecondsAgo(30), NOW));
        } finally {
            configured.destroy();
        }
    }

    /** A malformed property leaves the cut-off disabled rather than failing startup. */
    @Test
    public void aMalformedSystemPropertyLeavesTheCutOffDisabled() {
        System.setProperty(AbstractFeedMessageService.MAX_VEHICLE_AGE_PROPERTY, "two minutes");
        TestFeedMessageService configured = new TestFeedMessageService();
        try {
            configured.init();
            assertFalse(configured.isStale(vehicleLastUpdatedSecondsAgo(3600), NOW));
        } finally {
            configured.destroy();
        }
    }
}
