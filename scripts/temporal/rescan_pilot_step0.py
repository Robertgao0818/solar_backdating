#!/usr/bin/env python3
"""RUN 2 re-scan pilot — Step 0: owner's model-hypothesis probe.

Read-only against scan states / chips. For a fixed set of index anchors,
re-sends each anchor's full observed frame sequence (deduped by capture_date,
later round wins) to multiple {model, crop} combinations using the EXACT
production scoring path (`score_batch_with_fallback`, same batch prompt +
census-calibration suffix), so results are directly comparable to what
production actually saw.

Two crop variants:
  - production: `target_crop_review_png_path` PNG already cached on disk by
    the production run (read-only; never regenerated here since it already
    exists with the exact production params).
  - enlarged: same crop math, min_crop_size_m = 2x production review_extent_m,
    rendered fresh into $OUT/enlarged_pngs (NEVER written next to the source
    chip -- the chips dir is read-only for this pilot).

Usage:
    python scripts/temporal/rescan_pilot_step0.py --out-dir <OUT>/step0
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import ReviewTargetMarker, _clamp_int, _draw_single_target_marker_at
from scripts.temporal.run_adaptive_scan import _default_gemini_env, _load_gemini_config
from scripts.validation.gemini_solar_image_review import (
    BatchPick,
    GeminiClientConfig,
    score_batch_with_fallback,
)

ANCHORS_ALL_CSV = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/anchors_all.csv"
A24_SCAN_STATES_DIR = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/a24/scan_states"
A48_SCAN_STATES_DIR = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/a48/scan_states"

INDEX_ANCHOR_SUFFIXES = ("t00024435", "t00039667", "t00038450")

MODELS = ("gemini-3.1-flash-lite", "gemini-3-flash", "gemini-3-flash-agent")


def _find_anchor_row(anchor_suffix: str) -> dict[str, str]:
    with ANCHORS_ALL_CSV.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["anchor_id"].endswith(anchor_suffix):
                return row
    raise SystemExit(f"anchor not found in anchors_all.csv: {anchor_suffix}")


def _find_scan_state_path(anchor_id: str) -> Path:
    for d in (A24_SCAN_STATES_DIR, A48_SCAN_STATES_DIR):
        p = d / f"{anchor_id}.json"
        if p.exists():
            return p
    raise SystemExit(f"scan_state not found for {anchor_id}")


def _dedup_frames(state: dict) -> list[dict]:
    """Flatten all rounds' results, later round wins on same capture_date."""
    by_date: dict[str, dict] = {}
    for rnd in state.get("rounds", []):
        for res in rnd.get("results", []):
            if res.get("chip_path") and res.get("pv_present") is not None or res.get("quality_flag"):
                by_date[res["capture_date"]] = res
    return [by_date[d] for d in sorted(by_date)]


def _render_enlarged_png(
    *,
    source_tif: Path,
    marker: ReviewTargetMarker,
    chip_size_m: float,
    min_crop_size_m: float,
    bbox_width_m: float,
    bbox_height_m: float,
    out_path: Path,
) -> Path:
    """Same crop math as gehi_common.ensure_single_target_review_png, but
    written to an arbitrary out_path (scratchpad) instead of a sibling of
    source_tif -- so the read-only chips dir is never touched.
    """
    from PIL import Image

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    crop_context_multiplier = 0.01
    min_output_px = 256
    with Image.open(source_tif) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        else:
            img = img.copy()
        w, h = img.size
        short = min(w, h)
        target_x = w * (0.5 + float(marker.offset_x_m) / float(chip_size_m))
        target_y = h * (0.5 - float(marker.offset_y_m) / float(chip_size_m))
        radius_m = (
            float(marker.search_radius_m)
            if marker.search_radius_m is not None and marker.search_radius_m > 0
            else max(float(chip_size_m) * 0.05, 1.0)
        )
        crop_size_m = min(
            float(chip_size_m),
            max(float(min_crop_size_m), 2.0 * radius_m * float(crop_context_multiplier)),
        )
        crop_px = max(1, int(round(short * crop_size_m / float(chip_size_m))))
        crop_px = min(crop_px, w, h)
        left = _clamp_int(int(round(target_x - crop_px / 2)), 0, max(0, w - crop_px))
        top = _clamp_int(int(round(target_y - crop_px / 2)), 0, max(0, h - crop_px))
        right = left + crop_px
        bottom = top + crop_px
        crop = img.crop((left, top, right, bottom))

        scale = 1.0
        if min(crop.size) < min_output_px:
            scale = float(min_output_px) / float(min(crop.size))
            new_size = (max(1, int(round(crop.size[0] * scale))), max(1, int(round(crop.size[1] * scale))))
            resampling = getattr(Image, "Resampling", Image).BICUBIC
            crop = crop.resize(new_size, resampling)

        local_x = (target_x - left) * scale
        local_y = (target_y - top) * scale
        search_radius_px = short * radius_m / float(chip_size_m) * scale
        bbox_width_px = short * float(bbox_width_m) / float(chip_size_m) * scale
        bbox_height_px = short * float(bbox_height_m) / float(chip_size_m) * scale
        _draw_single_target_marker_at(
            crop,
            x=local_x,
            y=local_y,
            target_label=marker.target_label,
            search_radius_px=search_radius_px,
            bbox_width_px=bbox_width_px,
            bbox_height_px=bbox_height_px,
        )
        crop.save(out_path, format="PNG")
    return out_path


def _production_review_png(
    *,
    source_tif: Path,
    marker: ReviewTargetMarker,
    chip_size_m: float,
    review_extent_m: float,
    bbox_width_m: float,
    bbox_height_m: float,
) -> Path:
    from scripts.temporal.gehi_common import ensure_single_target_review_png

    return ensure_single_target_review_png(
        source_tif,
        marker,
        chip_size_m=chip_size_m,
        crop_context_multiplier=0.01,
        min_crop_size_m=review_extent_m,
        min_output_px=256,
        draw_marker=True,
        bbox_width_m=bbox_width_m,
        bbox_height_m=bbox_height_m,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--models", nargs="+", default=list(MODELS))
    parser.add_argument("--anchors", nargs="+", default=list(INDEX_ANCHOR_SUFFIXES))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    enlarged_dir = args.out_dir.parent / "enlarged_pngs"
    enlarged_dir.mkdir(parents=True, exist_ok=True)

    env_path = _default_gemini_env()
    base_cfg = _load_gemini_config(env_path)

    rows_out: list[dict] = []
    skipped_models: set[str] = set()

    for anchor_suffix in args.anchors:
        row = _find_anchor_row(anchor_suffix)
        anchor_id = row["anchor_id"]
        state_path = _find_scan_state_path(anchor_id)
        state = json.loads(state_path.read_text())
        frames = _dedup_frames(state)
        chip_size_m = float(row["chip_size_m"])
        review_extent_m = float(row.get("review_extent_m") or 24.0)
        bbox_w = float(row["source_width_m"])
        bbox_h = float(row["source_height_m"])
        marker = ReviewTargetMarker(
            target_id=anchor_id,
            target_label=row.get("target_label") or "T01",
            offset_x_m=float(row["target_offset_x_m"]),
            offset_y_m=float(row["target_offset_y_m"]),
            search_radius_m=float(row["search_radius_m"]),
        )
        census_date = state.get("census_date")

        print(f"[{anchor_id}] {len(frames)} frames, review_extent_m={review_extent_m}, census_date={census_date}")

        for variant in ("production", "enlarged"):
            picks: list[BatchPick] = []
            for i, frame in enumerate(frames, start=1):
                source_tif = Path(frame["chip_path"])
                if not source_tif.exists():
                    print(f"  MISSING chip on disk, skip frame: {source_tif}")
                    continue
                if variant == "production":
                    png_path = _production_review_png(
                        source_tif=source_tif,
                        marker=marker,
                        chip_size_m=chip_size_m,
                        review_extent_m=review_extent_m,
                        bbox_width_m=bbox_w,
                        bbox_height_m=bbox_h,
                    )
                else:
                    out_name = f"{anchor_id}_{frame['capture_date']}_v{frame.get('version','')}_enlarged.png"
                    png_path = _render_enlarged_png(
                        source_tif=source_tif,
                        marker=marker,
                        chip_size_m=chip_size_m,
                        min_crop_size_m=review_extent_m * 2.0,
                        bbox_width_m=bbox_w,
                        bbox_height_m=bbox_h,
                        out_path=enlarged_dir / out_name,
                    )
                picks.append(
                    BatchPick(
                        chip_index=len(picks) + 1,
                        chip_path=png_path,
                        capture_date=frame["capture_date"],
                        version=str(frame.get("version", "")),
                    )
                )

            if not picks:
                continue

            for model in args.models:
                if model in skipped_models:
                    continue
                cfg = GeminiClientConfig(
                    base_url=base_cfg.base_url,
                    api_key=base_cfg.api_key,
                    model=model,
                    api_format=base_cfg.api_format,
                    native_path=base_cfg.native_path,
                )
                audit_path = args.out_dir / f"{anchor_id}__{variant}__{model}.audit.jsonl"

                def _audit(payload: dict, _p=audit_path) -> None:
                    with _p.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

                try:
                    obs = score_batch_with_fallback(
                        picks,
                        config=cfg,
                        audit_writer=_audit,
                        census_mid_date_iso=census_date,
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc)
                    print(f"  [{model}/{variant}] ERROR: {msg[:200]}")
                    if "404" in msg or "not found" in msg.lower() or "NotFound" in msg:
                        print(f"  -> marking model {model!r} unavailable on this gateway, skipping for rest of run")
                        skipped_models.add(model)
                    continue

                for o, frame in zip(obs, [f for f in frames if Path(f["chip_path"]).exists()]):
                    rows_out.append(
                        {
                            "anchor_id": anchor_id,
                            "variant": variant,
                            "model": model,
                            "capture_date": frame["capture_date"],
                            "prod_pv_present": frame.get("pv_present"),
                            "prod_confidence": frame.get("confidence"),
                            "prod_quality_flag": frame.get("quality_flag"),
                            "new_pv_present": o.pv_present,
                            "new_confidence": o.confidence,
                            "new_quality_flag": o.quality_flag,
                            "new_evidence": o.evidence,
                            "new_notes": o.notes,
                            "new_decision_source": o.decision_source,
                        }
                    )
                    flip = ""
                    if frame.get("pv_present") is False and o.pv_present is True:
                        flip = "  <<< FLIP ABSENT->PRESENT"
                    print(
                        f"  [{model}/{variant}] {frame['capture_date']}: "
                        f"prod={frame.get('pv_present')} new={o.pv_present} "
                        f"(conf={o.confidence}, q={o.quality_flag}){flip}"
                    )

    csv_path = args.out_dir / "step0_results.csv"
    if rows_out:
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
            writer.writeheader()
            writer.writerows(rows_out)
        print(f"\nWrote {len(rows_out)} rows to {csv_path}")


if __name__ == "__main__":
    main()
