#!/usr/bin/env bash
# Leg-E E2: Cape Town citywide GEHI catalog probe (plan / probe / merge / status).
#
# Design:
#   - Reuses Top-52 route outcomes as seed (same anchor_id + identical 96 m geometry).
#   - Probes only the 90,348 non-Top-52 anchors, split into N stable lanes.
#   - All long work is meant for tmux; this script is resumable (probe skips
#     completed query keys already present in query_outcomes.jsonl).
#
# Usage:
#   bash scripts/temporal/run_ct_citywide_catalog_e2.sh plan
#   bash scripts/temporal/run_ct_citywide_catalog_e2.sh launch   # tmux lanes
#   bash scripts/temporal/run_ct_citywide_catalog_e2.sh status
#   bash scripts/temporal/run_ct_citywide_catalog_e2.sh merge
#   bash scripts/temporal/run_ct_citywide_catalog_e2.sh coverage

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/activate_env.sh"

RUN_ROOT="${CT_CITYWIDE_RUN_ROOT:-${HOME}/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812}"
GROUPS_CSV="${RUN_ROOT}/groups_v1/chip_groups_as_anchors.csv"
CATALOG_ROOT="${RUN_ROOT}/ct05_catalog_v1"
TOP52_CATALOG="${HOME}/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/ct05_catalog_v1"
TOP52_MANIFEST="${HOME}/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/manifests/ct_top52_manifest_v1.csv"
LANES="${CT_CITYWIDE_CATALOG_LANES:-16}"
REQUEST_INTERVAL="${CT_CITYWIDE_REQUEST_INTERVAL:-0.8}"
TMUX_PREFIX="${CT_CITYWIDE_TMUX_PREFIX:-ct_cw_cat}"
EXTRAPOLATED_2019PLUS=6140000  # plan §E2 ≈6.14M from Top-52 density

cmd="${1:-}"
if [[ -z "${cmd}" ]]; then
  echo "usage: $0 {plan|launch|status|merge|coverage|smoke}" >&2
  exit 2
fi

require_groups() {
  if [[ ! -f "${GROUPS_CSV}" ]]; then
    echo "missing groups CSV: ${GROUPS_CSV}" >&2
    exit 1
  fi
}

build_remaining_anchors() {
  require_groups
  mkdir -p "${CATALOG_ROOT}/plan" "${CATALOG_ROOT}/routes_new" "${CATALOG_ROOT}/logs"
  python3 - <<'PY'
from pathlib import Path
import json
import hashlib
import pandas as pd
from scripts.temporal.geid_temporal_common import write_csv_rows

run = Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
catalog = run / "ct05_catalog_v1"
groups = pd.read_csv(run / "groups_v1/chip_groups_as_anchors.csv")
top = pd.read_csv(
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
    / "manifests/ct_top52_manifest_v1.csv",
    usecols=["source_feature_id", "anchor_id"],
)
top_ids = set(top.anchor_id.astype(str))
remaining = groups[~groups.anchor_id.astype(str).isin(top_ids)].copy()
assert len(groups) == 111801
assert len(top) == 21453
assert len(remaining) == 111801 - 21453, len(remaining)

out = catalog / "plan" / "remaining_non_top52_anchors.csv"
remaining.to_csv(out, index=False, lineterminator="\n")

# Stable lane assignment independent of route: hash(anchor_id) % lanes
lanes = int(__import__("os").environ.get("CT_CITYWIDE_CATALOG_LANES", "16"))
lane_rows = {i: [] for i in range(lanes)}
for row in remaining.to_dict(orient="records"):
    digest = hashlib.sha256(str(row["anchor_id"]).encode("utf-8")).digest()
    lane = int.from_bytes(digest[:8], "big") % lanes
    lane_rows[lane].append(row)

lane_dir = catalog / "plan" / "lanes"
lane_dir.mkdir(parents=True, exist_ok=True)
meta = []
for i in range(lanes):
    path = lane_dir / f"lane{i+1:02d}.csv"
    rows = lane_rows[i]
    if rows:
        write_csv_rows(path, rows, list(remaining.columns))
    else:
        path.write_text(",".join(remaining.columns) + "\n", encoding="utf-8")
    meta.append(
        {
            "lane_id": f"lane{i+1:02d}",
            "route_id": f"citywide_lane{i+1:02d}",
            "anchor_count": len(rows),
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )

plan = {
    "schema_version": "ct_citywide_catalog_plan_v1",
    "citywide_anchor_count": int(len(groups)),
    "top52_seed_anchor_count": int(len(top_ids)),
    "remaining_anchor_count": int(len(remaining)),
    "lanes": lanes,
    "remaining_anchors_csv": str(out),
    "lane_shards": meta,
    "top52_catalog_root": str(
        Path.home()
        / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/ct05_catalog_v1"
    ),
    "notes": [
        "Top-52 outcomes reused as seed (identical anchor_id + 96 m geometry).",
        "New lanes probe only non-Top-52 anchors.",
        "merge combines --route-root top52 routes + routes_resumed + routes_new.",
    ],
}
(catalog / "plan" / "citywide_catalog_plan.json").write_text(
    json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(plan, indent=2, sort_keys=True))
PY
}

cmd_plan() {
  export CT_CITYWIDE_CATALOG_LANES="${LANES}"
  build_remaining_anchors
  # Also emit the stock 4-route plan over *all* anchors for audit completeness.
  python "${ROOT}/scripts/temporal/run_ct05_catalog_probe.py" plan \
    --anchors-csv "${GROUPS_CSV}" \
    --out-dir "${CATALOG_ROOT}/plan/all_anchors_route_plan"
  echo "plan ready under ${CATALOG_ROOT}/plan"
}

cmd_launch() {
  require_groups
  if [[ ! -f "${CATALOG_ROOT}/plan/citywide_catalog_plan.json" ]]; then
    cmd_plan
  fi
  # Refuse duplicate live sessions.
  if tmux ls 2>/dev/null | grep -q "^${TMUX_PREFIX}_lane"; then
    echo "live tmux sessions already present for prefix ${TMUX_PREFIX}; use status or kill first" >&2
    tmux ls 2>/dev/null | grep "^${TMUX_PREFIX}_lane" || true
    exit 1
  fi

  # Authenticated one-anchor GEHI preflight before mass launch.
  smoke_csv="${CATALOG_ROOT}/plan/lanes/lane01.csv"
  smoke_out="${CATALOG_ROOT}/smoke_preflight"
  mkdir -p "${smoke_out}"
  echo "preflight: one-anchor probe from lane01..."
  DOTNET_SYSTEM_NET_DISABLEIPV6=1 python "${ROOT}/scripts/temporal/run_ct05_catalog_probe.py" probe \
    --anchors-csv "${smoke_csv}" \
    --route-id citywide_preflight \
    --out-dir "${smoke_out}" \
    --limit-anchors 1 \
    --request-interval 0.5 \
    | tee "${CATALOG_ROOT}/logs/preflight.json"
  if ! python3 - <<'PY'
import json, sys
from pathlib import Path
p = Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/ct05_catalog_v1/smoke_preflight/summary.json"
d = json.loads(p.read_text())
ok = d.get("successful_this_run", 0) + d.get("skipped_completed", 0)
if ok < 1 and d.get("failed_this_run", 1) > 0 and d.get("attempted_this_run", 0) > 0 and d.get("successful_this_run", 0) == 0:
    # Allow skipped; fail only if we attempted and got zero success.
    print(d)
    sys.exit(1)
print("preflight ok", d)
PY
  then
    echo "GEHI preflight failed; refusing mass launch" >&2
    exit 1
  fi

  local i lane_csv out sess
  for i in $(seq 1 "${LANES}"); do
    lane_id=$(printf "lane%02d" "${i}")
    lane_csv="${CATALOG_ROOT}/plan/lanes/${lane_id}.csv"
    out="${CATALOG_ROOT}/routes_new/${lane_id}"
    sess="${TMUX_PREFIX}_${lane_id}"
    mkdir -p "${out}" "${CATALOG_ROOT}/logs"
    # shellcheck disable=SC2086
    tmux new-session -d -s "${sess}" \
      "source '${ROOT}/scripts/activate_env.sh'; \
       export DOTNET_SYSTEM_NET_DISABLEIPV6=1; \
       cd '${ROOT}'; \
       python -u scripts/temporal/run_ct05_catalog_probe.py probe \
         --anchors-csv '${lane_csv}' \
         --route-id 'citywide_${lane_id}' \
         --out-dir '${out}' \
         --request-interval '${REQUEST_INTERVAL}' \
         2>&1 | tee -a '${CATALOG_ROOT}/logs/${lane_id}.log'; \
       echo EXIT_CODE=\$? | tee -a '${CATALOG_ROOT}/logs/${lane_id}.log'"
    echo "launched ${sess} -> ${out}"
  done
  echo "launched ${LANES} lanes under prefix ${TMUX_PREFIX}"
}

cmd_status() {
  python3 - <<'PY'
import json
from pathlib import Path
from datetime import datetime

run = Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/ct05_catalog_v1"
plan_path = run / "plan/citywide_catalog_plan.json"
if not plan_path.exists():
    print("no plan yet")
    raise SystemExit(0)
plan = json.loads(plan_path.read_text())
print(f"remaining_planned={plan['remaining_anchor_count']} lanes={plan['lanes']}")
total_req = 0
done = 0
failed = 0
attempted = 0
for shard in plan["lane_shards"]:
    out = run / "routes_new" / shard["lane_id"]
    summary = out / "summary.json"
    outcomes = out / "query_outcomes.jsonl"
    n_out = sum(1 for _ in open(outcomes)) if outcomes.exists() else 0
    req = shard["anchor_count"] * 4
    total_req += req
    if summary.exists():
        s = json.loads(summary.read_text())
        attempted += int(s.get("attempted_this_run", 0))
        done += int(s.get("successful_this_run", 0)) + int(s.get("skipped_completed", 0))
        failed += int(s.get("failed_this_run", 0))
        print(
            f"{shard['lane_id']}: anchors={shard['anchor_count']} "
            f"outcomes={n_out}/{req} summary={s.get('successful_this_run')}/"
            f"{s.get('failed_this_run')}/skip={s.get('skipped_completed')} "
            f"finished={s.get('finished_utc','')}"
        )
    else:
        # live progress from outcomes length
        print(f"{shard['lane_id']}: anchors={shard['anchor_count']} outcomes={n_out}/{req} (running or not started)")
        done += n_out  # provisional
print(f"progress_queries≈{done}/{total_req} ({100.0*done/max(total_req,1):.2f}%) failed_sum={failed}")
# tmux
import subprocess
r = subprocess.run(["bash", "-lc", "tmux ls 2>/dev/null | grep ct_cw_cat || true"], capture_output=True, text=True)
print("tmux:")
print(r.stdout or "(none)")
PY
}

cmd_merge() {
  require_groups
  mkdir -p "${CATALOG_ROOT}/merged" "${CATALOG_ROOT}/logs"
  # Discover all route dirs with both JSONL files.
  python "${ROOT}/scripts/temporal/run_ct05_catalog_probe.py" merge \
    --anchors-csv "${GROUPS_CSV}" \
    --route-root "${TOP52_CATALOG}/routes" \
    --route-root "${TOP52_CATALOG}/routes_resumed" \
    --route-root "${CATALOG_ROOT}/routes_new" \
    --out-dir "${CATALOG_ROOT}/merged" \
    | tee "${CATALOG_ROOT}/logs/merge.json"

  # 2019+ slice for download planning + coverage report.
  # (pandas heredoc replaced 2026-08-18: system python3 segfaulted on the
  # 13.8M-row merged CSV; e2_merge_coverage.py streams with csv module.)
  python "${ROOT}/scripts/temporal/e2_merge_coverage.py" --run-root "${RUN_ROOT}"
  # sentinel
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${CATALOG_ROOT}/merged/.done"
  echo "merge complete: ${CATALOG_ROOT}/merged"
}

cmd_coverage() {
  if [[ -f "${CATALOG_ROOT}/merged/catalog_coverage_report.json" ]]; then
    cat "${CATALOG_ROOT}/merged/catalog_coverage_report.json"
  else
    cmd_status
  fi
}

cmd_smoke() {
  # Tiny smoke: 3 remaining anchors, single lane, foreground.
  require_groups
  cmd_plan
  local smoke="${CATALOG_ROOT}/smoke_lane"
  mkdir -p "${smoke}"
  python3 - <<'PY'
from pathlib import Path
import pandas as pd
run = Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
src = run / "ct05_catalog_v1/plan/lanes/lane01.csv"
df = pd.read_csv(src).head(3)
out = run / "ct05_catalog_v1/plan/smoke3.csv"
df.to_csv(out, index=False, lineterminator="\n")
print(out, len(df))
PY
  DOTNET_SYSTEM_NET_DISABLEIPV6=1 python "${ROOT}/scripts/temporal/run_ct05_catalog_probe.py" probe \
    --anchors-csv "${CATALOG_ROOT}/plan/smoke3.csv" \
    --route-id citywide_smoke3 \
    --out-dir "${CATALOG_ROOT}/smoke_lane" \
    --request-interval 0.5
  echo "smoke done"
}

case "${cmd}" in
  plan) cmd_plan ;;
  launch) cmd_launch ;;
  status) cmd_status ;;
  merge) cmd_merge ;;
  coverage) cmd_coverage ;;
  smoke) cmd_smoke ;;
  *)
    echo "unknown command: ${cmd}" >&2
    exit 2
    ;;
esac
