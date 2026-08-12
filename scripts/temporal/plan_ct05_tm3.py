#!/usr/bin/env python3
"""Split each CT-05 TM-z19 route into three stable anchor-local lanes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows  # noqa: E402
from scripts.temporal.run_ct05_catalog_probe import utc_now  # noqa: E402


def split_tm_route(
    candidates_csv: Path,
    *,
    route_id: str,
    lanes: int,
    out_dir: Path,
) -> dict[str, object]:
    rows = read_csv_rows(candidates_csv)
    if not rows:
        raise ValueError("TM route shard is empty")
    if lanes < 1:
        raise ValueError("lanes must be positive")
    if any(str(row.get("provider", "")) != "TM" for row in rows):
        raise ValueError("TM acceleration shard contains a non-TM candidate")
    if any(int(row.get("requested_zoom", 0)) != 19 for row in rows):
        raise ValueError("TM acceleration shard contains a non-z19 candidate")
    if any(str(row.get("capture_date", ""))[:10] < "2019-01-01" for row in rows):
        raise ValueError("TM acceleration shard contains a pre-2019 candidate")

    keys = [
        (str(row.get("anchor_id", "")), str(row.get("capture_date", ""))[:10])
        for row in rows
    ]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate (anchor_id, capture_date) in TM route shard")

    lane_rows: list[list[dict[str, str]]] = [[] for _ in range(lanes)]
    anchor_lane: dict[str, int] = {}
    for row in rows:
        anchor_id = str(row["anchor_id"])
        slot = int.from_bytes(
            hashlib.sha256(anchor_id.encode("utf-8")).digest()[:8], "big"
        ) % lanes
        prior = anchor_lane.setdefault(anchor_id, slot)
        if prior != slot:
            raise AssertionError("anchor lane assignment changed")
        lane_rows[slot].append(row)

    out_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    records = []
    for index, shard in enumerate(lane_rows, 1):
        lane_id = f"{route_id}_tm_lane{index:02d}"
        path = out_dir / f"{lane_id}.csv"
        write_csv_rows(path, shard, fieldnames)
        records.append(
            {
                "lane_id": lane_id,
                "candidate_count": len(shard),
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "schema_version": "ct05_tm3_route_plan_v1",
        "created_utc": utc_now(),
        "route_id": route_id,
        "source": str(candidates_csv),
        "source_sha256": hashlib.sha256(candidates_csv.read_bytes()).hexdigest(),
        "candidate_count": len(rows),
        "lanes": lanes,
        "request_interval_per_lane_s": 2.0,
        "theoretical_route_qps": lanes / 2.0,
        "lane_shards": records,
    }
    (out_dir / f"{route_id}_tm3_plan.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates-csv", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    parser.add_argument("--lanes", type=int, default=3)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = split_tm_route(
        args.candidates_csv,
        route_id=args.route_id,
        lanes=args.lanes,
        out_dir=args.out_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
