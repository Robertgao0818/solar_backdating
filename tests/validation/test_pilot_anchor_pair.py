"""Unit tests for anchor-pair pilot pure helpers (pre-reg 2026-07-10)."""

from __future__ import annotations

import numpy as np

from scripts.validation.pilot_anchor_pair_2026_07_10 import (
    area_bucket,
    build_eligibility,
    build_transition_mask,
    earliest_absent_key,
    mlp_param_count,
    pair_features,
    r1_verdict,
    stratum_metrics,
    transition_band_dates,
)


def test_earliest_absent_key():
    assert earliest_absent_key(["2020-01-01", "2018-06-01"], ["present", "absent"]) == "2018-06-01"
    assert earliest_absent_key(["2020-01-01"], ["present"]) is None


def test_transition_band_dates_basic():
    dates = ["2018", "2019", "2020", "2021", "2022"]
    labels = ["absent", "absent", "present", "present", "present"]
    # last_absent=2019, first_present=2020 → ±1 → 2018..2021
    assert transition_band_dates(dates, labels) == {"2018", "2019", "2020", "2021"}


def test_transition_band_empty_without_present():
    assert transition_band_dates(["2018", "2019"], ["absent", "absent"]) == set()


def test_transition_band_empty_without_absent_before_present():
    # present from first frame — no last-absent before first-present
    assert transition_band_dates(["2018", "2019"], ["present", "present"]) == set()


def test_eligibility_and_pair_features():
    anchors = np.array(["a1", "a1", "a1", "a2", "a2"])
    dates = np.array(["2018", "2019", "2020", "2019", "2020"])
    labels = np.array(["absent", "present", "present", "present", "present"])
    feats = np.eye(5, 4, dtype=np.float32)  # 5 rows, dim 4

    mask, anchor_date, meta = build_eligibility(anchors, dates, labels)
    assert meta["n_anchors_eligible"] == 1
    assert meta["n_anchors_ineligible"] == 1
    assert anchor_date["a1"] == "2018"
    assert mask.tolist() == [True, True, True, False, False]

    tb, tb_meta = build_transition_mask(anchors, dates, labels, mask)
    assert tb_meta["n_anchors_with_transition_band"] == 1
    # last_absent=2018, first_present=2019 → ±1 → 2018,2019,2020
    assert tb.tolist() == [True, True, True, False, False]

    pairs, keep, pmeta = pair_features(feats, anchors, dates, anchor_date, mask)
    assert pmeta["n_pairs"] == 3
    assert keep.tolist() == [True, True, True, False, False]
    # cand at 2018 is also the anchor → diff zero
    assert pairs.shape == (3, 12)
    np.testing.assert_allclose(pairs[0, 8:], 0.0)


def test_stratum_metrics_fp_fn():
    teacher = ["absent", "absent", "present", "present", "unusable"]
    student = ["present", "absent", "absent", "present", "unusable"]
    m = stratum_metrics(teacher, student)
    assert m["fp"] == 1
    assert m["fn"] == 1
    assert m["pa_agree"] == 2
    assert m["pa_decided"] == 4
    assert m["unusable_recall"] == 1.0


def test_r1_go_and_kill():
    def arm(fp, fn, agree, ur):
        return {
            "transition_band": {"fp": fp, "fn": fn},
            "overall_eligible_report": {
                "decided_agreement": agree,
                "unusable_recall": ur,
                "n_teacher_unusable": 10,
            },
        }

    # A cuts FP/FN by 50%, keeps agree and unusable
    go = r1_verdict(arm(5, 5, 0.80, 0.50), arm(10, 10, 0.79, 0.50))
    assert go["verdict"] == "GO"

    # one-sided FP fix only → KILL
    kill = r1_verdict(arm(5, 10, 0.80, 0.50), arm(10, 10, 0.79, 0.50))
    assert kill["verdict"] == "KILL"


def test_mlp_param_budget():
    # pair dim 1152, hidden 512/256 → well under 5M
    n = mlp_param_count(1152, (512, 256), 3)
    assert n < 5_000_000
    assert n == 1152 * 512 + 512 + 512 * 256 + 256 + 256 * 3 + 3


def test_area_bucket():
    assert area_bucket(10) == "a_xs<15"
    assert area_bucket(20) == "b_sm15-40"
    assert area_bucket(50) == "c_md40-100"
    assert area_bucket(100) == "d_lg>=100"
    assert area_bucket(None) == "unknown"
