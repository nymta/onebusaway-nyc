# Per-instance config (`/opt/oba/env-local.sh`)

One file per EC2 instance, holding everything that differs between them. The deployed tree is
**identical on all three** — an instance is defined entirely by the file here.

**These are tracked for reproducibility, not installed.** `deploy.sh` deliberately does not touch
`/opt/oba/env-local.sh`, so a host keeps its identity across deploys. Copy by hand when creating or
changing an instance. Making git authoritative would be tidier, but it adds a way to silently
rewrite an instance's identity from a wrong id→file mapping, and silent config divergence is
already this system's dominant failure mode (see the `env-common.sh` trap below).

No secrets here — RabbitMQ and S3 credentials come from SSM Parameter Store at runtime via
`env-common.sh`'s `gp()` helper.

| file | instance | input stream | deadband | archive prefix |
|---|---|---|---|---|
| `oba-nyc-prod.sh` | `i-0386b6bb8338b2f67` | `nyct.bustech.gps` | 10 m / 7 s / 30 s (defaults) | `v1/` |
| `oba-nyc-filtered.sh` | `i-0d78f39b83d961f5c` | `nyct.bustech.gps-filtered` | **off** (pre-filtered upstream) | `v3-filtered/` |
| `oba-nyc-fused-gps.sh` | `i-0a45277b7b11ea8be` | `nyct.bus.fused-gps` | 10 m / 7 s / 30 s (explicit) | `v4-fused-gps/` |

## Check an instance against its tracked config

Compare the `export` lines only — comment drift should not raise an alarm:

```
ID=i-0a45277b7b11ea8be; NAME=oba-nyc-fused-gps
CID=$(aws ssm send-command --instance-ids $ID --document-name AWS-RunShellScript \
  --parameters 'commands=["sudo grep -E \"^export\" /opt/oba/env-local.sh | sort"]' \
  --query Command.CommandId --output text)
sleep 5
diff <(aws ssm get-command-invocation --command-id $CID --instance-id $ID \
        --query StandardOutputContent --output text | grep '^export' | sort) \
     <(grep -E '^export' $NAME.sh | sort) && echo "matches"
```

## Two traps

**The file is inert unless `env-common.sh` sources it.** That line was added after the primary was
first built. Because the run scripts fall through to `${VAR-}` defaults rather than erroring, a host
with a stale `env-common.sh` reads *nothing* here and looks perfectly healthy while running stock
config — found on `oba-nyc-prod` on 2026-08-21. Verify against the live process, never the file:

```
grep -c env-local /opt/oba/env-common.sh        # 0 = stale, this file is inert
ps -ef | grep -o 'oba.rmq.streamName=[^ ]*'
ps -ef | grep -o 'oba.deadband[^ ]*'
```

**A variable only works if the deployed script reads it.** `OBA_RMQ_STREAM_NAME` and
`OBA_DEADBAND_ENABLED` were added to `run-inference.sh` in `88b0900e5`; on any older checkout they
are silently ignored. Confirm `grep -c OBA_RMQ_STREAM_NAME /opt/oba/run-inference.sh` is non-zero
before trusting a feed change.
