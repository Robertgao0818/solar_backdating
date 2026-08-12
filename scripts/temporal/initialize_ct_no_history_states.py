#!/usr/bin/env python3
"""Create explicit terminal states for CT anchors with no historical catalog.

The CT catalog probe has already established that these anchors were queried
successfully but have no TM or Wayback history.  They must remain in the final
denominator without consuming Gemini calls and without being encoded as an
``absent`` observation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.scan_state import (
    SPEC_VERSION,
    ScanState,
    create_scan_state,
    load_scan_state,
    save_scan_state,
    state_path_for,
)


EXPECTED_NO_HISTORY = {
    "ct_full_inventory_2026_06_21_merged_t00010449",
    "ct_full_inventory_2026_06_21_merged_t00010452",
    "ct_full_inventory_2026_06_21_merged_t00038228",
    "ct_full_inventory_2026_06_21_merged_t00038233",
    "ct_full_inventory_2026_06_21_merged_t00038296",
    "ct_full_inventory_2026_06_21_merged_t00038301",
}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _state_anchor(row: dict[str, str]) -> dict[str, str]:
    """Adapt the frozen CT anchor schema to the scan-state schema.

    CT v1 anchors intentionally call the placement column ``source_grid``;
    older adaptive-scan manifests called the same value ``grid_id``.  Keep the
    compatibility at this terminal-lane boundary instead of rewriting the
    frozen input CSV or weakening ``create_scan_state`` for every caller.
    """
    anchor = dict(row)
    if not anchor.get("grid_id"):
        anchor["grid_id"] = anchor.get("source_grid") or anchor.get("centroid_grid") or ""
    if not anchor["grid_id"]:
        raise ValueError(f"anchor {anchor.get('anchor_id', '')!r} has no source/grid identifier")
    return anchor


def initialize(
    anchors_csv: Path,
    outcomes_csv: Path,
    scan_states_dir: Path,
    *,
    census_date: str,
    expected_count: int = 6,
    require_frozen_ids: bool = True,
    selected_anchor_ids: set[str] | None = None,
) -> dict[str, object]:
    anchors = _rows(anchors_csv)
    outcomes = {row["anchor_id"]: row for row in _rows(outcomes_csv)}
    anchor_ids = {row.get("anchor_id", "") for row in anchors}
    if len(anchor_ids) != len(anchors):
        raise ValueError("anchors CSV contains duplicate/blank anchor_id")
    missing_outcomes = sorted(anchor_ids - set(outcomes))
    if missing_outcomes:
        raise ValueError(f"catalog outcomes missing {len(missing_outcomes)} anchors; first={missing_outcomes[0]}")
    no_history = {
        anchor_id
        for anchor_id in anchor_ids
        if outcomes[anchor_id].get("catalog_status") == "no_history"
    }
    if len(no_history) != expected_count:
        raise ValueError(f"expected {expected_count} no_history anchors, got {len(no_history)}")
    if require_frozen_ids and no_history != EXPECTED_NO_HISTORY:
        raise ValueError(
            "no_history roster drift: "
            f"missing={sorted(EXPECTED_NO_HISTORY - no_history)} "
            f"unexpected={sorted(no_history - EXPECTED_NO_HISTORY)}"
        )

    scan_states_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    reused = 0
    for anchor in anchors:
        anchor_id = anchor["anchor_id"]
        if anchor_id not in no_history or (
            selected_anchor_ids is not None and anchor_id not in selected_anchor_ids
        ):
            continue
        path = state_path_for(anchor_id, scan_states_dir)
        prior = load_scan_state(path) if path.exists() else None
        if prior is not None:
            if prior.status != "done_ambiguous_no_recent_anchor" or "catalog_status=no_history" not in prior.notes:
                raise ValueError(f"refusing to overwrite non-matching existing state: {path}")
            reused += 1
            continue
        state: ScanState = create_scan_state(_state_anchor(anchor))
        state.status = "done_ambiguous_no_recent_anchor"
        state.census_date = census_date
        state.post_census_reference_frames = 3
        state.notes = (
            "catalog_status=no_history; merged_date_count=0; "
            "catalog_queries_complete=1; operational_failure=0; no_gemini_request=1"
        )
        save_scan_state(state, path)
        created += 1

    manifest = {
        "schema_version": "ct_no_history_terminal_manifest_v1",
        "spec_version": SPEC_VERSION,
        "anchors_csv": str(anchors_csv),
        "anchors_sha256": hashlib.sha256(anchors_csv.read_bytes()).hexdigest(),
        "outcomes_csv": str(outcomes_csv),
        "outcomes_sha256": hashlib.sha256(outcomes_csv.read_bytes()).hexdigest(),
        "scan_states_dir": str(scan_states_dir),
        "census_date": census_date,
        "anchor_count": len(anchors),
        "no_history_count": len(no_history),
        "selected_anchor_count": (
            len(selected_anchor_ids) if selected_anchor_ids is not None else len(anchors)
        ),
        "no_history_anchor_ids": sorted(no_history),
        "created_count": created,
        "reused_count": reused,
    }
    output = scan_states_dir.parent / "no_history_terminal_manifest.json"
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, required=True)
    parser.add_argument("--catalog-outcomes-csv", type=Path, required=True)
    parser.add_argument("--scan-states-dir", type=Path, required=True)
    parser.add_argument("--census-date", default="2025-01-31")
    parser.add_argument("--expected-count", type=int, default=6)
    parser.add_argument("--allow-roster-drift", action="store_true")
    parser.add_argument(
        "--selected-anchors-csv",
        type=Path,
        default=None,
        help="Optional manifest whose anchor_id set limits which no-history states are written.",
    )
    args = parser.parse_args()
    selected_ids = None
    if args.selected_anchors_csv is not None:
        selected_ids = {
            row["anchor_id"]
            for row in _rows(args.selected_anchors_csv)
            if row.get("anchor_id")
        }
    result = initialize(
        args.anchors_csv,
        args.catalog_outcomes_csv,
        args.scan_states_dir,
        census_date=args.census_date,
        expected_count=args.expected_count,
        require_frozen_ids=not args.allow_roster_drift,
        selected_anchor_ids=selected_ids,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
