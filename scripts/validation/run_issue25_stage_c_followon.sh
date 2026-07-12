#!/usr/bin/env bash
# Wait for Stage-1, select the winner, then run Stage-2 and B0 sequentially.
set -euo pipefail

cd /home/gao/projects/solar_backdating
source scripts/activate_env.sh

ROOT="${ROOT:-/home/gao/zasolar_data/geid_temporal/issue25_teacher_geometry_20260710}"
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
  if tmux has-session -t issue25_stagec_stage1 2>/dev/null; then
    PANE_DEAD=$(tmux list-panes -t issue25_stagec_stage1 -F '#{pane_dead}' | head -1)
    if [[ "$PANE_DEAD" == "1" ]]; then
      echo "ERROR: Stage-1 tmux pane died before the completion sentinel" >&2
      exit 2
    fi
  fi
  sleep 20
done

python -u scripts/validation/issue25_stage_c.py analyze-stage1 \
  --run-root "$RUN_ROOT" \
  --anchors "$STAGE1_ANCHORS" \
  --output "$ANALYSIS" \
  --model "$MODEL"

bash scripts/validation/run_issue25_stage_c_b0.sh

source scripts/activate_env.sh >/dev/null
pytest -q tests/

date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/.ready_for_stage2_human"
echo "[ISSUE25] Stage-1 + B0 complete; Stage-2 awaits human winner confirmation utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
