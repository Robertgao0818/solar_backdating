#!/usr/bin/env bash
# Resumable tmux launcher: GEHI info at every Vexcel-covered H3-7 centroid.
# TM + Wayback, z19. No chip downloads.
#
#   bash scripts/temporal/run_vexcel_h3r7_gehi_info.sh launch
#   bash scripts/temporal/run_vexcel_h3r7_gehi_info.sh status
#   bash scripts/temporal/run_vexcel_h3r7_gehi_info.sh run      # foreground
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESSION="${SESSION:-vexcel_h3r7_gehi_info}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$HOME/zasolar_data/geid_temporal/satellite_vexcel_h3r7_gehi_info_2026-09-13}"
PYTHON_RUNNER="$ROOT/scripts/temporal/probe_vexcel_h3r7_gehi_info.py"
RUN_LOG="$OUTPUT_ROOT/run.log"
GEHI_EXE="${GEHI_EXE:-$HOME/zasolar_data/tools/GEHistoricalImagery/GEHistoricalImagery}"
MAX_PROBE_PASSES="${MAX_PROBE_PASSES:-5}"

usage() {
  echo "usage: $0 launch|run|status" >&2
}

status() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "session=$SESSION state=running"
  else
    echo "session=$SESSION state=not-running"
  fi
  if [[ -f "$OUTPUT_ROOT/progress.json" ]]; then
    python3 - "$OUTPUT_ROOT/progress.json" <<'PY'
import json, sys
p = json.loads(open(sys.argv[1]).read())
print(
    "progress: {n_done_success}/{n_queries} success remaining={n_remaining} "
    "last={last_key} updated={updated}".format(**p)
)
PY
  fi
  if [[ -f "$RUN_LOG" ]]; then
    echo "----- run.log (tail) -----"
    tail -n 20 "$RUN_LOG"
  fi
  if [[ -f "$OUTPUT_ROOT/stages/summarize.done" ]]; then
    echo "COMPLETE $OUTPUT_ROOT/stages/summarize.done"
  fi
}

run_all() {
  cd "$ROOT"
  # shellcheck disable=SC1091
  source scripts/activate_env.sh
  mkdir -p "$OUTPUT_ROOT/logs" "$OUTPUT_ROOT/stages"
  exec > >(stdbuf -oL tee -a "$RUN_LOG") 2>&1
  echo "[vexcel-h3r7-gehi] $(date -Is) start output=$OUTPUT_ROOT"

  if [[ ! -x "$GEHI_EXE" ]]; then
    echo "ERROR: GEHI executable missing: $GEHI_EXE" >&2
    exit 4
  fi

  python -u "$PYTHON_RUNNER" plan --output "$OUTPUT_ROOT"
  python -u "$PYTHON_RUNNER" preflight --output "$OUTPUT_ROOT" --gehi-exe "$GEHI_EXE"

  pass=1
  while (( pass <= MAX_PROBE_PASSES )); do
    echo "[vexcel-h3r7-gehi] $(date -Is) probe pass=$pass"
    if python -u "$PYTHON_RUNNER" probe --output "$OUTPUT_ROOT" --gehi-exe "$GEHI_EXE"; then
      break
    fi
    echo "[vexcel-h3r7-gehi] $(date -Is) probe pass=$pass incomplete; retrying"
    pass=$((pass + 1))
  done

  python -u "$PYTHON_RUNNER" summarize --output "$OUTPUT_ROOT" --publish
  echo "[vexcel-h3r7-gehi] $(date -Is) all stages complete"
}

cmd="${1:-}"
case "$cmd" in
  launch)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "ERROR: tmux session '$SESSION' already exists" >&2
      exit 3
    fi
    mkdir -p "$OUTPUT_ROOT"
    tmux new-session -d -s "$SESSION" \
      "cd '$ROOT' && SESSION='$SESSION' OUTPUT_ROOT='$OUTPUT_ROOT' bash '$0' run"
    sleep 2
    if ! tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "ERROR: tmux session died immediately; see $RUN_LOG" >&2
      tail -n 40 "$RUN_LOG" 2>/dev/null || true
      exit 5
    fi
    echo "launched session=$SESSION log=$RUN_LOG"
    ;;
  run)
    run_all
    ;;
  status)
    status
    ;;
  *)
    usage
    exit 2
    ;;
esac
