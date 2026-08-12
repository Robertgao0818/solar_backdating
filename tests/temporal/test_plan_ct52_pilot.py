"""Tests for deterministic CT paired-pilot selection."""

from __future__ import annotations

from pathlib import Path

from scripts.temporal.plan_ct52_pilot import (
    CANARY_GRIDS,
    _select_smoke,
)


def test_smoke_selector_returns_ten_per_grid_and_balances_arms() -> None:
    rows: list[dict[str, str]] = []
    outcomes: dict[str, dict[str, str]] = {}
    labels: dict[str, list[str]] = {}
    for grid in CANARY_GRIDS:
        for arm in ("A24", "A48"):
            for i in range(8):
                anchor_id = f"{grid}_{arm}_{i}"
                rows.append({"anchor_id": anchor_id, "source_grid": grid, "chip_arm": arm})
                outcomes[anchor_id] = {"catalog_status": "tm_and_wayback"}
                labels[anchor_id] = [arm, "20_40"]

    selected = _select_smoke(rows, outcomes, labels)
    assert len(selected) == 60
    by_grid = {grid: [row for row in selected if row["source_grid"] == grid] for grid in CANARY_GRIDS}
    assert all(len(grid_rows) == 10 for grid_rows in by_grid.values())
    assert all(sum(row["chip_arm"] == "A24" for row in grid_rows) == 5 for grid_rows in by_grid.values())
    assert all(sum(row["chip_arm"] == "A48" for row in grid_rows) == 5 for grid_rows in by_grid.values())
