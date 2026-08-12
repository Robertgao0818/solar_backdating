from collections import Counter

from scripts.temporal.prepare_ct11_disjoint_holdout import (
    proportional_counts,
    select_holdout,
    select_repeat,
    write_text_locked,
)


def _rows() -> list[dict[str, str]]:
    rows = []
    for grid in ("G1", "G2", "G3"):
        for index in range(20):
            rows.append(
                {
                    "anchor_id": f"{grid}-{index:02d}",
                    "grid_id": grid,
                    "status": "done_appears" if index % 3 else "done_installed_during_census",
                    "confidence": "high" if index % 2 else "medium",
                }
            )
    return rows


def test_proportional_counts_is_exact_and_capped() -> None:
    result = proportional_counts({"a": 2, "b": 8}, 7)
    assert sum(result.values()) == 7
    assert result["a"] <= 2
    assert result["b"] <= 8


def test_holdout_is_disjoint_deterministic_and_grid_bounded() -> None:
    rows = _rows()
    excluded = {"G1-00", "G2-00", "G3-00"}
    first = select_holdout(rows, excluded, n=30, grid_min=5, seed=42)
    second = select_holdout(rows, excluded, n=30, grid_min=5, seed=42)
    assert first == second
    assert len({row["anchor_id"] for row in first}) == 30
    assert not ({row["anchor_id"] for row in first} & excluded)
    assert min(Counter(row["grid_id"] for row in first).values()) >= 5


def test_repeat_is_exact_subset_and_deterministic() -> None:
    selected = select_holdout(_rows(), set(), n=30, grid_min=5, seed=42)
    first = select_repeat(selected, n=6, seed=43)
    second = select_repeat(selected, n=6, seed=43)
    assert first == second
    assert len(first) == 6
    assert {row["anchor_id"] for row in first} <= {row["anchor_id"] for row in selected}


def test_locked_text_is_idempotent_and_rejects_changes(tmp_path) -> None:
    path = tmp_path / "frozen.txt"
    write_text_locked(path, "same\n")
    write_text_locked(path, "same\n")
    assert path.stat().st_mode & 0o777 == 0o444

    try:
        write_text_locked(path, "different\n")
    except ValueError as exc:
        assert "existing frozen output differs" in str(exc)
    else:
        raise AssertionError("changed frozen output must fail closed")
