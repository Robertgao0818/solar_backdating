#!/usr/bin/env bash
# ISSUE-25 Stage C / stage-2 precision extension for the confirmed winner policy.
#
# After amendment 2026-07-11 the confirmed winner is the single-threshold area
# route routed_A24_A48_cut40 (A24 if source_area_m2 < 40 else A48). Launch is
# gated on stage2_winner_decision.json + .stage2_winner_confirmed.
set -euo pipefail

cd /home/gaosh/projects/solar_backdating
source scripts/activate_env.sh

ROOT="${ROOT:-/home/gaosh/zasolar_data/geid_temporal/issue25_teacher_geometry_20260710}"
RUN_ROOT="${RUN_ROOT:-$ROOT/stage_c}"
MANIFEST="${MANIFEST:-docs/replan_v2/issue25_manifest_20260710/target_anchors_96m.csv}"
CHIP_TARGETS="${CHIP_TARGETS:-/home/gaosh/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_targets.csv}"
ANALYSIS="${ANALYSIS:-$RUN_ROOT/stage1_analysis.json}"
DECISION="${DECISION:-$RUN_ROOT/stage2_winner_decision.json}"
SENTINEL="${SENTINEL:-$RUN_ROOT/.stage2_winner_confirmed}"
WORKERS="${WORKERS:-40}"
QPS="${QPS:-20}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
CATALOG_CACHE="$RUN_ROOT/catalog_cache"
STAGE2_ANCHORS="$RUN_ROOT/stage2_anchors_96m.csv"
STAGE2_SPLIT_DIR="$RUN_ROOT/stage2_arm_splits"

test -f "$RUN_ROOT/stage1/.complete" || { echo "ERROR: Stage-1 is not complete" >&2; exit 2; }
test -f "$ANALYSIS" || { echo "ERROR: missing Stage-1 analysis: $ANALYSIS" >&2; exit 2; }
test -f "$RUN_ROOT/authenticated_preflight_merged_bbox/.ok" || { echo "ERROR: authenticated kickoff preflight is not green" >&2; exit 2; }
test -f "$DECISION" || { echo "ERROR: missing Stage-2 winner decision: $DECISION" >&2; exit 2; }
test -f "$SENTINEL" || { echo "ERROR: Stage-2 winner not human-confirmed ($SENTINEL)" >&2; exit 2; }

WINNER=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["arm"])' "$DECISION")
MODE=$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("mode",""))' "$DECISION")
if [[ "$WINNER" != "routed_A24_A48_cut40" || "$MODE" != "routed" ]]; then
  echo "ERROR: Stage-2 requires confirmed routed_A24_A48_cut40; got winner=$WINNER mode=$MODE" >&2
  exit 2
fi

python -u scripts/validation/issue25_stage_c.py prepare-stage2 \
  --manifest "$MANIFEST" \
  --chip-targets "$CHIP_TARGETS" \
  --output "$STAGE2_ANCHORS" \
  --expected-count 800

python -u scripts/validation/issue25_stage_c.py split-stage2-by-arm \
  --anchors "$STAGE2_ANCHORS" \
  --output-dir "$STAGE2_SPLIT_DIR"

curl -fsS --max-time 10 http://localhost:8080/health >/dev/null
read -r ACTIVE_ACCOUNTS ACTIVE_SLOTS < <(
  docker exec sub2api-postgres sh -lc \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF " " -c "select count(*),coalesce(sum(concurrency),0) from accounts where deleted_at is null and platform='"'"'antigravity'"'"' and status='"'"'active'"'"' and schedulable=true"'
)
if [[ "$ACTIVE_SLOTS" -lt "$WORKERS" ]]; then
  echo "ERROR: workers=$WORKERS exceeds active gateway slots=$ACTIVE_SLOTS" >&2
  exit 3
fi
echo "[ISSUE25] stage2 winner=$WINNER mode=$MODE targets=800 accounts=$ACTIVE_ACCOUNTS slots=$ACTIVE_SLOTS"

run_arm_rep() {
  local arm="$1" rep="$2" extent="${1#A}"
  local out="$RUN_ROOT/stage2/$WINNER/rep$rep"
  local arm_anchors="$STAGE2_SPLIT_DIR/anchors_${arm}.csv"
  local arm_done="$out/.done_${arm}"
  if [[ -f "$arm_done" ]]; then
    echo "[ISSUE25] skip completed stage2 $WINNER $arm rep$rep"
    return 0
  fi
  mkdir -p "$out/scan_states" "$out/audit"
  echo "[ISSUE25] begin stage2 $WINNER $arm rep$rep extent=${extent}m utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$arm_anchors" \
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
    --review-extent-m "$extent" \
    --catalog-cache-dir "$CATALOG_CACHE" \
    2>&1 | tee -a "$out/run_${arm}.log"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$arm_done"
}

for rep in 1 2 3 4 5; do
  OUT="$RUN_ROOT/stage2/$WINNER/rep$rep"
  if [[ -f "$OUT/.done" ]]; then
    echo "[ISSUE25] skip completed stage2 $WINNER rep$rep"
    continue
  fi
  # Per-target route: run A24 and A48 subsets into the same rep scan_states tree.
  # Never cross-size group; each target keeps its own review extent.
  for arm in A24 A48; do
    run_arm_rep "$arm" "$rep"
  done
  date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/.done"
  echo "[ISSUE25] end stage2 $WINNER rep$rep utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
done

python -u scripts/validation/issue25_stage_c.py validate-matrix \
  --matrix-root "$RUN_ROOT/stage2/$WINNER" \
  --anchors "$STAGE2_ANCHORS" \
  --reps 5 \
  --model "$MODEL"
mkdir -p "$RUN_ROOT/stage2"
date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/stage2/.complete"
echo "[ISSUE25] stage2 complete winner=$WINNER utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
