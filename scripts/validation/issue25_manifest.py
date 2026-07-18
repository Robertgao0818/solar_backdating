#!/usr/bin/env python3
"""Build the frozen ISSUE-25 zone, sample, and target-centred anchor manifests."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from scripts.temporal.anchor_derivation import derive_per_target_anchor_rows

AREA_BUCKETS = ("a_xs_lt15", "b_sm_15_40", "c_md_40_100", "d_lg_ge100")
ZONES = ("cbd", "industrial", "residential")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHIP_TARGETS = (
    Path.home()
    / "zasolar_data/geid_temporal/"
    "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/"
    "chip_targets.csv"
)
DEFAULT_TASK_GRID = PROJECT_ROOT.parent / "ZAsolar/data/jhb_task_grid_unified.gpkg"
DEFAULT_LEGACY_REFERENCE = (
    Path.home()
    / "zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/reference.csv"
)
DEFAULT_SOURCE_CACHE = (
    Path.home()
    / "zasolar_data/geid_temporal/issue25_teacher_geometry_20260710/source_cache"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "docs/replan_v2/issue25_manifest_20260710"
COJ_ZONING_URL = (
    "https://services2.arcgis.com/160O4zMRy3tavX3J/arcgis/rest/services/"
    "Zoning/FeatureServer/0"
)

_STRATA = tuple((bucket, zone) for bucket in AREA_BUCKETS for zone in ZONES)
_TOTAL_QUOTA = {stratum: 100 for stratum in _STRATA}
_STAGE1_QUOTA = {
    stratum: 34 if index < 4 else 33
    for index, stratum in enumerate(_STRATA)
}
_B0_QUOTA = {
    ("a_xs_lt15", "cbd"): 13,
    ("a_xs_lt15", "industrial"): 11,
    ("a_xs_lt15", "residential"): 37,
    ("b_sm_15_40", "cbd"): 3,
    ("b_sm_15_40", "industrial"): 13,
    ("b_sm_15_40", "residential"): 37,
    ("c_md_40_100", "cbd"): 6,
    ("c_md_40_100", "industrial"): 3,
    ("c_md_40_100", "residential"): 13,
    ("d_lg_ge100", "cbd"): 1,
    ("d_lg_ge100", "industrial"): 8,
    ("d_lg_ge100", "residential"): 5,
}
_SMOKE_QUOTA = {
    (bucket, zone): (
        3
        if (bucket, zone) in {
            ("a_xs_lt15", "residential"),
            ("b_sm_15_40", "residential"),
        }
        else 2
        if bucket in {"a_xs_lt15", "b_sm_15_40"}
        else 1
    )
    for bucket, zone in _STRATA
}


def _area_bucket(area_m2: float) -> str:
    if area_m2 < 15.0:
        return "a_xs_lt15"
    if area_m2 < 40.0:
        return "b_sm_15_40"
    if area_m2 < 100.0:
        return "c_md_40_100"
    return "d_lg_ge100"


def _hash_rank(seed: int, salt: str, target_id: str) -> str:
    return hashlib.sha256(f"{seed}|{salt}|{target_id}".encode("utf-8")).hexdigest()


def build_grid_zone_lookup(
    *,
    grid_ids: Iterable[str],
    cbd_grid_ids: set[str] | frozenset[str],
    osm_industrial_share: Mapping[str, float],
    coj_evidence: Mapping[str, Mapping[str, int]],
    industrial_share_threshold: float = 0.03,
    coj_min_matches: int = 3,
    coj_min_purity: float = 0.70,
) -> list[dict[str, object]]:
    """Assign every grid one frozen ISSUE-25 zone with source provenance."""

    rows: list[dict[str, object]] = []
    for grid_id in sorted(set(grid_ids)):
        share = float(osm_industrial_share.get(grid_id, 0.0))
        evidence = coj_evidence.get(grid_id, {})
        industrial_n = int(evidence.get("industrial", 0))
        residential_n = int(evidence.get("residential", 0))
        evidence_n = industrial_n + residential_n
        purity = industrial_n / evidence_n if evidence_n else 0.0

        if grid_id in cbd_grid_ids:
            zone = "cbd"
            source = "cbd25_spatial_crosswalk"
        elif share >= industrial_share_threshold:
            zone = "industrial"
            source = f"osm_industrial_share_ge_{industrial_share_threshold:.2f}"
        elif evidence_n >= coj_min_matches and purity >= coj_min_purity:
            zone = "industrial"
            source = (
                f"coj_zoning_industrial_n{coj_min_matches}_"
                f"purity_ge_{coj_min_purity:.2f}"
            )
        else:
            zone = "residential"
            source = "residential_fallback"

        rows.append(
            {
                "grid_id": grid_id,
                "zone": zone,
                "zone_source": source,
                "osm_industrial_share": share,
                "coj_industrial_matches": industrial_n,
                "coj_residential_matches": residential_n,
                "coj_evidence_n": evidence_n,
                "coj_industrial_purity": purity,
            }
        )
    return rows


def compute_osm_industrial_share(grid_gdf, osm_features_gdf) -> dict[str, float]:
    """Return clipped OSM industrial-landuse area share for every task grid."""

    if "gridcell_id" not in grid_gdf.columns:
        raise ValueError("task grid is missing gridcell_id")
    grids = grid_gdf.copy()
    features = osm_features_gdf.to_crs(grids.crs).copy()
    industrial = features.loc[
        features.get("landuse", "").astype(str).eq("industrial")
        & features.geometry.geom_type.isin({"Polygon", "MultiPolygon"})
    ]
    if industrial.empty:
        return {str(grid_id): 0.0 for grid_id in grids["gridcell_id"]}
    industrial_union = (
        industrial.geometry.union_all()
        if hasattr(industrial.geometry, "union_all")
        else industrial.geometry.unary_union
    )
    out: dict[str, float] = {}
    for row in grids[["gridcell_id", "geometry"]].itertuples(index=False):
        grid_area = float(row.geometry.area)
        covered_area = float(row.geometry.intersection(industrial_union).area)
        out[str(row.gridcell_id)] = min(max(covered_area / grid_area, 0.0), 1.0)
    return out


def compute_coj_zoning_evidence(
    target_rows: Iterable[Mapping[str, object]],
    zoning_gdf,
) -> dict[str, dict[str, int]]:
    """Count unique target points in CoJ Residential*/Industrial* polygons."""

    import geopandas as gpd

    rows = [dict(row) for row in target_rows]
    if not rows:
        return {}
    targets = gpd.GeoDataFrame(
        rows,
        geometry=gpd.points_from_xy(
            [float(row["centroid_lon"]) for row in rows],
            [float(row["centroid_lat"]) for row in rows],
        ),
        crs="EPSG:4326",
    ).to_crs(zoning_gdf.crs)
    joined = gpd.sjoin(
        targets[["anchor_id", "grid_id", "geometry"]],
        zoning_gdf[["ZONE_DESC", "geometry"]],
        how="inner",
        predicate="within",
    )
    if joined.empty:
        return {}

    def _category(description: object) -> str | None:
        text = str(description or "")
        if text.startswith("Industrial"):
            return "industrial"
        if text.startswith("Residential"):
            return "residential"
        return None

    joined["category"] = joined["ZONE_DESC"].map(_category)
    joined = joined.dropna(subset=["category"]).drop_duplicates(
        subset=["anchor_id", "grid_id", "category"]
    )
    out: dict[str, dict[str, int]] = {}
    for grid_id, part in joined.groupby("grid_id"):
        counts = part["category"].value_counts()
        out[str(grid_id)] = {
            "industrial": int(counts.get("industrial", 0)),
            "residential": int(counts.get("residential", 0)),
        }
    return out


def select_issue25_targets(
    *,
    inventory_rows: Iterable[Mapping[str, object]],
    grid_zone_rows: Iterable[Mapping[str, object]],
    legacy_target_ids: set[str] | frozenset[str],
    seed: int = 20260710,
) -> list[dict[str, object]]:
    """Select the frozen 1,200-target geometry sample and 150-target B0 bridge."""

    zone_rows_by_grid = {str(row["grid_id"]): dict(row) for row in grid_zone_rows}
    by_stratum: dict[tuple[str, str], list[dict[str, object]]] = {
        stratum: [] for stratum in _STRATA
    }
    seen: set[str] = set()
    for source_row in inventory_rows:
        row = dict(source_row)
        target_id = str(row.get("anchor_id") or row.get("target_anchor_id") or "")
        grid_id = str(row.get("grid_id") or "")
        if not target_id:
            raise ValueError("inventory row missing anchor_id/target_anchor_id")
        if target_id in seen:
            raise ValueError(f"duplicate target id in inventory: {target_id}")
        seen.add(target_id)
        if grid_id not in zone_rows_by_grid:
            raise ValueError(f"grid {grid_id!r} missing from grid-zone lookup")
        area_m2 = float(row["source_area_m2"])
        bucket = _area_bucket(area_m2)
        zone_row = zone_rows_by_grid[grid_id]
        zone = str(zone_row["zone"])
        stratum = (bucket, zone)
        if stratum not in by_stratum:
            raise ValueError(f"unsupported ISSUE-25 stratum: {stratum!r}")
        row.update(
            {
                "anchor_id": target_id,
                "grid_id": grid_id,
                "source_area_m2": area_m2,
                "area_bucket": bucket,
                "zone": zone,
                "zone_source": str(zone_row.get("zone_source") or ""),
            }
        )
        by_stratum[stratum].append(row)

    selected: list[dict[str, object]] = []
    for stratum in _STRATA:
        pool = by_stratum[stratum]
        b0_pool = sorted(
            (row for row in pool if row["anchor_id"] in legacy_target_ids),
            key=lambda row: _hash_rank(seed, "b0", str(row["anchor_id"])),
        )
        b0_quota = _B0_QUOTA[stratum]
        if len(b0_pool) < b0_quota:
            raise ValueError(
                f"stratum {stratum!r} has {len(b0_pool)} legacy targets, "
                f"needs {b0_quota} for B0"
            )
        b0_ids = {str(row["anchor_id"]) for row in b0_pool[:b0_quota]}

        ranked_pool = sorted(
            pool,
            key=lambda row: _hash_rank(seed, "sample", str(row["anchor_id"])),
        )
        b0_rows = [row for row in ranked_pool if row["anchor_id"] in b0_ids]
        non_b0_rows = [row for row in ranked_pool if row["anchor_id"] not in b0_ids]
        total_quota = _TOTAL_QUOTA[stratum]
        stratum_rows = b0_rows + non_b0_rows[: total_quota - len(b0_rows)]
        if len(stratum_rows) < total_quota:
            raise ValueError(
                f"stratum {stratum!r} has {len(pool)} targets, needs {total_quota}"
            )

        stage1_ids = {
            str(row["anchor_id"])
            for row in sorted(
                stratum_rows,
                key=lambda row: _hash_rank(seed, "stage1", str(row["anchor_id"])),
            )[: _STAGE1_QUOTA[stratum]]
        }
        for row in stratum_rows:
            target_id = str(row["anchor_id"])
            out = dict(row)
            out["sample_stage"] = (
                "stage1_core" if target_id in stage1_ids else "stage2_precision"
            )
            out["b0_bridge"] = target_id in b0_ids
            out["selection_hash"] = _hash_rank(seed, "sample", target_id)
            selected.append(out)

    bucket_order = {value: index for index, value in enumerate(AREA_BUCKETS)}
    zone_order = {value: index for index, value in enumerate(ZONES)}
    return sorted(
        selected,
        key=lambda row: (
            row["sample_stage"] != "stage1_core",
            bucket_order[str(row["area_bucket"])],
            zone_order[str(row["zone"])],
            str(row["selection_hash"]),
        ),
    )


def select_issue25_smoke(
    sample_rows: Iterable[Mapping[str, object]],
    *,
    seed: int = 20260710,
) -> list[dict[str, object]]:
    """Select the frozen 20-target, all-strata Stage-B API smoke subset."""

    by_stratum: dict[tuple[str, str], list[dict[str, object]]] = {
        stratum: [] for stratum in _STRATA
    }
    for source_row in sample_rows:
        row = dict(source_row)
        if row.get("sample_stage") != "stage1_core":
            continue
        stratum = (str(row["area_bucket"]), str(row["zone"]))
        if stratum in by_stratum:
            by_stratum[stratum].append(row)

    out: list[dict[str, object]] = []
    for stratum in _STRATA:
        quota = _SMOKE_QUOTA[stratum]
        pool = sorted(
            by_stratum[stratum],
            key=lambda row: _hash_rank(seed, "smoke20", str(row["anchor_id"])),
        )
        if len(pool) < quota:
            raise ValueError(
                f"stage1 stratum {stratum!r} has {len(pool)} targets, "
                f"needs {quota} for smoke20"
            )
        out.extend(pool[:quota])
    return out


def build_target_centered_anchors(
    rows: Iterable[Mapping[str, object]],
    *,
    metric_crs: str = "EPSG:32735",
    chip_size_m: float = 96.0,
) -> list[dict[str, object]]:
    """Build scorer/download rows through the shared per-target derivation."""

    return derive_per_target_anchor_rows(
        rows,
        metric_crs=metric_crs,
        chip_size_m=chip_size_m,
    )


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _write_csv(path: Path, rows: list[Mapping[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_or_fetch_zone_sources(*, task_grids, source_cache: Path, refresh: bool):
    import geopandas as gpd

    osm_path = source_cache / "osm_jhb_landuse_industrial_commercial_retail.gpkg"
    coj_path = source_cache / "coj_zoning_active_2017.gpkg"
    source_cache.mkdir(parents=True, exist_ok=True)

    if refresh or not osm_path.exists():
        import osmnx as ox

        ox.settings.overpass_url = "https://overpass-api.de/api"
        ox.settings.requests_timeout = 240
        ox.settings.use_cache = True
        ox.settings.cache_folder = str(source_cache / "osmnx_http_cache")
        bbox = tuple(task_grids.to_crs("EPSG:4326").total_bounds)
        osm = ox.features.features_from_bbox(
            bbox,
            {"landuse": ["industrial", "commercial", "retail"]},
        ).reset_index()
        osm = osm.loc[
            osm.geometry.geom_type.isin({"Polygon", "MultiPolygon"}),
            ["element", "id", "landuse", "geometry"],
        ]
        osm.to_file(osm_path, driver="GPKG")
    else:
        osm = gpd.read_file(osm_path)

    if refresh or not coj_path.exists():
        import requests

        features: list[dict[str, object]] = []
        offset = 0
        while True:
            response = requests.get(
                f"{COJ_ZONING_URL}/query",
                params={
                    "f": "geojson",
                    "where": "STATUS_COD='A'",
                    "outFields": "FID,ZONE_CODE,ZONE_DESC,STATUS_COD,PRIORITY",
                    "returnGeometry": "true",
                    "outSR": 4326,
                    "resultRecordCount": 2000,
                    "resultOffset": offset,
                    "orderByFields": "FID",
                },
                timeout=120,
            )
            response.raise_for_status()
            page = response.json().get("features", [])
            features.extend(page)
            if len(page) < 2000:
                break
            offset += len(page)
        coj = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        coj.to_file(coj_path, driver="GPKG")
    else:
        coj = gpd.read_file(coj_path)
    return osm, coj, osm_path, coj_path


def _manifest_fields() -> tuple[list[str], list[str], list[str]]:
    zone_fields = [
        "grid_id",
        "zone",
        "zone_source",
        "osm_industrial_share",
        "coj_industrial_matches",
        "coj_residential_matches",
        "coj_evidence_n",
        "coj_industrial_purity",
    ]
    sample_fields = [
        "anchor_id",
        "chip_id",
        "grid_id",
        "region_key",
        "source_feature_id",
        "centroid_lon",
        "centroid_lat",
        "source_area_m2",
        "area_bucket",
        "zone",
        "zone_source",
        "sample_stage",
        "b0_bridge",
        "selection_hash",
    ]
    anchor_fields = [
        *sample_fields,
        "legacy_group_anchor_id",
        "source_grid",
        "target_index",
        "target_label",
        "target_offset_x_m",
        "target_offset_y_m",
        "search_radius_m",
        "chip_half_m",
        "chip_size_m",
        "chip_lon_min",
        "chip_lat_min",
        "chip_lon_max",
        "chip_lat_max",
    ]
    return zone_fields, sample_fields, anchor_fields


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chip-targets", type=Path, default=DEFAULT_CHIP_TARGETS)
    parser.add_argument("--task-grid", type=Path, default=DEFAULT_TASK_GRID)
    parser.add_argument("--legacy-reference", type=Path, default=DEFAULT_LEGACY_REFERENCE)
    parser.add_argument("--source-cache", type=Path, default=DEFAULT_SOURCE_CACHE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--refresh-zone-sources", action="store_true")
    return parser.parse_args()


def main() -> int:
    import geopandas as gpd

    from scripts.temporal.build_distillation_set import resolve_cbd_jnb_grids

    args = parse_args()
    target_rows = _read_csv_rows(args.chip_targets)
    task_grids = gpd.read_file(args.task_grid)
    task_grids = task_grids.loc[
        task_grids["gridcell_id"].astype(str).str.startswith("JNB")
    ].copy()
    osm, coj, osm_path, coj_path = _load_or_fetch_zone_sources(
        task_grids=task_grids,
        source_cache=args.source_cache,
        refresh=args.refresh_zone_sources,
    )
    osm_share = compute_osm_industrial_share(task_grids.to_crs("EPSG:32735"), osm)
    coj_evidence = compute_coj_zoning_evidence(target_rows, coj)
    cbd_grid_ids = resolve_cbd_jnb_grids(args.task_grid)
    if cbd_grid_ids is None:
        raise SystemExit("Could not resolve the approved CBD25 JNB crosswalk")
    zone_rows = build_grid_zone_lookup(
        grid_ids=task_grids["gridcell_id"].astype(str),
        cbd_grid_ids=cbd_grid_ids,
        osm_industrial_share=osm_share,
        coj_evidence=coj_evidence,
    )

    legacy_rows = _read_csv_rows(args.legacy_reference)
    legacy_ids = {
        str(row["target_anchor_id"])
        for row in legacy_rows
        if row.get("target_anchor_id")
    }
    sample_rows = select_issue25_targets(
        inventory_rows=target_rows,
        grid_zone_rows=zone_rows,
        legacy_target_ids=legacy_ids,
        seed=args.seed,
    )
    anchor_rows = build_target_centered_anchors(sample_rows)
    smoke_rows = select_issue25_smoke(sample_rows, seed=args.seed)
    smoke_anchor_rows = build_target_centered_anchors(smoke_rows)

    zone_fields, sample_fields, anchor_fields = _manifest_fields()
    zone_path = args.out_dir / "grid_zone_lookup.csv"
    sample_path = args.out_dir / "sample_manifest.csv"
    anchor_path = args.out_dir / "target_anchors_96m.csv"
    smoke_anchor_path = args.out_dir / "smoke20_anchors_96m.csv"
    _write_csv(zone_path, zone_rows, zone_fields)
    _write_csv(sample_path, sample_rows, sample_fields)
    _write_csv(anchor_path, anchor_rows, anchor_fields)
    _write_csv(smoke_anchor_path, smoke_anchor_rows, anchor_fields)

    summary = {
        "issue": 25,
        "created_date": "2026-07-10",
        "model": "gemini-3.1-flash-lite",
        "seed": args.seed,
        "geometry": {
            "source_extent_m": 96.0,
            "arms_m": [24, 48, 96],
            "target_centered": True,
        },
        "zone_rule": {
            "precedence": [
                "cbd25_spatial_crosswalk",
                "osm_industrial_share_ge_0.03",
                "coj_zoning_industrial_n3_purity_ge_0.70",
                "residential_fallback",
            ],
            "coj_zoning_url": COJ_ZONING_URL,
            "osm_query": {"landuse": ["industrial", "commercial", "retail"]},
        },
        "source_snapshots": {
            "osm_path": str(osm_path),
            "osm_sha256": _sha256_file(osm_path),
            "coj_path": str(coj_path),
            "coj_sha256": _sha256_file(coj_path),
        },
        "counts": {
            "grid_zone": {
                zone: sum(row["zone"] == zone for row in zone_rows) for zone in ZONES
            },
            "sample_total": len(sample_rows),
            "stage1_core": sum(row["sample_stage"] == "stage1_core" for row in sample_rows),
            "stage2_precision": sum(
                row["sample_stage"] == "stage2_precision" for row in sample_rows
            ),
            "b0_bridge": sum(bool(row["b0_bridge"]) for row in sample_rows),
            "smoke20": len(smoke_anchor_rows),
        },
        "file_sha256": {
            zone_path.name: _sha256_file(zone_path),
            sample_path.name: _sha256_file(sample_path),
            anchor_path.name: _sha256_file(anchor_path),
            smoke_anchor_path.name: _sha256_file(smoke_anchor_path),
        },
    }
    summary_path = args.out_dir / "manifest_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote frozen ISSUE-25 manifests -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
