#!/usr/bin/env python3
"""Build the v2 ten-grid image pack from local files, then download gaps.

Satellite chips already stored under the Cape Town citywide wave trees are
hardlinked into the pack. Aerial crops are taken from the local tile store
when a tile covers the roof. Missing aerials use ``fetch_cape_town_wms``.
Missing TM z19 satellite rows use ``run_a3_batch_lane``, which reads the
shared GEHI tile cache. This script does not score images.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_PLAN = Path.home() / (
    "zasolar_data/geid_temporal/reference_fulltimeline_lite_10grid_20261004/plan"
)
DEFAULT_CITYWIDE = Path.home() / (
    "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
)
DEFAULT_PACK = Path.home() / (
    "zasolar_data/geid_temporal/reference_fulltimeline_v2_10grid_20261005"
)
DEFAULT_AERIAL_ROOT = Path.home() / "zasolar_data/tiles/cape_town/aerial_2025"
CHIP_DATE_RE = re.compile(r"_(\d{8})_v")
GRIDS = (
    "CPT1524",
    "CPT1956",
    "CPT2274",
    "CPT2304",
    "CPT2305",
    "CPT2434",
    "CPT2935",
    "CPT2947",
    "CPT3234",
    "CPT3359",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[Mapping[str, object]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def capture_date_from_chip_name(name: str) -> str | None:
    """Return YYYY-MM-DD from ``{anchor}_{YYYYMMDD}_v{version}.tif``."""

    match = CHIP_DATE_RE.search(name)
    if match is None:
        return None
    token = match.group(1)
    return f"{token[:4]}-{token[4:6]}-{token[6:8]}"


def zoom_from_chip_path(path: Path) -> int | None:
    parent = path.parent.name
    if not parent.startswith("z") or not parent[1:].isdigit():
        return None
    return int(parent[1:])


def choose_local_chip(paths: Iterable[Path]) -> Path | None:
    """Prefer the higher zoom, then the larger non-empty file."""

    scored: list[tuple[int, int, str, Path]] = []
    for path in paths:
        if not path.is_file():
            continue
        size = path.stat().st_size
        zoom = zoom_from_chip_path(path)
        if size <= 0 or zoom is None or capture_date_from_chip_name(path.name) is None:
            continue
        scored.append((zoom, size, str(path), path))
    if not scored:
        return None
    scored.sort()
    return scored[-1][3]


def satellite_targets(
    candidates: Sequence[Mapping[str, str]],
    references: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    """One row per anchor and capture date that the ten-grid pack must hold.

    Callable historical rows and post-census satellite references are merged.
    Aerial reference rows are not satellite chips. The same roof and date keeps
    both roles instead of being downloaded twice.
    """

    merged: dict[tuple[str, str], dict[str, str]] = {}
    for row in candidates:
        if str(row.get("callable") or "") != "1":
            continue
        _add_satellite(merged, row, "candidate")
    for row in references:
        if str(row.get("kind") or "") != "satellite":
            continue
        _add_satellite(merged, row, "reference")
    return [merged[key] for key in sorted(merged)]


def _add_satellite(
    merged: dict[tuple[str, str], dict[str, str]],
    row: Mapping[str, str],
    role: str,
) -> None:
    anchor_id = str(row.get("anchor_id") or "").strip()
    capture_date = str(row.get("capture_date") or "").strip()[:10]
    if not anchor_id or not capture_date:
        raise ValueError(f"satellite row missing anchor or date: {row!r}")
    key = (anchor_id, capture_date)
    item = merged.get(key)
    if item is None:
        item = {
            "anchor_id": anchor_id,
            "capture_date": capture_date,
            "provider": str(row.get("provider") or "").strip(),
            "requested_zoom": str(row.get("requested_zoom") or "").strip(),
            "version": str(row.get("version") or "").strip(),
            "roles": role,
        }
        merged[key] = item
        return
    roles = set(filter(None, item["roles"].split("|")))
    roles.add(role)
    item["roles"] = "|".join(sorted(roles))
    if not item["provider"]:
        item["provider"] = str(row.get("provider") or "").strip()
    if not item["requested_zoom"]:
        item["requested_zoom"] = str(row.get("requested_zoom") or "").strip()
    if not item["version"]:
        item["version"] = str(row.get("version") or "").strip()


def chip_roots(citywide: Path) -> list[Path]:
    roots: list[Path] = []
    if not citywide.is_dir():
        return roots
    for wave in sorted(path for path in citywide.glob("wave_*") if path.is_dir()):
        for sub in sorted(path for path in wave.iterdir() if path.is_dir()):
            chips = sub if sub.name == "chips" else sub / "chips"
            if chips.is_dir():
                roots.append(chips)
    return roots


def index_local_chips(
    roots: Sequence[Path],
    targets: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Path]:
    """Find existing chips by looking up each needed anchor directory."""

    by_anchor: dict[str, set[str]] = {}
    for row in targets:
        by_anchor.setdefault(row["anchor_id"], set()).add(row["capture_date"])
    found: dict[tuple[str, str], list[Path]] = {}
    for root in roots:
        for anchor_id, dates in by_anchor.items():
            anchor_dir = root / anchor_id
            if not anchor_dir.is_dir():
                continue
            for path in anchor_dir.rglob("*.tif"):
                capture_date = capture_date_from_chip_name(path.name)
                if capture_date is None or capture_date not in dates:
                    continue
                found.setdefault((anchor_id, capture_date), []).append(path)
    chosen: dict[tuple[str, str], Path] = {}
    for key, paths in found.items():
        best = choose_local_chip(paths)
        if best is not None:
            chosen[key] = best
    return chosen


def link_file(source: Path, destination: Path) -> str:
    """Hardlink a local chip into the pack. Copy only if the link is refused."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        return "already_present"
    if destination.exists():
        destination.unlink()
    try:
        os.link(source, destination)
        return "linked"
    except OSError:
        import shutil

        shutil.copy2(source, destination)
        return "copied"


def local_destination(pack: Path, source: Path, anchor_id: str) -> Path:
    zoom = source.parent.name
    return pack / "imagery" / "satellite_local" / anchor_id / zoom / source.name


def aerial_destination(pack: Path, anchor_id: str) -> Path:
    return pack / "imagery" / "aerial" / f"{anchor_id}.tif"


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare(args: argparse.Namespace) -> dict[str, object]:
    plan = args.plan_dir
    anchors = read_csv(plan / "anchor_selection.csv")
    targets = satellite_targets(
        read_csv(plan / "candidates.csv"),
        read_csv(plan / "references.csv"),
    )
    grids = {row["anchor_id"]: row["grid_id"] for row in anchors}
    outside = sorted({row["anchor_id"] for row in targets if row["anchor_id"] not in grids})
    if outside:
        raise SystemExit(f"{len(outside)} satellite targets are outside the frozen anchors")
    roots = chip_roots(args.citywide)
    found = index_local_chips(roots, targets)
    linked_rows: list[dict[str, object]] = []
    missing: list[dict[str, str]] = []
    for row in targets:
        key = (row["anchor_id"], row["capture_date"])
        source = found.get(key)
        record = {
            **row,
            "grid_id": grids.get(row["anchor_id"], ""),
            "local_status": "missing",
            "source_path": "",
            "pack_path": "",
            "bytes": 0,
        }
        if source is not None:
            destination = local_destination(args.pack, source, row["anchor_id"])
            status = link_file(source, destination)
            record.update(
                {
                    "local_status": status,
                    "source_path": str(source),
                    "pack_path": str(destination),
                    "bytes": destination.stat().st_size,
                }
            )
        else:
            missing.append(row)
        linked_rows.append(record)
    fields = [
        "anchor_id",
        "grid_id",
        "capture_date",
        "provider",
        "requested_zoom",
        "version",
        "roles",
        "local_status",
        "source_path",
        "pack_path",
        "bytes",
    ]
    write_csv(args.pack / "metadata" / "satellite_local_index.csv", linked_rows, fields)
    write_csv(
        args.pack / "plan" / "satellite_missing.csv",
        missing,
        ["anchor_id", "capture_date", "provider", "requested_zoom", "version", "roles"],
    )
    write_csv(
        args.pack / "plan" / "aerial_targets.csv",
        [
            {
                "anchor_id": row["anchor_id"],
                "grid_id": row["grid_id"],
                "chip_lon_min": row["chip_lon_min"],
                "chip_lat_min": row["chip_lat_min"],
                "chip_lon_max": row["chip_lon_max"],
                "chip_lat_max": row["chip_lat_max"],
            }
            for row in anchors
        ],
        [
            "anchor_id",
            "grid_id",
            "chip_lon_min",
            "chip_lat_min",
            "chip_lon_max",
            "chip_lat_max",
        ],
    )
    providers = sorted({row["provider"] for row in missing})
    zooms = sorted({row["requested_zoom"] for row in missing})
    summary = {
        "n_anchors": len(anchors),
        "n_satellite_targets": len(targets),
        "n_local_satellite": len(targets) - len(missing),
        "n_satellite_missing": len(missing),
        "n_chip_roots": len(roots),
        "missing_providers": providers,
        "missing_zooms": zooms,
        "aerial_tile_root": str(args.aerial_root),
        "note": (
            "Per-anchor citywide chips were searched by anchor directory. "
            "The shared GEHI tile cache is not a chip and is used only by the downloader."
        ),
    }
    _write_json(args.pack / "metadata" / "PREPARE.json", summary)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


def _download_one_aerial(row: Mapping[str, str], pack: Path, attempts: int) -> dict[str, object]:
    from scripts.temporal.build_delivery_review_package import ChipRequest, fetch_cape_town_wms

    destination = aerial_destination(pack, row["anchor_id"])
    if destination.is_file() and destination.stat().st_size > 0:
        return {
            "anchor_id": row["anchor_id"],
            "grid_id": row["grid_id"],
            "status": "already_present",
            "path": str(destination),
            "bytes": destination.stat().st_size,
            "error": "",
        }
    request = ChipRequest(
        city="cape_town",
        anchor_id=row["anchor_id"],
        source_feature_id=row["anchor_id"],
        kind="aerial",
        capture_date="2025-01",
        provider="cape_town_wms_2025jan",
        bbox=(
            float(row["chip_lon_min"]),
            float(row["chip_lat_min"]),
            float(row["chip_lon_max"]),
            float(row["chip_lat_max"]),
        ),
    )
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            fetch_cape_town_wms(request, destination)
            if destination.is_file() and destination.stat().st_size > 0:
                return {
                    "anchor_id": row["anchor_id"],
                    "grid_id": row["grid_id"],
                    "status": "downloaded",
                    "path": str(destination),
                    "bytes": destination.stat().st_size,
                    "error": "",
                }
            last_error = "empty_output"
        except Exception as exc:  # noqa: BLE001 - one roof must not stop the pack
            last_error = f"{type(exc).__name__}: {exc}"
        if destination.exists():
            destination.unlink()
        time.sleep(min(30, 2 ** attempt))
    return {
        "anchor_id": row["anchor_id"],
        "grid_id": row["grid_id"],
        "status": "failed",
        "path": str(destination),
        "bytes": 0,
        "error": last_error,
    }


def download_aerial(args: argparse.Namespace) -> dict[str, object]:
    targets = read_csv(args.pack / "plan" / "aerial_targets.csv")
    if args.limit:
        targets = targets[: args.limit]
    log_path = args.pack / "metadata" / "aerial_download.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    counts = {"downloaded": 0, "already_present": 0, "failed": 0}
    with log_path.open("a", encoding="utf-8") as log, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(_download_one_aerial, row, args.pack, args.attempts) for row in targets
        ]
        done = 0
        for future in as_completed(futures):
            record = future.result()
            counts[str(record["status"])] = counts.get(str(record["status"]), 0) + 1
            log.write(json.dumps(record, sort_keys=True) + "\n")
            log.flush()
            done += 1
            if done % 25 == 0 or record["status"] == "failed":
                print(
                    f"[aerial] {done}/{len(targets)} {record['anchor_id']} {record['status']}",
                    flush=True,
                )
    summary = {"n_targets": len(targets), **counts}
    if not args.limit:
        _write_json(args.pack / "metadata" / "AERIAL_DONE.json", summary)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


def _completed_z18(log_path: Path) -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    if not log_path.is_file():
        return done
    with log_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") in {"ok", "skipped_existing"}:
                done.add((row["anchor_id"], row["capture_date"]))
    return done


def download_zoom18(args: argparse.Namespace, rows: Sequence[Mapping[str, str]]) -> dict[str, object]:
    """Download catalog rows whose complete coverage is z18, not z19.

    These rows are outside the A3 z19 batch. The ladder stays on the requested
    zoom so a z19 image is not substituted for a date the catalog did not mark
    complete at z19.
    """

    from scripts.temporal.gehi_download import download_chip_with_zoom_ladder

    anchors = {
        row["anchor_id"]: row
        for row in read_csv(args.plan_dir / "anchor_selection.csv")
    }
    log_path = args.pack / "metadata" / "satellite_z18.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    done = _completed_z18(log_path)
    counts = {"ok": 0, "skipped_existing": 0, "failed": 0, "skipped_done": 0}
    output = args.pack / "imagery" / "satellite_z18"
    with log_path.open("a", encoding="utf-8") as handle:
        for index, row in enumerate(rows, start=1):
            key = (row["anchor_id"], row["capture_date"])
            if key in done:
                counts["skipped_done"] += 1
                continue
            result = download_chip_with_zoom_ladder(
                anchors[row["anchor_id"]],
                capture_date=row["capture_date"],
                version=row.get("version") or "noversion",
                zoom_ladder=(int(row["requested_zoom"]),),
                output_root=output,
                provider=row.get("provider") or "TM",
                allow_nearest=False,
                parallel=args.parallel,
            )
            status = result.status if result.status in {"ok", "skipped_existing"} else "failed"
            counts[status] += 1
            record = {
                "anchor_id": row["anchor_id"],
                "capture_date": row["capture_date"],
                "status": result.status,
                "path": str(result.path or ""),
                "actual_zoom": result.actual_zoom,
                "error": result.error or "",
                "bytes": result.path.stat().st_size if result.path and result.path.is_file() else 0,
            }
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            if index % 10 == 0 or status == "failed":
                print(
                    f"[satellite-z18] {index}/{len(rows)} {row['anchor_id']} "
                    f"{row['capture_date']} {result.status}",
                    flush=True,
                )
    return counts


def download_satellite(args: argparse.Namespace) -> int:
    missing_csv = args.pack / "plan" / "satellite_missing.csv"
    rows = read_csv(missing_csv)
    if not rows:
        _write_json(args.pack / "metadata" / "SATELLITE_DONE.json", {"n_missing": 0})
        print(json.dumps({"n_missing": 0}), flush=True)
        return 0
    providers = {row["provider"] for row in rows}
    zooms = {row["requested_zoom"] for row in rows}
    if not providers <= {"TM"} or not zooms <= {"18", "19"}:
        raise SystemExit(
            f"satellite gap has an unsupported provider or zoom "
            f"(providers={sorted(providers)} zooms={sorted(zooms)})"
        )
    anchor_grids = {
        row["anchor_id"]: row["grid_id"]
        for row in read_csv(args.plan_dir / "anchor_selection.csv")
    }
    unknown_grids = sorted(
        {
            anchor_grids.get(row["anchor_id"], "")
            for row in rows
            if row["requested_zoom"] == "19"
        }
        - set(GRIDS)
    )
    if unknown_grids:
        raise SystemExit(f"z19 targets include grids outside the ten-grid pack: {unknown_grids}")
    output = args.pack / "imagery" / "satellite_a3"
    command = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "scripts/temporal/run_a3_batch_lane.py"),
        "--candidates-csv",
        str(missing_csv),
        "--anchors-csv",
        str(args.plan_dir / "anchor_selection.csv"),
        "--output-dir",
        str(output),
        "--provider",
        "TM",
        "--zoom",
        "19",
        "--grids",
        ",".join(GRIDS),
        "--request-interval",
        str(args.request_interval),
        "--parallel",
        str(args.parallel),
    ]
    print("[satellite] " + " ".join(command), flush=True)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        return completed.returncode
    lower = [row for row in rows if row["requested_zoom"] != "19"]
    lower_counts = download_zoom18(args, lower) if lower else {"n": 0}
    _write_json(
        args.pack / "metadata" / "SATELLITE_DONE.json",
        {
            "n_missing": len(rows),
            "n_z19": len(rows) - len(lower),
            "n_lower_zoom": len(lower),
            "lower_zoom": lower_counts,
            "output_dir": str(output),
            "exit_code": 0,
        },
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--citywide", type=Path, default=DEFAULT_CITYWIDE)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--aerial-root", type=Path, default=DEFAULT_AERIAL_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    aerial = sub.add_parser("aerial")
    aerial.add_argument("--limit", type=int, default=0)
    aerial.add_argument("--workers", type=int, default=4)
    aerial.add_argument("--attempts", type=int, default=3)
    satellite = sub.add_parser("satellite")
    satellite.add_argument("--request-interval", type=float, default=1.0)
    satellite.add_argument("--parallel", type=int, default=4)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        prepare(args)
        return 0
    if args.command == "aerial":
        download_aerial(args)
        return 0
    if args.command == "satellite":
        return download_satellite(args)
    raise SystemExit(f"unknown command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
