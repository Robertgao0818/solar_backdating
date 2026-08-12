"""Regression tests for the CT no-history terminal lane."""

from __future__ import annotations

import csv
from pathlib import Path

from scripts.temporal.initialize_ct_no_history_states import (
    EXPECTED_NO_HISTORY,
    initialize,
)
from scripts.temporal.scan_state import load_scan_state


def test_initialize_accepts_ct_source_grid_schema(tmp_path: Path) -> None:
    anchors_path = tmp_path / "anchors.csv"
    outcomes_path = tmp_path / "outcomes.csv"
    state_dir = tmp_path / "states"
    anchor_rows = [
        {
            "anchor_id": anchor_id,
            "source_grid": f"CPT{i:04d}",
            "region_key": "cape_town",
            "geometry_version": "fullscan_target96_review24_v2",
        }
        for i, anchor_id in enumerate(sorted(EXPECTED_NO_HISTORY), 1)
    ]
    with anchors_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(anchor_rows[0]))
        writer.writeheader()
        writer.writerows(anchor_rows)
    with outcomes_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["anchor_id", "catalog_status"])
        writer.writeheader()
        for row in anchor_rows:
            writer.writerow({"anchor_id": row["anchor_id"], "catalog_status": "no_history"})

    result = initialize(
        anchors_path,
        outcomes_path,
        state_dir,
        census_date="2025-01-31",
    )

    assert result["created_count"] == 6
    for row in anchor_rows:
        state = load_scan_state(state_dir / f"{row['anchor_id']}.json")
        assert state is not None
        assert state.grid_id == row["source_grid"]
        assert state.status == "done_ambiguous_no_recent_anchor"
