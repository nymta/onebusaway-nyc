#!/usr/bin/env bash
# Write the classpath that build-bundle.sh needs into .context/<pick>/cp.txt. Runnable from any dir.
#
# Module choice matters. FederatedTransitDataBundleCreatorMain lives in
# onebusaway-transit-data-federation-BUILDER, and the STIF tasks live in
# onebusaway-nyc-transit-data-federation. Only a module pulling in BOTH gives a working classpath.
# onebusaway-nyc-gtfsrt does; onebusaway-nyc-transit-data-federation on its own does NOT (it never
# declares the builder artifact) and fails at run time with ClassNotFoundException. RUNBOOK §10 uses
# the predictions repo's integration-tests module for the same reason.
#
# -Dmdep.outputFile must be ABSOLUTE: a relative path resolves against the module basedir, not the
# reactor root, so it silently lands under onebusaway-nyc-transit-data-federation/ instead.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
PICK="${1:-wholeMTA-c6}"
OUT="$MAIN_REPO/.context/$PICK/cp.txt"

cd "$MAIN_REPO"
mvn -pl onebusaway-nyc-gtfsrt dependency:build-classpath \
  -Dmdep.outputFile="$OUT" -Dmdep.includeScope=test

# Verify here rather than 4 minutes into a bundle build.
for C in org.onebusaway.transit_data_federation.bundle.FederatedTransitDataBundleCreatorMain \
         org.onebusaway.nyc.transit_data_federation.bundle.tasks.stif.stifImport.StifImportTask; do
  javap -cp "$(cat "$OUT")" "$C" >/dev/null 2>&1 \
    && echo "OK   $C" \
    || { echo "MISSING from classpath: $C" >&2; exit 1; }
done
echo "wrote $OUT"
