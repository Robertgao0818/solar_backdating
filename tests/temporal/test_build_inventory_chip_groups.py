from __future__ import annotations

import csv
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from scripts.temporal.build_inventory_chip_groups import (
    build_chip_groups,
    build_manifest_rows,
    build_summary,
    make_targets,
    write_outputs,
)


METRIC_CRS = "EPSG:32735"


def _inventory() -> gpd.GeoDataFrame:
    base_x = 600_000
    base_y = 7_100_000
    return gpd.GeoDataFrame(
        {
            "source_grid": ["JNB0001", "JNB0001", "JNB0002"],
            "confidence": [0.99, 0.98, 0.97],
            "score": [0.99, 0.98, 0.97],
            "sam_score": [0.91, 0.92, 0.93],
            "n_merged": [1, 1, 2],
        },
        geometry=[
            box(base_x, base_y, base_x + 4, base_y + 4),
            box(base_x + 10, base_y, base_x + 14, base_y + 4),
            box(base_x + 200, base_y, base_x + 204, base_y + 4),
        ],
        crs=METRIC_CRS,
    )


def _clustered_inventory(n: int, *, spacing: float = 6.0) -> gpd.GeoDataFrame:
    base_x = 600_000
    base_y = 7_100_000
    return gpd.GeoDataFrame(
        {
            "source_grid": ["JNB0001"] * n,
            "confidence": [0.99] * n,
            "score": [0.99] * n,
            "sam_score": [0.9] * n,
            "n_merged": [1] * n,
        },
        geometry=[
            box(base_x + i * spacing, base_y, base_x + i * spacing + 2, base_y + 2)
            for i in range(n)
        ],
        crs=METRIC_CRS,
    )


def test_build_chip_groups_packs_nearby_targets() -> None:
    targets = make_targets(
        _inventory(),
        region_key="johannesburg",
        inventory_tag="unit_test_inventory",
        pack_margin_m=1.0,
    )

    groups = build_chip_groups(
        targets,
        chip_size_m=40.0,
        max_targets_per_chip=8,
        inventory_tag="unit_test_inventory",
    )

    sizes = sorted(len(group.member_indices) for group in groups)
    assert sizes == [1, 2]
    packed = next(group for group in groups if len(group.member_indices) == 2)
    assert packed.chip_bounds[2] - packed.chip_bounds[0] == 40.0
    assert packed.chip_bounds[3] - packed.chip_bounds[1] == 40.0


def test_max_targets_per_chip_splits_dense_groups() -> None:
    targets = make_targets(
        _inventory(),
        region_key="johannesburg",
        inventory_tag="unit_test_inventory",
        pack_margin_m=1.0,
    )

    groups = build_chip_groups(
        targets,
        chip_size_m=250.0,
        max_targets_per_chip=2,
        inventory_tag="unit_test_inventory",
    )

    assert sorted(len(group.member_indices) for group in groups) == [1, 2]


def test_hard_max_targets_per_chip_rejects_oversized_groups() -> None:
    # 6 tightly clustered targets; soft cap high enough to pack them all into
    # one group, so the resulting group of 6 must trip the hard cap of 4.
    targets = make_targets(
        _clustered_inventory(6),
        region_key="johannesburg",
        inventory_tag="unit_test_inventory",
        pack_margin_m=1.0,
    )

    with pytest.raises(ValueError) as excinfo:
        build_chip_groups(
            targets,
            chip_size_m=96.0,
            max_targets_per_chip=10,
            inventory_tag="unit_test_inventory",
            hard_max_targets_per_chip=4,
        )
    assert "hard_max_targets_per_chip" in str(excinfo.value)


def test_groups_within_hard_cap_pass_unaffected() -> None:
    # Same clustering, but the soft cap (3) keeps every group within the
    # hard cap (4), so no rejection occurs and soft-cap behaviour is preserved.
    targets = make_targets(
        _clustered_inventory(6),
        region_key="johannesburg",
        inventory_tag="unit_test_inventory",
        pack_margin_m=1.0,
    )

    groups = build_chip_groups(
        targets,
        chip_size_m=96.0,
        max_targets_per_chip=3,
        inventory_tag="unit_test_inventory",
        hard_max_targets_per_chip=4,
    )

    assert groups, "expected at least one chip group"
    assert all(len(group.member_indices) <= 3 for group in groups)
    assert all(len(group.member_indices) <= 4 for group in groups)
    # every target is assigned to exactly one group
    assigned = sorted(i for group in groups for i in group.member_indices)
    assert assigned == list(range(len(targets)))


def test_hard_cap_default_none_skips_check() -> None:
    # Without a hard cap, large soft-capped groups are allowed (back-compat).
    targets = make_targets(
        _clustered_inventory(6),
        region_key="johannesburg",
        inventory_tag="unit_test_inventory",
        pack_margin_m=1.0,
    )

    groups = build_chip_groups(
        targets,
        chip_size_m=96.0,
        max_targets_per_chip=10,
        inventory_tag="unit_test_inventory",
    )

    assert max(len(group.member_indices) for group in groups) == 6


def test_hard_cap_rejects_invalid_relation_to_soft_cap() -> None:
    targets = make_targets(
        _clustered_inventory(2),
        region_key="johannesburg",
        inventory_tag="unit_test_inventory",
        pack_margin_m=1.0,
    )

    with pytest.raises(ValueError):
        build_chip_groups(
            targets,
            chip_size_m=96.0,
            max_targets_per_chip=8,
            inventory_tag="unit_test_inventory",
            hard_max_targets_per_chip=4,
        )


def test_manifest_rows_are_anchor_compatible_and_write_csv(tmp_path: Path) -> None:
    inventory_path = tmp_path / "inventory.gpkg"
    gdf = _inventory()
    gdf.to_file(inventory_path, driver="GPKG", layer="solar_predictions")

    targets = make_targets(
        gdf,
        region_key="johannesburg",
        inventory_tag="unit_test_inventory",
        pack_margin_m=1.0,
    )
    groups = build_chip_groups(
        targets,
        chip_size_m=40.0,
        max_targets_per_chip=8,
        inventory_tag="unit_test_inventory",
    )
    group_rows, target_rows = build_manifest_rows(
        targets,
        groups,
        inventory_path=inventory_path,
        inventory_tag="unit_test_inventory",
        chip_size_m=40.0,
        search_radius_m=10.0,
        metric_crs=METRIC_CRS,
    )
    summary = build_summary(
        inventory_path=inventory_path,
        targets=targets,
        groups=groups,
        chip_size_m=40.0,
        max_targets_per_chip=8,
        pack_margin_m=1.0,
        search_radius_m=10.0,
    )
    output_dir = tmp_path / "out"
    write_outputs(
        output_dir=output_dir,
        group_rows=group_rows,
        target_rows=target_rows,
        summary=summary,
        groups=groups,
        targets=targets,
        metric_crs=METRIC_CRS,
        write_gpkg=False,
    )

    with (output_dir / "chip_groups_as_anchors.csv").open(newline="", encoding="utf-8") as fh:
        anchor_rows = list(csv.DictReader(fh))
    with (output_dir / "chip_targets.csv").open(newline="", encoding="utf-8") as fh:
        target_csv_rows = list(csv.DictReader(fh))

    assert len(anchor_rows) == len(groups)
    assert len(target_csv_rows) == len(targets)
    first = anchor_rows[0]
    required_fields = (
        "anchor_id",
        "centroid_lon",
        "centroid_lat",
        "chip_lon_min",
        "chip_lat_min",
        "chip_lon_max",
        "chip_lat_max",
    )
    for required in required_fields:
        assert first[required]
    assert first["anchor_id"] == first["chip_id"]
    assert {row["chip_id"] for row in target_csv_rows} == {row["chip_id"] for row in anchor_rows}
