# Dispatcher actions: code changes in this repo

The predictions engine (the `onebusaway-nyc-predictions` repo) turns Bus Trek and Yard Trek dispatcher
actions into `SKIPPED` stop-time updates and `FULL_DATASET` messages of bare `CANCELED` trip updates on
its predictions queue. This repo's changes make the TDS, GTFS-RT and SIRI carry those two signals to
riders, and keep an archive of the two live dispatcher snapshots the engine reads. Design, measurements,
blockers and the end-to-end test plan live in the predictions repo:
`replay/docs/DISPATCHER-ACTIONS-STATUS.md` (overview), `-PLAYBOOK.md` (design, fix numbers F*),
`-EVIDENCE.md` (numbers). Branch `timothy/dispatcher-actions`: commit `676d8594` plus the uncommitted cleanup
and archiver files (2026-11-04). The bundle-builder CSVs at the transit-data-federation module root are not part of this work; `.gitignore` now lists them.

## Files

| File | Change | Fix |
|---|---|---|
| `onebusaway-nyc-transit-data-federation/…/impl/predictions/PredictionCancelledTripsSource.java` (new) | Replaces the cancelled-trip service's map with each `FULL_DATASET` message's `CANCELED` entities (an empty message empties it); drops the set after `tds.predictionCancelTtlSeconds` of silence; does nothing while `tds.enableCapi` is true. Hands the service a `ConcurrentHashMap`, as CAPI does; the expiry task logs and survives an exception | F13 |
| `…/impl/predictions/QueuePredictionIntegrationServiceImpl.java` | Hands every `FULL_DATASET` message to that source and returns (it carries no predictions); a `SKIPPED` stop-time update becomes a prediction record with `ScheduleRelationship.SKIPPED` and no times (the unset protobuf times would read as 1970) | F13, F9 |
| `onebusaway-nyc-transit-data-federation-webapp/…/data-sources.xml`, `onebusaway-nyc-gtfsrt-webapp/…/data-sources.xml` | `cancelledTripsFromPredictions` bean added beside the existing `CancelledTripHttpListenerTask`; the listener stays and owns the set while `tds.enableCapi` is true | F13 |
| `onebusaway-nyc-gtfsrt/…/service/TripUpdateFeedBuilder.java`, `…/impl/TripUpdateFeedBuilderImpl.java`, `…/impl/TripUpdateServiceImpl.java` | `makeCanceledTrip(TripBean, CancelledTripBean)`: the `CANCELED` entity's `start_date` is the cancelled trip's own service date (was the build day; today's when the bean has none); the service passes the bean through. The one-argument form, now unused, is removed | F6 |
| `onebusaway-nyc-gtfsrt-lib/…/util/GtfsRealtimeLibrary.java` | `CANCELED` trip update `timestamp` in POSIX seconds (was milliseconds) | F6 |
| `onebusaway-nyc-presentation/…/impl/realtime/SkippedStopSupport.java` (new) | A skipped prediction record gets no expected times; its call is marked `CANCELLED`; helpers drop skipped onward calls and detect a skipped monitored call | F10 |
| `…/impl/realtime/siri/SiriOnwardCallsBuilderServiceImpl.java`, `SiriMonitoredCallBuilderServiceImpl.java` | Use it when building SIRI v1 calls | F10 |
| `…/impl/realtime/RealtimeServiceImpl.java` | Vehicle monitoring drops skipped onward calls; stop monitoring drops skipped onward calls and hides a visit whose own stop is skipped, unless `showCancelled` was asked for | F10 |
| `onebusaway-nyc-webapp/…/api/siri/impl/SiriSupportV2.java`, `RealtimeServiceV2Impl.java` | The same for the SIRI v2 (`siri_2`) types | F10 |
| Tests: `PredictionCancelledTripsSourceTest` (new), `QueuePredictionIntegrationServiceImplTest` (+2), `TripUpdateServiceImplTest` (+2), `SkippedStopSupportTest` (new), `SiriSupportV2SkippedStopTest` (new, webapp), `RecordedCancellationFeedTest` (new, gated) | Unit tests per fix; the gated harness replays a recorded engine stream through the real source, cancelled-trip service and GTFS-RT builder and checks the `CANCELED` set mirrors every message and empties after the TTL | all |
| `local-loop/ec2/opt-oba/run-snapshot-archiver.sh` (new), `local-loop/ec2/systemd/oba-{bustrek,yardtrek}-live-archiver.service` (new), `local-loop/ec2/iam/oba-yardtrek-live-read.json` (new) | Archiver for the two live snapshots (below) | archivers |
| `.gitignore` | The six bundle-builder CSVs the transit-data-federation tests leave at the module root | — |

New files carry no license header (a decision for this work; the existing files keep theirs). Build with
`-Dlicense.skip=true`.

## Configuration

| Key | Where | Default | Meaning |
|---|---|---|---|
| `tds.predictionCancelTtlSeconds` | TDM config, read by `PredictionCancelledTripsSource` | 90 | drop the cancelled set when no `FULL_DATASET` message has arrived for this long; the check runs every 15 s, so the set goes 90–105 s after the last message |
| `tds.enableCapi` | TDM config (existing) | false | while true the CAPI poller owns the set and the predictions source stands aside; both beans are wired. Turned off again, CAPI's last set is replaced by the next `FULL_DATASET` message; if the producer is silent, the first expiry tick (within 15 s) wipes it when the source held a set from before CAPI went on, otherwise it stays until a message arrives |
| `showCancelled` | SIRI request parameter (existing) | false | when set, skipped calls and visits are kept, marked `CANCELLED`, as cancelled trips are |

No new queue, port or host: the TDS reads the cancellation messages on the predictions queue it already
consumes. The inference engine's own cancelled-trip cache is deliberately never fed, so a bus the engine
places on a cancelled trip is still inferred; that sighting is the producer's un-cancel signal.

## Testing

```bash
# JDK 21: Mockito's cglib needs both add-opens, passed as one argument. The webapp WAR step is known to fail; the modules compile.
# gtfsrt-lib is in the list so gtfsrt builds against this tree, not a stale ~/.m2 copy.
OPENS="-DargLine=--add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.util=ALL-UNNAMED"
mvn -o -q -pl onebusaway-nyc-gtfsrt-lib,onebusaway-nyc-transit-data-federation,onebusaway-nyc-gtfsrt,onebusaway-nyc-presentation,onebusaway-nyc-webapp test -Dlicense.skip=true "$OPENS"
# the harness, on a cancellation stream a replay wrote (adj-<day>.pb from run-adjustments-only.sh in the predictions repo)
mvn -o test -pl onebusaway-nyc-gtfsrt -Dtest=RecordedCancellationFeedTest -Dlicense.skip=true "$OPENS" \
    -Drecorded.cancellations.pb=/path/adj-2026-08-13.pb -Drecorded.cancellations.out=/path/tds-spans.csv
```

The harness output (`tds-spans.csv`, one row per cancelled trip instance with cancel and un-cancel instants
as the TDS saw them) is compared with the producer's `cancel-timeline.py` in the predictions repo; on the
runs so far they match exactly.

## The snapshot archivers

Bus Trek and Yard Trek each publish one S3 object that is overwritten in place and keeps no history
(`s3://mtabustrekdataexports/bustrek-digital-services/bustrek_latest.csv`, the service day's rows, rewritten
about every 60 s; `s3://yardtrek-digital-services/yardtrek-blocks_latest.csv`, the day's block assignments,
about every 30 s). `run-snapshot-archiver.sh <bustrek|yardtrek>` polls the object's ETag and copies every new
version, gzipped, to `s3://ds-oba/replay/prediction/dispatcher-actions/<name>-live/<UTC date>/<UTC time>Z.csv.gz`,
named by the source's LastModified. The predictions repo's replay tools play those files back on record time
(`adjustments.yardtrek.dir`, `bustrek-live-lag.py`), which is how live days are re-run offline.

Both units run on `oba-nyc-filtered` (Yard Trek since 2026-10-26 21:14 UTC, Bus Trek since 2026-10-29 21:30 UTC).
The box still runs the earlier two-script form (`run-{bustrek,yardtrek}-live-archiver.sh`); the merged script
behaves the same and replaces them at the next install. Reading the sources assumes
`arn:aws:iam::032610139471:role/ds-bustrek-yardtrek-s3-read`; `iam/oba-yardtrek-live-read.json` is the inline
policy on `oba-nyc-ec2-role` that allows the assume. Writing `ds-oba` uses the instance profile.

```bash
sudo install -m 755 local-loop/ec2/opt-oba/run-snapshot-archiver.sh /opt/oba/
sudo install -m 644 local-loop/ec2/systemd/oba-{bustrek,yardtrek}-live-archiver.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now oba-bustrek-live-archiver oba-yardtrek-live-archiver
journalctl -u oba-bustrek-live-archiver -f     # "bustrek-live: copied=… errors=0 …" every 10 min
```

Optional `OBA_SNAP_*` overrides go in `/etc/default/oba-{bustrek,yardtrek}-live-archiver` (read by the units
if present). A gzip that fails is not uploaded and its partial file is removed; an unparseable LastModified is
skipped rather than written under an empty key; a failure that repeats (an expired assume-role, say) is logged
once per 10 min and slows polling to 60 s until a poll succeeds.

They feed no OBA service and are safe to restart alone; an outage is an unrecoverable gap in the archive.

## What is necessary, and what is not

For the engine's output to reach riders correctly, three of these changes are required and the rest are
quality or tooling:

| Change | Without it | Verdict |
|---|---|---|
| `QueuePredictionIntegrationServiceImpl`: route `FULL_DATASET` messages to the source (F13) | the cancellation message is parsed as predictions: one trip lookup per cancelled trip every 15 s, an empty record set cached under a vehicle-less key, and the cancelled set never reaches the TDS | **required** for the cancel path |
| `PredictionCancelledTripsSource` + the two `data-sources.xml` beans (F13) | no cancelled trips at all; CAPI is off in prod, so the existing poller contributes nothing | **required** for the cancel path |
| `QueuePredictionIntegrationServiceImpl`: a `SKIPPED` stop-time update keeps no times (F9) | the unset protobuf times read as 0, a 1970 arrival, which SIRI renders as "arriving now" at a stop the bus will not serve: worse than no change | **required** for the skip path; the GTFS-RT side already emits `SKIPPED` for such a record (`GtfsRealtimeLibrary.makeStopTimeUpdate` on `develop`) |
| `SkippedStopSupport`, the SIRI v1 builders, `RealtimeServiceImpl` (F10) | a skipped record with no times still reaches SIRI and is rendered "now"; F9 alone is not enough for Bus Time | **required** with F9 |
| `SiriSupportV2`, `RealtimeServiceV2Impl` (F10, SIRI v2) | the same for v2 clients | required if v2 is served to anyone |
| `CANCELED` entity `timestamp` in seconds and `start_date` from the trip's service date (F6) | the entity is still emitted; its timestamp is 1,000x too large and its date is the build day, wrong for a trip cancelled after midnight; consumers keying on (trip_id, start_date) miss it | strongly advised, two small changes |
| `tds.predictionCancelTtlSeconds` and the expiry thread | a silent producer leaves trips cancelled until the TDS restarts | advised; the default needs no config |
| the snapshot archivers | the replay tooling loses its live inputs; no rider effect | tooling |

## Not done

- The PR against `develop` is not opened. F10 must ship with F9: without the SIRI filter a skipped stop shows as
  "expected now". F13 stands aside while `tds.enableCapi` is true, so the swap is a config decision at deploy time.
- The TDS's reaction to the engine's empty next-trip entity (F11, predictions repo) is not verified with a TDS in
  the loop; one harness test in the `RecordedCancellationFeedTest` pattern would do it.
- `RealtimeServiceV2Impl`'s visit hiding has no unit test; the `SiriSupportV2` helpers do.
- `tds.predictionCancelTtlSeconds` has no TDM default entry; the code default (90 s) applies.
- Nothing from this branch is deployed to any OBA instance except the two archiver units.
