#!/usr/bin/env bash
# Cape Town citywide disk pipeline orchestrator (rev-6).
# Plan of record: docs/replan_v2/RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md
#
# Subcommands (launch-critical path first):
#   gate                 report token + precondition status (fail-closed)
#   plan-waves           copy remaining anchors, derive roster, freeze wave plan
#   plan-wave-shards N   filter candidates, weighted shards, tm3 plans, assemble wave_NN
#   launch-wave N        token-gated launch on home (+koko light; box when enabled)
#   status               progress snapshot
#   merge-wave N | qa-wave N | archive-wave N | score-wave N | release-wave N
#                        (implemented in follow-up PR6b/PR7; currently refuse)
set -euo pipefail

REPO_ROOT="${SOLAR_BACKDATING_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/activate_env.sh" >/dev/null

OWNER_DECISIONS="${REPO_ROOT}/docs/replan_v2/OWNER_DECISIONS.md"
CITYWIDE="${HOME}/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
TOP52="${HOME}/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
DL_ROOT="${CITYWIDE}/ct05_download_v1"
PIPELINE_DIR="${CITYWIDE}/pipeline"
TOP52_STAGING="${TOP52}/ct05_download_v1/archive_staging"
PY="${ZASOLAR_ROOT:-/home/gao/projects/ZAsolar}/.venv/bin/python"

KOKO_SSH="koko@koko-82xm"
KOKO_CITYWIDE="/home/koko/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"

# route topology (rev-6 §8): home full-speed, koko light (lane-only, 1 process)
ROUTES="home_v4,home_v6,koko_v4"
ROUTE_WEIGHTS="home_v4=2,home_v6=2,koko_v4=1"
HOME_ROUTES="home_v4 home_v6"
TM3_ROUTES="home_v4 home_v6"   # koko runs lane-only (no TM3)
REQUEST_INTERVAL=2.0

log() { printf '[ctcw-pipeline] %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { echo "ERROR: $*" >&2; exit 2; }

token_value() {
  grep -E "^$1=[0-9]{4}-[0-9]{2}-[0-9]{2}$" "${OWNER_DECISIONS}" 2>/dev/null | head -1 | cut -d= -f2 || true
}

require_tokens() {
  local e0 e6
  e0="$(token_value E0_PREFETCH_DISK_SIGNED)"
  e6="$(token_value E6_SCORER_GO)"
  [[ -n "${e0}" ]] || die "E0_PREFETCH_DISK_SIGNED token missing in ${OWNER_DECISIONS}"
  [[ -n "${e6}" ]] || die "E6_SCORER_GO token missing in ${OWNER_DECISIONS}"
  log "tokens OK (E0=${e0} E6=${e6})"
}

cmd_gate() {
  local e0 e6
  e0="$(token_value E0_PREFETCH_DISK_SIGNED)"; e6="$(token_value E6_SCORER_GO)"
  printf 'E0 token:            %s\n' "${e0:-MISSING}"
  printf 'E6 token:            %s\n' "${e6:-MISSING}"
  printf 'E2 merged/.done:     %s\n' "$([[ -f "${CITYWIDE}/ct05_catalog_v1/merged/.done" ]] && echo yes || echo no)"
  printf 'B1 BACKUP_OK:        %s\n' "$([[ -f "${TOP52_STAGING}/BACKUP_OK.json" ]] && echo yes || echo no)"
  printf 'B1 RESTORE_DRILL_OK: %s\n' "$([[ -f "${TOP52_STAGING}/RESTORE_DRILL_OK.json" ]] && echo yes || echo no)"
  printf 'B1 RELEASE_OK:       %s\n' "$([[ -f "${TOP52_STAGING}/RELEASE_OK.json" ]] && echo yes || echo no)"
  printf 'wave plan:           %s\n' "$([[ -f "${DL_ROOT}/plan/waves/wave_plan.json" ]] && echo yes || echo no)"
  df -h /home | tail -1
}

cmd_plan_waves() {
  [[ -f "${CITYWIDE}/ct05_catalog_v1/merged/.done" ]] || die "E2 merge not PASS (merged/.done missing)"
  mkdir -p "${DL_ROOT}/plan"
  local src="${CITYWIDE}/ct05_catalog_v1/plan/remaining_non_top52_anchors.csv"
  local dst="${DL_ROOT}/plan/remaining_non_top52_anchors.csv"
  local roster="${DL_ROOT}/plan/remaining_non_top52_roster.csv"
  [[ -f "${src}" ]] || die "missing ${src}"
  if [[ ! -f "${dst}" ]] || ! cmp -s "${src}" "${dst}"; then
    cp -a "${src}" "${dst}"
    log "copied remaining anchors -> ${dst}"
  fi
  # roster: unique source_grid, sorted (deterministic), with anchor counts
  "${PY}" - "${dst}" "${roster}" <<'PY'
import csv, sys
from collections import Counter
src, roster = sys.argv[1], sys.argv[2]
with open(src, newline="") as fh:
    rows = list(csv.DictReader(fh))
counts = Counter(row["source_grid"] for row in rows)
with open(roster, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["source_grid", "anchor_count"])
    for grid in sorted(counts):
        w.writerow([grid, counts[grid]])
print(f"roster: {len(counts)} grids, {sum(counts.values())} anchors -> {roster}")
PY
  "${PY}" "${REPO_ROOT}/scripts/temporal/plan_ct52_production_waves.py" \
    --anchors-csv "${dst}" \
    --roster-csv "${roster}" \
    --output-dir "${DL_ROOT}/plan/waves" \
    --max-anchors 3500 \
    --no-canary
  # stamp citywide schema id onto the frozen plan
  "${PY}" - "${DL_ROOT}/plan/waves/wave_plan.json" <<'PY'
import json, sys
p = sys.argv[1]
plan = json.load(open(p))
plan["schema_version"] = "ct_citywide_download_wave_plan_v2"
plan["route_weights"] = "home_v4=2,home_v6=2,koko_v4=1"
json.dump(plan, open(p, "w"), indent=2, sort_keys=True)
print(f"wave plan: {plan['wave_count']} waves, {plan['anchor_count']} anchors")
PY
}

cmd_plan_wave_shards() {
  local n="${1:?wave number required}"
  local nn; nn=$(printf '%02d' "${n}")
  local wave_plan="${DL_ROOT}/plan/waves/wave_${nn}"
  local wave_run="${CITYWIDE}/wave_${nn}"
  local catalog="${CITYWIDE}/ct05_catalog_v1/merged/gehi_vintage_candidates_ct05_run3_2019plus.csv"
  [[ -f "${wave_plan}/wave_${nn}_all.csv" ]] || die "missing wave plan ${wave_plan} (run plan-waves)"
  [[ -f "${catalog}" ]] || die "missing merged 2019+ catalog ${catalog}"

  "${PY}" "${REPO_ROOT}/scripts/temporal/filter_wave_candidates.py" \
    --catalog-csv "${catalog}" \
    --wave-csv "${wave_plan}/wave_${nn}_all.csv" \
    --out-dir "${wave_plan}"

  "${PY}" "${REPO_ROOT}/scripts/temporal/run_ct05_chip_pipeline.py" plan \
    --candidates-csv "${wave_plan}/candidates_2019plus.csv" \
    --out-dir "${wave_plan}" \
    --routes "${ROUTES}" \
    --route-weights "${ROUTE_WEIGHTS}" \
    --min-date 2019-01-01

  local route
  for route in ${TM3_ROUTES}; do
    "${PY}" "${REPO_ROOT}/scripts/temporal/plan_ct05_tm3.py" \
      --candidates-csv "${wave_plan}/shards/${route}_tm_z19.csv" \
      --route-id "${route}" --lanes 3 \
      --out-dir "${wave_plan}/tm3/${route}"
  done

  # assemble wave RUN_ROOT (dirname(wave_run)=CITYWIDE -> groups_v1 resolves)
  mkdir -p "${wave_run}/plan" "${wave_run}/chips" "${wave_run}/lanes"
  rm -rf "${wave_run}/plan/shards" "${wave_run}/plan/tm3"
  cp -a "${wave_plan}/shards" "${wave_run}/plan/shards"
  cp -a "${wave_plan}/tm3" "${wave_run}/plan/tm3"

  # launch assert: no out-of-wave anchors in any shard
  "${PY}" - "${wave_plan}/wave_${nn}_all.csv" "${wave_run}/plan/shards" <<'PY'
import csv, sys
from pathlib import Path
wave_csv, shard_dir = Path(sys.argv[1]), Path(sys.argv[2])
wave_ids = {r["anchor_id"] for r in csv.DictReader(wave_csv.open())}
total = 0
for shard in sorted(shard_dir.glob("*.csv")):
    ids = {r["anchor_id"] for r in csv.DictReader(shard.open())}
    total += sum(1 for r in csv.DictReader(shard.open()))
    bad = ids - wave_ids
    if bad:
        raise SystemExit(f"shard {shard.name} has {len(bad)} out-of-wave anchors")
print(f"shard assert ok: {total} candidate rows, all within wave")
PY
  log "wave_${nn} shards assembled at ${wave_run}"
}

cmd_launch_wave() {
  local n="${1:?wave number required}"
  local nn; nn=$(printf '%02d' "${n}")
  local wave_run="${CITYWIDE}/wave_${nn}"
  require_tokens
  [[ -f "${CITYWIDE}/ct05_catalog_v1/merged/.done" ]] || die "E2 merge not PASS"
  # KD7: Top-52 archive must be verified restorable before citywide pixels.
  # RELEASE may still be trailing (hours-long lifecycle pass); wave_01 peak
  # (~24G) fits the 89G pre-release headroom, so drill is the hard gate.
  [[ -f "${TOP52_STAGING}/BACKUP_OK.json" ]] || die "B1 BACKUP_OK missing"
  [[ -f "${TOP52_STAGING}/RESTORE_DRILL_OK.json" ]] || die "B1 RESTORE_DRILL_OK missing"
  if [[ ! -f "${TOP52_STAGING}/RELEASE_OK.json" ]]; then
    log "WARNING: Top-52 RELEASE_OK missing; proceeding (recoverability proven, headroom ok)"
  fi
  [[ -d "${wave_run}/plan/shards" ]] || die "wave_${nn} shards not assembled (run plan-wave-shards ${n})"

  export REQUEST_INTERVAL

  # koko: groups_v1 sync (idempotent) + wave plan rsync, then light lane-only
  log "sync groups_v1 + wave_${nn} plan to koko"
  ssh -o BatchMode=yes "${KOKO_SSH}" "mkdir -p '${KOKO_CITYWIDE}/groups_v1'"
  rsync -a "${CITYWIDE}/groups_v1/chip_groups_as_anchors.csv" \
    "${KOKO_SSH}:${KOKO_CITYWIDE}/groups_v1/"
  ssh -o BatchMode=yes "${KOKO_SSH}" "mkdir -p '${KOKO_CITYWIDE}/wave_${nn}/chips' '${KOKO_CITYWIDE}/wave_${nn}/lanes'"
  rsync -a --delete "${wave_run}/plan/" "${KOKO_SSH}:${KOKO_CITYWIDE}/wave_${nn}/plan/"

  local route
  for route in ${HOME_ROUTES}; do
    mkdir -p "${wave_run}/lanes/${route}"
    log "launch home ${route} lane (skip-tm-z19 knob) + TM3"
    CT05_LANE_SKIP_TM_Z19=1 REQUEST_INTERVAL="${REQUEST_INTERVAL}" \
      setsid nohup bash "${REPO_ROOT}/scripts/temporal/run_ct05_download_lane.sh" \
      "${route}" "${wave_run}" >> "${wave_run}/lanes/${route}/launch.log" 2>&1 &
    REQUEST_INTERVAL="${REQUEST_INTERVAL}" \
      setsid nohup bash "${REPO_ROOT}/scripts/temporal/run_ct05_tm3_route.sh" \
      "${route}" "${wave_run}" "" >> "${wave_run}/lanes/${route}/tm3_launch.log" 2>&1 &
  done

  log "launch koko_v4 light lane (1 process, nice/ionice, lane-only no TM3)"
  # shellcheck disable=SC2087
  ssh -o BatchMode=yes "${KOKO_SSH}" bash <<EOF
set -euo pipefail
RUN="${KOKO_CITYWIDE}/wave_${nn}"
mkdir -p "\${RUN}/lanes/koko_v4"
cd /home/koko/projects/solar_backdating
REQUEST_INTERVAL=${REQUEST_INTERVAL} setsid nohup \
  nice -n 19 ionice -c3 bash scripts/temporal/run_ct05_download_lane.sh koko_v4 "\${RUN}" \
  >> "\${RUN}/lanes/koko_v4/launch.log" 2>&1 &
echo "koko lane pid=\$!"
EOF
  log "wave_${nn} launched: home={home_v4,home_v6}+TM3, koko_v4 light; box disabled"
}

# --- shared wave helpers ---------------------------------------------------

wave_meta() { printf '%s/waves/wave_%02d' "${DL_ROOT}" "$1"; }

hot_wave_count() {
  local count=0 meta
  for meta in "${DL_ROOT}"/waves/wave_*/; do
    [[ -f "${meta}merge/MERGE_OK.json" ]] || continue
    [[ -f "${meta}RELEASE_OK.json" ]] && continue
    count=$((count + 1))
  done
  printf '%s' "${count}"
}

home_avail_bytes() { df -B1 --output=avail /home | tail -1 | tr -d ' '; }

enforce_hotset_gate() {
  # 2-wave hot-set gate (rev-6 §5): enforced at merge/stage entry.
  local max_waves avail floor1 floor2 fs
  max_waves="$(${PY} -c "import json;print(json.load(open('${PIPELINE_DIR}/waterlines.json'))['home_hot_max_waves'])")"
  fs="$(${PY} -c "import json;print(json.load(open('${PIPELINE_DIR}/waterlines.json'))['fs_size_bytes'])")"
  floor1="$(${PY} -c "import json;w=json.load(open('${PIPELINE_DIR}/waterlines.json'));print(int(w['home_avail_floor_frac']*w['fs_size_bytes']))")"
  floor2="$(${PY} -c "import json;w=json.load(open('${PIPELINE_DIR}/waterlines.json'));print(int(w['home_hot_max_waves']*w['wave_bytes']+w['home_reserve_bytes']))")"
  avail="$(home_avail_bytes)"
  local hot; hot="$(hot_wave_count)"
  if (( hot >= max_waves )); then
    die "hot-set full (${hot}>=${max_waves} waves); release a scored wave first"
  fi
  if (( avail < floor1 || avail < floor2 )); then
    die "home avail ${avail} below waterline (floors ${floor1}/${floor2})"
  fi
  log "hotset gate ok: hot=${hot}/${max_waves} avail=$((avail / 1073741824))GiB"
}

cmd_merge_wave() {
  local n="${1:?wave number required}"
  local nn; nn=$(printf '%02d' "${n}")
  local wave_run="${CITYWIDE}/wave_${nn}"
  local meta; meta="$(wave_meta "${n}")"
  enforce_hotset_gate
  mkdir -p "${meta}/lanes_merged" "${meta}/merge" "${DL_ROOT}/chips"

  log "merge home work chips -> \$DL_ROOT/chips"
  rsync -a --remove-source-files "${wave_run}/chips/" "${DL_ROOT}/chips/"
  find "${wave_run}/chips" -depth -type d -empty -delete 2>/dev/null || true
  mkdir -p "${meta}/lanes_merged/home"
  rsync -a --include '*/' --include '*_manifest.csv' --exclude '*' \
    "${wave_run}/lanes/" "${meta}/lanes_merged/home/"

  log "merge koko work chips -> \$DL_ROOT/chips (remove sources on koko)"
  rsync -ac --remove-source-files \
    "${KOKO_SSH}:${KOKO_CITYWIDE}/wave_${nn}/chips/" "${DL_ROOT}/chips/"
  ssh -o BatchMode=yes "${KOKO_SSH}" \
    "find '${KOKO_CITYWIDE}/wave_${nn}/chips' -depth -type d -empty -delete 2>/dev/null || true"
  mkdir -p "${meta}/lanes_merged/koko"
  rsync -a --include '*/' --include '*_manifest.csv' --exclude '*' \
    "${KOKO_SSH}:${KOKO_CITYWIDE}/wave_${nn}/lanes/" "${meta}/lanes_merged/koko/"

  log "rewrite manifest paths -> ${DL_ROOT}/chips"
  "${PY}" "${REPO_ROOT}/scripts/temporal/merge_rewrite_manifest_paths.py" \
    --manifests-root "${meta}/lanes_merged" \
    --home-chips-root "${DL_ROOT}/chips"

  log "membership audit"
  "${PY}" "${REPO_ROOT}/scripts/temporal/verify_wave_membership.py" \
    --membership "${DL_ROOT}/plan/waves/wave_${nn}/membership_expected.json" \
    --manifests-root "${meta}/lanes_merged" \
    --out "${meta}/merge/membership_audit.json" \
    || die "membership audit FAILED (see membership_audit.json); work chips retained"

  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${meta}/merge/MERGE_OK.json"
  log "MERGE_OK wave_${nn}"
}

cmd_qa_wave() {
  local n="${1:?wave number required}"
  local nn; nn=$(printf '%02d' "${n}")
  local meta; meta="$(wave_meta "${n}")"
  [[ -f "${meta}/merge/MERGE_OK.json" ]] || die "MERGE_OK missing for wave_${nn}"
  local args=()
  while IFS= read -r m; do args+=(--manifest "${m}"); done \
    < <(find "${meta}/lanes_merged" -type f -name '*_manifest.csv' | sort)
  [[ "${#args[@]}" -gt 0 ]] || die "no merged manifests"
  "${PY}" "${REPO_ROOT}/scripts/temporal/run_ct05_chip_pipeline.py" qa \
    --candidates-csv "${DL_ROOT}/plan/waves/wave_${nn}/candidates_2019plus.csv" \
    --anchors-csv "${DL_ROOT}/plan/waves/wave_${nn}/wave_${nn}_all.csv" \
    "${args[@]}" \
    --out-dir "${meta}/qa"
  "${PY}" - "${meta}/qa/summary.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
rate = s["release_eligible_count"] / max(s["candidate_count"], 1)
print(f"qa admit rate: {rate:.4f} ({s['release_eligible_count']}/{s['candidate_count']})")
sys.exit(0 if rate >= 0.99 else 1)
PY
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${meta}/qa/.done"
  log "QA PASS wave_${nn}"
}

cmd_archive_wave() {
  local n="${1:?wave number required}"
  local nn; nn=$(printf '%02d' "${n}")
  local meta; meta="$(wave_meta "${n}")"
  local staging="${DL_ROOT}/archive_staging/wave_${nn}"
  local dbx="dropbox:zasolar_backdating/cape_town_citywide_v1/waves/wave_${nn}"
  [[ -f "${meta}/qa/.done" ]] || die "qa/.done missing for wave_${nn}"
  mkdir -p "${staging}"
  # members = wave anchor dirs present in the hot set
  cut -d, -f1 "${DL_ROOT}/plan/waves/wave_${nn}/wave_${nn}_all.csv" | tail -n +2 \
    > "${staging}/members.txt"
  log "tar wave_${nn} ($(wc -l < "${staging}/members.txt") anchors)"
  tar -C "${DL_ROOT}/chips" -cf "${staging}/wave_${nn}.tar" -T "${staging}/members.txt"
  (cd "${staging}" && sha256sum "wave_${nn}.tar" > "wave_${nn}.tar.sha256")
  log "rclone -> ${dbx}"
  rclone copyto "${staging}/wave_${nn}.tar" "${dbx}/wave_${nn}.tar" --checksum \
    --stats-one-line --stats 60s
  rclone check "${staging}" "${dbx}" --include "wave_${nn}.tar" --one-way
  local digest size
  digest="$(cut -d' ' -f1 "${staging}/wave_${nn}.tar.sha256")"
  size="$(stat -c%s "${staging}/wave_${nn}.tar")"
  rm -f "${staging}/wave_${nn}.tar"
  "${PY}" - "${meta}/BACKUP_OK.json" "${dbx}" "${nn}" "${digest}" "${size}" <<'PY'
import json, sys
from datetime import datetime, timezone
out, dbx, nn, digest, size = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
payload = {
    "schema_version": "ct_citywide_backup_ok_v1",
    "archive_kind": "wave_chips",
    "wave": nn,
    "archive_root": dbx,
    "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "volumes": [{"volume": f"wave_{nn}.tar", "dropbox_path": f"{dbx}/wave_{nn}.tar",
                 "gnu_sha256": digest, "bytes": size}],
    "verify": "rclone copyto --checksum + rclone check --one-way at upload; sha256sum -c at restore",
}
open(out, "w").write(json.dumps(payload, indent=2) + "\n")
print(f"BACKUP_OK wave_{nn}: {size/2**30:.1f} GiB")
PY
  rclone copyto "${meta}/BACKUP_OK.json" "${dbx}/BACKUP_OK.json"
  log "wave_${nn} archived -> staged (chips stay on home for scoring)"
}

cmd_prepare_score_lock() {
  require_tokens
  local score_root="${CITYWIDE}/production_gemini_v1"
  local catalog="${CITYWIDE}/ct05_catalog_v1/merged/gehi_vintage_candidates_ct05_run3_2019plus.csv"
  [[ -f "${catalog}" ]] || die "merged catalog missing"
  "${PY}" "${REPO_ROOT}/scripts/temporal/prepare_ct_runtime_lock_v2.py" \
    --run-root "${score_root}" \
    --anchors-csv "${DL_ROOT}/plan/remaining_non_top52_anchors.csv" \
    --grid-roster-csv "${DL_ROOT}/plan/remaining_non_top52_roster.csv" \
    --catalog-csv "${catalog}" \
    --outcomes-csv "${CITYWIDE}/ct05_catalog_v1/merged/anchor_catalog_outcomes.csv" \
    --chip-root "${DL_ROOT}/chips" \
    --expected-anchor-count 90348 \
    --workers 40 --qps 6 --max-in-flight 0 \
    --projected-logical-calls 240000
}

cmd_score_wave() {
  local n="${1:?wave number required}"
  local nn; nn=$(printf '%02d' "${n}")
  local meta; meta="$(wave_meta "${n}")"
  require_tokens
  # staged gate: BACKUP_OK + qa/.done + not released
  [[ -f "${meta}/BACKUP_OK.json" ]] || die "wave_${nn} not staged (BACKUP_OK missing)"
  [[ -f "${meta}/qa/.done" ]] || die "wave_${nn} qa not done"
  [[ ! -f "${meta}/RELEASE_OK.json" ]] || die "wave_${nn} already released"
  local score_root="${CITYWIDE}/production_gemini_v1"
  local catalog="${CITYWIDE}/ct05_catalog_v1/merged/gehi_vintage_candidates_ct05_run3_2019plus.csv"
  local arm extent
  for arm in A24 A48; do
    extent=24; [[ "${arm}" == "A48" ]] && extent=48
    local arm_csv="${DL_ROOT}/plan/waves/wave_${nn}/wave_${nn}_${arm}.csv"
    [[ -s "${arm_csv}" ]] || { log "skip ${arm} (empty)"; continue; }
    log "score wave_${nn} ${arm}"
    "${PY}" -u "${REPO_ROOT}/scripts/temporal/run_adaptive_scan.py" \
      --anchors-csv "${arm_csv}" \
      --scan-states-dir "${score_root}/primary/scan_states/${arm,,}" \
      --chips-dir "${score_root}/primary/unused_merged_chips" \
      --merged-tm-chips-dir "${DL_ROOT}/chips" \
      --merged-wayback-chips-dir "${DL_ROOT}/chips" \
      --audit-dir "${score_root}/primary/audit" \
      --provider Merged --scorer gemini \
      --anchor-workers 40 --qps 6 \
      --round1-model gemini-3.1-flash-lite \
      --round2-model gemini-3.1-flash-lite \
      --troubleshooting-model "" \
      --cheap-round-types initial,bisection,walk_back,tail,anchor_recovery \
      --routing-salt-mode target \
      --verdict-store "${score_root}/primary/verdict_store.jsonl" \
      --review-extent-m "${extent}" \
      --census-mid-date-override 2025-01-31 \
      --offline-tm-catalog-csv "${catalog}" --offline-wayback \
      --no-live-gehi --offline-require-chip-on-disk \
      --catalog-cache-dir "${score_root}/primary/catalog_cache" \
      2>&1 | tee -a "${score_root}/primary/logs/wave_${nn}_${arm,,}.log"
  done
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${meta}/SCORE_OK.json"
  log "SCORE_OK wave_${nn}"
}

cmd_release_wave() {
  local n="${1:?wave number required}"
  local nn; nn=$(printf '%02d' "${n}")
  local meta; meta="$(wave_meta "${n}")"
  [[ -f "${meta}/BACKUP_OK.json" ]] || die "BACKUP_OK missing (refuse release)"
  [[ -f "${meta}/SCORE_OK.json" ]] || die "SCORE_OK missing (refuse release of unscored wave)"
  "${PY}" "${REPO_ROOT}/scripts/temporal/chip_lifecycle.py" release \
    --run-dir "${DL_ROOT}/chips" \
    --anchor-ids-file "${DL_ROOT}/plan/waves/wave_${nn}/wave_${nn}_all.csv" \
    --backup-ok "${meta}/BACKUP_OK.json"
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${meta}/RELEASE_OK.json"
  log "RELEASE_OK wave_${nn}; df: $(df -h /home | tail -1)"
}

cmd_status() {
  cmd_gate
  echo "--- b1 upload:"
  bash "${REPO_ROOT}/scripts/temporal/b1_top52_dropbox_archive.sh" status || true
  echo "--- wave processes:"
  pgrep -af "run_ct05_download_lane|run_ct05_tm3_route|gehi_download" | head -20 || echo "(none)"
  echo "--- recent wave logs:"
  for logf in "${CITYWIDE}"/wave_*/lanes/*/launch.log; do
    [[ -f "${logf}" ]] || continue
    echo "== ${logf}"
    tail -2 "${logf}"
  done
}

cmd="${1:-}"
case "${cmd}" in
  gate) cmd_gate ;;
  plan-waves) cmd_plan_waves ;;
  plan-wave-shards) shift; cmd_plan_wave_shards "$@" ;;
  launch-wave) shift; cmd_launch_wave "$@" ;;
  status) cmd_status ;;
  merge-wave) shift; cmd_merge_wave "$@" ;;
  qa-wave) shift; cmd_qa_wave "$@" ;;
  archive-wave) shift; cmd_archive_wave "$@" ;;
  prepare-score-lock) cmd_prepare_score_lock ;;
  score-wave) shift; cmd_score_wave "$@" ;;
  release-wave) shift; cmd_release_wave "$@" ;;
  stage-wave) die "stage-wave (Dropbox restore) not yet implemented; chips are staged by archive-wave by default" ;;
  *) echo "usage: $0 {gate|plan-waves|plan-wave-shards N|launch-wave N|merge-wave N|qa-wave N|archive-wave N|prepare-score-lock|score-wave N|release-wave N|status}" >&2; exit 2 ;;
esac
