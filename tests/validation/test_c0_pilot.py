"""Unit tests for the Path C0 pilot (ISSUE-09) — decode math, anchor rule, gate rules.

Prereg: docs/dinov3_scorer/DATA-c0-reverse-template-prereg-2026-07-12.md
"""

from __future__ import annotations

import numpy as np
import pytest

from scripts.validation.check_student_path_gate import check_c0_r1, check_c0_s0
from scripts.validation.pilot_c0_reverse_template_2026_07_12 import (
    bayes_single_changepoint,
    glr_changepoint,
    isotonic_step,
    persistence_ok,
    rank_auc,
    select_anchor,
    shift_mask,
    C0Config,
)


# ---------------------------------------------------------------- decode math

DATES = np.array([0, 200, 400, 600, 800, 1000])  # days, irregular ok


def test_bayes_finds_clean_step():
    s = np.array([0.02, -0.01, 0.00, 0.55, 0.60, 0.58])
    out = bayes_single_changepoint(s, DATES)
    assert out["map_gap"] == 2
    assert out["q"] > 0.8
    assert out["p_nochange"] < 0.1


def test_bayes_flat_series_prefers_nochange():
    s = np.array([0.30, 0.31, 0.29, 0.30, 0.32, 0.30])
    out = bayes_single_changepoint(s, DATES)
    assert out["p_nochange"] > 0.5


def test_bayes_short_series_abstains():
    out = bayes_single_changepoint(np.array([0.1, 0.9]), DATES[:2])
    assert out["map_gap"] is None
    assert out["p_nochange"] == 1.0


def test_bayes_weights_downweight_outlier():
    # one cloudy spike pre-step; with low weight it must not steal the MAP gap
    s = np.array([0.00, 0.70, 0.02, 0.60, 0.62, 0.61])
    w = np.array([1.0, 0.05, 1.0, 1.0, 1.0, 1.0])
    out = bayes_single_changepoint(s, DATES, w)
    assert out["map_gap"] == 2


def test_glr_and_isotonic_agree_on_step():
    s = np.array([0.0, 0.01, -0.02, 0.5, 0.55, 0.52])
    assert glr_changepoint(s) == 2
    assert isotonic_step(s) == 2


def test_isotonic_rejects_downward_step():
    s = np.array([0.5, 0.55, 0.52, 0.0, 0.01, -0.02])
    assert isotonic_step(s) is None


def test_persistence_rule():
    s = np.array([0.0, 0.0, 0.6, 0.65, 0.62])
    usable = np.ones(5, dtype=bool)
    assert persistence_ok(s, usable, 1, 2)
    # only one usable post-step frame -> not persistent
    usable2 = np.array([True, True, True, False, False])
    assert not persistence_ok(s, usable2, 1, 2)


# ------------------------------------------------------------- anchor rule

T_C = 10_000


def test_anchor_right_nearest_wins():
    dates = [T_C - 900, T_C - 100, T_C + 50, T_C + 400]
    sel = select_anchor(dates, [True] * 4, T_C, delta_left_days=365)
    assert sel["anchor_idx"] == 2 and sel["anchor_side"] == "right"


def test_anchor_left_nearest_wins_within_window():
    dates = [T_C - 900, T_C - 30, T_C + 200]
    sel = select_anchor(dates, [True] * 3, T_C, delta_left_days=365)
    assert sel["anchor_idx"] == 1 and sel["anchor_side"] == "left"


def test_anchor_left_too_far_falls_to_right():
    dates = [T_C - 900, T_C + 200]
    sel = select_anchor(dates, [True] * 2, T_C, delta_left_days=365)
    assert sel["anchor_idx"] == 1 and sel["anchor_side"] == "right"


def test_anchor_consistency_gate_rejects_left():
    dates = [T_C - 30, T_C + 200]
    sel = select_anchor(dates, [True] * 2, T_C, delta_left_days=365,
                        left_right_cos=0.10, theta_anchor=0.5)
    assert sel["anchor_side"] == "right"
    assert sel["anchor_left_rejected"] is True


def test_anchor_left_only_flagged():
    dates = [T_C - 400, T_C - 30]
    sel = select_anchor(dates, [True] * 2, T_C, delta_left_days=365)
    assert sel["anchor_side"] == "left_only"


def test_anchor_none_when_nothing_eligible():
    dates = [T_C - 900, T_C - 800]
    sel = select_anchor(dates, [True] * 2, T_C, delta_left_days=365)
    assert sel["anchor_idx"] is None and sel["anchor_side"] == "none"


# -------------------------------------------------------------- small utils

def test_rank_auc_perfect_and_tied():
    assert rank_auc([1.0, 2.0], [0.0, 0.5]) == 1.0
    assert rank_auc([1.0], [1.0]) == 0.5
    assert np.isnan(rank_auc([], [1.0]))


def test_shift_mask_no_wrap():
    m = np.zeros((5, 5), dtype=bool)
    m[0, 0] = True
    out = shift_mask(m, -1, -1)  # would wrap; must vanish instead
    assert not out.any()
    out2 = shift_mask(m, 2, 3)
    assert out2[2, 3] and out2.sum() == 1


def test_config_hash_changes_with_knobs():
    assert C0Config().hash() != C0Config(shift_radius_patches=4).hash()


# ---------------------------------------------------------------- gate rules

C0_S0_GO = {"c0_smoke_auc": 0.81, "c0_smoke_n_anchors": 72}

C0_R1_GO = {
    "c0_replicate_equiv_delta_pp": -1.2,
    "c0_replicate_equiv_delta_ci_low_pp": -4.0,
    "c0_adjudication_correct_share": 0.45,
    "c0_adjudication_n": 44,
    "c0_safety_polarity_rate": 0.0,
    "c0_safety_all_inspected": 1,
}


def test_c0_s0_go_when_all_criteria_met():
    ok, lines = check_c0_s0(C0_S0_GO)
    assert ok and any("GO" in ln for ln in lines)


@pytest.mark.parametrize("key,bad", [
    ("c0_smoke_auc", 0.70),
    ("c0_smoke_n_anchors", 30),
])
def test_c0_s0_kill_on_violation(key, bad):
    ok, _ = check_c0_s0(dict(C0_S0_GO, **{key: bad}))
    assert not ok


def test_c0_s0_fails_closed_on_missing_keys():
    m = dict(C0_S0_GO)
    del m["c0_smoke_auc"]
    ok, lines = check_c0_s0(m)
    assert not ok
    assert any("FAIL-CLOSED" in ln and "c0_smoke_auc" in ln for ln in lines)


def test_c0_r1_go_when_all_criteria_met():
    ok, lines = check_c0_r1(C0_R1_GO)
    assert ok and any("GO" in ln for ln in lines)


@pytest.mark.parametrize("key,bad", [
    ("c0_replicate_equiv_delta_ci_low_pp", -6.5),
    ("c0_adjudication_correct_share", 0.20),
    ("c0_adjudication_n", 12),
    ("c0_safety_polarity_rate", 0.02),
    ("c0_safety_all_inspected", 0),
])
def test_c0_r1_kill_on_violation(key, bad):
    ok, _ = check_c0_r1(dict(C0_R1_GO, **{key: bad}))
    assert not ok


def test_c0_r1_fails_closed_on_missing_keys():
    m = dict(C0_R1_GO)
    del m["c0_adjudication_n"]
    ok, lines = check_c0_r1(m)
    assert not ok
    assert any("FAIL-CLOSED" in ln and "c0_adjudication_n" in ln for ln in lines)
