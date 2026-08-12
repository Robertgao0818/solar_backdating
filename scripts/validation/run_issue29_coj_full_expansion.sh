#!/usr/bin/env bash
# ISSUE-29 owner-authorized Stage-D full expansion. Resumable by 500-target
# atomic Parquet shards; fixed five-image same-target instrument.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESSION="${SESSION:-issue29_coj_full}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$HOME/zasolar_data/geid_temporal/coj_run3_external_robustness_2026-07}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
WORKERS="${WORKERS:-60}"
QPS="${QPS:-8}"
LOG="$OUTPUT_ROOT/full/full_run.log"

status() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "session=$SESSION state=running"
  elif [[ -f "$OUTPUT_ROOT/full/.complete" ]]; then
    echo "session=$SESSION state=complete"
  else
    echo "session=$SESSION state=not-running"
  fi
  [[ -f "$LOG" ]] && tail -n 35 "$LOG"
}

run_all() {
  cd "$REPO_ROOT"
  # shellcheck disable=SC1091
  source scripts/activate_env.sh
  mkdir -p "$OUTPUT_ROOT/full"
  exec > >(stdbuf -oL tee -a "$LOG") 2>&1
  echo "[ISSUE29-FULL] $(date -Is) start model=$MODEL workers=$WORKERS qps=$QPS"
  python -u scripts/validation/issue29_coj_external_robustness.py preflight --output-root "$OUTPUT_ROOT" --model "$MODEL" --workers 1 --qps 1
  python -u scripts/validation/issue29_coj_external_robustness.py full --output-root "$OUTPUT_ROOT" --model "$MODEL" --workers "$WORKERS" --qps "$QPS"
  touch "$OUTPUT_ROOT/full/.full.done"
  python -u scripts/validation/issue29_coj_external_robustness.py full_report --output-root "$OUTPUT_ROOT" --model "$MODEL" --workers "$WORKERS" --qps "$QPS"
  touch "$OUTPUT_ROOT/full/.report.done" "$OUTPUT_ROOT/full/.complete"
  echo "[ISSUE29-FULL] $(date -Is) complete"
}

case "${1:-}" in
  launch)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "ERROR: session '$SESSION' already exists" >&2
      exit 3
    fi
    mkdir -p "$OUTPUT_ROOT/full"
    tmux new-session -d -s "$SESSION" \
      "cd '$REPO_ROOT' && OUTPUT_ROOT='$OUTPUT_ROOT' MODEL='$MODEL' WORKERS='$WORKERS' QPS='$QPS' bash '$0' run"
    sleep 2
    status
    ;;
  run) run_all ;;
  status) status ;;
  *) echo "usage: $0 launch|run|status" >&2; exit 2 ;;
esac
