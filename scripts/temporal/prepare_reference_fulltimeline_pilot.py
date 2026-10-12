#!/usr/bin/env python3
"""Freeze the Cape Town 10-grid full-timeline pilot from the inventory catalogs.

Stdout is a deterministic summary. Progress and file paths go to stderr.
The command reads the anchor and GEHI catalogs and writes only the plan
directory. It does not call a model and it does not download imagery.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.reference_fulltimeline import (  # noqa: E402
    ANCHOR_FIELDS,
    CANDIDATE_FIELDS,
    GRID_FIELDS,
    REFERENCE_FIELDS,
    WINDOW_START,
    build_plan,
    format_inventory_summary,
    prompt_fingerprint,
)


DEFAULT_ROOT = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
)
DEFAULT_OUTPUT = (
    Path.home() / "zasolar_data/geid_temporal/reference_fulltimeline_lite_10grid_20261004"
)
RUN_ID = "reference_fulltimeline_lite_10grid_20261004"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fields),
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _connect():
    import duckdb

    return duckdb.connect()


def load_grid_rows(anchors_csv: Path, catalog_csv: Path) -> list[dict[str, Any]]:
    """Aggregate density and in-window date coverage by centroid_grid."""

    con = _connect()
    rows = con.execute(
        f"""
        WITH anchors AS (
          SELECT
            anchor_id,
            centroid_grid AS grid_id,
            chip_arm,
            coalesce(geometry_exception, '') AS geometry_exception,
            try_cast(centroid_lon AS DOUBLE) AS lon,
            try_cast(centroid_lat AS DOUBLE) AS lat
          FROM read_csv('{anchors_csv}', header=true, all_varchar=true)
        ),
        hist AS (
          SELECT anchor_id, substr(capture_date, 1, 10) AS capture_date
          FROM read_csv('{catalog_csv}', header=true, all_varchar=true)
          WHERE substr(capture_date, 1, 10) >= '{WINDOW_START}'
            AND substr(capture_date, 1, 10) < substr(census_date, 1, 10)
            AND reference_only = '0'
          GROUP BY 1, 2
        ),
        per_anchor AS (
          SELECT
            a.grid_id,
            a.anchor_id,
            count(h.capture_date) AS n_dates,
            max(CASE WHEN a.chip_arm = 'A48' THEN 1 ELSE 0 END) AS is_a48,
            max(CASE WHEN a.geometry_exception <> '' THEN 1 ELSE 0 END) AS is_geom,
            max(CASE WHEN a.lon IS NULL OR a.lat IS NULL THEN 1 ELSE 0 END) AS missing_xy
          FROM anchors a
          LEFT JOIN hist h USING (anchor_id)
          GROUP BY 1, 2
        )
        SELECT
          grid_id,
          count(*) AS n_anchors,
          median(n_dates) AS median_in_window_dates,
          sum(is_a48) AS n_a48,
          sum(is_geom) AS n_geometry_exception,
          sum(missing_xy) AS n_missing_xy,
          sum(CASE WHEN n_dates = 0 THEN 1 ELSE 0 END) AS n_no_history
        FROM per_anchor
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    columns = [
        "grid_id",
        "n_anchors",
        "median_in_window_dates",
        "n_a48",
        "n_geometry_exception",
        "n_missing_xy",
        "n_no_history",
    ]
    return [dict(zip(columns, row)) for row in rows]


def load_selected_anchors(anchors_csv: Path, grid_ids: Sequence[str]) -> list[dict[str, Any]]:
    con = _connect()
    con.execute("CREATE TABLE selected_grids(grid_id VARCHAR)")
    con.executemany("INSERT INTO selected_grids VALUES (?)", [(grid_id,) for grid_id in grid_ids])
    description_rows = con.execute(
        f"""
        SELECT
          anchor_id,
          centroid_grid AS grid_id,
          source_grid,
          source_grids,
          region_key,
          centroid_lon,
          centroid_lat,
          area_m2,
          chip_arm,
          review_extent_m,
          geometry_exception,
          geometry_version,
          chip_lon_min,
          chip_lat_min,
          chip_lon_max,
          chip_lat_max,
          source_width_m,
          source_height_m,
          source_box_width_m,
          source_box_height_m,
          center_offset_m
        FROM read_csv('{anchors_csv}', header=true, all_varchar=true)
        WHERE centroid_grid IN (SELECT grid_id FROM selected_grids)
        ORDER BY centroid_grid, anchor_id
        """
    ).fetchall()
    fields = [
        "anchor_id",
        "grid_id",
        "source_grid",
        "source_grids",
        "region_key",
        "centroid_lon",
        "centroid_lat",
        "area_m2",
        "chip_arm",
        "review_extent_m",
        "geometry_exception",
        "geometry_version",
        "chip_lon_min",
        "chip_lat_min",
        "chip_lon_max",
        "chip_lat_max",
        "source_width_m",
        "source_height_m",
        "source_box_width_m",
        "source_box_height_m",
        "center_offset_m",
    ]
    return [dict(zip(fields, row)) for row in description_rows]


def load_catalog_rows(
    catalog_csv: Path,
    anchor_ids: Sequence[str],
    *,
    full_catalog: Path | None,
) -> tuple[list[dict[str, Any]], int]:
    """Load window rows for the frozen anchors and fill gaps from the full catalog."""

    con = _connect()
    con.execute("CREATE TABLE selected_anchors(anchor_id VARCHAR)")
    con.executemany(
        "INSERT INTO selected_anchors VALUES (?)",
        [(anchor_id,) for anchor_id in anchor_ids],
    )
    columns = [
        "anchor_id",
        "capture_date",
        "version",
        "provider",
        "requested_zoom",
        "bbox_complete",
        "reference_only",
        "census_date",
    ]

    def _query(path: Path) -> list[dict[str, Any]]:
        fetched = con.execute(
            f"""
            SELECT
              anchor_id,
              substr(capture_date, 1, 10) AS capture_date,
              version,
              provider,
              requested_zoom,
              CASE WHEN bbox_complete_at_catalog IN ('1', 'true', 'True') THEN 1 ELSE 0 END
                AS bbox_complete,
              CASE WHEN reference_only IN ('1', 'true', 'True') THEN 1 ELSE 0 END
                AS reference_only,
              substr(census_date, 1, 10) AS census_date
            FROM read_csv('{path}', header=true, all_varchar=true)
            WHERE anchor_id IN (SELECT anchor_id FROM selected_anchors)
            ORDER BY anchor_id, capture_date, provider, version
            """
        ).fetchall()
        return [dict(zip(columns, row)) for row in fetched]

    print(f"[inventory] reading selected catalog rows from {catalog_csv.name}", file=sys.stderr)
    rows = _query(catalog_csv)
    seen = {
        (row["anchor_id"], row["capture_date"], row["provider"], str(row["version"]), row["reference_only"])
        for row in rows
    }
    added = 0
    if full_catalog is not None and full_catalog.is_file():
        print(f"[inventory] reconciling {full_catalog.name}", file=sys.stderr)
        for row in _query(full_catalog):
            if str(row["capture_date"]) < WINDOW_START:
                continue
            key = (
                row["anchor_id"],
                row["capture_date"],
                row["provider"],
                str(row["version"]),
                row["reference_only"],
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
    return rows, added


def write_plan(output_root: Path, plan: Mapping[str, Any], *, extra_lock: Mapping[str, Any]) -> None:
    plan_dir = output_root / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(plan_dir / "grid_selection.csv", plan["grids"], GRID_FIELDS)
    _write_csv(plan_dir / "anchor_selection.csv", plan["anchors"], ANCHOR_FIELDS)
    _write_csv(plan_dir / "candidates.csv", plan["candidates"], CANDIDATE_FIELDS)
    _write_csv(plan_dir / "references.csv", plan["references"], REFERENCE_FIELDS)
    out_fields = ["grid_id", "n_anchors", "out_of_scope_reason"]
    _write_csv(
        plan_dir / "out_of_scope_grids.csv",
        [
            {
                "grid_id": row.get("grid_id", ""),
                "n_anchors": row.get("n_anchors", ""),
                "out_of_scope_reason": row.get("out_of_scope_reason", ""),
            }
            for row in sorted(plan["out_of_scope"], key=lambda item: str(item.get("grid_id")))
        ],
        out_fields,
    )
    lock = {
        "run_id": RUN_ID,
        "seed": plan["summary"]["seed"],
        "model_alias": plan["summary"]["model_alias"],
        "catalog_min_date": WINDOW_START,
        "census_date_precision": "policy_census_bound",
        "prompt_sha256": prompt_fingerprint(),
        "code_version": _git_head(),
        "thresholds": plan["selection"]["thresholds"],
        "development_strata": plan["selection"]["development_strata"],
        "substitutes": plan["selection"]["substitutes"],
        "rng_order": plan["selection"]["rng_order"],
        "summary": plan["summary"],
        "reconciled_catalog_rows_added": extra_lock.get("reconciled_catalog_rows_added", 0),
        "input_sha256": extra_lock.get("input_sha256", {}),
    }
    (plan_dir / "run_lock.json").write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    budget = {
        "run_id": RUN_ID,
        "status": "awaiting_techcheck",
        "N": plan["summary"]["N"],
        "H": plan["summary"]["H"],
        "H_pair": plan["summary"]["H_pair"],
        "n_paired_anchors": plan["summary"]["n_paired_anchors"],
        "n_review_anchors": plan["summary"]["n_review_anchors"],
        "frozen": False,
        "illustrative_example_used_as_count": 0,
    }
    budget_path = plan_dir / "budget.json"
    if not budget_path.exists():
        budget_path.write_text(json.dumps(budget, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ROOT / "anchors_v1/anchors_all.csv")
    parser.add_argument(
        "--catalog-csv",
        type=Path,
        default=DEFAULT_ROOT / "ct05_catalog_v1/merged/gehi_vintage_candidates_ct05_run3_2019plus.csv",
    )
    parser.add_argument(
        "--full-catalog-csv",
        type=Path,
        default=DEFAULT_ROOT / "ct05_catalog_v1/merged/gehi_vintage_candidates_ct05.csv",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=20261004)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    for path in (args.anchors_csv, args.catalog_csv):
        if not path.is_file():
            raise SystemExit(f"missing input: {path}")
    print("[inventory] aggregating centroid grids", file=sys.stderr)
    grids = load_grid_rows(args.anchors_csv, args.catalog_csv)
    print(f"[inventory] grids={len(grids)}", file=sys.stderr)
    # Selection is a pure function of the grid table. Anchor and catalog reads
    # happen after the ids are frozen so the large extracts stay small.
    from scripts.temporal.reference_fulltimeline import select_grids

    selection = select_grids(grids, seed=args.seed)
    grid_ids = [row["grid_id"] for row in selection["grids"]]
    print(f"[inventory] selected {','.join(grid_ids)}", file=sys.stderr)
    anchors = load_selected_anchors(args.anchors_csv, grid_ids)
    catalog, added = load_catalog_rows(
        args.catalog_csv,
        [row["anchor_id"] for row in anchors],
        full_catalog=args.full_catalog_csv,
    )
    plan = build_plan(grids, anchors, catalog, seed=args.seed)
    if [row["grid_id"] for row in plan["grids"]] != grid_ids:
        raise SystemExit("grid selection changed between the id pass and the full plan")
    print("[inventory] hashing inputs", file=sys.stderr)
    hashes = {
        "anchors": _sha256_file(args.anchors_csv),
        "catalog_2019plus": _sha256_file(args.catalog_csv),
    }
    if args.full_catalog_csv.is_file():
        hashes["catalog_full"] = _sha256_file(args.full_catalog_csv)
    write_plan(
        args.output_root,
        plan,
        extra_lock={
            "reconciled_catalog_rows_added": added,
            "input_sha256": hashes,
        },
    )
    sys.stdout.write(format_inventory_summary(plan["summary"]))


if __name__ == "__main__":
    main()
