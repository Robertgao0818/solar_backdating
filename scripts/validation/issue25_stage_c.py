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
ROUTE_AREA_CUT_M2 = 40.0
ROUTED_WINNER_ID = "routed_A24_A48_cut40"
ROUTED_AMENDMENT_ID = "2026-07-11-routed-chip-geometry"
# Single-threshold route: bucket → arm. Threshold is the pre-registered 40 m² cut.
ROUTED_BUCKET_ARM = {
    "a_xs_lt15": "A24",
    "b_sm_15_40": "A24",
    "c_md_40_100": "A48",
    "d_lg_ge100": "A48",
}


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


def route_chip_arm(
    source_area_m2: float,
    *,
    cut_m2: float = ROUTE_AREA_CUT_M2,
) -> str:
    """Deterministic Stage-2 chip arm from frozen census footprint area."""
    if source_area_m2 >= cut_m2:
        return "A48"
    return "A24"


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
        "mode": "single_arm",
        "small_agreement": arm_metrics[winner]["small"]["agreement"],
        "large_agreement": arm_metrics[winner]["large"]["agreement"],
        "large_safety_floor": large_floor,
        "large_safe_candidates": safe,
        "selection_rule": "max small-bucket agreement among large-safe arms; exact tie -> A24",
    }


def compute_routed_counterfactual(
    arm_metrics: dict[str, dict],
    *,
    cut_m2: float = ROUTE_AREA_CUT_M2,
) -> dict[str, object]:
    """Re-pool Stage-1 per-bucket pairs under the single-threshold area route."""
    if cut_m2 != ROUTE_AREA_CUT_M2:
        raise ValueError(
            f"only the pre-registered cut {ROUTE_AREA_CUT_M2:g} m² is allowed; got {cut_m2:g}"
        )
    for arm in ("A24", "A48"):
        if arm not in arm_metrics:
            raise ValueError(f"routed counterfactual requires arm {arm}")

    matching = n_pairs = n_targets = 0
    per_bucket: dict[str, dict[str, object]] = {}
    for bucket, arm in ROUTED_BUCKET_ARM.items():
        bucket_stats = arm_metrics[arm]["per_area_bucket"][bucket]
        matching += int(bucket_stats["matching_pairs"])
        n_pairs += int(bucket_stats["n_pairs"])
        n_targets += int(bucket_stats["n_targets"])
        per_bucket[bucket] = {
            "arm": arm,
            "agreement": bucket_stats["agreement"],
            "matching_pairs": bucket_stats["matching_pairs"],
            "n_pairs": bucket_stats["n_pairs"],
            "n_targets": bucket_stats["n_targets"],
        }

    small = arm_metrics["A24"]["small"]
    large = arm_metrics["A48"]["large"]
    small_floor = LEGACY_SMALL_AGREEMENT + 0.08
    large_floor = LEGACY_LARGE_AGREEMENT - 0.02
    overall_agreement = matching / n_pairs if n_pairs else 0.0
    return {
        "arm": ROUTED_WINNER_ID,
        "mode": "routed",
        "area_cut_m2": cut_m2,
        "amendment": ROUTED_AMENDMENT_ID,
        "rule": f"source_area_m2 >= {cut_m2:g} -> A48 else A24",
        "small_arm": "A24",
        "large_arm": "A48",
        "overall": {
            "agreement": overall_agreement,
            "matching_pairs": matching,
            "n_pairs": n_pairs,
            "n_targets": n_targets,
        },
        "small": dict(small),
        "large": dict(large),
        "per_area_bucket": per_bucket,
        "per_stratum_gates": {
            "small": {
                "agreement": small["agreement"],
                "floor": small_floor,
                "pass": float(small["agreement"]) >= small_floor,
            },
            "large": {
                "agreement": large["agreement"],
                "floor": large_floor,
                "pass": float(large["agreement"]) >= large_floor,
            },
        },
        "selection_rule": (
            "ISSUE-25 amendment 2026-07-11: single-threshold area route; "
            "per-stratum floors still bind; overall is diagnostic only"
        ),
    }


def build_stage2_winner_decision(
    arm_metrics: dict[str, dict],
    *,
    cut_m2: float = ROUTE_AREA_CUT_M2,
) -> dict[str, object]:
    """Stage-2 production winner after the 2026-07-11 routed amendment."""
    routed = compute_routed_counterfactual(arm_metrics, cut_m2=cut_m2)
    single = choose_stage2_winner(arm_metrics)
    gates = routed["per_stratum_gates"]
    if not (gates["small"]["pass"] and gates["large"]["pass"]):
        raise ValueError(
            "routed policy fails a pre-registered stratum floor; "
            f"gates={gates}"
        )
    return {
        "arm": ROUTED_WINNER_ID,
        "mode": "routed",
        "area_cut_m2": cut_m2,
        "amendment": ROUTED_AMENDMENT_ID,
        "rule": routed["rule"],
        "small_arm": "A24",
        "large_arm": "A48",
        "small_agreement": routed["small"]["agreement"],
        "large_agreement": routed["large"]["agreement"],
        "overall_agreement": routed["overall"]["agreement"],
        "per_stratum_gates": gates,
        "single_arm_prereg_winner": single,
        "selection_rule": routed["selection_rule"],
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
    attach_chip_arm: bool = False,
    area_cut_m2: float = ROUTE_AREA_CUT_M2,
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
        if attach_chip_arm:
            try:
                area = float(row["source_area_m2"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"missing source_area_m2 for chip_arm routing on {anchor_id}"
                ) from exc
            arm = route_chip_arm(area, cut_m2=area_cut_m2)
            row["chip_arm"] = arm
            row["review_extent_m"] = arm.removeprefix("A")
            row["chip_arm_rule"] = f"source_area_m2>={area_cut_m2:g}->A48 else A24"
            row["chip_arm_amendment"] = ROUTED_AMENDMENT_ID

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
    attach_chip_arm: bool = True,
    area_cut_m2: float = ROUTE_AREA_CUT_M2,
) -> int:
    return _prepare_anchors(
        manifest_path,
        chip_targets_path,
        output_path,
        sample_stage="stage2_precision",
        expected_count=expected_count,
        attach_chip_arm=attach_chip_arm,
        area_cut_m2=area_cut_m2,
    )


def split_anchors_by_chip_arm(
    anchors_path: Path,
    output_dir: Path,
    *,
    arms: tuple[str, ...] = ("A24", "A48"),
) -> dict[str, int]:
    """Write one CSV per chip_arm for sequential fixed-extent Stage-2 scans."""
    rows = _read_csv(anchors_path)
    if not rows:
        raise ValueError(f"no anchors in {anchors_path}")
    if "chip_arm" not in rows[0]:
        raise ValueError("anchors CSV missing chip_arm; prepare Stage-2 with routing")
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for arm in arms:
        subset = [row for row in rows if row.get("chip_arm") == arm]
        if not subset:
            raise ValueError(f"no anchors routed to {arm}")
        out = output_dir / f"anchors_{arm}.csv"
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(subset[0]))
            writer.writeheader()
            writer.writerows(subset)
        counts[arm] = len(subset)
    return counts


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

    single_arm_winner = choose_stage2_winner(arms)
    routed = compute_routed_counterfactual(arms)
    stage2_winner = build_stage2_winner_decision(arms)
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
        "single_arm_prereg_winner": single_arm_winner,
        "routed_counterfactual": routed,
        "stage2_winner": stage2_winner,
        "note": (
            "Stage-2 production winner is the 2026-07-11 single-threshold area "
            "route (A24 if source_area_m2 < 40 else A48). single_arm_prereg_winner "
            "preserves the original max-small-among-large-safe selector for audit. "
            "CoJ and control-unusable gates remain Stage-D R1."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def write_stage2_winner_confirmation(
    analysis_path: Path,
    decision_path: Path,
    sentinel_path: Path,
) -> dict[str, object]:
    """Freeze the human-confirmed Stage-2 winner policy on disk."""
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    decision = analysis.get("stage2_winner")
    if not isinstance(decision, dict) or decision.get("arm") != ROUTED_WINNER_ID:
        raise ValueError(
            f"analysis stage2_winner must be {ROUTED_WINNER_ID}; got {decision!r}"
        )
    if decision.get("mode") != "routed":
        raise ValueError(f"stage2_winner.mode must be routed; got {decision.get('mode')}")
    gates = decision.get("per_stratum_gates") or {}
    if not (
        gates.get("small", {}).get("pass") is True
        and gates.get("large", {}).get("pass") is True
    ):
        raise ValueError(f"refusing to confirm routed winner with failed gates: {gates}")

    from datetime import datetime, timezone

    confirmed_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        **decision,
        "confirmed_utc": confirmed_utc,
        "analysis_path": str(analysis_path),
        "amendment_doc": (
            "docs/replan_v2/ISSUE-25-teacher-geometry-stability-pilot.md"
            "#amendment-2026-07-11--single-threshold-area-routed-stage-2-geometry"
        ),
        "data_memo": "docs/replan_v2/DATA-issue25-stage1-routed-counterfactual-2026-07-11.md",
    }
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    sentinel_path.write_text(confirmed_utc + "\n", encoding="utf-8")
    return payload


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
    prepare2.add_argument(
        "--no-chip-arm",
        action="store_true",
        help="Skip chip_arm provenance columns (tests / legacy only).",
    )
    split = sub.add_parser("split-stage2-by-arm")
    split.add_argument("--anchors", type=Path, required=True)
    split.add_argument("--output-dir", type=Path, required=True)
    validate = sub.add_parser("validate-preflight")
    validate.add_argument("--root", type=Path, required=True)
    validate.add_argument("--model", required=True)
    analyze = sub.add_parser("analyze-stage1")
    analyze.add_argument("--run-root", type=Path, required=True)
    analyze.add_argument("--anchors", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--model", default="gemini-3.1-flash-lite")
    confirm = sub.add_parser("confirm-stage2-winner")
    confirm.add_argument("--analysis", type=Path, required=True)
    confirm.add_argument("--decision", type=Path, required=True)
    confirm.add_argument("--sentinel", type=Path, required=True)
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
            attach_chip_arm=not args.no_chip_arm,
        )
        print(f"Wrote {count} ISSUE-25 stage2 anchors -> {args.output}")
    elif args.command == "split-stage2-by-arm":
        counts = split_anchors_by_chip_arm(args.anchors, args.output_dir)
        print(f"Split Stage-2 anchors by chip_arm -> {args.output_dir}: {counts}")
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
            f"Stage-1 winner={winner['arm']} small={float(winner['small_agreement']):.4f} "
            f"large={float(winner['large_agreement']):.4f} "
            f"overall={float(winner['overall_agreement']):.4f} -> {args.output}"
        )
    elif args.command == "confirm-stage2-winner":
        payload = write_stage2_winner_confirmation(
            args.analysis,
            args.decision,
            args.sentinel,
        )
        print(
            f"Confirmed Stage-2 winner={payload['arm']} "
            f"utc={payload['confirmed_utc']} -> {args.decision}"
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
