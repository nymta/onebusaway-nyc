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
package org.onebusaway.nyc.gtfsrt.service;

import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Finds scheduled trips that no vehicle is matched to and that should be published as CANCELED.
 */
public interface ScheduledTripCancellationService {

    /**
     * @param time current time, epoch ms
     * @param matchedTripIdsByVehicle trip ids (AgencyAndId strings) each vehicle is matched to at this feed build
     * @param excludedTripIds trip ids never to cancel here (e.g. already cancelled externally)
     * @return unmatched trips whose cancellation time has passed and whose last scheduled stop time has not
     */
    List<TripToCancel> getTripsToCancel(long time, Map<String, Set<String>> matchedTripIdsByVehicle,
                                        Set<String> excludedTripIds);

    final class TripToCancel {
        private final String _tripId;
        private final long _serviceDate;

        public TripToCancel(String tripId, long serviceDate) {
            _tripId = tripId;
            _serviceDate = serviceDate;
        }

        public String getTripId() {
            return _tripId;
        }

        public long getServiceDate() {
            return _serviceDate;
        }
    }
}
