"""Tests for Task D review fixes:

- chip_index dense renumbering for Gemini batch + remap back to original
- TIFF -> PNG conversion before sending to Gemini
- env-file fallback to ZASOLAR_ROOT when subrepo .env.gemini.local missing
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts.temporal.gehi_common import ensure_review_png
from scripts.temporal.gehi_download import DownloadResult
from scripts.temporal.run_adaptive_scan import (
    _build_batch_picks_with_remap,
    _default_gemini_env,
)
from scripts.temporal.scan_state import Pick


def _ok_outcome(path: Path, *, zoom: int = 20) -> DownloadResult:
    return DownloadResult(
        anchor_id="a",
        capture_date="2020-06-15",
        version="100",
        requested_zoom_ladder=(20, 19),
        actual_zoom=zoom,
        path=path,
        sha256="deadbeef",
        status="ok",
        error=None,
        gehi_command="",
        download_stdout_sha256="",
    )


def _failed_outcome() -> DownloadResult:
    return DownloadResult(
        anchor_id="a",
        capture_date="2020-06-15",
        version="100",
        requested_zoom_ladder=(20, 19),
        actual_zoom=None,
        path=None,
        sha256="",
        status="all_zooms_failed",
        error="simulated",
        gehi_command="",
        download_stdout_sha256="",
    )


def _pick(chip_index: int, capture_date: str = "2020-06-15") -> Pick:
    return Pick(chip_index=chip_index, capture_date=capture_date, version=100, requested_zoom=20)


def test_remap_renumbers_to_dense_when_some_downloads_fail(tmp_path: Path) -> None:
    """Sparse pick indices {1,2,3,4,5} with picks 2 and 4 failing → dense Gemini batch 1..3."""
    paths = [tmp_path / f"chip_{i}.png" for i in range(1, 4)]
    for p in paths:
        p.write_bytes(b"PNG_BYTES")

    outcomes = [
        (_pick(1), _ok_outcome(paths[0])),
        (_pick(2), _failed_outcome()),
        (_pick(3), _ok_outcome(paths[1])),
        (_pick(4), _failed_outcome()),
        (_pick(5), _ok_outcome(paths[2])),
    ]
    batch_picks, mapping = _build_batch_picks_with_remap(outcomes, lambda p: p)
    assert [bp.chip_index for bp in batch_picks] == [1, 2, 3]
    assert mapping == {1: 1, 2: 3, 3: 5}


def test_remap_uses_review_asset_resolver(tmp_path: Path) -> None:
    """Resolver is called per successful chip; its output is what BatchPick.chip_path holds."""
    tif = tmp_path / "chip.tif"
    tif.write_bytes(b"TIF")
    png = tmp_path / "chip.png"
    png.write_bytes(b"PNG")

    seen: list[Path] = []

    def resolver(p: Path) -> Path:
        seen.append(p)
        return p.with_suffix(".png")

    outcomes = [(_pick(1), _ok_outcome(tif))]
    batch_picks, _ = _build_batch_picks_with_remap(outcomes, resolver)
    assert seen == [tif]
    assert batch_picks[0].chip_path == png


def test_remap_empty_when_all_downloads_fail() -> None:
    outcomes = [(_pick(1), _failed_outcome()), (_pick(2), _failed_outcome())]
    batch_picks, mapping = _build_batch_picks_with_remap(outcomes, lambda p: p)
    assert batch_picks == []
    assert mapping == {}


def test_ensure_review_png_converts_tiff_to_png(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    tif = tmp_path / "chip.tif"
    Image.new("RGB", (16, 16), (200, 100, 50)).save(tif, format="TIFF")
    png = ensure_review_png(tif)
    assert png == tif.with_suffix(".png")
    assert png.exists() and png.stat().st_size > 0
    with Image.open(png) as im:
        assert im.format == "PNG"
        assert im.size == (16, 16)


def test_ensure_review_png_idempotent_when_png_newer(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image
    import os
    import time

    tif = tmp_path / "chip.tif"
    Image.new("RGB", (8, 8), "blue").save(tif, format="TIFF")
    png = ensure_review_png(tif)
    first_mtime = png.stat().st_mtime
    time.sleep(0.05)
    png2 = ensure_review_png(tif)
    assert png2 == png
    assert png.stat().st_mtime == first_mtime, "png must not be re-encoded when newer than tif"


def test_ensure_review_png_short_circuits_for_non_tiff(tmp_path: Path) -> None:
    jpg = tmp_path / "chip.jpg"
    jpg.write_bytes(b"FAKE_JPG")
    out = ensure_review_png(jpg)
    assert out == jpg


def test_ensure_review_png_re_encodes_when_tif_newer(tmp_path: Path) -> None:
    """If the user re-downloads the TIFF, the cached PNG must be replaced."""
    pytest.importorskip("PIL")
    from PIL import Image
    import os
    import time

    tif = tmp_path / "chip.tif"
    Image.new("RGB", (4, 4), "red").save(tif, format="TIFF")
    png = ensure_review_png(tif)
    first_size = png.stat().st_size
    time.sleep(0.05)
    Image.new("RGB", (32, 32), "green").save(tif, format="TIFF")
    png2 = ensure_review_png(tif)
    with Image.open(png2) as im:
        assert im.size == (32, 32)


def test_default_gemini_env_falls_back_to_zasolar_root(tmp_path: Path, monkeypatch) -> None:
    """When subrepo .env.gemini.local is missing, _default_gemini_env() picks ZASOLAR_ROOT's copy."""
    fake_zasolar = tmp_path / "fake_zasolar"
    fake_zasolar.mkdir()
    main_env = fake_zasolar / ".env.gemini.local"
    main_env.write_text("GOOGLE_GEMINI_BASE_URL=https://stub\nGEMINI_API_KEY=abc\n")

    from scripts.validation import gemini_solar_image_review as g

    monkeypatch.setattr(g, "PROJECT_ROOT", tmp_path / "fake_subrepo_no_env")
    monkeypatch.setenv("ZASOLAR_ROOT", str(fake_zasolar))

    resolved = _default_gemini_env()
    assert resolved == main_env
