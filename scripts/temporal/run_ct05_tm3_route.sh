#!/usr/bin/env bash
# Run three paced TM-z19 workers for one CT-05 network route.

set -euo pipefail

ROUTE="${1:?route required}"
RUN_ROOT="${2:-${HOME}/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/ct05_download_v1}"
PARENT_PID="${3:-}"
case "$ROUTE" in
  home_v4|home_v6|koko_v4|koko_v6|box_v4|box_v6) ;;
  *) echo "unsupported route: $ROUTE" >&2; exit 2 ;;
esac

REPO_ROOT="${SOLAR_BACKDATING_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
ANCHORS_CSV="$(dirname "$RUN_ROOT")/groups_v1/chip_groups_as_anchors.csv"
PLAN_DIR="$RUN_ROOT/plan/tm3/$ROUTE"
ROUTE_DIR="$RUN_ROOT/lanes/$ROUTE"
OUT_DIR="$ROUTE_DIR/tm3"
CHIPS_DIR="$RUN_ROOT/chips"
PYTHON_BIN="${PYTHON_BIN:-python3}"
GEHI_EXE="${GEHI_EXE:-${HOME}/zasolar_data/tools/GEHistoricalImagery/GEHistoricalImagery}"
REQUEST_INTERVAL="${REQUEST_INTERVAL:-2.0}"

mkdir -p "$OUT_DIR" "$CHIPS_DIR"
exec 9>"$OUT_DIR/route.lock"
flock -n 9 || { echo "TM3 route already running: $ROUTE" >&2; exit 3; }

if [[ "$ROUTE" == *_v4 ]]; then
  export DOTNET_SYSTEM_NET_DISABLEIPV6=1
else
  unset DOTNET_SYSTEM_NET_DISABLEIPV6 || true
fi

log() {
  printf '[CT05-TM3] %s route=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ROUTE" "$*"
}

run_lane() {
  local index="$1"
  local lane_id="${ROUTE}_tm_lane$(printf '%02d' "$index")"
  local candidates="$PLAN_DIR/${lane_id}.csv"
  local manifest="$OUT_DIR/${lane_id}_manifest.csv"
  local raw_log="$OUT_DIR/${lane_id}_raw.jsonl"
  local done_marker="$OUT_DIR/${lane_id}.done"
  if [[ -f "$done_marker" ]]; then
    log "skip completed lane=$lane_id"
    return
  fi
  if [[ ! -s "$candidates" ]]; then
    log "skip lane=$lane_id (no candidates file: $candidates)"
    return
  fi
  "$PYTHON_BIN" "$REPO_ROOT/scripts/temporal/gehi_download.py" \
    --anchors-csv "$ANCHORS_CSV" \
    --candidates-csv "$candidates" \
    --output-dir "$CHIPS_DIR" \
    --manifest "$manifest" \
    --raw-log "$raw_log" \
    --gehi-exe "$GEHI_EXE" \
    --zoom 19,18 \
    --provider TM \
    --parallel 4 \
    --timeout 600 \
    --request-interval "$REQUEST_INTERVAL" \
    --max-attempts 3 \
    --allow-failures
  date -u +%Y-%m-%dT%H:%M:%SZ > "$done_marker"
  log "done lane=$lane_id"
}

TM3_LANES="${TM3_LANES:-3}"
log "start lanes=${TM3_LANES} request_interval=${REQUEST_INTERVAL}s"
pids=()
for index in $(seq 1 "$TM3_LANES"); do
  run_lane "$index" >"$OUT_DIR/lane$(printf '%02d' "$index").log" 2>&1 &
  pids+=("$!")
done

rc=0
for pid in "${pids[@]}"; do
  wait "$pid" || rc=1
done
if [[ "$rc" -ne 0 ]]; then
  log "one or more TM3 lanes failed; rerun route manager to resume"
  exit 1
fi

date -u +%Y-%m-%dT%H:%M:%SZ > "$ROUTE_DIR/${ROUTE}_tm_z19.done"
log "all TM3 lanes complete"

# The original sequential route runner is intentionally stopped while TM3
# owns the TM shard. Once Wayback has flushed its manifest, resume it; it sees
# the TM .done marker, skips duplicate work, and writes the route .complete.
while [[ ! -s "$ROUTE_DIR/${ROUTE}_wayback_z19_manifest.csv" ]]; do
  sleep 60
done
if [[ -n "$PARENT_PID" ]] && kill -0 "$PARENT_PID" 2>/dev/null; then
  kill -CONT "$PARENT_PID"
  log "resumed original route parent pid=$PARENT_PID for finalization"
fi
