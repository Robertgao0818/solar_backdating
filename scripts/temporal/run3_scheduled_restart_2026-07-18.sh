#!/usr/bin/env bash
# One-shot restart for RUN 3 (fullscan_gemini_backdating_2026-07/run3_v2),
# scheduled via `at` for 2026-07-18T09:00:00Z — ~5min safety buffer past the
# observed gemini-3.1-flash-lite quota reset cluster (08:54:29Z-08:55:08Z
# across all 11 pooled sub2api accounts; see ISSUE-28). The run was killed
# manually at ~07:11Z after the real Google QUOTA_EXHAUSTED outage started.
#
# Idempotent: run_fullscan_backdating_v3.sh resumes from existing
# scan_states (24,436/36,322 a24 anchors already done as of the kill), no
# --force-restart. If quota genuinely hasn't reset yet, the launcher's own
# gate 4 (authenticated 1-anchor preflight) will fail fast and this script
# exits nonzero without burning the 80-worker pool.
set -uo pipefail

RUN_ROOT="$HOME/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/run3_v2"
LOG="$RUN_ROOT/restart_2026-07-18_0900z.log"
mkdir -p "$RUN_ROOT"

log() { echo "[RESTART3] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }

log "fired: at-job woke up"

now_epoch=$(date -u +%s)
safety_epoch=$(date -u -d "2026-07-18T08:55:10Z" +%s)
if [[ "$now_epoch" -lt "$safety_epoch" ]]; then
  log "ABORT: current time is before the 08:55:10Z safety floor (clock skew or mis-scheduled at-job) — refusing to launch early"
  exit 3
fi

if tmux has-session -t run3 2>/dev/null; then
  log "ABORT: tmux session 'run3' already exists — not double-launching. Investigate manually."
  exit 4
fi

log "launching: tmux new -d -s run3 ..."
tmux new -d -s run3 \
  "bash /home/gao/projects/solar_backdating/scripts/temporal/run_fullscan_backdating_v3.sh 2>&1 | tee -a $RUN_ROOT/launch.log"

sleep 30

if ! tmux has-session -t run3 2>/dev/null; then
  log "FAILED: tmux session 'run3' is not alive 30s after launch — the launcher likely exited fast (check $RUN_ROOT/launch.log tail below)"
  tail -n 40 "$RUN_ROOT/launch.log" 2>&1 | tee -a "$LOG"
  exit 5
fi

a24_count=$(grep -c '^\[RUN\]' "$RUN_ROOT/a24/run_main.log" 2>/dev/null || echo 0)
log "OK: tmux session alive 30s post-launch, a24 processed count=$a24_count (was 24436 at kill time)"
log "launch.log tail:"
tail -n 15 "$RUN_ROOT/launch.log" 2>&1 | tee -a "$LOG"

# Re-arm the a24-completion watcher now that 'run3' definitely exists (its
# wait loop is `while tmux has-session -t run3; do sleep 60; done` — starting
# it before run3 exists would fall through immediately and misfire).
if tmux has-session -t chain3 2>/dev/null; then
  log "chain3 watcher session already exists, not re-launching"
else
  echo "[CHAIN3] $(date -u +%Y-%m-%dT%H:%M:%SZ) re-armed by scheduled restart script" >> "$RUN_ROOT/chain_rerun.log"
  tmux new -d -s chain3 "bash $RUN_ROOT/chain_rerun_after_a24.sh"
  log "chain3 watcher (chain_rerun_after_a24.sh) re-armed in tmux session 'chain3'"
fi
