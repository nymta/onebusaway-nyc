package org.onebusaway.nyc.presentation.impl.realtime;

import uk.org.siri.siri.MonitoredCallStructure;
import uk.org.siri.siri.MonitoredVehicleJourneyStructure;
import uk.org.siri.siri.OnwardCallStructure;
import uk.org.siri.siri.ProgressStatusEnumeration;

/**
 * A skipped stop is one the predictions feed marked SKIPPED: the bus will not serve it, and its
 * prediction record carries no times, which the expected-time logic would otherwise render as
 * "now". The call builders give such a call no expected times and mark it CANCELLED; the realtime
 * services then drop skipped onward calls and, in stop monitoring, a visit whose own stop is
 * skipped, unless the request asked for cancelled service (showCancelled), which keeps everything
 * marked. Without showCancelled nothing else marks a call CANCELLED, so the mark alone identifies a
 * skipped call. SiriSupportV2 carries the same logic for the siri_2 types.
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

    /** The monitored call carries the skipped mark (meaningful without showCancelled, see above). */
    public static boolean hasSkippedMonitoredCall(MonitoredVehicleJourneyStructure journey) {
        return journey != null
                && journey.getMonitoredCall() != null
                && journey.getMonitoredCall().getArrivalStatus() == ProgressStatusEnumeration.CANCELLED;
    }

    /** Drops the onward calls carrying the skipped mark (meaningful without showCancelled, see above). */
    public static void removeSkippedOnwardCalls(MonitoredVehicleJourneyStructure journey) {
        if (journey == null || journey.getOnwardCalls() == null) {
            return;
        }
        journey.getOnwardCalls().getOnwardCall()
                .removeIf(call -> call.getArrivalStatus() == ProgressStatusEnumeration.CANCELLED);
    }
}
