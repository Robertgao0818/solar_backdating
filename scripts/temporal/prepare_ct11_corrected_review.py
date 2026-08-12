#!/usr/bin/env python3
"""Build the corrected CT-11 native, boundary-complete blind package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

from scripts.temporal.infer_install_dates import apply_dip_repair, infer_one
from scripts.temporal.render_ct11_native_review import render_native_review, sha256_file
from scripts.temporal.rerender_goldset_windows import RECOVERED_STATUSES, rerender_from_assignments
from scripts.temporal.scan_state import load_scan_state


CENSUS_DATE = date(2025, 1, 31)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def state_preference(path: Path) -> tuple[int, str]:
    text = str(path)
    priorities = (
        ("/primary/", 0),
        ("/canary/gate_retry2/", 1),
        ("/canary/control/", 2),
        ("/pilot_retry_v2/control/", 3),
        ("/pilot_retry_v2/optimized/", 4),
    )
    return next(((rank, text) for token, rank in priorities if token in text), (20, text))


def choose_matching_state(candidates: list[Path], assignment: dict[str, str]) -> tuple[Path, list[dict]]:
    evidence = []
    matches = []
    expected = (
        assignment["terminal_status"],
        assignment["pipeline_interval_start"],
        assignment["pipeline_interval_end"],
    )
    for path in candidates:
        state = load_scan_state(path)
        inferred_state, repaired_dates = apply_dip_repair(
            state,
            census_mid_date=CENSUS_DATE,
        )
        interval = infer_one(
            inferred_state,
            census_mid_date=CENSUS_DATE,
            scan_state_path=path,
            vexcel_capture_by_grid=None,
        )
        actual = (
            interval.status,
            interval.install_interval_start,
            interval.install_interval_end,
        )
        row = {
            "path": str(path),
            "sha256": sha256_file(path),
            "raw_status": state.status,
            "inferred_status": interval.status,
            "dip_repaired_dates": ";".join(repaired_dates),
            "interval_start": interval.install_interval_start,
            "interval_end": interval.install_interval_end,
            "matches_assignment": actual == expected,
        }
        evidence.append(row)
        if actual == expected:
            matches.append(path)
    if not matches:
        raise ValueError(f"{assignment['anchor_id']}: no scan state matches frozen assignment {expected}")
    return min(matches, key=state_preference), evidence


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--chipgroups", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--blind-seed", type=int, default=20260804)
    parser.add_argument("--repeat-seed", type=int, default=20260805)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)

    assignment_rows = read_csv(args.assignments)
    assignments: dict[str, dict[str, str]] = {}
    for row in assignment_rows:
        assignments.setdefault(row["anchor_id"], row)
    if len(assignments) != 500:
        raise SystemExit(f"corrected CT-11 package requires 500 unique anchors, got {len(assignments)}")

    candidate_index: dict[str, list[Path]] = defaultdict(list)
    for path in args.production_root.glob("**/scan_states/**/*.json"):
        if path.stem in assignments:
            candidate_index[path.stem].append(path)
    merged_states = args.output_root / "authoritative_scan_states"
    merged_states.mkdir(exist_ok=True)
    selection_rows = []
    candidate_evidence = []
    for anchor_id, assignment in sorted(assignments.items()):
        chosen, evidence = choose_matching_state(candidate_index.get(anchor_id, []), assignment)
        link = merged_states / f"{anchor_id}.json"
        if link.exists() or link.is_symlink():
            if link.resolve() != chosen.resolve():
                raise RuntimeError(f"existing authoritative link drift for {anchor_id}")
        else:
            link.symlink_to(chosen.resolve())
        selection_rows.append(
            {
                "anchor_id": anchor_id,
                "selected_state_path": str(chosen),
                "selected_state_sha256": sha256_file(chosen),
                "terminal_status": assignment["terminal_status"],
                "pipeline_interval_start": assignment["pipeline_interval_start"],
                "pipeline_interval_end": assignment["pipeline_interval_end"],
            }
        )
        for row in evidence:
            candidate_evidence.append({"anchor_id": anchor_id, **row})
    write_csv(
        args.output_root / "authoritative_state_selection.csv",
        selection_rows,
        list(selection_rows[0]),
    )
    write_csv(
        args.output_root / "state_candidate_audit.csv",
        candidate_evidence,
        list(candidate_evidence[0]),
    )

    frame_rows = rerender_from_assignments(
        args.assignments,
        scan_states_dir=merged_states,
        chipgroups_csv=args.chipgroups,
        root=args.output_root,
        coj_chips_dir=None,
        coj_years=(),
        flank=1,
    )
    recovered = [
        row for row in frame_rows
        if row["source"] == "scan_tm" and row["status"] in RECOVERED_STATUSES
    ]
    by_anchor: dict[str, list[dict]] = defaultdict(list)
    for row in recovered:
        by_anchor[str(row["anchor_id"])].append(row)

    coverage_failures = []
    for anchor_id, assignment in assignments.items():
        rows = by_anchor[anchor_id]
        role_dates = {(str(row["role"]), str(row["capture_date"])) for row in rows}
        for field, role in (
            ("latest_absent_date", "latest_absent"),
            ("earliest_present_date", "earliest_present"),
        ):
            claimed = assignment[field]
            if claimed and (role, claimed) not in role_dates:
                coverage_failures.append({"anchor_id": anchor_id, "role": role, "date": claimed})
        for row in rows:
            if str(row["capture_date"]) > CENSUS_DATE.isoformat():
                coverage_failures.append(
                    {"anchor_id": anchor_id, "role": "post_cutoff", "date": row["capture_date"]}
                )
    if coverage_failures:
        raise RuntimeError(f"corrected boundary/cutoff gate failed: {coverage_failures[:10]}")

    ordered = list(assignments)
    random.Random(args.blind_seed).shuffle(ordered)
    private_manifest = []
    for blind_index, anchor_id in enumerate(ordered, start=1):
        rows = sorted(by_anchor[anchor_id], key=lambda row: str(row["capture_date"]))
        if not 1 <= len(rows) <= 4:
            raise RuntimeError(f"{anchor_id}: corrected native layout got {len(rows)} frames")
        private_manifest.append(
            {
                "blind_index": blind_index,
                "anchor_id": anchor_id,
                "grid_id": assignments[anchor_id]["grid_id"],
                "n_frames": len(rows),
                "frame_dates": [str(row["capture_date"]) for row in rows],
            }
        )
    private_path = args.output_root / "private_blind_manifest.json"
    private_path.write_text(json.dumps(private_manifest, indent=2) + "\n")
    native = render_native_review(
        private_path,
        args.output_root / "rerender" / "frame_report.csv",
        args.output_root / "native_blind_sheets",
        rows_per_sheet=3,
        prefix="ct11_corrected_native",
    )
    repeat_items = random.Random(args.repeat_seed).sample(private_manifest, 100)
    random.Random(args.repeat_seed + 1).shuffle(repeat_items)
    repeat_manifest = [
        {
            "repeat_index": index,
            "anchor_id": item["anchor_id"],
            "grid_id": item["grid_id"],
            "n_frames": item["n_frames"],
            "frame_dates": item["frame_dates"],
        }
        for index, item in enumerate(repeat_items, start=1)
    ]
    repeat_private_path = args.output_root / "private_repeat_manifest.json"
    repeat_private_path.write_text(json.dumps(repeat_manifest, indent=2) + "\n")
    repeat_native = render_native_review(
        repeat_private_path,
        args.output_root / "rerender" / "frame_report.csv",
        args.output_root / "native_repeat_sheets",
        rows_per_sheet=3,
        prefix="ct11_corrected_repeat",
    )
    summary = {
        "schema_version": 1,
        "status": "frozen_before_corrected_review",
        "blind_seed": args.blind_seed,
        "repeat_seed": args.repeat_seed,
        "n_anchors": len(assignments),
        "n_frames": len(recovered),
        "n_sheets": native["n_sheets"],
        "repeat_n": len(repeat_manifest),
        "repeat_sheets": repeat_native["n_sheets"],
        "exact_claimed_boundary_failures": 0,
        "post_cutoff_frames": 0,
        "assignments_sha256": sha256_file(args.assignments),
        "private_blind_manifest_sha256": sha256_file(private_path),
        "private_repeat_manifest_sha256": sha256_file(repeat_private_path),
        "native_package_sha256": sha256_file(
            args.output_root / "native_blind_sheets" / "ct11_corrected_native_package.json"
        ),
        "repeat_package_sha256": sha256_file(
            args.output_root / "native_repeat_sheets" / "ct11_corrected_repeat_package.json"
        ),
    }
    summary_path = args.output_root / "corrected_package_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    top_files = [
        args.output_root / "authoritative_state_selection.csv",
        args.output_root / "state_candidate_audit.csv",
        args.output_root / "rerender" / "frame_report.csv",
        private_path,
        repeat_private_path,
        summary_path,
        args.output_root / "native_blind_sheets" / "ct11_corrected_native_manifest.json",
        args.output_root / "native_blind_sheets" / "ct11_corrected_native_package.json",
        args.output_root / "native_repeat_sheets" / "ct11_corrected_repeat_manifest.json",
        args.output_root / "native_repeat_sheets" / "ct11_corrected_repeat_package.json",
    ]
    hash_path = args.output_root / "corrected_package_outputs.sha256"
    hash_path.write_text(
        "".join(
            f"{sha256_file(path)}  {path.relative_to(args.output_root)}\n"
            for path in top_files
        )
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
