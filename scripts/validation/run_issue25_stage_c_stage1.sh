#!/usr/bin/env bash
# ISSUE-25 Stage C / stage-1 geometry bake-off.
#
# Runs the frozen 400-target core for A24/A48/A96, five independent reps, over
# both GE Time Machine and Wayback. Raw 96 m TIFFs and the catalog cache are
# shared; scan states, audits, and scoring provenance remain isolated by
# arm/rep/provider. Re-running is resumable: terminal states are skipped and a
# provider-level .done marker is written only after a clean process exit.
set -euo pipefail

cd /home/gaosh/projects/solar_backdating
source scripts/activate_env.sh

ROOT="${ROOT:-/home/gaosh/zasolar_data/geid_temporal/issue25_teacher_geometry_20260710}"
MANIFEST="${MANIFEST:-docs/replan_v2/issue25_manifest_20260710/target_anchors_96m.csv}"
RUN_ROOT="${RUN_ROOT:-$ROOT/stage_c}"
WORKERS="${WORKERS:-40}"
QPS="${QPS:-20}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"

STAGE1_ANCHORS="$RUN_ROOT/stage1_anchors_96m.csv"
CATALOG_CACHE="$RUN_ROOT/catalog_cache"
mkdir -p "$RUN_ROOT" "$CATALOG_CACHE"

# sample_stage is frozen column 12 in target_anchors_96m.csv.
awk -F, 'NR == 1 || $12 == "stage1_core"' "$MANIFEST" > "$STAGE1_ANCHORS"
N_STAGE1=$(( $(wc -l < "$STAGE1_ANCHORS") - 1 ))
if [[ "$N_STAGE1" -ne 400 ]]; then
  echo "ERROR: expected 400 stage1_core anchors, found $N_STAGE1" >&2
  exit 2
fi

echo "[ISSUE25] stage1 kickoff targets=$N_STAGE1 workers=$WORKERS qps=$QPS model=$MODEL"
echo "[ISSUE25] manifest_sha256=$(sha256sum "$MANIFEST" | awk '{print $1}')"
echo "[ISSUE25] started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

run_provider() {
  local arm="$1" rep="$2" provider="$3" limit="${4:-}"
  local provider_slug="${provider,,}"
  local out="$RUN_ROOT/stage1/A${arm}/rep${rep}/${provider_slug}"
  local done="$out/.done"
  local limit_tag="full"
  local limit_args=()
  if [[ -n "$limit" ]]; then
    limit_tag="first${limit}"
    limit_args=(--limit-anchors "$limit")
  elif [[ -f "$done" ]]; then
    echo "[ISSUE25] skip completed A${arm} rep${rep} ${provider}"
    return 0
  fi

  mkdir -p "$out"
  echo "[ISSUE25] begin A${arm} rep${rep} ${provider} scope=${limit_tag} utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$STAGE1_ANCHORS" \
    --scan-states-dir "$out/scan_states" \
    --chips-dir "$ROOT/full_prefetch/chips_${provider_slug}" \
    --audit-dir "$out/audit" \
    --provider "$provider" \
    --scorer gemini \
    --anchor-workers "$WORKERS" \
    --qps "$QPS" \
    --round1-model "$MODEL" \
    --round2-model "$MODEL" \
    --routing-salt-mode target \
    --no-verdict-store \
    --review-extent-m "$arm" \
    --catalog-cache-dir "$CATALOG_CACHE" \
    "${limit_args[@]}" \
    2>&1 | tee -a "$out/run.log"

  if [[ -z "$limit" ]]; then
    date -u +%Y-%m-%dT%H:%M:%SZ > "$done"
  fi
  echo "[ISSUE25] end A${arm} rep${rep} ${provider} scope=${limit_tag} utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# >10 GiB projection guard: finish the first 100 A24/TM targets, then record
# actual shared-tree growth before releasing the remaining stage-1 matrix.
if [[ ! -f "$RUN_ROOT/.first100_storage_checked" ]]; then
  BEFORE_BYTES=$(du -sb "$ROOT/full_prefetch" | awk '{print $1}')
  run_provider 24 1 TM 100
  AFTER_BYTES=$(du -sb "$ROOT/full_prefetch" | awk '{print $1}')
  {
    echo "before_bytes=$BEFORE_BYTES"
    echo "after_bytes=$AFTER_BYTES"
    echo "growth_bytes=$((AFTER_BYTES - BEFORE_BYTES))"
    echo "checked_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$RUN_ROOT/first100_storage_check.txt"
  touch "$RUN_ROOT/.first100_storage_checked"
  echo "[ISSUE25] first100 storage growth=$((AFTER_BYTES - BEFORE_BYTES)) bytes"
fi

for arm in 24 48 96; do
  for rep in 1 2 3 4 5; do
    run_provider "$arm" "$rep" TM
    run_provider "$arm" "$rep" Wayback
  done
done

date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/stage1/.complete"
echo "[ISSUE25] stage1 complete utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
