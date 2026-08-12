#!/usr/bin/env python3
"""Render CT-11 strips at native chip resolution for diagnostic re-review.

The release QA package is immutable.  This renderer consumes its blind
manifest and frame report but writes a separate package.  It deliberately
omits anchor IDs, production states, intervals, and prior verdicts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_inputs(manifest_path: Path, frame_report_path: Path) -> tuple[list[dict], dict[tuple[str, str], Path]]:
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("blind manifest must be a non-empty JSON list")
    frame_paths: dict[tuple[str, str], Path] = {}
    with frame_report_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["source"] != "scan_tm" or row["status"] not in {"ok", "skipped_existing"}:
                continue
            frame_paths[(row["anchor_id"], row["capture_date"])] = Path(row["chip_path"])
    return manifest, frame_paths


def render_native_review(
    manifest_path: Path,
    frame_report_path: Path,
    output_dir: Path,
    *,
    rows_per_sheet: int = 3,
    prefix: str = "native",
) -> dict:
    if rows_per_sheet < 1:
        raise ValueError("rows_per_sheet must be positive")
    manifest, frame_paths = load_inputs(manifest_path, frame_report_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    tile_w, tile_h = 398, 314
    label_h, left_w, gap = 20, 120, 8
    row_h = label_h * 2 + tile_h * 2 + gap * 3
    sheet_w = left_w + tile_w * 2 + gap * 3
    rendered_manifest: list[dict] = []
    sheet_paths: list[Path] = []

    for sheet_no, start in enumerate(range(0, len(manifest), rows_per_sheet), start=1):
        items = manifest[start : start + rows_per_sheet]
        sheet = Image.new("RGB", (sheet_w, row_h * len(items)), "white")
        draw = ImageDraw.Draw(sheet)
        for row_offset, item in enumerate(items):
            anchor_id = item["anchor_id"]
            dates = list(item["frame_dates"])
            if len(dates) > 4:
                raise ValueError(f"{anchor_id} has {len(dates)} frames; native layout supports at most four")
            base_y = row_offset * row_h
            draw.line((0, base_y, sheet_w, base_y), fill="#999999", width=1)
            blind_label = item.get("blind_index", item.get("repeat_index"))
            draw.text((8, base_y + 10), f"blind {int(blind_label):03d}", fill="black", font=font)
            draw.text((8, base_y + 28), f"grid {item['grid_id']}", fill="black", font=font)
            frame_records = []
            for index, capture_date in enumerate(dates):
                path = frame_paths.get((anchor_id, capture_date))
                if path is None or not path.is_file():
                    raise FileNotFoundError(f"missing frame for {anchor_id} {capture_date}: {path}")
                col, row = index % 2, index // 2
                x = left_w + gap + col * (tile_w + gap)
                y = base_y + gap + row * (tile_h + label_h + gap)
                draw.text((x, y), capture_date, fill="black", font=font)
                with Image.open(path) as source:
                    rgb = source.convert("RGB")
                    source_size = rgb.size
                    rgb.thumbnail((tile_w, tile_h), Image.Resampling.LANCZOS)
                    paste_x = x + (tile_w - rgb.width) // 2
                    paste_y = y + label_h + (tile_h - rgb.height) // 2
                    sheet.paste(rgb, (paste_x, paste_y))
                frame_records.append(
                    {
                        "capture_date": capture_date,
                        "source_size": list(source_size),
                        "rendered_size": [rgb.width, rgb.height],
                        "chip_sha256": sha256_file(path),
                    }
                )
            rendered_manifest.append(
                {
                    "sheet": f"{prefix}_{sheet_no:03d}.png",
                    "blind_index": int(blind_label),
                    "grid_id": item["grid_id"],
                    "n_frames": len(dates),
                    "frame_dates": dates,
                    "frames": frame_records,
                }
            )
        sheet_path = output_dir / f"{prefix}_{sheet_no:03d}.png"
        sheet.save(sheet_path, format="PNG", optimize=True)
        sheet_paths.append(sheet_path)

    output_manifest = output_dir / f"{prefix}_manifest.json"
    output_manifest.write_text(json.dumps(rendered_manifest, indent=2) + "\n")
    package = {
        "schema_version": 1,
        "purpose": "native-resolution diagnostic re-review; not a replacement for the frozen CT-11 release QA",
        "blind_manifest_sha256": sha256_file(manifest_path),
        "frame_report_sha256": sha256_file(frame_report_path),
        "n_anchors": len(rendered_manifest),
        "n_sheets": len(sheet_paths),
        "rows_per_sheet": rows_per_sheet,
        "tile_box": [tile_w, tile_h],
        "contains_anchor_ids": False,
        "contains_production_outcomes": False,
        "rendered_manifest": output_manifest.name,
        "rendered_manifest_sha256": sha256_file(output_manifest),
        "sheets": [{"path": path.name, "sha256": sha256_file(path)} for path in sheet_paths],
    }
    package_path = output_dir / f"{prefix}_package.json"
    package_path.write_text(json.dumps(package, indent=2) + "\n")
    return package


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind-manifest", type=Path, required=True)
    parser.add_argument("--frame-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rows-per-sheet", type=int, default=3)
    parser.add_argument("--prefix", default="native")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    package = render_native_review(
        args.blind_manifest,
        args.frame_report,
        args.output_dir,
        rows_per_sheet=args.rows_per_sheet,
        prefix=args.prefix,
    )
    print(json.dumps(package, indent=2))


if __name__ == "__main__":
    main()
