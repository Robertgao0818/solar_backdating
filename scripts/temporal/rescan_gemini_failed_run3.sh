#!/usr/bin/env bash
# Targeted rescan for the `done_ambiguous_gemini_failed` cohort fossilized
# during the 2026-07-18 Gemini quota outage (38 anchors in a24 as of the
# restart; a48 checked too in case anything blips there once it starts).
# `done_ambiguous_gemini_failed` is a TERMINAL status (scan_state.py:36) —
# the main run will never retry it on its own, so this is a separate,
# explicit remediation pass. See ISSUE-28.
#
# Waits for $RUN_ROOT/.complete (both a24 and a48 lanes finished, including
# chain3's auto-rerun of the two known orchestrator_error anchors) before
# touching anything, so it never races with the in-flight main run.
#
# Mechanism: build a small anchors CSV containing only the gemini_failed
# subset, then invoke run_adaptive_scan.py with --force-restart pointed at
# that filtered CSV (force_restart only deletes/recreates state for anchors
# it actually iterates, i.e. exactly this subset — everything else in
# scan_states is untouched). Gentle pace (10 workers / 2 qps) since this is
# at most ~100 anchors, not a full-population run.
set -uo pipefail

RUN_ROOT="$HOME/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/run3_v2"
DL_ROOT="$HOME/zasolar_data/geid_temporal/basemap_rebuild_2026-07-13"
ANCHORS_DIR="$HOME/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/anchors_v2"
CHIPS_DIR="$DL_ROOT/chips"
VEXCEL_CSV="${ZASOLAR_ROOT:-/home/gao/projects/ZAsolar}/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv"
OFFLINE_TM_CSV="$DL_ROOT/gehi_vintage_candidates_full_run3.csv"
MODEL="gemini-3.1-flash-lite"
LOG="$RUN_ROOT/rescan_gemini_failed.log"

log() { echo "[RESCAN-GF] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }

log "armed: waiting for $RUN_ROOT/.complete"
while [[ ! -f "$RUN_ROOT/.complete" ]]; do sleep 60; done
log "main run complete, proceeding"

cd /home/gao/projects/solar_backdating
source scripts/activate_env.sh

rescan_lane() {
  local lane="$1" extent="$2"
  local anchors_csv="$ANCHORS_DIR/anchors_${lane^^}.csv"
  local states_dir="$RUN_ROOT/$lane/scan_states"
  local filtered_csv="$RUN_ROOT/$lane/gemini_failed_subset.csv"

  python3 - "$anchors_csv" "$states_dir" "$filtered_csv" <<'PYEOF'
import csv, json, sys
from pathlib import Path

anchors_csv, states_dir, filtered_csv = sys.argv[1:4]
target_ids = set()
for f in Path(states_dir).glob("*.json"):
    try:
        s = json.loads(f.read_text())
    except Exception:
        continue
    if s.get("status") == "done_ambiguous_gemini_failed":
        target_ids.add(f.stem)

with open(anchors_csv, newline="") as src:
    reader = csv.DictReader(src)
    rows = [r for r in reader if r["anchor_id"] in target_ids]
    fieldnames = reader.fieldnames

with open(filtered_csv, "w", newline="") as dst:
    writer = csv.DictWriter(dst, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"{len(rows)}")
PYEOF
}

for lane_spec in "a24:24" "a48:48"; do
  IFS=: read -r lane extent <<<"$lane_spec"
  if [[ ! -d "$RUN_ROOT/$lane/scan_states" ]]; then
    log "lane $lane has no scan_states dir, skipping"
    continue
  fi
  n=$(rescan_lane "$lane" "$extent" | tail -1)
  filtered_csv="$RUN_ROOT/$lane/gemini_failed_subset.csv"
  log "lane $lane: $n gemini_failed anchors found"
  if [[ "$n" -eq 0 ]]; then
    log "lane $lane: nothing to rescan"
    continue
  fi
  log "lane $lane: rescanning $n anchors (force_restart scoped to this subset only)"
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$filtered_csv" \
    --scan-states-dir "$RUN_ROOT/$lane/scan_states" \
    --chips-dir "$RUN_ROOT/$lane/unused_merged_chips" \
    --merged-tm-chips-dir "$CHIPS_DIR" \
    --merged-wayback-chips-dir "$CHIPS_DIR" \
    --audit-dir "$RUN_ROOT/$lane/audit" \
    --provider Merged \
    --scorer gemini \
    --anchor-workers 10 \
    --qps 2 \
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
    --catalog-cache-dir "$RUN_ROOT/$lane/catalog_cache" \
    --force-restart \
    2>&1 | tee -a "$RUN_ROOT/$lane/rescan_gemini_failed.log" | tee -a "$LOG"
  log "lane $lane: rescan pass done"
done

log "all lanes swept"
