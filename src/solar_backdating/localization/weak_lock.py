"""PV-masked SuperPoint+LightGlue weak-lock matcher (PRD §5.2 cascade tier 2,
ISSUE-24). Reuses (import, never modifies) the matcher loader, keypoint
matcher and 1-point RANSAC translation fit from
``scripts/temporal/pilot_learned_match_2026-07-09.py`` -- that script has a
dash in its filename so it is loaded via ``importlib`` exactly like
``scripts/temporal/probe_weaklock_2026-07-10.py`` already does. Only the
Vexcel-specific reference-building helpers in that pilot (``build_ref_cache``,
``find_anchor_dir``) are NOT reused -- PRD §5.2 requires a same-domain GEHI
reference (the cross-domain GEHI<->Vexcel cos-0.31 KILL precedent), so
reference selection here is ``solar_backdating.localization.reference``'s
job, not the pilot's.

Masking follows the same convention as ``phase_corr.py``: the PV polygon +
buffer region is replaced with the unmasked region's own mean intensity in
both ``ref``/``mov`` grayscale arrays *before* they are handed to the
SuperPoint extractor, so no keypoint can be detected inside the region under
judgment.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PILOT_SCRIPT = _REPO_ROOT / "scripts" / "temporal" / "pilot_learned_match_2026-07-09.py"
_PILOT_MODULE_NAME = "pilot_learned_match_2026_07_09"

_models_cache: dict[str, dict] = {}


def _load_pilot_module():
    mod = sys.modules.get(_PILOT_MODULE_NAME)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(_PILOT_MODULE_NAME, _PILOT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_PILOT_MODULE_NAME] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def load_weak_lock_matcher(device: str) -> dict:
    """Loads (and caches, per-device) the SuperPoint+LightGlue models via the
    frozen pilot's ``load_matcher``."""
    if device not in _models_cache:
        pilot = _load_pilot_module()
        _models_cache[device] = pilot.load_matcher(
            "superpoint_lightglue", device, pilot.DEFAULT_MAX_KEYPOINTS
        )
    return _models_cache[device]


@dataclass(frozen=True)
class RawMatch:
    """PV-masked SP+LightGlue translation estimate, pre-schema (internal
    cascade bookkeeping)."""

    dx_px: float
    dy_px: float
    n_matches: int
    n_inliers: int
    inlier_ratio: float
    residual_std_px: float | None


def _mask_fill(gray: np.ndarray, mask: np.ndarray) -> np.ndarray:
    unmasked = ~mask
    fill = float(gray[unmasked].mean()) if unmasked.any() else float(gray.mean())
    return np.where(mask, fill, gray)


def match_translation_masked(
    ref_gray: np.ndarray,
    mov_gray: np.ndarray,
    mask: np.ndarray,
    device: str,
    *,
    ransac_thresh_px: float | None = None,
) -> RawMatch:
    """PV-masked counterpart of the pilot's ``match_translation`` -- masks
    both inputs, then reuses ``get_matched_keypoints``/``ransac_translation``
    directly (rather than the wrapper) so the inlier residual spread is
    available for ``shift_uncertainty_m``."""
    pilot = _load_pilot_module()
    models = load_weak_lock_matcher(device)
    thresh = ransac_thresh_px if ransac_thresh_px is not None else pilot.DEFAULT_RANSAC_THRESH_PX

    r = _mask_fill(ref_gray, mask)
    m = _mask_fill(mov_gray, mask)

    kpts0, kpts1 = pilot.get_matched_keypoints("superpoint_lightglue", models, r, m, device)
    n_matches = int(len(kpts0))
    if n_matches == 0:
        return RawMatch(0.0, 0.0, 0, 0, 0.0, None)

    disp = kpts1 - kpts0
    dx_px, dy_px, inlier_mask = pilot.ransac_translation(disp, thresh_px=thresh)
    n_inliers = int(inlier_mask.sum())
    inlier_ratio = n_inliers / n_matches

    residual_std_px = None
    if n_inliers > 0:
        inlier_disp = disp[inlier_mask]
        residuals = np.linalg.norm(inlier_disp - inlier_disp.mean(axis=0), axis=1)
        residual_std_px = float(residuals.std())

    return RawMatch(
        dx_px=dx_px,
        dy_px=dy_px,
        n_matches=n_matches,
        n_inliers=n_inliers,
        inlier_ratio=inlier_ratio,
        residual_std_px=residual_std_px,
    )
