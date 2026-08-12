#!/usr/bin/env python3
"""Build the Cape Town citywide scope freeze (E0 input / E1 geometry base).

Consumes the frozen full-city inventory GPKG + canonical CPT task grid and
emits the same per-target schema as the Top-52 scope freeze:

- target-centered 96 m source box (EPSG:32734 corners reprojected to WGS84)
- A24 if area_m2 < 40 else A48
- centroid_grid via point-in-polygon on the task grid
- source_grids = source_grid[,;centroid_grid] when they differ
- footprint >96 m marked ``source_footprint_exceeds_96m`` / review_required

Exit gates this script defends:

- 111,801 bijective ``source_feature_id`` / ``anchor_id`` rows
- 1,301 non-empty source grids (782 zero-seed grids retained in roster)
- byte-stable geometry for every Top-52 overlapping anchor
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry import box

ZASOLAR_ROOT = Path("/home/gao/projects/ZAsolar")
DEFAULT_INVENTORY_GPKG = (
    ZASOLAR_ROOT
    / "results/analysis/ct_census_output_table"
    / "ct_full_inventory_2026-06-21_merged.gpkg"
)
DEFAULT_TASK_GRID = ZASOLAR_ROOT / "data/task_grid_cpt.gpkg"
DEFAULT_TOP52_MANIFEST = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
    / "manifests/ct_top52_manifest_v1.csv"
)
DEFAULT_OUTPUT_DIR = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
    / "manifests"
)

EXPECTED_ROW_COUNT = 111_801
EXPECTED_NONEMPTY_GRIDS = 1_301
EXPECTED_CANONICAL_GRIDS = 2_083
EXPECTED_A24 = 94_891
EXPECTED_A48 = 16_910
AREA_CUT_M2 = 40.0
CHIP_SIZE_M = 96.0
METRIC_CRS = "EPSG:32734"
WGS84 = "EPSG:4326"
REGION_KEY = "cape_town"
ANCHOR_PREFIX = "ct_full_inventory_2026_06_21_merged"
COHORT_VERSION = "ct_citywide_v1_20260812"
GEOMETRY_VERSION_BY_ARM = {
    "A24": "fullscan_target96_review24_v2",
    "A48": "fullscan_target96_review48_v2",
}

MANIFEST_FIELDS = [
    "anchor_id",
    "source_feature_id",
    "source_grid",
    "centroid_grid",
    "source_grids",
    "source_grid_matches_centroid_grid",
    "region_key",
    "centroid_lon",
    "centroid_lat",
    "metric_crs",
    "area_m2",
    "confidence",
    "source_width_m",
    "source_height_m",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
    "source_box_width_m",
    "source_box_height_m",
    "center_offset_m",
    "chip_arm",
    "review_extent_m",
    "geometry_exception",
    "review_required",
    "geometry_version",
    "input_inventory_sha256",
    "roster_sha256",
    "cohort_version",
]

ROSTER_FIELDS = [
    "source_grid",
    "anchor_count",
    "a24_count",
    "a48_count",
    "geometry_exception_count",
    "is_nonempty",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity_roster_sha256(df: pd.DataFrame) -> str:
    """Match Top-52 memo: UTF-8 rows anchor_id,source_feature_id,source_grid\\n
    sorted by ascending source_feature_id."""
    ordered = df.sort_values("source_feature_id", kind="mergesort")
    payload = "".join(
        f"{row.anchor_id},{int(row.source_feature_id)},{row.source_grid}\n"
        for row in ordered.itertuples(index=False)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assign_centroid_grids(
    lon: pd.Series,
    lat: pd.Series,
    task_grid: gpd.GeoDataFrame,
) -> tuple[pd.Series, int]:
    """Assign centroid_grid via point-in-polygon; nearest-cell fallback for edge misses.

    Two inventory centroids sit a few centimetres outside the CPT tiling
    (source grids CPT1678 / CPT1790).  Fallback uses STRtree nearest so the
    citywide cohort stays complete without inventing a new grid namespace.
    """
    from shapely.strtree import STRtree

    pts = gpd.GeoDataFrame(
        {"_idx": range(len(lon))},
        geometry=gpd.points_from_xy(lon, lat),
        crs=WGS84,
    )
    grid = task_grid[["gridcell_id", "geometry"]].copy().reset_index(drop=True)
    joined = gpd.sjoin(pts, grid, how="left", predicate="within")
    if joined["_idx"].duplicated().any():
        joined = joined.drop_duplicates(subset="_idx", keep="first")
    joined = joined.set_index("_idx").reindex(range(len(lon)))
    miss_mask = joined["gridcell_id"].isna()
    n_miss = int(miss_mask.sum())
    if n_miss:
        tree = STRtree(list(grid.geometry))
        ids = grid.gridcell_id.astype(str).to_numpy()
        for idx in joined.index[miss_mask]:
            nearest_i = tree.nearest(pts.geometry.iloc[int(idx)])
            joined.at[idx, "gridcell_id"] = ids[int(nearest_i)]
    return joined["gridcell_id"].astype(str), n_miss


def build_manifest(
    inventory: gpd.GeoDataFrame,
    task_grid: gpd.GeoDataFrame,
    *,
    inventory_sha256: str,
) -> tuple[pd.DataFrame, int]:
    if inventory.crs is None or inventory.crs.to_string() != METRIC_CRS:
        raise ValueError(f"inventory CRS must be {METRIC_CRS}, got {inventory.crs}")
    required = {"source_feature_id", "source_grid", "area_m2", "confidence", "geometry"}
    missing = required - set(inventory.columns)
    if missing:
        raise ValueError(f"inventory missing columns: {sorted(missing)}")
    if len(inventory) != EXPECTED_ROW_COUNT:
        raise ValueError(f"unexpected inventory rows: {len(inventory)} != {EXPECTED_ROW_COUNT}")
    if inventory.source_feature_id.isna().any() or not inventory.source_feature_id.is_unique:
        raise ValueError("source_feature_id must be non-null and unique")

    inv = inventory.sort_values("source_feature_id", kind="mergesort").reset_index(drop=True)
    bounds = inv.geometry.bounds
    source_width_m = bounds.maxx - bounds.minx
    source_height_m = bounds.maxy - bounds.miny
    cx = inv.geometry.centroid.x
    cy = inv.geometry.centroid.y

    to_wgs84 = Transformer.from_crs(METRIC_CRS, WGS84, always_xy=True)
    half = CHIP_SIZE_M / 2.0
    centroid_lon, centroid_lat = to_wgs84.transform(cx.to_numpy(), cy.to_numpy())
    chip_lon_min, chip_lat_min = to_wgs84.transform((cx - half).to_numpy(), (cy - half).to_numpy())
    chip_lon_max, chip_lat_max = to_wgs84.transform((cx + half).to_numpy(), (cy + half).to_numpy())

    # Re-project corners back to metric to assert exact 96 m + zero offset.
    to_metric = Transformer.from_crs(WGS84, METRIC_CRS, always_xy=True)
    x0, y0 = to_metric.transform(chip_lon_min, chip_lat_min)
    x1, y1 = to_metric.transform(chip_lon_max, chip_lat_max)
    box_w = pd.Series(x1) - pd.Series(x0)
    box_h = pd.Series(y1) - pd.Series(y0)
    center_x = (pd.Series(x0) + pd.Series(x1)) / 2.0
    center_y = (pd.Series(y0) + pd.Series(y1)) / 2.0
    center_offset = ((center_x - cx) ** 2 + (center_y - cy) ** 2) ** 0.5
    if float(center_offset.max()) > 1e-6:
        raise ValueError(f"non-zero center offset: max={float(center_offset.max())}")
    if float((box_w - CHIP_SIZE_M).abs().max()) > 1e-6 or float((box_h - CHIP_SIZE_M).abs().max()) > 1e-6:
        raise ValueError("source box is not a 96 m square after round-trip")

    centroid_grid_series, n_centroid_outside = assign_centroid_grids(
        pd.Series(centroid_lon),
        pd.Series(centroid_lat),
        task_grid,
    )
    centroid_grid = centroid_grid_series.to_numpy()
    source_grid = inv.source_grid.astype(str).to_numpy()
    matches = source_grid == centroid_grid
    source_grids = [
        sg if match else f"{sg};{cg}"
        for sg, cg, match in zip(source_grid, centroid_grid, matches)
    ]

    chip_arm = pd.Series(
        ["A48" if float(a) >= AREA_CUT_M2 else "A24" for a in inv.area_m2],
        dtype="object",
    )
    review_extent_m = chip_arm.map({"A24": 24.0, "A48": 48.0})
    geometry_version = chip_arm.map(GEOMETRY_VERSION_BY_ARM)
    exceeds = (source_width_m > CHIP_SIZE_M) | (source_height_m > CHIP_SIZE_M)
    geometry_exception = exceeds.map({True: "source_footprint_exceeds_96m", False: ""})
    review_required = exceeds

    # Top-52 used 1-based zero-padded ids: feature 5825 -> t00005826.
    anchor_id = [
        f"{ANCHOR_PREFIX}_t{int(fid) + 1:08d}" for fid in inv.source_feature_id
    ]

    # Provisional roster hash placeholder filled after frame is complete.
    df = pd.DataFrame(
        {
            "anchor_id": anchor_id,
            "source_feature_id": inv.source_feature_id.astype(int),
            "source_grid": source_grid,
            "centroid_grid": centroid_grid,
            "source_grids": source_grids,
            "source_grid_matches_centroid_grid": matches,
            "region_key": REGION_KEY,
            "centroid_lon": centroid_lon,
            "centroid_lat": centroid_lat,
            "metric_crs": METRIC_CRS,
            "area_m2": inv.area_m2.astype(float),
            "confidence": inv.confidence.astype(float),
            "source_width_m": source_width_m.astype(float),
            "source_height_m": source_height_m.astype(float),
            "chip_lon_min": chip_lon_min,
            "chip_lat_min": chip_lat_min,
            "chip_lon_max": chip_lon_max,
            "chip_lat_max": chip_lat_max,
            "source_box_width_m": float(CHIP_SIZE_M),
            "source_box_height_m": float(CHIP_SIZE_M),
            "center_offset_m": 0.0,
            "chip_arm": chip_arm,
            "review_extent_m": review_extent_m.astype(float),
            "geometry_exception": geometry_exception,
            "review_required": review_required,
            "geometry_version": geometry_version,
            "input_inventory_sha256": inventory_sha256,
            "roster_sha256": "",  # filled below
            "cohort_version": COHORT_VERSION,
        }
    )
    roster_hash = identity_roster_sha256(df)
    df["roster_sha256"] = roster_hash
    return df[MANIFEST_FIELDS], n_centroid_outside


def build_grid_roster(manifest: pd.DataFrame, task_grid: gpd.GeoDataFrame) -> pd.DataFrame:
    counts = (
        manifest.groupby("source_grid", sort=True)
        .agg(
            anchor_count=("anchor_id", "size"),
            a24_count=("chip_arm", lambda s: int((s == "A24").sum())),
            a48_count=("chip_arm", lambda s: int((s == "A48").sum())),
            geometry_exception_count=(
                "geometry_exception",
                lambda s: int((s.fillna("") != "").sum()),
            ),
        )
        .reset_index()
    )
    all_grids = pd.DataFrame({"source_grid": sorted(task_grid.gridcell_id.astype(str).unique())})
    roster = all_grids.merge(counts, on="source_grid", how="left")
    for col in ("anchor_count", "a24_count", "a48_count", "geometry_exception_count"):
        roster[col] = roster[col].fillna(0).astype(int)
    roster["is_nonempty"] = roster["anchor_count"] > 0
    return roster[ROSTER_FIELDS]


def validate_manifest(
    df: pd.DataFrame,
    *,
    centroid_grid_nearest_fallback_count: int = 0,
) -> dict[str, object]:
    if len(df) != EXPECTED_ROW_COUNT:
        raise ValueError(f"row_count {len(df)} != {EXPECTED_ROW_COUNT}")
    if not df.anchor_id.is_unique or not df.source_feature_id.is_unique:
        raise ValueError("anchor_id / source_feature_id must be bijective")
    if int((df.chip_arm == "A24").sum()) != EXPECTED_A24:
        raise ValueError(f"A24 count mismatch: {(df.chip_arm == 'A24').sum()}")
    if int((df.chip_arm == "A48").sum()) != EXPECTED_A48:
        raise ValueError(f"A48 count mismatch: {(df.chip_arm == 'A48').sum()}")
    n_grids = int(df.source_grid.nunique())
    if n_grids != EXPECTED_NONEMPTY_GRIDS:
        raise ValueError(f"non-empty source_grid count {n_grids} != {EXPECTED_NONEMPTY_GRIDS}")
    if not df.region_key.eq(REGION_KEY).all():
        raise ValueError("non-cape_town region_key")
    if not df.metric_crs.eq(METRIC_CRS).all():
        raise ValueError("metric_crs mismatch")
    if not df.source_box_width_m.astype(float).eq(CHIP_SIZE_M).all():
        raise ValueError("source_box_width_m must be 96")
    if df.center_offset_m.astype(float).abs().max() > 0.01:
        raise ValueError("center_offset_m must be ~0")
    exception_count = int((df.geometry_exception.fillna("") != "").sum())
    return {
        "row_count": len(df),
        "unique_source_feature_id": int(df.source_feature_id.nunique()),
        "unique_anchor_id": int(df.anchor_id.nunique()),
        "nonempty_source_grids": n_grids,
        "arm_counts": {str(k): int(v) for k, v in df.chip_arm.value_counts().sort_index().items()},
        "geometry_exception_count": exception_count,
        "source_grid_centroid_grid_mismatch_count": int(
            (~df.source_grid_matches_centroid_grid.astype(bool)).sum()
        ),
        "centroid_grid_nearest_fallback_count": int(centroid_grid_nearest_fallback_count),
        "identity_roster_sha256": str(df.roster_sha256.iloc[0]),
    }


def spotcheck_top52_overlap(
    citywide: pd.DataFrame,
    top52_path: Path,
) -> dict[str, object]:
    if not top52_path.exists():
        return {"skipped": True, "reason": f"missing {top52_path}"}
    top = pd.read_csv(top52_path)
    geom_cols = [
        "source_feature_id",
        "anchor_id",
        "source_grid",
        "centroid_grid",
        "source_grids",
        "centroid_lon",
        "centroid_lat",
        "source_width_m",
        "source_height_m",
        "chip_lon_min",
        "chip_lat_min",
        "chip_lon_max",
        "chip_lat_max",
        "source_box_width_m",
        "source_box_height_m",
        "center_offset_m",
        "chip_arm",
        "review_extent_m",
        "geometry_version",
        "geometry_exception",
        "review_required",
        "area_m2",
        "confidence",
    ]
    left = citywide[geom_cols].copy()
    right = top[geom_cols].copy()
    # Normalize exception NaN vs empty.
    for frame in (left, right):
        frame["geometry_exception"] = frame["geometry_exception"].fillna("")
        frame["review_required"] = frame["review_required"].astype(bool)
    merged = left.merge(right, on="source_feature_id", suffixes=("_cw", "_t52"), how="inner")
    if len(merged) != len(top):
        raise ValueError(
            f"Top-52 overlap incomplete: {len(merged)} citywide matches for {len(top)} top52 rows"
        )
    mismatches: dict[str, int] = {}
    for col in geom_cols:
        if col == "source_feature_id":
            continue
        a = merged[f"{col}_cw"]
        b = merged[f"{col}_t52"]
        if pd.api.types.is_float_dtype(a) or pd.api.types.is_float_dtype(b):
            bad = (a.astype(float) - b.astype(float)).abs() > 1e-9
        else:
            bad = a.astype(str) != b.astype(str)
        n_bad = int(bad.sum())
        if n_bad:
            mismatches[col] = n_bad
    if mismatches:
        raise ValueError(f"Top-52 geometry mismatch on columns: {mismatches}")
    return {
        "skipped": False,
        "overlap_rows": int(len(merged)),
        "geometry_byte_stable": True,
        "mismatched_columns": {},
    }


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-gpkg", type=Path, default=DEFAULT_INVENTORY_GPKG)
    parser.add_argument("--task-grid", type=Path, default=DEFAULT_TASK_GRID)
    parser.add_argument("--top52-manifest", type=Path, default=DEFAULT_TOP52_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="Overwrite outputs inside an existing output-dir (files replaced).",
    )
    args = parser.parse_args()

    if not args.inventory_gpkg.exists():
        raise SystemExit(f"inventory not found: {args.inventory_gpkg}")
    if not args.task_grid.exists():
        raise SystemExit(f"task grid not found: {args.task_grid}")
    if args.output_dir.exists() and not args.allow_existing:
        # Permit empty dir creation path; refuse non-empty without flag.
        if any(args.output_dir.iterdir()):
            raise SystemExit(f"refusing to overwrite non-empty output: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    inventory_sha = sha256_file(args.inventory_gpkg)
    inventory = gpd.read_file(args.inventory_gpkg)
    task_grid = gpd.read_file(args.task_grid)
    if len(task_grid) != EXPECTED_CANONICAL_GRIDS:
        raise SystemExit(
            f"canonical grid count {len(task_grid)} != {EXPECTED_CANONICAL_GRIDS}"
        )

    manifest, n_centroid_fallback = build_manifest(
        inventory, task_grid, inventory_sha256=inventory_sha
    )
    stats = validate_manifest(
        manifest, centroid_grid_nearest_fallback_count=n_centroid_fallback
    )
    roster = build_grid_roster(manifest, task_grid)
    if int(roster.is_nonempty.sum()) != EXPECTED_NONEMPTY_GRIDS:
        raise SystemExit(
            f"roster nonempty grids {int(roster.is_nonempty.sum())} != {EXPECTED_NONEMPTY_GRIDS}"
        )
    if len(roster) != EXPECTED_CANONICAL_GRIDS:
        raise SystemExit(f"roster rows {len(roster)} != {EXPECTED_CANONICAL_GRIDS}")
    overlap = spotcheck_top52_overlap(manifest, args.top52_manifest)

    manifest_path = args.output_dir / "ct_citywide_scope_manifest.csv"
    roster_path = args.output_dir / "ct_citywide_grid_roster.csv"
    write_csv(manifest, manifest_path)
    write_csv(roster, roster_path)

    memo = {
        "schema_version": "ct_scope_freeze_v1",
        "cohort_version": COHORT_VERSION,
        "created_date": "2026-08-12",
        "selection_rule": "full frozen inventory; all non-empty source grids; zero-seed grids retained in roster only",
        "anchor_count": stats["row_count"],
        "arm_counts": stats["arm_counts"],
        "grid_count_nonempty": stats["nonempty_source_grids"],
        "grid_count_canonical": EXPECTED_CANONICAL_GRIDS,
        "geometry_exception_count": stats["geometry_exception_count"],
        "source_grid_centroid_grid_mismatch_count": stats[
            "source_grid_centroid_grid_mismatch_count"
        ],
        "centroid_grid_nearest_fallback_count": stats[
            "centroid_grid_nearest_fallback_count"
        ],
        "identity_roster_hash_definition": (
            "sha256 of UTF-8 rows anchor_id,source_feature_id,source_grid\\n "
            "in ascending source_feature_id order"
        ),
        "identity_roster_sha256": stats["identity_roster_sha256"],
        "manifest_sha256": sha256_file(manifest_path),
        "roster_sha256": sha256_file(roster_path),
        "inputs": {
            str(args.inventory_gpkg): inventory_sha,
            str(args.task_grid): sha256_file(args.task_grid),
        },
        "top52_overlap_spotcheck": overlap,
    }
    memo_path = args.output_dir / "selection_memo_v1.json"
    memo_path.write_text(json.dumps(memo, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    sha_path = args.output_dir / "scope_artifacts.sha256"
    with sha_path.open("w", encoding="utf-8") as handle:
        for path in (manifest_path, roster_path, memo_path):
            handle.write(f"{sha256_file(path)}  {path.name}\n")

    print(json.dumps(memo, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
