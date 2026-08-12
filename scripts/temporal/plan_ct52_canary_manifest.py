#!/usr/bin/env python3
"""Freeze the six-grid CT-08 canary manifest and its A24/A48 split."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


CANARY_GRIDS = (
    "CPT2932",
    "CPT2597",
    "CPT3790",
    "CPT3677",
    "CPT2124",
    "CPT2713",
)
EXPECTED_COUNT = 2700


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def plan(anchors_csv: Path, output_dir: Path) -> dict[str, object]:
    anchors = _read(anchors_csv)
    if len(anchors) != 21453 or len({row.get("anchor_id") for row in anchors}) != len(anchors):
        raise ValueError("expected the frozen unique 21,453-anchor CT manifest")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty canary directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [row for row in anchors if row.get("source_grid") in CANARY_GRIDS]
    if len(rows) != EXPECTED_COUNT:
        raise ValueError(f"canary anchor count {len(rows)} != {EXPECTED_COUNT}")
    fields = list(anchors[0])
    paths = {
        "all": output_dir / "canary_2700_all.csv",
        "A24": output_dir / "canary_2700_A24.csv",
        "A48": output_dir / "canary_2700_A48.csv",
    }
    _write(paths["all"], rows, fields)
    _write(paths["A24"], [row for row in rows if row.get("chip_arm") == "A24"], fields)
    _write(paths["A48"], [row for row in rows if row.get("chip_arm") == "A48"], fields)
    result = {
        "schema_version": "ct52_canary_manifest_v1",
        "anchors_csv": str(anchors_csv),
        "anchors_sha256": _sha(anchors_csv),
        "canary_grids": list(CANARY_GRIDS),
        "anchor_count": len(rows),
        "by_grid": dict(Counter(row["source_grid"] for row in rows)),
        "by_arm": dict(Counter(row["chip_arm"] for row in rows)),
        "paths": {key: str(path) for key, path in paths.items()},
        "sha256": {key: _sha(path) for key, path in paths.items()},
    }
    (output_dir / "canary_manifest.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(plan(args.anchors_csv, args.output_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
