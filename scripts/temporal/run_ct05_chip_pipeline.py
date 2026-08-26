#!/usr/bin/env python3
"""Plan four-route CT-05 downloads and quality-gate the resulting chips.

``plan`` consumes the reconciled CT catalog and emits route/provider/zoom
shards suitable for ``gehi_download.py``.  ``qa`` reconciles all returned
download manifests against the candidate denominator and distinguishes
missing/download/decode/pixel/bbox failures.  No failure class is converted
to an absence observation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_download import sha256_file  # noqa: E402
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows  # noqa: E402
from scripts.temporal.run_ct05_catalog_probe import (  # noqa: E402
    DEFAULT_ROUTES,
    utc_now,
)

RUN3_DOWNLOAD_MIN_DATE = "2019-01-01"

QA_FIELDS = (
    "anchor_id",
    "capture_date",
    "version",
    "provider",
    "requested_zoom",
    "actual_zoom",
    "zoom_status",
    "download_status",
    "path",
    "manifest_sha256",
    "actual_sha256",
    "hash_status",
    "decode_status",
    "bbox_status",
    "pixel_quality_status",
    "pixel_std",
    "pixel_p01",
    "pixel_p99",
    "near_black_fraction",
    "near_white_fraction",
    "release_eligible",
    "failure_class",
    "reference_only",
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def candidate_key(row: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        str(row.get("anchor_id", "")).strip(),
        str(row.get("capture_date", "")).strip()[:10],
        str(row.get("version", "")).strip(),
        str(row.get("provider", "")).strip(),
    )


def expand_route_slots(
    routes: Sequence[str],
    route_weights: Mapping[str, int] | None = None,
) -> list[str]:
    """Expand routes into hash slots; weight N = N slots (default weight 1).

    Weighted assignment keeps slow/light hosts (e.g. koko limited to one
    GEHI process) from becoming the straggler that stalls every wave.
    """
    if not routes:
        raise ValueError("at least one route is required")
    slots: list[str] = []
    for route in routes:
        weight = 1 if route_weights is None else int(route_weights.get(route, 1))
        if weight < 1:
            raise ValueError(f"route weight must be >= 1: {route}={weight}")
        slots.extend([route] * weight)
    return slots


def stable_download_route(
    row: Mapping[str, object],
    routes: Sequence[str],
    route_weights: Mapping[str, int] | None = None,
) -> str:
    slots = expand_route_slots(routes, route_weights)
    key = "|".join(candidate_key(row))
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return slots[int.from_bytes(digest[:8], "big") % len(slots)]


def parse_route_weights(value: str) -> dict[str, int] | None:
    """Parse 'home_v4=2,home_v6=2,koko_v4=1'; empty -> None (uniform)."""
    weights: dict[str, int] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        route, sep, raw = item.partition("=")
        if not sep:
            raise ValueError(f"bad --route-weights entry (want route=N): {item!r}")
        weights[route.strip()] = int(raw)
    return weights or None


def plan_downloads(
    candidates: Sequence[Mapping[str, object]],
    routes: Sequence[str],
    out_dir: Path,
    *,
    min_date: str = RUN3_DOWNLOAD_MIN_DATE,
    route_weights: Mapping[str, int] | None = None,
) -> dict[str, object]:
    input_candidate_count = len(candidates)
    candidates = [
        row
        for row in candidates
        if str(row.get("capture_date", "")).strip()[:10] >= min_date
    ]
    if not candidates:
        raise ValueError("candidate CSV is empty")
    keys = [candidate_key(row) for row in candidates]
    if any(not all(key) for key in keys):
        # TM has a deliberately empty version, so validate the other elements.
        bad = [key for key in keys if not key[0] or not key[1] or not key[3]]
        if bad:
            raise ValueError(f"candidate has blank identity fields: {bad[0]}")
    anchor_date_keys = {(key[0], key[1]) for key in keys}
    if len(anchor_date_keys) != len(candidates):
        raise ValueError("duplicate (anchor_id, capture_date) candidates")

    shards: dict[tuple[str, str, int], list[Mapping[str, object]]] = {}
    for row in candidates:
        provider = str(row.get("provider", ""))
        if provider not in {"TM", "Wayback"}:
            raise ValueError(f"unsupported provider: {provider!r}")
        requested_zoom = int(row.get("requested_zoom", 0))
        if requested_zoom not in {19, 18}:
            raise ValueError(f"CT-05 only admits requested z19/z18, got {requested_zoom}")
        route = stable_download_route(row, routes, route_weights)
        shards.setdefault((route, provider, requested_zoom), []).append(row)

    shard_dir = out_dir / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(candidates[0].keys())
    records = []
    for route in routes:
        for provider in ("TM", "Wayback"):
            for zoom in (19, 18):
                rows = shards.get((route, provider, zoom), [])
                path = shard_dir / f"{route}_{provider.lower()}_z{zoom}.csv"
                write_csv_rows(path, rows, fieldnames)
                records.append(
                    {
                        "route_id": route,
                        "provider": provider,
                        "requested_zoom": zoom,
                        "download_ladder": "19,18" if zoom == 19 else "18",
                        "candidate_count": len(rows),
                        "path": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
    manifest = {
        "schema_version": "ct05_download_plan_v1",
        "created_utc": utc_now(),
        "input_candidate_count": input_candidate_count,
        "candidate_count": len(candidates),
        "anchor_date_key_count": len(anchor_date_keys),
        "min_capture_date": min_date,
        "routes": list(routes),
        "route_weights": dict(route_weights) if route_weights else None,
        "shards": records,
    }
    _write_json(out_dir / "download_plan.json", manifest)
    return manifest


def _manifest_success(row: Mapping[str, object]) -> bool:
    return str(row.get("status", "")).split(":", 1)[0] in {"ok", "skipped_existing"}


def load_manifests(paths: Iterable[Path]) -> dict[tuple[str, str, str, str], dict[str, str]]:
    by_key: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for path in paths:
        for row in read_csv_rows(path):
            key = candidate_key(row)
            prior = by_key.get(key)
            if prior is None or (_manifest_success(row) and not _manifest_success(prior)):
                by_key[key] = row
    return by_key


def _contains_bbox(
    raster_bounds_wgs84: tuple[float, float, float, float],
    anchor: Mapping[str, object],
    *,
    tolerance_deg: float = 1e-7,
) -> bool:
    left, bottom, right, top = raster_bounds_wgs84
    return (
        left <= float(anchor["chip_lon_min"]) + tolerance_deg
        and bottom <= float(anchor["chip_lat_min"]) + tolerance_deg
        and right >= float(anchor["chip_lon_max"]) - tolerance_deg
        and top >= float(anchor["chip_lat_max"]) - tolerance_deg
    )


def inspect_raster(
    path: Path,
    anchor: Mapping[str, object],
) -> dict[str, object]:
    import numpy as np
    import rasterio
    from rasterio.warp import transform_bounds

    try:
        with rasterio.open(path) as src:
            data = src.read(masked=True)
            if src.crs is None:
                raise ValueError("missing raster CRS")
            bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
            # GEHI's GeoTIFF affine transform is snapped to the output pixel
            # grid.  The requested lon/lat bbox can therefore fall just beyond
            # an edge by a fraction of one pixel even when the chip covers the
            # requested footprint (especially after WGS84 reprojection).  Use a
            # resolution-derived tolerance for that quantisation, while keeping
            # materially clipped chips far outside the tolerance.
            xres, yres = abs(float(src.res[0])), abs(float(src.res[1]))
            bbox_tolerance_deg = max(1e-7, 0.75 * xres, 0.75 * yres)
    except Exception as exc:  # noqa: BLE001 - QA records, rather than hides, decode failure
        return {
            "decode_status": "fail",
            "bbox_status": "not_checked",
            "pixel_quality_status": "not_checked",
            "failure_class": f"decode_failed:{type(exc).__name__}:{exc}",
        }

    values = np.asarray(data.compressed(), dtype=np.float32)
    if values.size == 0:
        return {
            "decode_status": "pass",
            "bbox_status": "pass" if _contains_bbox(bounds, anchor) else "fail",
            "pixel_quality_status": "fail",
            "failure_class": "pixel_empty",
        }
    p01, p99 = np.percentile(values, [1, 99])
    std = float(values.std())
    near_black = float(np.mean(values <= 5))
    near_white = float(np.mean(values >= 250))
    pixel_ok = (
        std >= 2.0
        and float(p99 - p01) >= 10.0
        and near_black < 0.98
        and near_white < 0.98
    )
    bbox_ok = _contains_bbox(bounds, anchor, tolerance_deg=bbox_tolerance_deg)
    failures = []
    if not bbox_ok:
        failures.append("bbox_incomplete")
    if not pixel_ok:
        failures.append("pixel_uninformative")
    return {
        "decode_status": "pass",
        "bbox_status": "pass" if bbox_ok else "fail",
        "pixel_quality_status": "pass" if pixel_ok else "fail",
        "pixel_std": std,
        "pixel_p01": float(p01),
        "pixel_p99": float(p99),
        "near_black_fraction": near_black,
        "near_white_fraction": near_white,
        "failure_class": ";".join(failures),
    }


def quality_gate(
    candidates: Sequence[Mapping[str, object]],
    anchors: Sequence[Mapping[str, object]],
    manifest_paths: Sequence[Path],
    out_dir: Path,
) -> dict[str, object]:
    if not candidates:
        raise ValueError("candidate CSV is empty")
    anchor_index = {str(row["anchor_id"]): row for row in anchors}
    manifests = load_manifests(manifest_paths)
    qa_rows: list[dict[str, object]] = []
    failures = Counter()

    for candidate in candidates:
        key = candidate_key(candidate)
        anchor_id, capture_date, version, provider = key
        base: dict[str, object] = {
            "anchor_id": anchor_id,
            "capture_date": capture_date,
            "version": version,
            "provider": provider,
            "requested_zoom": candidate.get("requested_zoom", ""),
            "actual_zoom": "",
            "zoom_status": "not_checked",
            "download_status": "missing_manifest",
            "path": "",
            "manifest_sha256": "",
            "actual_sha256": "",
            "hash_status": "not_checked",
            "decode_status": "not_checked",
            "bbox_status": "not_checked",
            "pixel_quality_status": "not_checked",
            "pixel_std": "",
            "pixel_p01": "",
            "pixel_p99": "",
            "near_black_fraction": "",
            "near_white_fraction": "",
            "release_eligible": 0,
            "failure_class": "missing_manifest",
            "reference_only": candidate.get("reference_only", ""),
        }
        manifest = manifests.get(key)
        if manifest is None:
            failures["missing_manifest"] += 1
            qa_rows.append(base)
            continue
        base.update(
            {
                "actual_zoom": manifest.get("actual_zoom", ""),
                "download_status": manifest.get("status", ""),
                "path": manifest.get("path", ""),
                "manifest_sha256": manifest.get("sha256", ""),
            }
        )
        if not _manifest_success(manifest):
            base["failure_class"] = "download_failed"
            failures["download_failed"] += 1
            qa_rows.append(base)
            continue
        actual_zoom = int(manifest.get("actual_zoom", 0))
        requested_zoom = int(candidate.get("requested_zoom", 0))
        zoom_ok = actual_zoom in ({19, 18} if requested_zoom == 19 else {18})
        base["zoom_status"] = "pass" if zoom_ok else "fail"
        chip_path = Path(str(manifest.get("path", "")))
        if not chip_path.is_file() or chip_path.stat().st_size == 0:
            base["failure_class"] = "missing_file"
            failures["missing_file"] += 1
            qa_rows.append(base)
            continue
        actual_hash = sha256_file(chip_path)
        hash_ok = actual_hash == str(manifest.get("sha256", ""))
        base["actual_sha256"] = actual_hash
        base["hash_status"] = "pass" if hash_ok else "fail"
        anchor = anchor_index.get(anchor_id)
        if anchor is None:
            base["failure_class"] = "missing_anchor"
            failures["missing_anchor"] += 1
            qa_rows.append(base)
            continue
        raster = inspect_raster(chip_path, anchor)
        base.update(raster)
        failure_parts = []
        if not zoom_ok:
            failure_parts.append("zoom_invalid")
        if not hash_ok:
            failure_parts.append("hash_mismatch")
        if raster.get("failure_class"):
            failure_parts.append(str(raster["failure_class"]))
        base["failure_class"] = ";".join(failure_parts)
        eligible = not failure_parts
        base["release_eligible"] = int(eligible)
        if not eligible:
            for failure in failure_parts:
                failures[failure.split(":", 1)[0]] += 1
        qa_rows.append(base)

    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "chip_qa.csv"
    write_csv_rows(report, qa_rows, QA_FIELDS)
    eligible_count = sum(int(row["release_eligible"]) for row in qa_rows)
    summary = {
        "schema_version": "ct05_chip_qa_summary_v1",
        "created_utc": utc_now(),
        "candidate_count": len(candidates),
        "qa_row_count": len(qa_rows),
        "release_eligible_count": eligible_count,
        "failure_count": len(qa_rows) - eligible_count,
        "failure_classes": dict(sorted(failures.items())),
        "report": str(report),
    }
    _write_json(out_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--candidates-csv", type=Path, required=True)
    plan.add_argument("--out-dir", type=Path, required=True)
    plan.add_argument("--routes", default=",".join(DEFAULT_ROUTES))
    plan.add_argument("--route-weights", default="", help="e.g. home_v4=2,home_v6=2,koko_v4=1; empty = uniform")
    plan.add_argument("--min-date", default=RUN3_DOWNLOAD_MIN_DATE)

    qa = sub.add_parser("qa")
    qa.add_argument("--candidates-csv", type=Path, required=True)
    qa.add_argument("--anchors-csv", type=Path, required=True)
    qa.add_argument("--manifest", type=Path, action="append", required=True)
    qa.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidates = read_csv_rows(args.candidates_csv)
    if args.command == "plan":
        routes = tuple(value.strip() for value in args.routes.split(",") if value.strip())
        route_weights = parse_route_weights(args.route_weights)
        if route_weights:
            unknown = set(route_weights) - set(routes)
            if unknown:
                raise SystemExit(f"--route-weights references unknown routes: {sorted(unknown)}")
        result = plan_downloads(
            candidates,
            routes,
            args.out_dir,
            min_date=args.min_date,
            route_weights=route_weights,
        )
    else:
        result = quality_gate(
            candidates,
            read_csv_rows(args.anchors_csv),
            args.manifest,
            args.out_dir,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
