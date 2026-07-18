#!/usr/bin/env python3
"""Build the econ-analysis install-dated deliverable from a completed backdating run.

Joins three frozen inputs on ``source_feature_id`` (0..N-1, the FP-cut gpkg's
stable positional row order) and emits the same package shape as the
2026-06-05 production deliverable:

- ``<tag>.csv`` — one row per source polygon, no geometry, + ``centroid_lon/lat``
  (EPSG:4326).
- ``<tag>.gpkg`` — same attres + polygon geometry, layer ``solar_install_dated``,
  EPSG:32735 (native CRS of the geometry source, copied through unchanged).

Inputs
------
- ``--geometry-gpkg`` — the FP-cut inventory (e.g.
  ``jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01.gpkg``, layer
  ``solar_predictions``). Feature N's positional index (0-based, in on-disk
  iteration order) IS ``source_feature_id`` — verified invariant, not derived
  from any field in the file itself.
- ``--anchors-csv`` — a versioned ``anchors_all.csv`` (ISSUE-27 v2 builder;
  the historical v1 builder is archived at commit ``b76c1d3``):
  ``source_feature_id -> anchor_id`` (bijective) plus ``chip_arm``.
- ``--intervals-csv`` — ``install_intervals_all.csv`` from
  ``infer_install_dates.py``: ``anchor_id -> status`` + interval fields, plus
  a ``lane`` column.

Status -> deliverable field mapping
------------------------------------
``done_appears`` (valid bracket only — see "inverted_interval" note below):
    date_provider=gehi_merged, date_is_bound=0,
    install_date=install_mid_estimate (bracket midpoint).

``done_installed_during_census``:
    date_provider=gehi_merged_census, date_is_bound=1,
    install_date=install_interval_end (the per-grid census/flight upper
    bound — the imagery-backed "installed by" date; no finer signal).
    earliest_present_date is set to that same upper bound: the post-census
    reference frames in this run's window ARE the confirming-present
    observation for this status (see run doc ISSUE-26).

``done_already_present_before_geid_history``:
    date_provider=gehi_merged_leftcensor, date_is_bound=1, left_censored=1,
    install_date=install_interval_end (earliest scored capture / "present-by"
    date), install_interval_start empty (open lower bound).

``done_ambiguous_*`` (any status starting with that prefix) and the
"inverted_interval" done_appears bug catch below:
    undated; date_provider/date_status/install_date/etc. all blank;
    undated_reason records the cause.

**Known upstream data-quality catch (must-read before trusting the raw
status column):** ``infer_install_dates.py`` can leave a row labeled
``done_appears`` with ALL interval fields blank when its own inversion check
fires (``latest_absent > earliest_present``; the script's own note text says
"state machine should classify as done_ambiguous_nonmonotonic; check
scan_state" — an acknowledged inconsistency, not a data artifact of this
builder). This builder reclassifies those rows as undated
(``undated_reason=inverted_interval_mislabeled_appears``) rather than
emitting a "dated" row with no date, since the alternative violates the basic
interval invariant (``interval_start <= install_date <= interval_end``) that
every real "dated" row must satisfy. See ``classify_row`` and the module
docstring note in the README this script's caller writes alongside the
package.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fiona
from pyproj import Transformer
from shapely.geometry import shape

DEFAULT_GEOMETRY_GPKG = Path(
    "/home/gao/projects/ZAsolar/results/analysis/full382_merge01_2026-05-15/"
    "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01.gpkg"
)
DEFAULT_RUN_ROOT = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
DEFAULT_ANCHORS_CSV = DEFAULT_RUN_ROOT / "anchors_all.csv"
DEFAULT_INTERVALS_CSV = DEFAULT_RUN_ROOT / "intervals" / "install_intervals_all.csv"
DEFAULT_VEXCEL_CSV = Path(
    "/home/gao/projects/ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv"
)

GEOM_LAYER_OUT = "solar_install_dated"
GEOM_CRS_OUT = "EPSG:32735"

# Extra detector-provenance fields carried straight through from the geometry
# source into the GPKG only (matches the June GPKG's fuller schema; kept out
# of the CSV to stay exactly on the frozen CSV column list below).
GEOM_PASSTHROUGH_FIELDS = ("score", "orig_area_m2", "sam_score", "n_merged", "source_tile")

# Frozen CSV column order/names, identical to the 2026-06-05 deliverable.
CSV_JUNE_FIELDS = [
    "source_feature_id",
    "source_grid",
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
    "earliest_present_date",
    "install_confidence",
    "source_anchor_id",
    "undated_reason",
]
# New columns, additive only -- never repurposes a June name.
CSV_NEW_FIELDS = ["lane", "chip_arm", "scan_status", "interval_days", "left_censored", "census_bound"]
CSV_FIELDS = CSV_JUNE_FIELDS + CSV_NEW_FIELDS

AMBIGUOUS_PREFIX = "done_ambiguous_"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def load_vexcel_flight_dates(path: Path) -> dict[str, date]:
    out: dict[str, date] = {}
    for row in _read_csv(path):
        gid = row.get("grid_id", "").strip()
        raw = row.get("last_capture_date", "").strip()
        if not gid or not raw:
            continue
        out[gid] = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    return out


@dataclass
class ClassifiedFields:
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
    interval_days: str = ""


def classify_row(interval_row: dict[str, str]) -> ClassifiedFields:
    """Pure status -> deliverable-field mapping. See module docstring."""
    status = interval_row.get("status", "")
    out = ClassifiedFields()

    if status == "done_appears":
        mid = interval_row.get("install_mid_estimate", "").strip()
        start = interval_row.get("install_interval_start", "").strip()
        end = interval_row.get("install_interval_end", "").strip()
        if not mid or not start or not end:
            # infer_install_dates.py's inverted_interval / inconsistent branches
            # leave status="done_appears" with blank fields -- see module
            # docstring. Never emit a "dated" row with no date.
            out.undated_reason = "inverted_interval_mislabeled_appears"
            return out
        out.date_provider = "gehi_merged"
        out.date_status = status
        out.date_is_bound = "0"
        out.install_date = mid
        out.install_interval_start = start
        out.install_interval_end = end
        out.earliest_present_date = interval_row.get("earliest_present_date", "")
        out.install_confidence = interval_row.get("confidence", "")
        out.interval_days = str((_parse(end) - _parse(start)).days)
        return out

    if status == "done_installed_during_census":
        start = interval_row.get("install_interval_start", "").strip()
        end = interval_row.get("install_interval_end", "").strip()
        if not start or not end:
            out.undated_reason = "inconsistent_installed_during_census"
            return out
        out.date_provider = "gehi_merged_census"
        out.date_status = status
        out.date_is_bound = "1"
        out.install_date = end
        out.install_interval_start = start
        out.install_interval_end = end
        out.earliest_present_date = end  # post-census reference frames confirm presence
        out.install_confidence = interval_row.get("confidence", "")
        out.census_bound = 1
        out.interval_days = str((_parse(end) - _parse(start)).days)
        return out

    if status == "done_already_present_before_geid_history":
        end = interval_row.get("install_interval_end", "").strip()
        if not end:
            out.undated_reason = "inconsistent_already_present"
            return out
        out.date_provider = "gehi_merged_leftcensor"
        out.date_status = status
        out.date_is_bound = "1"
        out.install_date = end
        out.install_interval_start = ""
        out.install_interval_end = end
        out.earliest_present_date = interval_row.get("earliest_present_date", "")
        out.install_confidence = "low"
        out.left_censored = 1
        return out

    if status.startswith(AMBIGUOUS_PREFIX):
        out.undated_reason = status[len(AMBIGUOUS_PREFIX):]
        return out

    out.undated_reason = f"unhandled_status_{status}"
    return out


def _parse(iso: str) -> date:
    return datetime.strptime(iso[:10], "%Y-%m-%d").date()


def load_geometry_rows(path: Path) -> list[dict]:
    """Positional index (0-based) IS source_feature_id -- see module docstring."""
    transformer = Transformer.from_crs("EPSG:32735", "EPSG:4326", always_xy=True)
    rows = []
    with fiona.open(path) as src:
        for idx, feat in enumerate(src):
            props = feat["properties"]
            geom = shape(feat["geometry"])
            lon, lat = transformer.transform(geom.centroid.x, geom.centroid.y)
            rows.append(
                {
                    "source_feature_id": idx,
                    "geometry": feat["geometry"],
                    "source_grid": props.get("source_grid", ""),
                    "confidence": props.get("confidence", ""),
                    "area_m2": props.get("area_m2", ""),
                    "centroid_lon": lon,
                    "centroid_lat": lat,
                    **{f: props.get(f, "") for f in GEOM_PASSTHROUGH_FIELDS},
                }
            )
    return rows


def build_rows(
    geometry_rows: list[dict],
    anchors_by_sfid: dict[int, dict],
    intervals_by_anchor: dict[str, dict],
) -> list[dict]:
    out = []
    for geo in geometry_rows:
        sfid = geo["source_feature_id"]
        anchor = anchors_by_sfid.get(sfid)
        if anchor is None:
            raise ValueError(f"source_feature_id {sfid} missing from anchors_all.csv")
        anchor_id = anchor["anchor_id"]
        interval = intervals_by_anchor.get(anchor_id)
        if interval is None:
            raise ValueError(f"anchor_id {anchor_id} (sfid {sfid}) missing from intervals CSV")

        fields = classify_row(interval)
        row = {
            "source_feature_id": sfid,
            "source_grid": geo["source_grid"],
            "centroid_lon": geo["centroid_lon"],
            "centroid_lat": geo["centroid_lat"],
            "area_m2": geo["area_m2"],
            "confidence": geo["confidence"],
            "date_provider": fields.date_provider,
            "date_status": fields.date_status,
            "date_is_bound": fields.date_is_bound,
            "install_date": fields.install_date,
            "install_interval_start": fields.install_interval_start,
            "install_interval_end": fields.install_interval_end,
            "earliest_present_date": fields.earliest_present_date,
            "install_confidence": fields.install_confidence,
            "source_anchor_id": anchor_id,
            "undated_reason": fields.undated_reason,
            "lane": interval.get("lane", ""),
            "chip_arm": anchor.get("chip_arm", ""),
            "scan_status": interval.get("status", ""),
            "interval_days": fields.interval_days,
            "left_censored": fields.left_censored,
            "census_bound": fields.census_bound,
            "_geometry": geo["geometry"],
            "_passthrough": {f: geo[f] for f in GEOM_PASSTHROUGH_FIELDS},
        }
        out.append(row)
    return out


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in CSV_FIELDS})


def write_gpkg(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    schema = {
        "geometry": "Polygon",
        "properties": {
            "source_feature_id": "int",
            "source_grid": "str",
            "confidence": "float",
            "score": "float",
            "area_m2": "float",
            "orig_area_m2": "float",
            "sam_score": "float",
            "n_merged": "float",
            "source_tile": "str",
            "date_provider": "str",
            "date_status": "str",
            "date_is_bound": "str",
            "install_date": "str",
            "install_interval_start": "str",
            "install_interval_end": "str",
            "earliest_present_date": "str",
            "install_confidence": "str",
            "source_anchor_id": "str",
            "undated_reason": "str",
            "lane": "str",
            "chip_arm": "str",
            "scan_status": "str",
            "interval_days": "str",
            "left_censored": "int",
            "census_bound": "int",
        },
    }
    with fiona.open(
        path, "w", driver="GPKG", crs=GEOM_CRS_OUT, schema=schema, layer=GEOM_LAYER_OUT
    ) as dst:
        for row in rows:
            props = {k: row[k] for k in CSV_FIELDS if k in schema["properties"]}
            props.update(row["_passthrough"])
            dst.write({"geometry": row["_geometry"], "properties": props})


def run_gates(
    rows: list[dict],
    *,
    expected_count: int,
    vexcel_flight_dates: dict[str, date] | None,
    gpkg_path: Path | None = None,
) -> dict:
    gates: dict[str, dict] = {}

    gates["row_count"] = {
        "expected": expected_count,
        "actual": len(rows),
        "pass": len(rows) == expected_count,
    }

    sfids = sorted(r["source_feature_id"] for r in rows)
    gates["sfid_bijective"] = {
        "pass": sfids == list(range(expected_count)),
    }

    interval_violations = []
    for r in rows:
        start, mid, end = r["install_interval_start"], r["install_date"], r["install_interval_end"]
        if not mid:
            continue
        if r["date_status"] == "done_already_present_before_geid_history":
            continue  # open lower bound by design; nothing to check against start
        if not start or not end:
            interval_violations.append(r["source_feature_id"])
            continue
        if not (_parse(start) <= _parse(mid) <= _parse(end)):
            interval_violations.append(r["source_feature_id"])
    gates["interval_invariant"] = {
        "n_checked": sum(1 for r in rows if r["install_date"]),
        "n_violations": len(interval_violations),
        "sample_violations": interval_violations[:10],
        "pass": len(interval_violations) == 0,
    }

    if vexcel_flight_dates:
        flight_violations = []
        for r in rows:
            if not r["install_date"]:
                continue
            grid = r["source_grid"]
            ceiling = vexcel_flight_dates.get(grid)
            if ceiling is None:
                continue
            if _parse(r["install_date"]) > ceiling:
                flight_violations.append(r["source_feature_id"])
        gates["flight_date_ceiling"] = {
            "n_checked": sum(1 for r in rows if r["install_date"]),
            "n_violations": len(flight_violations),
            "sample_violations": flight_violations[:10],
            "pass": len(flight_violations) == 0,
        }

    n_dated = sum(1 for r in rows if r["date_is_bound"] == "0")
    n_left_censored = sum(1 for r in rows if r["left_censored"] == 1)
    n_census_bound = sum(1 for r in rows if r["census_bound"] == 1)
    n_undated = sum(1 for r in rows if r["undated_reason"])
    gates["coverage_reconcile"] = {
        "n_dated_bracket": n_dated,
        "n_census_bound": n_census_bound,
        "n_left_censored": n_left_censored,
        "n_undated": n_undated,
        "total": n_dated + n_census_bound + n_left_censored + n_undated,
        "pass": n_dated + n_census_bound + n_left_censored + n_undated == expected_count,
    }

    if gpkg_path is not None and gpkg_path.exists():
        import random

        transformer = Transformer.from_crs("EPSG:32735", "EPSG:4326", always_xy=True)
        csv_by_sfid = {r["source_feature_id"]: r for r in rows}
        rng = random.Random(20260717)
        sample_n = min(500, len(rows))
        max_dist_m = 0.0
        with fiona.open(gpkg_path) as src:
            feats = list(src)
            sample_idx = rng.sample(range(len(feats)), sample_n)
            for idx in sample_idx:
                feat = feats[idx]
                sfid = feat["properties"]["source_feature_id"]
                geom = shape(feat["geometry"])
                lon, lat = transformer.transform(geom.centroid.x, geom.centroid.y)
                csv_row = csv_by_sfid[sfid]
                dlat = (lat - csv_row["centroid_lat"]) * 111320
                dlon = (lon - csv_row["centroid_lon"]) * 111320 * 0.9  # ~cos(lat) at -26 deg
                dist = (dlat**2 + dlon**2) ** 0.5
                max_dist_m = max(max_dist_m, dist)
        gates["csv_gpkg_centroid_agreement"] = {
            "n_sampled": sample_n,
            "max_dist_m": max_dist_m,
            "pass": max_dist_m <= 1.0,
        }

    return gates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--geometry-gpkg", type=Path, default=DEFAULT_GEOMETRY_GPKG)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS_CSV)
    parser.add_argument("--intervals-csv", type=Path, default=DEFAULT_INTERVALS_CSV)
    parser.add_argument("--vexcel-capture-csv", type=Path, default=DEFAULT_VEXCEL_CSV)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tag", type=str, required=True, help="e.g. 2026-07-17")
    parser.add_argument("--expected-count", type=int, default=41393)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    geometry_rows = load_geometry_rows(args.geometry_gpkg)
    anchors_by_sfid = {int(r["source_feature_id"]): r for r in _read_csv(args.anchors_csv)}
    intervals_by_anchor = {r["anchor_id"]: r for r in _read_csv(args.intervals_csv)}
    vexcel_flight_dates = load_vexcel_flight_dates(args.vexcel_capture_csv) if args.vexcel_capture_csv else {}

    rows = build_rows(geometry_rows, anchors_by_sfid, intervals_by_anchor)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / f"jhb_full382_fpcut_install_dated_{args.tag}.csv"
    gpkg_path = args.output_dir / f"jhb_full382_fpcut_install_dated_{args.tag}.gpkg"
    write_csv(rows, csv_path)
    write_gpkg(rows, gpkg_path)

    gates = run_gates(
        rows,
        expected_count=args.expected_count,
        vexcel_flight_dates=vexcel_flight_dates,
        gpkg_path=gpkg_path,
    )

    print(f"rows={len(rows)} -> {csv_path}")
    print(f"rows={len(rows)} -> {gpkg_path}")
    all_pass = True
    for name, result in gates.items():
        status = "PASS" if result.get("pass") else "FAIL"
        if not result.get("pass"):
            all_pass = False
        print(f"[{status}] {name}: {result}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
