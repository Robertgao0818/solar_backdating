#!/usr/bin/env bash
# Resumable T3 window plus the 149-roof T2 arm. Re-running this command
# continues the raw ledger. It does not delete locks, responses, or sentinels.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/activate_env.sh
exec python -u scripts/temporal/run_reference_fulltimeline_scan.py \
  --stage v2 \
  --output-root "${HOME}/zasolar_data/geid_temporal/reference_fulltimeline_v2_10grid_20261005" \
  --plan-dir "${HOME}/zasolar_data/geid_temporal/reference_fulltimeline_lite_10grid_20261004/plan" \
  "$@"
