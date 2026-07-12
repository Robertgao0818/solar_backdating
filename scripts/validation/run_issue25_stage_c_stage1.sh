#!/usr/bin/env bash
# ISSUE-25 Stage C / stage-1 geometry bake-off.
#
# Runs the frozen 400-target core for A24/A48/A96, five independent reps. Each
# rep uses one production-style adaptive sequence merged from GE Time Machine
# and Wayback. Raw 96 m TIFFs and the catalog cache are shared; scan states,
# audits, and scoring provenance remain isolated by arm/rep. Re-running is
# resumable: terminal states are skipped and a .done marker is written only
# after a clean process exit.
set -euo pipefail

cd /home/gao/projects/solar_backdating
source scripts/activate_env.sh

ROOT="${ROOT:-/home/gao/zasolar_data/geid_temporal/issue25_teacher_geometry_20260710}"
MANIFEST="${MANIFEST:-docs/replan_v2/issue25_manifest_20260710/target_anchors_96m.csv}"
CHIP_TARGETS="${CHIP_TARGETS:-/home/gao/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_targets.csv}"
RUN_ROOT="${RUN_ROOT:-$ROOT/stage_c}"
WORKERS="${WORKERS:-40}"
QPS="${QPS:-20}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"

STAGE1_ANCHORS="$RUN_ROOT/stage1_anchors_96m.csv"
CATALOG_CACHE="$RUN_ROOT/catalog_cache"
mkdir -p "$RUN_ROOT" "$CATALOG_CACHE"

python -u scripts/validation/issue25_stage_c.py prepare-stage1 \
  --manifest "$MANIFEST" \
  --chip-targets "$CHIP_TARGETS" \
  --output "$STAGE1_ANCHORS" \
  --expected-count 400
N_STAGE1=400

if ! curl -fsS --max-time 10 http://localhost:8080/health >/dev/null; then
  echo "ERROR: sub2api gateway health preflight failed" >&2
  exit 3
fi
read -r ACTIVE_ACCOUNTS ACTIVE_SLOTS < <(
  docker exec sub2api-postgres sh -lc \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF " " -c "select count(*),coalesce(sum(concurrency),0) from accounts where deleted_at is null and platform='"'"'antigravity'"'"' and status='"'"'active'"'"' and schedulable=true"'
)
if [[ "$ACTIVE_SLOTS" -lt "$WORKERS" ]]; then
  echo "ERROR: workers=$WORKERS exceeds active gateway slots=$ACTIVE_SLOTS" >&2
  exit 3
fi

echo "[ISSUE25] stage1 kickoff targets=$N_STAGE1 workers=$WORKERS qps=$QPS model=$MODEL"
echo "[ISSUE25] manifest_sha256=$(sha256sum "$MANIFEST" | awk '{print $1}')"
echo "[ISSUE25] chip_targets_sha256=$(sha256sum "$CHIP_TARGETS" | awk '{print $1}')"
echo "[ISSUE25] started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[ISSUE25] gateway_accounts=$ACTIVE_ACCOUNTS gateway_slots=$ACTIVE_SLOTS"

PREFLIGHT="$RUN_ROOT/authenticated_preflight_merged_bbox"
if [[ ! -f "$PREFLIGHT/.ok" ]]; then
  rm -rf "$PREFLIGHT/audit" "$PREFLIGHT/scan_states"
  rm -f "$PREFLIGHT/scoring_provenance.jsonl" "$PREFLIGHT/chip_provenance.jsonl"
  mkdir -p "$PREFLIGHT"
  echo "[ISSUE25] authenticated merged+bbox preflight begin"
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$STAGE1_ANCHORS" \
    --limit-anchors 1 \
    --force-restart \
    --scan-states-dir "$PREFLIGHT/scan_states" \
    --chips-dir "$PREFLIGHT/unused_merged_chips" \
    --merged-tm-chips-dir "$PREFLIGHT/chips_tm" \
    --merged-wayback-chips-dir "$PREFLIGHT/chips_wayback" \
    --audit-dir "$PREFLIGHT/audit" \
    --provider Merged \
    --scorer gemini \
    --anchor-workers 1 \
    --qps 1 \
    --round1-model "$MODEL" \
    --round2-model "$MODEL" \
    --routing-salt-mode target \
    --no-verdict-store \
    --review-extent-m 24 \
    --catalog-cache-dir "$CATALOG_CACHE" \
    2>&1 | tee "$PREFLIGHT/run.log"
  python -u scripts/validation/issue25_stage_c.py validate-preflight \
    --root "$PREFLIGHT" \
    --model "$MODEL"
  touch "$PREFLIGHT/.ok"
fi

run_rep() {
  local arm="$1" rep="$2" limit="${3:-}"
  local out="$RUN_ROOT/stage1/A${arm}/rep${rep}"
  local done="$out/.done"
  local limit_tag="full"
  local limit_args=()
  if [[ -n "$limit" ]]; then
    limit_tag="first${limit}"
    limit_args=(--limit-anchors "$limit")
  elif [[ -f "$done" ]]; then
    echo "[ISSUE25] skip completed A${arm} rep${rep}"
    return 0
  fi

  mkdir -p "$out"
  echo "[ISSUE25] begin A${arm} rep${rep} merged scope=${limit_tag} utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$STAGE1_ANCHORS" \
    --scan-states-dir "$out/scan_states" \
    --chips-dir "$RUN_ROOT/unused_merged_chips" \
    --merged-tm-chips-dir "$ROOT/full_prefetch/chips_tm" \
    --merged-wayback-chips-dir "$ROOT/full_prefetch/chips_wayback" \
    --audit-dir "$out/audit" \
    --provider Merged \
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
  echo "[ISSUE25] end A${arm} rep${rep} merged scope=${limit_tag} utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# >10 GiB projection guard: finish the first 100 A24 merged-sequence targets, then record
# actual shared-tree growth before releasing the remaining stage-1 matrix.
if [[ ! -f "$RUN_ROOT/.first100_storage_checked" ]]; then
  BEFORE_BYTES=$(du -sb "$ROOT/full_prefetch" | awk '{print $1}')
  BEFORE_RAW_BYTES=$(find "$ROOT/full_prefetch" -type f -name '*.tif' -printf '%s\n' | awk '{s += $1} END {print s + 0}')
  BEFORE_RENDER_BYTES=$(find "$ROOT/full_prefetch" -type f -name '*.png' -printf '%s\n' | awk '{s += $1} END {print s + 0}')
  run_rep 24 1 100
  AFTER_BYTES=$(du -sb "$ROOT/full_prefetch" | awk '{print $1}')
  AFTER_RAW_BYTES=$(find "$ROOT/full_prefetch" -type f -name '*.tif' -printf '%s\n' | awk '{s += $1} END {print s + 0}')
  AFTER_RENDER_BYTES=$(find "$ROOT/full_prefetch" -type f -name '*.png' -printf '%s\n' | awk '{s += $1} END {print s + 0}')
  RAW_GROWTH_BYTES=$((AFTER_RAW_BYTES - BEFORE_RAW_BYTES))
  RENDER_GROWTH_BYTES=$((AFTER_RENDER_BYTES - BEFORE_RENDER_BYTES))
  PROJECTED_BYTES=$((BEFORE_BYTES + RAW_GROWTH_BYTES * 12 + RENDER_GROWTH_BYTES * 20))
  RESERVE_BYTES=$((14 * 1024 * 1024 * 1024))
  FREE_BYTES=$(df -B1 --output=avail "$ROOT" | tail -1 | tr -d ' ')
  {
    echo "before_bytes=$BEFORE_BYTES"
    echo "after_bytes=$AFTER_BYTES"
    echo "raw_growth_bytes=$RAW_GROWTH_BYTES"
    echo "render_growth_bytes=$RENDER_GROWTH_BYTES"
    echo "raw_scale=12"
    echo "render_scale=20"
    echo "conservative_projected_bytes=$PROJECTED_BYTES"
    echo "reserve_bytes=$RESERVE_BYTES"
    echo "free_bytes=$FREE_BYTES"
    echo "checked_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$RUN_ROOT/first100_storage_check.txt"
  if [[ "$PROJECTED_BYTES" -gt "$RESERVE_BYTES" ]]; then
    echo "ERROR: first100 projection exceeds the frozen 14 GiB reserve" >&2
    exit 4
  fi
  if [[ "$FREE_BYTES" -lt "$RESERVE_BYTES" ]]; then
    echo "ERROR: less than 14 GiB free after first100" >&2
    exit 4
  fi
  touch "$RUN_ROOT/.first100_storage_checked"
  echo "[ISSUE25] first100 raw_growth=$RAW_GROWTH_BYTES render_growth=$RENDER_GROWTH_BYTES projected=$PROJECTED_BYTES bytes"
fi

for arm in 24 48 96; do
  for rep in 1 2 3 4 5; do
    run_rep "$arm" "$rep"
  done
done

date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/stage1/.complete"
echo "[ISSUE25] stage1 complete utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
