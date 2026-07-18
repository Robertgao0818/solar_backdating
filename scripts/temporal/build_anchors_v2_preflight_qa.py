#!/usr/bin/env python3
"""Build the ISSUE-27 30-anchor preflight placement-review strip.

The gate deterministically samples both routed arms, downloads the Vexcel 2024
census image for each exact 96 m v2 bbox, renders that TIFF through the same
registry-backed production renderer used by ``run_adaptive_scan.py``, and shows
the marked review crop beside the un-cropped 96 m census reference. The output
is for human placement review; this script never creates the approval sentinel.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.run_adaptive_scan import make_fixed_extent_review_renderer

DEFAULT_ANCHORS = (
    Path.home()
    / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
    / "anchors_v2/anchors_all.csv"
)
DEFAULT_OUTPUT_DIR = DEFAULT_ANCHORS.parent / "preflight_qa_30"
DEFAULT_ENV = PROJECT_ROOT.parent / "ZAsolar/.env"
VEXCEL_COLLECTION = "za-gp-johannesburg-2024"
VEXCEL_LAYER = "urban"
TIFF_SIGNATURES = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
MANIFEST_FIELDS = (
    "anchor_id",
    "chip_arm",
    "geometry_version",
    "grid_id",
    "source_area_m2",
    "vexcel_tif",
    "reference_png",
    "production_review_png",
)


def _hash_rank(seed: int, arm: str, anchor_id: str) -> str:
    return hashlib.sha256(f"{seed}|{arm}|{anchor_id}".encode()).hexdigest()


def sample_anchors(
    rows: list[dict[str, str]], *, count: int = 30, seed: int = 20260718
) -> list[dict[str, str]]:
    """Return a deterministic balanced A24/A48 placement-QA sample."""
    if count < 2:
        raise ValueError("preflight sample must contain at least two anchors")
    by_arm = {
        arm: [row for row in rows if row.get("chip_arm") == arm]
        for arm in ("A24", "A48")
    }
    if any(not by_arm[arm] for arm in by_arm):
        raise ValueError("preflight sample requires both A24 and A48 anchors")
    quotas = {"A24": count // 2, "A48": count - count // 2}
    sampled: list[dict[str, str]] = []
    for arm in ("A24", "A48"):
        ranked = sorted(
            by_arm[arm],
            key=lambda row: _hash_rank(seed, arm, row["anchor_id"]),
        )
        if len(ranked) < quotas[arm]:
            raise ValueError(
                f"preflight arm {arm} has {len(ranked)} rows, needs {quotas[arm]}"
            )
        sampled.extend(ranked[: quotas[arm]])
    return sorted(sampled, key=lambda row: (row["chip_arm"], row["anchor_id"]))


def _is_valid_tiff(path: Path) -> bool:
    return (
        path.exists()
        and path.stat().st_size > 8
        and path.read_bytes()[:4] in TIFF_SIGNATURES
    )


def fetch_vexcel_references(
    rows: list[dict[str, str]],
    out_dir: Path,
    *,
    workers: int,
    token: str,
    base_url: str,
) -> dict[str, Path]:
    import requests
    from shapely.geometry import box

    out_dir.mkdir(parents=True, exist_ok=True)

    def fetch(row: dict[str, str]) -> tuple[str, Path | None, str]:
        anchor_id = row["anchor_id"]
        destination = out_dir / f"{anchor_id}.tif"
        if _is_valid_tiff(destination):
            return anchor_id, destination, "cached"
        bbox = (
            float(row["chip_lon_min"]),
            float(row["chip_lat_min"]),
            float(row["chip_lon_max"]),
            float(row["chip_lat_max"]),
        )
        params = {
            "layer": VEXCEL_LAYER,
            "collection": VEXCEL_COLLECTION,
            "wkt": box(*bbox).wkt,
            "srid": "4326",
            "image-format": "tiff",
            "token": token,
        }
        try:
            response = requests.get(
                f"{base_url.rstrip('/')}/ortho/extract",
                params=params,
                timeout=180,
            )
        except Exception as exc:  # noqa: BLE001
            return anchor_id, None, f"request error: {exc}"
        if response.status_code != 200 or response.content[:4] not in TIFF_SIGNATURES:
            return anchor_id, None, f"HTTP {response.status_code}: {response.text[:120]}"
        temporary = destination.with_suffix(".part")
        temporary.write_bytes(response.content)
        temporary.replace(destination)
        return anchor_id, destination, "downloaded"

    resolved: dict[str, Path] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(fetch, row) for row in rows]
        for future in as_completed(futures):
            anchor_id, path, status = future.result()
            if path is None:
                failures.append(f"{anchor_id}: {status}")
            else:
                resolved[anchor_id] = path
    if failures:
        raise RuntimeError(
            f"Vexcel preflight failed for {len(failures)} anchors; "
            f"first={failures[:3]}"
        )
    return resolved


def vexcel_tif_to_reference_png(path: Path) -> Path:
    """Decode a provider TIFF through rasterio, stripping problematic XMP tags."""
    import numpy as np
    import rasterio
    from PIL import Image

    output = path.with_name(f"{path.stem}.reference.png")
    if output.exists() and output.stat().st_size > 0:
        return output
    with rasterio.open(path) as source:
        if source.count >= 3:
            array = source.read((1, 2, 3))
        else:
            mono = source.read(1)
            array = np.stack((mono, mono, mono))
    if array.dtype != np.uint8:
        low = float(np.nanmin(array))
        high = float(np.nanmax(array))
        if high <= low:
            array = np.zeros(array.shape, dtype=np.uint8)
        else:
            array = np.clip((array - low) * 255.0 / (high - low), 0, 255).astype(
                np.uint8
            )
    image = Image.fromarray(np.moveaxis(array, 0, -1), mode="RGB")
    temporary = output.with_suffix(".part")
    image.save(temporary, format="PNG")
    temporary.replace(output)
    return output


def render_preflight_rows(
    rows: list[dict[str, str]], references: dict[str, Path]
) -> list[dict[str, str]]:
    """Render every reference through the production fixed-extent renderer."""
    renderers = {
        "A24": make_fixed_extent_review_renderer(24.0),
        "A48": make_fixed_extent_review_renderer(48.0),
    }
    out: list[dict[str, str]] = []
    for row in rows:
        anchor_id = row["anchor_id"]
        reference_tif = references.get(anchor_id)
        if reference_tif is None or not _is_valid_tiff(reference_tif):
            raise ValueError(f"missing valid Vexcel reference for {anchor_id}")
        reference_png = vexcel_tif_to_reference_png(reference_tif)
        review_png = renderers[row["chip_arm"]](reference_png, row)
        out.append(
            {
                "anchor_id": anchor_id,
                "chip_arm": row["chip_arm"],
                "geometry_version": row["geometry_version"],
                "grid_id": row["grid_id"],
                "source_area_m2": row["source_area_m2"],
                "vexcel_tif": str(reference_tif),
                "reference_png": str(reference_png),
                "production_review_png": str(review_png),
            }
        )
    return out


def _relative(path: str, base: Path) -> str:
    return os.path.relpath(path, base)


def render_html(rows: list[dict[str, str]], *, output_dir: Path) -> str:
    cards: list[str] = []
    for row in rows:
        reference = html.escape(_relative(row["reference_png"], output_dir))
        review = html.escape(_relative(row["production_review_png"], output_dir))
        cards.append(
            "<article class='card'>"
            f"<h2>{html.escape(row['anchor_id'])}</h2>"
            f"<p>{html.escape(row['chip_arm'])} · "
            f"{html.escape(row['geometry_version'])} · "
            f"{html.escape(row['grid_id'])} · area={html.escape(row['source_area_m2'])} m²</p>"
            "<div class='pair'>"
            f"<figure><img src='{review}'><figcaption>Production marked review crop</figcaption></figure>"
            f"<figure><img src='{reference}'><figcaption>Vexcel census 96 m reference</figcaption></figure>"
            "</div></article>"
        )
    counts = Counter(row["chip_arm"] for row in rows)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Anchors v2 preflight placement QA</title>"
        "<style>body{margin:0;background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif}"
        "header{position:sticky;top:0;background:#010409;padding:14px 20px;border-bottom:1px solid #30363d}"
        "main{padding:18px;display:grid;gap:16px}.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px}"
        "h1,h2{margin:0 0 6px}h2{font:600 13px ui-monospace,monospace;overflow-wrap:anywhere}p,figcaption{color:#9da7b3;font-size:12px}"
        ".pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}figure{margin:0}img{display:block;width:100%;height:auto;background:#000;border-radius:5px}"
        "@media(max-width:800px){.pair{grid-template-columns:1fr}}</style></head><body>"
        f"<header><h1>Anchors v2 placement preflight</h1><div>"
        f"{len(rows)} anchors · A24={counts['A24']} · A48={counts['A48']} · "
        "review marker/box must enclose the census PV detection</div></header>"
        f"<main>{''.join(cards)}</main></body></html>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    args = parser.parse_args()

    with args.anchors_csv.open("r", newline="", encoding="utf-8") as fh:
        anchors = [dict(row) for row in csv.DictReader(fh)]
    sampled = sample_anchors(anchors, count=args.count, seed=args.seed)

    from core.vexcel_auth import load_env, resolve_token

    env = load_env(args.env_file)
    base_url = env.get("VEXCEL_API_BASE", "https://api.vexcelgroup.com/v2")
    token = resolve_token(env, base_url)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    references = fetch_vexcel_references(
        sampled,
        args.output_dir / "vexcel_96m",
        workers=args.workers,
        token=token,
        base_url=base_url,
    )
    rendered = render_preflight_rows(sampled, references)
    manifest_path = args.output_dir / "qa_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(MANIFEST_FIELDS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rendered)
    html_path = args.output_dir / "index.html"
    html_path.write_text(
        render_html(rendered, output_dir=args.output_dir), encoding="utf-8"
    )
    print(
        f"preflight={len(rendered)} arms={dict(Counter(r['chip_arm'] for r in rendered))} "
        f"html={html_path} approval_sentinel={args.output_dir / '.approved'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
