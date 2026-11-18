# The dispatcher-actions instance (`oba-nyc-dispatcher`, archive prefix `v7-dispatcher-actions`)

A sixth OBA instance: `oba-nyc-filtered`'s configuration (BusTech's ~28 s filtered feed, no deadband) with the
predictions engine's dispatcher adjustments switched on, built on a clone of `oba-nyc-cancel` for its Mongo history.
It publishes `SKIPPED` stop-time updates and `CANCELED` trip updates in its own feed and archive.

**Why clone `oba-nyc-cancel` and not `oba-nyc-filtered`.** Its predictions database holds production's link-time
history (`AggregateLinkTimes-2026-10-31.ndjson.gz`, loaded 2026-11-02) plus everything it has learned since, where
`oba-nyc-filtered`'s is shallow on weekends (0.35–0.5 points of accuracy). And it runs the stock predictions engine on
the same input, so its predictions archive (`v6-cancel`) is the control: production-depth history, engine off.
`oba-nyc-filtered` (`v3-filtered`) is a second reference.

**Nothing of the cancel instance's own logic is adopted.** That logic (CANCELED for scheduled trips with no matched
vehicle, and a GTFS-RT archiver) lives in its Inference Repo branch `ds/ec2-cancel-scheduled`, its `env-local.sh`
(`OBA_GTFSRT_CANCEL_UNMATCHED`) and its `oba-gtfsrt-archiver` unit. The quarantine removes the `env-local.sh` and
disables the unit, `install.sh` replaces the clone's `deploy.sh` (the cancel branch's installs and starts that
archiver) with this branch's, and the deploy checks out and rebuilds `timothy/dispatcher-actions`, which contains
neither of that branch's two commits. Both are confined to the GTFS-RT module and host scripts, so
`oba-nyc-cancel`'s predictions archive is the stock engine's. Step 7 verifies the result. Why, the settings and the results:
onebusaway-nyc-predictions `replay/docs/DISPATCHER-ACTIONS-STATUS.md` and `-STAGE5-PLAN.md`.

Run 2026-11-18 01:03 UTC: the instance is `i-0dcf1fc4e88c347ff`, engine on since 01:28 UTC. The files:

| file | what |
|---|---|
| `../env-local/oba-nyc-dispatcher.sh` | the instance's identity: v3's input and settings, the archive prefixes, the engine flags (`OBA_PREDICTIONS_EXTRA_OPTS`) and the log config (`OBA_PREDICTIONS_LOG4J`) |
| `quarantine-userdata.yaml` | first boot of the clone, before any OBA unit can start: stops and disables every `oba-*` unit, removes the inherited `env-local.sh` and leaves a quarantine one with uploads off (it works for a clone of any OBA instance; `oba-nyc-cancel` itself was launched with the `write_files` half) |
| `install.sh` | on the quarantined clone, as root: installs the identity, the log config, the sync timer and `TimePoints.csv`, disables the units that belong to other instances, enables this instance's |
| `log4j2-predictions-dispatcher.xml` | `log4j2-predictions.xml` plus a daily file of the engine's decision lines, `/data/adjustments/logs/adj-<date>.log` |
| `adjustments-sync.sh`, `oba-adjustments-sync.service`, `.timer` | hourly: decision logs and the skip-span file to `s3://ds-oba/replay/prediction/dispatcher-actions/live/v7-dispatcher-actions/` |

## Before starting

1. Both `timothy/dispatcher-actions` branches pushed to `nymta`. They already include `ds/ec2-deploy` (merged
   2026-11-17), so the new instance loses none of the other instances' fixes.
2. D&A told that `v7-dispatcher-actions` will carry `SKIPPED` and `CANCELED`, and how to score them.

## Steps

```bash
P=s3://ds-oba/replay/prediction/dispatcher-actions/live/v7-dispatcher-actions
SRC=i-03be377d82a9bbd72                      # oba-nyc-cancel: the Mongo history; its config and code are replaced

# 1. publish the install files
aws s3 sync local-loop/ec2/dispatcher/ $P/deploy/ --exclude README.md
aws s3 cp local-loop/ec2/env-local/oba-nyc-dispatcher.sh $P/deploy/
aws s3 cp local-loop/ec2/opt-oba/deploy.sh $P/deploy/          # this branch's, to replace the clone's before the first deploy

# 2. image oba-nyc-cancel without rebooting it (root + the 500 GB /data volume: bundle, Mongo history, built trees)
AMI=$(aws ec2 create-image --instance-id $SRC --name oba-nyc-dispatcher-base-$(date +%F) --no-reboot \
  --query ImageId --output text); aws ec2 wait image-available --image-ids $AMI

# 3. launch the clone, quarantined, with /data kept on termination (a clone inherits delete-on-termination)
ID=$(aws ec2 run-instances --image-id $AMI --instance-type c7i.12xlarge --subnet-id subnet-893587ee \
  --security-group-ids sg-0fca012b2afe2a1ee --key-name oba-nyc-ec2 \
  --iam-instance-profile Arn=arn:aws:iam::032610139471:instance-profile/oba-nyc-ec2-profile \
  --metadata-options HttpTokens=required \
  --block-device-mappings '[{"DeviceName":"/dev/sdf","Ebs":{"DeleteOnTermination":false}}]' \
  --user-data file://local-loop/ec2/dispatcher/quarantine-userdata.yaml \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=oba-nyc-dispatcher},{Key=project,Value=oba-nyc}]' \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 describe-instances --instance-ids $ID \
  --query 'Reservations[].Instances[].BlockDeviceMappings[].[DeviceName,Ebs.DeleteOnTermination]' --output text

# 4. over SSM on the clone: confirm the quarantine held before anything else
cat /opt/oba/CLONE-QUARANTINED; systemctl is-active oba-predictions oba-inference oba-gtfsrt \
  oba-gtfsrt-archiver oba-predictions-archiver                                     # all inactive
grep -c quarantine /opt/oba/env-local.sh                                          # the quarantine identity, not the source's
journalctl -b --no-pager -u 'oba-*' | head                                       # nothing from this boot

# 5. install the identity (starts nothing)
aws s3 cp $P/deploy/install.sh /root/install.sh && bash /root/install.sh

# 6. build both repos at the branch and start the chain (several minutes; Mongo is not touched)
/opt/oba/deploy.sh deploy timothy/dispatcher-actions timothy/dispatcher-actions
systemctl start oba-monitor.timer oba-adjustments-sync.timer

# 7. verify against the process, not the files
ps -ef | grep -o 'oba.rmq.streamName=[^ ]*'                    # nyct.bustech.gps-filtered
ps -ef | grep -o 'adjustments\.[a-zA-Z.]*=[^ ]*'               # the six flags of env-local
P2=$(systemctl show oba-predictions-archiver -p MainPID --value); tr '\0' '\n' < /proc/$P2/environ | grep OBA_ARCHIVER_S3_PREFIX
grep -m1 'dispatcher adjustments enabled' /data/adjustments/logs/adj-current.log
grep 'adj summary' /data/adjustments/logs/adj-current.log | tail -1   # after the first hour: every counter
# none of the cancel instance's logic: our branch, no cancel-unmatched setting, its archiver off
sudo -u oba git -C /opt/oba/onebusaway-nyc rev-parse --abbrev-ref HEAD               # timothy/dispatcher-actions
sudo -u oba git -C /opt/oba/onebusaway-nyc-predictions rev-parse --abbrev-ref HEAD   # timothy/dispatcher-actions
G=$(systemctl show oba-gtfsrt -p MainPID --value); tr '\0' '\n' < /proc/$G/environ | grep -c OBA_GTFSRT_CANCEL   # 0
systemctl is-enabled oba-gtfsrt-archiver                                             # disabled
```

Then the feed checks, M1–M6 in the predictions repo's `DISPATCHER-ACTIONS-REVIEW-PACKET.md` §8, and a week of daily
scoring from the synced logs.

## What the clone carries, and what to do about it

- **Mongo history:** `oba-nyc-cancel`'s, as of the image: production's link-time history to 2026-10-31 and its own
  since. This is what the clone is for. From then on the two instances learn from the same input, so their histories
  stay close.
- **The cancel instance's code and units:** replaced or disabled, as above. Its `/data/gtfsrt-archive` and
  `/data/historicals` (2 GB) come along and can be deleted on the clone.
- **The inference archiver stays off:** the unit is on the clone but disabled, and `install.sh` leaves it so. This
  instance's inference is `oba-nyc-filtered`'s input through the same code, and that instance's archive serves for
  replays. Enable `oba-inference-archiver` here if a replay of one of its days fails to match its live decisions.
- **Dependence on the control:** `oba-nyc-cancel` has to keep running unchanged for the comparison to hold, and its
  owner should know it is now a control.

## Not covered here

- A CloudWatch dashboard and disk alarm for the instance (v3's were copied from prod's with the ids rewritten).
- Stopping the shadow units on `oba-nyc-filtered` once this instance publishes, or keeping them a week to compare
  the shadow's decisions with the live ones.

## Rollback

Engine off, instance kept: remove `OBA_PREDICTIONS_EXTRA_OPTS` from `/opt/oba/env-local.sh` and
`systemctl restart oba-predictions`. Whole instance back to the shared tree:
`/opt/oba/deploy.sh deploy ds/ec2-deploy ds/ec2-deploy`. Removing the instance: terminate it, then delete its `/data`
volume by hand (it is kept on termination).
