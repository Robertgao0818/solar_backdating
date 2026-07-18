#!/usr/bin/env bash
# Full-population Gemini backdating scan, RUN 3 (clean full rescan of all
# 41,393 anchors consuming the ISSUE-27 anchors_v2 package; RUN 2 output at
# $FULLSCAN_ROOT is preserved untouched as provenance).
#
# Differences from run_fullscan_backdating_v2.sh (RUN 2):
#   - Anchors come from the prebuilt anchors_v2 package ($ANCHORS_DIR):
#     offsets recomputed, source_grids recomputed, geometry_version
#     fullscan_target96_review{24,48}_v2. Gate 2 validates the
#     anchors_v2_issue27 summary schema (invariants.*) and the owner-approved
#     preflight_qa_30/.approved marker.
#   - Run root is $RUN_ROOT (default .../fullscan_gemini_backdating_2026-07/run3_v2),
#     never the RUN 2 root. Hard guard below refuses to write into RUN 2.
#   - Offline TM catalog defaults to gehi_vintage_candidates_full_run3.csv
#     (= RUN 2 catalog + census_v2 top-up rows for the 186 census-shifted
#     anchors; original catalog stays byte-identical).
# Everything else (strict no-live-GEHI, sequential a24->a48 lanes,
# owner-frozen pace WORKERS_TOTAL=80 / QPS_TOTAL=8, resumability, tmux) is
# unchanged from RUN 2.
set -euo pipefail

cd /home/gao/projects/solar_backdating
source scripts/activate_env.sh

DL_ROOT="${DL_ROOT:-$HOME/zasolar_data/geid_temporal/basemap_rebuild_2026-07-13}"
FULLSCAN_ROOT="$HOME/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
RUN_ROOT="${RUN_ROOT:-$FULLSCAN_ROOT/run3_v2}"
ANCHORS_DIR="${ANCHORS_DIR:-$FULLSCAN_ROOT/anchors_v2}"
CHIPS_DIR="$DL_ROOT/chips"
VEXCEL_CSV="${VEXCEL_CSV:-${ZASOLAR_ROOT:-/home/gao/projects/ZAsolar}/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
WORKERS_TOTAL="${WORKERS_TOTAL:-80}"
QPS_TOTAL="${QPS_TOTAL:-8}"
OFFLINE_TM_CSV="${OFFLINE_TM_CSV:-$DL_ROOT/gehi_vintage_candidates_full_run3.csv}"
EXPECTED_ANCHORS="${EXPECTED_ANCHORS:-41393}"
RESERVE_GIB="${RESERVE_GIB:-20}"

# --- gate 0: never write into the RUN 2 root (provenance is immutable) ------
if [[ "$RUN_ROOT" == "$FULLSCAN_ROOT" ]]; then
  echo "ERROR: RUN_ROOT must be a versioned subdir (e.g. $FULLSCAN_ROOT/run3_v2), not the RUN 2 root" >&2
  exit 3
fi
mkdir -p "$RUN_ROOT"

# --- gate 1: inputs ----------------------------------------------------------
if [[ ! -f "$DL_ROOT/.download_complete" ]]; then
  echo "ERROR: $DL_ROOT/.download_complete missing - basemap download not verified complete" >&2
  exit 3
fi
[[ -f "$VEXCEL_CSV" ]] || { echo "ERROR: vexcel CSV missing: $VEXCEL_CSV" >&2; exit 3; }
[[ -d "$CHIPS_DIR" ]] || { echo "ERROR: chips dir missing: $CHIPS_DIR" >&2; exit 3; }
[[ -f "$OFFLINE_TM_CSV" ]] || { echo "ERROR: offline catalog CSV missing: $OFFLINE_TM_CSV" >&2; exit 3; }

# --- gate 2: prebuilt anchors_v2 package (ISSUE-27) --------------------------
for f in anchors_summary.json anchors_A24.csv anchors_A48.csv; do
  [[ -f "$ANCHORS_DIR/$f" ]] || { echo "ERROR: anchors_v2 package incomplete: $ANCHORS_DIR/$f missing" >&2; exit 3; }
done
if [[ ! -f "$ANCHORS_DIR/preflight_qa_30/.approved" ]]; then
  echo "ERROR: anchors_v2 preflight_qa_30/.approved missing - owner approval required" >&2
  exit 3
fi
python - "$ANCHORS_DIR/anchors_summary.json" "$EXPECTED_ANCHORS" <<'EOF'
import json, sys
s = json.load(open(sys.argv[1]))
expected = int(sys.argv[2])
assert s.get("schema_version") == "anchors_v2_issue27", f"bad schema_version: {s.get('schema_version')}"
inv = s["invariants"]
assert inv["passed"] is True, "anchors_v2 invariants not passed"
assert inv["row_count"] == expected, f"row_count {inv['row_count']} != {expected}"
assert inv["unique_anchor_ids"] == expected, f"unique_anchor_ids {inv['unique_anchor_ids']} != {expected}"
gv = s["geometry_versions"]
assert gv == {"A24": "fullscan_target96_review24_v2", "A48": "fullscan_target96_review48_v2"}, f"bad geometry_versions: {gv}"
print(f"[FULLSCAN3] anchors_v2 ok: n={inv['row_count']} arm_counts={inv['arm_counts']} geometry={gv}")
EOF

# --- gate 3: gateway health + slots -------------------------------------------
if ! curl -fsS --max-time 10 http://localhost:8080/health >/dev/null; then
  echo "ERROR: sub2api gateway health preflight failed" >&2
  exit 3
fi
read -r ACTIVE_ACCOUNTS ACTIVE_SLOTS < <(
  docker exec sub2api-postgres sh -lc \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF " " -c "select count(*),coalesce(sum(concurrency),0) from accounts where deleted_at is null and platform='"'"'antigravity'"'"' and status='"'"'active'"'"' and schedulable=true"'
)
if [[ "$ACTIVE_SLOTS" -lt "$WORKERS_TOTAL" ]]; then
  echo "ERROR: need $WORKERS_TOTAL gateway slots, active=$ACTIVE_SLOTS" >&2
  exit 3
fi
echo "[FULLSCAN3] gateway_accounts=$ACTIVE_ACCOUNTS gateway_slots=$ACTIVE_SLOTS"

run_lane() {
  local lane="$1" anchors="$2" extent="$3" limit="${4:-}" tag="${5:-main}"
  local out="$RUN_ROOT/$lane"
  local limit_args=()
  [[ -n "$limit" ]] && limit_args=(--limit-anchors "$limit")
  mkdir -p "$out"
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$anchors" \
    --scan-states-dir "$out/scan_states" \
    --chips-dir "$out/unused_merged_chips" \
    --merged-tm-chips-dir "$CHIPS_DIR" \
    --merged-wayback-chips-dir "$CHIPS_DIR" \
    --audit-dir "$out/audit" \
    --provider Merged \
    --scorer gemini \
    --anchor-workers "$WORKERS_TOTAL" \
    --qps "$QPS_TOTAL" \
    --round1-model "$MODEL" \
    --round2-model "$MODEL" \
    --routing-salt-mode target \
    --no-verdict-store \
    --review-extent-m "$extent" \
    --vexcel-capture-csv "$VEXCEL_CSV" \
    --offline-tm-catalog-csv "$OFFLINE_TM_CSV" \
    --offline-wayback \
    --no-live-gehi \
    --offline-require-chip-on-disk \
    --catalog-cache-dir "$out/catalog_cache" \
    "${limit_args[@]}" \
    2>&1 | tee -a "$out/run_${tag}.log"
}

# --- gate 4: authenticated 1-anchor preflight + chip cache-hit assertion ----
PREFLIGHT="$RUN_ROOT/preflight"
if [[ ! -f "$PREFLIGHT/.ok" ]]; then
  rm -rf "$PREFLIGHT"
  mkdir -p "$PREFLIGHT"
  echo "[FULLSCAN3] authenticated preflight begin utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$ANCHORS_DIR/anchors_A24.csv" \
    --limit-anchors 1 \
    --force-restart \
    --scan-states-dir "$PREFLIGHT/scan_states" \
    --chips-dir "$PREFLIGHT/unused_merged_chips" \
    --merged-tm-chips-dir "$CHIPS_DIR" \
    --merged-wayback-chips-dir "$CHIPS_DIR" \
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
    --vexcel-capture-csv "$VEXCEL_CSV" \
    --offline-tm-catalog-csv "$OFFLINE_TM_CSV" \
    --offline-wayback \
    --no-live-gehi \
    --offline-require-chip-on-disk \
    --catalog-cache-dir "$PREFLIGHT/catalog_cache" \
    2>&1 | tee "$PREFLIGHT/run.log"
  python -u scripts/validation/issue25_stage_c.py validate-preflight \
    --root "$PREFLIGHT" \
    --model "$MODEL"
  python - "$PREFLIGHT/chip_provenance.jsonl" <<'EOF'
import collections, json, sys
from pathlib import Path
rows = [json.loads(l) for l in Path(sys.argv[1]).read_text().splitlines() if l.strip()]
counts = collections.Counter(r.get("status") for r in rows)
print(f"[FULLSCAN3] preflight chip statuses: {dict(counts)}")
if counts.get("skipped_existing", 0) == 0:
    raise SystemExit("preflight resolved zero pre-downloaded chips - layout mismatch, refusing to launch")
EOF
  touch "$PREFLIGHT/.ok"
  echo "[FULLSCAN3] preflight ok"
fi

# --- gate 5: storage sanity (run 1 measured ~16KB/anchor, projected <1G) ----
FREE=$(df -B1 --output=avail "$RUN_ROOT" | tail -1 | tr -d ' ')
RESERVE=$((RESERVE_GIB * 1024 * 1024 * 1024))
PROJECTED=$((EXPECTED_ANCHORS * 16 * 1024 * 2))  # 2x safety on run 1's measured per-anchor footprint
if [[ $((PROJECTED + RESERVE)) -gt "$FREE" ]]; then
  echo "ERROR: projected $(numfmt --to=iec $PROJECTED) + reserve exceeds free $(numfmt --to=iec $FREE)" >&2
  exit 4
fi
echo "[FULLSCAN3] storage ok: projected=$(numfmt --to=iec $PROJECTED) free=$(numfmt --to=iec $FREE)"

# --- main run: strictly sequential lanes -------------------------------------
echo "[FULLSCAN3] launch utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) model=$MODEL workers=$WORKERS_TOTAL qps=$QPS_TOTAL no_live_gehi=1 anchors=$ANCHORS_DIR"
for lane_spec in "a24:anchors_A24.csv:24" "a48:anchors_A48.csv:48"; do
  IFS=: read -r lane anchors extent <<<"$lane_spec"
  if [[ -f "$RUN_ROOT/$lane/.done" ]]; then
    echo "[FULLSCAN3] lane $lane already done, skipping"
    continue
  fi
  if run_lane "$lane" "$ANCHORS_DIR/$anchors" "$extent"; then
    date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/$lane/.done"
    echo "[FULLSCAN3] lane $lane done utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  else
    echo "ERROR: lane $lane exited nonzero (failed anchors recorded; rerun this script to resume)" >&2
    exit 1
  fi
done
date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/.complete"
echo "[FULLSCAN3] complete utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
