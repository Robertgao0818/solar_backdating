#!/usr/bin/env python3
"""RUN 2 re-scan mini-pilot -- Step 1 scoring loop (resumable).

For each sampled anchor (rescan_pilot_sample.py manifest), re-scores its full
deduped frame history through one or more pilot arms, using the EXACT
production batch-scoring path (score_batch_with_fallback via the registered
`gemini` PresenceScorer, chunked by gemini_max_dates_per_call=5 exactly as
production did), then recomputes status + install interval with a simplified
re-derivation of scan_decision.py's terminal-status logic (Case A/B/C/D on
the re-scored evidence; Case E/R -- failure-rate and no-usable-evidence --
are NOT re-simulated since no new rounds are planned here) plus
infer_install_dates.infer_one for the bracket.

Arms:
  A0  baseline replay   -- gemini-3.1-flash-lite, production crop, prod prompt
  A1  enlarged context  -- gemini-3.1-flash-lite, 2x review-extent crop
  A2  parallax prompt   -- gemini-3.1-flash-lite, production crop, + addendum
  A3  model swap        -- gemini-3-flash,         production crop
  A4  combo             -- gemini-3-flash,          enlarged crop, + addendum
      (A4 is meant to run only on a small owner-selected disagreement subset;
      this script does not special-case it, the caller controls that via
      --manifest.)

Resumable: appends to --out-csv; on startup, loads existing (anchor_id, arm)
pairs and skips them. Safe to Ctrl-C and re-run, or run under tmux.

Read-only against scan states / chips (production review PNGs are read via
ensure_single_target_review_png -- already cached on disk, confirmed no
writes since mtime-gated -- and this script never calls it with a variant it
doesn't already expect to find). Enlarged crops render fresh into
$OUT/enlarged_pngs, never into the chips dir.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date as _date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.validation.gemini_solar_image_review as gsr  # noqa: E402
from scripts.temporal.gehi_common import (  # noqa: E402
    ReviewTargetMarker,
    _clamp_int,
    _draw_single_target_marker_at,
    ensure_single_target_review_png,
)
from scripts.temporal.infer_install_dates import infer_one  # noqa: E402
from scripts.temporal.presence_scorer import get_scorer  # noqa: E402
from scripts.temporal.run_adaptive_scan import (  # noqa: E402
    _default_gemini_env,
    _load_gemini_config,
    _score_batch_picks_chunked,
)
from scripts.temporal.scan_config import AdaptiveScanConfig  # noqa: E402
from scripts.temporal.scan_decision import (  # noqa: E402
    find_transitions,
    is_nonmonotonic,
    usable_observations,
)
from scripts.temporal.scan_state import RoundResult, load_scan_state  # noqa: E402
from scripts.validation.gemini_solar_image_review import BatchPick, GeminiClientConfig  # noqa: E402

BASE = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
ANCHORS_ALL_CSV = BASE / "anchors_all.csv"
SCAN_STATE_DIRS = {"A24": BASE / "a24" / "scan_states", "A48": BASE / "a48" / "scan_states"}

PARALLAX_PROMPT_ADDENDUM = """
ADDITIONAL INSTRUCTION -- PARALLAX AWARENESS: Off-nadir/oblique satellite
captures can shift a rooftop's apparent position by several meters relative
to the fixed marker coordinates, especially on tall buildings -- the marker
was placed using one capture's geometry and may not sit exactly on the same
roof section in a different capture. Before deciding pv_present for each
chip, first LOCATE THE SAME BUILDING / ROOF STRUCTURE visible in the other
chips of this same batch (by roof outline, material, and surroundings) --
track the structure, not the marker's pixel coordinates. If the building
appears shifted relative to where the marker lands in a given chip, judge PV
presence on that same building/roof segment, not strictly under the
crosshair. Do not let a marker-vs-building misalignment cause you to report
a real, visible PV array as absent; if you cannot find the same building at
all in a chip, use quality_flag="ambiguous" rather than a confident absent.
"""

ARM_SPECS = {
    "A0": {"model": "gemini-3.1-flash-lite", "crop": "production", "prompt": "prod"},
    "A1": {"model": "gemini-3.1-flash-lite", "crop": "enlarged", "prompt": "prod"},
    "A2": {"model": "gemini-3.1-flash-lite", "crop": "production", "prompt": "parallax"},
    "A3": {"model": "gemini-3-flash", "crop": "production", "prompt": "prod"},
    "A4": {"model": "gemini-3-flash", "crop": "enlarged", "prompt": "parallax"},
    # A5 (2026-07-18 mid-flight addendum): anchors_all.csv's target_offset_x_m/
    # y_m is STALE (computed vs the legacy group-chip center in
    # build_inventory_chip_groups.py); the rebuilt per-target 96m chips are
    # actually centered on each target's own centroid, so the true offset is
    # ~(0,0) and run_adaptive_scan.py's make_fixed_extent_review_renderer fed
    # the stale value into every production review crop (marker AND crop
    # center). A5 re-renders at the SAME crop size as production
    # (review_extent_m, not enlarged) but with the offset zeroed -- isolates
    # the placement-fix effect from the enlargement effect (A1 mechanically
    # compensates for large offsets by simply covering more area, which is a
    # different mechanism from actually recentering).
    "A5": {"model": "gemini-3.1-flash-lite", "crop": "recentered", "prompt": "prod"},
}

OUT_FIELDS = [
    "anchor_id", "fingerprint", "arm", "model", "crop", "prompt",
    "orig_status", "n_frames", "n_chunks", "elapsed_sec",
    "contradiction_frame_date", "contradiction_kind",
    "contradiction_orig_verdict", "contradiction_new_verdict", "flipped_absent_to_present",
    "post_census_agreement_rate", "n_post_census_frames",
    "new_status", "status_changed",
    "orig_interval_start", "orig_interval_end",
    "new_interval_start", "new_interval_end", "bracket_changed", "bracket_shift_days",
    "error",
]


def _anchors_all_index() -> dict[str, dict[str, str]]:
    idx: dict[str, dict[str, str]] = {}
    with ANCHORS_ALL_CSV.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            idx[row["anchor_id"]] = row
    return idx


def _find_scan_state_path(anchor_id: str, chip_arm: str) -> Path:
    d = SCAN_STATE_DIRS[chip_arm.upper()]
    p = d / f"{anchor_id}.json"
    if not p.exists():
        raise SystemExit(f"scan_state not found: {p}")
    return p


def _dedup_frames(state) -> list[RoundResult]:
    by_date: dict[str, RoundResult] = {}
    for rnd in state.rounds:
        for res in rnd.results:
            by_date[res.capture_date] = res
    return [by_date[d] for d in sorted(by_date)]


def _render_enlarged_png(*, source_tif: Path, marker: ReviewTargetMarker, chip_size_m: float,
                          min_crop_size_m: float, bbox_width_m: float, bbox_height_m: float,
                          out_path: Path) -> Path:
    from PIL import Image

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    crop_context_multiplier = 0.01
    min_output_px = 256
    with Image.open(source_tif) as img:
        img = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img.copy()
        w, h = img.size
        short = min(w, h)
        target_x = w * (0.5 + float(marker.offset_x_m) / float(chip_size_m))
        target_y = h * (0.5 - float(marker.offset_y_m) / float(chip_size_m))
        radius_m = (float(marker.search_radius_m)
                    if marker.search_radius_m is not None and marker.search_radius_m > 0
                    else max(float(chip_size_m) * 0.05, 1.0))
        crop_size_m = min(float(chip_size_m), max(float(min_crop_size_m), 2.0 * radius_m * crop_context_multiplier))
        crop_px = max(1, int(round(short * crop_size_m / float(chip_size_m))))
        crop_px = min(crop_px, w, h)
        left = _clamp_int(int(round(target_x - crop_px / 2)), 0, max(0, w - crop_px))
        top = _clamp_int(int(round(target_y - crop_px / 2)), 0, max(0, h - crop_px))
        crop = img.crop((left, top, left + crop_px, top + crop_px))
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
        _draw_single_target_marker_at(crop, x=local_x, y=local_y, target_label=marker.target_label,
                                       search_radius_px=search_radius_px, bbox_width_px=bbox_width_px,
                                       bbox_height_px=bbox_height_px)
        crop.save(out_path, format="PNG")
    return out_path


def _reclassify_status(patched_rounds, census_date: str) -> str:
    """Simplified re-derivation of scan_decision.py's terminal Case A/B/C/D.

    Deliberately does NOT re-simulate Case E (>50% failure) or Case R (no
    usable evidence -> anchor_recovery rounds), since this pilot re-scores
    existing frames only and never plans new rounds. Falls back to
    'done_ambiguous_no_recent_anchor' when there is no usable pre-census
    evidence at all, matching Case R's terminal label without the recovery
    round machinery.
    """
    all_results = [r for rnd in patched_rounds for r in rnd.results]
    evidence = [r for r in all_results if r.capture_date[:10] < census_date[:10]]
    usable = usable_observations(evidence)
    if not usable:
        return "done_ambiguous_no_recent_anchor"
    if is_nonmonotonic(usable, census_date=census_date):
        return "done_ambiguous_nonmonotonic"
    if find_transitions(usable):
        return "done_appears"
    n_present = sum(1 for o in usable if o.pv_present)
    n_absent = sum(1 for o in usable if not o.pv_present)
    if n_absent > 0 and n_present == 0:
        return "done_installed_during_census"
    if n_present > 0 and n_absent == 0:
        return "done_already_present_before_geid_history"
    return "done_ambiguous_nonmonotonic"


def _bracket_shift_days(orig_start: str, orig_end: str, new_start: str, new_end: str) -> str:
    def _mid(s: str, e: str):
        if not s or not e:
            return None
        try:
            sd, ed = _date.fromisoformat(s[:10]), _date.fromisoformat(e[:10])
        except ValueError:
            return None
        return sd + (ed - sd) / 2
    om, nm = _mid(orig_start, orig_end), _mid(new_start, new_end)
    if om is None or nm is None:
        return ""
    return str((nm - om).days)


def _existing_pairs(out_csv: Path) -> set[tuple[str, str]]:
    if not out_csv.exists():
        return set()
    with out_csv.open(newline="", encoding="utf-8") as fh:
        return {(row["anchor_id"], row["arm"]) for row in csv.DictReader(fh)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-csv", type=Path, required=True)
    parser.add_argument("--enlarged-dir", type=Path, required=True)
    parser.add_argument("--arms", nargs="+", default=["A0", "A1", "A2", "A3"])
    args = parser.parse_args()

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    args.enlarged_dir.mkdir(parents=True, exist_ok=True)
    done_pairs = _existing_pairs(args.out_csv)
    write_header = not args.out_csv.exists()

    anchors_idx = _anchors_all_index()
    with args.manifest.open(newline="", encoding="utf-8") as fh:
        manifest_rows = list(csv.DictReader(fh))

    env_path = _default_gemini_env()
    base_cfg = _load_gemini_config(env_path)
    scan_cfg = AdaptiveScanConfig()
    scorer = get_scorer("gemini")

    out_fh = args.out_csv.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_fh, fieldnames=OUT_FIELDS)
    if write_header:
        writer.writeheader()
        out_fh.flush()

    n_done = 0
    for row in manifest_rows:
        anchor_id = row["anchor_id"]
        fingerprint = row["fingerprint"]
        chip_arm = row["chip_arm"]
        census_date = row["census_date"]
        anchor_row = anchors_idx.get(anchor_id)
        if anchor_row is None:
            print(f"SKIP {anchor_id}: not in anchors_all.csv")
            continue
        state_path = _find_scan_state_path(anchor_id, chip_arm)
        state = load_scan_state(state_path)
        frames = _dedup_frames(state)
        frames_on_disk = [f for f in frames if Path(f.chip_path).exists()]
        if not frames_on_disk:
            print(f"SKIP {anchor_id}: no chips on disk")
            continue

        chip_size_m = float(anchor_row["chip_size_m"])
        review_extent_m = float(anchor_row.get("review_extent_m") or 24.0)
        bbox_w = float(anchor_row["source_width_m"])
        bbox_h = float(anchor_row["source_height_m"])
        marker = ReviewTargetMarker(
            target_id=anchor_id,
            target_label=anchor_row.get("target_label") or "T01",
            offset_x_m=float(anchor_row["target_offset_x_m"]),
            offset_y_m=float(anchor_row["target_offset_y_m"]),
            search_radius_m=float(anchor_row["search_radius_m"]),
        )

        if fingerprint == "F1_critical":
            contradiction_date = row["absent_date"]
            contradiction_kind = "F1_pre_census_contradiction"
        else:
            contradiction_date = row["last_absent_date"]
            contradiction_kind = "F3_late_transition_suspect"

        orig_interval = infer_one(
            state, census_mid_date=_date.fromisoformat(census_date[:10]),
            scan_state_path=state_path, vexcel_capture_by_grid=None,
        )

        for arm in args.arms:
            if (anchor_id, arm) in done_pairs:
                continue
            spec = ARM_SPECS[arm]
            t0 = time.monotonic()

            png_paths: dict[str, Path] = {}
            for f in frames_on_disk:
                source_tif = Path(f.chip_path)
                if spec["crop"] == "production":
                    png_paths[f.capture_date] = ensure_single_target_review_png(
                        source_tif, marker, chip_size_m=chip_size_m,
                        crop_context_multiplier=0.01, min_crop_size_m=review_extent_m,
                        min_output_px=256, draw_marker=True,
                        bbox_width_m=bbox_w, bbox_height_m=bbox_h,
                    )
                elif spec["crop"] == "enlarged":
                    out_name = f"{anchor_id}_{f.capture_date}_v{f.version}_enlarged.png"
                    png_paths[f.capture_date] = _render_enlarged_png(
                        source_tif=source_tif, marker=marker, chip_size_m=chip_size_m,
                        min_crop_size_m=review_extent_m * 2.0,
                        bbox_width_m=bbox_w, bbox_height_m=bbox_h,
                        out_path=args.enlarged_dir / out_name,
                    )
                elif spec["crop"] == "recentered":
                    # Same crop SIZE as production (review_extent_m) but the
                    # stale target_offset_x_m/y_m is zeroed -- the marker/crop
                    # center moves to the chip's own center, which is where
                    # the rebuilt per-target chip is actually centered.
                    import dataclasses as _dc

                    zeroed_marker = _dc.replace(marker, offset_x_m=0.0, offset_y_m=0.0)
                    out_name = f"{anchor_id}_{f.capture_date}_v{f.version}_recentered.png"
                    png_paths[f.capture_date] = _render_enlarged_png(
                        source_tif=source_tif, marker=zeroed_marker, chip_size_m=chip_size_m,
                        min_crop_size_m=review_extent_m,
                        bbox_width_m=bbox_w, bbox_height_m=bbox_h,
                        out_path=args.enlarged_dir / out_name,
                    )
                else:
                    raise ValueError(f"unknown crop variant: {spec['crop']!r}")

            score_picks = [
                BatchPick(
                    chip_index=i, chip_path=png_paths[f.capture_date],
                    capture_date=f.capture_date, version=str(f.version),
                    actual_zoom=f.actual_zoom,
                )
                for i, f in enumerate(frames_on_disk, start=1)
            ]
            batch_to_original = {i: i for i in range(1, len(score_picks) + 1)}
            gemini_config = GeminiClientConfig(
                base_url=base_cfg.base_url, api_key=base_cfg.api_key, model=spec["model"],
                api_format=base_cfg.api_format, native_path=base_cfg.native_path,
            )

            audit_path = args.enlarged_dir.parent / "audit" / f"{anchor_id}__{arm}.audit.jsonl"
            audit_path.parent.mkdir(parents=True, exist_ok=True)

            def _audit(payload: dict, _p=audit_path) -> None:
                with _p.open("a", encoding="utf-8") as afh:
                    afh.write(json.dumps(payload, ensure_ascii=False) + "\n")

            n_chunks = -(-len(score_picks) // scan_cfg.gemini_max_dates_per_call)
            error = ""
            try:
                original_template = gsr.BATCH_PROMPT_TEMPLATE
                if spec["prompt"] == "parallax":
                    gsr.BATCH_PROMPT_TEMPLATE = original_template + PARALLAX_PROMPT_ADDENDUM
                try:
                    obs_by_original = _score_batch_picks_chunked(
                        score_picks, batch_to_original, config=scan_cfg,
                        gemini_config=gemini_config, audit_writer=_audit,
                        census_mid_date_iso=census_date, scorer=scorer,
                    )
                finally:
                    gsr.BATCH_PROMPT_TEMPLATE = original_template
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"[:400]
                obs_by_original = {}

            elapsed = time.monotonic() - t0

            # Patch a deep-copied round-result set for reclassification / interval recompute.
            import copy
            patched_state = copy.deepcopy(state)
            new_by_date: dict[str, object] = {}
            for rnd in patched_state.rounds:
                for res in rnd.results:
                    obs = obs_by_original.get(
                        next((i for i, f in enumerate(frames_on_disk, start=1) if f.capture_date == res.capture_date), -1)
                    )
                    if obs is not None:
                        res.pv_present = obs.pv_present
                        res.confidence = obs.confidence
                        res.quality_flag = obs.quality_flag
                        res.decision_source = obs.decision_source
                        new_by_date[res.capture_date] = obs

            contradiction_orig = next((f.pv_present for f in frames_on_disk if f.capture_date == contradiction_date), None)
            contradiction_new_obs = new_by_date.get(contradiction_date)
            contradiction_new = contradiction_new_obs.pv_present if contradiction_new_obs is not None else None
            flipped = bool(contradiction_orig is False and contradiction_new is True)

            post_census_frames = [f for f in frames_on_disk if f.capture_date[:10] >= census_date[:10]]
            agree = 0
            for f in post_census_frames:
                new_obs = new_by_date.get(f.capture_date)
                if new_obs is not None and new_obs.pv_present == f.pv_present:
                    agree += 1
            agreement_rate = (agree / len(post_census_frames)) if post_census_frames else ""

            new_status = _reclassify_status(patched_state.rounds, census_date) if not error else ""
            patched_state.status = new_status or patched_state.status
            new_interval = (
                infer_one(patched_state, census_mid_date=_date.fromisoformat(census_date[:10]),
                          scan_state_path=state_path, vexcel_capture_by_grid=None)
                if not error else None
            )

            out_row = {
                "anchor_id": anchor_id, "fingerprint": fingerprint, "arm": arm,
                "model": spec["model"], "crop": spec["crop"], "prompt": spec["prompt"],
                "orig_status": state.status, "n_frames": len(frames_on_disk), "n_chunks": n_chunks,
                "elapsed_sec": f"{elapsed:.1f}",
                "contradiction_frame_date": contradiction_date, "contradiction_kind": contradiction_kind,
                "contradiction_orig_verdict": contradiction_orig, "contradiction_new_verdict": contradiction_new,
                "flipped_absent_to_present": flipped,
                "post_census_agreement_rate": agreement_rate, "n_post_census_frames": len(post_census_frames),
                "new_status": new_status, "status_changed": bool(new_status and new_status != state.status),
                "orig_interval_start": orig_interval.install_interval_start,
                "orig_interval_end": orig_interval.install_interval_end,
                "new_interval_start": new_interval.install_interval_start if new_interval else "",
                "new_interval_end": new_interval.install_interval_end if new_interval else "",
                "bracket_changed": bool(
                    new_interval and (
                        new_interval.install_interval_start != orig_interval.install_interval_start
                        or new_interval.install_interval_end != orig_interval.install_interval_end
                    )
                ),
                "bracket_shift_days": (
                    _bracket_shift_days(
                        orig_interval.install_interval_start, orig_interval.install_interval_end,
                        new_interval.install_interval_start, new_interval.install_interval_end,
                    ) if new_interval else ""
                ),
                "error": error,
            }
            writer.writerow(out_row)
            out_fh.flush()
            n_done += 1
            print(f"[{n_done}] {anchor_id} {arm}: flip={flipped} new_status={new_status} "
                  f"bracket_changed={out_row['bracket_changed']} elapsed={elapsed:.1f}s" + (f" ERROR={error}" if error else ""))

    out_fh.close()
    print(f"Done. {n_done} new (anchor, arm) rows written to {args.out_csv}")


if __name__ == "__main__":
    main()
