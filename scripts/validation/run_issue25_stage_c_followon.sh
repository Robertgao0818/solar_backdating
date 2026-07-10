#!/usr/bin/env bash
# Wait for Stage-1, select the winner, then run Stage-2 and B0 sequentially.
set -euo pipefail

cd /home/gaosh/projects/solar_backdating
source scripts/activate_env.sh

ROOT="${ROOT:-/home/gaosh/zasolar_data/geid_temporal/issue25_teacher_geometry_20260710}"
RUN_ROOT="${RUN_ROOT:-$ROOT/stage_c}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
STAGE1_ANCHORS="$RUN_ROOT/stage1_anchors_96m.csv"
ANALYSIS="$RUN_ROOT/stage1_analysis.json"

echo "[ISSUE25] follow-on waiting for Stage-1 utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
while [[ ! -f "$RUN_ROOT/stage1/.complete" ]]; do
  if grep -q '\[ISSUE25\] driver_exit=[1-9]' "$RUN_ROOT/stage1_driver.log" 2>/dev/null; then
    echo "ERROR: Stage-1 driver exited nonzero" >&2
    exit 2
  fi
  sleep 20
done

python -u scripts/validation/issue25_stage_c.py analyze-stage1 \
  --run-root "$RUN_ROOT" \
  --anchors "$STAGE1_ANCHORS" \
  --output "$ANALYSIS" \
  --model "$MODEL"

bash scripts/validation/run_issue25_stage_c_stage2.sh
bash scripts/validation/run_issue25_stage_c_b0.sh

source scripts/activate_env.sh >/dev/null
pytest -q tests/

date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/.complete"
echo "[ISSUE25] Stage C complete utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
