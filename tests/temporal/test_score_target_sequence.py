from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.temporal.score_chip_group_matrix import ChipArtifact, ChipTarget
from scripts.temporal.score_target_sequence import (
    ReviewPng,
    render_review_png_manifest,
    score_target_sequences,
)
from scripts.validation.gemini_solar_image_review import (
    GeminiClientConfig,
    GeminiSequenceObservation,
    GeminiSequenceResult,
)


def _config() -> GeminiClientConfig:
    return GeminiClientConfig(base_url="https://stub.example", api_key="stub")


def _review_pngs(tmp_path: Path, dates: list[str]) -> list[ReviewPng]:
    out: list[ReviewPng] = []
    for idx, date in enumerate(dates, start=1):
        png = tmp_path / f"target_{idx}.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        src = tmp_path / f"source_{idx}.tif"
        src.write_bytes(b"fake")
        out.append(
            ReviewPng(
                anchor_id="target_01",
                region_key="johannesburg",
                grid_id="JNB0202",
                chip_id="chip_001",
                target_label="T01",
                date_index=idx,
                capture_date=date,
                review_png_path=png,
                source_chip_path=src,
                actual_zoom=20,
                render_status="ok",
                render_notes="",
            )
        )
    return out


def _result(pattern: list[bool]) -> GeminiSequenceResult:
    dates = ["2018-03-30", "2019-07-30"]
    return GeminiSequenceResult(
        sequence_pattern="-".join("1" if value else "0" for value in pattern),
        first_present_date=dates[pattern.index(True)] if True in pattern else None,
        first_present_date_index=pattern.index(True) + 1 if True in pattern else None,
        confidence=0.91,
        consistency_flag="monotonic",
        quality_flag="usable",
        review_notes="stub",
        observations=[
            GeminiSequenceObservation(
                date_index=idx,
                capture_date=date,
                pv_present=value,
                pv_score=0.9 if value else 0.1,
                evidence=f"date {idx}",
                notes="",
            )
            for idx, (date, value) in enumerate(zip(dates, pattern), start=1)
        ],
        decision_source="gemini_sequence",
    )


def test_score_target_sequences_scores_one_target_window(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_scorer(date_picks, *, config, audit_writer, max_tokens):
        calls.append(
            {
                "dates": [pick.capture_date for pick in date_picks],
                "max_tokens": max_tokens,
                "config": config,
            }
        )
        audit_writer({"stage": "sequence_attempt_1", "ok": True})
        return _result([False, True])

    target_rows, long_rows = score_target_sequences(
        review_pngs=_review_pngs(tmp_path, ["2018-03-30", "2019-07-30"]),
        dates=["2018-03-30", "2019-07-30"],
        config=_config(),
        audit_dir=tmp_path / "audit",
        scorer=fake_scorer,
        max_tokens=None,
    )

    assert len(calls) == 1
    assert calls[0]["dates"] == ["2018-03-30", "2019-07-30"]
    assert calls[0]["max_tokens"] is None
    assert len(target_rows) == 1
    assert target_rows[0]["sequence_pattern"] == "0-1"
    assert target_rows[0]["first_present_date"] == "2019-07-30"
    assert Path(str(target_rows[0]["audit_path"])).exists()
    assert len(long_rows) == 2
    assert [row["pv_present"] for row in long_rows] == ["0", "1"]
    assert {row["decision_source"] for row in long_rows} == {"gemini_sequence"}


def test_score_target_sequences_preserves_missing_review_png_targets(tmp_path: Path) -> None:
    calls = 0

    def fake_scorer(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _result([False, True])

    target_rows, long_rows = score_target_sequences(
        review_pngs=_review_pngs(tmp_path, ["2018-03-30"]),
        dates=["2018-03-30", "2019-07-30"],
        config=_config(),
        scorer=fake_scorer,
    )

    assert calls == 0
    assert target_rows[0]["quality_flag"] == "sequence_pending_missing_review_png"
    assert target_rows[0]["sequence_pattern"] == "?-?"
    assert len(long_rows) == 2
    assert {row["decision_source"] for row in long_rows} == {"sequence_pending"}


def test_render_review_png_manifest_creates_target_centered_png(tmp_path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    tif = tmp_path / "chip.tif"
    Image.new("RGB", (128, 128), (40, 40, 40)).save(tif, format="TIFF")
    target = ChipTarget(
        chip_id="chip_001",
        target_id="target_01",
        target_label="T01",
        target_index=1,
        region_key="johannesburg",
        grid_id="JNB0202",
        offset_x_m=0.0,
        offset_y_m=0.0,
        search_radius_m=8.0,
        chip_size_m=96.0,
    )
    artifact = ChipArtifact(
        chip_id="chip_001",
        capture_date="2018-03-30",
        version="1",
        path=tif,
        actual_zoom=20,
        status="ok",
    )

    rows = render_review_png_manifest(
        targets_by_chip={"chip_001": [target]},
        artifacts_by_chip={"chip_001": [artifact]},
        dates=["2018-03-30", "2019-07-30"],
    )

    assert [row["render_status"] for row in rows] == ["ok", "failed"]
    assert "missing_source_artifact_for_date" in str(rows[1]["render_notes"])
    review_png = Path(str(rows[0]["review_png_path"]))
    assert review_png.exists()
    assert ".target-T01-" in review_png.name
