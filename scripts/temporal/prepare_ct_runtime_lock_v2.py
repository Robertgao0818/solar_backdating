#!/usr/bin/env python3
"""Freeze CT-52 Lite-only runtime inputs and a dynamic quota policy.

This lock is deliberately separate from the superseded CT runtime locks.  It
does not call Gemini or GEHI.  The account roster is read from the local
Sub2API Postgres container, and the resulting policy is passed explicitly to
``run_adaptive_scan.py``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CT_ROOT = Path("/home/gao/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724")
DEFAULT_RUN_ROOT = CT_ROOT / "production_lite_v2_20260731"
PRIMARY_ALIAS = "gemini-3.1-flash-lite"
RESCUE_ALIAS = "gemini-3.6-flash-high"
RESCUE_EXACT = "gemini-3.6-flash"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gateway_health(url: str) -> dict[str, object]:
    with urlopen(url, timeout=10) as response:  # noqa: S310 - explicit local URL default
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError(f"gateway health response is not ok: {payload!r}")
    return payload


def roster_from_postgres(container: str) -> list[dict[str, object]]:
    query = (
        "select id,concurrency,extra from accounts "
        "where deleted_at is null and platform='antigravity' "
        "and status='active' and schedulable=true order by id"
    )
    result = subprocess.run(
        ["docker", "exec", container, "sh", "-lc", f"psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -AtF '|' -c \"{query}\""],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            raise RuntimeError(f"unexpected account roster row: {line!r}")
        try:
            extra = json.loads(parts[2]) if parts[2] else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"account {parts[0]} has malformed extra JSON") from exc
        rows.append({"id": parts[0], "concurrency": int(parts[1]), "extra": extra})
    if not rows:
        raise RuntimeError("active/schedulable Antigravity roster is empty")
    return rows


def max_model_reset(roster: list[dict[str, object]], model: str) -> tuple[str | None, int]:
    values: list[str] = []
    with_model_reset = 0
    for row in roster:
        extra = row.get("extra")
        limits = extra.get("model_rate_limits", {}) if isinstance(extra, dict) else {}
        limit = limits.get(model) if isinstance(limits, dict) else None
        if isinstance(limit, dict) and limit.get("rate_limit_reset_at"):
            values.append(str(limit["rate_limit_reset_at"]))
            with_model_reset += 1
    return (max(values) if values else None), with_model_reset


def read_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def build_lock(args: argparse.Namespace) -> dict[str, object]:
    if args.round1_model != PRIMARY_ALIAS or args.round2_model != PRIMARY_ALIAS:
        raise ValueError("CT primary lock is Lite-only")
    if args.expected_anchor_count <= 0:
        raise ValueError("expected anchor count must be positive")
    if args.max_in_flight < 0:
        raise ValueError("max-in-flight must be >= 0")
    required = [args.anchors_csv, args.catalog_csv, args.outcomes_csv, args.chip_root]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    if not args.anchors_csv.is_file() or not args.catalog_csv.is_file() or not args.outcomes_csv.is_file():
        raise ValueError("anchor/catalog/outcomes inputs must be files")
    anchor_count = read_count(args.anchors_csv)
    if anchor_count != args.expected_anchor_count:
        raise ValueError(f"anchor count {anchor_count} != expected {args.expected_anchor_count}")
    gateway = gateway_health(args.gateway_health_url)
    roster = roster_from_postgres(args.postgres_container)
    active_accounts = len(roster)
    active_slots = sum(int(row["concurrency"]) for row in roster)
    if active_accounts < 1 or active_slots < args.workers:
        raise ValueError(f"gateway roster insufficient: accounts={active_accounts} slots={active_slots} workers={args.workers}")
    reset_from_db, reset_count = max_model_reset(roster, PRIMARY_ALIAS)
    now = datetime.now(timezone.utc)
    # A stale reset timestamp is not evidence for this new production window.
    reset_after = reset_from_db
    if not reset_after:
        reset_after = (now + timedelta(hours=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    else:
        try:
            parsed = datetime.fromisoformat(reset_after.replace("Z", "+00:00"))
        except ValueError:
            parsed = now - timedelta(seconds=1)
        if parsed <= now:
            reset_after = (now + timedelta(hours=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    gross_safe = math.floor(active_accounts * args.per_account_quota * args.safe_fraction)
    retry_reserve = math.ceil(args.projected_logical_calls * args.retry_fraction)
    work_budget = gross_safe - args.canary_reserve - retry_reserve
    warning_budget = math.floor(work_budget * args.warning_fraction)
    if min(gross_safe, work_budget, warning_budget) <= 0:
        raise ValueError("computed quota budget is non-positive")
    window_id = args.window_id or f"ct52_lite_{now.strftime('%Y%m%dT%H%M%SZ')}"
    run_root = args.run_root
    if run_root.exists() and any(run_root.iterdir()) and not args.allow_existing_run_root:
        raise FileExistsError(f"refusing to overwrite non-empty run root: {run_root}")
    run_root.mkdir(parents=True, exist_ok=True)
    lock_dir = run_root / args.lock_name
    lock_dir.mkdir(parents=True, exist_ok=False)
    ledger_path = args.ledger_path or (run_root / "primary/quota_ledger.jsonl")
    pause_path = args.pause_path or (run_root / "primary/PAUSED_QUOTA.json")
    source_paths = {
        "anchors_csv": args.anchors_csv,
        "catalog_csv": args.catalog_csv,
        "catalog_outcomes_csv": args.outcomes_csv,
        "grid_roster_csv": args.grid_roster_csv,
        "run_adaptive_scan.py": PROJECT_ROOT / "scripts/temporal/run_adaptive_scan.py",
        "gemini_solar_image_review.py": PROJECT_ROOT / "scripts/validation/gemini_solar_image_review.py",
        "quota_control.py": PROJECT_ROOT / "scripts/temporal/quota_control.py",
        "scan_decision.py": PROJECT_ROOT / "scripts/temporal/scan_decision.py",
        "infer_install_dates.py": PROJECT_ROOT / "scripts/temporal/infer_install_dates.py",
    }
    source_hashes = {name: sha256(path) for name, path in source_paths.items() if path.exists()}
    quota = {
        "window_id": window_id,
        "active_accounts": active_accounts,
        "active_slots": active_slots,
        "effective_accounts": active_accounts,
        "effective_account_basis": "active_schedulable_antigravity_roster_with_model_rate_limit_records",
        "per_account_quota": args.per_account_quota,
        "safe_fraction": args.safe_fraction,
        "gross_safe_budget": gross_safe,
        "canary_reserve": args.canary_reserve,
        "projected_logical_calls": args.projected_logical_calls,
        "retry_fraction": args.retry_fraction,
        "retry_reserve": retry_reserve,
        "work_budget": work_budget,
        "warning_fraction": args.warning_fraction,
        "warning_budget": warning_budget,
        "reset_after": reset_after,
        "reset_timestamp_observations": reset_count,
        "grace_seconds": 300,
        "ledger": str(ledger_path),
        "pause_marker": str(pause_path),
    }
    policy_path = run_root / "QUOTA_POLICY.json"
    policy_path.write_text(json.dumps(quota, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lock = {
        "schema_version": "ct52_runtime_lock_v2",
        "status": "awaiting_fresh_lite_canary",
        "created_utc": now.isoformat(),
        "run_root": str(run_root),
        "scope": {
            "region_key": "cape_town",
            "grid_count": 52,
            "anchor_count": anchor_count,
            "census_cutoff": args.census_date,
            "metric_crs": "EPSG:32734",
        },
        "models": {
            "primary": {"requested_alias": PRIMARY_ALIAS, "expected_model_version": PRIMARY_ALIAS},
            "rescue": {"requested_alias": RESCUE_ALIAS, "expected_model_version": RESCUE_EXACT, "isolated": True},
            "in_process_troubleshooting": False,
        },
        "planner": {
            "historical_evidence_slots": 5,
            "initial_reference_slots": 1,
            "gemini_max_dates_per_call": 6,
            "reference_only_is_excluded_from_inference": True,
        },
        "imagery": {
            "provider": "GEHI Merged(TM+Wayback)",
            "primary_zoom": 19,
            "fallback_zoom": 18,
            "no_live_gehi": True,
            "candidate_key": "(anchor_id,capture_date)",
        },
        "runtime": {
            "workers": args.workers,
            "qps": args.qps,
            "max_in_flight": args.max_in_flight,
            "primary_verdict_store": str(run_root / "primary/verdict_store.jsonl"),
            "fresh_store_per_repetition": True,
            "gateway_health": gateway,
            "active_accounts": active_accounts,
            "active_slots": active_slots,
        },
        "quota": quota,
        "inputs": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in source_paths.items()
            if path.exists() and path.is_file()
        },
        "code_sha256": source_hashes,
        "frozen_exception_roster": {
            "no_history_count": 6,
            "wayback_only_anchor": "ct_full_inventory_2026_06_21_merged_t00038314",
            "geometry_exception_anchor": "ct_full_inventory_2026_06_21_merged_t00107162",
        },
    }
    lock_path = lock_dir / "RUN_LOCK.json"
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (lock_dir / "QUOTA_POLICY.json").write_text(json.dumps(quota, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"run_lock": str(lock_path), "quota_policy": str(policy_path), "lock": lock, "quota": quota}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--anchors-csv", type=Path, default=CT_ROOT / "anchors_v1/anchors_all.csv")
    parser.add_argument("--grid-roster-csv", type=Path, default=CT_ROOT / "manifests/ct_top52_grid_roster_v1.csv")
    parser.add_argument("--catalog-csv", type=Path, default=CT_ROOT / "ct05_catalog_v1/merged/gehi_vintage_candidates_ct05_run3_2019plus.csv")
    parser.add_argument("--outcomes-csv", type=Path, default=CT_ROOT / "ct05_catalog_v1/merged/anchor_catalog_outcomes.csv")
    parser.add_argument("--chip-root", type=Path, default=CT_ROOT / "ct05_download_v1/chips")
    parser.add_argument("--census-date", default="2025-01-31")
    parser.add_argument("--expected-anchor-count", type=int, default=21453)
    parser.add_argument("--round1-model", default=PRIMARY_ALIAS)
    parser.add_argument("--round2-model", default=PRIMARY_ALIAS)
    parser.add_argument("--workers", type=int, default=40)
    parser.add_argument(
        "--max-in-flight",
        type=int,
        default=0,
        help="Optional cap on simultaneous Gemini HTTP attempts across workers; "
        "0 disables it. Startup protection is provided by the strict global --qps pacer.",
    )
    parser.add_argument("--qps", type=float, default=6.0)
    parser.add_argument("--gateway-health-url", default="http://localhost:8080/health")
    parser.add_argument("--postgres-container", default="sub2api-postgres")
    parser.add_argument("--per-account-quota", type=int, default=5000)
    parser.add_argument("--safe-fraction", type=float, default=0.80)
    parser.add_argument("--projected-logical-calls", type=int, default=31000)
    parser.add_argument("--retry-fraction", type=float, default=0.10)
    parser.add_argument("--canary-reserve", type=int, default=10)
    parser.add_argument("--warning-fraction", type=float, default=0.90)
    parser.add_argument("--window-id", default=None)
    parser.add_argument(
        "--lock-name",
        default="runtime_lock_ct52_lite_v2",
        help="Unique lock directory name. Use a new name for an explicit final lock amendment; never overwrite an existing lock.",
    )
    parser.add_argument(
        "--allow-existing-run-root",
        action="store_true",
        help="Allow adding a new lock to an existing run root. The selected lock directory must still be absent.",
    )
    parser.add_argument(
        "--ledger-path",
        type=Path,
        default=None,
        help="Reuse an existing shared quota ledger when creating a final lock amendment.",
    )
    parser.add_argument("--pause-path", type=Path, default=None)
    args = parser.parse_args()
    result = build_lock(args)
    print(json.dumps({"run_lock": result["run_lock"], "quota_policy": result["quota_policy"], "quota": result["quota"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
