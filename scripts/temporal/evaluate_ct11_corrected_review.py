#!/usr/bin/env python3
"""Evaluate the frozen corrected Cape Town CT-11 blind-review holdout."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path


CUTOFF = "2025-01-31"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        raise ValueError("Wilson interval requires n > 0")
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - half, center + half


def bracket(row: dict[str, str]) -> tuple[str, str] | None:
    state = row["independent_class"]
    if state == "TRANSITION":
        return row["corrected_latest_absent"], row["corrected_earliest_present"]
    if state == "ALREADY_PRESENT":
        return "", row["corrected_earliest_present"]
    if state == "ALL_ABSENT":
        return row["corrected_latest_absent"], CUTOFF
    if state == "UNDATABLE":
        return None
    raise ValueError(f"unknown independent_class: {state}")


def weight_stratum(row: dict[str, str]) -> str:
    status = row["date_status"]
    confidence = row["install_confidence"]
    if status == "done_appears" and confidence == "high":
        return "done_appears_x_high"
    if status == "done_appears":
        return "done_appears_x_medium_or_low"
    if status == "done_installed_during_census":
        return "done_installed_during_census_x_high_or_medium"
    if status == "done_already_present_before_geid_history":
        return "done_already_present_before_geid_history_x_low"
    return "undated_x_unscored"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--assignments", required=True, type=Path)
    parser.add_argument("--deliverable", required=True, type=Path)
    parser.add_argument("--acceptance-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pass1_path = args.review_root / "blind_pass1_independent.csv"
    repeat_path = args.review_root / "blind_repeat_independent.csv"
    if (os.stat(pass1_path).st_mode & 0o777) != 0o444:
        raise SystemExit("Pass-1 is not frozen mode 0444")
    if (os.stat(repeat_path).st_mode & 0o777) != 0o444:
        raise SystemExit("repeat is not frozen mode 0444")

    config = json.loads(args.acceptance_config.read_text(encoding="utf-8"))
    if not config["owner_confirmation"]["confirmed"]:
        raise SystemExit("owner acceptance confirmation is missing")
    package_summary = json.loads(
        (args.package_root / "corrected_package_summary.json").read_text(encoding="utf-8")
    )
    expected_package = config["package"]
    package_hashes = {
        "assignment_sha256": package_summary["assignments_sha256"],
        "pass1_package_sha256": package_summary["native_package_sha256"],
        "repeat_package_sha256": package_summary["repeat_package_sha256"],
    }
    if package_hashes != expected_package:
        raise SystemExit(f"acceptance/package hash mismatch: {package_hashes}")

    pass1_rows = read_csv(pass1_path)
    repeat_rows = read_csv(repeat_path)
    if [int(row["blind_index"]) for row in pass1_rows] != list(range(1, 501)):
        raise SystemExit("Pass-1 is not exactly blind_index 1..500")
    if [int(row["repeat_index"]) for row in repeat_rows] != list(range(1, 101)):
        raise SystemExit("repeat is not exactly repeat_index 1..100")

    private_pass1 = json.loads(
        (args.package_root / "private_blind_manifest.json").read_text(encoding="utf-8")
    )
    private_repeat = json.loads(
        (args.package_root / "private_repeat_manifest.json").read_text(encoding="utf-8")
    )
    pass1_by_index = {int(row["blind_index"]): row for row in pass1_rows}
    repeat_by_index = {int(row["repeat_index"]): row for row in repeat_rows}
    pass1_by_anchor = {
        item["anchor_id"]: pass1_by_index[int(item["blind_index"])] for item in private_pass1
    }
    if len(pass1_by_anchor) != 500:
        raise SystemExit("private Pass-1 map is not a 500-anchor bijection")

    selections = {
        row["anchor_id"]: row
        for row in read_csv(args.package_root / "authoritative_state_selection.csv")
    }
    assignments: dict[str, dict[str, str]] = {}
    for row in read_csv(args.assignments):
        assignments.setdefault(row["anchor_id"], row)
    inventory_rows = read_csv(args.deliverable)
    inventory = {row["source_anchor_id"]: row for row in inventory_rows}
    if len(inventory_rows) != 21453 or len(inventory) != 21453:
        raise SystemExit("deliverable is not the frozen 21,453-anchor bijection")

    joined: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    for item in private_pass1:
        anchor_id = item["anchor_id"]
        review = pass1_by_index[int(item["blind_index"])]
        corrected = bracket(review)
        production = (
            selections[anchor_id]["pipeline_interval_start"],
            selections[anchor_id]["pipeline_interval_end"],
        )
        if corrected is None:
            verdict = "UNDATABLE"
            corr_start = corr_end = ""
        else:
            corr_start, corr_end = corrected
            verdict = "CONFIRM" if corrected == production else "SHIFT"
        counts[verdict] += 1
        joined.append(
            {
                "anchor_id": anchor_id,
                "blind_index": review["blind_index"],
                "grid_id": item["grid_id"],
                "weight_stratum": weight_stratum(inventory[anchor_id]),
                "production_status": selections[anchor_id]["terminal_status"],
                "production_interval_start": production[0],
                "production_interval_end": production[1],
                "independent_class": review["independent_class"],
                "corrected_interval_start": corr_start,
                "corrected_interval_end": corr_end,
                "verdict": verdict,
                "review_confidence": review["review_confidence"],
            }
        )

    adjudicable = counts["CONFIRM"] + counts["SHIFT"]
    agreement = counts["CONFIRM"] / adjudicable
    agreement_wilson = wilson(counts["CONFIRM"], adjudicable)
    undatable_rate = counts["UNDATABLE"] / len(joined)

    population_counts = Counter(weight_stratum(row) for row in inventory_rows)
    sample_buckets: defaultdict[str, list[str]] = defaultdict(list)
    for row in joined:
        sample_buckets[row["weight_stratum"]].append(row["verdict"])
    if set(population_counts) != set(sample_buckets):
        raise SystemExit("population/sample standardization strata differ")
    weighted_mass: Counter[str] = Counter()
    weighted_strata: list[dict[str, object]] = []
    for stratum, population_n in sorted(population_counts.items()):
        sample_counts = Counter(sample_buckets[stratum])
        population_weight = population_n / len(inventory_rows)
        for verdict in ("CONFIRM", "SHIFT", "UNDATABLE"):
            weighted_mass[verdict] += (
                population_weight * sample_counts[verdict] / sum(sample_counts.values())
            )
        weighted_strata.append(
            {
                "stratum": stratum,
                "population_n": population_n,
                "sample_n": sum(sample_counts.values()),
                "sample_verdict_counts": dict(sample_counts),
            }
        )
    standardized_agreement = weighted_mass["CONFIRM"] / (
        weighted_mass["CONFIRM"] + weighted_mass["SHIFT"]
    )

    repeat_state_matches = repeat_interval_matches = 0
    for item in private_repeat:
        repeat = repeat_by_index[int(item["repeat_index"])]
        prior = pass1_by_anchor[item["anchor_id"]]
        state_match = repeat["independent_class"] == prior["independent_class"]
        interval_match = (
            state_match
            and repeat["corrected_latest_absent"] == prior["corrected_latest_absent"]
            and repeat["corrected_earliest_present"] == prior["corrected_earliest_present"]
        )
        repeat_state_matches += state_match
        repeat_interval_matches += interval_match
    repeat_state_wilson = wilson(repeat_state_matches, 100)

    thresholds = config["thresholds"]
    gates = {
        "exact_bracket_wilson_95_lower": {
            "actual": agreement_wilson[0],
            "threshold": thresholds["exact_bracket_wilson_95_lower_min"],
            "pass": agreement_wilson[0] >= thresholds["exact_bracket_wilson_95_lower_min"],
        },
        "inventory_weighted_exact_bracket": {
            "actual": standardized_agreement,
            "threshold": thresholds["inventory_weighted_exact_bracket_min"],
            "pass": standardized_agreement >= thresholds["inventory_weighted_exact_bracket_min"],
        },
        "undatable_rate": {
            "actual": undatable_rate,
            "threshold": thresholds["undatable_rate_max"],
            "pass": undatable_rate <= thresholds["undatable_rate_max"],
        },
        "repeat_state_agreement": {
            "actual": repeat_state_matches / 100,
            "threshold": thresholds["repeat_state_agreement_min"],
            "pass": repeat_state_matches / 100 >= thresholds["repeat_state_agreement_min"],
        },
        "repeat_state_wilson_95_lower": {
            "actual": repeat_state_wilson[0],
            "threshold": thresholds["repeat_state_wilson_95_lower_min"],
            "pass": repeat_state_wilson[0] >= thresholds["repeat_state_wilson_95_lower_min"],
        },
        "exact_boundary_failures": {
            "actual": package_summary["exact_claimed_boundary_failures"],
            "threshold": thresholds["exact_boundary_failures_max"],
            "pass": package_summary["exact_claimed_boundary_failures"] <= thresholds["exact_boundary_failures_max"],
        },
        "post_cutoff_frames": {
            "actual": package_summary["post_cutoff_frames"],
            "threshold": thresholds["post_cutoff_frames_max"],
            "pass": package_summary["post_cutoff_frames"] <= thresholds["post_cutoff_frames_max"],
        },
    }
    release_pass = all(gate["pass"] for gate in gates.values())
    report = {
        "schema_version": 1,
        "scope": config["scope"],
        "decision": "PASS" if release_pass else "HOLD",
        "ct12_authorized": release_pass,
        "full_cape_town_gehi_authorized": release_pass,
        "artifacts": {
            "acceptance_config_sha256": sha256_file(args.acceptance_config),
            "pass1_sha256": sha256_file(pass1_path),
            "repeat_sha256": sha256_file(repeat_path),
            **package_hashes,
        },
        "pass1": {
            "n": len(joined),
            "verdict_counts": dict(counts),
            "adjudicable_n": adjudicable,
            "exact_bracket_agreement": agreement,
            "wilson_95": agreement_wilson,
            "undatable_rate": undatable_rate,
        },
        "inventory_standardized": {
            "exact_bracket_agreement": standardized_agreement,
            "weighted_verdict_mass": dict(weighted_mass),
            "strata": weighted_strata,
            "note": "Direct standardization; no pseudo-Wilson interval assigned.",
        },
        "repeat": {
            "n": 100,
            "state_agreement_k": repeat_state_matches,
            "state_agreement": repeat_state_matches / 100,
            "state_wilson_95": repeat_state_wilson,
            "interval_agreement_k": repeat_interval_matches,
            "interval_agreement": repeat_interval_matches / 100,
            "interval_wilson_95": wilson(repeat_interval_matches, 100),
        },
        "gates": gates,
        "policy": {
            "failed_holdout_may_not_be_tuned": True,
            "remediation_requires_new_frozen_holdout": True,
            "claim_language": "Codex-reviewed QA; first-visible-appearance interval",
        },
    }

    args.output.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output / "ct11_corrected_metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    joined_path = args.output / "ct11_corrected_joined.csv"
    with joined_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(joined[0]))
        writer.writeheader()
        writer.writerows(joined)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
