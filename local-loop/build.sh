#!/usr/bin/env bash
# Build the main repo (all modules) into ~/.m2 with JDK 11. Runnable from any directory.
#
#   -Dlicense.skip=true : three non-source files on the EC2 branches (local-loop/ec2/bundle/*,
#                         .github/workflows/deploy.yml) carry no license header and fail the check.
#   -fae                : keep going after a failure. NOTE this means SUCCESS lines can be followed
#                         by failures further down — read the reactor summary, not the first green.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
cd "$MAIN_REPO"
exec mvn -P skip-integration-tests -DskipTests -Dlicense.skip=true -B -fae install "$@"
