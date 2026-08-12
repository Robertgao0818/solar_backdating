#!/usr/bin/env python3
"""Freeze the CT-06 placement and CT-07 E2E pilot manifests.

This is a read-only selector over the frozen CT anchor/catalog inputs.  It
creates two deterministic, hashed manifests:

* ``placement_120.csv``: a balanced geometry/coverage review sheet, including
  the explicit no-history and Wayback-only exception rows;
* ``smoke_60.csv``: ten history-bearing anchors per CT-08 canary grid, split
  across A24/A48 where both arms exist, for the paired control/optimized scan.

The selector never downloads imagery or calls Gemini.  Its output directory is
versioned and must be empty before creation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.audit_ct52_scoring_run import NO_HISTORY, WAYBACK_ONLY


CANARY_GRIDS = (
    "CPT2932",
    "CPT2597",
    "CPT3790",
    "CPT3677",
    "CPT2124",
    "CPT2713",
)
AREA_BINS = ("lt10", "10_20", "20_40", "40_80", "ge80")


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_key(anchor_id: str) -> str:
    return hashlib.sha256(anchor_id.encode("utf-8")).hexdigest()


def _area_bin(area: str) -> str:
    value = float(area)
    if value < 10:
        return "lt10"
    if value < 20:
        return "10_20"
    if value < 40:
        return "20_40"
    if value < 80:
        return "40_80"
    return "ge80"


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _chip_counts(
    catalog_csv: Path,
    chip_root: Path,
) -> dict[str, dict[str, int]]:
    """Count candidate rows with an existing z19/z18 chip per anchor."""
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"z19": 0, "z18": 0, "z18_fallback": 0}
    )
    with catalog_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            anchor_id = row["anchor_id"]
            capture_date = row["capture_date"][:10].replace("-", "")
            version = row.get("version", "").strip() or "noversion"
            paths = {
                zoom: chip_root
                / anchor_id
                / f"z{zoom}"
                / f"{anchor_id}_{capture_date}_v{version}.tif"
                for zoom in (19, 18)
            }
            exists = {
                zoom: path.is_file() and path.stat().st_size > 0
                for zoom, path in paths.items()
            }
            for zoom in (19, 18):
                if exists[zoom]:
                    counts[anchor_id][f"z{zoom}"] += 1
            if exists[18] and not exists[19]:
                counts[anchor_id]["z18_fallback"] += 1
    return counts


def _labels(
    row: dict[str, str],
    outcome: dict[str, str],
    chip_counts: dict[str, int],
) -> list[str]:
    labels = [row.get("chip_arm", ""), _area_bin(row.get("area_m2", "0"))]
    if row.get("source_grid") != row.get("centroid_grid"):
        labels.append("boundary_halo")
    if float(row.get("confidence", "1") or 1) < 0.95:
        labels.append("low_confidence")
    if _truthy(row.get("review_required", "")):
        labels.append("review_required")
    if row.get("geometry_exception", "").strip():
        labels.append("geometry_exception")
    if chip_counts.get("z18_fallback", 0) > 0:
        labels.append("z18_fallback")
    if outcome.get("catalog_status") == "wayback_only":
        labels.append("wayback_only")
    if outcome.get("catalog_status") == "no_history":
        labels.append("no_history")
    return labels


def _write(path: Path, rows: list[dict[str, str]], base_fields: list[str]) -> None:
    fields = [*base_fields, "pilot_strata", "catalog_status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _ordered(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(rows, key=lambda row: _stable_key(row["anchor_id"]))


def _select_placement(
    rows: list[dict[str, str]],
    labels_by_id: dict[str, list[str]],
    *,
    target_per_arm: int = 60,
) -> list[dict[str, str]]:
    by_id = {row["anchor_id"]: row for row in rows}
    selected: list[dict[str, str]] = []
    selected_ids: set[str] = set()

    def add(candidates: list[dict[str, str]], n: int | None = None) -> None:
        ordered = _ordered([row for row in candidates if row["anchor_id"] not in selected_ids])
        for row in ordered[: n if n is not None else len(ordered)]:
            selected.append(row)
            selected_ids.add(row["anchor_id"])

    # These rows are mandatory in the placement sheet.  They are not silently
    # treated as scorer input: the no-history rows are handled by the explicit
    # terminal lane, and Wayback-only remains a provider-boundary fixture.
    add(
        [
            by_id[anchor_id]
            for anchor_id in sorted(NO_HISTORY | {WAYBACK_ONLY})
            if anchor_id in by_id
        ]
    )
    add([row for row in rows if "geometry_exception" in labels_by_id[row["anchor_id"]]])
    add([row for row in rows if "review_required" in labels_by_id[row["anchor_id"]]])

    for arm in ("A24", "A48"):
        arm_rows = [row for row in rows if row.get("chip_arm") == arm]
        for area in AREA_BINS:
            add(
                [
                    row
                    for row in arm_rows
                    if area in labels_by_id[row["anchor_id"]]
                ],
                5,
            )
        for flag in ("boundary_halo", "low_confidence", "z18_fallback"):
            add(
                [
                    row
                    for row in arm_rows
                    if flag in labels_by_id[row["anchor_id"]]
                ],
                5,
            )

    # Complete each arm to exactly 60 with a stable hash order.  This keeps
    # the pilot balanced even when special strata overlap heavily.
    for arm in ("A24", "A48"):
        arm_rows = [row for row in rows if row.get("chip_arm") == arm]
        add(arm_rows, target_per_arm - sum(1 for r in selected if r.get("chip_arm") == arm))

    if len(selected) != target_per_arm * 2:
        raise ValueError(f"placement selection has {len(selected)} rows, expected {target_per_arm * 2}")
    for row in selected:
        row["pilot_strata"] = ";".join(labels_by_id[row["anchor_id"]])
    return selected


def _select_smoke(
    rows: list[dict[str, str]],
    outcomes: dict[str, dict[str, str]],
    labels_by_id: dict[str, list[str]],
    *,
    per_grid: int = 10,
) -> list[dict[str, str]]:
    smoke: list[dict[str, str]] = []
    for grid in CANARY_GRIDS:
        eligible = [
            row
            for row in rows
            if row.get("source_grid") == grid
            and outcomes[row["anchor_id"]].get("catalog_status") != "no_history"
        ]
        if len(eligible) < per_grid:
            raise ValueError(f"canary grid {grid} has only {len(eligible)} eligible anchors")
        chosen: list[dict[str, str]] = []
        for arm in ("A24", "A48"):
            arm_rows = _ordered([row for row in eligible if row.get("chip_arm") == arm])
            chosen.extend(arm_rows[: per_grid // 2])
        chosen_ids = {row["anchor_id"] for row in chosen}
        if len(chosen) < per_grid:
            chosen.extend(
                row
                for row in _ordered(eligible)
                if row["anchor_id"] not in chosen_ids
            )
            chosen = chosen[:per_grid]
        for row in chosen:
            copy = dict(row)
            copy["pilot_strata"] = f"smoke_{grid};" + ";".join(labels_by_id[row["anchor_id"]])
            smoke.append(copy)
    if len(smoke) != len(CANARY_GRIDS) * per_grid:
        raise ValueError(f"smoke selection has {len(smoke)} rows")
    return smoke


def plan(
    anchors_csv: Path,
    outcomes_csv: Path,
    catalog_csv: Path,
    chip_root: Path,
    output_dir: Path,
) -> dict[str, object]:
    anchors = _read(anchors_csv)
    outcomes_rows = _read(outcomes_csv)
    outcomes = {row["anchor_id"]: row for row in outcomes_rows}
    if len(anchors) != 21453 or len({row.get("anchor_id") for row in anchors}) != len(anchors):
        raise ValueError("frozen CT anchors are not the expected unique 21,453 rows")
    if set(outcomes) != {row["anchor_id"] for row in anchors}:
        raise ValueError("catalog outcomes are not bijective with anchors")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty pilot directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    counts = _chip_counts(catalog_csv, chip_root)
    labels_by_id = {
        row["anchor_id"]: _labels(row, outcomes[row["anchor_id"]], counts.get(row["anchor_id"], {}))
        for row in anchors
    }
    placement = _select_placement(anchors, labels_by_id)
    smoke = _select_smoke(anchors, outcomes, labels_by_id)
    base_fields = list(anchors[0])
    for row in placement + smoke:
        row["catalog_status"] = outcomes[row["anchor_id"]].get("catalog_status", "")
    placement_path = output_dir / "placement_120.csv"
    smoke_path = output_dir / "smoke_60.csv"
    smoke_a24_path = output_dir / "smoke_60_A24.csv"
    smoke_a48_path = output_dir / "smoke_60_A48.csv"
    _write(placement_path, placement, base_fields)
    _write(smoke_path, smoke, base_fields)
    _write(smoke_a24_path, [row for row in smoke if row.get("chip_arm") == "A24"], base_fields)
    _write(smoke_a48_path, [row for row in smoke if row.get("chip_arm") == "A48"], base_fields)
    plan_payload = {
        "schema_version": "ct52_pilot_plan_v1",
        "anchors_csv": str(anchors_csv),
        "anchors_sha256": _sha(anchors_csv),
        "outcomes_csv": str(outcomes_csv),
        "outcomes_sha256": _sha(outcomes_csv),
        "catalog_csv": str(catalog_csv),
        "catalog_sha256": _sha(catalog_csv),
        "placement_count": len(placement),
        "placement_by_arm": dict(Counter(row["chip_arm"] for row in placement)),
        "placement_labels": dict(
            Counter(label for row in placement for label in row["pilot_strata"].split(";") if label)
        ),
        "smoke_count": len(smoke),
        "smoke_by_grid": dict(Counter(row["source_grid"] for row in smoke)),
        "smoke_by_arm": dict(Counter(row["chip_arm"] for row in smoke)),
        "canary_grids": list(CANARY_GRIDS),
        "placement_csv": str(placement_path),
        "smoke_csv": str(smoke_path),
        "smoke_a24_csv": str(smoke_a24_path),
        "smoke_a48_csv": str(smoke_a48_path),
        "sha256": {
            "placement": _sha(placement_path),
            "smoke": _sha(smoke_path),
            "smoke_a24": _sha(smoke_a24_path),
            "smoke_a48": _sha(smoke_a48_path),
        },
    }
    (output_dir / "pilot_plan.json").write_text(
        json.dumps(plan_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return plan_payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, required=True)
    parser.add_argument("--catalog-outcomes-csv", type=Path, required=True)
    parser.add_argument("--catalog-csv", type=Path, required=True)
    parser.add_argument("--chip-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = plan(
        anchors_csv=args.anchors_csv,
        outcomes_csv=args.catalog_outcomes_csv,
        catalog_csv=args.catalog_csv,
        chip_root=args.chip_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
