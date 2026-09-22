# /opt/oba/env-local.sh  --  oba-nyc-traffic-weights  (i-0a0d51b7c668935d1)
#
# Traffic-weight A/B arm. Same input feed and deadband as oba-nyc-prod (the only
# variable under test is the prediction weights), so both are left unset here.

export OBA_PREDICTIONS_WEIGHTS=10/30/30/30

export OBA_ARCHIVER_S3_PREFIX=v5-traffic-weights

# The publisher belongs to oba-nyc-prod alone (it holds the allowlisted Elastic IP).
export OBA_CSPUB_ENABLED=0
