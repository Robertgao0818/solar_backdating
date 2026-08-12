#!/usr/bin/env bash
# Run one resumable Cape Town CT-05 download route.
#
# Usage:
#   run_ct05_download_lane.sh home_v4 [run_root]
#
# The route plan must already exist at RUN_ROOT/plan/shards. Each route owns
# four disjoint provider/zoom shards. Successful shard completion is recorded
# with a .done marker; rerunning the lane skips completed shards and
# gehi_download.py itself skips chips already present on disk.

set -euo pipefail

LANE="${1:?lane required: home_v4, home_v6, koko_v4, or koko_v6}"
case "$LANE" in
  home_v4|home_v6|koko_v4|koko_v6) ;;
  *) echo "unsupported lane: $LANE" >&2; exit 2 ;;
esac

REPO_ROOT="${SOLAR_BACKDATING_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUN_ROOT="${2:-${HOME}/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/ct05_download_v1}"
CT_ROOT="$(dirname "$RUN_ROOT")"
ANCHORS_CSV="${CT_ROOT}/groups_v1/chip_groups_as_anchors.csv"
PLAN_DIR="${RUN_ROOT}/plan/shards"
CHIPS_DIR="${RUN_ROOT}/chips"
LANE_DIR="${RUN_ROOT}/lanes/${LANE}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
GEHI_EXE="${GEHI_EXE:-${HOME}/zasolar_data/tools/GEHistoricalImagery/GEHistoricalImagery}"
REQUEST_INTERVAL="${REQUEST_INTERVAL:-1.0}"

mkdir -p "$LANE_DIR" "$CHIPS_DIR"
exec 9>"${LANE_DIR}/lane.lock"
if ! flock -n 9; then
  echo "lane already running: $LANE" >&2
  exit 3
fi

if [[ "$LANE" == *_v4 ]]; then
  export DOTNET_SYSTEM_NET_DISABLEIPV6=1
else
  unset DOTNET_SYSTEM_NET_DISABLEIPV6 || true
fi

log() {
  printf '[CT05-DL] %s lane=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$LANE" "$*"
}

run_shard() {
  local provider="$1"
  local zoom="$2"
  local ladder="$3"
  local stem="${LANE}_${provider,,}_z${zoom}"
  local candidates="${PLAN_DIR}/${stem}.csv"
  local manifest="${LANE_DIR}/${stem}_manifest.csv"
  local raw_log="${LANE_DIR}/${stem}_raw.jsonl"
  local done_marker="${LANE_DIR}/${stem}.done"

  if [[ -f "$done_marker" ]]; then
    log "skip completed shard=$stem"
    return
  fi
  if [[ ! -s "$candidates" ]]; then
    log "missing/empty candidate shard=$candidates"
    return 2
  fi

  log "start shard=$stem ladder=$ladder"
  "$PYTHON_BIN" "$REPO_ROOT/scripts/temporal/gehi_download.py" \
    --anchors-csv "$ANCHORS_CSV" \
    --candidates-csv "$candidates" \
    --output-dir "$CHIPS_DIR" \
    --manifest "$manifest" \
    --raw-log "$raw_log" \
    --gehi-exe "$GEHI_EXE" \
    --zoom "$ladder" \
    --provider "$provider" \
    --parallel 4 \
    --timeout 600 \
    --request-interval "$REQUEST_INTERVAL" \
    --max-attempts 3 \
    --allow-failures
  date -u +%Y-%m-%dT%H:%M:%SZ > "$done_marker"
  log "done shard=$stem"
}

log "lane start cache=${SOLAR_GEHI_TILE_CACHE_DIR:-${HOME}/zasolar_data/geid_raw/gehi_tile_cache}"
# Finish the bounded fallback/Wayback shards before the dominant TM-z19 shard,
# giving QA useful complete strata early while retaining one process per route.
run_shard TM 18 18
run_shard Wayback 18 18
run_shard Wayback 19 19,18
run_shard TM 19 19,18
date -u +%Y-%m-%dT%H:%M:%SZ > "${LANE_DIR}/.complete"
log "lane complete"
