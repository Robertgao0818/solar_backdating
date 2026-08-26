#!/usr/bin/env bash
# B1 (rev-6): Top-52 chips -> per-volume tar -> Dropbox (rclone) -> per-volume
# verify -> delete local staging volume. NON-DESTRUCTIVE: never touches the
# source chips tree. Release is a separate, token-gated step.
#
# Contract (RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18 rev-6 §B1):
#   - volumes <= archive_volume_target_bytes (15.5 GiB), paths sorted so each
#     anchor stays whole inside one volume
#   - hash per VOLUME only (no per-tile hashes)
#   - upload verify = rclone copyto --checksum + rclone check --one-way
#   - local staging .tar deleted only after remote verify PASS
#   - resumable: volumes listed in volumes.done are skipped
#   - output: BACKUP_OK.json (volumes + bytes + gnu sha256 + dropbox path)
#
# Usage:
#   bash scripts/temporal/b1_top52_dropbox_archive.sh plan     # build partNN.members.txt
#   bash scripts/temporal/b1_top52_dropbox_archive.sh upload   # tar+upload loop (tmux!)
#   bash scripts/temporal/b1_top52_dropbox_archive.sh status
set -euo pipefail

TOP52="${HOME}/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
CHIPS="${TOP52}/ct05_download_v1/chips"
STAGING="${TOP52}/ct05_download_v1/archive_staging"
DBX="${DROPBOX_REMOTE:-dropbox:}zasolar_backdating/cape_town_citywide_v1/top52_chips_20260818"
TARGET_BYTES=16642998272   # 15.5 GiB, hard max 16 GiB per waterlines
SIZES_TSV="${STAGING}/all_files.sizes.tsv"
DONE_LIST="${STAGING}/volumes.done"
SHA_FILE="${STAGING}/volumes.sha256"

cmd="${1:-}"
[[ -n "${cmd}" ]] || { echo "usage: $0 {plan|upload|drill|release|status}" >&2; exit 2; }

cmd_plan() {
  [[ -s "${SIZES_TSV}" ]] || { echo "missing ${SIZES_TSV} (find still running?)" >&2; exit 1; }
  python3 - "${SIZES_TSV}" "${STAGING}" "${TARGET_BYTES}" <<'PY'
import sys
from pathlib import Path

sizes_tsv, staging, target = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
rows = []
with sizes_tsv.open() as fh:
    for line in fh:
        sz, rel = line.rstrip("\n").split("\t", 1)
        rows.append((int(sz), rel))
rows.sort(key=lambda r: r[1])  # path order keeps anchors/grids contiguous
parts, cur, cur_bytes = [], [], 0
for sz, rel in rows:
    if cur and cur_bytes + sz > target:
        parts.append(cur); cur, cur_bytes = [], 0
    cur.append(rel); cur_bytes += sz
if cur:
    parts.append(cur)
total = sum(sz for sz, _ in rows)
for i, members in enumerate(parts, 1):
    p = staging / f"part{i:02d}.members.txt"
    p.write_text("\n".join(members) + "\n", encoding="utf-8")
    print(f"{p.name}: {len(members)} files")
print(f"TOTAL: {len(rows)} files, {total/2**30:.1f} GiB, {len(parts)} volumes")
PY
}

cmd_upload() {
  [[ -f "${STAGING}/part01.members.txt" ]] || { echo "run plan first" >&2; exit 1; }
  touch "${DONE_LIST}" "${SHA_FILE}"
  shopt -s nullglob
  for members in "${STAGING}"/part*.members.txt; do
    part="$(basename "${members}" .members.txt)"
    if grep -qx "${part}" "${DONE_LIST}"; then
      echo "[skip] ${part} already done"
      continue
    fi
    tar_file="${STAGING}/${part}.tar"
    echo "[tar ] ${part} ($(wc -l < "${members}") files) $(date -u +%H:%M:%SZ)"
    tar -C "${CHIPS}" -cf "${tar_file}" -T "${members}"
    echo "[hash] ${part}"
    (cd "${STAGING}" && sha256sum "${part}.tar") >> "${SHA_FILE}"
    echo "[up  ] ${part} -> ${DBX} $(date -u +%H:%M:%SZ)"
    rclone copyto "${tar_file}" "${DBX}/${part}.tar" --checksum --stats-one-line --stats 60s
    echo "[chk ] ${part}"
    rclone check "${STAGING}" "${DBX}" --include "${part}.tar" --one-way
    rm -f "${tar_file}"
    echo "${part}" >> "${DONE_LIST}"
    echo "[ok  ] ${part} $(date -u +%H:%M:%SZ)"
  done
  # all volumes done -> BACKUP_OK.json
  STAGING="${STAGING}" DBX="${DBX}" python3 - <<'PY'
import hashlib, json, os, subprocess
from pathlib import Path

staging = Path(os.environ["STAGING"]); dbx = os.environ["DBX"]
done = staging.joinpath("volumes.done").read_text().split()
sha = {}
for line in staging.joinpath("volumes.sha256").read_text().splitlines():
    digest, name = line.split(None, 1)
    sha[name] = digest
volumes = []
for part in done:
    name = f"{part}.tar"
    members = staging / f"{part}.members.txt"
    volumes.append({
        "volume": name,
        "dropbox_path": f"{dbx}/{name}",
        "gnu_sha256": sha[name],
        "member_count": sum(1 for _ in members.open()),
    })
payload = {
    "schema_version": "ct_citywide_backup_ok_v1",
    "archive_kind": "top52_chips",
    "archive_root": dbx,
    "volume_count": len(volumes),
    "volumes": volumes,
    "verify": "rclone copyto --checksum + rclone check --one-way at upload; sha256sum -c at restore",
}
out = staging / "BACKUP_OK.json"
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
subprocess.run(["rclone", "copyto", str(out), f"{dbx}/BACKUP_OK.json"], check=True)
print(f"BACKUP_OK written: {out} (+ dropbox copy)")
PY
}

cmd_drill() {
  # Restore drill (rev-6 B1): prove the Dropbox archive round-trips BEFORE
  # any release. Pulls part01 back, verifies the volume sha256, extracts a
  # deterministic sample, byte-compares against the live originals.
  [[ -f "${STAGING}/BACKUP_OK.json" ]] || { echo "BACKUP_OK.json missing (upload not finished)" >&2; exit 2; }
  local drill_dir="${DRILL_DIR:-/tmp/top52_restore_drill}"
  local sample_count="${DRILL_SAMPLE_COUNT:-200}"
  rm -rf "${drill_dir}"
  mkdir -p "${drill_dir}/extract"
  echo "[drill] fetch part01.tar from ${DBX}"
  rclone copyto "${DBX}/part01.tar" "${drill_dir}/part01.tar" --checksum --stats-one-line --stats 30s
  echo "[drill] sha256 -c"
  (cd "${drill_dir}" && grep 'part01.tar$' "${SHA_FILE}" | sha256sum -c -)
  # deterministic sample: evenly spaced members of part01
  python3 - "${STAGING}/part01.members.txt" "${drill_dir}/sample.txt" "${sample_count}" <<'PY'
import sys
members = open(sys.argv[1]).read().splitlines()
n = int(sys.argv[3])
step = max(1, len(members) // n)
sample = members[::step][:n]
open(sys.argv[2], "w").write("\n".join(sample) + "\n")
print(f"[drill] sample: {len(sample)} of {len(members)} members")
PY
  echo "[drill] extract sample"
  tar -C "${drill_dir}/extract" -xf "${drill_dir}/part01.tar" -T "${drill_dir}/sample.txt"
  echo "[drill] byte-compare vs originals"
  local fails=0 total=0
  while IFS= read -r rel; do
    total=$((total + 1))
    if ! cmp -s "${drill_dir}/extract/${rel}" "${CHIPS}/${rel}"; then
      echo "[drill] MISMATCH: ${rel}" >&2
      fails=$((fails + 1))
    fi
  done < "${drill_dir}/sample.txt"
  [[ "${fails}" -eq 0 ]] || { echo "[drill] FAILED: ${fails}/${total} mismatches" >&2; exit 1; }
  python3 - "${STAGING}" "${total}" <<'PY'
import json, sys
from datetime import datetime, timezone
staging, total = sys.argv[1], int(sys.argv[2])
payload = {
    "schema_version": "ct_citywide_restore_drill_ok_v1",
    "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "volume": "part01.tar",
    "sample_count": total,
    "byte_compare": "cmp vs live originals, 0 mismatches",
}
open(f"{staging}/RESTORE_DRILL_OK.json", "w").write(json.dumps(payload, indent=2) + "\n")
print(f"[drill] PASS ({total} files) -> RESTORE_DRILL_OK.json")
PY
  rm -rf "${drill_dir}"
}

cmd_release() {
  # Destructive step (rev-6 KD6/KD7): delete Top-52 chip tifs from home.
  # Hard gates: E0+E6 tokens + BACKUP_OK + RESTORE_DRILL_OK. Designed to be
  # run inside tmux (lifecycle hash-backfill over ~1.2M files takes hours).
  local decisions="/home/gao/projects/solar_backdating/docs/replan_v2/OWNER_DECISIONS.md"
  grep -qE '^E0_PREFETCH_DISK_SIGNED=[0-9]{4}-[0-9]{2}-[0-9]{2}$' "${decisions}" \
    || { echo "E0 token missing; refuse release" >&2; exit 2; }
  grep -qE '^E6_SCORER_GO=[0-9]{4}-[0-9]{2}-[0-9]{2}$' "${decisions}" \
    || { echo "E6 token missing; refuse release" >&2; exit 2; }
  [[ -f "${STAGING}/BACKUP_OK.json" ]] || { echo "BACKUP_OK missing" >&2; exit 2; }
  [[ -f "${STAGING}/RESTORE_DRILL_OK.json" ]] || { echo "RESTORE_DRILL_OK missing" >&2; exit 2; }
  [[ -f "${STAGING}/RELEASE_OK.json" ]] && { echo "already released"; exit 0; }
  local py="/home/gao/projects/ZAsolar/.venv/bin/python"
  echo "[release] starting $(date -u +%Y-%m-%dT%H:%M:%SZ); expect hours (1.2M file hash backfill)"
  "${py}" /home/gao/projects/solar_backdating/scripts/temporal/chip_lifecycle.py release \
    --run-dir "${CHIPS}" \
    --backup-ok "${STAGING}/BACKUP_OK.json"
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${STAGING}/RELEASE_OK.json"
  echo "[release] RELEASE_OK written; df now:"
  df -h /home | tail -1
  # koko redundant Top-52 work copy (B0 sample-verified redundant 2026-08-18)
  echo "[release] deleting koko redundant Top-52 work copy (23G)"
  ssh -o BatchMode=yes koko@koko-82xm \
    "rm -rf /home/koko/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/ct05_download_v1/chips" \
    && echo "[release] koko copy deleted" || echo "[release] WARNING: koko delete failed; retry manually"
}

cmd_status() {
  echo "done volumes: $(grep -c . "${DONE_LIST}" 2>/dev/null || echo 0)"
  ls "${STAGING}"/part*.members.txt 2>/dev/null | wc -l | xargs echo "planned volumes:"
  df -h /home | tail -1
  [[ -f "${STAGING}/BACKUP_OK.json" ]] && echo "BACKUP_OK: yes" || echo "BACKUP_OK: no"
}

case "${cmd}" in
  plan) cmd_plan ;;
  upload) cmd_upload ;;
  drill) cmd_drill ;;
  release) cmd_release ;;
  status) cmd_status ;;
  *) echo "unknown cmd ${cmd}" >&2; exit 2 ;;
esac
