#!/usr/bin/env bash
# Full-population Gemini backdating scan (RUN-fullscan-gemini-backdating-2026-07-15).
#
# Pre-downloaded basemap_rebuild_2026-07-13 chips, model gemini-3.1-flash-lite
# (ISSUE-25 freeze), Merged provider, ISSUE-26 per-grid Vexcel census cutoff.
#
# Topology: three lanes over two GEHI address families, balanced by anchor
# count so each family's TM catalog limiter (--tm-catalog-interval, the
# 403-ban guard) is saturated evenly:
#   v4 (DOTNET_SYSTEM_NET_DISABLEIPV6=1): a24_v4 shard        (~20.7k anchors)
#   v6:                                   a48, then a24_v6    (~5.1k + ~15.6k)
# At most two scan processes run concurrently (20 workers / 10 qps each =
# Stage-C's tested 40/20 aggregate on the Gemini gateway).
# Resumable: rerunning skips terminal scan states; .done per lane, .complete
# at the end. Run inside tmux; expect ~20-30 h wall clock (TM-catalog-bound:
# ~4-5 TM metadata calls/anchor at 1 req/s/family).
set -euo pipefail

cd /home/gao/projects/solar_backdating
source scripts/activate_env.sh

DL_ROOT="${DL_ROOT:-$HOME/zasolar_data/geid_temporal/basemap_rebuild_2026-07-13}"
RUN_ROOT="${RUN_ROOT:-$HOME/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07}"
CHIPS_DIR="$DL_ROOT/chips"
VEXCEL_CSV="${VEXCEL_CSV:-${ZASOLAR_ROOT:-/home/gao/projects/ZAsolar}/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
WORKERS_PER_LANE="${WORKERS_PER_LANE:-20}"
QPS_PER_LANE="${QPS_PER_LANE:-10}"
TM_CATALOG_INTERVAL="${TM_CATALOG_INTERVAL:-1.0}"
# Offline TM catalog (2026-07-16 mid-run amendment): the download-phase
# candidates CSV already lists every anchor's TM dates, so the scan builds the
# TM catalog offline (0 live TM metadata calls; completeness delegated to the
# downloaded basemap) and picks carry _vnoversion so pre-downloaded TM chips
# are actually reused. TM_CATALOG_INTERVAL stays as the guard for the rare
# live-fallback anchors missing from the CSV.
# Offline Wayback catalog (--offline-wayback, same-day follow-up): the same
# CSV's Wayback rows (real numeric version) build the Wayback catalog offline
# too, eliminating the remaining ~2 live Wayback info calls/anchor (z19/z18)
# plus the z20 lazy-query; z20 is marked unavailable rather than lazily
# fetched (see run_adaptive_scan.py's _build_offline_wayback_catalog).
OFFLINE_TM_CSV="${OFFLINE_TM_CSV:-$DL_ROOT/gehi_vintage_candidates_full.csv}"
EXPECTED_ANCHORS="${EXPECTED_ANCHORS:-41393}"
RESERVE_GIB="${RESERVE_GIB:-20}"

mkdir -p "$RUN_ROOT"

# --- gate 1: download-complete sentinel (written by the monitor after
# verifying all batch .done markers on both machines + final koko rsync) ----
if [[ ! -f "$DL_ROOT/.download_complete" ]]; then
  echo "ERROR: $DL_ROOT/.download_complete missing - basemap download not verified complete" >&2
  exit 3
fi
[[ -f "$VEXCEL_CSV" ]] || { echo "ERROR: vexcel CSV missing: $VEXCEL_CSV" >&2; exit 3; }
[[ -d "$CHIPS_DIR" ]] || { echo "ERROR: chips dir missing: $CHIPS_DIR" >&2; exit 3; }
[[ -f "$OFFLINE_TM_CSV" ]] || { echo "ERROR: offline TM catalog CSV missing: $OFFLINE_TM_CSV" >&2; exit 3; }

# --- gate 2: historical RUN-1 anchors are immutable/prebuilt ----------------
# The legacy builder was archived at commit b76c1d3 by ISSUE-27. This runner is
# a RUN-1 provenance script and must never rebuild or silently consume anchors-v2.
if [[ ! -f "$RUN_ROOT/anchors_summary.json" ]]; then
  echo "ERROR: prebuilt RUN-1 anchors missing at $RUN_ROOT (legacy builder archived at b76c1d3)" >&2
  exit 3
fi
echo "[FULLSCAN] anchors_summary=$(cat "$RUN_ROOT/anchors_summary.json" | python -c 'import json,sys; s=json.load(sys.stdin); print(s["n_anchors"], s["arm_counts"])')"

# Balance the two GEHI address families by anchor count: v4 takes the head of
# A24; v6 takes A48 plus the A24 tail, so both families carry ~half the TM
# catalog calls. Contiguous split preserves the grid-clustered row order.
if [[ ! -f "$RUN_ROOT/anchors_A24_v4.csv" || ! -f "$RUN_ROOT/anchors_A24_v6.csv" ]]; then
  python - "$RUN_ROOT" <<'EOF'
import csv, sys
from pathlib import Path

run_root = Path(sys.argv[1])
a24 = list(csv.DictReader((run_root / "anchors_A24.csv").open(newline="", encoding="utf-8")))
a48 = list(csv.DictReader((run_root / "anchors_A48.csv").open(newline="", encoding="utf-8")))
n_v4 = (len(a24) + len(a48)) // 2  # v6 additionally carries all of A48
shards = {"anchors_A24_v4.csv": a24[:n_v4], "anchors_A24_v6.csv": a24[n_v4:]}
for name, rows in shards.items():
    with (run_root / name).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
print(f"[FULLSCAN] family shards: v4={n_v4} v6={len(a24)-n_v4}+A48={len(a48)}")
EOF
fi

# --- gate 3: gateway health + slots ------------------------------------------
if ! curl -fsS --max-time 10 http://localhost:8080/health >/dev/null; then
  echo "ERROR: sub2api gateway health preflight failed" >&2
  exit 3
fi
read -r ACTIVE_ACCOUNTS ACTIVE_SLOTS < <(
  docker exec sub2api-postgres sh -lc \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF " " -c "select count(*),coalesce(sum(concurrency),0) from accounts where deleted_at is null and platform='"'"'antigravity'"'"' and status='"'"'active'"'"' and schedulable=true"'
)
NEEDED_SLOTS=$((2 * WORKERS_PER_LANE))
if [[ "$ACTIVE_SLOTS" -lt "$NEEDED_SLOTS" ]]; then
  echo "ERROR: need $NEEDED_SLOTS gateway slots, active=$ACTIVE_SLOTS" >&2
  exit 3
fi
echo "[FULLSCAN] gateway_accounts=$ACTIVE_ACCOUNTS gateway_slots=$ACTIVE_SLOTS"

run_lane() {
  local lane="$1" anchors="$2" extent="$3" family="$4" limit="${5:-}" tag="${6:-main}"
  local out="$RUN_ROOT/$lane"
  local limit_args=()
  [[ -n "$limit" ]] && limit_args=(--limit-anchors "$limit")
  local env_prefix=()
  # v4 lanes pin GEHI (dotnet) to IPv4 so each family's TM catalog limiter
  # paces an independent 1 req/s budget, matching the download split.
  [[ "$family" == "v4" ]] && env_prefix=(env DOTNET_SYSTEM_NET_DISABLEIPV6=1)
  mkdir -p "$out"
  "${env_prefix[@]}" python -u scripts/temporal/run_adaptive_scan.py \
    --anchors-csv "$anchors" \
    --scan-states-dir "$out/scan_states" \
    --chips-dir "$out/unused_merged_chips" \
    --merged-tm-chips-dir "$CHIPS_DIR" \
    --merged-wayback-chips-dir "$CHIPS_DIR" \
    --audit-dir "$out/audit" \
    --provider Merged \
    --scorer gemini \
    --anchor-workers "$WORKERS_PER_LANE" \
    --qps "$QPS_PER_LANE" \
    --round1-model "$MODEL" \
    --round2-model "$MODEL" \
    --routing-salt-mode target \
    --no-verdict-store \
    --review-extent-m "$extent" \
    --vexcel-capture-csv "$VEXCEL_CSV" \
    --tm-catalog-interval "$TM_CATALOG_INTERVAL" \
    --offline-tm-catalog-csv "$OFFLINE_TM_CSV" \
    --offline-wayback \
    --catalog-cache-dir "$out/catalog_cache" \
    "${limit_args[@]}" \
    2>&1 | tee -a "$out/run_${tag}.log"
}

# --- gate 4: authenticated 1-anchor preflight + chip cache-hit assertion ----
PREFLIGHT="$RUN_ROOT/preflight"
if [[ ! -f "$PREFLIGHT/.ok" ]]; then
  rm -rf "$PREFLIGHT"
  mkdir -p "$PREFLIGHT"
  echo "[FULLSCAN] authenticated preflight begin utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  env DOTNET_SYSTEM_NET_DISABLEIPV6=1 python -u scripts/temporal/run_adaptive_scan.py \
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
    --tm-catalog-interval "$TM_CATALOG_INTERVAL" \
    --offline-tm-catalog-csv "$OFFLINE_TM_CSV" \
    --offline-wayback \
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
print(f"[FULLSCAN] preflight chip statuses: {dict(counts)}")
if counts.get("skipped_existing", 0) == 0:
    raise SystemExit("preflight resolved zero pre-downloaded chips - layout mismatch, refusing to launch")
EOF
  touch "$PREFLIGHT/.ok"
  echo "[FULLSCAN] preflight ok"
fi

# --- gate 5: first-200 storage projection (a24_v4 lane head, reused by main run)
if [[ ! -f "$RUN_ROOT/.storage_checked" ]]; then
  BEFORE_BYTES=$(( $(du -sb "$CHIPS_DIR" | awk '{print $1}') + $(du -sb "$RUN_ROOT" | awk '{print $1}') ))
  run_lane a24_v4 "$RUN_ROOT/anchors_A24_v4.csv" 24 v4 200 first200
  AFTER_BYTES=$(( $(du -sb "$CHIPS_DIR" | awk '{print $1}') + $(du -sb "$RUN_ROOT" | awk '{print $1}') ))
  GROWTH=$((AFTER_BYTES - BEFORE_BYTES))
  PROJECTED=$((GROWTH * EXPECTED_ANCHORS / 200))
  RESERVE=$((RESERVE_GIB * 1024 * 1024 * 1024))
  FREE=$(df -B1 --output=avail "$RUN_ROOT" | tail -1 | tr -d ' ')
  {
    echo "growth_first200_bytes=$GROWTH"
    echo "projected_bytes=$PROJECTED"
    echo "free_bytes=$FREE"
    echo "reserve_bytes=$RESERVE"
    echo "checked_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$RUN_ROOT/storage_check.txt"
  if [[ $((PROJECTED + RESERVE)) -gt "$FREE" ]]; then
    echo "ERROR: projected growth $(numfmt --to=iec $PROJECTED) + reserve exceeds free $(numfmt --to=iec $FREE); prune GEHI tile cache first" >&2
    exit 4
  fi
  touch "$RUN_ROOT/.storage_checked"
  echo "[FULLSCAN] storage ok: projected=$(numfmt --to=iec $PROJECTED) free=$(numfmt --to=iec $FREE)"
fi

# --- main run: v4 lane parallel with the sequential v6 lanes ----------------
echo "[FULLSCAN] launch lanes utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) model=$MODEL workers=${WORKERS_PER_LANE}x2 qps=${QPS_PER_LANE}x2 tm_interval=${TM_CATALOG_INTERVAL}s"
pids=()
if [[ ! -f "$RUN_ROOT/a24_v4/.done" ]]; then
  ( run_lane a24_v4 "$RUN_ROOT/anchors_A24_v4.csv" 24 v4 \
      && date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/a24_v4/.done" ) &
  pids+=("a24_v4:$!")
fi
if [[ ! -f "$RUN_ROOT/a48/.done" || ! -f "$RUN_ROOT/a24_v6/.done" ]]; then
  (
    if [[ ! -f "$RUN_ROOT/a48/.done" ]]; then
      run_lane a48 "$RUN_ROOT/anchors_A48.csv" 48 v6
      date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/a48/.done"
    fi
    if [[ ! -f "$RUN_ROOT/a24_v6/.done" ]]; then
      run_lane a24_v6 "$RUN_ROOT/anchors_A24_v6.csv" 24 v6
      date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/a24_v6/.done"
    fi
  ) &
  pids+=("v6_chain:$!")
fi
rc=0
for entry in "${pids[@]}"; do
  lane="${entry%%:*}"; pid="${entry##*:}"
  if ! wait "$pid"; then
    echo "ERROR: lane $lane exited nonzero (rerun this script to resume)" >&2
    rc=1
  fi
done
if [[ "$rc" -ne 0 ]]; then
  exit "$rc"
fi

date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN_ROOT/.complete"
echo "[FULLSCAN] complete utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
