#!/usr/bin/env python3
"""ISSUE-29 CoJ 2019/2023 fixed-epoch external robustness runner.

The automated stages are resumable and deliberately stop short of a RUN3
partial-verdict label until blind-human adjudication has passed the frozen
instrument gate.  Gemini observations are never represented as human truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import rasterio
from PIL import Image, ImageDraw
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from scripts.validation.gemini_solar_image_review import (
    DEFAULT_ENV_FILE,
    GeminiClientConfig,
    RateLimiter,
    SequenceDatePick,
    _call_gemini,
    env_value,
    extract_json_object,
    load_env_file,
    parse_jsonl_lenient,
    score_single_target_sequence,
    validate_observation_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path("/home/gao/zasolar_data/geid_temporal")
COJ_ROOT = DATA_ROOT / "coj_prefetch_allgroups_2026-07-12"
FULLSCAN_ROOT = DATA_ROOT / "fullscan_gemini_backdating_2026-07"
RUN3_INTERVALS = FULLSCAN_ROOT / "run3_v2/intervals/install_intervals_all.csv"
ANCHORS_V2 = FULLSCAN_ROOT / "anchors_v2/anchors_all.csv"
R0_ROOT = DATA_ROOT / "run3_native_line_2026-07/r0_manifest_v1"
R0_MANIFEST = R0_ROOT / "manifest.parquet"
R0_SPLITS = R0_ROOT / "splits.parquet"
R0_LOCK = R0_ROOT / "MANIFEST_LOCK.json"
EPOCHS = (2019, 2023)
SEED = 2026072901
METRIC_CRS = "EPSG:32735"

BATCH_PROMPT = """You are reviewing N={count} independent high-resolution City of Johannesburg aerial-image crops for rooftop solar PV. Each crop is target-specific: a yellow ring and cross mark the only roof footprint to score. Images are independent and presented in index order 1..N.

Return ONLY JSONL, exactly N lines, with one object per image:
{{"chip_index": <int>, "pv_present": true|false|null, "confidence": <0.0-1.0>, "quality_flag": "usable"|"ambiguous"|"unusable", "evidence": "short specific visual evidence", "notes": "short caveat"}}

Count PV only when a regular rectangular photovoltaic module grid is visible within the yellow ring or immediately adjacent on the same roof plane. Do not count skylights, vents, HVAC, water-heater tanks/tubes, shadows, dark roof paint, or PV on a neighbouring roof. Use null for blur, occlusion, corrupt/blank imagery, or when the marked target cannot be interpreted. Do not transfer a decision between images. Output no prose or markdown.
"""


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def stable_hash(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()


def parse_day(value: Any) -> date | None:
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def expected_state_for_year(status: str, lower: Any, upper: Any, year: int) -> str:
    """Conservative RUN3 state against the full acquisition-year interval."""
    d0, d1 = date(year, 1, 1), date(year, 12, 31)
    lo, hi = parse_day(lower), parse_day(upper)
    if status == "done_already_present_before_geid_history":
        return "present" if hi is not None and d0 > hi else "non_identifying"
    if status not in {"done_appears", "done_installed_during_census"}:
        return "non_identifying"
    if lo is None or hi is None or lo >= hi:
        return "non_identifying"
    if d1 <= lo:
        return "absent"
    if d0 > hi:
        return "present"
    return "non_identifying"


def area_bin(area: float) -> str:
    if area < 15:
        return "<15"
    if area < 40:
        return "15-40"
    if area < 100:
        return "40-100"
    return ">=100"


def client_config(model: str) -> GeminiClientConfig:
    values = load_env_file(DEFAULT_ENV_FILE)
    base_url = env_value(values, "GOOGLE_GEMINI_BASE_URL")
    api_key = env_value(values, "GEMINI_API_KEY")
    api_format = env_value(values, "GEMINI_API_FORMAT", "native")
    native_path = env_value(values, "GEMINI_NATIVE_PATH", "/v1beta")
    if api_format != "agy" and (not base_url or not api_key):
        raise RuntimeError("Sub2API URL/key missing from environment or .env.gemini.local")
    return GeminiClientConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        api_format=api_format,
        native_path=native_path,
        timeout=180,
        max_tokens_per_chip=500,
    )


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def preflight(args: argparse.Namespace) -> None:
    config = client_config(args.model)
    source_chip = next((COJ_ROOT / "chips/2019").glob("*.tif"))
    chip = args.output_root / "qa/preflight.png"
    chip.parent.mkdir(parents=True, exist_ok=True)
    if not chip.exists():
        with rasterio.open(source_chip) as ds:
            arr = ds.read([1, 2, 3], out_shape=(3, 384, 384), resampling=Resampling.bilinear)
        Image.fromarray(np.moveaxis(arr, 0, 2).astype(np.uint8), "RGB").save(chip, optimize=True)
    text, raw = _call_gemini(
        image_paths=[chip],
        prompt=BATCH_PROMPT.format(count=1),
        config=config,
        max_tokens=600,
        routing_salt="issue29-preflight",
        limiter=RateLimiter(1),
    )
    parsed, missing = parse_jsonl_lenient(text, 1)
    if missing or len(parsed) != 1 or not validate_observation_schema(parsed[0]):
        raise RuntimeError(f"authenticated Gemini preflight returned invalid schema: {text[:300]}")
    atomic_json(args.output_root / "qa/preflight.json", {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "api_format": config.api_format,
        "model_version": raw.get("modelVersion") if isinstance(raw, dict) else None,
        "schema_valid": True,
    })


def _hash_tiffs(paths: list[Path], workers: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(sha256_file, p): p for p in paths}
        for i, fut in enumerate(as_completed(futures), 1):
            p = futures[fut]
            out.append({"path": str(p), "sha256": fut.result(), "size": p.stat().st_size})
            if i % 2000 == 0:
                print(f"[prepare] hashed {i}/{len(paths)} CoJ TIFFs", flush=True)
    return sorted(out, key=lambda x: x["path"])


def _repo_state() -> dict[str, Any]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True).strip())
    return {"commit": commit, "dirty": dirty}


def _geometry_coverage(row: pd.Series, chip_path: Path) -> tuple[bool, str]:
    if not chip_path.exists() or chip_path.stat().st_size == 0:
        return False, "missing_or_empty_tiff"
    try:
        with rasterio.open(chip_path) as ds:
            to_metric = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)
            to_raster = Transformer.from_crs(METRIC_CRS, ds.crs, always_xy=True)
            cx, cy = to_metric.transform(float(row.centroid_lon), float(row.centroid_lat))
            half = float(row.review_extent_m) / 2.0
            corners = [to_raster.transform(cx + dx, cy + dy) for dx in (-half, half) for dy in (-half, half)]
            xs, ys = zip(*corners)
            eps = max(abs(ds.transform.a), abs(ds.transform.e)) * 0.51
            if min(xs) < ds.bounds.left - eps or max(xs) > ds.bounds.right + eps:
                return False, "context_outside_x"
            if min(ys) < ds.bounds.bottom - eps or max(ys) > ds.bounds.top + eps:
                return False, "context_outside_y"
            if ds.count < 3 or ds.width <= 0 or ds.height <= 0:
                return False, "invalid_raster_shape"
    except Exception as exc:  # noqa: BLE001
        return False, f"raster_open_error:{type(exc).__name__}"
    return True, "complete"


def _round_robin_sample(rows: pd.DataFrame, n_targets: int, *, seed: int = SEED) -> list[str]:
    candidates = rows[(rows.coverage_complete) & (rows.expected_state != "non_identifying")].copy()
    candidates["stratum"] = candidates.epoch.astype(str) + "|" + candidates.expected_state + "|" + candidates.area_bin + "|" + candidates.interval_kind
    candidates["h"] = [stable_hash(seed, a, e) for a, e in zip(candidates.anchor_id, candidates.epoch)]
    buckets = {k: g.sort_values("h") for k, g in candidates.groupby("stratum")}
    selected: list[str] = []
    seen: set[str] = set()
    positions = {k: 0 for k in buckets}
    while len(selected) < n_targets:
        progressed = False
        for key in sorted(buckets):
            g = buckets[key]
            pos = positions[key]
            while pos < len(g) and str(g.iloc[pos].anchor_id) in seen:
                pos += 1
            positions[key] = pos + 1
            if pos < len(g):
                aid = str(g.iloc[pos].anchor_id)
                selected.append(aid)
                seen.add(aid)
                progressed = True
                if len(selected) >= n_targets:
                    break
        if not progressed:
            break
    return selected


def prepare(args: argparse.Namespace) -> None:
    out = args.output_root
    for d in ("locks", "samples", "crops/2019", "crops/2023", "metrics", "qa"):
        (out / d).mkdir(parents=True, exist_ok=True)
    tiffs = sorted((COJ_ROOT / "chips").glob("*/*.tif"))
    if len(tiffs) != 31718:
        raise RuntimeError(f"expected 31,718 CoJ TIFFs, found {len(tiffs)}")
    tiff_hashes = _hash_tiffs(tiffs, args.workers)
    fixed = [
        COJ_ROOT / "cohort_units.csv",
        COJ_ROOT / "fetch_stats.jsonl",
        COJ_ROOT / "fetch_failures.csv",
        RUN3_INTERVALS,
        ANCHORS_V2,
        R0_MANIFEST,
        R0_SPLITS,
        R0_LOCK,
    ]
    lock = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "coj_acquisition_precision": "year_interval",
        "coj_acquisition_intervals": {str(y): [f"{y}-01-01", f"{y}-12-31"] for y in EPOCHS},
        "repo": _repo_state(),
        "fixed_inputs": [{"path": str(p), "sha256": sha256_file(p), "size": p.stat().st_size} for p in fixed],
        "coj_tiffs": tiff_hashes,
    }
    atomic_json(out / "locks/COJ_INPUT_LOCK.json", lock)
    tiff_sha_by_path = {x["path"]: x["sha256"] for x in tiff_hashes}

    anchors = pd.read_csv(ANCHORS_V2)
    intervals = pd.read_csv(RUN3_INTERVALS)
    splits = pd.read_parquet(R0_SPLITS)
    if len(anchors) != 41393 or anchors.anchor_id.nunique() != 41393:
        raise RuntimeError("anchors_v2 row/bijection mismatch")
    if anchors.legacy_group_anchor_id.nunique() != 15859:
        raise RuntimeError("legacy group count mismatch")
    merged = anchors.merge(intervals[["anchor_id", "status", "install_interval_start", "install_interval_end"]], on="anchor_id", validate="one_to_one")
    merged = merged.merge(splits, on="anchor_id", validate="one_to_one")
    multiplicity = merged.groupby("legacy_group_anchor_id").anchor_id.transform("size")
    rows: list[dict[str, Any]] = []
    for i, row in merged.iterrows():
        if i and i % 5000 == 0:
            print(f"[prepare] eligibility anchors {i}/{len(merged)}", flush=True)
        for epoch in EPOCHS:
            chip = COJ_ROOT / f"chips/{epoch}/{row.legacy_group_anchor_id}.tif"
            complete, reason = _geometry_coverage(row, chip)
            rows.append({
                "anchor_id": row.anchor_id,
                "legacy_group_anchor_id": row.legacy_group_anchor_id,
                "coj_epoch": epoch,
                "coj_path": str(chip),
                "coj_sha256": tiff_sha_by_path[str(chip)],
                "target_geometry_source": str(ANCHORS_V2),
                "target_geometry_hash": stable_hash(row.centroid_lon, row.centroid_lat, row.source_area_m2, row.review_extent_m, row.geometry_version),
                "centroid_lon": row.centroid_lon,
                "centroid_lat": row.centroid_lat,
                "source_area_m2": row.source_area_m2,
                "area_bin": area_bin(float(row.source_area_m2)),
                "chip_arm": row.chip_arm,
                "review_extent_m": row.review_extent_m,
                "geometry_version": row.geometry_version,
                "coverage_complete": complete,
                "coverage_reason": reason,
                "interval_kind": row.status,
                "install_interval_start": row.install_interval_start,
                "install_interval_end": row.install_interval_end,
                "expected_state": expected_state_for_year(row.status, row.install_interval_start, row.install_interval_end, epoch),
                "grid_id": row.grid_id,
                "split": row.split,
                "legacy_group_multiplicity": int(multiplicity.iloc[i]),
                "sampling_hash": stable_hash(SEED, row.anchor_id, epoch),
            })
    eligibility = pd.DataFrame(rows)
    eligibility.to_parquet(out / "target_epoch_eligibility.parquet", index=False)
    pilot_ids = _round_robin_sample(eligibility.rename(columns={"coj_epoch": "epoch"}), 800)
    smoke_ids = _round_robin_sample(eligibility.rename(columns={"coj_epoch": "epoch"}), 40)
    eligibility[eligibility.anchor_id.isin(pilot_ids)].drop_duplicates("anchor_id").to_parquet(out / "samples/pilot_targets.parquet", index=False)
    eligibility[eligibility.anchor_id.isin(smoke_ids)].drop_duplicates("anchor_id").to_parquet(out / "samples/smoke_targets.parquet", index=False)
    scorer_config = {
        "model": args.model,
        "provider": "sub2api",
        "temperature": 0,
        "vocabulary": ["present", "absent", "uninformative"],
        "informative_rule": "usable and pv_present is boolean and confidence>=0.80",
        "prompt_sha256": hashlib.sha256(BATCH_PROMPT.encode()).hexdigest(),
        "batch_size": 8,
        "seed": SEED,
    }
    atomic_json(out / "locks/scorer_config.json", scorer_config)


def render_crop(row: pd.Series, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        return output
    with rasterio.open(row.coj_path) as ds:
        to_metric = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)
        to_raster = Transformer.from_crs(METRIC_CRS, ds.crs, always_xy=True)
        cx, cy = to_metric.transform(float(row.centroid_lon), float(row.centroid_lat))
        half = float(row.review_extent_m) / 2.0
        corners = [to_raster.transform(cx + dx, cy + dy) for dx in (-half, half) for dy in (-half, half)]
        xs, ys = zip(*corners)
        window = from_bounds(min(xs), min(ys), max(xs), max(ys), ds.transform)
        arr = ds.read([1, 2, 3], window=window, out_shape=(3, 384, 384), resampling=Resampling.bilinear)
    image = Image.fromarray(np.moveaxis(arr, 0, 2).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(image)
    roi_edge = min(float(row.review_extent_m), math.sqrt(max(float(row.source_area_m2), 0.0)))
    radius = max(8, int(round((roi_edge / float(row.review_extent_m)) * 384 / 2)))
    center = 192
    yellow = (255, 225, 0)
    draw.ellipse((center-radius, center-radius, center+radius, center+radius), outline=yellow, width=4)
    draw.line((center-10, center, center+10, center), fill=yellow, width=3)
    draw.line((center, center-10, center, center+10), fill=yellow, width=3)
    image.save(output, optimize=True)
    return output


def _score_batch(rows: pd.DataFrame, config: GeminiClientConfig, limiter: RateLimiter, salt: str) -> list[dict[str, Any]]:
    paths = [Path(p) for p in rows.crop_path]
    prompt = BATCH_PROMPT.format(count=len(paths))
    last_error = ""
    for attempt in (1, 2):
        try:
            text, raw = _call_gemini(image_paths=paths, prompt=prompt, config=config, max_tokens=500*len(paths)+256, routing_salt=f"{salt}-{attempt}", limiter=limiter)
            parsed, missing = parse_jsonl_lenient(text, len(paths))
            valid = {int(x["chip_index"]): x for x in parsed if validate_observation_schema(x)}
            if not missing and len(valid) == len(paths):
                return [{**rows.iloc[i-1].to_dict(), **valid[i], "decision_source": "gemini_batch", "raw_model_version": raw.get("modelVersion") if isinstance(raw, dict) else None} for i in range(1, len(paths)+1)]
            last_error = f"schema incomplete missing={missing}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
    return [{**r.to_dict(), "pv_present": None, "confidence": None, "quality_flag": "unusable", "evidence": "", "notes": last_error[:500], "decision_source": "gemini_failed", "raw_model_version": None} for _, r in rows.iterrows()]


def _select_context_rows(r0_rows: pd.DataFrame) -> pd.DataFrame:
    """Outcome-blind earliest/median/latest R0 images for one target."""
    usable = r0_rows[["capture_date", "chip_png_path"]].dropna().copy()
    usable = usable[usable.chip_png_path.map(lambda p: Path(str(p)).exists())]
    usable = usable.sort_values(["capture_date", "chip_png_path"]).drop_duplicates("capture_date")
    if len(usable) < 3:
        return usable.iloc[0:0]
    picks = sorted({0, len(usable) // 2, len(usable) - 1})
    if len(picks) != 3:
        return usable.iloc[0:0]
    return usable.iloc[picks].reset_index(drop=True)


def _sequence_sort_key(label: str) -> tuple[int, int, str]:
    if label.startswith("CoJ-"):
        return int(label[4:8]), 1, label
    return int(label[:4]), 0, label


def _score_target_sequence(
    target_rows: pd.DataFrame,
    context_rows: pd.DataFrame,
    config: GeminiClientConfig,
    limiter: RateLimiter,
) -> list[dict[str, Any]]:
    """Score exactly five same-target images; return only the two CoJ cells."""
    if len(target_rows) != 2 or len(context_rows) != 3:
        return [
            {
                **row.to_dict(),
                "pv_present": None,
                "confidence": None,
                "quality_flag": "unusable",
                "evidence": "",
                "notes": "five_sequence_ineligible: requires two CoJ and three distinct R0 context dates",
                "decision_source": "sequence_ineligible",
                "raw_model_version": None,
                "sequence_context_dates": "",
            }
            for _, row in target_rows.iterrows()
        ]

    items: list[tuple[str, Path, str]] = []
    for _, row in context_rows.iterrows():
        label = str(row.capture_date)[:10]
        items.append((label, Path(str(row.chip_png_path)), "r0_context"))
    for _, row in target_rows.iterrows():
        label = f"CoJ-{int(row.coj_epoch)}-year-only"
        items.append((label, Path(str(row.crop_path)), "coj"))
    items.sort(key=lambda item: _sequence_sort_key(item[0]))
    picks = [
        SequenceDatePick(date_index=i, chip_path=path, capture_date=label)
        for i, (label, path, _kind) in enumerate(items, 1)
    ]
    result = score_single_target_sequence(
        picks,
        config=config,
        max_tokens=2200,
        routing_salt=f"issue29-5seq-{target_rows.iloc[0].anchor_id}",
        limiter=limiter,
    )
    observation_by_label = {
        pick.capture_date: obs for pick, obs in zip(picks, result.observations)
    }
    context_dates = ";".join(str(x.capture_date)[:10] for _, x in context_rows.iterrows())
    output: list[dict[str, Any]] = []
    for _, row in target_rows.iterrows():
        label = f"CoJ-{int(row.coj_epoch)}-year-only"
        obs = observation_by_label.get(label)
        if obs is None:
            output.append({
                **row.to_dict(),
                "pv_present": None,
                "confidence": None,
                "quality_flag": "unusable",
                "evidence": "",
                "notes": "missing CoJ cell in five-image sequence response",
                "decision_source": "gemini_failed",
                "raw_model_version": None,
                "sequence_context_dates": context_dates,
            })
            continue
        if obs.pv_score is None:
            confidence = result.confidence
        elif obs.pv_present is True:
            confidence = float(obs.pv_score)
        elif obs.pv_present is False:
            confidence = 1.0 - float(obs.pv_score)
        else:
            confidence = None
        output.append({
            **row.to_dict(),
            "pv_present": obs.pv_present,
            "confidence": confidence,
            "quality_flag": result.quality_flag,
            "evidence": obs.evidence,
            "notes": obs.notes or result.review_notes,
            "decision_source": result.decision_source,
            "raw_model_version": config.model,
            "sequence_context_dates": context_dates,
        })
    return output


def _score_scope(args: argparse.Namespace, scope: str) -> None:
    atomic_json(args.output_root / "locks/scorer_config.json", {
        "model": args.model,
        "provider": "sub2api",
        "temperature": 0,
        "instrument": "same_target_five_image_sequence_v1",
        "sequence_members": [
            "R0 earliest distinct capture date",
            "R0 temporal-median distinct capture date",
            "R0 latest distinct capture date",
            "CoJ 2019 year-only",
            "CoJ 2023 year-only",
        ],
        "context_selection_reads": ["anchor_id", "capture_date", "chip_png_path"],
        "context_selection_forbidden": ["RUN3 interval", "expected_state", "prior Gemini verdict"],
        "metric_cells": ["CoJ-2019-year-only", "CoJ-2023-year-only"],
        "vocabulary": ["present", "absent", "uninformative"],
        "informative_rule": "sequence usable and CoJ pv_present is boolean and derived confidence>=0.80",
        "seed": SEED,
    })
    eligibility = pd.read_parquet(args.output_root / "target_epoch_eligibility.parquet")
    sample = pd.read_parquet(args.output_root / f"samples/{scope}_targets.parquet")
    rows = eligibility[(eligibility.anchor_id.isin(sample.anchor_id)) & eligibility.coverage_complete].copy()
    rows["crop_path"] = [str(args.output_root / f"crops/{e}/{a}.png") for a, e in zip(rows.anchor_id, rows.coj_epoch)]
    for i, row in rows.iterrows():
        render_crop(row, Path(row.crop_path))
    output = args.output_root / (
        "observations.parquet" if scope == "pilot" else f"{scope}_observations.parquet"
    )
    done_keys: set[tuple[str, int]] = set()
    existing = None
    if output.exists():
        existing = pd.read_parquet(output)
        done_keys = set(zip(existing.anchor_id.astype(str), existing.coj_epoch.astype(int)))
    rows = rows[[ (str(a), int(e)) not in done_keys for a, e in zip(rows.anchor_id, rows.coj_epoch) ]]
    if rows.empty:
        return
    target_ids = sorted(rows.anchor_id.unique())
    r0 = pd.read_parquet(
        R0_MANIFEST,
        columns=["anchor_id", "capture_date", "chip_png_path"],
        filters=[("anchor_id", "in", target_ids)],
    )
    contexts = {aid: _select_context_rows(g) for aid, g in r0.groupby("anchor_id")}
    config = client_config(args.model)
    limiter = RateLimiter(args.qps)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                _score_target_sequence,
                rows[rows.anchor_id == aid].sort_values("coj_epoch").reset_index(drop=True),
                contexts.get(aid, pd.DataFrame()),
                config,
                limiter,
            ): aid
            for aid in target_ids
        }
        for n, fut in enumerate(as_completed(futures), 1):
            results.extend(fut.result())
            if n % 10 == 0:
                print(f"[{scope}] scored five-image target sequences {n}/{len(futures)}", flush=True)
    new = pd.DataFrame(results)
    combined = pd.concat([existing, new], ignore_index=True) if existing is not None else new
    combined.sort_values(["anchor_id", "coj_epoch"]).to_parquet(output, index=False)
    if scope.startswith("smoke"):
        html = ["<html><body><h1>ISSUE-29 blinded geometry smoke</h1>"]
        for _, r in rows.iterrows():
            rel = Path(r.crop_path).relative_to(args.output_root)
            html.append(f'<figure><img src="../{rel}" width="384"><figcaption>{r.anchor_id} / CoJ {r.coj_epoch}</figcaption></figure>')
        html.append("</body></html>")
        sheet_name = "blind_sheet.html" if scope == "smoke" else f"blind_sheet_{scope}.html"
        (args.output_root / f"qa/{sheet_name}").write_text("\n".join(html), encoding="utf-8")


def smoke(args: argparse.Namespace) -> None:
    _score_scope(args, "smoke")


def smoke2(args: argparse.Namespace) -> None:
    """Independent post-pilot sensitivity smoke; frozen instrument, no tuning."""
    eligibility = pd.read_parquet(args.output_root / "target_epoch_eligibility.parquet")
    excluded: set[str] = set()
    for name in ("smoke_targets.parquet", "pilot_targets.parquet"):
        path = args.output_root / f"samples/{name}"
        if path.exists():
            excluded.update(pd.read_parquet(path).anchor_id.astype(str))
    pool = eligibility[~eligibility.anchor_id.astype(str).isin(excluded)].rename(columns={"coj_epoch": "epoch"})
    ids = _round_robin_sample(pool, 40, seed=2026072302)
    if len(ids) != 40:
        raise RuntimeError(f"smoke2 expected 40 targets, sampled {len(ids)}")
    eligibility[eligibility.anchor_id.isin(ids)].drop_duplicates("anchor_id").to_parquet(
        args.output_root / "samples/smoke2_targets.parquet", index=False
    )
    _score_scope(args, "smoke2")


def pilot(args: argparse.Namespace) -> None:
    _score_scope(args, "pilot")
    obs = pd.read_parquet(args.output_root / "observations.parquet")
    obs["automated_verdict"] = np.where(
        (obs.quality_flag == "usable") & obs.pv_present.notna() & (obs.confidence.fillna(0) >= 0.80),
        np.where(obs.pv_present == True, "present", "absent"),  # noqa: E712
        "uninformative",
    )
    obs["validation_hash"] = [stable_hash(SEED, "blind", a, e) for a, e in zip(obs.anchor_id, obs.coj_epoch)]
    panel = obs.sort_values(["automated_verdict", "validation_hash"]).groupby("automated_verdict", group_keys=False).head(80)
    if len(panel) < 200:
        extra = obs[~obs.index.isin(panel.index)].sort_values("validation_hash").head(200-len(panel))
        panel = pd.concat([panel, extra])
    panel[["anchor_id", "legacy_group_anchor_id", "coj_epoch", "crop_path", "automated_verdict", "validation_hash"]].head(200).to_parquet(args.output_root / "samples/blind_validation.parquet", index=False)
    human = args.output_root / "human_adjudication.csv"
    if not human.exists():
        pd.DataFrame(columns=["anchor_id", "coj_epoch", "human_verdict", "adjudicator", "notes"]).to_csv(human, index=False)


def _cluster_bootstrap(df: pd.DataFrame, reps: int = 2000) -> dict[str, float | int | None]:
    informative = df[df.automated_verdict.isin(["present", "absent"]) & df.expected_state.isin(["present", "absent"])]
    if informative.empty:
        return {"n": 0, "agreement": None, "ci_low": None, "ci_high": None}
    grouped = (
        informative.assign(correct=informative.automated_verdict == informative.expected_state)
        .groupby("legacy_group_anchor_id", sort=True)
        .agg(n=("correct", "size"), correct=("correct", "sum"))
    )
    counts = grouped["n"].to_numpy(dtype=np.int64)
    correct = grouped["correct"].to_numpy(dtype=np.int64)
    n_groups = len(grouped)
    rng = np.random.default_rng(SEED)
    value_chunks: list[np.ndarray] = []
    for start in range(0, reps, 100):
        chunk_reps = min(100, reps - start)
        sampled_indices = rng.integers(0, n_groups, size=(chunk_reps, n_groups))
        sampled_n = counts[sampled_indices].sum(axis=1)
        sampled_correct = correct[sampled_indices].sum(axis=1)
        value_chunks.append(sampled_correct / sampled_n)
    values = np.concatenate(value_chunks)
    point = float(correct.sum() / counts.sum())
    return {
        "n": int(counts.sum()),
        "agreement": point,
        "ci_low": float(np.quantile(values, .025)),
        "ci_high": float(np.quantile(values, .975)),
    }


def report(args: argparse.Namespace) -> None:
    eligibility = pd.read_parquet(args.output_root / "target_epoch_eligibility.parquet")
    obs = pd.read_parquet(args.output_root / "observations.parquet")
    if "automated_verdict" not in obs:
        obs["automated_verdict"] = np.where((obs.quality_flag == "usable") & obs.pv_present.notna() & (obs.confidence.fillna(0) >= .80), np.where(obs.pv_present == True, "present", "absent"), "uninformative")  # noqa: E712
    metric_columns = ["expected_state", "area_bin", "interval_kind", "grid_id", "chip_arm", "legacy_group_multiplicity"]
    if all(col in obs.columns for col in metric_columns):
        joined = obs.copy()
    else:
        joined = obs.merge(
            eligibility[["anchor_id", "coj_epoch", *metric_columns]],
            on=["anchor_id", "coj_epoch"],
            validate="one_to_one",
        )
    rows = []
    for level, col in [("overall", None), ("epoch", "coj_epoch"), ("expected", "expected_state"), ("area", "area_bin"), ("arm", "chip_arm"), ("multiplicity", "legacy_group_multiplicity")]:
        groups: Iterable[tuple[Any, pd.DataFrame]] = [("all", joined)] if col is None else joined.groupby(col)
        for value, frame in groups:
            metric = _cluster_bootstrap(frame)
            rows.append({"level": level, "stratum": str(value), **metric, "non_identifying": int((frame.expected_state == "non_identifying").sum()), "uninformative": int((frame.automated_verdict == "uninformative").sum())})
    pd.DataFrame(rows).to_csv(args.output_root / "metrics/strata.csv", index=False)
    overall = _cluster_bootstrap(joined)
    human_path = args.output_root / "human_adjudication.csv"
    human = pd.read_csv(human_path) if human_path.exists() else pd.DataFrame()
    human_complete = len(human) >= 200 and set(human.get("human_verdict", pd.Series(dtype=str)).dropna().unique()) <= {"present", "absent", "uninformative"}
    verdict = {
        "status": "PENDING_HUMAN_GATE" if not human_complete else "HUMAN_GATE_REQUIRES_EVALUATION",
        "partial_verdict": None,
        "reason": "Gemini automated scoring completed, but the preregistered >=200 blind-human instrument gate is not complete.",
        "provisional_automated_agreement": overall,
        "geometry_eligible_rows": int(eligibility.coverage_complete.sum()),
        "geometry_failed_rows": int((~eligibility.coverage_complete).sum()),
        "pilot_observations": int(len(joined)),
        "uninformative": int((joined.automated_verdict == "uninformative").sum()),
    }
    atomic_json(args.output_root / "metrics/bootstrap.json", {"seed": SEED, "reps": 2000, "overall": overall})
    atomic_json(args.output_root / "metrics/VERDICT.json", verdict)
    lines = [
        "# DATA — ISSUE-29 CoJ RUN3 external robustness automated stage",
        "",
        f"Status: **{verdict['status']}**",
        "",
        "The fixed 2019/2023 CoJ imagery was scored target-by-target with Gemini via Sub2API. This is an automated observation channel, not human ground truth. No partial robustness verdict is issued before the preregistered blind-human instrument gate.",
        "",
        f"- Pilot observations: {len(joined)}",
        f"- Geometry eligible/full rows: {verdict['geometry_eligible_rows']}/{len(eligibility)}",
        f"- Uninformative automated observations: {verdict['uninformative']}",
        f"- Provisional informative agreement: {overall.get('agreement')}",
        f"- Clustered 95% CI: [{overall.get('ci_low')}, {overall.get('ci_high')}]",
        "",
        "Next required action: complete `human_adjudication.csv` from the frozen blinded panel, then evaluate balanced accuracy and class recalls before any Stage-C label.",
    ]
    (args.output_root / "DATA-coj-run3-external-robustness-automated.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    artifact_files = sorted(p for p in args.output_root.rglob("*") if p.is_file() and p.name != "artifacts.sha256")
    (args.output_root / "artifacts.sha256").write_text("".join(f"{sha256_file(p)}  {p.relative_to(args.output_root)}\n" for p in artifact_files), encoding="utf-8")


def full(args: argparse.Namespace) -> None:
    """Stage-D full expansion with target-sharded, atomic, resumable output."""
    root = args.output_root
    full_root = root / "full"
    shards_root = full_root / "shards"
    shards_root.mkdir(parents=True, exist_ok=True)
    eligibility = pd.read_parquet(root / "target_epoch_eligibility.parquet")
    complete_counts = eligibility[eligibility.coverage_complete].groupby("anchor_id").coj_epoch.nunique()
    target_ids = sorted(complete_counts[complete_counts == 2].index.astype(str))
    manifest = pd.DataFrame({
        "anchor_id": target_ids,
        "full_order": np.arange(len(target_ids), dtype=np.int64),
        "shard_id": np.arange(len(target_ids), dtype=np.int64) // 500,
    })
    manifest.to_parquet(full_root / "full_targets.parquet", index=False)
    print(f"[full] eligible targets={len(target_ids)} shards={manifest.shard_id.nunique()}", flush=True)

    r0 = pd.read_parquet(R0_MANIFEST, columns=["anchor_id", "capture_date", "chip_png_path"])
    r0 = r0[r0.anchor_id.astype(str).isin(set(target_ids))]
    contexts = {str(aid): _select_context_rows(g) for aid, g in r0.groupby("anchor_id")}
    config = client_config(args.model)
    limiter = RateLimiter(args.qps)

    for shard_id, shard_manifest in manifest.groupby("shard_id", sort=True):
        shard_path = shards_root / f"shard_{int(shard_id):05d}.parquet"
        shard_ids = shard_manifest.anchor_id.astype(str).tolist()
        expected_keys = {(aid, epoch) for aid in shard_ids for epoch in EPOCHS}
        if shard_path.exists():
            existing = pd.read_parquet(shard_path, columns=["anchor_id", "coj_epoch"])
            existing_keys = set(zip(existing.anchor_id.astype(str), existing.coj_epoch.astype(int)))
            if existing_keys != expected_keys:
                raise RuntimeError(f"existing shard key mismatch: {shard_path}")
            print(f"[full] skip shard {int(shard_id)+1}/{manifest.shard_id.nunique()}", flush=True)
            continue
        rows = eligibility[
            eligibility.anchor_id.astype(str).isin(set(shard_ids)) & eligibility.coverage_complete
        ].copy()
        rows["crop_path"] = [str(root / f"crops/{e}/{a}.png") for a, e in zip(rows.anchor_id, rows.coj_epoch)]
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as render_pool:
            render_futures = [render_pool.submit(render_crop, row, Path(row.crop_path)) for _, row in rows.iterrows()]
            for future in as_completed(render_futures):
                future.result()
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as score_pool:
            futures = {
                score_pool.submit(
                    _score_target_sequence,
                    rows[rows.anchor_id.astype(str) == aid].sort_values("coj_epoch").reset_index(drop=True),
                    contexts.get(aid, pd.DataFrame()),
                    config,
                    limiter,
                ): aid
                for aid in shard_ids
            }
            for n, future in enumerate(as_completed(futures), 1):
                results.extend(future.result())
                if n % 100 == 0:
                    print(
                        f"[full] shard {int(shard_id)+1}/{manifest.shard_id.nunique()} "
                        f"sequences {n}/{len(futures)}",
                        flush=True,
                    )
        shard = pd.DataFrame(results).sort_values(["anchor_id", "coj_epoch"])
        shard_keys = set(zip(shard.anchor_id.astype(str), shard.coj_epoch.astype(int)))
        if shard_keys != expected_keys:
            raise RuntimeError(f"new shard key mismatch: {shard_path}")
        tmp = shard_path.with_suffix(".parquet.tmp")
        shard.to_parquet(tmp, index=False)
        os.replace(tmp, shard_path)
        print(f"[full] committed shard {int(shard_id)+1}/{manifest.shard_id.nunique()}", flush=True)

    shard_paths = sorted(shards_root.glob("shard_*.parquet"))
    if len(shard_paths) != manifest.shard_id.nunique():
        raise RuntimeError("full expansion ended with missing shards")
    combined = pd.concat([pd.read_parquet(path) for path in shard_paths], ignore_index=True)
    combined = combined.sort_values(["anchor_id", "coj_epoch"])
    tmp = full_root / "full_observations.parquet.tmp"
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, full_root / "full_observations.parquet")
    atomic_json(full_root / "FULL_RUN.json", {
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "workers": args.workers,
        "qps": args.qps,
        "targets": int(combined.anchor_id.nunique()),
        "observations": int(len(combined)),
        "shards": len(shard_paths),
        "instrument": "same_target_five_image_sequence_v1",
    })


def full_report(args: argparse.Namespace) -> None:
    root = args.output_root
    full_root = root / "full"
    eligibility = pd.read_parquet(root / "target_epoch_eligibility.parquet")
    obs = pd.read_parquet(full_root / "full_observations.parquet")
    obs["automated_verdict"] = np.where(
        (obs.quality_flag == "usable") & obs.pv_present.notna() & (obs.confidence.fillna(0) >= .80),
        np.where(obs.pv_present == True, "present", "absent"),  # noqa: E712
        "uninformative",
    )
    metric_columns = ["expected_state", "area_bin", "interval_kind", "grid_id", "chip_arm", "legacy_group_multiplicity"]
    if all(col in obs.columns for col in metric_columns):
        joined = obs
    else:
        joined = obs.merge(
            eligibility[["anchor_id", "coj_epoch", *metric_columns]],
            on=["anchor_id", "coj_epoch"],
            validate="one_to_one",
        )
    rows = []
    for level, col in [("overall", None), ("epoch", "coj_epoch"), ("expected", "expected_state"), ("area", "area_bin"), ("arm", "chip_arm"), ("multiplicity", "legacy_group_multiplicity")]:
        groups: Iterable[tuple[Any, pd.DataFrame]] = [("all", joined)] if col is None else joined.groupby(col)
        for value, frame in groups:
            metric = _cluster_bootstrap(frame)
            rows.append({"level": level, "stratum": str(value), **metric, "non_identifying": int((frame.expected_state == "non_identifying").sum()), "uninformative": int((frame.automated_verdict == "uninformative").sum())})
    pd.DataFrame(rows).to_csv(full_root / "full_strata.csv", index=False)
    overall = _cluster_bootstrap(joined)
    atomic_json(full_root / "FULL_VERDICT.json", {
        "status": "SUPPLEMENTAL_FULL_EXPANSION_PENDING_HUMAN_GATE",
        "partial_verdict": None,
        "reason": "Stage-D expansion cannot retroactively change Stage-C and the blind-human instrument gate remains incomplete.",
        "automated_agreement": overall,
        "targets": int(joined.anchor_id.nunique()),
        "observations": int(len(joined)),
        "uninformative": int((joined.automated_verdict == "uninformative").sum()),
        "full_population_geometry_failed_rows": int((~eligibility.coverage_complete).sum()),
    })


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["preflight", "prepare", "smoke", "smoke2", "pilot", "report", "full", "full_report"])
    p.add_argument("--output-root", type=Path, required=True)
    p.add_argument("--model", default="gemini-3.1-flash-lite")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--qps", type=float, default=2.0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.output_root = args.output_root.expanduser().resolve()
    globals()[args.stage](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
