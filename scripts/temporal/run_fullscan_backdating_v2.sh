#!/usr/bin/env bash
# Full-population Gemini backdating scan, run 2 (clean rerun of run 1, which is
# archived at $RUN_ROOT/dirty_run1_archive_2026-07-17/ — forensics in
# docs/replan_v2/DATA-fullscan-run1-dirty-forensics-2026-07-17.md).
#
# Differences from run_fullscan_backdating.sh (run 1):
#   - Strict no-live-GEHI: both catalogs offline + --no-live-gehi; the process
#     never spawns a GEHI subprocess, so the only network traffic is the Gemini
#     gateway. Missing chips are recorded as download-failed picks (greppable),
#     never fetched.
#   - No GEHI address-family topology (it only existed to spread 403-ban risk):
#     two SEQUENTIAL lanes, a24 (anchors_A24.csv, extent 24) then a48
#     (anchors_A48.csv, extent 48). One process at a time.
#   - Owner-frozen pace 2026-07-17: WORKERS_TOTAL=80 / QPS_TOTAL=8 apply to
#     whichever single lane is running (80 per owner's gateway check: 110
#     slots, one process at a time; priority is clean output, not speed;
#     transport 429/5xx now retried with exponential backoff inside
#     gemini_solar_image_review.py instead of fossilizing into
#     done_ambiguous_gemini_failed).
# Resumable: rerunning skips terminal scan states; .done per lane on clean
# exit only, .complete when both lanes done. Run inside tmux.
set -euo pipefail

cd /home/gao/projects/solar_backdating
source scripts/activate_env.sh

DL_ROOT="${DL_ROOT:-$HOME/zasolar_data/geid_temporal/basemap_rebuild_2026-07-13}"
RUN_ROOT="${RUN_ROOT:-$HOME/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07}"
CHIPS_DIR="$DL_ROOT/chips"
VEXCEL_CSV="${VEXCEL_CSV:-${ZASOLAR_ROOT:-/home/gao/projects/ZAsolar}/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
WORKERS_TOTAL="${WORKERS_TOTAL:-80}"
QPS_TOTAL="${QPS_TOTAL:-8}"
OFFLINE_TM_CSV="${OFFLINE_TM_CSV:-$DL_ROOT/gehi_vintage_candidates_full.csv}"
EXPECTED_ANCHORS="${EXPECTED_ANCHORS:-41393}"
RESERVE_GIB="${RESERVE_GIB:-20}"

mkdir -p "$RUN_ROOT"

# --- gate 1: inputs ----------------------------------------------------------
if [[ ! -f "$DL_ROOT/.download_complete" ]]; then
  echo "ERROR: $DL_ROOT/.download_complete missing - basemap download not verified complete" >&2
  exit 3
fi
[[ -f "$VEXCEL_CSV" ]] || { echo "ERROR: vexcel CSV missing: $VEXCEL_CSV" >&2; exit 3; }
[[ -d "$CHIPS_DIR" ]] || { echo "ERROR: chips dir missing: $CHIPS_DIR" >&2; exit 3; }
[[ -f "$OFFLINE_TM_CSV" ]] || { echo "ERROR: offline catalog CSV missing: $OFFLINE_TM_CSV" >&2; exit 3; }

# --- gate 2: historical RUN-2 anchors are immutable/prebuilt ----------------
# The legacy builder was archived at commit b76c1d3 by ISSUE-27. Despite this
# filename's "v2", it means RUN 2, not anchors schema v2.
if [[ ! -f "$RUN_ROOT/anchors_summary.json" ]]; then
  echo "ERROR: prebuilt RUN-2 anchors missing at $RUN_ROOT (legacy builder archived at b76c1d3)" >&2
  exit 3
fi
echo "[FULLSCAN2] anchors_summary=$(cat "$RUN_ROOT/anchors_summary.json" | python -c 'import json,sys; s=json.load(sys.stdin); print(s["n_anchors"], s["arm_counts"])')"

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
echo "[FULLSCAN2] gateway_accounts=$ACTIVE_ACCOUNTS gateway_slots=$ACTIVE_SLOTS"

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
  echo "[FULLSCAN2] authenticated preflight begin utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$RUN_ROOT/anchors_A24.csv" \
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
print(f"[FULLSCAN2] preflight chip statuses: {dict(counts)}")
if counts.get("skipped_existing", 0) == 0:
    raise SystemExit("preflight resolved zero pre-downloaded chips - layout mismatch, refusing to launch")
EOF
  touch "$PREFLIGHT/.ok"
  echo "[FULLSCAN2] preflight ok"
fi

# --- gate 5: storage sanity (run 1 measured ~16KB/anchor, projected <1G) ----
FREE=$(df -B1 --output=avail "$RUN_ROOT" | tail -1 | tr -d ' ')
RESERVE=$((RESERVE_GIB * 1024 * 1024 * 1024))
PROJECTED=$((EXPECTED_ANCHORS * 16 * 1024 * 2))  # 2x safety on run 1's measured per-anchor footprint
if [[ $((PROJECTED + RESERVE)) -gt "$FREE" ]]; then
  echo "ERROR: projected $(numfmt --to=iec $PROJECTED) + reserve exceeds free $(numfmt --to=iec $FREE)" >&2
  exit 4
fi
echo "[FULLSCAN2] storage ok: projected=$(numfmt --to=iec $PROJECTED) free=$(numfmt --to=iec $FREE)"

# --- main run: strictly sequential lanes -------------------------------------
echo "[FULLSCAN2] launch utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) model=$MODEL workers=$WORKERS_TOTAL qps=$QPS_TOTAL no_live_gehi=1"
for lane_spec in "a24:anchors_A24.csv:24" "a48:anchors_A48.csv:48"; do
  IFS=: read -r lane anchors extent <<<"$lane_spec"
  if [[ -f "$RUN_ROOT/$lane/.done" ]]; then
    echo "[FULLSCAN2] lane $lane already done, skipping"
    continue
  fi
  if run_lane "$lane" "$RUN_ROOT/$anchors" "$extent"; then
    date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/$lane/.done"
    echo "[FULLSCAN2] lane $lane done utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  else
    echo "ERROR: lane $lane exited nonzero (failed anchors recorded; rerun this script to resume)" >&2
    exit 1
  fi
done
date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/.complete"
echo "[FULLSCAN2] complete utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
