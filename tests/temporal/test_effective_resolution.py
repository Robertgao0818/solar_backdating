"""Tests for the sentinel effective-resolution estimator (ISSUE-18 / D17).

All imagery is synthetic (numpy, fixed seed) written to tmp GeoTIFFs with
rasterio. Rungs are simulated by blur + downsample + nearest-neighbour upsample:
1x = sharp (z20), 2x = one rung coarser (z19), 4x = two rungs coarser (z18).

Covers: (a) the self-gate passes and metric medians order correctly on
well-separated sentinels; (b) the self-gate FAILS on indistinguishable sentinels
and then NO flags are emitted (fail-closed); (c) with a passing gate a nominal-z20
candidate degraded 2x is flagged while a genuinely sharp z20 candidate is not.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from scipy.ndimage import gaussian_filter

from scripts.temporal.effective_resolution import (
    build_gate_report,
    compute_sentinel_metrics,
    estimate_effective_zoom,
    evaluate_candidate,
    main,
    normalized_gradient_energy,
    pairwise_auc,
)

SIZE = 128
# rung factor -> known zoom: sharp=z20, 2x-coarse=z19, 4x-coarse=z18
FACTOR_FOR_ZOOM = {20: 1, 19: 2, 18: 4}


def make_scene(size: int, seed: int) -> np.ndarray:
    r = np.random.default_rng(seed)
    base = gaussian_filter(r.standard_normal((size, size)), sigma=size / 16)
    fine = gaussian_filter(r.standard_normal((size, size)), sigma=0.8)
    img = base + 1.0 * fine
    for _ in range(10):
        x = int(r.integers(0, size - 16))
        y = int(r.integers(0, size - 16))
        w = int(r.integers(4, 16))
        h = int(r.integers(4, 16))
        img[y : y + h, x : x + w] += r.uniform(-2.5, 2.5)
    img = (img - img.min()) / (img.max() - img.min() + 1e-9)
    return img.astype(np.float64)


def degrade(img: np.ndarray, factor: int) -> np.ndarray:
    if factor == 1:
        return img.copy()
    size = img.shape[0]
    blurred = gaussian_filter(img, sigma=factor * 0.5)
    small = blurred[::factor, ::factor]
    up = np.repeat(np.repeat(small, factor, axis=0), factor, axis=1)
    return up[:size, :size]


def write_geotiff(path: Path, arr: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(arr * 255.0, 0, 255).astype("uint8")
    h, w = data.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=1,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_origin(0, h, 1, 1),
    ) as ds:
        ds.write(data, 1)


def _write_sentinel_set(root: Path, factor_by_zoom: dict[int, int], n_per_zoom: int = 8) -> list[tuple[str, int]]:
    """Write n chips per zoom and return (chip_path, known_zoom) rows."""
    rows: list[tuple[str, int]] = []
    seed = 0
    for zoom, factor in factor_by_zoom.items():
        for i in range(n_per_zoom):
            scene = make_scene(SIZE, seed)
            seed += 1
            chip = degrade(scene, factor)
            path = root / f"z{zoom}_{i}.tif"
            write_geotiff(path, chip)
            rows.append((str(path), zoom))
    return rows


# ---------------------------------------------------------------------------
# core metric
# ---------------------------------------------------------------------------

def test_metric_orders_by_degradation() -> None:
    m1, m2, m4 = [], [], []
    for seed in range(8):
        scene = make_scene(SIZE, seed + 500)
        m1.append(normalized_gradient_energy(degrade(scene, 1)))
        m2.append(normalized_gradient_energy(degrade(scene, 2)))
        m4.append(normalized_gradient_energy(degrade(scene, 4)))
    assert np.median(m1) > np.median(m2) > np.median(m4)


def test_pairwise_auc_perfect_separation() -> None:
    assert pairwise_auc([3.0, 4.0, 5.0], [0.0, 1.0, 2.0]) == 1.0
    assert pairwise_auc([0.0, 1.0], [0.0, 1.0]) == 0.5


def test_flat_image_has_zero_metric() -> None:
    assert normalized_gradient_energy(np.full((64, 64), 0.5)) == 0.0


# ---------------------------------------------------------------------------
# (a) self-gate passes on separated sentinels; medians order correctly
# ---------------------------------------------------------------------------

def test_self_gate_passes_on_separated_sentinels(tmp_path: Path) -> None:
    rows = _write_sentinel_set(tmp_path / "sent", FACTOR_FOR_ZOOM)
    metrics_by_zoom = compute_sentinel_metrics(rows)
    gate = build_gate_report(metrics_by_zoom, auc_threshold=0.75, min_group=3)

    assert gate.passed is True
    assert gate.medians_monotonic is True
    # finer zoom -> higher metric
    assert gate.zoom_medians[20] > gate.zoom_medians[19] > gate.zoom_medians[18]
    assert all(a >= 0.75 for a in gate.adjacent_aucs.values())


# ---------------------------------------------------------------------------
# (b) self-gate FAILS on indistinguishable sentinels -> zero flags (fail-closed)
# ---------------------------------------------------------------------------

def test_self_gate_fails_on_indistinguishable_sentinels(tmp_path: Path) -> None:
    # every "zoom" is really the same 2x degradation -> no separation
    rows = _write_sentinel_set(tmp_path / "sent", {20: 2, 19: 2, 18: 2})
    metrics_by_zoom = compute_sentinel_metrics(rows)
    gate = build_gate_report(metrics_by_zoom, auc_threshold=0.75, min_group=3)
    assert gate.passed is False
    assert gate.reasons  # structured explanation present


def test_gate_failure_emits_zero_flags_fail_closed(tmp_path: Path) -> None:
    sent_rows = _write_sentinel_set(tmp_path / "sent", {20: 2, 19: 2, 18: 2})
    sentinel_csv = tmp_path / "sentinels.csv"
    with sentinel_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["chip_path", "known_zoom"])
        w.writeheader()
        for cp, z in sent_rows:
            w.writerow({"chip_path": cp, "known_zoom": z})

    # a genuinely degraded candidate that WOULD flag under a passing gate
    cand = degrade(make_scene(SIZE, 9001), 4)
    cand_path = tmp_path / "cand.tif"
    write_geotiff(cand_path, cand)
    candidates_csv = tmp_path / "candidates.csv"
    with candidates_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["chip_path", "achieved_zoom"])
        w.writeheader()
        w.writerow({"chip_path": str(cand_path), "achieved_zoom": 20})

    out_csv = tmp_path / "flags.csv"
    gate_json = tmp_path / "gate.json"
    rc = main(
        [
            "--sentinel-manifest", str(sentinel_csv),
            "--candidates", str(candidates_csv),
            "--output", str(out_csv),
            "--gate-report", str(gate_json),
        ]
    )
    assert rc != 0, "gate failure must return a nonzero exit code"
    report = json.loads(gate_json.read_text())
    assert report["passed"] is False
    # fail-closed: the flags CSV exists but carries zero flagged rows
    with out_csv.open(newline="", encoding="utf-8") as fh:
        flag_rows = [r for r in csv.DictReader(fh) if r.get("flagged", "").lower() == "true"]
    assert flag_rows == []


# ---------------------------------------------------------------------------
# (c) passing gate: degraded z20 candidate flagged, sharp z20 candidate not
# ---------------------------------------------------------------------------

def test_flag_degraded_candidate_but_not_sharp(tmp_path: Path) -> None:
    rows = _write_sentinel_set(tmp_path / "sent", FACTOR_FOR_ZOOM)
    metrics_by_zoom = compute_sentinel_metrics(rows)
    gate = build_gate_report(metrics_by_zoom, auc_threshold=0.75, min_group=3)
    assert gate.passed is True

    # nominal z20 but really 2x-degraded -> effective z19, one rung coarser -> FLAG
    degraded = degrade(make_scene(SIZE, 7777), 2)
    degraded_path = tmp_path / "degraded_z20.tif"
    write_geotiff(degraded_path, degraded)
    dflag = evaluate_candidate(str(degraded_path), 20, gate)
    assert dflag.effective_zoom == 19
    assert dflag.coarser_by == 1
    assert dflag.flagged is True
    assert dflag.raster_error is None

    # genuinely sharp z20 -> effective z20 -> NOT flagged
    sharp = degrade(make_scene(SIZE, 8888), 1)
    sharp_path = tmp_path / "sharp_z20.tif"
    write_geotiff(sharp_path, sharp)
    sflag = evaluate_candidate(str(sharp_path), 20, gate)
    assert sflag.effective_zoom == 20
    assert sflag.flagged is False


def test_estimate_effective_zoom_boundaries() -> None:
    medians = {18: 0.10, 19: 0.20, 20: 0.40}
    assert estimate_effective_zoom(0.40, medians) == 20
    assert estimate_effective_zoom(0.20, medians) == 19
    assert estimate_effective_zoom(0.05, medians) == 18
    assert estimate_effective_zoom(100.0, medians) == 20  # clamps to finest
