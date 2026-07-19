"""Tests for the R0.1 footprint sidecar builder (PRD §3.2 amendment).

Synthetic tier (always runs): derived-field maths (aspect, bbox-fill, area-matched
rectangle), reconciliation pass/fail branches, and the freeze guard (frozen R0
products left byte-identical) via a fully synthetic tmp R0 dir. Real-data tier
(skipped without the drive): the actual 41,393-anchor sidecar reconciles against
the frozen manifest and leaves the frozen files untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.temporal import build_r0_footprint_sidecar as S

R0_DIR = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1"
)
MANIFEST = R0_DIR / "manifest.parquet"
requires_data = pytest.mark.skipif(
    not MANIFEST.exists(), reason="frozen R0 manifest not present"
)


# ------------------------------------------------------------------ synthetic #
def _anchors_csv(tmp_path: Path, n: int = 6) -> Path:
    # Deterministic footprints spanning square-ish to elongated shapes.
    rng = np.random.default_rng(0)
    rows = []
    for i in range(n):
        w = 4.0 + i * 2.0
        h = 4.0 + (i % 3)  # varies aspect
        # polygon area smaller than bbox (rotation/irregularity) => fill < 1
        area = round(0.6 * w * h, 4)
        rows.append(
            {
                "anchor_id": f"a{i:04d}",
                "chip_arm": "A24" if area < 40 else "A48",
                "source_area_m2": area,
                "source_width_m": w,
                "source_height_m": h,
                "centroid_lon": 28.0 + i * 1e-4,
                "centroid_lat": -26.1,
            }
        )
    p = tmp_path / "anchors_all.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _frozen_r0_dir(tmp_path: Path, anchor_ids, areas) -> Path:
    """A tmp R0 dir with a matching manifest + dummy frozen products."""
    d = tmp_path / "r0"
    d.mkdir()
    man = pd.DataFrame(
        {
            "anchor_id": list(anchor_ids) * 2,  # multiple obs/anchor, dedup in reconcile
            "source_area_m2": list(areas) * 2,
        }
    )
    man.to_parquet(d / "manifest.parquet", index=False)
    (d / "splits.parquet").write_bytes(b"frozen-splits-bytes")
    (d / "MANIFEST_LOCK.json").write_text('{"frozen": true}')
    return d


def test_derived_fields_and_area_matched_rectangle(tmp_path):
    df = S.build_sidecar_frame(_anchors_csv(tmp_path))
    assert list(df["anchor_id"]) == sorted(df["anchor_id"])  # sorted output
    w, h, area = df.source_width_m, df.source_height_m, df.source_area_m2
    # aspect = long/short; bbox_area = w*h; fill = area/bbox
    np.testing.assert_allclose(df.aspect_ratio, np.maximum(w, h) / np.minimum(w, h))
    np.testing.assert_allclose(df.bbox_area_m2, w * h)
    np.testing.assert_allclose(df.bbox_fill, area / (w * h))
    # the area-matched rectangle preserves area AND the bbox aspect ratio.
    np.testing.assert_allclose(df.areamatched_long_m * df.areamatched_short_m, area, rtol=1e-9)
    np.testing.assert_allclose(
        df.areamatched_long_m / df.areamatched_short_m, df.aspect_ratio, rtol=1e-9
    )
    assert (df.footprint_version == S.FOOTPRINT_VERSION).all()


def test_reconcile_pass_and_freeze_guard(tmp_path, monkeypatch):
    csv = _anchors_csv(tmp_path, n=6)
    df = S.build_sidecar_frame(csv)
    d = _frozen_r0_dir(tmp_path, df.anchor_id, df.source_area_m2)
    monkeypatch.setattr(S, "EXPECTED_ANCHORS", len(df))

    # capture frozen shas before
    before = {f: S.sha256_file(d / f) for f in S.FROZEN_FILES}
    rc = S.build(S.parse_args(["--anchors-csv", str(csv), "--r0-dir", str(d)]))
    assert rc == 0
    # sidecar + lock written, frozen products byte-identical
    assert (d / "footprint_sidecar_v1.parquet").exists()
    lock = json.loads((d / "FOOTPRINT_SIDECAR_LOCK.json").read_text())
    assert lock["reconciliation"]["passed"] is True
    assert lock["frozen_files_unchanged"] is True
    after = {f: S.sha256_file(d / f) for f in S.FROZEN_FILES}
    assert before == after


def test_reconcile_fails_on_missing_anchor(tmp_path, monkeypatch):
    csv = _anchors_csv(tmp_path, n=6)
    df = S.build_sidecar_frame(csv)
    # manifest has an anchor the sidecar lacks -> reconcile must fail (non-zero).
    ids = list(df.anchor_id) + ["a9999"]
    areas = list(df.source_area_m2) + [12.0]
    d = _frozen_r0_dir(tmp_path, ids, areas)
    monkeypatch.setattr(S, "EXPECTED_ANCHORS", len(df))
    rc = S.build(S.parse_args(["--anchors-csv", str(csv), "--r0-dir", str(d)]))
    assert rc == 2
    # no sidecar written on reconcile failure
    assert not (d / "footprint_sidecar_v1.parquet").exists()


def test_reconcile_fails_on_area_drift(tmp_path, monkeypatch):
    csv = _anchors_csv(tmp_path, n=6)
    df = S.build_sidecar_frame(csv)
    areas = list(df.source_area_m2)
    areas[0] += 5.0  # manifest area disagrees with CSV area
    d = _frozen_r0_dir(tmp_path, df.anchor_id, areas)
    monkeypatch.setattr(S, "EXPECTED_ANCHORS", len(df))
    rc = S.build(S.parse_args(["--anchors-csv", str(csv), "--r0-dir", str(d)]))
    assert rc == 2


def test_row_count_gate(tmp_path, monkeypatch):
    csv = _anchors_csv(tmp_path, n=6)
    df = S.build_sidecar_frame(csv)
    d = _frozen_r0_dir(tmp_path, df.anchor_id, df.source_area_m2)
    # leave EXPECTED_ANCHORS at the real 41393 -> row-count check must fail.
    rc = S.build(S.parse_args(["--anchors-csv", str(csv), "--r0-dir", str(d)]))
    assert rc == 2


# ------------------------------------------------------------------ real data #
@requires_data
def test_real_sidecar_reconciles_and_freezes(tmp_path):
    # Build against the real CSV but into a scratch dir with COPIES of the frozen
    # products, so the test never risks the real drive. Assert reconcile + freeze.
    import shutil

    d = tmp_path / "r0"
    d.mkdir()
    for f in S.FROZEN_FILES:
        shutil.copy2(R0_DIR / f, d / f)
    before = {f: S.sha256_file(d / f) for f in S.FROZEN_FILES}
    rc = S.build(
        S.parse_args(
            ["--anchors-csv", str(S.DEFAULT_ANCHORS_CSV), "--r0-dir", str(d)]
        )
    )
    assert rc == 0
    df = pd.read_parquet(d / "footprint_sidecar_v1.parquet")
    assert len(df) == S.EXPECTED_ANCHORS
    assert df.anchor_id.nunique() == len(df)
    lock = json.loads((d / "FOOTPRINT_SIDECAR_LOCK.json").read_text())
    assert lock["reconciliation"]["passed"] is True
    assert lock["frozen_files_unchanged"] is True
    assert {f: S.sha256_file(d / f) for f in S.FROZEN_FILES} == before
