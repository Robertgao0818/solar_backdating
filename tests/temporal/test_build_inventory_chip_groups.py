from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from scripts.temporal.build_inventory_chip_groups import (
    _load_inventory,
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


# --- source_fid (native GPKG fid) vs source_feature_id (0-based position) ---


def _dispersed_inventory(confidences: list[float]) -> gpd.GeoDataFrame:
    """n polygons 500 m apart so each becomes its own singleton chip group."""
    base_x = 600_000
    base_y = 7_100_000
    n = len(confidences)
    return gpd.GeoDataFrame(
        {
            "source_grid": ["JNB0001"] * n,
            "confidence": confidences,
            "score": confidences,
            "sam_score": [0.9] * n,
            "n_merged": [1] * n,
        },
        geometry=[
            box(base_x + i * 500, base_y, base_x + i * 500 + 4, base_y + 4)
            for i in range(n)
        ],
        crs=METRIC_CRS,
    )


def _run_builder(inventory_path: Path, out_dir: Path, *, min_confidence=None) -> list[dict]:
    """Full load->manifest->write path; returns chip_targets.csv rows."""
    gdf = _load_inventory(
        inventory_path,
        layer="solar_predictions",
        metric_crs=METRIC_CRS,
        min_confidence=min_confidence,
    )
    targets = make_targets(
        gdf, region_key="johannesburg", inventory_tag="unit_test_inventory", pack_margin_m=1.0
    )
    groups = build_chip_groups(
        targets, chip_size_m=96.0, max_targets_per_chip=6, inventory_tag="unit_test_inventory"
    )
    group_rows, target_rows = build_manifest_rows(
        targets,
        groups,
        inventory_path=inventory_path,
        inventory_tag="unit_test_inventory",
        chip_size_m=96.0,
        search_radius_m=10.0,
        metric_crs=METRIC_CRS,
    )
    summary = build_summary(
        inventory_path=inventory_path,
        targets=targets,
        groups=groups,
        chip_size_m=96.0,
        max_targets_per_chip=6,
        pack_margin_m=1.0,
        search_radius_m=10.0,
    )
    write_outputs(
        output_dir=out_dir,
        group_rows=group_rows,
        target_rows=target_rows,
        summary=summary,
        groups=groups,
        targets=targets,
        metric_crs=METRIC_CRS,
        write_gpkg=False,
    )
    with (out_dir / "chip_targets.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_source_fid_is_native_and_source_feature_id_is_positional(tmp_path: Path) -> None:
    inv = tmp_path / "inventory.gpkg"
    _dispersed_inventory([0.99, 0.98, 0.97, 0.96]).to_file(
        inv, driver="GPKG", layer="solar_predictions"
    )

    rows = _run_builder(inv, tmp_path / "out")

    pairs = sorted((int(r["source_feature_id"]), int(r["source_fid"])) for r in rows)
    # source_feature_id = 0-based position; source_fid = 1-based native fid.
    assert pairs == [(0, 1), (1, 2), (2, 3), (3, 4)]


def test_source_fid_survives_min_confidence_filter_unchanged(tmp_path: Path) -> None:
    inv = tmp_path / "inventory.gpkg"
    _dispersed_inventory([0.5, 0.99, 0.6, 0.98]).to_file(
        inv, driver="GPKG", layer="solar_predictions"
    )

    rows = _run_builder(inv, tmp_path / "out", min_confidence=0.9)

    # Only positions 1 and 3 (fids 2 and 4) clear the threshold; their IDs are
    # assigned before the filter, so kept rows keep the pre-filter values.
    pairs = sorted((int(r["source_feature_id"]), int(r["source_fid"])) for r in rows)
    assert pairs == [(1, 2), (3, 4)]


def test_source_fid_tracks_noncontiguous_fids(tmp_path: Path) -> None:
    # Delete a middle feature so native fids ([1,3,4]) diverge from position+1
    # ([1,2,3]) -- the case that proves source_fid is the real fid, not pos+1.
    inv = tmp_path / "inventory.gpkg"
    _dispersed_inventory([0.99, 0.98, 0.97, 0.96]).to_file(
        inv, driver="GPKG", layer="solar_predictions"
    )
    con = sqlite3.connect(inv)
    con.execute("DELETE FROM solar_predictions WHERE fid = 2")
    con.commit()
    con.close()

    rows = _run_builder(inv, tmp_path / "out")

    pairs = sorted((int(r["source_feature_id"]), int(r["source_fid"])) for r in rows)
    assert pairs == [(0, 1), (1, 3), (2, 4)]
    # source_fid diverges from position+1 exactly where a fid was deleted.
    assert [p[1] for p in pairs] != [p[0] + 1 for p in pairs]


def test_group_row_carries_both_id_lists(tmp_path: Path) -> None:
    # Two adjacent polygons pack into one chip group; the group row must carry
    # both source_feature_id (positions) and source_fid (native fids) as lists.
    inv = tmp_path / "inventory.gpkg"
    base_x, base_y = 600_000, 7_100_000
    gpd.GeoDataFrame(
        {
            "source_grid": ["JNB0001", "JNB0001"],
            "confidence": [0.99, 0.98],
            "score": [0.99, 0.98],
            "sam_score": [0.9, 0.9],
            "n_merged": [1, 1],
        },
        geometry=[box(base_x, base_y, base_x + 4, base_y + 4), box(base_x + 10, base_y, base_x + 14, base_y + 4)],
        crs=METRIC_CRS,
    ).to_file(inv, driver="GPKG", layer="solar_predictions")

    gdf = _load_inventory(inv, layer="solar_predictions", metric_crs=METRIC_CRS, min_confidence=None)
    targets = make_targets(
        gdf, region_key="johannesburg", inventory_tag="unit_test_inventory", pack_margin_m=1.0
    )
    groups = build_chip_groups(
        targets, chip_size_m=96.0, max_targets_per_chip=6, inventory_tag="unit_test_inventory"
    )
    group_rows, _ = build_manifest_rows(
        targets,
        groups,
        inventory_path=inv,
        inventory_tag="unit_test_inventory",
        chip_size_m=96.0,
        search_radius_m=10.0,
        metric_crs=METRIC_CRS,
    )

    assert len(group_rows) == 1
    assert group_rows[0]["source_feature_id"] == "0;1"
    assert group_rows[0]["source_fid"] == "1;2"
