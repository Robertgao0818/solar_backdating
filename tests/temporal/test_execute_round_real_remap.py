"""Tests for Task D review fixes:

- chip_index dense renumbering for Gemini batch + remap back to original
- TIFF -> PNG conversion before sending to Gemini
- env-file fallback to ZASOLAR_ROOT when subrepo .env.gemini.local missing

Plus regression guards added for the post-review defect sweep:

- #1: execute_round_real must call _score_batch_picks_chunked with the
  AdaptiveScanConfig passed as ``config`` and the GeminiClientConfig passed as
  ``gemini_config``. Previously the call passed config=gemini_config and OMITTED
  gemini_config, which crashed the real scan path with TypeError /
  AttributeError (config.gemini_max_dates_per_call on a GeminiClientConfig).
- #5: the batch orchestrator must exit nonzero when any anchor failed (status
  starting with ``done_ambiguous_orchestrator_error``); continue-on-error is
  preserved, only the final exit code changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.temporal import gehi_common as _gehi_common
from scripts.temporal import gehi_download as _gehi_download
from scripts.temporal.gehi_common import ensure_review_png
from scripts.temporal.gehi_download import DownloadResult
from scripts.temporal.run_adaptive_scan import (
    _build_batch_picks_with_remap,
    _default_gemini_env,
    _exit_code_for_states,
    _score_batch_picks_chunked,
    execute_round_real,
)
from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_state import Pick, Round, RoundResult, ScanState
from scripts.validation import gemini_solar_image_review as gsir
from scripts.validation.gemini_solar_image_review import BatchPick, GeminiObservation


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


def _make_round(picks, *, round_id: int = 0) -> Round:
    return Round(
        round_id=round_id,
        round_type="initial",
        window_start_date=None,
        window_end_date=None,
        picks=list(picks),
    )


def _make_state(anchor_id: str, status: str) -> ScanState:
    """Build a ScanState with a given status.

    ScanState.__post_init__ rejects unknown statuses, but the orchestrator
    assigns terminal statuses by mutating `.status` after construction (see
    run_one_anchor / _record_orchestrator_failure). We mirror that here: build a
    valid `scanning` state, then set the status directly. This also lets the
    prefix-match test use a hypothetical suffixed orchestrator-error status.
    """
    state = ScanState(anchor_id=anchor_id, region_key="r", grid_id="g")
    state.status = status
    return state


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


def test_score_batch_picks_chunked_enforces_date_limit(tmp_path: Path, monkeypatch) -> None:
    from scripts.validation import gemini_solar_image_review as g

    score_picks: list[BatchPick] = []
    batch_to_original: dict[int, int] = {}
    for idx in range(1, 8):
        chip = tmp_path / f"chip_{idx}.png"
        chip.write_bytes(b"PNG")
        score_picks.append(
            BatchPick(
                chip_index=idx,
                chip_path=chip,
                capture_date=f"2020-01-{idx:02d}",
                version=idx,
                actual_zoom=20,
            )
        )
        batch_to_original[idx] = idx + 100

    chunk_sizes: list[int] = []
    seen_indices: list[list[int]] = []

    def fake_score_batch(picks, *, config, audit_writer, census_mid_date_iso):
        chunk_sizes.append(len(picks))
        seen_indices.append([p.chip_index for p in picks])
        audit_writer({"stage": "batch_attempt_1", "n_picks": len(picks)})
        return [
            GeminiObservation(
                chip_index=p.chip_index,
                pv_present=True,
                confidence=0.9,
                quality_flag="usable",
                evidence="ok",
                notes="",
                decision_source="gemini_batch",
            )
            for p in picks
        ]

    monkeypatch.setattr(g, "score_batch_with_fallback", fake_score_batch)
    audits: list[dict[str, Any]] = []
    obs_by_original = _score_batch_picks_chunked(
        score_picks,
        batch_to_original,
        config=AdaptiveScanConfig(gemini_max_dates_per_call=5),
        gemini_config=object(),
        audit_writer=audits.append,
        census_mid_date_iso=None,
    )

    assert chunk_sizes == [5, 2]
    assert seen_indices == [[1, 2, 3, 4, 5], [1, 2]]
    assert sorted(obs_by_original) == [101, 102, 103, 104, 105, 106, 107]
    assert {a["batch_chunk_index"] for a in audits} == {1, 2}


# --------------------------------------------------------------------------
# #1: execute_round_real real scan path no longer crashes (config remap)
# --------------------------------------------------------------------------


def test_execute_round_real_does_not_crash_on_config_remap(tmp_path: Path, monkeypatch) -> None:
    """Regression guard for #1.

    execute_round_real must forward the AdaptiveScanConfig as ``config`` and the
    GeminiClientConfig as ``gemini_config`` to _score_batch_picks_chunked. With
    the pre-fix swapped/omitted kwargs the run raised TypeError (missing
    gemini_config) / AttributeError (GeminiClientConfig has no
    gemini_max_dates_per_call). This exercises the path offline and asserts a
    populated Round comes back with no such error.

    Helpers imported *inside* execute_round_real / _score_batch_picks_chunked are
    patched at their source modules (not on run_adaptive_scan), because the
    function-local imports rebind from the source each call.
    """
    seen: dict[str, Any] = {}

    def fake_download(anchor, *, capture_date, version, zoom_ladder, output_root, vintage_check=None):
        p = tmp_path / f"chip_{capture_date}.tif"
        p.write_bytes(b"TIF")
        return _ok_outcome(p)

    def fake_ensure(path):
        return Path(str(path)).with_suffix(".png")

    def fake_score_batch(picks, *, config, audit_writer, census_mid_date_iso):
        # The object reaching the Gemini client must be the GeminiClientConfig,
        # proving config/gemini_config were not swapped on the way through.
        seen["gemini_config"] = config
        audit_writer({"stage": "batch_attempt_1", "n_picks": len(picks)})
        return [
            GeminiObservation(
                chip_index=p.chip_index,
                pv_present=True,
                confidence=0.88,
                quality_flag="usable",
                evidence="panels visible",
                notes="",
                decision_source="gemini_batch",
            )
            for p in picks
        ]

    monkeypatch.setattr(_gehi_download, "download_chip_with_zoom_ladder", fake_download)
    monkeypatch.setattr(_gehi_common, "ensure_review_png", fake_ensure)
    monkeypatch.setattr(gsir, "score_batch_with_fallback", fake_score_batch)

    config = AdaptiveScanConfig(gemini_max_dates_per_call=2)
    sentinel_gemini_config = object()  # NOT an AdaptiveScanConfig
    picks = [_pick(1, "2020-01-01"), _pick(2, "2021-01-01"), _pick(3, "2022-01-01")]
    rnd = _make_round(picks, round_id="r0")
    anchor = {"anchor_id": "A1", "lat": "-26.2", "lon": "28.0", "region_key": "johannesburg"}

    returned = execute_round_real(
        rnd,
        anchor,
        config,
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=sentinel_gemini_config,
        vintage_check=None,
        census_mid_date_iso=None,
    )

    assert returned.completed is True
    assert returned.failed is False
    assert len(returned.results) == len(picks)
    assert all(isinstance(r, RoundResult) for r in returned.results)
    # Every pick was scored true (no gemini_failed fallbacks).
    assert all(r.pv_present is True for r in returned.results)
    assert all(r.decision_source == "gemini_batch" for r in returned.results)
    # The GeminiClientConfig (not the AdaptiveScanConfig) reached the scorer.
    assert seen["gemini_config"] is sentinel_gemini_config


def test_execute_round_real_marks_failed_downloads(tmp_path: Path, monkeypatch) -> None:
    """A pick whose download fails is recorded as unusable/gemini_failed, others score."""

    def fake_download(anchor, *, capture_date, version, zoom_ladder, output_root, vintage_check=None):
        if capture_date == "2021-01-01":
            return _failed_outcome()
        p = tmp_path / f"chip_{capture_date}.tif"
        p.write_bytes(b"TIF")
        return _ok_outcome(p)

    monkeypatch.setattr(_gehi_download, "download_chip_with_zoom_ladder", fake_download)
    monkeypatch.setattr(_gehi_common, "ensure_review_png", lambda path: Path(str(path)).with_suffix(".png"))

    def fake_score_batch(picks, *, config, audit_writer, census_mid_date_iso):
        return [
            GeminiObservation(
                chip_index=p.chip_index,
                pv_present=False,
                confidence=0.7,
                quality_flag="usable",
                evidence="",
                notes="",
                decision_source="gemini_batch",
            )
            for p in picks
        ]

    monkeypatch.setattr(gsir, "score_batch_with_fallback", fake_score_batch)

    picks = [_pick(1, "2020-01-01"), _pick(2, "2021-01-01")]
    rnd = _make_round(picks, round_id="r1")
    anchor = {"anchor_id": "A2", "region_key": "johannesburg"}

    returned = execute_round_real(
        rnd,
        anchor,
        AdaptiveScanConfig(gemini_max_dates_per_call=5),
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=object(),
        vintage_check=None,
        census_mid_date_iso=None,
    )

    by_idx = {r.chip_index: r for r in returned.results}
    assert by_idx[1].pv_present is False
    assert by_idx[1].decision_source == "gemini_batch"
    assert by_idx[2].pv_present is None
    assert by_idx[2].quality_flag == "unusable"
    assert by_idx[2].decision_source == "gemini_failed"


# --------------------------------------------------------------------------
# #5: orchestrator exit code reflects per-anchor failures
# --------------------------------------------------------------------------


def test_exit_code_nonzero_when_anchor_failed() -> None:
    states = [
        _make_state("A1", "done_appears"),
        _make_state("A2", "done_ambiguous_orchestrator_error"),
    ]
    assert _exit_code_for_states(states) != 0


def test_exit_code_zero_when_all_clean() -> None:
    states = [
        _make_state("A1", "done_appears"),
        _make_state("A2", "done_installed_during_census"),
    ]
    assert _exit_code_for_states(states) == 0


def test_exit_code_matches_orchestrator_error_prefix() -> None:
    """Match is by prefix, so any suffixed orchestrator-error status still fails."""
    states = [_make_state("A1", "done_ambiguous_orchestrator_error_timeout")]
    assert _exit_code_for_states(states) == 1
