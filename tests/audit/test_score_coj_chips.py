"""Tests for cohort-scale resume/chunk helpers in `scripts.audit.score_coj_chips`
(ISSUE-09 WP-C).

Pure CSV-fixture tests on ``tmp_path`` for the three pure helpers
(`pending_score_rows`, `append_scored_rows`, `pending_classifier_chips`); no
network, no GPU, no model/checkpoint loads. `score_manifest_resumable`'s
skip/flush loop is exercised by monkeypatching `score_chip_file` with a fake
scorer (no torch model is constructed, `model`/`device` are passed as
``None``) - this only substitutes the per-chip GPU call, the resume/chunk
logic around it is real production code. `load_detector` / `score_chip_file`
themselves stay untested here, same as the pilot's own GPU code.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

import scripts.audit.score_coj_chips as score_coj_chips
from scripts.audit.score_coj_chips import (
    append_scored_rows,
    pending_classifier_chips,
    pending_score_rows,
    score_manifest_resumable,
)


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _make_chip(tmp_path: Path, name: str) -> Path:
    chip = tmp_path / "chips" / name
    chip.parent.mkdir(parents=True, exist_ok=True)
    chip.write_bytes(b"fake-tif-bytes")
    return chip


# ---------------------------------------------------------------------------
# pending_score_rows
# ---------------------------------------------------------------------------


def test_pending_score_rows_absent_scored_csv_returns_everything(tmp_path):
    rows = [{"chip_path": "a.tif"}, {"chip_path": "b.tif"}]
    scored_csv = tmp_path / "scored.csv"  # never written
    assert pending_score_rows(rows, scored_csv) == rows


def test_pending_score_rows_empty_scored_csv_returns_everything(tmp_path):
    rows = [{"chip_path": "a.tif"}, {"chip_path": "b.tif"}]
    scored_csv = tmp_path / "scored.csv"
    scored_csv.write_text("")  # exists but 0 bytes
    assert pending_score_rows(rows, scored_csv) == rows


def test_pending_score_rows_all_scored_returns_empty(tmp_path):
    rows = [{"chip_path": "a.tif"}, {"chip_path": "b.tif"}]
    scored_csv = tmp_path / "scored.csv"
    _write_csv(
        scored_csv,
        [{"chip_path": "a.tif", "score": "0.9"}, {"chip_path": "b.tif", "score": "0.1"}],
        fieldnames=["chip_path", "score"],
    )
    assert pending_score_rows(rows, scored_csv) == []


def test_pending_score_rows_partial_returns_only_unscored(tmp_path):
    rows = [{"chip_path": "a.tif"}, {"chip_path": "b.tif"}, {"chip_path": "c.tif"}]
    scored_csv = tmp_path / "scored.csv"
    _write_csv(scored_csv, [{"chip_path": "a.tif", "score": "0.9"}], fieldnames=["chip_path", "score"])
    pending = pending_score_rows(rows, scored_csv)
    assert [r["chip_path"] for r in pending] == ["b.tif", "c.tif"]


# ---------------------------------------------------------------------------
# append_scored_rows
# ---------------------------------------------------------------------------


def test_append_scored_rows_creates_header_when_absent(tmp_path):
    scored_csv = tmp_path / "scored.csv"
    append_scored_rows(
        [{"chip_path": "a.tif", "score": 0.9, "detector_status": "scored"}],
        scored_csv,
        fieldnames=["chip_path", "score", "detector_status"],
    )
    with open(scored_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["chip_path"] == "a.tif"


def test_append_scored_rows_appends_without_duplicate_header(tmp_path):
    scored_csv = tmp_path / "scored.csv"
    fieldnames = ["chip_path", "score", "detector_status"]
    append_scored_rows(
        [{"chip_path": "a.tif", "score": 0.9, "detector_status": "scored"}], scored_csv, fieldnames=fieldnames
    )
    append_scored_rows(
        [{"chip_path": "b.tif", "score": 0.1, "detector_status": "scored"}], scored_csv, fieldnames=fieldnames
    )
    with open(scored_csv, newline="") as f:
        lines = f.readlines()
    header = ",".join(fieldnames)
    assert lines[0].strip() == header
    assert sum(1 for line in lines if line.strip() == header) == 1
    with open(scored_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["chip_path"] for r in rows] == ["a.tif", "b.tif"]


def test_append_scored_rows_round_trip_then_pending_empty(tmp_path):
    rows = [{"chip_path": "a.tif"}, {"chip_path": "b.tif"}]
    scored_csv = tmp_path / "scored.csv"
    fieldnames = ["chip_path", "score", "detector_status"]
    scored_rows = [
        {"chip_path": "a.tif", "score": 0.9, "detector_status": "scored"},
        {"chip_path": "b.tif", "score": 0.05, "detector_status": "scored"},
    ]
    append_scored_rows(scored_rows, scored_csv, fieldnames=fieldnames)
    assert pending_score_rows(rows, scored_csv) == []


def test_append_scored_rows_fills_missing_keys_blank(tmp_path):
    scored_csv = tmp_path / "scored.csv"
    fieldnames = ["chip_path", "score", "detector_status", "classifier_pv_prob"]
    append_scored_rows(
        [{"chip_path": "a.tif", "score": 0.9, "detector_status": "scored"}], scored_csv, fieldnames=fieldnames
    )
    with open(scored_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["classifier_pv_prob"] == ""


# ---------------------------------------------------------------------------
# pending_classifier_chips
# ---------------------------------------------------------------------------


def test_pending_classifier_chips_absent_scores_csv_returns_all_scored(tmp_path):
    scored_rows = [
        {"chip_path": "a.tif", "detector_status": "scored"},
        {"chip_path": "b.tif", "detector_status": "scored"},
    ]
    classifier_scores_csv = tmp_path / "classifier_scores.csv"  # never written
    assert set(pending_classifier_chips(scored_rows, classifier_scores_csv)) == {"a.tif", "b.tif"}


def test_pending_classifier_chips_excludes_already_classified(tmp_path):
    scored_rows = [
        {"chip_path": "a.tif", "detector_status": "scored"},
        {"chip_path": "b.tif", "detector_status": "scored"},
    ]
    classifier_scores_csv = tmp_path / "classifier_scores.csv"
    _write_csv(
        classifier_scores_csv, [{"chip_path": "a.tif", "pv_prob": "0.9"}], fieldnames=["chip_path", "pv_prob"]
    )
    assert pending_classifier_chips(scored_rows, classifier_scores_csv) == ["b.tif"]


def test_pending_classifier_chips_excludes_non_scored_detector_status(tmp_path):
    scored_rows = [
        {"chip_path": "a.tif", "detector_status": "missing_chip"},
        {"chip_path": "b.tif", "detector_status": "unreadable"},
        {"chip_path": "c.tif", "detector_status": "scored"},
    ]
    classifier_scores_csv = tmp_path / "classifier_scores.csv"
    assert pending_classifier_chips(scored_rows, classifier_scores_csv) == ["c.tif"]


# ---------------------------------------------------------------------------
# score_manifest_resumable (skip/flush loop; score_chip_file faked, no GPU)
# ---------------------------------------------------------------------------


def test_score_manifest_resumable_scores_all_pending_and_writes_csv(tmp_path, monkeypatch):
    chip_a = _make_chip(tmp_path, "a.tif")
    chip_b = _make_chip(tmp_path, "b.tif")
    rows = [
        {"chip_path": str(chip_a), "anchor_id": "anc_a", "year": "2019"},
        {"chip_path": str(chip_b), "anchor_id": "anc_b", "year": "2023"},
    ]
    scored_csv = tmp_path / "scored.csv"

    fake_scores = {str(chip_a): 0.99, str(chip_b): 0.02}
    calls: list[str] = []

    def fake_score_chip_file(tif_path, model, device, *, chip_size, overlap):
        calls.append(str(tif_path))
        return fake_scores[str(tif_path)]

    monkeypatch.setattr(score_coj_chips, "score_chip_file", fake_score_chip_file)

    out = score_manifest_resumable(rows, model=None, device=None, scored_csv=scored_csv, flush_every=200)
    assert out == scored_csv
    with open(scored_csv, newline="") as f:
        scored_rows = list(csv.DictReader(f))
    assert {r["chip_path"] for r in scored_rows} == {str(chip_a), str(chip_b)}
    assert len(calls) == 2
    by_path = {r["chip_path"]: r for r in scored_rows}
    assert float(by_path[str(chip_a)]["score"]) == pytest.approx(0.99)
    assert by_path[str(chip_a)]["detector_status"] == "scored"


def test_score_manifest_resumable_skips_already_scored_chips(tmp_path, monkeypatch):
    chip_a = _make_chip(tmp_path, "a.tif")
    chip_b = _make_chip(tmp_path, "b.tif")
    rows = [
        {"chip_path": str(chip_a), "anchor_id": "anc_a"},
        {"chip_path": str(chip_b), "anchor_id": "anc_b"},
    ]
    scored_csv = tmp_path / "scored.csv"
    fieldnames = sorted({"chip_path", "anchor_id", "score", "detector_status"})
    append_scored_rows(
        [{"chip_path": str(chip_a), "anchor_id": "anc_a", "score": 0.9, "detector_status": "scored"}],
        scored_csv,
        fieldnames=fieldnames,
    )

    calls: list[str] = []

    def fake_score_chip_file(tif_path, model, device, *, chip_size, overlap):
        calls.append(str(tif_path))
        return 0.5

    monkeypatch.setattr(score_coj_chips, "score_chip_file", fake_score_chip_file)

    score_manifest_resumable(rows, model=None, device=None, scored_csv=scored_csv, flush_every=200)

    assert calls == [str(chip_b)]  # only the unscored chip was scored
    with open(scored_csv, newline="") as f:
        scored_rows = list(csv.DictReader(f))
    assert {r["chip_path"] for r in scored_rows} == {str(chip_a), str(chip_b)}


def test_score_manifest_resumable_flushes_in_chunks(tmp_path, monkeypatch):
    chips = [_make_chip(tmp_path, f"c{i}.tif") for i in range(5)]
    rows = [{"chip_path": str(c)} for c in chips]
    scored_csv = tmp_path / "scored.csv"

    flush_calls: list[int] = []
    real_append = score_coj_chips.append_scored_rows

    def spy_append(rows_, csv_path, *, fieldnames):
        flush_calls.append(len(rows_))
        real_append(rows_, csv_path, fieldnames=fieldnames)

    monkeypatch.setattr(score_coj_chips, "append_scored_rows", spy_append)
    monkeypatch.setattr(score_coj_chips, "score_chip_file", lambda *a, **kw: 0.5)

    score_manifest_resumable(rows, model=None, device=None, scored_csv=scored_csv, flush_every=2)

    assert flush_calls == [2, 2, 1]  # 5 rows at flush_every=2 -> chunks of 2,2,1
    with open(scored_csv, newline="") as f:
        scored_rows = list(csv.DictReader(f))
    assert len(scored_rows) == 5


def test_score_manifest_resumable_handles_missing_chip_file(tmp_path, monkeypatch):
    missing = tmp_path / "chips" / "gone.tif"  # never created
    rows = [{"chip_path": str(missing)}]
    scored_csv = tmp_path / "scored.csv"

    def fail_if_called(*a, **kw):
        raise AssertionError("score_chip_file must not be called for a missing chip")

    monkeypatch.setattr(score_coj_chips, "score_chip_file", fail_if_called)

    score_manifest_resumable(rows, model=None, device=None, scored_csv=scored_csv)
    with open(scored_csv, newline="") as f:
        scored_rows = list(csv.DictReader(f))
    assert scored_rows[0]["detector_status"] == "missing_chip"
    assert scored_rows[0]["score"] == ""


def test_score_manifest_resumable_no_pending_rows_is_a_noop(tmp_path, monkeypatch):
    chip_a = _make_chip(tmp_path, "a.tif")
    rows = [{"chip_path": str(chip_a)}]
    scored_csv = tmp_path / "scored.csv"
    fieldnames = sorted({"chip_path", "score", "detector_status"})
    append_scored_rows(
        [{"chip_path": str(chip_a), "score": 0.9, "detector_status": "scored"}],
        scored_csv,
        fieldnames=fieldnames,
    )

    def fail_if_called(*a, **kw):
        raise AssertionError("score_chip_file must not be called when nothing is pending")

    monkeypatch.setattr(score_coj_chips, "score_chip_file", fail_if_called)

    score_manifest_resumable(rows, model=None, device=None, scored_csv=scored_csv)
    with open(scored_csv, newline="") as f:
        scored_rows = list(csv.DictReader(f))
    assert len(scored_rows) == 1
