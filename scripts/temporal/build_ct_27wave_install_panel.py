#!/usr/bin/env python3
"""Cape Town 27-wave date x installed panel, using the JHB panel rules.

Top-52 anchors are omitted: their scored frames are not on disk, so a catalog
date could not be marked ``observed`` without inventing a label. The candidate
dates are the 2019+ GEHI vintage catalog. ``installed`` outside a published
bracket is filled by the same rule as ``build_jhb_run3_install_panel.py``.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from scripts.temporal.build_jhb_run3_install_panel import build_panel
from scripts.temporal.infer_install_dates import _all_results, _evidence_results, apply_dip_repair
from scripts.temporal.scan_state import ScanState, load_scan_state

CITY_ROOT = (
    Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
)
CENSUS_CUTOFF = "2025-01-31"
OBSERVATION_FIELDS = (
    "source_anchor_id",
    "capture_date",
    "pv_present",
    "quality_flag",
    "in_evidence",
    "post_census",
    "usable",
    "dip_flipped",
)


def observations_from_state(state: ScanState) -> list[dict[str, str]]:
    """One scored frame per row, in the shape the JHB panel reader expects.

    Dip repair uses the census-date fence the Cape Town intervals were built
    with. A frame is evidence only when it is not reference-only and falls
    before that census date.
    """
    cutoff = (state.census_date or "").strip()[:10]
    if len(cutoff) != 10:
        raise ValueError(f"{state.anchor_id}: census_date {state.census_date!r}")
    census_mid = date.fromisoformat(cutoff)
    raw_all = _all_results(state)
    evidence_ids = {id(result) for result in _evidence_results(state, census_mid)}
    _repaired, flipped_dates = apply_dip_repair(state, census_mid_date=census_mid)
    flipped = set(flipped_dates)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for result in raw_all:
        capture = (result.capture_date or "").strip()[:10]
        if capture in seen:
            raise ValueError(f"{state.anchor_id}: duplicate capture_date {capture}")
        seen.add(capture)
        post_census = capture >= cutoff
        usable = result.quality_flag == "usable" and result.pv_present is not None
        if result.pv_present is None:
            raw = ""
        else:
            raw = "1" if result.pv_present else "0"
        rows.append(
            {
                "source_anchor_id": state.anchor_id,
                "capture_date": capture,
                "pv_present": raw,
                "quality_flag": result.quality_flag,
                "in_evidence": "1" if id(result) in evidence_ids else "0",
                "post_census": "1" if post_census else "0",
                "usable": "1" if usable else "0",
                "dip_flipped": "1" if capture in flipped and result.pv_present is False else "0",
            }
        )
    return rows


def _write_observations(polygons: Path, states_dir: Path, dest: Path) -> set[str]:
    anchors: set[str] = set()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with polygons.open(newline="", encoding="utf-8") as source, dest.open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OBSERVATION_FIELDS))
        writer.writeheader()
        for index, row in enumerate(csv.DictReader(source), start=1):
            anchor_id = row["source_anchor_id"]
            anchors.add(anchor_id)
            state_path = states_dir / Path(row["scan_state_path"]).name
            state = load_scan_state(state_path)
            if state is None or state.anchor_id != anchor_id:
                raise SystemExit(f"missing scan state for {anchor_id}: {state_path}")
            for obs in observations_from_state(state):
                writer.writerow(obs)
            if index % 5000 == 0:
                print(f"observations {index}", file=sys.stderr)
    return anchors


def _write_flights(polygons: Path, dest: Path) -> None:
    grids: set[str] = set()
    with polygons.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            grid = row["source_grid"].strip()
            if grid:
                grids.add(grid)
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["grid_id", "last_capture_date"])
        writer.writeheader()
        for grid in sorted(grids):
            writer.writerow({"grid_id": grid, "last_capture_date": CENSUS_CUTOFF})


def build(args: argparse.Namespace) -> dict:
    anchors = _write_observations(args.polygons, args.states_dir, args.observations)
    _write_flights(args.polygons, args.flights)
    panel_args = argparse.Namespace(
        polygons=args.polygons,
        observations=args.observations,
        catalog=args.catalog,
        vexcel_csv=args.flights,
        output=args.output,
        report=args.report,
        anchors=anchors,
    )
    return build_panel(panel_args)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--polygons",
        type=Path,
        default=CITY_ROOT / "deliverable_27wave_20260923/ct_27wave_install_dated_20260902.csv",
    )
    parser.add_argument(
        "--states-dir",
        type=Path,
        default=CITY_ROOT / "e8_descriptive_20260902/merged_scan_states",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=CITY_ROOT / "ct05_catalog_v1/merged/gehi_vintage_candidates_ct05_run3_2019plus.csv",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        default=CITY_ROOT / "deliverable_27wave_20260923/ct_27wave_panel_observations.csv",
    )
    parser.add_argument(
        "--flights",
        type=Path,
        default=CITY_ROOT / "deliverable_27wave_20260923/ct_census_cutoff_by_grid.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=CITY_ROOT / "deliverable_27wave_20260923/ct_27wave_install_panel.csv",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=CITY_ROOT / "deliverable_27wave_20260923/ct_27wave_install_panel_report.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    build(parse_args(argv))


if __name__ == "__main__":
    main()
