#!/usr/bin/env python3
"""Sentinel-set effective-resolution estimator (ISSUE-18 / D17).

GEHistoricalImagery's binary silently substitutes coarser tiles (up to two zoom
levels, nearest-neighbour upsampled) per 256 px tile *inside* a nominally
successful download. `RoundResult.actual_zoom` records only which ladder rung the
download call succeeded at — never the delivered pixels — so an in-chip
substitution is invisible to every rung-level accounting tool. This module
estimates each chip's *effective* rung from its pixels and flags chips that are
delivered ≥1 rung coarser than their nominal (achieved) rung.

Metric — normalized gradient energy.
    metric(I) = mean(|∇I|²) / (Var(I) + ε), measured on a central crop.
Nearest-neighbour tile substitution replicates one coarse pixel into an f×f block
of *identical* values, so every gradient interior to a block is exactly zero and
1-px gradient energy collapses in proportion to the substitution factor f. That
makes the statistic a direct, content-agnostic probe of the finest spatial scale
at which the image actually varies — the definition of effective resolution.
Dividing by the intensity variance removes global-contrast dependence so a
low-contrast sharp chip and a high-contrast sharp chip score alike. We prefer
this over a raw FFT high-frequency ratio because NN upsampling injects spectral
block-replicas that muddy a fixed high-frequency band, whereas the interior-zero
property is exact regardless of scene content. Absolute calibration is not
claimed — robustness to content is what matters, which is exactly why the
self-gate exists.

Self-gate (fail-closed, MANDATORY).
    A sentinel manifest of KNOWN-zoom chips (z18/z19/z20) is scored first. The
    estimator is allowed to flag *anything* only if those known sets separate:
    medians strictly increase with zoom AND every adjacent pair's rank statistic
    (AUC = P(metric_finer > metric_coarser)) clears a threshold. Otherwise it
    emits a structured gate report, zero flags, and a nonzero exit.

D2 note: the zoom-stratified emission matrices key on ACHIEVED zoom
(`RoundResult.actual_zoom` / provenance `achieved_zoom`), never the requested
rung — see `docs/resolution_provenance.md`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CENTRAL_CROP = 0.8
DEFAULT_AUC_THRESHOLD = 0.75
DEFAULT_MIN_GROUP = 3
_EPS = 1e-12


# ---------------------------------------------------------------------------
# core metric (pure, importable)
# ---------------------------------------------------------------------------

def to_grayscale(arr: np.ndarray) -> np.ndarray:
    """Collapse a raster array to a 2-D float grayscale plane.

    Accepts a 2-D plane, a rasterio band-first cube (bands, h, w), or a
    channels-last image (h, w, channels). Uses up to the first three bands.
    """
    a = np.asarray(arr, dtype=np.float64)
    if a.ndim == 2:
        return a
    if a.ndim == 3:
        if a.shape[0] <= 4 and a.shape[0] <= a.shape[-1]:
            bands = a[:3] if a.shape[0] >= 3 else a
            return bands.mean(axis=0)
        bands = a[..., :3] if a.shape[-1] >= 3 else a
        return bands.mean(axis=-1)
    raise ValueError(f"cannot grayscale array with shape {a.shape}")


def central_crop(gray: np.ndarray, fraction: float = DEFAULT_CENTRAL_CROP) -> np.ndarray:
    """Return the centred `fraction`-sized window (dodges tile-edge artifacts)."""
    if not 0 < fraction <= 1:
        raise ValueError(f"central-crop fraction must be in (0, 1], got {fraction}")
    h, w = gray.shape
    ch = max(1, int(round(h * fraction)))
    cw = max(1, int(round(w * fraction)))
    y0 = (h - ch) // 2
    x0 = (w - cw) // 2
    return gray[y0 : y0 + ch, x0 : x0 + cw]


def normalized_gradient_energy(
    arr: np.ndarray, *, central_crop_fraction: float = DEFAULT_CENTRAL_CROP
) -> float:
    """Effective-detail metric: mean squared gradient / variance on a central crop.

    Scale-invariant (numerator and denominator both scale with contrast²) so uint8
    and float chips score identically. A perfectly flat crop returns 0.0.
    """
    gray = central_crop(to_grayscale(arr), central_crop_fraction).astype(np.float64)
    if gray.size < 4:
        return 0.0
    var = float(gray.var())
    if var <= _EPS:
        return 0.0
    gy, gx = np.gradient(gray)
    energy = float((gx * gx + gy * gy).mean())
    return energy / (var + _EPS)


# ---------------------------------------------------------------------------
# raster IO
# ---------------------------------------------------------------------------

def read_raster_gray(path: Path) -> np.ndarray:
    """Read a GeoTIFF/PNG via rasterio and return a 2-D grayscale plane."""
    import rasterio

    with rasterio.open(path) as ds:
        arr = ds.read()  # (bands, h, w)
    return to_grayscale(arr)


def chip_metric(path: Path, *, central_crop_fraction: float = DEFAULT_CENTRAL_CROP) -> float:
    return normalized_gradient_energy(
        read_raster_gray(Path(path)), central_crop_fraction=central_crop_fraction
    )


# ---------------------------------------------------------------------------
# separation statistics + self-gate
# ---------------------------------------------------------------------------

def pairwise_auc(finer: Sequence[float], coarser: Sequence[float]) -> float:
    """AUC = P(metric drawn from `finer` > metric drawn from `coarser`), ties=0.5.

    1.0 = the finer group's metrics are all above the coarser group's; 0.5 = no
    separation. NaN when either group is empty.
    """
    f = np.asarray(list(finer), dtype=np.float64)
    c = np.asarray(list(coarser), dtype=np.float64)
    if f.size == 0 or c.size == 0:
        return float("nan")
    wins = 0.0
    for value in f:
        wins += float(np.sum(value > c)) + 0.5 * float(np.sum(value == c))
    return wins / (f.size * c.size)


@dataclass
class GateReport:
    passed: bool
    auc_threshold: float
    min_group: int
    zoom_counts: dict[int, int]
    zoom_medians: dict[int, float]
    adjacent_aucs: dict[str, float]
    medians_monotonic: bool
    min_adjacent_auc: float | None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        # JSON object keys must be strings
        payload["zoom_counts"] = {str(k): v for k, v in self.zoom_counts.items()}
        payload["zoom_medians"] = {str(k): v for k, v in self.zoom_medians.items()}
        return payload


def build_gate_report(
    metrics_by_zoom: dict[int, Sequence[float]],
    *,
    auc_threshold: float = DEFAULT_AUC_THRESHOLD,
    min_group: int = DEFAULT_MIN_GROUP,
) -> GateReport:
    """Decide whether the sentinel metrics separate well enough to flag anything.

    Passes iff there are ≥2 non-empty zoom groups each with ≥`min_group` chips,
    the per-zoom medians strictly increase with zoom, and every adjacent-zoom AUC
    is ≥`auc_threshold`. Fail-closed: any shortfall is recorded in `reasons` and
    `passed` is False.
    """
    zoom_counts = {z: len(list(v)) for z, v in metrics_by_zoom.items()}
    zoom_medians = {
        z: (float(np.median(list(v))) if zoom_counts[z] else float("nan"))
        for z, v in metrics_by_zoom.items()
    }
    reasons: list[str] = []

    zooms = sorted(z for z, n in zoom_counts.items() if n > 0)
    if len(zooms) < 2:
        reasons.append(f"need >=2 non-empty zoom groups, got {len(zooms)}")

    undersized = [z for z in zooms if zoom_counts[z] < min_group]
    if undersized:
        reasons.append(
            f"zoom groups below min_group={min_group}: "
            + ", ".join(f"z{z}(n={zoom_counts[z]})" for z in undersized)
        )

    meds = [zoom_medians[z] for z in zooms]
    monotonic = len(zooms) >= 2 and all(meds[i] < meds[i + 1] for i in range(len(meds) - 1))
    if len(zooms) >= 2 and not monotonic:
        reasons.append(
            "zoom medians not strictly increasing with zoom: "
            + ", ".join(f"z{z}={zoom_medians[z]:.5f}" for z in zooms)
        )

    adjacent_aucs: dict[str, float] = {}
    min_auc: float | None = None
    for i in range(len(zooms) - 1):
        z_coarse, z_fine = zooms[i], zooms[i + 1]
        auc = pairwise_auc(metrics_by_zoom[z_fine], metrics_by_zoom[z_coarse])
        adjacent_aucs[f"z{z_coarse}_vs_z{z_fine}"] = auc
        min_auc = auc if min_auc is None else min(min_auc, auc)
    if min_auc is not None and min_auc < auc_threshold:
        reasons.append(f"min adjacent AUC {min_auc:.3f} < threshold {auc_threshold}")

    passed = (
        len(zooms) >= 2
        and not undersized
        and monotonic
        and min_auc is not None
        and min_auc >= auc_threshold
    )
    return GateReport(
        passed=passed,
        auc_threshold=auc_threshold,
        min_group=min_group,
        zoom_counts=zoom_counts,
        zoom_medians=zoom_medians,
        adjacent_aucs=adjacent_aucs,
        medians_monotonic=monotonic,
        min_adjacent_auc=min_auc,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# effective-zoom mapping + flagging
# ---------------------------------------------------------------------------

def estimate_effective_zoom(metric: float, zoom_medians: dict[int, float]) -> int:
    """Classify a metric to the nearest known zoom via geometric-mean boundaries.

    Boundaries sit at the geometric mean of adjacent-zoom medians (the metric
    decays multiplicatively with the substitution factor). A metric below the
    lowest boundary clamps to the coarsest known zoom; above the highest, to the
    finest.
    """
    zooms = sorted(zoom_medians)
    meds = [zoom_medians[z] for z in zooms]
    if len(zooms) == 1:
        return zooms[0]
    bounds = [
        math.sqrt(max(meds[i], _EPS) * max(meds[i + 1], _EPS)) for i in range(len(meds) - 1)
    ]
    for i, boundary in enumerate(bounds):
        if metric < boundary:
            return zooms[i]
    return zooms[-1]


@dataclass
class FlagRow:
    chip_path: str
    nominal_zoom: int | None
    metric: float | None
    effective_zoom: int | None
    coarser_by: int | None
    flagged: bool
    raster_error: str | None


FLAG_FIELDS = [
    "chip_path",
    "nominal_zoom",
    "metric",
    "effective_zoom",
    "coarser_by",
    "flagged",
    "raster_error",
]


def evaluate_candidate(
    chip_path: str,
    nominal_zoom: int | None,
    gate: GateReport,
    *,
    central_crop_fraction: float = DEFAULT_CENTRAL_CROP,
) -> FlagRow:
    """Estimate one candidate's effective zoom and flag it if ≥1 rung coarser.

    A raster read failure yields a row with `raster_error` set and `flagged=False`
    (never silently flag on missing pixels).
    """
    try:
        metric = chip_metric(Path(chip_path), central_crop_fraction=central_crop_fraction)
    except Exception as exc:  # noqa: BLE001 - one unreadable chip must not abort the run
        return FlagRow(
            chip_path=str(chip_path),
            nominal_zoom=nominal_zoom,
            metric=None,
            effective_zoom=None,
            coarser_by=None,
            flagged=False,
            raster_error=f"{type(exc).__name__}: {exc}",
        )
    effective = estimate_effective_zoom(metric, gate.zoom_medians)
    coarser_by = None if nominal_zoom is None else nominal_zoom - effective
    flagged = coarser_by is not None and coarser_by >= 1
    return FlagRow(
        chip_path=str(chip_path),
        nominal_zoom=nominal_zoom,
        metric=metric,
        effective_zoom=effective,
        coarser_by=coarser_by,
        flagged=flagged,
        raster_error=None,
    )


# ---------------------------------------------------------------------------
# manifest / candidate IO
# ---------------------------------------------------------------------------

def _coerce_zoom(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def read_sentinel_manifest(path: Path) -> list[tuple[str, int]]:
    """Read a sentinel CSV (chip_path, known_zoom); rows with no zoom are dropped."""
    rows: list[tuple[str, int]] = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for record in csv.DictReader(fh):
            chip_path = (record.get("chip_path") or "").strip()
            zoom = _coerce_zoom(record.get("known_zoom"))
            if chip_path and zoom is not None:
                rows.append((chip_path, zoom))
    return rows


def read_candidates(path: Path) -> list[tuple[str, int | None]]:
    """Read candidate chips from a CSV or JSONL.

    CSV columns: chip_path + one of achieved_zoom / nominal_zoom / zoom.
    JSONL fields: chip_path + achieved_zoom (or nominal_zoom).
    """
    path = Path(path)
    rows: list[tuple[str, int | None]] = []
    if path.suffix == ".jsonl":
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            chip_path = (obj.get("chip_path") or "").strip()
            if not chip_path:
                continue
            zoom = obj.get("achieved_zoom", obj.get("nominal_zoom"))
            rows.append((chip_path, _coerce_zoom(zoom)))
        return rows
    with path.open(newline="", encoding="utf-8") as fh:
        for record in csv.DictReader(fh):
            chip_path = (record.get("chip_path") or "").strip()
            if not chip_path:
                continue
            zoom = record.get("achieved_zoom") or record.get("nominal_zoom") or record.get("zoom")
            rows.append((chip_path, _coerce_zoom(zoom)))
    return rows


def compute_sentinel_metrics(
    sentinels: Sequence[tuple[str, int]],
    *,
    central_crop_fraction: float = DEFAULT_CENTRAL_CROP,
) -> dict[int, list[float]]:
    """Score each sentinel chip, grouping metrics by known zoom (unreadable skipped)."""
    metrics_by_zoom: dict[int, list[float]] = {}
    for chip_path, zoom in sentinels:
        try:
            metric = chip_metric(Path(chip_path), central_crop_fraction=central_crop_fraction)
        except Exception:  # noqa: BLE001 - a bad sentinel chip is dropped, not fatal
            continue
        metrics_by_zoom.setdefault(zoom, []).append(metric)
    return metrics_by_zoom


def _write_flags_csv(path: Path, flags: Sequence[FlagRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FLAG_FIELDS)
        writer.writeheader()
        for flag in flags:
            row = asdict(flag)
            row["flagged"] = "true" if flag.flagged else "false"
            if flag.metric is not None:
                row["metric"] = f"{flag.metric:.6f}"
            writer.writerow(row)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--sentinel-manifest", type=Path, required=True,
                   help="CSV: chip_path, known_zoom (known z18/z19/z20 chips for the self-gate).")
    p.add_argument("--candidates", type=Path, default=None,
                   help="CSV (chip_path + achieved_zoom) or JSONL provenance rows to flag.")
    p.add_argument("--output", type=Path, default=None, help="Flags CSV output path.")
    p.add_argument("--gate-report", type=Path, default=None, help="Gate-report JSON output path.")
    p.add_argument("--central-crop", type=float, default=DEFAULT_CENTRAL_CROP)
    p.add_argument("--auc-threshold", type=float, default=DEFAULT_AUC_THRESHOLD)
    p.add_argument("--min-group", type=int, default=DEFAULT_MIN_GROUP)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.sentinel_manifest.exists():
        raise SystemExit(f"sentinel manifest not found: {args.sentinel_manifest}")

    sentinels = read_sentinel_manifest(args.sentinel_manifest)
    metrics_by_zoom = compute_sentinel_metrics(sentinels, central_crop_fraction=args.central_crop)
    gate = build_gate_report(
        metrics_by_zoom, auc_threshold=args.auc_threshold, min_group=args.min_group
    )

    if args.gate_report is not None:
        args.gate_report.parent.mkdir(parents=True, exist_ok=True)
        args.gate_report.write_text(json.dumps(gate.to_dict(), indent=2), encoding="utf-8")

    print("Self-gate:", "PASS" if gate.passed else "FAIL")
    print("  zoom medians:", {z: round(m, 5) for z, m in sorted(gate.zoom_medians.items())})
    print("  adjacent AUCs:", {k: round(v, 3) for k, v in gate.adjacent_aucs.items()})
    for reason in gate.reasons:
        print("  reason:", reason)

    if not gate.passed:
        # fail-closed: no candidate is examined; zero flags emitted
        if args.output is not None:
            _write_flags_csv(args.output, [])
        print("GATE FAILED -> fail-closed: zero flags emitted, nonzero exit.")
        return 2

    if args.candidates is None:
        print("Gate passed; no --candidates supplied, nothing to flag.")
        return 0

    candidates = read_candidates(args.candidates)
    flags = [
        evaluate_candidate(chip_path, nominal_zoom, gate, central_crop_fraction=args.central_crop)
        for chip_path, nominal_zoom in candidates
    ]
    if args.output is not None:
        _write_flags_csv(args.output, flags)
    n_flagged = sum(1 for f in flags if f.flagged)
    n_error = sum(1 for f in flags if f.raster_error is not None)
    print(f"Flagged {n_flagged}/{len(flags)} chips as >=1 rung coarser than nominal "
          f"({n_error} unreadable).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
