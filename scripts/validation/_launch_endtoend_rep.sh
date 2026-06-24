#!/usr/bin/env bash
# tmux entry wrapper: run one end-to-end replica with timestamped file logging + exit marker.
# Usage (inside tmux): _launch_endtoend_rep.sh <rep_index>
REP="${1:?rep index}"
cd /home/gaosh/projects/solar_backdating
ROOT=/home/gaosh/zasolar_data/geid_temporal/llm_endtoend_20260623
LOG="$ROOT/rep${REP}.log"
: > "$LOG"
ANCHOR_WORKERS="${ANCHOR_WORKERS:-30}" QPS="${QPS:-8}" \
  stdbuf -oL -eL bash scripts/validation/run_endtoend_rep.sh "$REP" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
echo "EXITCODE=$rc  $(date -u +%FT%TZ)" | tee -a "$LOG"
