#!/usr/bin/env bash
# Resumable T3-only rescore with gemini-3.5-flash-lite.
# Re-running this command continues the new ledger. It does not delete
# locks, responses, or sentinels, and it does not write into the 3.1 pack.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/activate_env.sh
exec python -u scripts/temporal/run_v2_t3_gemini35_compare.py "$@"
