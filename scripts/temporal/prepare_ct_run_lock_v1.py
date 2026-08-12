#!/usr/bin/env python3
"""Prepare the immutable pre-canary runtime lock for the CT top-52 run."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.presence_scorer import get_scorer
from scripts.temporal.scoring_provenance import canonical_hash
from scripts.validation.gemini_solar_image_review import GeminiClientConfig

RUN_ROOT = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
)
GEHI = Path("/home/gao/data/ZAsolar/tools/GEHistoricalImagery/GEHistoricalImagery")
LOCKED_INPUTS = [
    RUN_ROOT / "manifests/ct_top52_manifest_v1.csv",
    RUN_ROOT / "manifests/ct_top52_grid_roster_v1.csv",
    RUN_ROOT / "anchors_v1/anchors_all.csv",
    RUN_ROOT / "groups_v1/chip_groups_as_anchors.csv",
    RUN_ROOT / "groups_v1/chip_targets.csv",
    Path("/home/gao/projects/ZAsolar/data/task_grid_cpt.gpkg"),
    Path(
        "/home/gao/projects/ZAsolar/results/analysis/ct_census_output_table/"
        "ct_full_inventory_2026-06-21_merged.gpkg"
    ),
]
RUNTIME_SOURCES = [
    PROJECT_ROOT / "scripts/temporal/run_adaptive_scan.py",
    PROJECT_ROOT / "scripts/temporal/presence_scorer.py",
    PROJECT_ROOT / "scripts/temporal/scoring_provenance.py",
    PROJECT_ROOT / "scripts/temporal/scan_state.py",
    PROJECT_ROOT / "scripts/temporal/scan_decision.py",
    PROJECT_ROOT / "scripts/temporal/build_gehi_candidates.py",
    PROJECT_ROOT / "scripts/temporal/run_ct05_catalog_probe.py",
    PROJECT_ROOT / "scripts/temporal/run_ct05_chip_pipeline.py",
    PROJECT_ROOT / "scripts/temporal/build_ct_chip_groups_v1.py",
    PROJECT_ROOT / "scripts/temporal/build_ct_install_dated_deliverable.py",
    PROJECT_ROOT / "scripts/validation/gemini_solar_image_review.py",
    Path("/home/gao/projects/ZAsolar/configs/datasets/regions.yaml"),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=PROJECT_ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def prompt_hash(model: str) -> str:
    scorer = get_scorer("gemini")
    config = GeminiClientConfig(
        base_url="LOCK_REDACTED",
        api_key="LOCK_REDACTED",
        model=model,
        api_format="native",
    )
    return canonical_hash(scorer.prompt_config_fingerprint("batch", config))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=RUN_ROOT / "runtime_lock_v1")
    # CT routing contract: routine initial/follow-up observations use the
    # lite model; only anchor_recovery is escalated to the stronger flash model.
    parser.add_argument("--round1-model", default="gemini-3.1-flash-lite")
    parser.add_argument("--round2-model", default="gemini-3-flash")
    parser.add_argument("--troubleshooting-model", default="gemini-3.6-flash-high")
    parser.add_argument("--anchor-workers", type=int, default=1)
    parser.add_argument("--qps", type=float, default=0.5)
    parser.add_argument("--tm-catalog-interval", type=float, default=1.0)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing lock directory: {args.output_dir}")
    if args.anchor_workers < 1 or args.qps <= 0 or args.tm_catalog_interval <= 0:
        raise ValueError("production lock requires positive workers/qps/catalog interval")
    missing = [str(path) for path in LOCKED_INPUTS + RUNTIME_SOURCES + [GEHI] if not path.exists()]
    if missing:
        raise ValueError(f"lock inputs missing: {missing}")

    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=PROJECT_ROOT)
    code = {
        "git_head": git("rev-parse", "HEAD"),
        "git_status_porcelain": git("status", "--porcelain=v1"),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "runtime_source_sha256": {str(path): sha256(path) for path in RUNTIME_SOURCES},
    }
    gehi_version = subprocess.check_output(
        [str(GEHI), "version"], text=True, stderr=subprocess.STDOUT
    ).strip()
    models = {
        "round1": {
            "requested_alias": args.round1_model,
            "returned_model_identity": None,
            "batch_prompt_config_hash": prompt_hash(args.round1_model),
        },
        "round2": {
            "requested_alias": args.round2_model,
            "returned_model_identity": None,
            "batch_prompt_config_hash": prompt_hash(args.round2_model),
        },
        "troubleshooting": {
            "requested_alias": args.troubleshooting_model,
            "returned_model_identity": None,
            "batch_prompt_config_hash": prompt_hash(args.troubleshooting_model),
        },
    }
    lock = {
        "schema_version": "ct_run_lock_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "run_root": str(RUN_ROOT),
        "status": "awaiting_fresh_model_canary",
        "immutable_after_canary": True,
        "scope": {
            "region_key": "cape_town",
            "grid_count": 52,
            "anchor_count": 21_453,
            "census_cutoff": "2025-01-31",
            "metric_crs": "EPSG:32734",
        },
        "inputs_sha256": {str(path): sha256(path) for path in LOCKED_INPUTS},
        "code_snapshot": code,
        "gehi": {
            "path": str(GEHI),
            "sha256": sha256(GEHI),
            "version": gehi_version,
        },
        "scorer": {
            "name": "gemini",
            "api_format": "native",
            "models": models,
            "fresh_canary_required_on": ["initial_launch", "every_resume"],
            "model_identity_drift_policy": "stop",
        },
        "runtime": {
            "anchor_workers": args.anchor_workers,
            "qps": args.qps,
            "tm_catalog_interval_seconds": args.tm_catalog_interval,
            "provider": "Merged",
            "primary_zoom": 19,
            "fallback_zoom": 18,
            "verdict_store_enabled": True,
            "primary_verdict_store": str(RUN_ROOT / "production/verdict_store.jsonl"),
            "repeat_store_policy": "fresh path and distinct repetition ID",
        },
    }
    args.output_dir.mkdir(parents=True)
    code_path = args.output_dir / "CODE_SNAPSHOT.json"
    code_path.write_text(json.dumps(code, indent=2, sort_keys=True) + "\n")
    lock["code_snapshot_sha256"] = sha256(code_path)
    lock_path = args.output_dir / "RUN_LOCK.json"
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"run_lock": str(lock_path), "sha256": sha256(lock_path), "status": lock["status"]}, indent=2))


if __name__ == "__main__":
    main()
