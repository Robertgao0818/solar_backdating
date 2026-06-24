#!/usr/bin/env bash
# End-to-end from-scratch backdating rerun for ONE replica over the 642-installation §3 sample.
# Full 4-layer chain (L0 gehi_main -> L1 per-target no-recent -> L_census 2023 narrowing ->
# merge_three_layers + flatten), 1:1 with production flags. Nothing is frozen: GEHI/Wayback
# are re-fetched, chips re-rendered, every Gemini decision re-scored.
#
# Usage:   run_endtoend_rep.sh <rep_index>
# Env:     ANCHOR_WORKERS (default 30), QPS (default 8), LIMIT (smoke: --limit-anchors on L0)
# Tmux:    tmux new -s e2e_rep1 'bash scripts/validation/run_endtoend_rep.sh 1; bash'
set -uo pipefail
REP="${1:?usage: run_endtoend_rep.sh <rep_index>}"

cd /home/gaosh/projects/solar_backdating
source scripts/activate_env.sh

ROOT=/home/gaosh/zasolar_data/geid_temporal/llm_endtoend_20260623
REF=$ROOT/reference.csv
WL=$ROOT/work_lists
REPDIR=$ROOT/rep$REP
VEXCEL=/home/gaosh/projects/ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv
PROD_NR=/home/gaosh/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/norecent_pertarget

ANCHOR_WORKERS=${ANCHOR_WORKERS:-30}
QPS=${QPS:-8}
LIMIT="${LIMIT:-}"
SCAN_COMMON=(--anchor-workers "$ANCHOR_WORKERS" --qps "$QPS"
            --routing-salt-mode target
            --cheap-round-types initial,bisection,walk_back,tail)

mkdir -p "$REPDIR"/{L0,L1/norecent_pertarget,L_census}
ts() { date -u +%H:%M:%S; }

echo "############ REP $REP  $(ts)  workers=$ANCHOR_WORKERS qps=$QPS limit=${LIMIT:-none} ############"

# ---------------- L0: gehi_main group scan ----------------
echo "===== [$(ts)] L0 run_adaptive_scan (244 group anchors) ====="
LIMARG=(); [ -n "$LIMIT" ] && LIMARG=(--limit-anchors "$LIMIT")
python -u scripts/temporal/run_adaptive_scan.py \
  --anchors-csv "$WL/l0_anchors.csv" \
  --scan-states-dir "$REPDIR/L0/scan_states" \
  --chips-dir "$REPDIR/L0/chips" \
  --audit-dir "$REPDIR/L0/audit" \
  "${SCAN_COMMON[@]}" "${LIMARG[@]}" || { echo "L0 scan FAILED"; exit 1; }

echo "===== [$(ts)] L0 infer_install_dates (vexcel-clamped) ====="
python -u scripts/temporal/infer_install_dates.py \
  --scan-states-dir "$REPDIR/L0/scan_states" \
  --output "$REPDIR/L0/install_intervals.csv" \
  --vexcel-capture-csv "$VEXCEL" || { echo "L0 infer FAILED"; exit 1; }

# ---------------- L1: per-target no-recent rescan ----------------
echo "===== [$(ts)] L1 expand per-target anchors from THIS replica's L0 ====="
python -u scripts/validation/llm_endtoend_expand_l1.py \
  --reference "$REF" \
  --l0-intervals "$REPDIR/L0/install_intervals.csv" \
  --output "$REPDIR/L1/norecent_pertarget/per_target_anchors.csv" || { echo "L1 expand FAILED"; exit 1; }

# always ensure a header-only recovered file exists (splice reads it even when L1 is empty)
head -1 "$PROD_NR/recovered_polygons_pertarget.csv" > "$REPDIR/L1/norecent_pertarget/recovered_polygons_pertarget.csv"

NL1=$(( $(wc -l < "$REPDIR/L1/norecent_pertarget/per_target_anchors.csv") - 1 ))
if [ "$NL1" -gt 0 ]; then
  echo "===== [$(ts)] L1 run_adaptive_scan ($NL1 per-target anchors) ====="
  python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$REPDIR/L1/norecent_pertarget/per_target_anchors.csv" \
    --scan-states-dir "$REPDIR/L1/scan_states" \
    --chips-dir "$REPDIR/L1/chips" \
    --audit-dir "$REPDIR/L1/audit" \
    "${SCAN_COMMON[@]}" || { echo "L1 scan FAILED"; exit 1; }

  echo "===== [$(ts)] L1 infer_install_dates (pertarget, vexcel-clamped) ====="
  python -u scripts/temporal/infer_install_dates.py \
    --scan-states-dir "$REPDIR/L1/scan_states" \
    --output "$REPDIR/L1/norecent_pertarget/install_intervals_pertarget.csv" \
    --vexcel-capture-csv "$VEXCEL" || { echo "L1 infer FAILED"; exit 1; }

  echo "===== [$(ts)] L1 aggregate_recovery -> recovered_polygons_pertarget.csv ====="
  cp "$PROD_NR/aggregate_recovery.py" "$REPDIR/L1/norecent_pertarget/aggregate_recovery.py"
  if python -u "$REPDIR/L1/norecent_pertarget/aggregate_recovery.py"; then :; else
    echo "  (aggregate produced no recovered rows; keeping header-only file)"
    head -1 "$PROD_NR/recovered_polygons_pertarget.csv" > "$REPDIR/L1/norecent_pertarget/recovered_polygons_pertarget.csv"
  fi
else
  echo "  L1: no per-target anchors for this replica (no sample group went no-recent); skipping."
fi

# ---------------- L_census: 2023 Wayback narrowing ----------------
echo "===== [$(ts)] L_census run_census2023_scan ($(($(wc -l < "$WL/census_cohort_sample.csv")-1)) cohort rows) ====="
python -u scripts/temporal/run_census2023_scan.py \
  --cohort-csv "$WL/census_cohort_sample.csv" \
  --main-scan-states-dir "$REPDIR/L0/scan_states" \
  --norecent-scan-states-dir "$REPDIR/L1/scan_states" \
  --census-chips-dir "$REPDIR/L_census/chips" \
  --audit-dir "$REPDIR/L_census/audit" \
  --output "$REPDIR/L_census/census_install_intervals.csv" \
  --workers "$ANCHOR_WORKERS" --qps "$QPS" --routing-salt-mode target \
  || { echo "L_census scan FAILED"; exit 1; }

# ---------------- merge + flatten (real production post-processing) ----------------
echo "===== [$(ts)] splice + REAL merge_three_layers + flatten ====="
python -u scripts/validation/llm_endtoend_splice_merge.py \
  --reference "$REF" --rep-dir "$REPDIR" || { echo "splice/merge FAILED"; exit 1; }

echo "############ REP $REP DONE  $(ts)  -> $REPDIR/delivery.csv ############"
