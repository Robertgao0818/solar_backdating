#!/usr/bin/env bash
# CoJ cohort audit (ISSUE-09) full-launch chain: build -> fetch -> score ->
# join -> gates -> report, unbuffered logs, tee'd to a timestamped log file.
#
# Run inside tmux (design §8 runbook) so an SSH drop never kills the ~14h
# fetch stage, and you can reattach to watch progress:
#
#   tmux new -s coj_cohort 'bash scripts/audit/coj_audit_cohort_launch.sh; bash'
#   tmux attach -t coj_cohort
#
# Env overrides (all optional):
#   WORKERS       fetch concurrency (default 5, see .claude/rules/05-runpod-inference.md
#                 concurrency guidance -- this script is local/RTX 4070, not RunPod)
#   OUTPUT_ROOT   cohort output root (default matches coj_audit_cohort.py's own default)
#   SEED          seed for the cohort build + falsification subsample (default 20260704)
#   EXTRA_FLAGS   appended verbatim to every stage invocation (e.g. "--smoke 30"
#                 for a smoke run, or "--skip-classifier")
#
# Example smoke run:
#   EXTRA_FLAGS="--smoke 30" WORKERS=3 bash scripts/audit/coj_audit_cohort_launch.sh
#
# Each stage is idempotent (reads its predecessor's file from --output-root),
# so re-running this script after a partial/interrupted run resumes rather
# than redoing completed work (fetch/score are explicitly resumable; build/
# join/gates/report simply regenerate their outputs from what exists).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# tmux spawns a fresh shell with no venv on PATH — activate the shared env
# (idempotent if already active).
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  # shellcheck disable=SC1091
  source scripts/activate_env.sh
fi

WORKERS="${WORKERS:-5}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$HOME/zasolar_data/geid_temporal/coj_audit_cohort_20260704}"
SEED="${SEED:-20260704}"
EXTRA_FLAGS="${EXTRA_FLAGS:-}"

mkdir -p "$OUTPUT_ROOT"
LOG_FILE="$OUTPUT_ROOT/launch_$(date +%Y%m%d_%H%M%S).log"

run_stage() {
  local stage="$1"
  shift
  echo "=== [$(date -Is)] stage: $stage ===" | tee -a "$LOG_FILE"
  # shellcheck disable=SC2086
  stdbuf -oL python -u -m scripts.audit.coj_audit_cohort "$stage" \
    --output-root "$OUTPUT_ROOT" --seed "$SEED" "$@" $EXTRA_FLAGS 2>&1 | tee -a "$LOG_FILE"
}

echo "CoJ cohort audit launch -> $OUTPUT_ROOT (log: $LOG_FILE)" | tee -a "$LOG_FILE"

run_stage build
run_stage fetch --workers "$WORKERS"
run_stage score
run_stage join
run_stage gates
run_stage report

echo "=== [$(date -Is)] all stages complete ===" | tee -a "$LOG_FILE"
