#!/usr/bin/env python3
"""Path C0 pilot — training-free census-anchored reverse template matching.

Prereg: docs/dinov3_scorer/DATA-c0-reverse-template-prereg-2026-07-12.md
(tracker slot ISSUE-09). Stop-discipline: no trained head anywhere; the only
fitted objects are the PCA whitening basis and train-split-pinned thresholds,
both frozen via the config hash before held-out / paired eval.

Stages (independent subcommands, disk artifacts in between):

  embed   GPU once per (arm, anchor): render .nomarker crops for every vintage,
          extract patch-token grids, per-frame quality + registration metrics.
  curve   CPU: anchor selection (census nearest-frame both-sides), whitening,
          footprint/ring masks, +-shift search, s_t = cos(fp) - cos(ring).
  decode  CPU: Bayesian single-changepoint posterior over real-date gaps
          (+ GLR / isotonic cross-checks, persistence rule, cleanliness q).
  smoke   Stage-0 GO/KILL: AUC of s_t vs legacy high-confidence transitions
          (noisy indicative reference only), area-stratified, delta=0 ablation.
  eval    Paired eval vs the fresh Gemini round — stub until the rebuilt
          full-GEHI download + fresh round exist.

All numeric knobs live in C0Config; its sha256 prefix is stamped into every
artifact filename (content-addressed lock, prereg "blind before fresh Gemini").
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(PROJECT_ROOT / "src"), str(PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GT_ROOT = Path("~/zasolar_data/geid_temporal").expanduser()
OUT_DEFAULT = GT_ROOT / "pilot_c0_reverse_template_20260712"

# Vexcel 2024 mid-date fallback (regions.yaml vexcel_2024). The real per-grid
# flight window is 2024-02-17..2024-04-19; per-grid dates preferred when a
# ScanState.census_date is available. See run_adaptive_scan._resolve_census_mid_date.
CENSUS_DATE_FALLBACK = "2024-03-18"

ARMS = {
    # arm name -> (scorer_class_name, backbone override or None for class default)
    "dinov2_floor": ("Dinov2PresenceScorer", None),
    "dinov3_l_sat": ("Dinov3PresenceScorer", None),
    "dinov3_web_s": ("Dinov3PresenceScorer", "vit_small_patch16_dinov3.lvd1689m"),
}


@dataclass(frozen=True)
class C0Config:
    """Frozen prereg knobs. Changing any field changes the config hash."""

    geometry_version: str = "chip_geom_v2_tight12"
    facet: str = "token"              # "token" | "key" (key = qkv-hook arm)
    template_dilate_patches: int = 1  # footprint dilation for the template
    ring_inner_patches: int = 3       # ring = dilate(fp, outer) & ~dilate(fp, inner)
    ring_outer_patches: int = 6
    shift_radius_patches: int = 3     # +- local shift search (~2 m at tight12)
    reg_disagree_m: float = 1.5       # feature-shift vs phase-corr disagreement flag
    delta_left_days: int = 365        # left-anchor eligibility window
    theta_anchor: float | None = None # left-anchor consistency gate; None = record-only
    whiten_eps: float = 1e-4
    whiten_max_tokens: int = 200_000
    gap_prior_by_days: bool = True    # P(tau in gap) prop to gap length in days
    persistence_frames: int = 2       # post-step usable frames required
    q_clean: float = 0.8              # cleanliness threshold (reported, not routing)
    smoke_auc_go: float = 0.75
    smoke_min_anchors: int = 60

    def hash(self) -> str:
        blob = json.dumps(dataclasses.asdict(self), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]


# --------------------------------------------------------------------------
# basemap96 stack adapter (2026-07-13 basemap rebuild layout).
#
# The pre-rebuild scan_states + legacy chip dirs this harness originally
# targeted were wiped by the 2026-07-13 basemap rebuild. The rebuild's
# `chips/<target_id>/z<zoom>/<target_id>_<capture_ymd>_v<vintage_ymd>.tif`
# layout ships a sibling `.tfw` ESRI world file per frame, which gives EXACT
# per-frame georeferencing -- strictly better than the legacy path's aeqd
# approximation + positional GPKG join (`_footprint_mask` below). CRS check
# (2026-07-13): GEHI z19 world-file pixel scale is 0.29858214173896974,
# exactly `156543.03392804097 / 2**19` -- the standard Web-Mercator (EPSG:3857)
# z19 tile resolution -- and the world-file origin of an arbitrary target's
# frame reproduces that target's `chip_lon_min`/`chip_lat_max` in
# anchors_per_target_96m.csv to 1e-7 deg after EPSG:3857 -> EPSG:4326 inverse
# projection. Confirmed EPSG:3857, not a plain equirectangular degree grid.
# --------------------------------------------------------------------------

BASEMAP_TIF_RE = re.compile(r"^(?P<target>.+)_(?P<capture>\d{8})_v(?P<vintage>\d{8})\.tif$")
BASEMAP_ZOOM_DIR_RE = re.compile(r"^z(\d+)$")


@dataclass(frozen=True)
class TfwAffine:
    """A parsed ESRI world file: pixel (col, row) <-> map (x, y).

    Field names follow the 6 world-file lines in file order exactly, so
    ``TfwAffine(*six_floats)`` is correct with no reordering:
    line1 ``a`` = x-scale (map-x per pixel col), line2 ``d`` = rotation
    (map-y per pixel col, 0 for north-up), line3 ``b`` = rotation (map-x per
    pixel row, 0 for north-up), line4 ``e`` = y-scale (map-y per pixel row,
    negative for north-up), line5 ``c`` = x-origin, line6 ``f`` = y-origin
    -- ``c``/``f`` are the map coords of the CENTER of pixel (col=0, row=0).
    """

    a: float
    d: float
    b: float
    e: float
    c: float
    f: float

    def to_map(self, col: float, row: float) -> tuple[float, float]:
        x = self.a * col + self.b * row + self.c
        y = self.d * col + self.e * row + self.f
        return x, y

    def to_pixel(self, x: float, y: float) -> tuple[float, float]:
        det = self.a * self.e - self.b * self.d
        if abs(det) < 1e-12:
            raise ValueError("singular TFW affine (a*e - b*d ~= 0)")
        dx, dy = x - self.c, y - self.f
        col = (self.e * dx - self.b * dy) / det
        row = (-self.d * dx + self.a * dy) / det
        return col, row


def parse_tfw(path: Path) -> TfwAffine:
    """Parse a 6-line ESRI world file. Raises ValueError on malformed input
    (caller treats this as a per-frame skip, never a crash -- the basemap
    rebuild download this reads from is running concurrently)."""
    lines = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
    if len(lines) < 6:
        raise ValueError(f"expected 6 world-file lines, got {len(lines)}")
    try:
        vals = [float(x) for x in lines[:6]]
    except ValueError as exc:
        raise ValueError(f"non-numeric world-file line: {exc}") from None
    return TfwAffine(*vals)


def enumerate_basemap_stack(target_dir: Path) -> tuple[list[dict], list[dict]]:
    """Enumerate one target's basemap_rebuild_2026-07-13 vintage stack.

    Layout: ``<target_dir>/z<zoom>/<target_id>_<capture_ymd>_v<vintage_ymd>.tif``
    with a sibling ``.tfw``. The download populating this tree runs
    concurrently with the pilot -- a file may not exist yet, the .tfw may lag
    the .tif, or the .tif may be a partial write -- so every per-file failure
    is a *skip* with a reason, never a raised exception. Returns
    ``(good, skipped)``, sorted by ``(capture_ymd, vintage_ymd)``. Each
    ``good`` entry carries the parsed ``TfwAffine`` and the tif's own
    (pre-resize) pixel size, so the curve stage can project the census
    footprint exactly per frame.
    """
    from PIL import Image

    good: list[dict] = []
    skipped: list[dict] = []
    for tif_path in sorted(target_dir.rglob("*.tif")):
        m = BASEMAP_TIF_RE.match(tif_path.name)
        if not m:
            skipped.append({"path": tif_path, "skip_reason": "name_no_match"})
            continue
        zoom = 0
        zm = BASEMAP_ZOOM_DIR_RE.match(tif_path.parent.name)
        if zm:
            zoom = int(zm.group(1))
        tfw_path = tif_path.with_suffix(".tfw")
        if not tfw_path.exists():
            skipped.append({"path": tif_path, "skip_reason": "missing_tfw"})
            continue
        try:
            tfw = parse_tfw(tfw_path)
        except Exception as exc:  # noqa: BLE001 -- concurrent download, any parse failure is a skip
            skipped.append({"path": tif_path, "skip_reason": f"bad_tfw:{exc}"})
            continue
        try:
            with Image.open(tif_path) as img:
                img.load()
                img_w, img_h = img.size
        except Exception as exc:  # noqa: BLE001 -- partial/corrupt write mid-download
            skipped.append({"path": tif_path, "skip_reason": f"unreadable_tif:{exc}"})
            continue
        if img_w <= 0 or img_h <= 0:
            skipped.append({"path": tif_path, "skip_reason": "empty_image"})
            continue
        good.append({
            "path": tif_path, "tfw_path": tfw_path,
            "capture_ymd": int(m.group("capture")), "vintage_ymd": int(m.group("vintage")),
            "zoom": zoom, "tfw": tfw, "img_w": img_w, "img_h": img_h,
        })
    good.sort(key=lambda e: (e["capture_ymd"], e["vintage_ymd"]))
    return good, skipped


def pick_reference_basemap(stack: list[dict]) -> dict | None:
    """Latest capture at the highest zoom -- basemap96 stack's present-day proxy
    (mirrors ``audit_gehi_displacement.pick_reference`` for the new layout)."""
    if not stack:
        return None
    max_zoom = max(e["zoom"] for e in stack)
    cands = [e for e in stack if e["zoom"] == max_zoom]
    return max(cands, key=lambda e: e["capture_ymd"])


_WGS84_TO_MERC = None


def _wgs84_to_web_mercator():
    """Cached EPSG:4326 -> EPSG:3857 transform (matches the GEHI z19 chips)."""
    global _WGS84_TO_MERC
    if _WGS84_TO_MERC is None:
        import pyproj

        _WGS84_TO_MERC = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:3857", always_xy=True).transform
    return _WGS84_TO_MERC


def _basemap_tif_to_png(tif_path: Path, cache_dir: Path) -> Path:
    """TIF -> PNG transcode for the basemap96 stack: no crop, no marker overlay
    (the per-target tif IS the geometry; see ``geometry_version
    basemap96_z19_v1``). Writes into ``cache_dir`` (the pilot's own output
    tree) -- NEVER beside the source tif, since basemap_rebuild_2026-07-13 is
    a live download directory that must stay read-only while it is in flight.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    png_path = cache_dir / f"{tif_path.stem}.png"
    try:
        if (
            png_path.exists()
            and png_path.stat().st_size > 0
            and png_path.stat().st_mtime >= tif_path.stat().st_mtime
        ):
            return png_path
    except OSError:
        pass
    from PIL import Image

    with Image.open(tif_path) as img:
        img = img.convert("RGB") if img.mode != "RGB" else img.copy()
        img.save(png_path, format="PNG")
    return png_path


# --------------------------------------------------------------------------
# Small numerics shared by stages (kept dependency-light and unit-testable).
# --------------------------------------------------------------------------

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def rank_auc(pos: Sequence[float], neg: Sequence[float]) -> float:
    """Mann-Whitney AUC: P(score_pos > score_neg), ties counted 0.5."""
    pos_a = np.asarray(list(pos), dtype=np.float64)
    neg_a = np.asarray(list(neg), dtype=np.float64)
    if pos_a.size == 0 or neg_a.size == 0:
        return float("nan")
    wins = 0.0
    for p in pos_a:
        wins += float(np.sum(p > neg_a)) + 0.5 * float(np.sum(p == neg_a))
    return wins / (pos_a.size * neg_a.size)


def fit_whitener(tokens: np.ndarray, eps: float) -> dict[str, np.ndarray]:
    """PCA whitening (mean + rotation/scale) fit on [N, C] float tokens."""
    mu = tokens.mean(axis=0)
    x = tokens - mu
    cov = (x.T @ x) / max(1, x.shape[0] - 1)
    evals, evecs = np.linalg.eigh(cov)
    w = evecs @ np.diag(1.0 / np.sqrt(np.maximum(evals, 0.0) + eps)) @ evecs.T
    return {"mean": mu.astype(np.float32), "w": w.astype(np.float32)}


def apply_whitener(vec: np.ndarray, whitener: Mapping[str, np.ndarray] | None) -> np.ndarray:
    if whitener is None:
        return vec
    return (vec - whitener["mean"]) @ whitener["w"]


def _dilate(mask: np.ndarray, iterations: int) -> np.ndarray:
    if iterations <= 0:
        return mask.copy()
    from scipy.ndimage import binary_dilation

    return binary_dilation(mask, iterations=iterations)


def shift_mask(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Shift a boolean [G, G] mask without wrap-around."""
    out = np.zeros_like(mask)
    g = mask.shape[0]
    ys, xs = np.nonzero(mask)
    ys2, xs2 = ys + dy, xs + dx
    keep = (ys2 >= 0) & (ys2 < g) & (xs2 >= 0) & (xs2 < g)
    out[ys2[keep], xs2[keep]] = True
    return out


def region_mean(grid: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    """Mean token over masked positions of a [G, G, C] grid; None if empty."""
    if not mask.any():
        return None
    return grid[mask].astype(np.float32).mean(axis=0)


# --------------------------------------------------------------------------
# Decode math (prereg "Decode" section) — pure numpy, unit-tested.
# --------------------------------------------------------------------------

def _weighted_log_marginal(x: np.ndarray, w: np.ndarray, *, mu0: float,
                           kappa0: float, alpha0: float, beta0: float) -> float:
    """Log marginal likelihood of weighted Gaussian data under NIG prior.

    Weights scale precision: x_i ~ N(mu, sigma^2 / w_i). Standard conjugate
    update with effective counts n_eff = sum(w).
    """
    n_eff = float(np.sum(w))
    if n_eff <= 0.0:
        return 0.0
    xbar = float(np.sum(w * x) / n_eff)
    ss = float(np.sum(w * (x - xbar) ** 2))
    kappa_n = kappa0 + n_eff
    alpha_n = alpha0 + n_eff / 2.0
    beta_n = beta0 + 0.5 * ss + (kappa0 * n_eff * (xbar - mu0) ** 2) / (2.0 * kappa_n)
    return (
        math.lgamma(alpha_n) - math.lgamma(alpha0)
        + alpha0 * math.log(beta0) - alpha_n * math.log(beta_n)
        + 0.5 * (math.log(kappa0) - math.log(kappa_n))
        - (n_eff / 2.0) * math.log(2.0 * math.pi)
    )


def bayes_single_changepoint(
    s: np.ndarray,
    dates: np.ndarray,
    weights: np.ndarray | None = None,
    *,
    gap_prior_by_days: bool = True,
) -> dict[str, Any]:
    """Posterior over 'install falls in gap k' for a 1-D similarity series.

    s[t] is the ring-contrast similarity at ordinal date dates[t] (ints,
    YYYYMMDD converted to days elsewhere; here any increasing numeric axis).
    Hypotheses: tau in gap k (k = 0..T-2, meaning between frame k and k+1),
    plus H_nochange (single segment). Returns per-gap posterior, P(nochange),
    MAP gap, and cleanliness q = max gap posterior mass.
    """
    s = np.asarray(s, dtype=np.float64)
    t_n = s.size
    if weights is None:
        weights = np.ones_like(s)
    weights = np.asarray(weights, dtype=np.float64)
    if t_n < 3:
        return {"posterior": np.zeros(max(0, t_n - 1)), "p_nochange": 1.0,
                "map_gap": None, "q": 0.0}

    # Noise prior from first differences (robust to the step itself): for iid
    # noise var sigma^2, Var(diff) = 2 sigma^2; median-of-squares resists the
    # single step-sized diff. Full-series variance would flatten the posterior.
    diffs = np.diff(s)
    noise_var = max(float(np.median(diffs ** 2)) / 2.0 if diffs.size else 1e-6, 1e-8)
    prior = dict(mu0=float(np.mean(s)), kappa0=0.01, alpha0=2.0,
                 beta0=2.0 * noise_var)

    log_scores = []
    for k in range(t_n - 1):
        left = _weighted_log_marginal(s[: k + 1], weights[: k + 1], **prior)
        right = _weighted_log_marginal(s[k + 1:], weights[k + 1:], **prior)
        lp = left + right
        if gap_prior_by_days:
            gap_days = max(1.0, float(dates[k + 1] - dates[k]))
            lp += math.log(gap_days)
        log_scores.append(lp)
    log_nochange = _weighted_log_marginal(s, weights, **prior)
    if gap_prior_by_days:
        total_days = max(1.0, float(dates[-1] - dates[0]))
        log_nochange += math.log(total_days)

    all_lp = np.array(log_scores + [log_nochange])
    all_lp -= all_lp.max()
    probs = np.exp(all_lp)
    probs /= probs.sum()
    gap_post = probs[:-1]
    p_nochange = float(probs[-1])
    map_gap = int(np.argmax(gap_post)) if gap_post.size else None
    q = float(gap_post.max()) if gap_post.size else 0.0
    return {"posterior": gap_post, "p_nochange": p_nochange,
            "map_gap": map_gap, "q": q}


def glr_changepoint(s: np.ndarray) -> int | None:
    """Least-squares / GLR single-split argmax (cross-check estimator)."""
    s = np.asarray(s, dtype=np.float64)
    if s.size < 3:
        return None
    best_k, best_cost = None, float("inf")
    for k in range(1, s.size):
        left, right = s[:k], s[k:]
        cost = float(((left - left.mean()) ** 2).sum() + ((right - right.mean()) ** 2).sum())
        if cost < best_cost:
            best_cost, best_k = cost, k - 1  # gap index between k-1 and k
    return best_k


def isotonic_step(s: np.ndarray) -> int | None:
    """Best single non-decreasing step fit; equals GLR under low<high constraint."""
    k = glr_changepoint(s)
    if k is None:
        return None
    left_mean = float(s[: k + 1].mean())
    right_mean = float(s[k + 1:].mean())
    return k if right_mean > left_mean else None


def persistence_ok(s: np.ndarray, usable: np.ndarray, gap: int, n_required: int) -> bool:
    """Post-step regime must hold for >= n_required subsequent usable frames."""
    post = s[gap + 1:][usable[gap + 1:]]
    if post.size < n_required:
        return False
    pre = s[: gap + 1][usable[: gap + 1]]
    if pre.size == 0:
        return True
    mid = (float(np.median(pre)) + float(np.median(post))) / 2.0
    return bool(np.sum(post > mid) >= n_required)


# --------------------------------------------------------------------------
# Anchor selection (census nearest-frame both-sides rule).
# --------------------------------------------------------------------------

def select_anchor(
    dates: Sequence[int],       # days since epoch, ascending
    usable: Sequence[bool],
    t_c_days: int,
    *,
    delta_left_days: int,
    left_right_cos: float | None = None,
    theta_anchor: float | None = None,
) -> dict[str, Any]:
    """Implements the prereg's asymmetric nearest-frame anchor rule.

    Right side (t >= T_c): eligible at any distance. Left side: eligible only
    within delta_left_days. If a left frame wins and a right frame exists,
    the caller passes left_right_cos (footprint cosine between the two); when
    theta_anchor is set and the cosine misses it, we fall back to the right
    anchor and flag anchor_left_rejected.
    """
    idx_usable = [i for i, u in enumerate(usable) if u]
    rights = [i for i in idx_usable if dates[i] >= t_c_days]
    lefts = [i for i in idx_usable
             if dates[i] < t_c_days and (t_c_days - dates[i]) <= delta_left_days]
    candidates = rights + lefts
    if not candidates:
        return {"anchor_idx": None, "anchor_side": "none", "anchor_left_rejected": False}
    best = min(candidates, key=lambda i: abs(dates[i] - t_c_days))
    side = "right" if dates[best] >= t_c_days else "left"
    rejected = False
    if side == "left" and rights:
        if theta_anchor is not None and left_right_cos is not None \
                and left_right_cos < theta_anchor:
            best = min(rights, key=lambda i: abs(dates[i] - t_c_days))
            side, rejected = "right", True
    if side == "left" and not rights:
        side = "left_only"
    return {"anchor_idx": best, "anchor_side": side, "anchor_left_rejected": rejected}


# --------------------------------------------------------------------------
# Stage: embed
# --------------------------------------------------------------------------

def _build_scorer(arm: str, device: str):
    from scripts.temporal import dinov3_scorer as ds

    cls_name, backbone = ARMS[arm]
    cls = getattr(ds, cls_name)
    kwargs: dict[str, Any] = {"device": device}
    if backbone is not None:
        kwargs["backbone_model_id"] = backbone
        kwargs["input_size"] = 256
    return cls(**kwargs)


def _extract_grids(scorer, paths: list[str], facet: str, batch_size: int) -> np.ndarray:
    if facet == "token":
        return scorer.embed_patch_grids(paths, batch_size=batch_size)
    if facet == "key":
        return _embed_key_grids(scorer, paths, batch_size=batch_size)
    raise ValueError(f"unknown facet: {facet}")


def _embed_key_grids(scorer, paths: list[str], *, batch_size: int) -> np.ndarray:
    """Experimental key-facet arm: hook the last block's attn.qkv output.

    Fails loudly (with a pointer to --facet token) if the timm module layout
    is not the expected VisionTransformer blocks[-1].attn.qkv.
    """
    import torch

    scorer.embed_patch_grids(paths[:1], batch_size=1)  # force _ensure_model
    model = getattr(scorer, "_model", None) or getattr(scorer, "model", None)
    if model is None or not hasattr(model, "blocks"):
        raise RuntimeError(
            "key facet: cannot locate timm ViT blocks on the scorer; "
            "re-run with --facet token (prereg fallback arm)")
    qkv = model.blocks[-1].attn.qkv
    captured: list[torch.Tensor] = []

    def hook(_mod, _inp, out):
        captured.append(out.detach())

    handle = qkv.register_forward_hook(hook)
    try:
        grids = []
        n_prefix = model.num_prefix_tokens
        for start in range(0, len(paths), batch_size):
            captured.clear()
            batch = paths[start:start + batch_size]
            scorer.embed_patch_grids(batch, batch_size=len(batch))
            if not captured:
                raise RuntimeError("key facet: qkv hook captured nothing")
            out = captured[-1]                       # [B, N, 3C]
            c = out.shape[-1] // 3
            keys = out[..., c:2 * c][:, n_prefix:, :]  # [B, N_patch, C]
            g = int(round(math.sqrt(keys.shape[1])))
            grids.append(keys.reshape(keys.shape[0], g, g, c)
                         .to("cpu", dtype=torch.float16).numpy())
        return np.concatenate(grids, axis=0)
    finally:
        handle.remove()


def run_embed(args: argparse.Namespace, cfg: C0Config) -> None:
    if args.stack_format == "basemap96":
        _run_embed_basemap96(args, cfg)
    else:
        _run_embed_legacy(args, cfg)


def _run_embed_legacy(args: argparse.Namespace, cfg: C0Config) -> None:
    import pandas as pd
    from scripts.temporal.audit_gehi_displacement import enumerate_stack, pick_reference
    from scripts.temporal.chip_displacement import estimate_shift, to_gray
    from scripts.temporal.chip_geometry import resolve_chip_geometry
    from scripts.temporal.effective_resolution import normalized_gradient_energy
    from scripts.temporal.gehi_common import ReviewTargetMarker, ensure_single_target_review_png
    from PIL import Image

    geom = resolve_chip_geometry(cfg.geometry_version)
    targets = pd.read_csv(args.chip_targets)
    by_anchor = {str(r.anchor_id): r for r in targets.itertuples()}

    device = args.device or _auto_device()
    scorer = _build_scorer(args.arm, device)
    out_dir = Path(args.out) / "embeds" / f"{args.arm}_{cfg.facet}_{cfg.hash()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    chips_root = Path(args.chips_root)
    anchor_dirs = sorted(p for p in chips_root.iterdir() if p.is_dir())
    if args.limit:
        anchor_dirs = anchor_dirs[: args.limit]

    n_done = n_skip = 0
    for anchor_dir in anchor_dirs:
        anchor_id = anchor_dir.name
        row = by_anchor.get(anchor_id)
        if row is None:
            n_skip += 1
            continue
        npz_path = out_dir / f"{anchor_id}.npz"
        if npz_path.exists() and not args.force:
            continue
        stack = enumerate_stack(anchor_dir)
        if len(stack) < 3:
            n_skip += 1
            continue
        marker = ReviewTargetMarker(
            target_id=str(row.target_index), target_label=str(row.target_label),
            offset_x_m=float(row.target_offset_x_m), offset_y_m=float(row.target_offset_y_m),
            search_radius_m=float(row.search_radius_m))
        pngs, dates, zooms, versions = [], [], [], []
        for entry in stack:
            png = ensure_single_target_review_png(
                Path(entry["path"]), marker,
                chip_size_m=float(row.chip_size_m),
                crop_context_multiplier=geom.crop_context_multiplier,
                min_crop_size_m=geom.min_crop_size_m,
                min_output_px=geom.min_output_px,
                draw_marker=False,
                bbox_width_m=float(row.source_width_m),
                bbox_height_m=float(row.source_height_m))
            pngs.append(str(png))
            dates.append(int(entry["date"]))
            zooms.append(int(entry["zoom"]))
            versions.append(int(entry["version"]))

        grids = _extract_grids(scorer, pngs, cfg.facet, args.batch_size)

        arrays = [np.asarray(Image.open(p).convert("RGB")) for p in pngs]
        ref = pick_reference(stack)
        ref_i = next(i for i, e in enumerate(stack) if e["path"] == ref["path"])
        ref_gray = to_gray(arrays[ref_i])
        reg_dx, reg_dy, reg_psr, reg_ok = [], [], [], []
        for i, arr in enumerate(arrays):
            if i == ref_i:
                reg_dx.append(0.0); reg_dy.append(0.0); reg_psr.append(99.0); reg_ok.append(True)
                continue
            res = estimate_shift(ref_gray, to_gray(arr))
            reg_dx.append(res.dx_px); reg_dy.append(res.dy_px)
            reg_psr.append(res.psr); reg_ok.append(res.ok)
        grad_energy = [normalized_gradient_energy(to_gray(a)) for a in arrays]

        np.savez_compressed(
            npz_path, grids=grids,
            dates=np.array(dates, dtype=np.int64),
            zooms=np.array(zooms, dtype=np.int16),
            versions=np.array(versions, dtype=np.int32),
            png_paths=np.array(pngs), ref_index=np.int64(ref_i),
            reg_dx_px=np.array(reg_dx, np.float32), reg_dy_px=np.array(reg_dy, np.float32),
            reg_psr=np.array(reg_psr, np.float32), reg_ok=np.array(reg_ok, bool),
            grad_energy=np.array(grad_energy, np.float32))
        n_done += 1
    print(f"[embed] arm={args.arm} facet={cfg.facet} done={n_done} skipped={n_skip} -> {out_dir}")


def _run_embed_basemap96(args: argparse.Namespace, cfg: C0Config) -> None:
    """embed stage, basemap_rebuild_2026-07-13 per-target chips/<id>/z<zoom>/
    layout. No render-crop: each per-target tif IS the geometry (see
    ``geometry_version basemap96_z19_v1``); this only transcodes tif -> png
    (into the pilot's own output tree, never beside the source tif) and
    extracts patch grids + registration/quality metrics, same as legacy.
    """
    import pandas as pd
    from scripts.temporal.chip_displacement import estimate_shift, to_gray
    from scripts.temporal.effective_resolution import normalized_gradient_energy
    from PIL import Image

    targets = pd.read_csv(args.chip_targets)
    by_anchor = {str(r.anchor_id): r for r in targets.itertuples()}

    device = args.device or _auto_device()
    scorer = _build_scorer(args.arm, device)
    out_dir = Path(args.out) / "embeds" / f"{args.arm}_{cfg.facet}_{cfg.hash()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    png_cache_root = Path(args.out) / "png_cache" / "basemap96"

    chips_root = Path(args.chips_root)
    target_dirs = sorted(p for p in chips_root.iterdir() if p.is_dir())
    if args.limit:
        target_dirs = target_dirs[: args.limit]

    n_done = n_skip_no_row = n_skip_too_few = 0
    frame_skip_reasons: dict[str, int] = {}
    for target_dir in target_dirs:
        target_id = target_dir.name
        row = by_anchor.get(target_id)
        if row is None:
            n_skip_no_row += 1
            continue
        npz_path = out_dir / f"{target_id}.npz"
        if npz_path.exists() and not args.force:
            continue
        stack, skipped = enumerate_basemap_stack(target_dir)
        for s in skipped:
            frame_skip_reasons[s["skip_reason"]] = frame_skip_reasons.get(s["skip_reason"], 0) + 1
        if len(stack) < args.min_embed_frames:
            n_skip_too_few += 1
            continue

        pngs, dates, zooms, versions = [], [], [], []
        img_w_list, img_h_list, tfw_list = [], [], []
        for entry in stack:
            png = _basemap_tif_to_png(entry["path"], png_cache_root / target_id)
            pngs.append(str(png))
            dates.append(int(entry["capture_ymd"]))
            zooms.append(int(entry["zoom"]))
            versions.append(int(entry["vintage_ymd"]))
            img_w_list.append(int(entry["img_w"]))
            img_h_list.append(int(entry["img_h"]))
            tfw_list.append(entry["tfw"])

        grids = _extract_grids(scorer, pngs, cfg.facet, args.batch_size)

        arrays = [np.asarray(Image.open(p).convert("RGB")) for p in pngs]
        ref = pick_reference_basemap(stack)
        ref_i = next(i for i, e in enumerate(stack) if e["path"] == ref["path"])
        ref_gray = to_gray(arrays[ref_i])
        reg_dx, reg_dy, reg_psr, reg_ok = [], [], [], []
        for i, arr in enumerate(arrays):
            if i == ref_i:
                reg_dx.append(0.0); reg_dy.append(0.0); reg_psr.append(99.0); reg_ok.append(True)
                continue
            res = estimate_shift(ref_gray, to_gray(arr))
            reg_dx.append(res.dx_px); reg_dy.append(res.dy_px)
            reg_psr.append(res.psr); reg_ok.append(res.ok)
        grad_energy = [normalized_gradient_energy(to_gray(a)) for a in arrays]

        np.savez_compressed(
            npz_path, grids=grids,
            dates=np.array(dates, dtype=np.int64),
            zooms=np.array(zooms, dtype=np.int16),
            versions=np.array(versions, dtype=np.int32),
            png_paths=np.array(pngs), ref_index=np.int64(ref_i),
            reg_dx_px=np.array(reg_dx, np.float32), reg_dy_px=np.array(reg_dy, np.float32),
            reg_psr=np.array(reg_psr, np.float32), reg_ok=np.array(reg_ok, bool),
            grad_energy=np.array(grad_energy, np.float32),
            stack_format=np.array("basemap96"),
            input_size=np.int64(scorer.input_size),
            img_w_px=np.array(img_w_list, dtype=np.int32),
            img_h_px=np.array(img_h_list, dtype=np.int32),
            tfw_a=np.array([t.a for t in tfw_list], dtype=np.float64),
            tfw_d=np.array([t.d for t in tfw_list], dtype=np.float64),
            tfw_b=np.array([t.b for t in tfw_list], dtype=np.float64),
            tfw_e=np.array([t.e for t in tfw_list], dtype=np.float64),
            tfw_c=np.array([t.c for t in tfw_list], dtype=np.float64),
            tfw_f=np.array([t.f for t in tfw_list], dtype=np.float64))
        n_done += 1
    print(f"[embed:basemap96] arm={args.arm} facet={cfg.facet} done={n_done} "
          f"skip_no_chip_targets_row={n_skip_no_row} skip_too_few_frames={n_skip_too_few} "
          f"(min={args.min_embed_frames}) frame_skip_reasons={frame_skip_reasons} -> {out_dir}")


def _auto_device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


# --------------------------------------------------------------------------
# Stage: curve
# --------------------------------------------------------------------------

def _ymd_to_days(ymd: int) -> int:
    from datetime import date

    y, m, d = ymd // 10000, (ymd // 100) % 100, ymd % 100
    return date(y, m, d).toordinal()


def _footprint_mask(row: Any, grid_side: int, crop_size_m: float) -> tuple[np.ndarray, str]:
    """Project the census PV polygon into the [G, G] patch grid.

    Falls back to a centred disk of search_radius_m when the source GPKG is
    unavailable (footprint_source records which path was taken).
    """
    patch_m = crop_size_m / grid_side
    centre = (grid_side - 1) / 2.0
    gpkg = getattr(row, "source_inventory_path", None)
    try:
        polygon = _load_polygon(gpkg, int(row.source_feature_id))
    except Exception:
        polygon = None
    if polygon is not None:
        import pyproj
        from shapely.ops import transform as shp_transform

        lat0, lon0 = _crop_centre_lonlat(row)
        proj = pyproj.Transformer.from_crs(
            "EPSG:4326", f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +units=m",
            always_xy=True).transform
        poly_m = shp_transform(proj, polygon)
        from shapely.geometry import Point

        mask = np.zeros((grid_side, grid_side), dtype=bool)
        for gy in range(grid_side):
            for gx in range(grid_side):
                x_m = (gx - centre) * patch_m
                y_m = (centre - gy) * patch_m  # row 0 = north
                if poly_m.contains(Point(x_m, y_m)):
                    mask[gy, gx] = True
        if mask.any():
            return mask, "census_polygon"
    radius_p = max(1.0, float(row.search_radius_m) / patch_m)
    yy, xx = np.mgrid[0:grid_side, 0:grid_side]
    mask = (yy - centre) ** 2 + (xx - centre) ** 2 <= radius_p ** 2
    return mask, "radius_disk"


def _crop_centre_lonlat(row: Any) -> tuple[float, float]:
    lat_c = (float(row.chip_lat_min) + float(row.chip_lat_max)) / 2.0
    lon_c = (float(row.chip_lon_min) + float(row.chip_lon_max)) / 2.0
    lat = lat_c + float(row.target_offset_y_m) / 111_320.0
    lon = lon_c + float(row.target_offset_x_m) / (111_320.0 * math.cos(math.radians(lat_c)))
    return lat, lon


_POLY_CACHE: dict[str, Any] = {}


def _load_polygon(gpkg_path: str | None, feature_idx: int):
    if not gpkg_path:
        return None
    key = str(gpkg_path)
    if key not in _POLY_CACHE:
        import geopandas as gpd

        _POLY_CACHE[key] = gpd.read_file(gpkg_path, layer="solar_predictions")
    gdf = _POLY_CACHE[key]
    if feature_idx < 0 or feature_idx >= len(gdf):
        return None
    return gdf.geometry.iloc[feature_idx]


def _realized_crop_size_m(row: Any, geom) -> float:
    from scripts.temporal.chip_geometry import contain_crop_to_footprint

    raw = min(float(row.chip_size_m),
              max(geom.min_crop_size_m,
                  2.0 * float(row.search_radius_m) * geom.crop_context_multiplier))
    return contain_crop_to_footprint(
        raw, source_width_m=float(row.source_width_m),
        source_height_m=float(row.source_height_m),
        chip_size_m=float(row.chip_size_m))


def _project_polygon_to_grid(
    poly_map: Any, tfw: TfwAffine, grid_side: int,
    img_w_px: int, img_h_px: int, input_size: int,
) -> np.ndarray:
    """Patch-center-sampled containment mask for a polygon already in the
    TFW's own map CRS (EPSG:3857 for GEHI). Pure grid/affine math, no I/O --
    kept separate from ``_footprint_mask_tfw`` so it is unit-testable without
    a real GPKG or a real WGS84->3857 transform.

    Each patch's center is mapped through the SAME resize the scorer applies
    (``img.resize((input_size, input_size))``, which stretches non-uniformly
    when ``img_w_px != img_h_px``) before the TFW converts it back to map
    coordinates -- so the projection is exact even though the token grid is
    forced square over a non-square source chip.
    """
    from shapely.geometry import Point

    patch_px_resized = input_size / grid_side
    sx = img_w_px / input_size   # original px per resized px, x axis
    sy = img_h_px / input_size   # original px per resized px, y axis
    mask = np.zeros((grid_side, grid_side), dtype=bool)
    for gy in range(grid_side):
        for gx in range(grid_side):
            col = (gx + 0.5) * patch_px_resized * sx
            row_px = (gy + 0.5) * patch_px_resized * sy
            x, y = tfw.to_map(col, row_px)
            if poly_map.contains(Point(x, y)):
                mask[gy, gx] = True
    return mask


_CRS_TO_MERC_CACHE: dict[str, Any] = {}


def _to_web_mercator(crs: Any):
    """Cached ``crs`` -> EPSG:3857 transform for an arbitrary source CRS.

    2026-07-13 finding: the census polygon GPKG's ``solar_predictions`` layer
    (``jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01.gpkg``) is stored
    in EPSG:32735 (UTM 35S), NOT EPSG:4326 -- ``geopandas.read_file(...).crs``
    confirms it. The legacy ``_footprint_mask`` hardcodes an EPSG:4326 source
    CRS for this same GPKG (see its ``pyproj.Transformer.from_crs("EPSG:4326"
    , ...)`` call), so it silently mis-transforms the polygon on every real
    target (garbage/empty geometry -> always falls back to ``radius_disk``);
    left untouched here (out of this adapter's scope; legacy already stays
    on its own aeqd path unconditionally either way), but this function reads
    the GPKG's OWN ``.crs`` instead of assuming EPSG:4326.
    """
    key = str(crs)
    if key not in _CRS_TO_MERC_CACHE:
        import pyproj

        _CRS_TO_MERC_CACHE[key] = pyproj.Transformer.from_crs(
            crs, "EPSG:3857", always_xy=True).transform
    return _CRS_TO_MERC_CACHE[key]


def _footprint_mask_tfw(
    row: Any, tfw: TfwAffine, grid_side: int,
    img_w_px: int, img_h_px: int, input_size: int,
) -> tuple[np.ndarray, str]:
    """Exact TFW-projected footprint mask for the basemap96 stack format.

    Projects the census polygon (its OWN native CRS, per the GPKG -- see
    ``_to_web_mercator``) -> EPSG:3857 -> this frame's own TFW affine -> the
    patch-token grid (``_project_polygon_to_grid``). This replaces
    ``_footprint_mask``'s aeqd approximation + positional GPKG join for
    basemap96 frames, since the TFW is the frame's REAL georeferencing, not
    an approximation centred on an assumed crop.

    Falls back to a disk centred on the target's own centroid (``row.
    centroid_lon``/``centroid_lat``, genuinely WGS84 degrees per
    chip_targets.csv -- NOT the shared chip-group download box's centre,
    which may not even contain this target when several targets share one
    chip-group tif) when the polygon is unavailable.
    """
    gpkg = getattr(row, "source_inventory_path", None)
    try:
        polygon = _load_polygon(gpkg, int(row.source_feature_id))
    except Exception:
        polygon = None

    if polygon is not None:
        from shapely.ops import transform as shp_transform

        cached_gdf = _POLY_CACHE.get(str(gpkg))
        src_crs = cached_gdf.crs if cached_gdf is not None and cached_gdf.crs else "EPSG:4326"
        poly_merc = shp_transform(_to_web_mercator(src_crs), polygon)
        mask = _project_polygon_to_grid(poly_merc, tfw, grid_side, img_w_px, img_h_px, input_size)
        if mask.any():
            return mask, "tfw_polygon"

    lat = float(row.centroid_lat)
    cx, cy = _wgs84_to_web_mercator()(float(row.centroid_lon), lat)
    col_c, row_c = tfw.to_pixel(cx, cy)
    # EPSG:3857 units are Web-Mercator meters (isotropic in a/e, verified
    # a == |e| exactly for GEHI z19); scale by cos(lat) for TRUE ground m.
    ground_res_m = abs(tfw.a) * math.cos(math.radians(lat))
    radius_px_orig = max(1.0, float(row.search_radius_m) / max(ground_res_m, 1e-9))
    patch_px_resized = input_size / grid_side
    sx = img_w_px / input_size
    sy = img_h_px / input_size
    mask = np.zeros((grid_side, grid_side), dtype=bool)
    for gy in range(grid_side):
        for gx in range(grid_side):
            col = (gx + 0.5) * patch_px_resized * sx
            row_px = (gy + 0.5) * patch_px_resized * sy
            if (col - col_c) ** 2 + (row_px - row_c) ** 2 <= radius_px_orig ** 2:
                mask[gy, gx] = True
    return mask, "tfw_radius_disk"


def _resolve_frame_geometry(
    z: Mapping[str, np.ndarray], row: Any, grid_side: int, geom,
) -> tuple[np.ndarray, str, float, float]:
    """Dispatch fp_mask/patch_m/png_px_m by inspecting the npz's OWN content
    (not a CLI flag) -- self-describing artifacts, so a curve run is correct
    even if ``--stack-format`` is forgotten (only the config-hash/artifact
    tag can then mismatch, which fails closed at the embed-dir lookup).

    Returns ``(fp_mask, fp_source, patch_m, png_px_m)``:
    ``patch_m`` = metres per patch for the shift-search / reg_disagree_m
    scalar consumers in ``compute_curve``; for basemap96, x/y ground
    resolution is identical (Web-Mercator, isotropic) but the patch grid is
    forced square over a non-square chip, so patch_m is the geometric mean
    of the anisotropic patch_m_x/patch_m_y -- an approximation ONLY for
    that scalar shift-search consumer, never for the footprint mask itself
    (which uses the full per-axis affine, exact).
    """
    if "tfw_a" in z.files:
        input_size = int(z["input_size"])
        img_w = int(z["img_w_px"][0])
        img_h = int(z["img_h_px"][0])
        tfw = TfwAffine(a=float(z["tfw_a"][0]), d=float(z["tfw_d"][0]), b=float(z["tfw_b"][0]),
                        e=float(z["tfw_e"][0]), c=float(z["tfw_c"][0]), f=float(z["tfw_f"][0]))
        fp_mask, fp_source = _footprint_mask_tfw(row, tfw, grid_side, img_w, img_h, input_size)
        lat = float(row.centroid_lat)
        ground_res_m = abs(tfw.a) * math.cos(math.radians(lat))
        patch_px_resized = input_size / grid_side
        patch_m_x = patch_px_resized * (img_w / input_size) * ground_res_m
        patch_m_y = patch_px_resized * (img_h / input_size) * ground_res_m
        patch_m = math.sqrt(max(patch_m_x, 1e-9) * max(patch_m_y, 1e-9))
        png_px_m = ground_res_m  # registration ran on the untresized PNG (original px)
        return fp_mask, fp_source, patch_m, png_px_m

    crop_m = _realized_crop_size_m(row, geom)
    patch_m = crop_m / grid_side
    png_px_m = crop_m / geom.min_output_px
    fp_mask, fp_source = _footprint_mask(row, grid_side, crop_m)
    return fp_mask, fp_source, patch_m, png_px_m


def _realized_crop_extent_m(z: Mapping[str, np.ndarray], row: Any) -> tuple[float, float] | None:
    """basemap96 only: the frame's actual (width_m, height_m) from its TFW +
    pixel size -- the realized geometry, since ``chip_size_m`` (96.0) in
    chip_targets.csv is nominal and the tile-snapped download is usually
    somewhat larger (see prereg amendment 2026-07-13)."""
    if "tfw_a" not in z.files:
        return None
    lat = float(row.centroid_lat)
    ground_res_m = abs(float(z["tfw_a"][0])) * math.cos(math.radians(lat))
    w_m = float(z["img_w_px"][0]) * ground_res_m
    h_m = float(z["img_h_px"][0]) * ground_res_m
    return w_m, h_m


def compute_curve(
    grids: np.ndarray, dates_days: np.ndarray, fp_mask: np.ndarray,
    cfg: C0Config, anchor_idx: int,
    whitener: Mapping[str, np.ndarray] | None,
    patch_m: float, reg_dx_px: np.ndarray, reg_dy_px: np.ndarray, png_px_m: float,
) -> dict[str, Any]:
    """Template + shift-searched ring-contrast similarity for one anchor."""
    tmpl_mask = _dilate(fp_mask, cfg.template_dilate_patches)
    ring = _dilate(fp_mask, cfg.ring_outer_patches) & ~_dilate(fp_mask, cfg.ring_inner_patches)
    template = apply_whitener(region_mean(grids[anchor_idx], tmpl_mask), whitener)

    r = cfg.shift_radius_patches
    n = grids.shape[0]
    s = np.zeros(n); s0 = np.zeros(n)
    cos_fp = np.zeros(n); cos_ring = np.zeros(n)
    sh_dy = np.zeros(n, int); sh_dx = np.zeros(n, int)
    reg_suspect = np.zeros(n, bool)
    for t in range(n):
        grid = grids[t]
        best = (-2.0, 0, 0)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                f = region_mean(grid, shift_mask(fp_mask, dy, dx))
                if f is None:
                    continue
                c = cosine(apply_whitener(f, whitener), template)
                if c > best[0]:
                    best = (c, dy, dx)
        c_fp, dy, dx = best
        rg = region_mean(grid, shift_mask(ring, dy, dx))
        c_rg = cosine(apply_whitener(rg, whitener), template) if rg is not None else 0.0
        s[t] = c_fp - c_rg
        cos_fp[t], cos_ring[t] = c_fp, c_rg
        sh_dy[t], sh_dx[t] = dy, dx
        # delta=0 ablation
        f0 = region_mean(grid, fp_mask)
        r0 = region_mean(grid, ring)
        c0f = cosine(apply_whitener(f0, whitener), template) if f0 is not None else 0.0
        c0r = cosine(apply_whitener(r0, whitener), template) if r0 is not None else 0.0
        s0[t] = c0f - c0r
        # cross-check vs phase correlation (both in metres)
        feat_dx_m, feat_dy_m = dx * patch_m, dy * patch_m
        pc_dx_m, pc_dy_m = reg_dx_px[t] * png_px_m, reg_dy_px[t] * png_px_m
        if math.hypot(feat_dx_m - pc_dx_m, feat_dy_m - pc_dy_m) > cfg.reg_disagree_m:
            reg_suspect[t] = True
    return {"s": s, "s_noshift": s0, "cos_fp": cos_fp, "cos_ring": cos_ring,
            "shift_dy": sh_dy, "shift_dx": sh_dx, "reg_suspect": reg_suspect}


def frame_weights(grad_energy: np.ndarray, reg_psr: np.ndarray,
                  reg_suspect: np.ndarray) -> np.ndarray:
    """Inverse-variance weights from quality heuristics + registration trust."""
    ge = grad_energy / max(1e-9, float(np.median(grad_energy)))
    psr = np.clip(reg_psr / 12.0, 0.0, 1.0)
    w = np.clip(ge, 0.2, 2.0) * np.clip(psr, 0.2, 1.0)
    w[reg_suspect] *= 0.5
    return w


def run_curve(args: argparse.Namespace, cfg: C0Config) -> None:
    import pandas as pd
    from scripts.temporal.chip_geometry import resolve_chip_geometry

    geom = resolve_chip_geometry(cfg.geometry_version)
    targets = pd.read_csv(args.chip_targets)
    by_anchor = {str(r.anchor_id): r for r in targets.itertuples()}
    t_c_days = _ymd_to_days(int(args.census_date.replace("-", "")))

    embed_dir = Path(args.out) / "embeds" / f"{args.arm}_{cfg.facet}_{cfg.hash()}"
    npzs = sorted(embed_dir.glob("*.npz"))
    if not npzs:
        raise SystemExit(f"[curve] no embeds under {embed_dir}; run --stage embed first")

    whitener = None
    if args.whitener:
        wz = np.load(args.whitener)
        whitener = {"mean": wz["mean"], "w": wz["w"]}
    elif args.fit_whitener:
        whitener = _fit_whitener_from_embeds(npzs, by_anchor, geom, cfg)
        wpath = Path(args.out) / f"whitener_{args.arm}_{cfg.facet}_{cfg.hash()}.npz"
        np.savez_compressed(wpath, **whitener)
        print(f"[curve] whitener fitted -> {wpath}")

    long_rows, anchor_rows = [], []
    for npz_path in npzs:
        anchor_id = npz_path.stem
        row = by_anchor.get(anchor_id)
        if row is None:
            continue
        z = np.load(npz_path, allow_pickle=False)
        grids = z["grids"].astype(np.float32)
        dates_days = np.array([_ymd_to_days(int(d)) for d in z["dates"]])
        grid_side = grids.shape[1]

        usable = (z["grad_energy"] >= args.min_grad_energy) & z["reg_ok"]
        fp_mask, fp_source, patch_m, png_px_m = _resolve_frame_geometry(z, row, grid_side, geom)
        realized_extent = _realized_crop_extent_m(z, row)

        # Pass 1 (no gate) to learn which side wins; if left wins and a right
        # frame exists, compute the anchor-consistency cosine and re-select
        # with the gate armed (theta_anchor=None keeps it record-only).
        sel = select_anchor(dates_days.tolist(), usable.tolist(), t_c_days,
                            delta_left_days=cfg.delta_left_days)
        lr_cos = None
        if sel["anchor_idx"] is not None and sel["anchor_side"].startswith("left"):
            rights = [i for i in range(len(dates_days))
                      if dates_days[i] >= t_c_days and usable[i]]
            if rights:
                j = min(rights, key=lambda i: abs(dates_days[i] - t_c_days))
                fa = region_mean(grids[sel["anchor_idx"]], fp_mask)
                fb = region_mean(grids[j], fp_mask)
                if fa is not None and fb is not None:
                    lr_cos = cosine(apply_whitener(fa, whitener),
                                    apply_whitener(fb, whitener))
            sel = select_anchor(dates_days.tolist(), usable.tolist(), t_c_days,
                                delta_left_days=cfg.delta_left_days,
                                left_right_cos=lr_cos,
                                theta_anchor=cfg.theta_anchor)
        if sel["anchor_idx"] is None:
            anchor_rows.append({"anchor_id": anchor_id, "anchor_side": "none",
                                "fp_source": fp_source,
                                "realized_crop_w_m": realized_extent[0] if realized_extent else None,
                                "realized_crop_h_m": realized_extent[1] if realized_extent else None})
            continue
        a_idx = sel["anchor_idx"]

        cur = compute_curve(grids, dates_days, fp_mask, cfg, a_idx, whitener,
                            patch_m, z["reg_dx_px"], z["reg_dy_px"], png_px_m)
        w = frame_weights(z["grad_energy"], z["reg_psr"], cur["reg_suspect"])

        anchor_rows.append({
            "anchor_id": anchor_id, "anchor_side": sel["anchor_side"],
            "anchor_left_rejected": sel["anchor_left_rejected"],
            "anchor_date": int(z["dates"][a_idx]), "census_date": args.census_date,
            "left_right_cos": lr_cos, "fp_source": fp_source,
            "n_frames": int(len(dates_days)), "n_usable": int(usable.sum()),
            "source_area_m2": float(row.source_area_m2),
            "realized_crop_w_m": realized_extent[0] if realized_extent else None,
            "realized_crop_h_m": realized_extent[1] if realized_extent else None,
        })
        for t in range(len(dates_days)):
            long_rows.append({
                "anchor_id": anchor_id, "capture_date": int(z["dates"][t]),
                "s": float(cur["s"][t]), "s_noshift": float(cur["s_noshift"][t]),
                "cos_fp": float(cur["cos_fp"][t]), "cos_ring": float(cur["cos_ring"][t]),
                "shift_dy": int(cur["shift_dy"][t]), "shift_dx": int(cur["shift_dx"][t]),
                "reg_suspect": bool(cur["reg_suspect"][t]),
                "usable": bool(usable[t]), "weight": float(w[t]),
                "is_anchor": t == a_idx,
            })

    tag = f"{args.arm}_{cfg.facet}_{cfg.hash()}"
    curves_path = Path(args.out) / f"curves_{tag}.csv"
    anchors_path = Path(args.out) / f"curve_anchors_{tag}.csv"
    pd.DataFrame(long_rows).to_csv(curves_path, index=False)
    pd.DataFrame(anchor_rows).to_csv(anchors_path, index=False)
    _write_lock(Path(args.out), tag, [curves_path, anchors_path], cfg)
    print(f"[curve] {len(anchor_rows)} anchors -> {curves_path}")


def _fit_whitener_from_embeds(npzs, by_anchor, geom, cfg: C0Config) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(20_260_712)
    samples = []
    budget = cfg.whiten_max_tokens
    for npz_path in npzs:
        row = by_anchor.get(npz_path.stem)
        if row is None or budget <= 0:
            continue
        z = np.load(npz_path, allow_pickle=False)
        grids = z["grids"].astype(np.float32)
        g = grids.shape[1]
        fp, _, _, _ = _resolve_frame_geometry(z, row, g, geom)
        region = _dilate(fp, cfg.ring_outer_patches)
        toks = grids[:, region, :].reshape(-1, grids.shape[-1])
        take = min(len(toks), 400)
        idx = rng.choice(len(toks), size=take, replace=False)
        samples.append(toks[idx])
        budget -= take
    tokens = np.concatenate(samples, axis=0)
    return fit_whitener(tokens, cfg.whiten_eps)


def _write_lock(out: Path, tag: str, files: list[Path], cfg: C0Config) -> None:
    entries = {}
    for f in files:
        entries[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    lock = {"tag": tag, "config": dataclasses.asdict(cfg), "files": entries}
    (out / f"lock_{tag}.json").write_text(json.dumps(lock, indent=2))


# --------------------------------------------------------------------------
# Stage: decode
# --------------------------------------------------------------------------

def run_decode(args: argparse.Namespace, cfg: C0Config) -> None:
    import pandas as pd

    tag = f"{args.arm}_{cfg.facet}_{cfg.hash()}"
    curves = pd.read_csv(Path(args.out) / f"curves_{tag}.csv")
    rows = []
    for anchor_id, g in curves.groupby("anchor_id"):
        g = g.sort_values("capture_date")
        u = g[g["usable"]]
        if len(u) < 3:
            rows.append({"anchor_id": anchor_id, "verdict": "insufficient_frames"})
            continue
        s = u["s"].to_numpy()
        w = u["weight"].to_numpy()
        days = np.array([_ymd_to_days(int(d)) for d in u["capture_date"]])
        post = bayes_single_changepoint(s, days, w, gap_prior_by_days=cfg.gap_prior_by_days)
        glr = glr_changepoint(s)
        iso = isotonic_step(s)
        k = post["map_gap"]
        entry: dict[str, Any] = {
            "anchor_id": anchor_id, "q": post["q"], "p_nochange": post["p_nochange"],
            "map_gap": k, "glr_gap": glr, "iso_gap": iso,
            "estimators_agree": (k is not None and k == glr and k == iso),
        }
        if post["p_nochange"] >= 0.5 or k is None:
            level = float(np.average(s, weights=w))
            entry["verdict"] = ("present_before_window" if level > 0
                                else "no_change_detected")
        else:
            ok = persistence_ok(s, np.ones(len(s), bool), k, cfg.persistence_frames)
            entry["verdict"] = "interval" if ok else "step_not_persistent"
            entry["interval_start"] = int(u["capture_date"].iloc[k])
            entry["interval_end"] = int(u["capture_date"].iloc[k + 1])
        entry["clean"] = bool(entry.get("verdict") == "interval"
                              and post["q"] >= cfg.q_clean
                              and entry["estimators_agree"])
        rows.append(entry)
    out_path = Path(args.out) / f"decoded_{tag}.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)
    n_clean = sum(1 for r in rows if r.get("clean"))
    print(f"[decode] {len(rows)} anchors, clean={n_clean} -> {out_path}")


# --------------------------------------------------------------------------
# Stage: smoke (Stage-0 GO/KILL, legacy labels as noisy indicative reference)
# --------------------------------------------------------------------------

def _labels_from_scan_states(scan_dir: Path, min_confidence: float) -> dict[str, dict[int, bool]]:
    """Legacy label source: scan_states done_appears + confidence gate.

    Dead post-basemap-rebuild (the dirs were wiped 2026-07-13) but kept for
    any pre-rebuild artifact that still has scan_states on disk.
    """
    from scripts.temporal.scan_state import load_scan_state

    if not scan_dir.is_dir():
        raise SystemExit(
            f"[smoke] scan-states dir not found: {scan_dir} — the legacy dir may "
            "have been wiped by the basemap rebuild; pass --labels-csv instead "
            "(see prereg amendment 2026-07-13)")
    labels: dict[str, dict[int, bool]] = {}
    for state_path in sorted(scan_dir.glob("*.json")):
        try:
            state = load_scan_state(state_path)
        except Exception:
            continue
        if getattr(state, "status", None) != "done_appears":
            continue
        per_anchor: dict[int, bool] = {}
        for obs in state.usable_observations():
            conf = obs.confidence if obs.confidence is not None else 0.0
            if conf >= min_confidence:
                per_anchor[int(str(obs.capture_date).replace("-", ""))] = bool(obs.pv_present)
        if per_anchor:
            labels[state_path.stem] = per_anchor
    return labels


def _labels_from_csv(path: Path) -> dict[str, dict[int, bool]]:
    """Manual-annotation smoke labels (amendment 2026-07-13): target_id,
    frame_date,label with label in {present, absent, unsure}. ``unsure``
    rows are excluded. Malformed rows raise (fail-closed) rather than being
    silently skipped -- this is a small hand-curated file where a silent
    skip would hide a typo class rather than surface it.
    """
    import csv as _csv

    if not path.is_file():
        raise SystemExit(f"[smoke] labels CSV not found: {path}")
    labels: dict[str, dict[int, bool]] = {}
    with path.open(newline="") as f:
        reader = _csv.DictReader(f)
        missing = {"target_id", "frame_date", "label"} - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"[smoke] labels CSV missing columns: {sorted(missing)}")
        for i, r in enumerate(reader):
            label = (r["label"] or "").strip().lower()
            if label in ("", "unsure"):
                continue
            if label not in ("present", "absent"):
                raise SystemExit(
                    f"[smoke] labels CSV row {i}: bad label {r['label']!r} "
                    "(expected present|absent|unsure)")
            ymd = int(r["frame_date"].replace("-", ""))
            labels.setdefault(r["target_id"], {})[ymd] = (label == "present")
    return labels


def run_smoke(args: argparse.Namespace, cfg: C0Config) -> None:
    import pandas as pd

    tag = f"{args.arm}_{cfg.facet}_{cfg.hash()}"
    curves = pd.read_csv(Path(args.out) / f"curves_{tag}.csv")
    anchors = pd.read_csv(Path(args.out) / f"curve_anchors_{tag}.csv")
    area_by_anchor = dict(zip(anchors["anchor_id"], anchors.get("source_area_m2", np.nan)))

    if args.labels_csv:
        labels_by_anchor = _labels_from_csv(Path(args.labels_csv))
        label_source = "manual_csv_2026_07_13"
    else:
        labels_by_anchor = _labels_from_scan_states(Path(args.scan_states), args.min_confidence)
        label_source = "legacy_scan_states"

    pos, neg = [], []                     # (value, area) tuples
    pos0, neg0 = [], []                   # delta=0 ablation
    n_anchors = 0
    for anchor_id, labels in labels_by_anchor.items():
        if len(labels) < 3 or len(set(labels.values())) < 2:
            continue
        g = curves[curves["anchor_id"] == anchor_id]
        if g.empty:
            continue
        n_anchors += 1
        area = area_by_anchor.get(anchor_id, float("nan"))
        for _, r in g[g["usable"]].iterrows():
            lab = labels.get(int(r["capture_date"]))
            if lab is None:
                continue
            (pos if lab else neg).append((float(r["s"]), area))
            (pos0 if lab else neg0).append((float(r["s_noshift"]), area))

    if n_anchors < cfg.smoke_min_anchors:
        print(f"[smoke] WARNING: only {n_anchors} transition anchors "
              f"(prereg requires >= {cfg.smoke_min_anchors})")

    def _stratify(pairs, lo, hi):
        return [v for v, a in pairs if not math.isnan(a) and lo <= a < hi]

    areas = sorted(a for _, a in pos + neg if not math.isnan(a))
    t1 = areas[len(areas) // 3] if areas else float("nan")
    t2 = areas[2 * len(areas) // 3] if areas else float("nan")

    metrics = {
        "c0_smoke_auc": rank_auc([v for v, _ in pos], [v for v, _ in neg]),
        "c0_smoke_auc_noshift": rank_auc([v for v, _ in pos0], [v for v, _ in neg0]),
        "c0_smoke_auc_small": rank_auc(_stratify(pos, 0, t1), _stratify(neg, 0, t1)),
        "c0_smoke_auc_medium": rank_auc(_stratify(pos, t1, t2), _stratify(neg, t1, t2)),
        "c0_smoke_auc_large": rank_auc(_stratify(pos, t2, float("inf")),
                                       _stratify(neg, t2, float("inf"))),
        "c0_smoke_n_anchors": n_anchors,
        "c0_smoke_n_pos_frames": len(pos), "c0_smoke_n_neg_frames": len(neg),
        "c0_smoke_area_tertiles_m2": [t1, t2],
        "c0_smoke_auc_go_bar": cfg.smoke_auc_go,
        "c0_smoke_min_anchors_bar": cfg.smoke_min_anchors,
        "c0_smoke_label_source": label_source,
        "arm": args.arm, "facet": cfg.facet, "config_hash": cfg.hash(),
    }
    out_path = Path(args.out) / f"smoke_metrics_{tag}.json"
    out_path.write_text(json.dumps(metrics, indent=2, default=float))
    print(json.dumps(metrics, indent=2, default=float))

    from scripts.validation.check_student_path_gate import check_c0_s0

    ok, lines = check_c0_s0({k: v for k, v in metrics.items()
                             if not isinstance(v, (list, str))})
    print("\n".join(lines))
    print(f"[smoke] metrics -> {out_path}")


# --------------------------------------------------------------------------
# Stage: label_template (amendment 2026-07-13 — smoke label source)
#
# Draws a fixed-seed sample of target ids from the pilot2023 candidates list
# for manual annotation, and writes both the fill-in template and the
# "reserved" target-id list. The reservation is CREATED by this draw (there
# was no pre-existing main-eval/smoke split to consume — pilot2023's anchor
# set is identical to full's, see the amendment) -- once drawn, these ids
# must be excluded from the main-eval target pool to keep the annotation
# disjoint from main eval (zero-Gemini-overlap blind-lock).
# --------------------------------------------------------------------------

def sample_label_template_targets(candidates_csv: Path, *, n: int, seed: int) -> list[str]:
    """Deterministic sample of ``n`` distinct target ids from a candidates CSV.

    Uses ``random.Random(seed)`` over the SORTED distinct ``anchor_id`` set
    so the draw is reproducible independent of the source CSV's row order.
    """
    import csv as _csv
    import random

    anchor_ids: set[str] = set()
    with candidates_csv.open(newline="") as f:
        for r in _csv.DictReader(f):
            anchor_ids.add(r["anchor_id"])
    pool = sorted(anchor_ids)
    rng = random.Random(seed)
    k = min(n, len(pool))
    return sorted(rng.sample(pool, k))


def emit_label_template(
    candidates_csv: Path, out_csv: Path, *, n: int, seed: int,
    reserved_out: Path | None = None,
) -> Path:
    """Write a ready-to-fill smoke-label annotation template: one row per
    (target_id, available capture_date) in the sample, ``label`` left blank
    for the annotator (present/absent/unsure, per ``_labels_from_csv``).
    Optionally also writes the sampled target-id list (the main-eval
    reservation) to ``reserved_out``.
    """
    import csv as _csv

    sample = set(sample_label_template_targets(candidates_csv, n=n, seed=seed))
    rows_by_target: dict[str, set[str]] = {}
    with candidates_csv.open(newline="") as f:
        for r in _csv.DictReader(f):
            if r["anchor_id"] in sample:
                rows_by_target.setdefault(r["anchor_id"], set()).add(r["capture_date"])

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["target_id", "frame_date", "label"])
        for target_id in sorted(rows_by_target):
            for frame_date in sorted(rows_by_target[target_id]):
                w.writerow([target_id, frame_date, ""])

    if reserved_out is not None:
        reserved_out.parent.mkdir(parents=True, exist_ok=True)
        with reserved_out.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["target_id"])
            for target_id in sorted(rows_by_target):
                w.writerow([target_id])
    return out_csv


def run_label_template(args: argparse.Namespace) -> None:
    out = Path(args.label_template_out)
    reserved = (
        Path(args.label_template_reserved_out) if args.label_template_reserved_out
        else out.with_name(out.stem + "_reserved_targets.csv"))
    emit_label_template(
        Path(args.pilot2023_candidates), out,
        n=args.label_template_n, seed=args.label_template_seed, reserved_out=reserved)
    print(f"[label_template] sampled {args.label_template_n} targets "
          f"(seed={args.label_template_seed}) -> {out} (reserved list -> {reserved})")


# --------------------------------------------------------------------------
# Stage: eval (paired vs fresh Gemini round) — stub until the rebuild lands
# --------------------------------------------------------------------------

def run_eval(args: argparse.Namespace, cfg: C0Config) -> None:
    raise SystemExit(
        "[eval] blocked by design: the paired eval needs the rebuilt full-GEHI "
        "download plus the owner's fresh Gemini round (incl. rep-rep reliability "
        "sample). Per the prereg, C0 curves for those stacks must be produced "
        "and lock_*.json-committed BEFORE reading fresh Gemini verdicts. "
        "Run --stage curve on the new stacks first; the comparison harness "
        "lands as a follow-on once fresh verdicts exist.")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", required=True,
                    choices=["embed", "curve", "decode", "smoke", "eval", "label_template"])
    ap.add_argument("--arm", default="dinov2_floor", choices=sorted(ARMS))
    ap.add_argument("--facet", default=None, choices=["token", "key"],
                    help="override C0Config.facet")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--stack-format", default="legacy", choices=["legacy", "basemap96"],
                    help="embed/curve stack layout: 'legacy' scan-based per-anchor dirs "
                         "(chip_geom_v2_tight12), or 'basemap96' -- the "
                         "basemap_rebuild_2026-07-13 chips/<target_id>/z<zoom>/ layout "
                         "(geometry_version basemap96_z19_v1). Also sets the default "
                         "geometry_version (override with --geometry-version); the curve "
                         "stage's actual mask math is chosen from each npz's own content, "
                         "not this flag, so it stays correct even if forgotten there.")
    ap.add_argument("--geometry-version", default=None,
                    help="override C0Config.geometry_version (default depends on --stack-format)")
    ap.add_argument("--min-embed-frames", type=int, default=3,
                    help="embed: skip a target with fewer usable stack frames than this")
    ap.add_argument("--chips-root", default=None,
                    help="dir of per-anchor/per-target GEHI chip dirs (embed stage)")
    ap.add_argument("--chip-targets", default=None, help="chip_targets.csv path")
    ap.add_argument("--census-date", default=CENSUS_DATE_FALLBACK,
                    help="T_c (YYYY-MM-DD); per-grid flight date preferred when known")
    ap.add_argument("--scan-states", default=None,
                    help="legacy scan_states dir (smoke stage; mutually exclusive with "
                         "--labels-csv)")
    ap.add_argument("--labels-csv", default=None,
                    help="smoke stage: manual-annotation CSV (target_id,frame_date,label) "
                         "-- amendment 2026-07-13 replacement for the wiped scan_states "
                         "label source; mutually exclusive with --scan-states")
    ap.add_argument("--pilot2023-candidates", default=None,
                    help="label_template stage: gehi_vintage_candidates_pilot2023.csv path")
    ap.add_argument("--label-template-out", default=None,
                    help="label_template stage: output CSV path")
    ap.add_argument("--label-template-reserved-out", default=None,
                    help="label_template stage: output CSV of sampled (main-eval-reserved) "
                         "target ids (default: <out>_reserved_targets.csv)")
    ap.add_argument("--label-template-n", type=int, default=150,
                    help="label_template stage: number of targets to sample")
    ap.add_argument("--label-template-seed", type=int, default=20_260_713,
                    help="label_template stage: RNG seed for the sample draw")
    ap.add_argument("--whitener", default=None, help="pre-fitted whitener npz")
    ap.add_argument("--fit-whitener", action="store_true")
    ap.add_argument("--min-grad-energy", type=float, default=0.02,
                    help="frame usability floor (normalized gradient energy)")
    ap.add_argument("--min-confidence", type=float, default=0.7,
                    help="smoke: legacy scan-states label confidence floor")
    ap.add_argument("--device", default=None)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="debug: cap anchors")
    ap.add_argument("--force", action="store_true", help="re-embed existing npz")
    args = ap.parse_args(argv)

    default_geometry_version = (
        "basemap96_z19_v1" if args.stack_format == "basemap96" else "chip_geom_v2_tight12")
    cfg_kwargs: dict[str, Any] = {
        "geometry_version": args.geometry_version or default_geometry_version,
    }
    if args.facet is not None:
        cfg_kwargs["facet"] = args.facet
    cfg = C0Config(**cfg_kwargs)
    Path(args.out).mkdir(parents=True, exist_ok=True)

    if args.stage == "embed":
        if not args.chips_root or not args.chip_targets:
            ap.error("--stage embed requires --chips-root and --chip-targets")
        run_embed(args, cfg)
    elif args.stage == "curve":
        if not args.chip_targets:
            ap.error("--stage curve requires --chip-targets")
        run_curve(args, cfg)
    elif args.stage == "decode":
        run_decode(args, cfg)
    elif args.stage == "smoke":
        if not args.scan_states and not args.labels_csv:
            ap.error("--stage smoke requires --scan-states or --labels-csv")
        if args.scan_states and args.labels_csv:
            ap.error("--stage smoke: pass only one of --scan-states / --labels-csv")
        run_smoke(args, cfg)
    elif args.stage == "eval":
        run_eval(args, cfg)
    elif args.stage == "label_template":
        if not args.pilot2023_candidates or not args.label_template_out:
            ap.error("--stage label_template requires --pilot2023-candidates and "
                     "--label-template-out")
        run_label_template(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
