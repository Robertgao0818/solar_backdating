import csv
from pathlib import Path

from scripts.temporal.evaluate_ct11_human_calibration import (
    ADJUDICATION_FIELDS,
    FIELDS,
    compare_reviews,
    validate_adjudication,
    validate_review,
)


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    path.chmod(0o444)


def test_validate_and_compare_reviews(tmp_path) -> None:
    frames = {1: ["2020-01-01", "2021-01-01"], 2: ["2020-01-01", "2021-01-01"]}
    a_path, b_path = tmp_path / "a.csv", tmp_path / "b.csv"
    base = [
        {"calibration_index": "1", "independent_class": "ALREADY_PRESENT", "latest_absent": "", "earliest_present": "2020-01-01", "review_confidence": "HIGH", "rationale": "visible"},
        {"calibration_index": "2", "independent_class": "ALL_ABSENT", "latest_absent": "2021-01-01", "earliest_present": "", "review_confidence": "MEDIUM", "rationale": "clear roof"},
    ]
    other = [dict(row) for row in base]
    other[1].update({"independent_class": "UNDATABLE", "latest_absent": "", "review_confidence": "LOW", "rationale": "tree cover"})
    _write(a_path, base)
    _write(b_path, other)
    a = validate_review(a_path, frames)
    b = validate_review(b_path, frames)
    metrics, conflicts = compare_reviews(a, b)
    assert metrics["state_matches"] == 1
    assert metrics["interval_matches"] == 1
    assert metrics["n_adjudication_rows"] == 1
    assert conflicts[0]["calibration_index"] == "2"


def test_review_rejects_false_absence_date(tmp_path) -> None:
    path = tmp_path / "bad.csv"
    _write(path, [{"calibration_index": "1", "independent_class": "ALL_ABSENT", "latest_absent": "2020-01-01", "earliest_present": "", "review_confidence": "HIGH", "rationale": "claimed"}])
    try:
        validate_review(path, {1: ["2020-01-01", "2021-01-01"]})
    except ValueError as exc:
        assert "last frame" in str(exc)
    else:
        raise AssertionError("invalid ALL_ABSENT date must fail closed")


def test_adjudication_must_cover_exact_conflicts(tmp_path) -> None:
    path = tmp_path / "adjudication.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ADJUDICATION_FIELDS)
        writer.writeheader()
        writer.writerow({"calibration_index": "2", "adjudicated_class": "TRANSITION", "latest_absent": "2020-01-01", "earliest_present": "2021-01-01", "adjudicator_id": "senior-1", "rationale": "visible change"})
    path.chmod(0o444)
    result = validate_adjudication(path, {2}, {2: ["2020-01-01", "2021-01-01"]})
    assert result[2]["independent_class"] == "TRANSITION"
