#!/usr/bin/env bash
# ISSUE-25 Stage C / stage-2 precision extension for the Stage-1 winner.
set -euo pipefail

cd /home/gaosh/projects/solar_backdating
source scripts/activate_env.sh

ROOT="${ROOT:-/home/gaosh/zasolar_data/geid_temporal/issue25_teacher_geometry_20260710}"
RUN_ROOT="${RUN_ROOT:-$ROOT/stage_c}"
MANIFEST="${MANIFEST:-docs/replan_v2/issue25_manifest_20260710/target_anchors_96m.csv}"
CHIP_TARGETS="${CHIP_TARGETS:-/home/gaosh/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_targets.csv}"
ANALYSIS="${ANALYSIS:-$RUN_ROOT/stage1_analysis.json}"
WORKERS="${WORKERS:-40}"
QPS="${QPS:-20}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
CATALOG_CACHE="$RUN_ROOT/catalog_cache"
STAGE2_ANCHORS="$RUN_ROOT/stage2_anchors_96m.csv"

test -f "$RUN_ROOT/stage1/.complete" || { echo "ERROR: Stage-1 is not complete" >&2; exit 2; }
test -f "$ANALYSIS" || { echo "ERROR: missing Stage-1 analysis: $ANALYSIS" >&2; exit 2; }
test -f "$RUN_ROOT/authenticated_preflight_merged_bbox/.ok" || { echo "ERROR: authenticated kickoff preflight is not green" >&2; exit 2; }

WINNER=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["stage2_winner"]["arm"])' "$ANALYSIS")
case "$WINNER" in
  A24|A48|A96) ;;
  *) echo "ERROR: invalid Stage-1 winner: $WINNER" >&2; exit 2 ;;
esac
ARM_M="${WINNER#A}"

python -u scripts/validation/issue25_stage_c.py prepare-stage2 \
  --manifest "$MANIFEST" \
  --chip-targets "$CHIP_TARGETS" \
  --output "$STAGE2_ANCHORS" \
  --expected-count 800

curl -fsS --max-time 10 http://localhost:8080/health >/dev/null
read -r ACTIVE_ACCOUNTS ACTIVE_SLOTS < <(
  docker exec sub2api-postgres sh -lc \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF " " -c "select count(*),coalesce(sum(concurrency),0) from accounts where deleted_at is null and platform='"'"'antigravity'"'"' and status='"'"'active'"'"' and schedulable=true"'
)
if [[ "$ACTIVE_SLOTS" -lt "$WORKERS" ]]; then
  echo "ERROR: workers=$WORKERS exceeds active gateway slots=$ACTIVE_SLOTS" >&2
  exit 3
fi
echo "[ISSUE25] stage2 winner=$WINNER targets=800 accounts=$ACTIVE_ACCOUNTS slots=$ACTIVE_SLOTS"

for rep in 1 2 3 4 5; do
  OUT="$RUN_ROOT/stage2/$WINNER/rep$rep"
  if [[ -f "$OUT/.done" ]]; then
    echo "[ISSUE25] skip completed stage2 $WINNER rep$rep"
    continue
  fi
  mkdir -p "$OUT"
  echo "[ISSUE25] begin stage2 $WINNER rep$rep utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$STAGE2_ANCHORS" \
    --scan-states-dir "$OUT/scan_states" \
    --chips-dir "$RUN_ROOT/unused_merged_chips" \
    --merged-tm-chips-dir "$ROOT/full_prefetch/chips_tm" \
    --merged-wayback-chips-dir "$ROOT/full_prefetch/chips_wayback" \
    --audit-dir "$OUT/audit" \
    --provider Merged \
    --scorer gemini \
    --anchor-workers "$WORKERS" \
    --qps "$QPS" \
    --round1-model "$MODEL" \
    --round2-model "$MODEL" \
    --routing-salt-mode target \
    --no-verdict-store \
    --review-extent-m "$ARM_M" \
    --catalog-cache-dir "$CATALOG_CACHE" \
    2>&1 | tee -a "$OUT/run.log"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/.done"
done

python -u scripts/validation/issue25_stage_c.py validate-matrix \
  --matrix-root "$RUN_ROOT/stage2/$WINNER" \
  --anchors "$STAGE2_ANCHORS" \
  --reps 5 \
  --model "$MODEL"
mkdir -p "$RUN_ROOT/stage2"
date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/stage2/.complete"
echo "[ISSUE25] stage2 complete winner=$WINNER utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
