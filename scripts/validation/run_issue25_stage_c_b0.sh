#!/usr/bin/env bash
# ISSUE-25 Stage C / B0 model bridge: new model on exact banked96 frame sets.
set -euo pipefail

cd /home/gao/projects/solar_backdating
source scripts/activate_env.sh

ROOT="${ROOT:-/home/gao/zasolar_data/geid_temporal/issue25_teacher_geometry_20260710}"
RUN_ROOT="${RUN_ROOT:-$ROOT/stage_c}"
BANK="${BANK:-/home/gao/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704}"
SAMPLE="${SAMPLE:-docs/replan_v2/issue25_manifest_20260710/sample_manifest.csv}"
GROUP_ANCHORS="${GROUP_ANCHORS:-/home/gao/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_groups_as_anchors.csv}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
WORKERS="${WORKERS:-40}"
QPS="${QPS:-20}"
B0_GROUPS="$RUN_ROOT/b0_group_anchors.csv"

test -f "$RUN_ROOT/authenticated_preflight_merged_bbox/.ok" || { echo "ERROR: authenticated kickoff preflight is not green" >&2; exit 2; }
python -u scripts/validation/issue25_stage_c.py prepare-b0 \
  --sample-manifest "$SAMPLE" \
  --group-anchors "$GROUP_ANCHORS" \
  --output "$B0_GROUPS" \
  --expected-targets 150

curl -fsS --max-time 10 http://localhost:8080/health >/dev/null
read -r ACTIVE_ACCOUNTS ACTIVE_SLOTS < <(
  docker exec sub2api-postgres sh -lc \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF " " -c "select count(*),coalesce(sum(concurrency),0) from accounts where deleted_at is null and platform='"'"'antigravity'"'"' and status='"'"'active'"'"' and schedulable=true"'
)
if [[ "$ACTIVE_SLOTS" -lt "$WORKERS" ]]; then
  echo "ERROR: workers=$WORKERS exceeds active gateway slots=$ACTIVE_SLOTS" >&2
  exit 3
fi
echo "[ISSUE25] B0 kickoff accounts=$ACTIVE_ACCOUNTS slots=$ACTIVE_SLOTS model=$MODEL"

for rep in 1 2 3 4 5; do
  OUT="$RUN_ROOT/b0/rep$rep"
  if [[ -f "$OUT/.done" ]]; then
    echo "[ISSUE25] skip completed B0 rep$rep"
    continue
  fi
  mkdir -p "$OUT"
  echo "[ISSUE25] begin B0 rep$rep utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python -u scripts/validation/run_issue25_b0_bridge.py \
    --groups-csv "$B0_GROUPS" \
    --source-states-dir "$BANK/rep$rep/L0/scan_states" \
    --output-dir "$OUT" \
    --model "$MODEL" \
    --workers "$WORKERS" \
    --qps "$QPS" \
    2>&1 | tee -a "$OUT/run.log"
  python -u scripts/validation/issue25_stage_c.py validate-preflight \
    --root "$OUT" \
    --model "$MODEL"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/.done"
done

python -u scripts/validation/issue25_stage_c.py validate-matrix \
  --matrix-root "$RUN_ROOT/b0" \
  --anchors "$B0_GROUPS" \
  --reps 5 \
  --model "$MODEL"
mkdir -p "$RUN_ROOT/b0"
date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/b0/.complete"
echo "[ISSUE25] B0 complete utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
