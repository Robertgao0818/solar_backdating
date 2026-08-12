#!/usr/bin/env bash
# One-shot CT-05 four-route download health check with bounded recovery.
#
# Missing lane runners are relaunched when their .complete marker is absent.
# Live-but-slow lanes are reported, not killed: GEHI retries and provider
# backoff can legitimately make a live lane quiet for tens of minutes.

set -euo pipefail

CHECK_LABEL="${1:-manual}"
REPO_ROOT="${SOLAR_BACKDATING_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
CT_ROOT="${HOME}/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
RUN_ROOT="${CT_ROOT}/ct05_download_v1"
REPORT_DIR="${RUN_ROOT}/healthchecks"
HOME_PYTHON="/home/gao/projects/ZAsolar/.venv/bin/python"
HOME_GEHI="/home/gao/zasolar_data/tools/GEHistoricalImagery/GEHistoricalImagery"
KOKO_HOST="koko@koko-82xm"
KOKO_RUN_ROOT="/home/koko/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/ct05_download_v1"
KOKO_REPO="/home/koko/projects/solar_backdating"
KOKO_PYTHON="/home/koko/.venvs/coj/bin/python"
KOKO_GEHI="/home/koko/zasolar_data/tools/GEHistoricalImagery/GEHistoricalImagery"

mkdir -p "$REPORT_DIR"
REPORT="${REPORT_DIR}/health_${CHECK_LABEL}_$(date +%Y%m%dT%H%M%S%z).log"
exec >>"$REPORT" 2>&1

log() {
  printf '[CT05-HEALTH] %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

latest_raw_age() {
  local lane_dir="$1"
  local latest now
  latest="$(find "$lane_dir" -maxdepth 1 -type f -name '*_raw.jsonl' -printf '%T@\n' 2>/dev/null | sort -nr | head -1)"
  if [[ -z "$latest" ]]; then
    echo "none"
    return
  fi
  now="$(date +%s)"
  printf '%d\n' "$((now - ${latest%.*}))"
}

launch_home_lane() {
  local lane="$1"
  local command
  command="cd '$REPO_ROOT' && PYTHON_BIN='$HOME_PYTHON' GEHI_EXE='$HOME_GEHI' bash scripts/temporal/run_ct05_download_lane.sh '$lane' '$RUN_ROOT' >> '$RUN_ROOT/lanes/$lane/recovery.log' 2>&1"
  mkdir -p "$RUN_ROOT/lanes/$lane"
  if tmux has-session -t ct05_dl_home 2>/dev/null; then
    tmux new-window -d -t ct05_dl_home -n "${lane}_recovery" "$command"
  else
    tmux new-session -d -s ct05_dl_home -n "${lane}_recovery" "$command"
  fi
}

check_home_lane() {
  local lane="$1"
  local lane_dir="$RUN_ROOT/lanes/$lane"
  if [[ -f "$lane_dir/.complete" ]]; then
    log "home lane=$lane status=complete"
  elif pgrep -f "run_ct05_download_lane.sh $lane $RUN_ROOT" >/dev/null; then
    log "home lane=$lane status=alive latest_raw_age_s=$(latest_raw_age "$lane_dir")"
  else
    log "home lane=$lane status=missing action=relaunch"
    launch_home_lane "$lane"
    sleep 3
    if pgrep -f "run_ct05_download_lane.sh $lane $RUN_ROOT" >/dev/null; then
      log "home lane=$lane recovery=pass"
    else
      log "home lane=$lane recovery=fail"
    fi
  fi
}

check_koko_lane() {
  local lane="$1"
  local remote_status
  remote_status="$(
    ssh -o BatchMode=yes -o ConnectTimeout=20 "$KOKO_HOST" "
      if test -f '$KOKO_RUN_ROOT/lanes/$lane/.complete'; then
        echo complete
      elif pgrep -f 'run_ct05_download_lane.sh $lane $KOKO_RUN_ROOT' >/dev/null; then
        echo alive
      else
        echo missing
      fi
    " 2>/dev/null || echo unreachable
  )"
  log "koko lane=$lane status=$remote_status"
  if [[ "$remote_status" != "missing" ]]; then
    return
  fi
  ssh -o BatchMode=yes -o ConnectTimeout=20 "$KOKO_HOST" "
    mkdir -p '$KOKO_RUN_ROOT/lanes/$lane'
    cd '$KOKO_REPO'
    PYTHON_BIN='$KOKO_PYTHON' GEHI_EXE='$KOKO_GEHI' setsid nohup \
      bash scripts/temporal/run_ct05_download_lane.sh '$lane' '$KOKO_RUN_ROOT' \
      >> '$KOKO_RUN_ROOT/lanes/$lane/recovery.log' 2>&1 < /dev/null &
  "
  sleep 3
  if ssh -o BatchMode=yes -o ConnectTimeout=20 "$KOKO_HOST" \
    "pgrep -f 'run_ct05_download_lane.sh $lane $KOKO_RUN_ROOT' >/dev/null"; then
    log "koko lane=$lane recovery=pass"
  else
    log "koko lane=$lane recovery=fail"
  fi
}

log "begin label=$CHECK_LABEL local_time=$(date --iso-8601=seconds)"
log "home disk=$(df -h /home | tail -1)"
log "home tif_count=$(find "$RUN_ROOT/chips" -type f -name '*.tif' 2>/dev/null | wc -l)"
check_home_lane home_v4
check_home_lane home_v6

if ssh -o BatchMode=yes -o ConnectTimeout=20 "$KOKO_HOST" true; then
  log "koko disk=$(ssh "$KOKO_HOST" 'df -h / | tail -1')"
  log "koko tif_count=$(ssh "$KOKO_HOST" "find '$KOKO_RUN_ROOT/chips' -type f -name '*.tif' 2>/dev/null | wc -l")"
  check_koko_lane koko_v4
  check_koko_lane koko_v6
else
  log "koko status=unreachable action=none"
fi
log "end label=$CHECK_LABEL report=$REPORT"
