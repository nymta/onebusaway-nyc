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

import uk.org.siri.siri.MonitoredCallStructure;
import uk.org.siri.siri.MonitoredVehicleJourneyStructure;
import uk.org.siri.siri.OnwardCallStructure;
import uk.org.siri.siri.ProgressStatusEnumeration;

/**
 * A skipped stop is one the predictions feed marked SKIPPED: the bus will not serve it, and its
 * prediction record carries no times. Such a record must never reach the expected-time logic,
 * which renders any time before now as "now".
 *
 * The call builders give a skipped call no expected times and mark its arrival and departure
 * CANCELLED. Vehicle monitoring then drops skipped onward calls and keeps the monitored call, so
 * the vehicle is still reported. Stop monitoring drops skipped onward calls and, when the stop
 * itself is skipped, the whole visit, as it hides a cancelled trip; a request that asked for
 * cancelled trips (showCancelled) keeps everything, marked. Without showCancelled nothing else
 * marks a call CANCELLED, so the status alone identifies a skipped call.
 *
 * SiriSupportV2 carries the same logic for the siri_2 types.
 */
public final class SkippedStopSupport {

    private SkippedStopSupport() {
    }

    public static boolean isSkipped(SiriSupportPredictionTimepointRecord prediction) {
        return prediction != null
                && prediction.getTimepointPredictionRecord() != null
                && prediction.getTimepointPredictionRecord().isSkipped();
    }

    public static void markSkipped(OnwardCallStructure call) {
        call.setArrivalStatus(ProgressStatusEnumeration.CANCELLED);
        call.setDepartureStatus(ProgressStatusEnumeration.CANCELLED);
    }

    public static void markSkipped(MonitoredCallStructure call) {
        call.setArrivalStatus(ProgressStatusEnumeration.CANCELLED);
        call.setDepartureStatus(ProgressStatusEnumeration.CANCELLED);
    }

    /** Reads the CANCELLED mark back; only meaningful without showCancelled (see class comment). */
    public static boolean hasSkippedMonitoredCall(MonitoredVehicleJourneyStructure journey) {
        return journey != null
                && journey.getMonitoredCall() != null
                && journey.getMonitoredCall().getArrivalStatus() == ProgressStatusEnumeration.CANCELLED;
    }

    /** Reads the CANCELLED mark back; only meaningful without showCancelled (see class comment). */
    public static void removeSkippedOnwardCalls(MonitoredVehicleJourneyStructure journey) {
        if (journey == null || journey.getOnwardCalls() == null) {
            return;
        }
        journey.getOnwardCalls().getOnwardCall()
                .removeIf(call -> call.getArrivalStatus() == ProgressStatusEnumeration.CANCELLED);
    }
}
