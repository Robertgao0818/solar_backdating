from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import scripts.temporal.score_chip_group_matrix as scgm
from scripts.temporal.score_chip_group_matrix import (
    ChipArtifact,
    ChipTarget,
    score_chip_group_matrices,
)
from scripts.validation.gemini_solar_image_review import (
    GeminiClientConfig,
    GeminiMatrixObservation,
)


def _config() -> GeminiClientConfig:
    return GeminiClientConfig(base_url="https://stub.example", api_key="stub")


def _write_tif(path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (96, 96), (40, 40, 40)).save(path, format="TIFF")


def _targets(count: int) -> list[ChipTarget]:
    out: list[ChipTarget] = []
    for idx in range(1, count + 1):
        out.append(
            ChipTarget(
                chip_id="chip_001",
                target_id=f"target_{idx:02d}",
                target_label=f"T{idx:02d}",
                target_index=idx,
                region_key="johannesburg",
                grid_id="G0001",
                offset_x_m=float((idx - 1) * 6),
                offset_y_m=0.0,
                search_radius_m=8.0,
                chip_size_m=96.0,
            )
        )
    return out


def _artifacts(tmp_path: Path, count: int) -> list[ChipArtifact]:
    out: list[ChipArtifact] = []
    for idx in range(1, count + 1):
        path = tmp_path / f"chip_{idx}.tif"
        _write_tif(path)
        out.append(
            ChipArtifact(
                chip_id="chip_001",
                capture_date=f"2020-01-{idx:02d}",
                version=str(100 + idx),
                path=path,
                actual_zoom=20,
                status="ok",
            )
        )
    return out


def test_score_chip_group_matrices_writes_target_level_presence_rows(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_scorer(date_picks, targets, *, config, audit_writer, **_kwargs):
        calls.append(
            {
                "date_count": len(date_picks),
                "target_labels": [t.target_label for t in targets],
                "image_paths": [p.chip_path for p in date_picks],
            }
        )
        assert config is not None
        assert all(p.chip_path.exists() and ".targets-" in p.chip_path.name for p in date_picks)
        if audit_writer is not None:
            audit_writer({"stage": "matrix_attempt_1", "n_valid": len(date_picks) * len(targets)})
        rows: list[GeminiMatrixObservation] = []
        cell_index = 1
        for pick in date_picks:
            for target in targets:
                rows.append(
                    GeminiMatrixObservation(
                        cell_index=cell_index,
                        date_index=pick.date_index,
                        capture_date=pick.capture_date,
                        target_id=target.target_id,
                        target_label=target.target_label,
                        pv_present=True,
                        confidence=0.91,
                        quality_flag="usable",
                        evidence=f"{target.target_label} panels",
                        notes="",
                        decision_source="gemini_matrix",
                    )
                )
                cell_index += 1
        return rows

    rows = score_chip_group_matrices(
        artifacts_by_chip={"chip_001": _artifacts(tmp_path, 2)},
        targets_by_chip={"chip_001": _targets(2)},
        config=_config(),
        audit_dir=tmp_path / "audit",
        scorer=fake_scorer,
    )

    assert len(rows) == 4
    assert len(calls) == 1
    assert calls[0]["date_count"] == 2
    assert calls[0]["target_labels"] == ["T01", "T02"]
    assert {row["anchor_id"] for row in rows} == {"target_01", "target_02"}
    assert {row["pv_present"] for row in rows} == {"1"}
    assert all(Path(str(row["review_png_path"])).exists() for row in rows)
    assert sorted((tmp_path / "audit" / "chip_001").glob("*.jsonl"))


def test_score_chip_group_matrices_splits_dates_and_targets(tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []

    def fake_scorer(date_picks, targets, **_kwargs):
        calls.append((len(date_picks), len(targets)))
        out: list[GeminiMatrixObservation] = []
        cell_index = 1
        for pick in date_picks:
            for target in targets:
                out.append(
                    GeminiMatrixObservation(
                        cell_index=cell_index,
                        date_index=pick.date_index,
                        capture_date=pick.capture_date,
                        target_id=target.target_id,
                        target_label=target.target_label,
                        pv_present=False,
                        confidence=0.8,
                        quality_flag="usable",
                        evidence="no PV",
                        notes="",
                        decision_source="gemini_matrix",
                    )
                )
                cell_index += 1
        return out

    rows = score_chip_group_matrices(
        artifacts_by_chip={"chip_001": _artifacts(tmp_path, 6)},
        targets_by_chip={"chip_001": _targets(5)},
        config=_config(),
        scorer=fake_scorer,
        max_dates=5,
        max_targets=4,
        hard_max_cells=24,
    )

    assert calls == [(5, 4), (1, 4), (5, 1), (1, 1)]
    assert len(rows) == 30
    assert {row["pv_present"] for row in rows} == {"0"}


def test_score_chip_group_matrices_flags_non_monotonic_target_series(tmp_path: Path) -> None:
    def fake_scorer(date_picks, targets, **_kwargs):
        out: list[GeminiMatrixObservation] = []
        pattern = {1: False, 2: True, 3: False}
        cell_index = 1
        for pick in date_picks:
            for target in targets:
                out.append(
                    GeminiMatrixObservation(
                        cell_index=cell_index,
                        date_index=pick.date_index,
                        capture_date=pick.capture_date,
                        target_id=target.target_id,
                        target_label=target.target_label,
                        pv_present=pattern[pick.date_index],
                        confidence=0.8,
                        quality_flag="usable",
                        evidence="stub",
                        notes="",
                        decision_source="gemini_matrix",
                    )
                )
                cell_index += 1
        return out

    rows = score_chip_group_matrices(
        artifacts_by_chip={"chip_001": _artifacts(tmp_path, 3)},
        targets_by_chip={"chip_001": _targets(1)},
        config=_config(),
        scorer=fake_scorer,
        max_dates=3,
        max_targets=1,
    )

    assert len(rows) == 3
    assert all("non_monotonic_requires_review" in str(row["notes"]) for row in rows)


def _ok_scorer(date_picks, targets, **_kwargs):
    out: list[GeminiMatrixObservation] = []
    cell_index = 1
    for pick in date_picks:
        for target in targets:
            out.append(
                GeminiMatrixObservation(
                    cell_index=cell_index,
                    date_index=pick.date_index,
                    capture_date=pick.capture_date,
                    target_id=target.target_id,
                    target_label=target.target_label,
                    pv_present=True,
                    confidence=0.9,
                    quality_flag="usable",
                    evidence="panels",
                    notes="",
                    decision_source="gemini_matrix",
                )
            )
            cell_index += 1
    return out


def test_score_chip_group_matrices_degrades_on_corrupt_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt/unreadable artifact image must not crash the batch."""

    def raising_render(*_args, **_kwargs):
        raise OSError("cannot identify image file: truncated TIFF")

    monkeypatch.setattr(scgm, "ensure_target_review_png", raising_render)

    rows = score_chip_group_matrices(
        artifacts_by_chip={"chip_001": _artifacts(tmp_path, 2)},
        targets_by_chip={"chip_001": _targets(2)},
        config=_config(),
        scorer=_ok_scorer,
    )

    assert len(rows) == 4
    assert all(row["pv_present"] == "" for row in rows)
    assert all(row["quality_flag"] == "unusable" for row in rows)
    assert all(row["decision_source"] == "gemini_failed" for row in rows)
    assert all("cannot identify image file" in str(row["gemini_error"]) for row in rows)
    assert {row["anchor_id"] for row in rows} == {"target_01", "target_02"}


def _chip(chip_id: str, target_id: str, target_label: str) -> ChipTarget:
    return ChipTarget(
        chip_id=chip_id,
        target_id=target_id,
        target_label=target_label,
        target_index=1,
        region_key="johannesburg",
        grid_id="G0001",
        offset_x_m=0.0,
        offset_y_m=0.0,
        search_radius_m=8.0,
        chip_size_m=96.0,
    )


def _artifact(chip_id: str, path: Path) -> ChipArtifact:
    _write_tif(path)
    return ChipArtifact(
        chip_id=chip_id,
        capture_date="2020-01-01",
        version="101",
        path=path,
        actual_zoom=20,
        status="ok",
    )


def test_score_chip_group_matrices_degrades_on_scorer_exception_and_continues(
    tmp_path: Path,
) -> None:
    """A scorer that raises for one chip group must degrade that group and keep
    scoring the remaining chip groups in the batch."""
    seen_chips: list[str] = []

    def flaky_scorer(date_picks, targets, **_kwargs):
        chip_label = targets[0].target_label
        seen_chips.append(chip_label)
        if chip_label == "T01":
            raise RuntimeError("matrix scorer blew up")
        return _ok_scorer(date_picks, targets)

    # chip_ids are scored in sorted order: chip_a (raises) then chip_b (ok).
    rows = score_chip_group_matrices(
        artifacts_by_chip={
            "chip_a": [_artifact("chip_a", tmp_path / "a.tif")],
            "chip_b": [_artifact("chip_b", tmp_path / "b.tif")],
        },
        targets_by_chip={
            "chip_a": [_chip("chip_a", "target_a01", "T01")],
            "chip_b": [_chip("chip_b", "target_b01", "T02")],
        },
        config=_config(),
        scorer=flaky_scorer,
    )

    assert seen_chips == ["T01", "T02"]
    by_anchor = {row["anchor_id"]: row for row in rows}
    assert set(by_anchor) == {"target_a01", "target_b01"}
    # Failed group degraded to unusable.
    assert by_anchor["target_a01"]["quality_flag"] == "unusable"
    assert by_anchor["target_a01"]["pv_present"] == ""
    assert by_anchor["target_a01"]["decision_source"] == "gemini_failed"
    assert "matrix scorer blew up" in str(by_anchor["target_a01"]["gemini_error"])
    # Healthy group still scored.
    assert by_anchor["target_b01"]["quality_flag"] == "usable"
    assert by_anchor["target_b01"]["pv_present"] == "1"
    assert by_anchor["target_b01"]["decision_source"] == "gemini_matrix"
