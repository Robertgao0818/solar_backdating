#!/usr/bin/env python3
"""Reference-conditioned CoJ 2019/2023 external audit.

The scorer sees only confirmed-present semantic reference imagery plus the two
CoJ audit cells.  It never sees RUN3 interval bounds or prior model decisions.
References are selected outcome-blind:

* use the local 2024-02-29 Vexcel-derived frame when present;
* select one local 2025 frame by a frozen per-anchor hash;
* when both exist, provide both references.

Outputs are target-sharded, atomic, and resumable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
from PIL import Image, ImageDraw
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from scripts.validation.gemini_solar_image_review import (
    RateLimiter,
    _call_gemini,
    extract_json_object,
)
from scripts.validation.issue29_coj_external_robustness import (
    _cluster_bootstrap,
    atomic_json,
    client_config,
    sha256_file,
)

DATA_ROOT = Path("/home/gao/zasolar_data/geid_temporal")
SOURCE_ROOT = DATA_ROOT / "coj_run3_external_robustness_2026-07"
CHIP_ROOT = DATA_ROOT / "basemap_rebuild_2026-07-13/chips"
REFERENCE_SEED = "issue29-coj-reference-audit-20260724-v1"
EPOCHS = (2019, 2023)
SHARD_SIZE = 500

REFERENCE_PROMPT = """You are auditing one rooftop target using {count} images.
The first {reference_count} image(s) are confirmed-present semantic references
for the same target. They show what the target roof and its PV array look like.
The final two images are independent City of Johannesburg aerial audit images
from 2019 and 2023.

Use the references to locate the same roof segment and recognize its PV array,
but decide each CoJ audit image from visible pixels in that image. Do not copy
the present state into an older audit image. Different sources can have
different scale, resolution, colour, alignment, and viewing angle. Score only
the marked roof segment, not neighbouring roofs. Do not count skylights,
shadows, vents, water heaters, or dark roof paint unless a regular rectangular
PV module grid is visible.

Return ONLY one JSON object with an "observations" array of exactly {count}
objects in date_index order. Each object must contain:
{{"date_index": <int>, "pv_present": true|false|null,
  "confidence": <0.0-1.0|null>,
  "quality_flag": "usable"|"ambiguous"|"unusable",
  "evidence": "<specific visible evidence>", "notes": "<short caveat>"}}

Use pv_present=null when the marked target is too blurry, occluded, clipped,
misaligned, or otherwise uninterpretable. Also return a top-level
"review_notes" string. No prose or markdown.

Image mapping:
{mapping}
"""


def stable_hash(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()


def _capture_date(path: Path) -> str:
    match = re.search(r"_(20\d{6})_", path.name)
    if not match:
        raise ValueError(f"cannot parse capture date: {path}")
    value = match.group(1)
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def reference_candidates(anchor_id: str, chip_root: Path = CHIP_ROOT) -> tuple[list[Path], list[Path]]:
    """Return 2024-02-29 and 2025 source candidates without reading outcomes."""
    anchor_root = chip_root / anchor_id
    # A failed historical download can leave a zero-byte placeholder with a
    # valid-looking name. Exclude it before the frozen hash draw so a later
    # shard cannot die while rendering an otherwise resumable run.
    ref_2024 = sorted(
        path
        for path in (anchor_root / "z19").glob("*_20240229_*.tif")
        if path.stat().st_size > 0
    )
    ref_2025 = sorted(
        path
        for path in (anchor_root / "z19").glob("*_2025*.tif")
        if path.stat().st_size > 0
    )
    if not ref_2025:
        ref_2025 = sorted(
            path
            for path in (anchor_root / "z18").glob("*_2025*.tif")
            if path.stat().st_size > 0
        )
    return ref_2024, ref_2025


def choose_2025_reference(anchor_id: str, paths: list[Path]) -> Path | None:
    """Choose one capture date deterministically; choose a version deterministically."""
    by_date: dict[str, list[Path]] = {}
    for path in paths:
        by_date.setdefault(_capture_date(path), []).append(path)
    if not by_date:
        return None
    chosen_date = min(
        by_date,
        key=lambda value: stable_hash(REFERENCE_SEED, anchor_id, value),
    )
    return sorted(by_date[chosen_date])[0]


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def prepare(args: argparse.Namespace) -> None:
    full_targets = pd.read_parquet(SOURCE_ROOT / "full/full_targets.parquet")
    eligibility = pd.read_parquet(SOURCE_ROOT / "target_epoch_eligibility.parquet")
    target_meta = (
        eligibility[eligibility.coverage_complete]
        .sort_values(["anchor_id", "coj_epoch"])
        .drop_duplicates("anchor_id")
    )
    targets = full_targets.merge(target_meta, on="anchor_id", validate="one_to_one")
    refs: list[dict[str, Any]] = []
    counts = {"both": 0, "2024_only": 0, "2025_only": 0, "none": 0}
    for n, row in enumerate(targets.itertuples(index=False), 1):
        anchor_id = str(row.anchor_id)
        candidates_2024, candidates_2025 = reference_candidates(anchor_id)
        path_2024 = candidates_2024[0] if candidates_2024 else None
        path_2025 = choose_2025_reference(anchor_id, candidates_2025)
        if path_2024 is not None:
            refs.append({
                "anchor_id": anchor_id,
                "role": "reference_2024",
                "capture_date": "2024-02-29",
                "source_path": str(path_2024),
                "source_zoom": 19,
                "selection_hash": stable_hash(REFERENCE_SEED, anchor_id, "2024-02-29"),
            })
        if path_2025 is not None:
            refs.append({
                "anchor_id": anchor_id,
                "role": "reference_2025",
                "capture_date": _capture_date(path_2025),
                "source_path": str(path_2025),
                "source_zoom": 19 if "/z19/" in str(path_2025) else 18,
                "selection_hash": stable_hash(
                    REFERENCE_SEED, anchor_id, _capture_date(path_2025)
                ),
            })
        key = (
            "both" if path_2024 is not None and path_2025 is not None
            else "2024_only" if path_2024 is not None
            else "2025_only" if path_2025 is not None
            else "none"
        )
        counts[key] += 1
        if n % 5000 == 0:
            print(f"[prepare] references {n}/{len(targets)}", flush=True)
    references = pd.DataFrame(refs).sort_values(["anchor_id", "role"])
    if counts["none"]:
        raise RuntimeError(f"{counts['none']} targets have no semantic reference")
    targets["shard_id"] = np.arange(len(targets), dtype=np.int64) // SHARD_SIZE
    roles_by_anchor = references.groupby("anchor_id").role.agg(set).to_dict()
    targets["reference_arm"] = [
        (
            "dual_2024_2025"
            if roles_by_anchor[aid] == {"reference_2024", "reference_2025"}
            else "single_2024"
            if roles_by_anchor[aid] == {"reference_2024"}
            else "single_2025"
        )
        for aid in targets.anchor_id.astype(str)
    ]
    _atomic_parquet(targets, args.output_root / "reference_targets.parquet")
    _atomic_parquet(references, args.output_root / "reference_manifest.parquet")
    atomic_json(args.output_root / "REFERENCE_LOCK.json", {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "instrument": "confirmed_present_reference_plus_coj_pair_v1",
        "reference_seed": REFERENCE_SEED,
        "selection_reads": ["anchor_id", "capture_date", "source_path", "zoom"],
        "selection_forbidden": [
            "pv_present", "confidence", "quality_flag", "install_interval",
            "expected_state", "prior verdict",
        ],
        "counts": counts,
        "targets": int(len(targets)),
        "references": int(len(references)),
        "prompt_sha256": hashlib.sha256(REFERENCE_PROMPT.encode()).hexdigest(),
    })
    print(f"[prepare] targets={len(targets)} references={len(references)} counts={counts}", flush=True)


def render_reference(row: pd.Series, output: Path) -> tuple[Path, str]:
    output.parent.mkdir(parents=True, exist_ok=True)
    source = Path(str(row.source_path))
    source_sha256 = sha256_file(source)
    if output.exists():
        return output, source_sha256
    with rasterio.open(source) as ds:
        metric_crs = "EPSG:32735"
        to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
        to_raster = Transformer.from_crs(metric_crs, ds.crs, always_xy=True)
        cx, cy = to_metric.transform(float(row.centroid_lon), float(row.centroid_lat))
        half = float(row.review_extent_m) / 2.0
        corners = [
            to_raster.transform(cx + dx, cy + dy)
            for dx in (-half, half)
            for dy in (-half, half)
        ]
        xs, ys = zip(*corners)
        window = from_bounds(min(xs), min(ys), max(xs), max(ys), ds.transform)
        arr = ds.read(
            [1, 2, 3],
            window=window,
            out_shape=(3, 384, 384),
            resampling=Resampling.bilinear,
            boundless=True,
            fill_value=0,
        )
    image = Image.fromarray(np.moveaxis(arr, 0, 2).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(image)
    roi_edge = min(float(row.review_extent_m), math.sqrt(max(float(row.source_area_m2), 0.0)))
    radius = max(8, int(round((roi_edge / float(row.review_extent_m)) * 192)))
    yellow = (255, 225, 0)
    draw.ellipse((192-radius, 192-radius, 192+radius, 192+radius), outline=yellow, width=4)
    draw.line((182, 192, 202, 192), fill=yellow, width=3)
    draw.line((192, 182, 192, 202), fill=yellow, width=3)
    image.save(output, format="JPEG", quality=92, optimize=True)
    return output, source_sha256


def _response_schema() -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "review_notes": {"type": "STRING"},
            "observations": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "date_index": {"type": "INTEGER"},
                        "pv_present": {"type": "BOOLEAN", "nullable": True},
                        "confidence": {"type": "NUMBER", "nullable": True},
                        "quality_flag": {
                            "type": "STRING",
                            "enum": ["usable", "ambiguous", "unusable"],
                        },
                        "evidence": {"type": "STRING"},
                        "notes": {"type": "STRING"},
                    },
                    "required": [
                        "date_index", "pv_present", "confidence",
                        "quality_flag", "evidence", "notes",
                    ],
                },
            },
        },
        "required": ["review_notes", "observations"],
    }


def _parse_response(text: str, count: int) -> tuple[list[dict[str, Any]], str]:
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        raise ValueError("response is not an observations object")
    observations = payload["observations"]
    if len(observations) != count:
        raise ValueError(f"expected {count} observations, got {len(observations)}")
    by_index: dict[int, dict[str, Any]] = {}
    for obs in observations:
        if not isinstance(obs, dict):
            raise ValueError("observation is not an object")
        index = int(obs.get("date_index", -1))
        if index in by_index or index < 1 or index > count:
            raise ValueError(f"invalid/duplicate date_index={index}")
        if obs.get("pv_present") not in (True, False, None):
            raise ValueError(f"invalid pv_present at date_index={index}")
        if obs.get("quality_flag") not in {"usable", "ambiguous", "unusable"}:
            raise ValueError(f"invalid quality_flag at date_index={index}")
        confidence = obs.get("confidence")
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise ValueError(f"invalid confidence at date_index={index}")
        by_index[index] = obs
    if set(by_index) != set(range(1, count + 1)):
        raise ValueError("date_index coverage mismatch")
    return [by_index[i] for i in range(1, count + 1)], str(payload.get("review_notes", ""))


def _score_sequence(
    items: list[dict[str, Any]],
    *,
    anchor_id: str,
    config: Any,
    limiter: RateLimiter,
) -> list[dict[str, Any]]:
    mapping_lines = []
    reference_count = sum(item["role"].startswith("reference_") for item in items)
    for index, item in enumerate(items, 1):
        if item["role"] == "reference_2024":
            label = "confirmed-present reference A (2024-02-29)"
        elif item["role"] == "reference_2025":
            label = "confirmed-present reference B (random frozen 2025 frame)"
        elif item["role"] == "audit_2019":
            label = "CoJ audit image, acquisition year 2019"
        else:
            label = "CoJ audit image, acquisition year 2023"
        mapping_lines.append(f"- {index}: {label}")
    prompt = REFERENCE_PROMPT.format(
        count=len(items),
        reference_count=reference_count,
        mapping="\n".join(mapping_lines),
    )
    last_error = ""
    for attempt in (1, 2):
        try:
            text, raw = _call_gemini(
                image_paths=[Path(item["image_path"]) for item in items],
                prompt=prompt,
                config=config,
                max_tokens=650 * len(items) + 256,
                response_mime_type="application/json",
                response_schema=_response_schema(),
                routing_salt=f"issue29-refaudit-{anchor_id}-{attempt}",
                limiter=limiter,
            )
            parsed, review_notes = _parse_response(text, len(items))
            model_version = raw.get("modelVersion") if isinstance(raw, dict) else None
            return [
                {
                    **item,
                    **obs,
                    "review_notes": review_notes,
                    "decision_source": "gemini_reference_sequence",
                    "raw_model_version": model_version or config.model,
                }
                for item, obs in zip(items, parsed)
            ]
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
    return [
        {
            **item,
            "date_index": index,
            "pv_present": None,
            "confidence": None,
            "quality_flag": "unusable",
            "evidence": "",
            "notes": last_error[:500],
            "review_notes": last_error[:500],
            "decision_source": "gemini_failed",
            "raw_model_version": config.model,
        }
        for index, item in enumerate(items, 1)
    ]


def _target_items(
    target: pd.Series,
    references: pd.DataFrame,
    eligibility: pd.DataFrame,
    output_root: Path,
) -> list[dict[str, Any]]:
    anchor_id = str(target.anchor_id)
    common = {
        "anchor_id": anchor_id,
        "legacy_group_anchor_id": str(target.legacy_group_anchor_id),
        "area_bin": str(target.area_bin),
        "chip_arm": str(target.chip_arm),
        "legacy_group_multiplicity": int(target.legacy_group_multiplicity),
        "reference_arm": str(target.reference_arm),
    }
    items: list[dict[str, Any]] = []
    for _, ref in references.sort_values("role").iterrows():
        output = output_root / f"reference_crops/{ref.role}/{anchor_id}_{str(ref.capture_date).replace('-', '')}.jpg"
        render_row = target.copy()
        for column in ("source_path", "capture_date", "role", "source_zoom"):
            render_row[column] = ref[column]
        rendered, source_sha = render_reference(render_row, output)
        items.append({
            **common,
            "role": str(ref.role),
            "capture_date": str(ref.capture_date),
            "coj_epoch": None,
            "expected_state": "reference_present",
            "image_path": str(rendered),
            "source_path": str(ref.source_path),
            "source_sha256": source_sha,
            "source_zoom": int(ref.source_zoom),
        })
    for epoch in EPOCHS:
        row = eligibility[eligibility.coj_epoch.astype(int) == epoch].iloc[0]
        items.append({
            **common,
            "role": f"audit_{epoch}",
            "capture_date": f"{epoch}-year-only",
            "coj_epoch": epoch,
            "expected_state": str(row.expected_state),
            "image_path": str(SOURCE_ROOT / f"crops/{epoch}/{anchor_id}.png"),
            "source_path": str(row.coj_path),
            "source_sha256": str(row.coj_sha256),
            "source_zoom": None,
            "interval_kind": str(row.interval_kind),
            "install_interval_start": row.install_interval_start,
            "install_interval_end": row.install_interval_end,
            "grid_id": str(row.grid_id),
            "split": str(row.split),
        })
    return items


def _score_targets(
    targets: pd.DataFrame,
    references: pd.DataFrame,
    eligibility: pd.DataFrame,
    args: argparse.Namespace,
) -> pd.DataFrame:
    config = client_config(args.model)
    limiter = RateLimiter(args.qps)
    render_items: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {}
        for _, target in targets.iterrows():
            anchor_id = str(target.anchor_id)
            target_refs = references[references.anchor_id.astype(str) == anchor_id]
            target_eligibility = eligibility[eligibility.anchor_id.astype(str) == anchor_id]
            futures[pool.submit(
                _target_items, target, target_refs, target_eligibility, args.output_root
            )] = anchor_id
        for future in as_completed(futures):
            render_items[futures[future]] = future.result()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                _score_sequence,
                render_items[str(target.anchor_id)],
                anchor_id=str(target.anchor_id),
                config=config,
                limiter=limiter,
            ): str(target.anchor_id)
            for _, target in targets.iterrows()
        }
        for n, future in enumerate(as_completed(futures), 1):
            results.extend(future.result())
            if n % 100 == 0:
                print(f"[score] sequences {n}/{len(futures)}", flush=True)
    return pd.DataFrame(results)


def smoke(args: argparse.Namespace) -> None:
    targets = pd.read_parquet(args.output_root / "reference_targets.parquet")
    references = pd.read_parquet(args.output_root / "reference_manifest.parquet")
    eligibility = pd.read_parquet(SOURCE_ROOT / "target_epoch_eligibility.parquet")
    sample = (
        targets.sort_values(["reference_arm", "sampling_hash"])
        .groupby("reference_arm", group_keys=False)
        .head(1)
    )
    result = _score_targets(sample, references, eligibility, args)
    _atomic_parquet(result, args.output_root / "smoke_observations.parquet")
    if result.decision_source.eq("gemini_failed").any():
        raise RuntimeError("reference audit smoke contains failed sequence")
    expected = {
        str(row.anchor_id): 2 + int(
            (references.anchor_id.astype(str) == str(row.anchor_id)).sum()
        )
        for row in sample.itertuples()
    }
    actual = result.groupby("anchor_id").size().to_dict()
    if actual != expected:
        raise RuntimeError(f"smoke cardinality mismatch expected={expected} actual={actual}")
    print(f"[smoke] targets={len(sample)} observations={len(result)} arms={sorted(sample.reference_arm)}", flush=True)


def full(args: argparse.Namespace) -> None:
    targets = pd.read_parquet(args.output_root / "reference_targets.parquet")
    references = pd.read_parquet(args.output_root / "reference_manifest.parquet")
    eligibility = pd.read_parquet(SOURCE_ROOT / "target_epoch_eligibility.parquet")
    shards_root = args.output_root / "full/shards"
    shards_root.mkdir(parents=True, exist_ok=True)
    shard_total = targets.shard_id.nunique()
    roles_by_anchor = references.groupby("anchor_id").role.agg(list).to_dict()
    for shard_id, shard_targets in targets.groupby("shard_id", sort=True):
        shard_path = shards_root / f"shard_{int(shard_id):05d}.parquet"
        shard_ids = set(shard_targets.anchor_id.astype(str))
        expected_roles = {
            (aid, role)
            for aid in shard_ids
            for role in (
                [str(role) for role in roles_by_anchor[aid]]
                + ["audit_2019", "audit_2023"]
            )
        }
        if shard_path.exists():
            existing = pd.read_parquet(shard_path, columns=["anchor_id", "role"])
            existing_roles = set(zip(existing.anchor_id.astype(str), existing.role.astype(str)))
            if existing_roles != expected_roles:
                raise RuntimeError(f"existing shard key mismatch: {shard_path}")
            print(f"[full] skip shard {int(shard_id)+1}/{shard_total}", flush=True)
            continue
        result = _score_targets(shard_targets, references, eligibility, args)
        actual_roles = set(zip(result.anchor_id.astype(str), result.role.astype(str)))
        if actual_roles != expected_roles:
            raise RuntimeError(f"new shard key mismatch: {shard_path}")
        _atomic_parquet(
            result.sort_values(["anchor_id", "date_index"]),
            shard_path,
        )
        print(f"[full] committed shard {int(shard_id)+1}/{shard_total}", flush=True)
    paths = sorted(shards_root.glob("shard_*.parquet"))
    if len(paths) != shard_total:
        raise RuntimeError(f"expected {shard_total} shards, found {len(paths)}")
    combined = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    _atomic_parquet(
        combined.sort_values(["anchor_id", "date_index"]),
        args.output_root / "full/full_sequence_observations.parquet",
    )
    atomic_json(args.output_root / "full/FULL_RUN.json", {
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "instrument": "confirmed_present_reference_plus_coj_pair_v1",
        "model": args.model,
        "workers": args.workers,
        "qps": args.qps,
        "targets": int(combined.anchor_id.nunique()),
        "observations": int(len(combined)),
        "shards": int(shard_total),
    })


def report(args: argparse.Namespace) -> None:
    full_root = args.output_root / "full"
    obs = pd.read_parquet(full_root / "full_sequence_observations.parquet")
    refs = obs[obs.role.str.startswith("reference_")].copy()
    audits = obs[obs.role.str.startswith("audit_")].copy()
    refs["reference_pass"] = (
        (refs.quality_flag == "usable")
        & (refs.pv_present == True)  # noqa: E712
        & (refs.confidence.fillna(0) >= 0.80)
    )
    gate = refs.groupby("anchor_id").reference_pass.any().rename("reference_gate_pass")
    audits = audits.merge(gate, on="anchor_id", validate="many_to_one")
    audits["automated_verdict"] = np.where(
        (audits.quality_flag == "usable")
        & audits.pv_present.notna()
        & (audits.confidence.fillna(0) >= 0.80),
        np.where(audits.pv_present == True, "present", "absent"),  # noqa: E712
        "uninformative",
    )
    primary = audits[audits.reference_gate_pass]
    all_metric = _cluster_bootstrap(audits)
    primary_metric = _cluster_bootstrap(primary)
    _atomic_parquet(audits, full_root / "full_coj_observations.parquet")
    ref_summary = (
        refs.groupby(["reference_arm", "role"])
        .agg(
            n=("anchor_id", "size"),
            pass_n=("reference_pass", "sum"),
            uninformative=("quality_flag", lambda x: int((x != "usable").sum())),
        )
        .reset_index()
    )
    ref_summary["pass_rate"] = ref_summary.pass_n / ref_summary.n
    ref_summary.to_csv(full_root / "reference_gate.csv", index=False)
    atomic_json(full_root / "FULL_VERDICT.json", {
        "status": "REFERENCE_CONDITIONED_AUDIT_PENDING_HUMAN_GATE",
        "reason": "Automated external audit is supplemental until the blind-human instrument gate is complete.",
        "targets": int(audits.anchor_id.nunique()),
        "audit_observations": int(len(audits)),
        "reference_observations": int(len(refs)),
        "reference_gate_pass_targets": int(gate.sum()),
        "reference_gate_failed_targets": int((~gate).sum()),
        "all_audit_agreement": all_metric,
        "primary_reference_gate_agreement": primary_metric,
        "uninformative_audit_observations": int(
            (audits.automated_verdict == "uninformative").sum()
        ),
    })
    print(
        f"[report] reference_gate={int(gate.sum())}/{len(gate)} "
        f"primary_agreement={primary_metric.get('agreement')}",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "smoke", "full", "report"])
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--qps", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_root = args.output_root.expanduser().resolve()
    args.output_root.mkdir(parents=True, exist_ok=True)
    globals()[args.stage](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
