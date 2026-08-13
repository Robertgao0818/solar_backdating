#!/usr/bin/env python3
"""Build the cohort-scoped Cape Town install-date CSV/GPKG deliverable.

This is intentionally separate from the legacy Johannesburg deliverable
builder.  It joins the frozen CT anchors to the CT inventory by the explicit
``source_feature_id`` property, retains only the 52-grid cohort, writes native
EPSG:32734 polygons, and enforces the 2025-01-31 census ceiling.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import fiona
from pyproj import Transformer
from shapely.geometry import shape

RUN_ROOT = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
)
DEFAULT_GEOMETRY = Path(
    "/home/gao/projects/ZAsolar/results/analysis/ct_census_output_table/"
    "ct_full_inventory_2026-06-21_merged.gpkg"
)
DEFAULT_ANCHORS = RUN_ROOT / "anchors_v1/anchors_all.csv"
DEFAULT_INTERVALS = RUN_ROOT / "intervals/install_intervals_all.csv"
DEFAULT_OUTPUT = RUN_ROOT / "deliverable"
INPUT_LAYER = "ct_solar_inventory"
OUTPUT_LAYER = "ct_solar_install_dated"
METRIC_CRS = "EPSG:32734"
CENSUS_CUTOFF = date(2025, 1, 31)
EXPECTED_COUNT = 21_453
# Fiona/GDAL stamps gpkg_contents.last_change with wall-clock UTC. Freeze it so
# two writes of the same rows are byte-identical (Leg-R R1).
GPKG_LAST_CHANGE = "2025-01-31T00:00:00.000Z"

ANCHOR_REQUIRED_FIELDS = {
    "anchor_id",
    "source_feature_id",
    "source_grid",
    "source_grids",
    "region_key",
    "centroid_lon",
    "centroid_lat",
    "metric_crs",
    "chip_arm",
    "review_extent_m",
    "geometry_version",
    "cohort_version",
}
INTERVAL_REQUIRED_FIELDS = {
    "anchor_id",
    "region_key",
    "grid_id",
    "status",
    "latest_absent_date",
    "earliest_present_date",
    "install_interval_start",
    "install_interval_end",
    "install_mid_estimate",
    "n_observations",
    "n_absent",
    "n_present",
    "n_unusable",
    "n_rounds",
    "scan_state_path",
    "confidence",
    "notes",
}

CSV_FIELDS = [
    "source_feature_id",
    "source_grid",
    "source_grids",
    "centroid_lon",
    "centroid_lat",
    "area_m2",
    "confidence",
    "date_provider",
    "date_status",
    "date_is_bound",
    "install_date",
    "install_interval_start",
    "install_interval_end",
    "latest_absent_date",
    "earliest_present_date",
    "install_confidence",
    "source_anchor_id",
    "undated_reason",
    "scan_status",
    "n_observations",
    "n_absent",
    "n_present",
    "n_unusable",
    "n_rounds",
    "scan_state_path",
    "scan_notes",
    "left_censored",
    "census_bound",
    "chip_arm",
    "review_extent_m",
    "geometry_version",
    "cohort_version",
    "census_cutoff",
]


@dataclass
class Classified:
    date_provider: str = ""
    date_status: str = ""
    date_is_bound: str = ""
    install_date: str = ""
    install_interval_start: str = ""
    install_interval_end: str = ""
    earliest_present_date: str = ""
    install_confidence: str = ""
    undated_reason: str = ""
    left_censored: int = 0
    census_bound: int = 0


def _parse(value: str) -> date:
    return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(interval: dict[str, str]) -> Classified:
    status = interval["status"].strip()
    out = Classified()
    if status == "done_appears":
        start = interval["install_interval_start"].strip()
        end = interval["install_interval_end"].strip()
        mid = interval["install_mid_estimate"].strip()
        if not start or not end or not mid:
            out.undated_reason = "inverted_interval_mislabeled_appears"
            return out
        if not (_parse(start) <= _parse(mid) <= _parse(end)):
            out.undated_reason = "invalid_appears_interval"
            return out
        out.date_provider = "gehi_merged"
        out.date_status = status
        out.date_is_bound = "0"
        out.install_date = mid
        out.install_interval_start = start
        out.install_interval_end = end
        out.earliest_present_date = interval["earliest_present_date"].strip()
        out.install_confidence = interval["confidence"].strip()
        return out
    if status == "done_installed_during_census":
        start = interval["install_interval_start"].strip()
        end = interval["install_interval_end"].strip()
        if not start or not end:
            out.undated_reason = "inconsistent_installed_during_census"
            return out
        out.date_provider = "gehi_merged_census"
        out.date_status = status
        out.date_is_bound = "1"
        out.install_date = end
        out.install_interval_start = start
        out.install_interval_end = end
        out.earliest_present_date = end
        out.install_confidence = interval["confidence"].strip()
        out.census_bound = 1
        return out
    if status == "done_already_present_before_geid_history":
        end = interval["install_interval_end"].strip()
        if not end:
            out.undated_reason = "inconsistent_already_present"
            return out
        out.date_provider = "gehi_merged_leftcensor"
        out.date_status = status
        out.date_is_bound = "1"
        out.install_date = end
        out.install_interval_end = end
        out.earliest_present_date = interval["earliest_present_date"].strip()
        out.install_confidence = "low"
        out.left_censored = 1
        return out
    if status.startswith("done_ambiguous_"):
        out.undated_reason = status.removeprefix("done_ambiguous_")
    else:
        out.undated_reason = f"unhandled_status_{status or 'blank'}"
    return out


def load_geometry(path: Path, wanted: set[int]) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    transformer = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)
    with fiona.open(path, layer=INPUT_LAYER) as src:
        if "32734" not in str(src.crs):
            raise ValueError(f"CT inventory CRS must be {METRIC_CRS}, got {src.crs}")
        for feature in src:
            props = dict(feature["properties"])
            sfid = int(props["source_feature_id"])
            if sfid not in wanted:
                continue
            if sfid in rows:
                raise ValueError(f"duplicate inventory source_feature_id {sfid}")
            geom = shape(feature["geometry"])
            lon, lat = transformer.transform(geom.centroid.x, geom.centroid.y)
            rows[sfid] = {
                "geometry": feature["geometry"],
                "source_grid": str(props.get("source_grid") or ""),
                "area_m2": props.get("area_m2"),
                "confidence": props.get("confidence"),
                "centroid_lon": lon,
                "centroid_lat": lat,
            }
    missing = wanted - set(rows)
    if missing:
        raise ValueError(f"{len(missing)} frozen source_feature_id values missing from inventory")
    return rows


def build_rows(
    anchors: list[dict[str, str]],
    intervals_by_anchor: dict[str, dict[str, str]],
    geometry_by_sfid: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for anchor in anchors:
        anchor_id = anchor["anchor_id"].strip()
        sfid = int(anchor["source_feature_id"])
        interval = intervals_by_anchor.get(anchor_id)
        if interval is None:
            raise ValueError(f"interval missing for anchor_id {anchor_id}")
        if interval["region_key"].strip() != "cape_town":
            raise ValueError(f"non-CT interval for {anchor_id}")
        if interval["grid_id"].strip() != anchor["source_grid"].strip():
            raise ValueError(f"grid mismatch for {anchor_id}")
        geom = geometry_by_sfid[sfid]
        if geom["source_grid"] != anchor["source_grid"].strip():
            raise ValueError(f"inventory/anchor source_grid mismatch for {anchor_id}")
        classified = classify(interval)
        output.append(
            {
                "source_feature_id": sfid,
                "source_grid": anchor["source_grid"].strip(),
                "source_grids": anchor["source_grids"].strip(),
                "centroid_lon": geom["centroid_lon"],
                "centroid_lat": geom["centroid_lat"],
                "area_m2": geom["area_m2"],
                "confidence": geom["confidence"],
                "date_provider": classified.date_provider,
                "date_status": classified.date_status,
                "date_is_bound": classified.date_is_bound,
                "install_date": classified.install_date,
                "install_interval_start": classified.install_interval_start,
                "install_interval_end": classified.install_interval_end,
                "latest_absent_date": interval["latest_absent_date"].strip(),
                "earliest_present_date": classified.earliest_present_date,
                "install_confidence": classified.install_confidence,
                "source_anchor_id": anchor_id,
                "undated_reason": classified.undated_reason,
                "scan_status": interval["status"].strip(),
                "n_observations": int(interval["n_observations"] or 0),
                "n_absent": int(interval["n_absent"] or 0),
                "n_present": int(interval["n_present"] or 0),
                "n_unusable": int(interval["n_unusable"] or 0),
                "n_rounds": int(interval["n_rounds"] or 0),
                "scan_state_path": interval["scan_state_path"].strip(),
                "scan_notes": interval["notes"].strip(),
                "left_censored": classified.left_censored,
                "census_bound": classified.census_bound,
                "chip_arm": anchor["chip_arm"].strip(),
                "review_extent_m": float(anchor["review_extent_m"]),
                "geometry_version": anchor["geometry_version"].strip(),
                "cohort_version": anchor["cohort_version"].strip(),
                "census_cutoff": CENSUS_CUTOFF.isoformat(),
                "_geometry": geom["geometry"],
            }
        )
    return output


def run_gates(rows: list[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    ids = [row["source_anchor_id"] for row in rows]
    sfids = [row["source_feature_id"] for row in rows]
    ceiling_violations: list[str] = []
    interval_violations: list[str] = []
    for row in rows:
        start = row["install_interval_start"]
        mid = row["install_date"]
        end = row["install_interval_end"]
        for value in (mid, end):
            if value and _parse(value) > CENSUS_CUTOFF:
                ceiling_violations.append(row["source_anchor_id"])
                break
        if mid and start and end and not (_parse(start) <= _parse(mid) <= _parse(end)):
            interval_violations.append(row["source_anchor_id"])
    categories = {
        "dated_bracket": sum(row["date_is_bound"] == "0" for row in rows),
        "census_bound": sum(row["census_bound"] == 1 for row in rows),
        "left_censored": sum(row["left_censored"] == 1 for row in rows),
        "undated": sum(bool(row["undated_reason"]) for row in rows),
    }
    return {
        "row_count": {"actual": len(rows), "expected": expected_count, "pass": len(rows) == expected_count},
        "anchor_id_unique": {"pass": len(ids) == len(set(ids))},
        "source_feature_id_unique": {"pass": len(sfids) == len(set(sfids))},
        "ct_namespace": {"pass": all(row["source_grid"].startswith("CPT") for row in rows)},
        "census_ceiling": {
            "cutoff": CENSUS_CUTOFF.isoformat(),
            "n_violations": len(ceiling_violations),
            "examples": ceiling_violations[:10],
            "pass": not ceiling_violations,
        },
        "interval_invariant": {
            "n_violations": len(interval_violations),
            "examples": interval_violations[:10],
            "pass": not interval_violations,
        },
        "coverage_reconcile": {
            **categories,
            "total": sum(categories.values()),
            "pass": sum(categories.values()) == expected_count,
        },
    }


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows({field: row[field] for field in CSV_FIELDS} for row in rows)


def write_gpkg(rows: list[dict[str, Any]], path: Path) -> None:
    schema = {
        "geometry": "Polygon",
        "properties": {
            field: (
                "int"
                if field
                in {
                    "source_feature_id",
                    "n_observations",
                    "n_absent",
                    "n_present",
                    "n_unusable",
                    "n_rounds",
                    "left_censored",
                    "census_bound",
                }
                else "float"
                if field in {"centroid_lon", "centroid_lat", "area_m2", "confidence", "review_extent_m"}
                else "str"
            )
            for field in CSV_FIELDS
        },
    }
    with fiona.open(
        path,
        "w",
        driver="GPKG",
        crs=METRIC_CRS,
        schema=schema,
        layer=OUTPUT_LAYER,
    ) as dst:
        for row in rows:
            dst.write(
                {
                    "geometry": row["_geometry"],
                    "properties": {field: row[field] for field in CSV_FIELDS},
                }
            )


def freeze_gpkg_last_change(path: Path, last_change: str = GPKG_LAST_CHANGE) -> None:
    """Overwrite GDAL's wall-clock ``gpkg_contents.last_change`` with a frozen stamp."""
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE gpkg_contents SET last_change = ?", (last_change,))
        connection.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry-gpkg", type=Path, default=DEFAULT_GEOMETRY)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--intervals-csv", type=Path, default=DEFAULT_INTERVALS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--tag", default="2026-07-24")
    parser.add_argument("--expected-count", type=int, default=EXPECTED_COUNT)
    args = parser.parse_args()

    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    anchor_fields, anchors = _read_csv(args.anchors_csv)
    interval_fields, intervals = _read_csv(args.intervals_csv)
    missing_anchor = ANCHOR_REQUIRED_FIELDS - set(anchor_fields)
    missing_interval = INTERVAL_REQUIRED_FIELDS - set(interval_fields)
    if missing_anchor:
        raise ValueError(f"anchors missing fields: {sorted(missing_anchor)}")
    if missing_interval:
        raise ValueError(f"intervals missing fields: {sorted(missing_interval)}")
    if len(anchors) != args.expected_count:
        raise ValueError(f"unexpected CT anchor count: {len(anchors)}")
    if any(row["region_key"].strip() != "cape_town" for row in anchors):
        raise ValueError("anchors contain a non-Cape Town row")
    if any(row["metric_crs"].strip() != METRIC_CRS for row in anchors):
        raise ValueError(f"anchors must use {METRIC_CRS}")
    anchor_ids = [row["anchor_id"].strip() for row in anchors]
    interval_ids = [row["anchor_id"].strip() for row in intervals]
    if len(anchor_ids) != len(set(anchor_ids)) or len(interval_ids) != len(set(interval_ids)):
        raise ValueError("anchor_id must be unique in both inputs")
    if set(anchor_ids) != set(interval_ids):
        raise ValueError("interval anchor set does not exactly match frozen CT anchors")

    wanted = {int(row["source_feature_id"]) for row in anchors}
    geometry = load_geometry(args.geometry_gpkg, wanted)
    rows = build_rows(anchors, {row["anchor_id"].strip(): row for row in intervals}, geometry)
    gates = run_gates(rows, args.expected_count)
    failed = [name for name, result in gates.items() if not result["pass"]]
    if failed:
        raise ValueError(f"deliverable gates failed before write: {failed}")

    args.output_dir.mkdir(parents=True)
    stem = f"ct_top52_install_dated_{args.tag}"
    csv_path = args.output_dir / f"{stem}.csv"
    gpkg_path = args.output_dir / f"{stem}.gpkg"
    gates_path = args.output_dir / "gates.json"
    write_csv(rows, csv_path)
    write_gpkg(rows, gpkg_path)
    freeze_gpkg_last_change(gpkg_path)
    gates_path.write_text(json.dumps(gates, indent=2, sort_keys=True) + "\n")
    manifest = {
        "schema_version": "ct_top52_install_dated_v1",
        "cohort_scope": "frozen Cape Town 52-grid complete tie band",
        "row_count": len(rows),
        "metric_crs": METRIC_CRS,
        "census_cutoff": CENSUS_CUTOFF.isoformat(),
        "inputs": {
            str(args.geometry_gpkg): sha256(args.geometry_gpkg),
            str(args.anchors_csv): sha256(args.anchors_csv),
            str(args.intervals_csv): sha256(args.intervals_csv),
        },
        "outputs": {
            str(csv_path): sha256(csv_path),
            str(gpkg_path): sha256(gpkg_path),
            str(gates_path): sha256(gates_path),
        },
    }
    (args.output_dir / "release_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
