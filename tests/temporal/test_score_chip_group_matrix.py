from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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
