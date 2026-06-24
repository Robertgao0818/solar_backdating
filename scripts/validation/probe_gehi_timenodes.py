#!/usr/bin/env python3
"""Probe GEHI TM vintage availability at grid-cell centroids for time-node heatmaps.

Drives ``gehi_info`` (centroid ``info`` probe) over an anchors CSV in parallel and
writes one row per (anchor x distinct capture_date). This is the lightweight probe
the time-node heatmap deliverable (Task 3) needs: it enumerates the GEHI/TM imagery
capture dates available at each grid cell's centroid, NOT the install-date scan.

Resumable: anchors already present in --output are skipped. Raw GEHI stdout/stderr is
logged to --raw-log (jsonl) for adversarial verification.

Usage:
  python scripts/validation/probe_gehi_timenodes.py \
      --anchors-csv data/timenode_heatmaps/anchors_cpt2083.csv \
      --output      data/timenode_heatmaps/gehi_info_cpt.csv \
      --raw-log     data/timenode_heatmaps/gehi_info_cpt_raw.jsonl \
      --workers 8
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import (  # noqa: E402
    DEFAULT_GEHI_EXE,
    DEFAULT_PROBE_ZOOM,
    DEFAULT_PROVIDER,
    GehiRunResult,
)
from scripts.temporal.gehi_info import fetch_vintages_for_anchor  # noqa: E402
from scripts.temporal.geid_temporal_common import read_csv_rows  # noqa: E402

OUT_FIELDS = [
    "anchor_id",
    "region_key",
    "grid_id",
    "provider",
    "zoom",
    "capture_date",
    "version",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--anchors-csv", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--raw-log", type=Path, required=True)
    p.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    p.add_argument("--zoom", type=int, default=DEFAULT_PROBE_ZOOM)
    p.add_argument("--provider", default=DEFAULT_PROVIDER)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--limit", type=int, help="Probe only the first N (post-skip) anchors.")
    return p.parse_args()


def load_done_anchor_ids(output: Path) -> set[str]:
    if not output.exists():
        return set()
    done: set[str] = set()
    with output.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            done.add(row["anchor_id"])
    return done


def main() -> None:
    args = parse_args()
    anchors = read_csv_rows(args.anchors_csv)
    if not anchors:
        raise SystemExit(f"No anchors in {args.anchors_csv}")

    done = load_done_anchor_ids(args.output)
    todo = [a for a in anchors if str(a["anchor_id"]) not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(
        f"[probe] {len(anchors)} anchors total, {len(done)} already done, "
        f"{len(todo)} to probe (workers={args.workers}, provider={args.provider}, z={args.zoom})",
        flush=True,
    )
    if not todo:
        print("[probe] nothing to do.", flush=True)
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_header = not args.output.exists()
    out_fh = args.output.open("a", newline="", encoding="utf-8")
    raw_fh = args.raw_log.open("a", encoding="utf-8")
    writer = csv.DictWriter(out_fh, fieldnames=OUT_FIELDS)
    if write_header:
        writer.writeheader()
    lock = threading.Lock()
    counter = {"done": 0, "rows": 0, "empty": 0}

    def work(anchor: dict) -> tuple[str, list[dict], dict | None]:
        raw_holder: dict = {}

        def _log(anchor_id: str, result: GehiRunResult) -> None:
            raw_holder["raw"] = {
                "anchor_id": anchor_id,
                "grid_id": anchor.get("grid_id", ""),
                "returncode": result.returncode,
                "command": result.command,
                "stdout_sha256": result.stdout_sha256,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        rows = fetch_vintages_for_anchor(
            anchor,
            zoom=args.zoom,
            provider=args.provider,
            gehi_exe=args.gehi_exe,
            timeout=args.timeout,
            raw_log_callback=_log,
        )
        return str(anchor["anchor_id"]), rows, raw_holder.get("raw")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(work, a): a for a in todo}
        for fut in as_completed(futs):
            anchor = futs[fut]
            try:
                anchor_id, rows, raw = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"[probe] {anchor['anchor_id']} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                continue
            with lock:
                out_rows = [
                    {
                        "anchor_id": anchor_id,
                        "region_key": anchor.get("region_key", ""),
                        "grid_id": anchor.get("grid_id", ""),
                        "provider": args.provider,
                        "zoom": args.zoom,
                        "capture_date": r["capture_date"],
                        "version": r.get("version", ""),
                    }
                    for r in rows
                ]
                for r in out_rows:
                    writer.writerow(r)
                out_fh.flush()
                if raw is not None:
                    raw_fh.write(json.dumps(raw, ensure_ascii=False) + "\n")
                    raw_fh.flush()
                counter["done"] += 1
                counter["rows"] += len(out_rows)
                if not out_rows:
                    counter["empty"] += 1
                if counter["done"] % 50 == 0 or counter["done"] == len(todo):
                    print(
                        f"[probe] {counter['done']}/{len(todo)} cells | "
                        f"{counter['rows']} date-rows | {counter['empty']} empty",
                        flush=True,
                    )

    out_fh.close()
    raw_fh.close()
    print(
        f"[probe] DONE: probed {counter['done']} cells, wrote {counter['rows']} date-rows, "
        f"{counter['empty']} empty -> {args.output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
