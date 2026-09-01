# /opt/oba/env-local.sh  --  oba-nyc-prod  (i-0386b6bb8338b2f67)
#
# The primary. Runs the stock configuration, so it sets only what is genuinely host-specific:
# everything else falls through to the ${VAR:-default} values in run-inference.sh, which ARE
# prod's values (deadband 10 m / 7 s / 30 s on nyct.bustech.gps).

# This host holds the allowlisted Elastic IP, so it is the only one that may run the CS
# filtered-AVL publisher. A second publisher would double-publish into the shared exchange
# and corrupt the replay archive.
export OBA_CSPUB_ENABLED=1

export OBA_ARCHIVER_S3_PREFIX=v1
