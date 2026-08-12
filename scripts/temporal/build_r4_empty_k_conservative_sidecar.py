#!/usr/bin/env python3
"""Build the owner-gated conservative R4 empty-K sidecar.

The builder is inert until its amendment config records explicit owner
confirmation. It derives the population only from the frozen R0 manifest,
never consumes test rows as outputs, and never imports V3/V4/V5 labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal import run_r4_training as r4  # noqa: E402

EXPECTED_MANIFEST_SHA256 = r4.EXPECTED_INPUTS["manifest.parquet"]
EXPECTED_ANCHORS = 2146
EXPECTED_ROWS = 4292
DECISION_SOURCE = "R4_EMPTY_K_CONSERVATIVE_ABSTAIN_V1"
FINAL_CLASS = "EMPTY_K_UNREPRESENTABLE"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def validate_amendment(payload: dict[str, Any]) -> None:
    expected_policy = {
        "interval_loss_eligible": False,
        "earlier_boundary_override": "uninformative",
        "later_boundary_override": "uninformative",
        "non_boundary_frames_unchanged": True,
        "r0_manifest_unchanged": True,
        "v3_v4_v5_labels_consumed": False,
        "test_split_consumed": False,
    }
    if payload.get("amendment_id") != "r4_empty_k_conservative_abstain_v1":
        raise ValueError("unexpected amendment_id")
    if payload.get("policy") != expected_policy:
        raise ValueError("amendment policy differs from the conservative frozen proposal")
    owner = payload.get("owner_confirmation", {})
    if owner.get("confirmed") is not True or not owner.get("confirmation_evidence"):
        raise PermissionError("formal sidecar build requires explicit owner confirmation")


def derive_sidecar(
    manifest: pd.DataFrame, *, enforce_counts: bool = True
) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {
        "anchor_id", "capture_date", "chip_index", "label_v1", "scan_status", "split"
    }
    if missing := sorted(required - set(manifest.columns)):
        raise ValueError(f"manifest missing columns: {missing}")
    allowed = manifest.loc[manifest["split"].isin(["train", "calibration"])].copy()
    r4.assert_r4_split_access(allowed, context="empty-K sidecar population")
    allowed["effective_label"] = allowed["label_v1"].astype(str)
    records: list[dict[str, Any]] = []
    split_counts: dict[str, int] = {}
    for anchor_id, rows in allowed.groupby("anchor_id", sort=True):
        if str(rows["scan_status"].iloc[0]) != "done_appears":
            continue
        bracket = r4.derive_teacher_bracket(rows)
        dates = [r4._parse_date(value) for value in rows.sort_values(
            ["capture_date", "chip_index"]
        )["capture_date"]]
        bounds = r4._cell_bounds(r4._epoch_groups(dates), dates)
        try:
            r4.interval_cell_indices(bounds, bracket)
            continue
        except ValueError:
            pass
        endpoints = (("earlier", bracket.lower), ("later", bracket.upper))
        split = str(rows["split"].iloc[0])
        split_counts[split] = split_counts.get(split, 0) + 1
        for endpoint, endpoint_date in endpoints:
            matched = rows.loc[
                rows["capture_date"].astype(str).str[:10].eq(endpoint_date.isoformat())
            ]
            if len(matched) != 1:
                raise ValueError(
                    f"{anchor_id} {endpoint} matched {len(matched)} manifest rows"
                )
            source = matched.iloc[0]
            records.append({
                "anchor_id": str(anchor_id),
                "capture_date": endpoint_date.isoformat(),
                "endpoint": endpoint,
                "original_label": str(source["label_v1"]),
                "override_label": "uninformative",
                "decision_source": DECISION_SOURCE,
                "final_class": FINAL_CLASS,
                "interval_loss_eligible": False,
                "r0_split": split,
            })
    sidecar = pd.DataFrame(records).sort_values(
        ["anchor_id", "capture_date", "endpoint"]
    ).reset_index(drop=True)
    anchors = int(sidecar["anchor_id"].nunique()) if len(sidecar) else 0
    if sidecar.duplicated(["anchor_id", "capture_date"]).any():
        raise ValueError("sidecar contains duplicate frame keys")
    if len(sidecar) and not sidecar.groupby("anchor_id").size().eq(2).all():
        raise ValueError("every sidecar anchor must have exactly two boundary rows")
    if set(sidecar.get("r0_split", [])) - {"train", "calibration"}:
        raise PermissionError("sidecar contains forbidden test rows")
    if enforce_counts and (anchors != EXPECTED_ANCHORS or len(sidecar) != EXPECTED_ROWS):
        raise ValueError(
            f"empty-K population differs: anchors={anchors}, rows={len(sidecar)}"
        )
    summary = {
        "anchors": anchors,
        "boundary_rows": len(sidecar),
        "split_anchor_counts": split_counts,
        "override_label_counts": sidecar["override_label"].value_counts().to_dict(),
        "test_anchors": int(sidecar["r0_split"].eq("test").sum()) if len(sidecar) else 0,
    }
    return sidecar, summary


def write_manifest(root: Path) -> Path:
    paths = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.name != "artifacts.sha256" and not path.name.endswith(".tmp")
    )
    target = root / "artifacts.sha256"
    tmp = target.with_suffix(".sha256.tmp")
    tmp.write_text("".join(
        f"{sha256_file(path)}  {path.relative_to(root)}\n" for path in paths
    ))
    os.replace(tmp, target)
    return target


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    data_root = Path.home() / "zasolar_data/geid_temporal/run3_native_line_2026-07"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=data_root / "r0_manifest_v1/manifest.parquet")
    parser.add_argument(
        "--amendment", type=Path,
        default=PROJECT_ROOT / "configs/r4_empty_k_conservative_amendment_v1.json",
    )
    parser.add_argument("--out-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    amendment = json.loads(args.amendment.read_text())
    validate_amendment(amendment)
    manifest_sha = sha256_file(args.manifest)
    if manifest_sha != EXPECTED_MANIFEST_SHA256:
        raise ValueError(f"manifest SHA mismatch: {manifest_sha}")
    manifest = pd.read_parquet(args.manifest)
    sidecar, summary = derive_sidecar(manifest)
    atomic_parquet(args.out_root / "frame_label_overrides.parquet", sidecar)
    payload = {
        "run_id": "r4_empty_k_conservative_sidecar_v1",
        "status": "COMPLETE",
        "manifest_path": str(args.manifest.resolve()),
        "manifest_sha256": manifest_sha,
        "amendment_path": str(args.amendment.resolve()),
        "amendment_sha256": sha256_file(args.amendment),
        "owner_confirmation": amendment["owner_confirmation"],
        "summary": summary,
        "policy": amendment["policy"],
    }
    atomic_json(args.out_root / "RUN_LOCK.json", payload)
    atomic_json(args.out_root / "summary.json", summary)
    write_manifest(args.out_root)
    for path in args.out_root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
