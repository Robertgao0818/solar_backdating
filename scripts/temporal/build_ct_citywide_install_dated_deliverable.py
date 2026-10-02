#!/usr/bin/env python3
"""Build the Cape Town citywide install-date CSV/GPKG research preview.

Joins the frozen citywide 111,801-anchor roster to:

* the 2026-08-03 Top-52 Dropbox deliverable (21,453 rows, not re-decoded);
* the 2026-09-02 27-wave intervals after the wave_04 rescue revert (90,348).

The two sources are a disjoint union of the citywide roster. Output schema is
the Top-52 deliverable plus citywide provenance columns (``h3_r7``,
``source_kind``, ``wave``, ``cohort``).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path
from typing import Any

from scripts.temporal.build_ct_install_dated_deliverable import (
    ANCHOR_REQUIRED_FIELDS,
    CENSUS_CUTOFF,
    CSV_FIELDS,
    INTERVAL_REQUIRED_FIELDS,
    METRIC_CRS,
    build_rows,
    freeze_gpkg_last_change,
    load_geometry,
    run_gates,
    sha256,
    write_csv,
    write_gpkg,
)

CITY_ROOT = (
    Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
)
DEFAULT_GEOMETRY = Path(
    "/home/gao/projects/ZAsolar/results/analysis/ct_census_output_table/"
    "ct_full_inventory_2026-06-21_merged.gpkg"
)
DEFAULT_ANCHORS = CITY_ROOT / "anchors_v1/anchors_all.csv"
DEFAULT_WAVE_INTERVALS = (
    CITY_ROOT / "e8_descriptive_20260902/intervals/install_intervals_all.csv"
)
DEFAULT_CLASSIFIED = CITY_ROOT / "e8_descriptive_20260902/classified_citywide_all.csv"
DEFAULT_TOP52 = Path(
    "/home/gao/Dropbox/RA_Solar/Gao/backdating/"
    "ct_top52_install_dated_2026-08-03_research_preview/"
    "ct_top52_install_dated_20260803_lite_v2.csv"
)
DEFAULT_HTML = (
    CITY_ROOT
    / "e8_descriptive_20260902"
    / "descriptive_stats_2026-09-02-citywide-aligned.html"
)
DEFAULT_H3_CSV = CITY_ROOT / "e8_descriptive_20260902/h3_r7_cells.csv"
DEFAULT_H3_GEOJSON = CITY_ROOT / "e8_descriptive_20260902/h3_r7_cells.geojson"
DEFAULT_FIGS = CITY_ROOT / "e8_descriptive_20260902/figs_aligned"
DEFAULT_OUTPUT = CITY_ROOT / "deliverable_2026-09-02"
DEFAULT_DROPBOX = Path(
    "/home/gao/Dropbox/RA_Solar/Gao/backdating/"
    "ct_citywide_install_dated_2026-09-02_research_preview"
)

EXPECTED_COUNT = 111_801
EXPECTED_TOP52 = 21_453
EXPECTED_WAVE = 90_348
EXPECTED_COVERAGE = {
    "dated_bracket": 73_336,
    "census_bound": 27_591,
    "left_censored": 9_843,
    "undated": 1_031,
}
CITYWIDE_EXTRA_FIELDS = ["h3_r7", "source_kind", "wave", "cohort"]
CSV_FIELDS_CITYWIDE = CSV_FIELDS + CITYWIDE_EXTRA_FIELDS
SCHEMA_VERSION = "ct_citywide_install_dated_v1"
STEM = "ct_citywide_install_dated_20260902"
PACKAGE_COPIES = (
    ("descriptive_stats_2026-09-02-citywide-aligned.html", DEFAULT_HTML),
    ("h3_r7_cells.csv", DEFAULT_H3_CSV),
    ("h3_r7_cells.geojson", DEFAULT_H3_GEOJSON),
)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def interval_from_top52_row(row: dict[str, str]) -> dict[str, str]:
    """Invert the Top-52 deliverable row back into the interval contract."""
    return {
        "anchor_id": row["source_anchor_id"].strip(),
        "region_key": "cape_town",
        "grid_id": row["source_grid"].strip(),
        "status": row["scan_status"].strip(),
        "latest_absent_date": row["latest_absent_date"].strip(),
        "earliest_present_date": row["earliest_present_date"].strip(),
        "install_interval_start": row["install_interval_start"].strip(),
        "install_interval_end": row["install_interval_end"].strip(),
        "install_mid_estimate": row["install_date"].strip(),
        "n_observations": row["n_observations"].strip(),
        "n_absent": row["n_absent"].strip(),
        "n_present": row["n_present"].strip(),
        "n_unusable": row["n_unusable"].strip(),
        "n_rounds": row["n_rounds"].strip(),
        "scan_state_path": row["scan_state_path"].strip(),
        "confidence": row["install_confidence"].strip(),
        "notes": row["scan_notes"].strip(),
    }


INTERVAL_CSV_FIELDS = [
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
]


def write_intervals(rows: list[dict[str, str]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INTERVAL_CSV_FIELDS)
        writer.writeheader()
        writer.writerows({field: row[field] for field in INTERVAL_CSV_FIELDS} for row in rows)


def attach_citywide_fields(
    rows: list[dict[str, Any]],
    classified_by_anchor: dict[str, dict[str, str]],
) -> None:
    missing = 0
    for row in rows:
        extra = classified_by_anchor.get(row["source_anchor_id"])
        if extra is None:
            missing += 1
            continue
        row["h3_r7"] = extra["h3_r7"].strip()
        row["source_kind"] = extra["source_kind"].strip()
        row["wave"] = extra["wave"].strip()
        row["cohort"] = extra["cohort"].strip()
    if missing:
        raise ValueError(f"{missing} deliverable rows missing from classified table")
    blank_h3 = [row["source_anchor_id"] for row in rows if not row["h3_r7"]]
    if blank_h3:
        raise ValueError(f"{len(blank_h3)} rows missing h3_r7, e.g. {blank_h3[:5]}")


def write_sha256sums(directory: Path, names: list[str]) -> Path:
    lines = []
    for name in names:
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(path)
        lines.append(f"{sha256(path)}  {name}")
    out = directory / "SHA256SUMS.txt"
    out.write_text("\n".join(lines) + "\n")
    return out


def write_readme(path: Path, gates: dict[str, Any]) -> None:
    cov = gates["coverage_reconcile"]
    dated = cov["dated_bracket"]
    census = cov["census_bound"]
    left = cov["left_censored"]
    undated = cov["undated"]
    n = cov["total"]
    bounded = dated + census
    path.write_text(
        f"""# Cape Town citywide install-date research preview

Delivery date: 2026-09-02
Schema version: `{SCHEMA_VERSION}`

This delivery contains the complete Cape Town rooftop-PV census used for
install-date backdating: **{n:,} polygons / {n:,} anchors**. It is the
polygon-level analysis table (CSV + GPKG), not a hex×year stock panel.

Each temporal estimate is a **first-visible-appearance interval under the
available GEHistoricalImagery (GEHI) observations**, not a verified physical
installation date. The Cape Town census cutoff is 2025-01-31; no later image
is used as installation-date evidence.

## Files

- `{STEM}.csv` — analysis table, {n:,} rows (EPSG:4326 centroids).
- `{STEM}.gpkg` — spatial version of the same table (metric CRS EPSG:32734;
  layer `ct_solar_install_dated`).
- `descriptive_stats_2026-09-02-citywide-aligned.html` — self-contained
  descriptive-statistics report (two Esri-basemap figures; opens offline).
- `h3_r7_cells.csv` / `h3_r7_cells.geojson` — Uber H3 resolution-7 cells
  covering the {n:,} centroids (join key `h3_index` = row-level `h3_r7`).
- `figs/map_coverage_class.png` and `figs/map_median_year.png` — the two
  lead maps from the HTML, also as standalone PNGs.
- `gates.json` — structural and interval-invariant checks.
- `release_manifest.json` — cohort scope, input provenance, schema, hashes.
- `SHA256SUMS.txt` — hashes of the files in this Dropbox delivery.

## How the 111,801 rows were assembled

The citywide roster is a disjoint union of two already-terminal production
runs. They do not overlap on `source_anchor_id` / `source_feature_id`, and
their union is the full census:

| queue | n | source |
|---|---:|---|
| Top-52 high-density grids | {EXPECTED_TOP52:,} | Dropbox `ct_top52_install_dated_20260803_lite_v2.csv` (not re-decoded) |
| remaining 27 waves | {EXPECTED_WAVE:,} | 2026-09-02 decode of citywide scan states after reverting the wave_04 3.6-high overlay |

27-wave scoring used Gemini 3.1 Flash Lite as primary, Gemini 3.6 Flash High
rescue of Lite-ambiguous anchors, and a Lite rescore of CPT3622. Wave 04's
638 apparent `gemini_failed` rows were a dead 3.6 rescue (HTTP 400
`project_id`) that had been allowed to overwrite successful Lite states;
those rows were reverted to Lite and re-inferred with dip-repair. Top-52 was
not sent through 3.6 rescue.

## Outcome classes

The {n:,} rows comprise:

- **{dated:,} dated brackets** (`done_appears`, {dated / n:.1%});
- **{census:,} census-bounded records** (`done_installed_during_census`, {census / n:.1%});
- **{left:,} left-censored records** (`done_already_present_before_geid_history`, {left / n:.1%});
- **{undated:,} undated/ambiguous records** ({undated / n:.1%}).

Bounded (dated + census) = **{bounded:,} ({bounded / n:.1%})**. No row is
dropped merely because its date is censored or ambiguous. `gemini_failed` = 0.

## Important validation limitation

This is a **research preview for descriptive analysis and external-dataset
validation**, not an internally validated final install-date product.

The corrected Top-52 Codex temporal review did not pass the preregistered
accuracy/stability gates (exact-bracket Wilson lower bound 17.3%; the 70%
gate is unmet). Codex temporal verdicts are not human ground truth. The
citywide run has not had a multi-model visual panel review (Leg-E E8).

Do not describe `install_date` as an independently verified physical install
date. Preferred wording is **model-derived GEHI first-visible-appearance
estimate**. External validation is required before making accuracy claims.

`install_date` on dated rows is the midpoint of
`[install_interval_start, install_interval_end]`. That midpoint is a
convenience field, not the primary statistical channel. Census-bounded rows
use the 2025-01-31 cutoff as `install_date`; left-censored rows use the
first observed present date. Treating those as event dates manufactures
peaks at 2025 and 2019.

## Analysis guidance

For primary analysis, retain the interval and censoring structure rather than
treating every `install_date` as an exact event date. Useful fields include:

- identifiers and geography: `source_feature_id`, `source_anchor_id`,
  `source_grid`, `source_grids`, `centroid_lon`, `centroid_lat`, `h3_r7`;
- temporal bounds: `install_interval_start`, `install_interval_end`,
  `latest_absent_date`, `earliest_present_date`, `install_date`;
- interpretation: `date_status`, `date_is_bound`, `left_censored`,
  `census_bound`, `undated_reason`, `scan_status`;
- provenance: `source_kind` (`top52_lite` / `primary_lite` / `rescue_36high`
  / `rescore`), `wave`, `cohort`;
- evidence: `install_confidence`, `n_observations`, `n_absent`, `n_present`,
  `n_unusable`, `n_rounds`, `chip_arm`, and `geometry_version`.

Recommended robustness checks include interval lower/end/midpoint
specifications, high-confidence-only results, exclusion of censored or
ambiguous records, and direct comparison against an independent external
dataset. Do not treat the 1,031 undated rows as random missingness — they
concentrate in non-monotonic and no-recent-anchor cases.

Citywide dated midpoints span 2019-01-22 … 2024-08-30. Dated interval width
median is 37 days (IQR 30–91); 80.0% are ≤180 days.

## Structural gates

All of the following passed before write: exact row count {n:,}; unique
`source_anchor_id` and `source_feature_id`; Cape Town `CPT*` namespace; no
inverted intervals; zero inference evidence after 2025-01-31; coverage
classes sum to {n:,} and match the frozen 2026-09-02 classified table.

Output hashes are in `SHA256SUMS.txt` and `release_manifest.json`.
""".lstrip()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry-gpkg", type=Path, default=DEFAULT_GEOMETRY)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--wave-intervals-csv", type=Path, default=DEFAULT_WAVE_INTERVALS)
    parser.add_argument("--top52-csv", type=Path, default=DEFAULT_TOP52)
    parser.add_argument("--classified-csv", type=Path, default=DEFAULT_CLASSIFIED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dropbox-dir", type=Path, default=None)
    parser.add_argument("--expected-count", type=int, default=EXPECTED_COUNT)
    args = parser.parse_args()

    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    if args.dropbox_dir is not None and args.dropbox_dir.exists():
        raise SystemExit(f"refusing to overwrite existing Dropbox dir: {args.dropbox_dir}")

    anchor_fields, anchors = _read_csv(args.anchors_csv)
    wave_fields, wave_intervals = _read_csv(args.wave_intervals_csv)
    top52_fields, top52_rows = _read_csv(args.top52_csv)
    classified_fields, classified_rows = _read_csv(args.classified_csv)
    missing_anchor = ANCHOR_REQUIRED_FIELDS - set(anchor_fields)
    missing_interval = INTERVAL_REQUIRED_FIELDS - set(wave_fields)
    if missing_anchor:
        raise ValueError(f"anchors missing fields: {sorted(missing_anchor)}")
    if missing_interval:
        raise ValueError(f"27-wave intervals missing fields: {sorted(missing_interval)}")
    needed_top52 = {
        "source_anchor_id",
        "source_grid",
        "scan_status",
        "latest_absent_date",
        "earliest_present_date",
        "install_interval_start",
        "install_interval_end",
        "install_date",
        "n_observations",
        "n_absent",
        "n_present",
        "n_unusable",
        "n_rounds",
        "scan_state_path",
        "install_confidence",
        "scan_notes",
    }
    missing_top52 = needed_top52 - set(top52_fields)
    if missing_top52:
        raise ValueError(f"Top-52 deliverable missing fields: {sorted(missing_top52)}")
    needed_classified = {"anchor_id", "h3_r7", "source_kind", "wave", "cohort"}
    missing_classified = needed_classified - set(classified_fields)
    if missing_classified:
        raise ValueError(f"classified table missing fields: {sorted(missing_classified)}")

    if len(anchors) != args.expected_count:
        raise ValueError(f"unexpected CT citywide anchor count: {len(anchors)}")
    if len(wave_intervals) != EXPECTED_WAVE:
        raise ValueError(f"unexpected 27-wave interval count: {len(wave_intervals)}")
    if len(top52_rows) != EXPECTED_TOP52:
        raise ValueError(f"unexpected Top-52 count: {len(top52_rows)}")
    if len(classified_rows) != args.expected_count:
        raise ValueError(f"unexpected classified count: {len(classified_rows)}")
    if any(row["region_key"].strip() != "cape_town" for row in anchors):
        raise ValueError("anchors contain a non-Cape Town row")
    if any(row["metric_crs"].strip() != METRIC_CRS for row in anchors):
        raise ValueError(f"anchors must use {METRIC_CRS}")

    top52_intervals = [interval_from_top52_row(row) for row in top52_rows]
    intervals = wave_intervals + top52_intervals
    anchor_ids = [row["anchor_id"].strip() for row in anchors]
    interval_ids = [row["anchor_id"].strip() for row in intervals]
    wave_ids = {row["anchor_id"].strip() for row in wave_intervals}
    top52_ids = {row["anchor_id"].strip() for row in top52_intervals}
    if wave_ids & top52_ids:
        raise ValueError(f"Top-52 overlaps 27-wave: {len(wave_ids & top52_ids)}")
    if len(anchor_ids) != len(set(anchor_ids)) or len(interval_ids) != len(set(interval_ids)):
        raise ValueError("anchor_id must be unique in both inputs")
    if set(anchor_ids) != set(interval_ids):
        raise ValueError("interval anchor set does not exactly match citywide anchors")

    classified_by_anchor = {row["anchor_id"].strip(): row for row in classified_rows}
    if set(classified_by_anchor) != set(anchor_ids):
        raise ValueError("classified table is not a 1:1 match to citywide anchors")

    wanted = {int(row["source_feature_id"]) for row in anchors}
    geometry = load_geometry(args.geometry_gpkg, wanted)
    rows = build_rows(anchors, {row["anchor_id"].strip(): row for row in intervals}, geometry)
    attach_citywide_fields(rows, classified_by_anchor)
    gates = run_gates(rows, args.expected_count)
    cov = gates["coverage_reconcile"]
    coverage_mismatch = {
        key: {"actual": cov[key], "expected": expected}
        for key, expected in EXPECTED_COVERAGE.items()
        if cov[key] != expected
    }
    gates["frozen_classified_coverage"] = {
        "expected": EXPECTED_COVERAGE,
        "actual": {key: cov[key] for key in EXPECTED_COVERAGE},
        "pass": not coverage_mismatch,
        "mismatches": coverage_mismatch,
    }
    failed = [name for name, result in gates.items() if not result["pass"]]
    if failed:
        raise ValueError(f"deliverable gates failed before write: {failed}\n{json.dumps(gates, indent=2)}")

    args.output_dir.mkdir(parents=True)
    csv_path = args.output_dir / f"{STEM}.csv"
    gpkg_path = args.output_dir / f"{STEM}.gpkg"
    gates_path = args.output_dir / "gates.json"
    intervals_path = args.output_dir / "install_intervals_citywide_all.csv"
    write_intervals(intervals, intervals_path)
    write_csv(rows, csv_path, fields=CSV_FIELDS_CITYWIDE)
    write_gpkg(rows, gpkg_path, fields=CSV_FIELDS_CITYWIDE)
    freeze_gpkg_last_change(gpkg_path)
    gates_path.write_text(json.dumps(gates, indent=2, sort_keys=True) + "\n")

    figs_dir = args.output_dir / "figs"
    figs_dir.mkdir()
    for src_name, src in PACKAGE_COPIES:
        shutil.copy2(src, args.output_dir / src_name)
    for png_name in ("map_coverage_class.png", "map_median_year.png"):
        shutil.copy2(DEFAULT_FIGS / png_name, figs_dir / png_name)

    readme_path = args.output_dir / "README.md"
    write_readme(readme_path, gates)

    package_names = [
        "README.md",
        f"{STEM}.csv",
        f"{STEM}.gpkg",
        "descriptive_stats_2026-09-02-citywide-aligned.html",
        "h3_r7_cells.csv",
        "h3_r7_cells.geojson",
        "gates.json",
        "figs/map_coverage_class.png",
        "figs/map_median_year.png",
    ]
    # release_manifest + SHA256SUMS hashed after they exist; SHA256SUMS lists
    # the public package files except itself, matching the Top-52 preview.
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "cohort_scope": "Cape Town citywide census 111801 (Top-52 21453 ∪ 27-wave 90348)",
        "row_count": len(rows),
        "metric_crs": METRIC_CRS,
        "census_cutoff": CENSUS_CUTOFF.isoformat(),
        "coverage": {key: cov[key] for key in EXPECTED_COVERAGE},
        "inputs": {
            str(args.geometry_gpkg): sha256(args.geometry_gpkg),
            str(args.anchors_csv): sha256(args.anchors_csv),
            str(args.wave_intervals_csv): sha256(args.wave_intervals_csv),
            str(args.top52_csv): sha256(args.top52_csv),
            str(args.classified_csv): sha256(args.classified_csv),
        },
        "outputs": {
            str(csv_path): sha256(csv_path),
            str(gpkg_path): sha256(gpkg_path),
            str(gates_path): sha256(gates_path),
            str(intervals_path): sha256(intervals_path),
        },
    }
    manifest_path = args.output_dir / "release_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    package_names.append("release_manifest.json")
    write_sha256sums(args.output_dir, package_names)

    if args.dropbox_dir is not None:
        args.dropbox_dir.mkdir(parents=True)
        public_names = package_names + ["SHA256SUMS.txt"]
        for name in public_names:
            src = args.output_dir / name
            dest = args.dropbox_dir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
