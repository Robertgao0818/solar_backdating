#!/usr/bin/env python3
"""R2 cascade replay: the semantic-localization cascade (PRD §5/§6/§9 item 2,
``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md``) over real RUN
3-native anchors, emitting one ``TargetLocalizationObservation`` JSONL row per
``(anchor_id, capture_date)`` observation.

Cascade contract (§5.2): ``identity`` (trust TFW/Run3 geometry) ->
``phase_correlation`` (PV-masked, bounded translation lock; escalate on a
weak/ambiguous lock) -> ``weak_lock`` (PV-masked SuperPoint+LightGlue +
1-point RANSAC, bounded translation only). Each stage is a plain function
``StageInput -> TargetLocalizationObservation`` (unchanged contract from the
R2 skeleton, so ``--stage identity|phase_correlation|weak_lock`` still runs
one stage standalone for debugging); ``run_full_cascade`` is the real
priority-ordered orchestrator the replay uses.

Registration internals (PV masking, masked phase correlation, masked
SP+LightGlue, same-domain reference selection) live in
``solar_backdating.localization.{pv_mask,phase_corr,weak_lock,reference,geo}``
-- this script wires them together and owns the manifest I/O, stratified
sampling, and purification-vs-miskill reporting.

Input: the R0 frozen manifest (PRD §3.2,
``~/zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1/manifest.parquet``)
is now the default and only fully-supported path for a real cascade replay.
The legacy ``--anchors-csv``/``--provenance-jsonl`` join (pre-R0) is kept for
the ``identity``-only debug path but does not carry ``source_area_m2``/
``label_v1``/``quality_flag``/reference pools, so ``phase_correlation``/
``weak_lock``/``cascade`` refuse to run against it.

Nothing here mutates or duplicates real data products -- reads only. Output
goes wherever ``--out-dir`` points (default
``~/zasolar_data/geid_temporal/run3_native_line_2026-07/r2_replay_v1/``,
never committed).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from scripts.temporal import chip_displacement as cd
from solar_backdating.localization import (
    CASCADE_STAGES,
    TargetLocalizationObservation,
    effective_label,
    transform_within_bounds,
)
from solar_backdating.localization.geo import shift_ring_lonlat, target_roi_ring_lonlat
from solar_backdating.localization.phase_corr import (
    PSR_FULL,
    RawShift,
    estimate_shift_masked,
    periodicity_score,
)
from solar_backdating.localization.pv_mask import build_centered_mask, pv_mask_half_extent_m
from solar_backdating.localization.reference import ReferenceCandidate, select_reference
from solar_backdating.localization.weak_lock import RawMatch, match_translation_masked

GT_ROOT = Path("~/zasolar_data/geid_temporal").expanduser()
DEFAULT_ANCHORS_CSV = (
    GT_ROOT / "fullscan_gemini_backdating_2026-07" / "anchors_v2" / "anchors_all.csv"
)
DEFAULT_PROVENANCE_JSONL = [
    GT_ROOT / "fullscan_gemini_backdating_2026-07" / "run3_v2" / "a24" / "chip_provenance.jsonl",
    GT_ROOT / "fullscan_gemini_backdating_2026-07" / "run3_v2" / "a48" / "chip_provenance.jsonl",
]
DEFAULT_MANIFEST = GT_ROOT / "run3_native_line_2026-07" / "r0_manifest_v1" / "manifest.parquet"
DEFAULT_OUT_DIR = GT_ROOT / "run3_native_line_2026-07" / "r2_replay_v1"

# chip_provenance.jsonl statuses that mean "there is a usable raster on disk".
_USABLE_STATUSES = frozenset({"downloaded", "skipped_existing"})

# --------------------------------------------------------------------------- #
# Registration-grid / masking constants (§5.2). No per-grid task-grid lookup
# (that registry has no gpkg files on disk in this checkout -- confirmed
# empty -- and this repo's own pilot scripts already fall back to EPSG:32735
# / UTM 35S for the same reason); Johannesburg is the sole region in scope
# for RUN 3-native.
# --------------------------------------------------------------------------- #
METRIC_CRS = "EPSG:32735"
REGISTRATION_GSD_M = 0.3

#: ``projected_target_polygon`` base-geometry version (r3-ceiling cross-check
#: gap fix, ruled by team-lead 2026-07-19: `_identity_ring` switched from the
#: whole chip bbox to the R1-aligned nominal ROI square,
#: `geo.target_roi_ring_lonlat`). Bumped whenever that base geometry changes,
#: so a re-emitted `observations.jsonl` is provenance-distinguishable from an
#: earlier run without one.
POLYGON_BASE_VERSION = "r1_cropgeo_v1_aligned@2026-07-19"
MIN_GRID_SIDE_PX = 24

#: §5.2 tier-2 escalation trigger: a phase-correlation lock below
#: ``chip_displacement.DEFAULT_MIN_PSR`` is "weak" (that constant is
#: calibrated on real GEHI same-anchor pairs, i.e. exactly this domain).
WEAK_LOCK_PSR_FLOOR = cd.DEFAULT_MIN_PSR

#: Weak-lock (SP+LightGlue) confident-lock gate. Provisional defaults, NOT
#: recalibrated against a same-domain (GEHI<->GEHI) positive control in this
#: R2 pass -- ISSUE-24's own calibration sweep was against the cross-domain
#: GEHI<->Vexcel weak-lock population, a different pairing. Flagged as an
#: open follow-up in the R2 delivery memo rather than silently inherited.
MIN_WEAK_LOCK_INLIERS = 8
MIN_WEAK_LOCK_INLIER_RATIO = 0.4

#: Two independent registration signals (phase-correlation's own weak,
#: uncommitted estimate vs. weak-lock's confident fit) disagreeing by more
#: than this are treated as a genuine conflict (§5.3 forced abstain), not
#: estimator noise. Chosen as 2x the schema's own bound so a "conflict" means
#: the two signals could not both be legalizing the same correction.
from solar_backdating.localization.observation import DEFAULT_MAX_TRANSLATION_M

CONFLICT_DISAGREEMENT_M = 2.0 * DEFAULT_MAX_TRANSLATION_M


def _psr_confidence(psr: float) -> float:
    return float(min(1.0, max(0.0, psr / PSR_FULL)))


# --------------------------------------------------------------------------- #
# Stage input/output contract                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StageInput:
    """Everything a cascade stage needs to produce one
    ``TargetLocalizationObservation``.

    ``chip_path`` is the observation's own georeferenced source raster
    (``src_tiff_path`` when built from the R0 manifest -- the rendered
    review PNG is not georeferenced, so registration reads the TIFF).
    ``reference_pool`` is every other RUN 3-native frame of the same
    ``(anchor_id, chip_arm)`` (built from the *full* manifest, not just the
    sampled subset, so a sampled observation's sibling frames are always
    available for reference selection even if the sibling itself was not
    sampled) -- ``solar_backdating.localization.reference.select_reference``
    picks from this pool and enforces the self-pair exclusion.
    """

    anchor_id: str
    capture_date: date
    anchor_bbox_lonlat: tuple[float, float, float, float]
    chip_path: Path | None
    reference_chip_path: Path | None
    prior_observation: TargetLocalizationObservation | None
    chip_arm: str = ""
    area_bin: str = ""
    source_area_m2: float = 0.0
    label_v1: str = ""
    quality_flag: str = ""
    confidence: float | None = None
    reference_pool: tuple[ReferenceCandidate, ...] = ()
    #: Target centroid + routed review extent (manifest's `centroid_lon`/
    #: `centroid_lat`/`review_extent_m` columns) -- feeds the R1-aligned
    #: nominal ROI ring (`_identity_ring`, fixed 2026-07-19, see geo.py's
    #: `target_roi_ring_lonlat` docstring). `None` for the legacy (pre-R0)
    #: join path, which doesn't carry these columns -- `_identity_ring`
    #: falls back to the old whole-chip-bbox ring in that case (identity
    #: debug mode only; the manifest-driven cascade replay always has them).
    centroid_lon: float | None = None
    centroid_lat: float | None = None
    fov_m: float | None = None


StageFn = Callable[[StageInput], TargetLocalizationObservation]


def _identity_ring(inp: StageInput) -> tuple[tuple[float, float], ...]:
    """Nominal target-footprint ring for `projected_target_polygon`.

    R1-aligned ROI square (`geo.target_roi_ring_lonlat`) when the manifest
    fields it needs are present; falls back to the whole chip-bbox ring
    (the pre-fix behaviour) only for the legacy join path, which doesn't
    carry `centroid_lon`/`centroid_lat`/`fov_m`/`source_area_m2`.
    """
    if inp.centroid_lon is not None and inp.centroid_lat is not None and inp.fov_m is not None:
        return target_roi_ring_lonlat(
            inp.centroid_lon,
            inp.centroid_lat,
            fov_m=inp.fov_m,
            source_area_m2=inp.source_area_m2,
        )
    lon_min, lat_min, lon_max, lat_max = inp.anchor_bbox_lonlat
    return (
        (lon_min, lat_min),
        (lon_max, lat_min),
        (lon_max, lat_max),
        (lon_min, lat_max),
        (lon_min, lat_min),
    )


def identity_stage(inp: StageInput) -> TargetLocalizationObservation:
    """§9 item 2 / §5.2 default: trust TFW/Run3 geometry as-is, no correction.
    Never abstains, never fails -- the fallback every observation gets."""
    return TargetLocalizationObservation(
        anchor_id=inp.anchor_id,
        capture_date=inp.capture_date,
        building_found=True,
        roof_plane_matched=True,
        target_localized=True,
        transform_type="identity",
        transform_params={},
        registration_confidence=1.0,
        shift_uncertainty_m=0.0,
        projected_target_polygon=_identity_ring(inp),
        failure_reason="",
        cascade_stage="identity",
        abstain=False,
    )


def _identity_fallback(inp: StageInput, cascade_stage: str) -> TargetLocalizationObservation:
    """No usable reference / grid too small / unreadable raster on the
    *reference* side -- i.e. no information to act on. Per §5.3 "默认信任
    TFW/Run3 几何,仅在有可靠证据时施加有界修正": absence of a correction
    signal is not itself evidence against the observation, so this keeps
    the identity-stage trust rather than downgrading the label."""
    return TargetLocalizationObservation(
        anchor_id=inp.anchor_id,
        capture_date=inp.capture_date,
        building_found=True,
        roof_plane_matched=True,
        target_localized=True,
        transform_type="identity",
        transform_params={},
        registration_confidence=None,
        shift_uncertainty_m=None,
        projected_target_polygon=_identity_ring(inp),
        failure_reason="",
        cascade_stage=cascade_stage,
        abstain=False,
    )


def _corrupt_signal(inp: StageInput, cascade_stage: str) -> TargetLocalizationObservation:
    """The observation's own (masked) chip has no registrable texture --
    the positive corrupt/blank-frame signal this layer is meant to catch
    (PRD §5.1's decontamination framing, not a blind-bucket rescue)."""
    return TargetLocalizationObservation(
        anchor_id=inp.anchor_id,
        capture_date=inp.capture_date,
        building_found=True,
        roof_plane_matched=False,
        target_localized=False,
        transform_type="identity",
        transform_params={},
        registration_confidence=None,
        shift_uncertainty_m=None,
        projected_target_polygon=None,
        failure_reason="roof_plane_not_matched",
        cascade_stage=cascade_stage,
        abstain=False,
    )


# --------------------------------------------------------------------------- #
# Registration context (grid + mask + co-gridded ref/mov), shared by the      #
# phase-correlation and weak-lock stages so an escalation reuses exactly the  #
# same reprojection rather than re-deriving it.                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegistrationContext:
    ref_cand: ReferenceCandidate
    ref_gray: np.ndarray
    mov_gray: np.ndarray
    mask: np.ndarray
    gsd_x: float
    gsd_y: float


def _build_grid_and_mask(
    inp: StageInput,
) -> tuple[Any, tuple[int, int], tuple[float, float], np.ndarray] | None:
    lon_min, lat_min, lon_max, lat_max = inp.anchor_bbox_lonlat
    transform, shape, (gsd_y, gsd_x) = cd.utm_grid_for_bounds(
        lon_min, lat_min, lon_max, lat_max, metric_crs=METRIC_CRS, gsd_m=REGISTRATION_GSD_M
    )
    if shape[0] < MIN_GRID_SIDE_PX or shape[1] < MIN_GRID_SIDE_PX:
        return None
    half_extent_m = pv_mask_half_extent_m(inp.source_area_m2)
    mask, _ = build_centered_mask(shape, REGISTRATION_GSD_M, half_extent_m)
    return transform, shape, (gsd_y, gsd_x), mask


def _reproject_or_none(path: Path, transform: Any, shape: tuple[int, int]) -> np.ndarray | None:
    try:
        return cd.reproject_to_grid(path, dst_crs=METRIC_CRS, dst_transform=transform, dst_shape=shape)
    except Exception:
        return None


def _prepare_registration_context(inp: StageInput) -> RegistrationContext | None:
    if inp.chip_path is None:
        return None
    ref_cand = select_reference(list(inp.reference_pool), exclude_date=inp.capture_date)
    if ref_cand is None:
        return None
    grid = _build_grid_and_mask(inp)
    if grid is None:
        return None
    transform, shape, (gsd_y, gsd_x), mask = grid
    mov_gray = _reproject_or_none(inp.chip_path, transform, shape)
    if mov_gray is None:
        return None
    ref_gray = _reproject_or_none(ref_cand.src_tiff_path, transform, shape)
    if ref_gray is None:
        return None
    return RegistrationContext(
        ref_cand=ref_cand, ref_gray=ref_gray, mov_gray=mov_gray, mask=mask, gsd_x=gsd_x, gsd_y=gsd_y
    )


# --------------------------------------------------------------------------- #
# Tier 1: PV-masked phase correlation (§5.2)                                  #
# --------------------------------------------------------------------------- #


def _phase_correlation_core(
    inp: StageInput,
) -> tuple[TargetLocalizationObservation, RawShift | None, RegistrationContext | None]:
    ctx = _prepare_registration_context(inp)
    if ctx is None:
        return _identity_fallback(inp, "phase_correlation"), None, None

    raw = estimate_shift_masked(ctx.ref_gray, ctx.mov_gray, ctx.mask)

    if not raw.ok:
        if raw.reason in ("low-texture-mov", "low-texture-both"):
            return _corrupt_signal(inp, "phase_correlation"), raw, ctx
        if raw.reason == "low-texture-ref":
            # the reference frame is the blank one -- not evidence about mov.
            return _identity_fallback(inp, "phase_correlation"), raw, ctx
        # "max-offset" or "low-psr": ambiguous lock -- escalate, commit nothing.
        tlo = TargetLocalizationObservation(
            anchor_id=inp.anchor_id,
            capture_date=inp.capture_date,
            building_found=True,
            roof_plane_matched=True,
            target_localized=False,
            transform_type="identity",
            transform_params={},
            registration_confidence=_psr_confidence(raw.psr),
            shift_uncertainty_m=None,
            projected_target_polygon=None,
            failure_reason="low_confidence",
            cascade_stage="phase_correlation",
            abstain=False,
        )
        return tlo, raw, ctx

    dx_m, dy_m, _offset_m = cd.shift_px_to_m(raw.dy_px, raw.dx_px, gsd_y_m=ctx.gsd_y, gsd_x_m=ctx.gsd_x)
    params = {"dx_m": round(dx_m, 4), "dy_m": round(dy_m, 4)}
    confidence = _psr_confidence(raw.psr)
    # skimage upsample_factor=10 subpixel floor, in metres at this grid's gsd.
    shift_unc_m = max(ctx.gsd_x, ctx.gsd_y) * 0.3

    if transform_within_bounds("translation", params):
        polygon = shift_ring_lonlat(_identity_ring(inp), dx_m, dy_m, metric_crs=METRIC_CRS)
        tlo = TargetLocalizationObservation(
            anchor_id=inp.anchor_id,
            capture_date=inp.capture_date,
            building_found=True,
            roof_plane_matched=True,
            target_localized=True,
            transform_type="translation",
            transform_params=params,
            registration_confidence=confidence,
            shift_uncertainty_m=shift_unc_m,
            projected_target_polygon=polygon,
            failure_reason="",
            cascade_stage="phase_correlation",
            abstain=False,
        )
    else:
        tlo = TargetLocalizationObservation(
            anchor_id=inp.anchor_id,
            capture_date=inp.capture_date,
            building_found=True,
            roof_plane_matched=True,
            target_localized=False,
            transform_type="translation",
            transform_params=params,
            registration_confidence=confidence,
            shift_uncertainty_m=shift_unc_m,
            projected_target_polygon=None,
            failure_reason="transform_out_of_bounds",
            cascade_stage="phase_correlation",
            abstain=True,
        )
    return tlo, raw, ctx


def phase_correlation_stage(inp: StageInput) -> TargetLocalizationObservation:
    tlo, _raw, _ctx = _phase_correlation_core(inp)
    return tlo


# --------------------------------------------------------------------------- #
# Tier 2: PV-masked SuperPoint+LightGlue weak lock (§5.2, ISSUE-24)           #
# --------------------------------------------------------------------------- #


def _weak_lock_core(
    inp: StageInput,
    ctx: RegistrationContext | None,
    prior_raw: RawShift | None,
    device: str,
) -> tuple[TargetLocalizationObservation, RawMatch | None]:
    if ctx is None:
        return _identity_fallback(inp, "weak_lock"), None

    match = match_translation_masked(ctx.ref_gray, ctx.mov_gray, ctx.mask, device)

    if (
        match.n_matches == 0
        or match.n_inliers < MIN_WEAK_LOCK_INLIERS
        or match.inlier_ratio < MIN_WEAK_LOCK_INLIER_RATIO
    ):
        tlo = TargetLocalizationObservation(
            anchor_id=inp.anchor_id,
            capture_date=inp.capture_date,
            building_found=True,
            roof_plane_matched=True,
            target_localized=False,
            transform_type="identity",
            transform_params={},
            registration_confidence=match.inlier_ratio,
            shift_uncertainty_m=None,
            projected_target_polygon=None,
            failure_reason="dark_zone",
            cascade_stage="weak_lock",
            abstain=True,
        )
        return tlo, match

    dx_m, dy_m, _offset_m = cd.shift_px_to_m(match.dy_px, match.dx_px, gsd_y_m=ctx.gsd_y, gsd_x_m=ctx.gsd_x)
    params = {"dx_m": round(dx_m, 4), "dy_m": round(dy_m, 4)}
    shift_unc_m = (
        (match.residual_std_px * max(ctx.gsd_x, ctx.gsd_y)) if match.residual_std_px is not None else None
    )

    if not transform_within_bounds("translation", params):
        tlo = TargetLocalizationObservation(
            anchor_id=inp.anchor_id,
            capture_date=inp.capture_date,
            building_found=True,
            roof_plane_matched=True,
            target_localized=False,
            transform_type="translation",
            transform_params=params,
            registration_confidence=match.inlier_ratio,
            shift_uncertainty_m=shift_unc_m,
            projected_target_polygon=None,
            failure_reason="transform_out_of_bounds",
            cascade_stage="weak_lock",
            abstain=True,
        )
        return tlo, match

    # §5.3 "两信号冲突...⇒ abstain": compare against phase-correlation's own
    # (uncommitted) estimate, when it produced one (a real peak, not a
    # low-texture short-circuit -- prior_raw.psr > 0 gates that).
    conflict = False
    if prior_raw is not None and prior_raw.psr > 0:
        prior_dx_m, prior_dy_m, _ = cd.shift_px_to_m(
            prior_raw.dy_px, prior_raw.dx_px, gsd_y_m=ctx.gsd_y, gsd_x_m=ctx.gsd_x
        )
        disagreement_m = float(np.hypot(dx_m - prior_dx_m, dy_m - prior_dy_m))
        if disagreement_m > CONFLICT_DISAGREEMENT_M:
            conflict = True

    if conflict:
        tlo = TargetLocalizationObservation(
            anchor_id=inp.anchor_id,
            capture_date=inp.capture_date,
            building_found=True,
            roof_plane_matched=True,
            target_localized=False,
            transform_type="translation",
            transform_params=params,
            registration_confidence=match.inlier_ratio,
            shift_uncertainty_m=shift_unc_m,
            projected_target_polygon=None,
            failure_reason="transform_conflict",
            cascade_stage="weak_lock",
            abstain=True,
        )
        return tlo, match

    polygon = shift_ring_lonlat(_identity_ring(inp), dx_m, dy_m, metric_crs=METRIC_CRS)
    tlo = TargetLocalizationObservation(
        anchor_id=inp.anchor_id,
        capture_date=inp.capture_date,
        building_found=True,
        roof_plane_matched=True,
        target_localized=True,
        transform_type="translation",
        transform_params=params,
        registration_confidence=match.inlier_ratio,
        shift_uncertainty_m=shift_unc_m,
        projected_target_polygon=polygon,
        failure_reason="",
        cascade_stage="weak_lock",
        abstain=False,
    )
    return tlo, match


def resolve_device(preferred: str | None = None) -> str:
    if preferred:
        return preferred
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def weak_lock_stage(inp: StageInput) -> TargetLocalizationObservation:
    """Standalone entry point (``--stage weak_lock``): always attempts
    SP+LightGlue directly, independent of phase-correlation's verdict --
    matches the original stub's semantics of a single-stage debug run."""
    ctx = _prepare_registration_context(inp)
    tlo, _match = _weak_lock_core(inp, ctx, prior_raw=None, device=resolve_device())
    return tlo


STAGE_FUNCS: dict[str, StageFn] = {
    "identity": identity_stage,
    "phase_correlation": phase_correlation_stage,
    "weak_lock": weak_lock_stage,
}


def run_full_cascade(inp: StageInput, *, device: str | None = None) -> TargetLocalizationObservation:
    """The real priority-ordered cascade (§5.2): phase-correlation first;
    escalate to weak-lock only on its ``low_confidence`` (ambiguous, nothing
    committed) marker. A confident phase-correlation lock, a forced abstain,
    or an identity fallback are all final -- weak-lock never re-litigates
    them (ISSUE-24's dark-zone cost makes it the expensive tier-2 fallback,
    not a second opinion on every row)."""
    dev = resolve_device(device)
    tlo, raw, ctx = _phase_correlation_core(inp)
    if (
        tlo.cascade_stage == "phase_correlation"
        and tlo.failure_reason == "low_confidence"
        and not tlo.abstain
        and not tlo.target_localized
    ):
        wl_tlo, _match = _weak_lock_core(inp, ctx, raw, dev)
        return wl_tlo
    return tlo


# --------------------------------------------------------------------------- #
# Manifest-driven data loading (R0, PRD §3.2)                                 #
# --------------------------------------------------------------------------- #

STRATA_COLUMNS = ("chip_arm", "area_bin", "label_v1")
DEFAULT_SAMPLE_SIZE = 2000
#: Reuses R0's own manifest-build seed (MANIFEST_LOCK.json ``params.seed``)
#: for a single documented seed value across the R2 line rather than a fresh
#: unrelated one.
DEFAULT_SAMPLE_SEED = 20260719


def _parse_date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def load_manifest(manifest_path: Path):
    import pandas as pd

    df = pd.read_parquet(manifest_path)
    required = {
        "anchor_id",
        "capture_date",
        "chip_arm",
        "label_v1",
        "quality_flag",
        "confidence",
        "source_area_m2",
        "area_bin",
        "src_tiff_path",
        "chip_lon_min",
        "chip_lat_min",
        "chip_lon_max",
        "chip_lat_max",
        "centroid_lon",
        "centroid_lat",
        "review_extent_m",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"manifest at {manifest_path} missing required columns: {sorted(missing)}")
    return df


def stratified_sample(df, *, target_n: int, seed: int) -> tuple[Any, dict[tuple[Any, ...], int]]:
    """Proportional stratified sample over ``STRATA_COLUMNS``, largest-
    remainder rounding, capped per-stratum at that stratum's population.
    Deterministic given ``seed`` (fixed row order via ``pandas.groupby``'s
    default lexicographic key sort, then ``numpy.random.default_rng(seed)``
    per-stratum index draws)."""
    import pandas as pd

    total = len(df)
    groups = list(df.groupby(list(STRATA_COLUMNS), dropna=False, sort=True))

    raw_alloc = {key: (len(g) / total) * target_n for key, g in groups}
    floor_alloc = {key: int(v) for key, v in raw_alloc.items()}
    remainder = target_n - sum(floor_alloc.values())
    by_frac = sorted(raw_alloc.items(), key=lambda kv: (kv[1] - int(kv[1])), reverse=True)
    for key, _frac in by_frac[: max(remainder, 0)]:
        floor_alloc[key] += 1

    rng = np.random.default_rng(seed)
    parts = []
    counts: dict[tuple[Any, ...], int] = {}
    for key, g in groups:
        n_take = min(floor_alloc.get(key, 0), len(g))
        counts[key] = n_take
        if n_take > 0:
            idx = rng.choice(len(g), size=n_take, replace=False)
            parts.append(g.iloc[idx])
    sample = pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]
    return sample, counts


def build_reference_pools(df) -> dict[tuple[str, str], list[ReferenceCandidate]]:
    pools: dict[tuple[str, str], list[ReferenceCandidate]] = {}
    for row in df.itertuples(index=False):
        key = (row.anchor_id, row.chip_arm)
        conf = row.confidence
        pools.setdefault(key, []).append(
            ReferenceCandidate(
                anchor_id=row.anchor_id,
                capture_date=_parse_date(row.capture_date),
                chip_arm=row.chip_arm,
                src_tiff_path=Path(row.src_tiff_path),
                label_v1=row.label_v1,
                quality_flag=row.quality_flag,
                confidence=float(conf) if conf is not None and not (isinstance(conf, float) and np.isnan(conf)) else 0.0,
            )
        )
    return pools


def build_stage_inputs_from_manifest(
    sample_df, pools: dict[tuple[str, str], list[ReferenceCandidate]]
) -> list[StageInput]:
    inputs = []
    for row in sample_df.itertuples(index=False):
        bbox = (
            float(row.chip_lon_min),
            float(row.chip_lat_min),
            float(row.chip_lon_max),
            float(row.chip_lat_max),
        )
        pool = tuple(pools.get((row.anchor_id, row.chip_arm), ()))
        conf = row.confidence
        area = row.source_area_m2
        inputs.append(
            StageInput(
                anchor_id=row.anchor_id,
                capture_date=_parse_date(row.capture_date),
                anchor_bbox_lonlat=bbox,
                chip_path=Path(row.src_tiff_path) if row.src_tiff_path else None,
                reference_chip_path=None,
                prior_observation=None,
                chip_arm=row.chip_arm,
                area_bin=row.area_bin,
                source_area_m2=float(area) if area is not None and not (isinstance(area, float) and np.isnan(area)) else 0.0,
                label_v1=row.label_v1,
                quality_flag=row.quality_flag,
                confidence=float(conf) if conf is not None and not (isinstance(conf, float) and np.isnan(conf)) else None,
                reference_pool=pool,
                centroid_lon=_to_float_or_none(row.centroid_lon),
                centroid_lat=_to_float_or_none(row.centroid_lat),
                fov_m=_to_float_or_none(row.review_extent_m),
            )
        )
    return inputs


def _to_float_or_none(value: Any) -> float | None:
    """``centroid_lon``/``centroid_lat`` are stored as strings in the R0
    manifest (object dtype); ``review_extent_m`` as int64. Both parsed
    defensively -- ``None``/NaN/unparseable falls back to the legacy
    whole-chip-bbox ring in ``_identity_ring`` rather than raising."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


# --------------------------------------------------------------------------- #
# Legacy (pre-R0) anchors_all.csv x chip_provenance.jsonl join -- identity-   #
# only debug path, kept for continuity with the original R2 skeleton.        #
# --------------------------------------------------------------------------- #


def load_anchor_bboxes(anchors_csv: Path) -> dict[str, tuple[float, float, float, float]]:
    out: dict[str, tuple[float, float, float, float]] = {}
    with anchors_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            out[row["anchor_id"]] = (
                float(row["chip_lon_min"]),
                float(row["chip_lat_min"]),
                float(row["chip_lon_max"]),
                float(row["chip_lat_max"]),
            )
    return out


def iter_provenance_observations(paths: list[Path]) -> Iterator[tuple[str, date, Path]]:
    for path in paths:
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("status") not in _USABLE_STATUSES:
                    continue
                if rec.get("raster_error"):
                    continue
                chip_path = rec.get("chip_path")
                if not chip_path:
                    continue
                try:
                    d = date.fromisoformat(str(rec["capture_date"])[:10])
                except (KeyError, ValueError):
                    continue
                yield rec["anchor_id"], d, Path(chip_path)


def build_stage_inputs_legacy(
    anchors_csv: Path, provenance_jsonl: list[Path], limit: int | None
) -> list[StageInput]:
    bboxes = load_anchor_bboxes(anchors_csv)
    seen: set[tuple[str, date]] = set()
    inputs: list[StageInput] = []
    for anchor_id, capture_date, chip_path in iter_provenance_observations(provenance_jsonl):
        key = (anchor_id, capture_date)
        if key in seen:
            continue
        bbox = bboxes.get(anchor_id)
        if bbox is None:
            continue
        seen.add(key)
        inputs.append(
            StageInput(
                anchor_id=anchor_id,
                capture_date=capture_date,
                anchor_bbox_lonlat=bbox,
                chip_path=chip_path,
                reference_chip_path=None,
                prior_observation=None,
            )
        )
        if limit is not None and len(inputs) >= limit:
            break
    return inputs


# --------------------------------------------------------------------------- #
# Purification-vs-miskill table (§7 G3 empirical input)                       #
# --------------------------------------------------------------------------- #

#: Gated reasons that positively indicate a corrupt/artifact frame the
#: registration layer *could* detect (the PRD §5.1 "预期清理" target).
PURIFICATION_REASONS = frozenset({"roof_plane_not_matched"})
#: Gated reasons that are a forced abstain on a geometric-signal conflict --
#: candidates for "疑似误杀" review, especially when the underlying Gemini
#: call was itself high-confidence/usable (i.e. nothing else about the frame
#: looked wrong).
FORCED_ABSTAIN_LIKE = frozenset({"transform_conflict", "transform_out_of_bounds", "dark_zone"})
HIGH_CONFIDENCE_FLOOR = 0.9


def compute_purification_table(rows: list[dict]) -> list[dict]:
    """One row per ``(chip_arm, area_bin)`` stratum, over the sampled
    replay's ``label_v1 == "absent"`` subset (§3.3's contamination channel).
    ``rows`` are plain dicts merging each sampled manifest row with its TLO's
    ``effective_label`` gate result (see ``main``'s ``_row_record``)."""
    from collections import defaultdict

    by_stratum: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["label_v1"] != "absent":
            continue
        by_stratum[(r["chip_arm"], r["area_bin"])].append(r)

    out = []
    for (arm, area_bin), sub in sorted(by_stratum.items()):
        n_absent = len(sub)
        gated = [r for r in sub if r["gated"]]
        n_gated = len(gated)
        n_purified = sum(1 for r in gated if r["gated_reason"] in PURIFICATION_REASONS)
        n_forced_abstain = sum(1 for r in gated if r["gated_reason"] in FORCED_ABSTAIN_LIKE)
        n_suspect_miskill = sum(
            1
            for r in gated
            if r["gated_reason"] in FORCED_ABSTAIN_LIKE
            and r["quality_flag"] == "usable"
            and (r["confidence"] or 0.0) >= HIGH_CONFIDENCE_FLOOR
        )
        out.append(
            {
                "chip_arm": arm,
                "area_bin": area_bin,
                "n_absent": n_absent,
                "n_gated": n_gated,
                "gated_rate": round(n_gated / n_absent, 4) if n_absent else None,
                "n_purified_corrupt_artifact": n_purified,
                "purified_rate_of_gated": round(n_purified / n_gated, 4) if n_gated else None,
                "n_forced_abstain": n_forced_abstain,
                "n_suspect_miskill": n_suspect_miskill,
                "suspect_miskill_rate_of_gated": round(n_suspect_miskill / n_gated, 4) if n_gated else None,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Periodicity diagnostic (team-lead follow-up, 2026-07-19, DATA-r2 §5.4):    #
# quantifies the "spurious alias lock on repeating structure" hypothesis     #
# raised by the R2 blind-review overlays. Diagnostic only -- does not alter  #
# the cascade's decisions or the purification table above.                  #
# --------------------------------------------------------------------------- #


def run_periodicity_diagnostic(inputs: list[StageInput], *, device: str | None = None) -> list[dict]:
    """One row per observation with a usable registration context: the final
    cascade outcome bucket (mirrors ``run_full_cascade``'s escalation logic
    exactly, but keeps the ``RegistrationContext`` alive so
    ``periodicity_score`` can run on the same masked ``mov_gray`` the real
    stages scored) plus its single-frame periodicity diagnostic."""
    dev = resolve_device(device)
    rows: list[dict] = []
    for inp in inputs:
        pc_tlo, raw, ctx = _phase_correlation_core(inp)
        if ctx is None:
            continue  # no registration context -- nothing to score (identity fallback)

        if (
            pc_tlo.cascade_stage == "phase_correlation"
            and pc_tlo.failure_reason == "low_confidence"
            and not pc_tlo.abstain
            and not pc_tlo.target_localized
        ):
            final_tlo, _match = _weak_lock_core(inp, ctx, raw, dev)
        else:
            final_tlo = pc_tlo

        if final_tlo.target_localized:
            bucket = "confident_lock"
        elif final_tlo.failure_reason:
            bucket = final_tlo.failure_reason
        else:
            bucket = "other"

        pr = periodicity_score(ctx.mov_gray, ctx.mask)
        rows.append(
            {
                "anchor_id": inp.anchor_id,
                "capture_date": inp.capture_date.isoformat(),
                "chip_arm": inp.chip_arm,
                "area_bin": inp.area_bin,
                "label_v1": inp.label_v1,
                "cascade_stage": final_tlo.cascade_stage,
                "bucket": bucket,
                "phase_corr_psr": raw.psr if raw is not None else None,
                "phase_corr_offset_px": raw.offset_px if raw is not None else None,
                "periodicity_alias_psr": pr.alias_psr,
                "periodicity_score": pr.score,
                "periodicity_lag_dx_px": pr.lag_dx_px,
                "periodicity_lag_dy_px": pr.lag_dy_px,
            }
        )
    return rows


def _quantiles(vals: list[float], ps: tuple[float, ...] = (10, 25, 50, 75, 90)) -> dict[str, float | None]:
    if not vals:
        return {f"p{p:g}": None for p in ps} | {"n": 0, "max": None}
    arr = np.asarray(vals, dtype=float)
    out: dict[str, float | None] = {f"p{p:g}": round(float(np.percentile(arr, p)), 3) for p in ps}
    out["n"] = len(vals)
    out["max"] = round(float(arr.max()), 3)
    return out


def summarize_periodicity_diagnostic(rows: list[dict]) -> dict:
    """Per-bucket quantile comparison of ``periodicity_alias_psr`` -- the
    headline table for DATA-r2 §5.4 (see that memo's ``periodicity_score``
    docstring note: ``alias_psr`` is the discriminative field, not the raw
    ``score`` ratio)."""
    from collections import defaultdict

    by_bucket: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_bucket[r["bucket"]].append(r["periodicity_alias_psr"])
    return {bucket: _quantiles(vals) for bucket, vals in sorted(by_bucket.items())}


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--legacy-join",
        action="store_true",
        help="Use the pre-R0 anchors_all.csv x chip_provenance.jsonl join instead of --manifest "
        "(identity stage only -- phase_correlation/weak_lock/cascade need manifest-only fields).",
    )
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS_CSV)
    parser.add_argument("--provenance-jsonl", type=Path, nargs="+", default=DEFAULT_PROVENANCE_JSONL)
    parser.add_argument(
        "--stage",
        choices=(*CASCADE_STAGES, "cascade"),
        default="cascade",
        help="'cascade' (default) runs the real priority-ordered cascade; a single stage name "
        "runs only that stage standalone over every observation (debug).",
    )
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SAMPLE_SEED)
    parser.add_argument("--no-sample", action="store_true", help="skip stratified sampling; use --limit rows in manifest order")
    parser.add_argument("--limit", type=int, default=None, help="cap on observations processed (debug)")
    parser.add_argument("--device", type=str, default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--periodicity-diag",
        action="store_true",
        help="Run the periodicity diagnostic (DATA-r2 §5.4) over the sample instead of the "
        "normal cascade replay -- per-observation periodicity_score + a bucket-wise PSR "
        "quantile comparison table. Requires manifest mode (not --legacy-join).",
    )
    parser.add_argument(
        "--periodicity-diag-dir",
        type=Path,
        default=None,
        help="Output dir for --periodicity-diag (default: <out-dir>/periodicity_diag).",
    )
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.legacy_join:
        inputs = build_stage_inputs_legacy(args.anchors_csv, args.provenance_jsonl, args.limit)
        sample_meta: dict[str, Any] = {"mode": "legacy_join", "n": len(inputs)}
        row_meta: list[dict] = [{} for _ in inputs]
    else:
        df = load_manifest(args.manifest)
        pools = build_reference_pools(df)
        if args.no_sample:
            sample_df = df if args.limit is None else df.head(args.limit)
            strata_counts: dict[tuple[Any, ...], int] = {}
        else:
            sample_df, strata_counts = stratified_sample(df, target_n=args.sample_size, seed=args.seed)
            if args.limit is not None:
                sample_df = sample_df.head(args.limit)
        inputs = build_stage_inputs_from_manifest(sample_df, pools)
        row_meta = [
            {
                "chip_arm": r.chip_arm,
                "area_bin": r.area_bin,
                "label_v1": r.label_v1,
                "quality_flag": r.quality_flag,
                "confidence": None if (r.confidence is None or (isinstance(r.confidence, float) and np.isnan(r.confidence))) else float(r.confidence),
            }
            for r in sample_df.itertuples(index=False)
        ]
        sample_meta = {
            "mode": "manifest",
            "manifest": str(args.manifest),
            "seed": args.seed,
            "target_sample_size": args.sample_size,
            "n_sampled": len(inputs),
            "n_manifest_total": len(df),
            "strata_counts": {"|".join(map(str, k)): v for k, v in sorted(strata_counts.items())},
        }
        lock_path = args.out_dir / "SAMPLE_LOCK.json"
        lock_path.write_text(json.dumps(sample_meta, indent=2) + "\n")

    if not inputs:
        print("no observations to replay -- check --manifest/--anchors-csv paths", file=sys.stderr)
        return 1

    if args.periodicity_diag:
        if args.legacy_join:
            print("--periodicity-diag requires manifest mode; got --legacy-join", file=sys.stderr)
            return 1
        diag_dir = args.periodicity_diag_dir or (args.out_dir / "periodicity_diag")
        diag_dir.mkdir(parents=True, exist_ok=True)
        diag_device = resolve_device(args.device)
        rows = run_periodicity_diagnostic(inputs, device=diag_device)
        with (diag_dir / "periodicity_diag.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        summary = summarize_periodicity_diagnostic(rows)
        (diag_dir / "periodicity_diag_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"wrote {len(rows)} periodicity-diagnostic rows -> {diag_dir / 'periodicity_diag.jsonl'}")
        print(json.dumps(summary, indent=2))
        return 0

    device = resolve_device(args.device)
    stage_fn: StageFn
    if args.stage == "cascade":
        if args.legacy_join:
            print("--stage cascade requires manifest mode (needs source_area_m2/reference pools); "
                  "got --legacy-join", file=sys.stderr)
            return 1
        stage_fn = lambda inp: run_full_cascade(inp, device=device)  # noqa: E731
    else:
        if args.stage != "identity" and args.legacy_join:
            print(f"--stage {args.stage} requires manifest mode; got --legacy-join", file=sys.stderr)
            return 1
        stage_fn = STAGE_FUNCS[args.stage]

    observations: list[TargetLocalizationObservation] = []
    stats: Counter = Counter()
    row_records: list[dict] = []
    for k, (inp, meta) in enumerate(zip(inputs, row_meta), 1):
        stats["attempted"] += 1
        tlo = stage_fn(inp)
        observations.append(tlo)
        stats["cascade_stage:" + tlo.cascade_stage] += 1
        stats["target_localized"] += int(tlo.target_localized)
        stats["abstain"] += int(tlo.abstain)
        stats["building_found"] += int(tlo.building_found)
        stats["roof_plane_matched"] += int(tlo.roof_plane_matched)
        stats["failure_reason:" + (tlo.failure_reason or "none")] += 1

        if meta:
            gate = effective_label(meta["label_v1"], meta["quality_flag"], tlo)
            row_records.append(
                {
                    **meta,
                    "gated": gate["label"] == "uninformative" and meta["label_v1"] == "absent",
                    "gated_reason": gate["gated_reason"],
                }
            )
        if k % 100 == 0:
            print(f"  {k}/{len(inputs)} observations", file=sys.stderr, flush=True)

    out_jsonl = args.out_dir / "observations.jsonl"
    with out_jsonl.open("w") as f:
        for tlo in observations:
            f.write(json.dumps(tlo.to_json_record()) + "\n")

    summary: dict[str, Any] = {
        "generator": "scripts/temporal/replay_localization_cascade.py",
        "polygon_base_version": POLYGON_BASE_VERSION,
        "stage": args.stage,
        "n_observations": len(observations),
        "n_anchors": len({tlo.anchor_id for tlo in observations}),
        **{k: v for k, v in sorted(stats.items())},
    }
    (args.out_dir / "observations.summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    if row_records:
        purification = compute_purification_table(row_records)
        (args.out_dir / "purification_table.json").write_text(json.dumps(purification, indent=2) + "\n")
        with (args.out_dir / "purification_table.csv").open("w", newline="") as f:
            if purification:
                w = csv.DictWriter(f, fieldnames=list(purification[0].keys()))
                w.writeheader()
                w.writerows(purification)

    print(f"wrote {len(observations)} observations -> {out_jsonl}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
