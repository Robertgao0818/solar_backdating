#!/usr/bin/env bash
# Resumable tmux launcher for ISSUE-29.  The scientific stages live in
# issue29_coj_external_robustness.py; this wrapper owns preflight, logging,
# duplicate-session protection, and completion sentinels.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESSION="${SESSION:-issue29_coj_robustness}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$HOME/zasolar_data/geid_temporal/coj_run3_external_robustness_2026-07}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
WORKERS="${WORKERS:-4}"
QPS="${QPS:-2}"
RUN_LOG="$OUTPUT_ROOT/run.log"
PYTHON_RUNNER="$REPO_ROOT/scripts/validation/issue29_coj_external_robustness.py"

usage() {
  echo "usage: $0 launch|run|status"
}

status() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "session=$SESSION state=running"
  else
    echo "session=$SESSION state=not-running"
  fi
  if [[ -f "$RUN_LOG" ]]; then
    tail -n 30 "$RUN_LOG"
  fi
}

run_stage() {
  local stage="$1"
  local sentinel="$OUTPUT_ROOT/.${stage}.done"
  if [[ -f "$sentinel" ]]; then
    echo "[ISSUE29] $(date -Is) skip completed stage=$stage"
    return 0
  fi
  echo "[ISSUE29] $(date -Is) begin stage=$stage"
  python -u "$PYTHON_RUNNER" "$stage" \
    --output-root "$OUTPUT_ROOT" \
    --model "$MODEL" \
    --workers "$WORKERS" \
    --qps "$QPS"
  touch "$sentinel"
  echo "[ISSUE29] $(date -Is) complete stage=$stage"
}

run_all() {
  cd "$REPO_ROOT"
  # shellcheck disable=SC1091
  source scripts/activate_env.sh
  mkdir -p "$OUTPUT_ROOT"
  exec > >(stdbuf -oL tee -a "$RUN_LOG") 2>&1
  echo "[ISSUE29] $(date -Is) run start model=$MODEL workers=$WORKERS qps=$QPS"
  run_stage preflight
  run_stage prepare
  run_stage smoke
  run_stage pilot
  run_stage report
  touch "$OUTPUT_ROOT/.complete"
  echo "[ISSUE29] $(date -Is) all automated stages complete"
}

command="${1:-}"
case "$command" in
  launch)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "ERROR: tmux session '$SESSION' already exists" >&2
      exit 3
    fi
    if [[ ! -f "$PYTHON_RUNNER" ]]; then
      echo "ERROR: missing runner $PYTHON_RUNNER" >&2
      exit 4
    fi
    mkdir -p "$OUTPUT_ROOT"
    tmux new-session -d -s "$SESSION" \
      "cd '$REPO_ROOT' && SESSION='$SESSION' OUTPUT_ROOT='$OUTPUT_ROOT' MODEL='$MODEL' WORKERS='$WORKERS' QPS='$QPS' bash '$0' run"
    sleep 2
    if ! tmux has-session -t "$SESSION" 2>/dev/null; then
      if [[ -f "$OUTPUT_ROOT/.complete" ]]; then
        echo "session exited after completing all stages; $OUTPUT_ROOT/.complete exists"
        status
        exit 0
      fi
      echo "ERROR: session exited during startup; inspect $RUN_LOG" >&2
      status
      exit 5
    fi
    status
    ;;
  run)
    run_all
    ;;
  status)
    status
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
