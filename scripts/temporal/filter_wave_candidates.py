"""Filter the merged citywide 2019+ catalog down to one wave's anchors.

Inputs:
  --catalog-csv   merged gehi_vintage_candidates_ct05_run3_2019plus.csv
  --wave-csv      wave_NN_all.csv from the frozen wave plan
  --out-dir       wave plan dir (writes candidates_2019plus.csv +
                  membership_expected.json)

membership_expected.json is the download/merge denominator: one entry per
(anchor_id, capture_date) candidate admitted for this wave.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-csv", type=Path, required=True)
    parser.add_argument("--wave-csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    wave_rows = _read(args.wave_csv)
    wave_anchor_ids = {row["anchor_id"] for row in wave_rows}
    if len(wave_anchor_ids) != len(wave_rows):
        raise SystemExit("wave CSV has duplicate anchor_id")

    catalog_rows = _read(args.catalog_csv)
    selected = [
        row for row in catalog_rows if row.get("anchor_id") in wave_anchor_ids
    ]
    if not selected:
        raise SystemExit("no catalog candidates matched this wave")

    # structural dedupe guard: (anchor_id, capture_date) is the membership key
    keys = [(row["anchor_id"], row["capture_date"]) for row in selected]
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate (anchor_id, capture_date) in wave candidates")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = args.out_dir / "candidates_2019plus.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_rows[0].keys()))
        writer.writeheader()
        writer.writerows(selected)

    by_provider = Counter(row.get("provider", "") for row in selected)
    membership = {
        "schema_version": "ct_citywave_membership_expected_v1",
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "wave_csv": str(args.wave_csv),
        "catalog_csv": str(args.catalog_csv),
        "wave_anchor_count": len(wave_anchor_ids),
        "anchors_with_candidates": len({row["anchor_id"] for row in selected}),
        "candidate_count": len(selected),
        "provider_counts": dict(sorted(by_provider.items())),
        "keys": [f"{anchor}|{date}" for anchor, date in sorted(keys)],
    }
    (args.out_dir / "membership_expected.json").write_text(
        json.dumps(membership, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in membership.items() if k != "keys"},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
