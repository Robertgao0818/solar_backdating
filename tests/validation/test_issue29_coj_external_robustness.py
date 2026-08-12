import pandas as pd

from scripts.validation.issue29_coj_external_robustness import (
    _cluster_bootstrap,
    _select_context_rows,
    expected_state_for_year,
)


def test_year_interval_expected_state_is_conservative():
    assert expected_state_for_year("done_appears", "2020-01-01", "2021-01-01", 2019) == "absent"
    assert expected_state_for_year("done_appears", "2020-01-01", "2021-01-01", 2023) == "present"
    assert expected_state_for_year("done_appears", "2019-06-01", "2020-01-01", 2019) == "non_identifying"


def test_left_censored_never_invents_absence():
    assert expected_state_for_year("done_already_present_before_geid_history", None, "2018-04-01", 2019) == "present"
    assert expected_state_for_year("done_already_present_before_geid_history", None, "2020-04-01", 2019) == "non_identifying"


def test_ambiguous_and_invalid_bounds_are_non_identifying():
    assert expected_state_for_year("done_ambiguous_nonmonotonic", None, None, 2023) == "non_identifying"
    assert expected_state_for_year("done_appears", "2022-01-01", "2021-01-01", 2023) == "non_identifying"


def test_context_selection_is_earliest_median_latest_and_outcome_blind(tmp_path):
    paths = []
    for i in range(5):
        path = tmp_path / f"{i}.png"
        path.write_bytes(b"x")
        paths.append(str(path))
    rows = pd.DataFrame({
        "capture_date": ["2018-01-01", "2019-01-01", "2020-01-01", "2021-01-01", "2022-01-01"],
        "chip_png_path": paths,
        "pv_present": [True, False, True, False, True],
    })
    selected = _select_context_rows(rows)
    assert selected.capture_date.tolist() == ["2018-01-01", "2020-01-01", "2022-01-01"]
    assert "pv_present" not in selected.columns


def test_cluster_bootstrap_uses_group_level_resampling():
    rows = pd.DataFrame({
        "legacy_group_anchor_id": ["g1", "g1", "g2", "g2"],
        "automated_verdict": ["present", "present", "absent", "present"],
        "expected_state": ["present", "present", "absent", "absent"],
    })
    result = _cluster_bootstrap(rows, reps=100)
    assert result["n"] == 4
    assert result["agreement"] == 0.75
    assert 0 <= result["ci_low"] <= result["ci_high"] <= 1


def test_round_robin_sampling_seed_changes_draw_but_is_repeatable():
    from scripts.validation.issue29_coj_external_robustness import _round_robin_sample

    rows = pd.DataFrame({
        "anchor_id": [f"a{i}" for i in range(20)],
        "epoch": [2019] * 20,
        "coverage_complete": [True] * 20,
        "expected_state": ["absent"] * 20,
        "area_bin": ["<15"] * 20,
        "interval_kind": ["done_appears"] * 20,
    })
    assert _round_robin_sample(rows, 5, seed=7) == _round_robin_sample(rows, 5, seed=7)
    assert _round_robin_sample(rows, 5, seed=7) != _round_robin_sample(rows, 5, seed=8)
