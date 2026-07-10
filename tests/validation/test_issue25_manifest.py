from __future__ import annotations

import pytest

from scripts.validation.issue25_manifest import (
    AREA_BUCKETS,
    ZONES,
    build_target_centered_anchors,
    build_grid_zone_lookup,
    compute_coj_zoning_evidence,
    compute_osm_industrial_share,
    select_issue25_smoke,
    select_issue25_targets,
)


def test_grid_zone_lookup_applies_frozen_precedence_and_covers_every_grid() -> None:
    rows = build_grid_zone_lookup(
        grid_ids=["JNB0001", "JNB0002", "JNB0003", "JNB0004"],
        cbd_grid_ids={"JNB0001"},
        osm_industrial_share={
            "JNB0001": 0.80,
            "JNB0002": 0.03,
            "JNB0003": 0.00,
            "JNB0004": 0.00,
        },
        coj_evidence={
            "JNB0001": {"industrial": 8, "residential": 0},
            "JNB0002": {"industrial": 0, "residential": 10},
            "JNB0003": {"industrial": 7, "residential": 2},
            "JNB0004": {"industrial": 2, "residential": 1},
        },
    )

    assert [(row["grid_id"], row["zone"], row["zone_source"]) for row in rows] == [
        ("JNB0001", "cbd", "cbd25_spatial_crosswalk"),
        ("JNB0002", "industrial", "osm_industrial_share_ge_0.03"),
        ("JNB0003", "industrial", "coj_zoning_industrial_n3_purity_ge_0.70"),
        ("JNB0004", "residential", "residential_fallback"),
    ]


def test_target_sample_is_balanced_deterministic_and_keeps_b0_in_legacy_pool() -> None:
    inventory_rows: list[dict[str, object]] = []
    legacy_ids: set[str] = set()
    area_by_bucket = {
        "a_xs_lt15": 10.0,
        "b_sm_15_40": 20.0,
        "c_md_40_100": 60.0,
        "d_lg_ge100": 150.0,
    }
    zone_rows = []
    for zone_index, zone in enumerate(ZONES, start=1):
        grid_id = f"JNB{zone_index:04d}"
        zone_rows.append({"grid_id": grid_id, "zone": zone})
        for bucket in AREA_BUCKETS:
            for index in range(120):
                target_id = f"{zone}-{bucket}-{index:03d}"
                inventory_rows.append(
                    {
                        "anchor_id": target_id,
                        "grid_id": grid_id,
                        "source_area_m2": area_by_bucket[bucket],
                    }
                )
                if index < 100:
                    legacy_ids.add(target_id)

    rows = select_issue25_targets(
        inventory_rows=inventory_rows,
        grid_zone_rows=zone_rows,
        legacy_target_ids=legacy_ids,
        seed=20260710,
    )
    reversed_rows = select_issue25_targets(
        inventory_rows=reversed(inventory_rows),
        grid_zone_rows=reversed(zone_rows),
        legacy_target_ids=legacy_ids,
        seed=20260710,
    )

    assert len(rows) == 1200
    assert sum(row["sample_stage"] == "stage1_core" for row in rows) == 400
    assert sum(bool(row["b0_bridge"]) for row in rows) == 150
    assert len({row["anchor_id"] for row in rows}) == 1200
    assert all(row["anchor_id"] in legacy_ids for row in rows if row["b0_bridge"])
    assert {
        (bucket, zone): sum(
            row["area_bucket"] == bucket and row["zone"] == zone for row in rows
        )
        for bucket in AREA_BUCKETS
        for zone in ZONES
    } == {(bucket, zone): 100 for bucket in AREA_BUCKETS for zone in ZONES}
    assert rows == reversed_rows


def test_target_centered_anchor_is_exactly_96m_and_has_zero_offset() -> None:
    from pyproj import Transformer

    rows = build_target_centered_anchors(
        [
            {
                "anchor_id": "target-1",
                "grid_id": "JNB0001",
                "centroid_lon": 28.05,
                "centroid_lat": -26.20,
                "source_area_m2": 12.5,
            }
        ],
        metric_crs="EPSG:32735",
        chip_size_m=96.0,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["anchor_id"] == "target-1"
    assert row["chip_id"] == "target-1"
    assert row["target_label"] == "T01"
    assert row["target_offset_x_m"] == 0.0
    assert row["target_offset_y_m"] == 0.0
    assert row["chip_size_m"] == 96.0

    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:32735", always_xy=True)
    min_x, min_y = to_metric.transform(row["chip_lon_min"], row["chip_lat_min"])
    max_x, max_y = to_metric.transform(row["chip_lon_max"], row["chip_lat_max"])
    center_x, center_y = to_metric.transform(row["centroid_lon"], row["centroid_lat"])
    assert max_x - min_x == pytest.approx(96.0, abs=0.02)
    assert max_y - min_y == pytest.approx(96.0, abs=0.02)
    assert (min_x + max_x) / 2.0 == pytest.approx(center_x, abs=0.02)
    assert (min_y + max_y) / 2.0 == pytest.approx(center_y, abs=0.02)


def test_osm_adapter_returns_clipped_industrial_area_share_per_grid() -> None:
    import geopandas as gpd
    from shapely.geometry import box

    grids = gpd.GeoDataFrame(
        {"gridcell_id": ["JNB0001", "JNB0002"]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100)],
        crs="EPSG:3857",
    )
    features = gpd.GeoDataFrame(
        {"landuse": ["industrial", "commercial"]},
        geometry=[box(0, 0, 30, 100), box(100, 0, 200, 100)],
        crs="EPSG:3857",
    )

    shares = compute_osm_industrial_share(grids, features)

    assert shares == {"JNB0001": pytest.approx(0.30), "JNB0002": 0.0}


def test_coj_adapter_counts_only_residential_and_industrial_target_evidence() -> None:
    import geopandas as gpd
    from shapely.geometry import box

    targets = [
        {"anchor_id": "t1", "grid_id": "JNB0001", "centroid_lon": 1, "centroid_lat": 1},
        {"anchor_id": "t2", "grid_id": "JNB0001", "centroid_lon": 11, "centroid_lat": 1},
        {"anchor_id": "t3", "grid_id": "JNB0001", "centroid_lon": 21, "centroid_lat": 1},
        {"anchor_id": "t4", "grid_id": "JNB0002", "centroid_lon": 1, "centroid_lat": 1},
    ]
    zoning = gpd.GeoDataFrame(
        {"ZONE_DESC": ["Industrial 1", "Residential 1", "Business 1"]},
        geometry=[box(0, 0, 5, 5), box(10, 0, 15, 5), box(20, 0, 25, 5)],
        crs="EPSG:4326",
    )

    evidence = compute_coj_zoning_evidence(targets, zoning)

    assert evidence == {
        "JNB0001": {"industrial": 1, "residential": 1},
        "JNB0002": {"industrial": 1, "residential": 0},
    }


def test_smoke20_covers_every_stratum_and_only_uses_stage1_targets() -> None:
    rows = [
        {
            "anchor_id": f"{bucket}-{zone}-{index}",
            "area_bucket": bucket,
            "zone": zone,
            "sample_stage": "stage1_core",
        }
        for bucket in AREA_BUCKETS
        for zone in ZONES
        for index in range(40)
    ]

    smoke = select_issue25_smoke(rows, seed=20260710)

    counts = {
        (bucket, zone): sum(
            row["area_bucket"] == bucket and row["zone"] == zone for row in smoke
        )
        for bucket in AREA_BUCKETS
        for zone in ZONES
    }
    assert len(smoke) == 20
    assert len({row["anchor_id"] for row in smoke}) == 20
    assert all(row["sample_stage"] == "stage1_core" for row in smoke)
    assert counts == {
        ("a_xs_lt15", "cbd"): 2,
        ("a_xs_lt15", "industrial"): 2,
        ("a_xs_lt15", "residential"): 3,
        ("b_sm_15_40", "cbd"): 2,
        ("b_sm_15_40", "industrial"): 2,
        ("b_sm_15_40", "residential"): 3,
        ("c_md_40_100", "cbd"): 1,
        ("c_md_40_100", "industrial"): 1,
        ("c_md_40_100", "residential"): 1,
        ("d_lg_ge100", "cbd"): 1,
        ("d_lg_ge100", "industrial"): 1,
        ("d_lg_ge100", "residential"): 1,
    }
