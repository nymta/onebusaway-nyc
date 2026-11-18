# The dispatcher-actions instance (`oba-nyc-dispatcher`, archive prefix `v7-dispatcher-actions`)

A sixth OBA instance: a clone of `oba-nyc-filtered` (v3) with the predictions engine's dispatcher adjustments switched
on, so that v3 stays the control and the engine is the only difference. It publishes `SKIPPED` stop-time updates and
`CANCELED` trip updates in its own feed and archive. Why, the settings and the results:
onebusaway-nyc-predictions `replay/docs/DISPATCHER-ACTIONS-STATUS.md` and `-STAGE5-PLAN.md`.

Nothing here has been run yet. The files:

| file | what |
|---|---|
| `../env-local/oba-nyc-dispatcher.sh` | the instance's identity: v3's input and settings, the archive prefixes, the engine flags (`OBA_PREDICTIONS_EXTRA_OPTS`) and the log config (`OBA_PREDICTIONS_LOG4J`) |
| `quarantine-userdata.yaml` | first boot of the clone: stops and disables every `oba-*` unit and removes the inherited `env-local.sh`, once |
| `install.sh` | on the quarantined clone, as root: installs the identity, the log config, the sync timer and `TimePoints.csv`, disables v3's own units, enables this instance's |
| `log4j2-predictions-dispatcher.xml` | `log4j2-predictions.xml` plus a daily file of the engine's decision lines, `/data/adjustments/logs/adj-<date>.log` |
| `adjustments-sync.sh`, `oba-adjustments-sync.service`, `.timer` | hourly: decision logs and the skip-span file to `s3://ds-oba/replay/prediction/dispatcher-actions/live/v7-dispatcher-actions/` |

## Before starting

1. Both `timothy/dispatcher-actions` branches pushed to `nymta`. They already include `ds/ec2-deploy` (merged
   2026-11-17), so the new instance loses none of the other instances' fixes.
2. D&A told that `v7-dispatcher-actions` will carry `SKIPPED` and `CANCELED`, and how to score them.

## Steps

```bash
P=s3://ds-oba/replay/prediction/dispatcher-actions/live/v7-dispatcher-actions
SRC=i-0d78f39b83d961f5c                      # oba-nyc-filtered

# 1. publish the install files
aws s3 sync local-loop/ec2/dispatcher/ $P/deploy/ --exclude README.md
aws s3 cp local-loop/ec2/env-local/oba-nyc-dispatcher.sh $P/deploy/

# 2. image v3 without rebooting it (root + the 500 GB /data volume: bundle, Mongo history, built trees)
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
cat /opt/oba/CLONE-QUARANTINED; systemctl is-active oba-predictions oba-inference oba-shadow-predictions \
  oba-bustrek-live-archiver oba-yardtrek-live-archiver oba-predictions-archiver   # all inactive
journalctl -b --no-pager -u 'oba-*' | head                                       # nothing from this boot

# 5. install the identity (starts nothing)
aws s3 cp $P/deploy/install.sh /root/install.sh && bash /root/install.sh

# 6. build both repos at the branch and start the chain (several minutes; Mongo is not touched)
/opt/oba/deploy.sh deploy timothy/dispatcher-actions timothy/dispatcher-actions
systemctl start oba-inference-archiver oba-monitor.timer oba-adjustments-sync.timer

# 7. verify against the process, not the files
ps -ef | grep -o 'oba.rmq.streamName=[^ ]*'                    # nyct.bustech.gps-filtered
ps -ef | grep -o 'adjustments\.[a-zA-Z.]*=[^ ]*'               # the six flags of env-local
P2=$(systemctl show oba-predictions-archiver -p MainPID --value); tr '\0' '\n' < /proc/$P2/environ | grep OBA_ARCHIVER_S3_PREFIX
grep -m1 'dispatcher adjustments enabled' /data/adjustments/logs/adj-current.log
grep 'adj summary' /data/adjustments/logs/adj-current.log | tail -1   # after the first hour: every counter
```

Then the feed checks, M1–M6 in the predictions repo's `DISPATCHER-ACTIONS-REVIEW-PACKET.md` §8, and a week of daily
scoring from the synced logs.

## What the clone carries, and what to do about it

- **Mongo history:** v3's, as of the image. That is the same depth as the control, which is what the comparison
  wants. `oba-nyc-cancel` instead loaded production's historicals at launch (`/data/historicals` on that host); how
  is not written down, and it is not needed for a v3-against-v7 comparison.
- **The shadow:** its units are disabled by the quarantine and again by `install.sh`. Its 4.7 GB under `/data/shadow`
  and its Mongo database `onebusaway-nyc-predictions-shadow` come along and can be deleted on the clone.
- **v3's snapshot archivers** (`oba-bustrek-live-archiver`, `oba-yardtrek-live-archiver`): disabled here; v3 keeps
  archiving.
- **v3's inference archive prefix:** replaced by `oba-ec2-inference/v7-dispatcher-actions` in the identity, so a day
  on this instance can be replayed on its own records.

## Not covered here

- A CloudWatch dashboard and disk alarm for the instance (v3's were copied from prod's with the ids rewritten).
- Stopping the shadow units on `oba-nyc-filtered` once this instance publishes, or keeping them a week to compare
  the shadow's decisions with the live ones.

## Rollback

Engine off, instance kept: remove `OBA_PREDICTIONS_EXTRA_OPTS` from `/opt/oba/env-local.sh` and
`systemctl restart oba-predictions`. Whole instance back to the shared tree:
`/opt/oba/deploy.sh deploy ds/ec2-deploy ds/ec2-deploy`. Removing the instance: terminate it, then delete its `/data`
volume by hand (it is kept on termination).
