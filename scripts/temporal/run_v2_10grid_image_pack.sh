#!/usr/bin/env bash
# Resumable ten-grid image pack. `launch` starts a detached tmux session.
# `run` is what that session executes. Re-running resumes finished files.
set -euo pipefail

SESSION=v2_10grid_image_pack
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PACK="${PACK:-$HOME/zasolar_data/geid_temporal/reference_fulltimeline_v2_10grid_20261005}"

cmd_run() {
  # shellcheck disable=SC1091
  source "$ROOT/scripts/activate_env.sh"
  cd "$ROOT"
  mkdir -p "$PACK/metadata"
  python -u scripts/temporal/build_v2_10grid_image_pack.py prepare \
    >>"$PACK/prepare.log" 2>&1
  python -u scripts/temporal/build_v2_10grid_image_pack.py aerial --workers 4 \
    >>"$PACK/aerial.log" 2>&1 &
  aerial_pid=$!
  python -u scripts/temporal/build_v2_10grid_image_pack.py satellite \
    --request-interval 1.0 --parallel 4 \
    >>"$PACK/satellite.log" 2>&1 &
  satellite_pid=$!
  aerial_exit=0
  satellite_exit=0
  wait "$aerial_pid" || aerial_exit=$?
  wait "$satellite_pid" || satellite_exit=$?
  printf 'aerial_exit=%s satellite_exit=%s\n' "$aerial_exit" "$satellite_exit" \
    | tee -a "$PACK/run.log"
  if [[ "$aerial_exit" -ne 0 || "$satellite_exit" -ne 0 ]]; then
    exit 1
  fi
}

cmd_launch() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "refusing duplicate tmux session $SESSION" >&2
    exit 1
  fi
  tmux new -d -s "$SESSION" "bash '$ROOT/scripts/temporal/run_v2_10grid_image_pack.sh' run"
  echo "started $SESSION"
}

cmd_status() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "session $SESSION is live"
  else
    echo "session $SESSION is not live"
  fi
  echo "--- aerial ---"
  tail -n 5 "$PACK/aerial.log" 2>/dev/null || true
  echo "--- satellite ---"
  tail -n 8 "$PACK/satellite.log" 2>/dev/null || true
}

usage() {
  echo "usage: $0 launch|run|status" >&2
  exit 2
}

case "${1:-}" in
  launch) cmd_launch ;;
  run) cmd_run ;;
  status) cmd_status ;;
  *) usage ;;
esac
