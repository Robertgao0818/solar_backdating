#!/usr/bin/env python3
"""Deterministic preparation helpers for ISSUE-25 Stage C."""

from __future__ import annotations

import argparse
import csv
import json
from itertools import combinations
from pathlib import Path

LEGACY_SMALL_AGREEMENT = (359 * 0.6657 + 249 * 0.6506) / (359 + 249)
LEGACY_LARGE_AGREEMENT = 0.8182


def pairwise_agreement(keys_by_target: dict[str, list[str]]) -> dict[str, int | float]:
    """Unweighted exact agree_key agreement over every within-target rep pair."""
    matching = total = 0
    for keys in keys_by_target.values():
        for left, right in combinations(keys, 2):
            total += 1
            matching += left == right
    return {
        "agreement": matching / total if total else 0.0,
        "matching_pairs": matching,
        "n_pairs": total,
        "n_targets": len(keys_by_target),
    }


def choose_stage2_winner(arm_metrics: dict[str, dict]) -> dict[str, object]:
    """Pick the small-bucket leader subject to the pre-registered large guard."""
    large_floor = LEGACY_LARGE_AGREEMENT - 0.02
    safe = [
        arm
        for arm, metrics in arm_metrics.items()
        if float(metrics["large"]["agreement"]) >= large_floor
    ]
    pool = safe or list(arm_metrics)
    ranked = sorted(
        pool,
        key=lambda arm: (
            -float(arm_metrics[arm]["small"]["agreement"]),
            arm != "A24",
            arm,
        ),
    )
    winner = ranked[0]
    return {
        "arm": winner,
        "small_agreement": arm_metrics[winner]["small"]["agreement"],
        "large_agreement": arm_metrics[winner]["large"]["agreement"],
        "large_safety_floor": large_floor,
        "large_safe_candidates": safe,
        "selection_rule": "max small-bucket agreement among large-safe arms; exact tie -> A24",
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _prepare_anchors(
    manifest_path: Path,
    chip_targets_path: Path,
    output_path: Path,
    *,
    sample_stage: str,
    expected_count: int,
) -> int:
    """Filter one frozen sample stage and attach teacher bbox dimensions."""
    manifest = _read_csv(manifest_path)
    target_rows = _read_csv(chip_targets_path)
    bbox_by_anchor = {
        row["anchor_id"]: (row.get("source_width_m", ""), row.get("source_height_m", ""))
        for row in target_rows
    }
    selected = [row for row in manifest if row.get("sample_stage") == sample_stage]
    if len(selected) != expected_count:
        raise ValueError(
            f"expected {expected_count} {sample_stage} anchors, found {len(selected)}"
        )
    for row in selected:
        anchor_id = row.get("anchor_id", "")
        width, height = bbox_by_anchor.get(anchor_id, ("", ""))
        try:
            valid = float(width) > 0 and float(height) > 0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError(f"missing footprint bbox for {anchor_id}")
        row["source_width_m"] = str(width)
        row["source_height_m"] = str(height)

    fieldnames = list(selected[0])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)
    return len(selected)


def prepare_stage1_anchors(
    manifest_path: Path,
    chip_targets_path: Path,
    output_path: Path,
    *,
    expected_count: int = 400,
) -> int:
    return _prepare_anchors(
        manifest_path,
        chip_targets_path,
        output_path,
        sample_stage="stage1_core",
        expected_count=expected_count,
    )


def prepare_stage2_anchors(
    manifest_path: Path,
    chip_targets_path: Path,
    output_path: Path,
    *,
    expected_count: int = 800,
) -> int:
    return _prepare_anchors(
        manifest_path,
        chip_targets_path,
        output_path,
        sample_stage="stage2_precision",
        expected_count=expected_count,
    )


def prepare_b0_groups(
    sample_manifest_path: Path,
    group_anchors_path: Path,
    output_path: Path,
    *,
    expected_targets: int = 150,
) -> int:
    """Freeze unique legacy group anchors for the 150-target B0 bridge."""
    sample = _read_csv(sample_manifest_path)
    selected = [row for row in sample if row.get("b0_bridge") == "True"]
    if len(selected) != expected_targets:
        raise ValueError(
            f"expected {expected_targets} B0 targets, found {len(selected)}"
        )
    group_ids = {row["chip_id"] for row in selected}
    group_rows = [
        row
        for row in _read_csv(group_anchors_path)
        if row.get("anchor_id") in group_ids
    ]
    found = {row["anchor_id"] for row in group_rows}
    missing = sorted(group_ids - found)
    if missing:
        raise ValueError(f"missing B0 legacy group anchors: {missing[:5]}")
    group_rows.sort(key=lambda row: row["anchor_id"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(group_rows[0]))
        writer.writeheader()
        writer.writerows(group_rows)
    return len(group_rows)


def validate_authenticated_preflight(root: Path, model: str) -> dict[str, int]:
    """Fail closed unless the bounded real call returned non-empty valid schema."""
    audit_rows = []
    for path in sorted((root / "audit").rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                audit_rows.append(json.loads(line))
    if not audit_rows:
        raise ValueError("authenticated preflight produced no audit calls")
    for row in audit_rows:
        if (
            row.get("stage") != "batch_attempt_1"
            or row.get("error") not in (None, "")
            or int(row.get("n_valid") or 0) <= 0
            or row.get("missing_indices") not in (None, [])
            or not str(row.get("raw_response") or "").strip()
        ):
            raise ValueError(f"authenticated preflight audit failed: {row}")

    provenance_path = root / "scoring_provenance.jsonl"
    provenance = [
        json.loads(line)
        for line in provenance_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not provenance:
        raise ValueError("authenticated preflight produced no scored observations")
    wrong_models = sorted({str(row.get("model_id")) for row in provenance if row.get("model_id") != model})
    if wrong_models:
        raise ValueError(
            f"authenticated preflight model mismatch: expected {model}, got {wrong_models}"
        )
    return {
        "audit_calls": len(audit_rows),
        "scored_observations": len(provenance),
    }


def validate_scan_matrix(
    matrix_root: Path,
    anchors_path: Path,
    *,
    reps: int,
    model: str,
) -> dict[str, int]:
    """Require the exact frozen anchor set, terminal states, and one model."""
    expected = {row["anchor_id"] for row in _read_csv(anchors_path)}
    total_states = 0
    for rep in range(1, reps + 1):
        rep_root = matrix_root / f"rep{rep}"
        state_paths = sorted((rep_root / "scan_states").glob("*.json"))
        found = {path.stem for path in state_paths}
        if found != expected:
            raise ValueError(
                f"rep{rep} state set mismatch: missing={len(expected - found)} "
                f"extra={len(found - expected)}"
            )
        for path in state_paths:
            status = str(json.loads(path.read_text(encoding="utf-8")).get("status", ""))
            if not status.startswith("done_"):
                raise ValueError(f"rep{rep} non-terminal state: {path} status={status}")
        provenance = [
            json.loads(line)
            for line in (rep_root / "scoring_provenance.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        models = {row.get("model_id") for row in provenance}
        if models != {model}:
            raise ValueError(f"rep{rep} model mismatch: {sorted(models)}")
        total_states += len(state_paths)
    return {"anchors": len(expected), "reps": reps, "states": total_states}


def analyze_stage1(
    run_root: Path,
    anchors_path: Path,
    output_path: Path,
    *,
    model: str = "gemini-3.1-flash-lite",
) -> dict:
    """Decode five reps per arm and emit the pre-registered stability tables."""
    from scripts.validation.estimator_endtoend_decode import (
        ADOPTED_DECODER_EPOCH_GAP_DAYS,
        ADOPTED_ESTIMATOR,
        DEFAULT_COHORT_PRIOR_JSON,
        DEFAULT_EMISSIONS_JSON,
        DEFAULT_VEXCEL_CSV,
        _build_config,
        _load_emissions,
        _load_vexcel_ceiling,
    )
    from scripts.validation.fidelity_gate import posterior_to_agree_key
    from solar_backdating.estimators import ClampContext, get_estimator
    from solar_backdating.estimators.survival import cohort_prior_from_json
    from solar_backdating.eval.scan_state_io import load_scan_observations

    anchors = _read_csv(anchors_path)
    by_id = {row["anchor_id"]: row for row in anchors}
    emissions = _load_emissions(DEFAULT_EMISSIONS_JSON)
    cohort_prior = cohort_prior_from_json(
        json.loads(DEFAULT_COHORT_PRIOR_JSON.read_text(encoding="utf-8"))
    )
    config = _build_config(
        epoch_gap_days=None,
        decoder_epoch_gap_days=ADOPTED_DECODER_EPOCH_GAP_DAYS,
        emissions=emissions,
        cohort_prior=cohort_prior,
    )
    estimator = get_estimator(ADOPTED_ESTIMATOR)
    vexcel = _load_vexcel_ceiling(DEFAULT_VEXCEL_CSV)

    arms: dict[str, dict] = {}
    for arm in ("A24", "A48", "A96"):
        keys_by_target: dict[str, list[str]] = {anchor_id: [] for anchor_id in by_id}
        unusable = total_observations = download_failures = 0
        statuses: dict[str, int] = {}
        for rep in range(1, 6):
            rep_root = run_root / "stage1" / arm / f"rep{rep}"
            provenance_path = rep_root / "scoring_provenance.jsonl"
            provenance = [
                json.loads(line)
                for line in provenance_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            models = {row.get("model_id") for row in provenance}
            if models != {model}:
                raise ValueError(f"{arm} rep{rep} model mismatch: {sorted(models)}")
            for anchor_id, anchor in by_id.items():
                scan_path = rep_root / "scan_states" / f"{anchor_id}.json"
                if not scan_path.exists():
                    raise ValueError(f"missing Stage-1 scan state: {scan_path}")
                doc = json.loads(scan_path.read_text(encoding="utf-8"))
                status = str(doc.get("status", ""))
                statuses[status] = statuses.get(status, 0) + 1
                results = [
                    result
                    for rnd in doc.get("rounds", [])
                    for result in rnd.get("results", [])
                ]
                total_observations += len(results)
                unusable += sum(r.get("quality_flag") == "unusable" for r in results)
                download_failures += sum(
                    str(r.get("notes", "")).startswith("download_failed:")
                    for r in results
                )
                observations = load_scan_observations(scan_path)
                posterior = estimator(
                    observations,
                    ClampContext(ceiling_date=vexcel.get(anchor["grid_id"])),
                    config,
                )
                keys_by_target[anchor_id].append(posterior_to_agree_key(posterior))

        def metric(predicate) -> dict[str, int | float]:
            return pairwise_agreement(
                {
                    anchor_id: keys
                    for anchor_id, keys in keys_by_target.items()
                    if predicate(by_id[anchor_id])
                }
            )

        per_bucket = {
            bucket: metric(lambda row, bucket=bucket: row["area_bucket"] == bucket)
            for bucket in sorted({row["area_bucket"] for row in anchors})
        }
        per_zone = {
            zone: metric(lambda row, zone=zone: row["zone"] == zone)
            for zone in sorted({row["zone"] for row in anchors})
        }
        arms[arm] = {
            "overall": metric(lambda _row: True),
            "small": metric(
                lambda row: row["area_bucket"] in {"a_xs_lt15", "b_sm_15_40"}
            ),
            "large": metric(lambda row: row["area_bucket"] == "d_lg_ge100"),
            "per_area_bucket": per_bucket,
            "per_zone": per_zone,
            "unusable_rate": unusable / total_observations if total_observations else 0.0,
            "n_unusable": unusable,
            "n_observations": total_observations,
            "download_failure_rate": (
                download_failures / total_observations if total_observations else 0.0
            ),
            "n_download_failures": download_failures,
            "terminal_status_counts": dict(sorted(statuses.items())),
        }

    winner = choose_stage2_winner(arms)
    report = {
        "model": model,
        "decoder": ADOPTED_ESTIMATOR,
        "decoder_epoch_gap_days": ADOPTED_DECODER_EPOCH_GAP_DAYS,
        "legacy_control": {
            "small_agreement": LEGACY_SMALL_AGREEMENT,
            "large_agreement": LEGACY_LARGE_AGREEMENT,
            "small_adoption_floor": LEGACY_SMALL_AGREEMENT + 0.08,
            "large_safety_floor": LEGACY_LARGE_AGREEMENT - 0.02,
        },
        "arms": arms,
        "stage2_winner": winner,
        "note": (
            "Stage-2 selection uses the pre-registered stability primary metric "
            "and large-bucket guard. CoJ and control-unusable gates remain Stage-D R1."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-stage1")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--chip-targets", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--expected-count", type=int, default=400)
    prepare2 = sub.add_parser("prepare-stage2")
    prepare2.add_argument("--manifest", type=Path, required=True)
    prepare2.add_argument("--chip-targets", type=Path, required=True)
    prepare2.add_argument("--output", type=Path, required=True)
    prepare2.add_argument("--expected-count", type=int, default=800)
    validate = sub.add_parser("validate-preflight")
    validate.add_argument("--root", type=Path, required=True)
    validate.add_argument("--model", required=True)
    analyze = sub.add_parser("analyze-stage1")
    analyze.add_argument("--run-root", type=Path, required=True)
    analyze.add_argument("--anchors", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--model", default="gemini-3.1-flash-lite")
    prepare_b0 = sub.add_parser("prepare-b0")
    prepare_b0.add_argument("--sample-manifest", type=Path, required=True)
    prepare_b0.add_argument("--group-anchors", type=Path, required=True)
    prepare_b0.add_argument("--output", type=Path, required=True)
    prepare_b0.add_argument("--expected-targets", type=int, default=150)
    validate_matrix = sub.add_parser("validate-matrix")
    validate_matrix.add_argument("--matrix-root", type=Path, required=True)
    validate_matrix.add_argument("--anchors", type=Path, required=True)
    validate_matrix.add_argument("--reps", type=int, required=True)
    validate_matrix.add_argument("--model", required=True)
    args = parser.parse_args()

    if args.command == "prepare-stage1":
        count = prepare_stage1_anchors(
            args.manifest,
            args.chip_targets,
            args.output,
            expected_count=args.expected_count,
        )
        print(f"Wrote {count} ISSUE-25 stage1 anchors -> {args.output}")
    elif args.command == "prepare-stage2":
        count = prepare_stage2_anchors(
            args.manifest,
            args.chip_targets,
            args.output,
            expected_count=args.expected_count,
        )
        print(f"Wrote {count} ISSUE-25 stage2 anchors -> {args.output}")
    elif args.command == "validate-preflight":
        summary = validate_authenticated_preflight(args.root, args.model)
        print(
            "Authenticated preflight passed: "
            f"audit_calls={summary['audit_calls']} "
            f"scored_observations={summary['scored_observations']}"
        )
    elif args.command == "analyze-stage1":
        report = analyze_stage1(
            args.run_root,
            args.anchors,
            args.output,
            model=args.model,
        )
        winner = report["stage2_winner"]
        print(
            f"Stage-1 winner={winner['arm']} small={winner['small_agreement']:.4f} "
            f"large={winner['large_agreement']:.4f} -> {args.output}"
        )
    elif args.command == "prepare-b0":
        count = prepare_b0_groups(
            args.sample_manifest,
            args.group_anchors,
            args.output,
            expected_targets=args.expected_targets,
        )
        print(f"Wrote {count} unique ISSUE-25 B0 group anchors -> {args.output}")
    elif args.command == "validate-matrix":
        summary = validate_scan_matrix(
            args.matrix_root,
            args.anchors,
            reps=args.reps,
            model=args.model,
        )
        print(
            f"Validated matrix anchors={summary['anchors']} reps={summary['reps']} "
            f"states={summary['states']}"
        )


if __name__ == "__main__":
    main()
