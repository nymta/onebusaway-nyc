# /opt/oba/env-local.sh  --  oba-nyc-traffic-weights  (i-0a0d51b7c668935d1)
#
# Traffic-weight A/B arm. Same input feed and deadband as oba-nyc-prod, so both
# are left unset here. Variables under test: the prediction weights (TRAFFIC on)
# and the component fallback mode (missing components redistribute proportionally
# across whatever's present, rather than SCHEDULE absorbing the whole gap).

export OBA_PREDICTIONS_WEIGHTS=10/30/30/30

export OBA_PREDICTIONS_FALLBACK_MODE=PROPORTIONAL

export OBA_ARCHIVER_S3_PREFIX=v5-traffic-weights

# The publisher belongs to oba-nyc-prod alone (it holds the allowlisted Elastic IP).
export OBA_CSPUB_ENABLED=0
