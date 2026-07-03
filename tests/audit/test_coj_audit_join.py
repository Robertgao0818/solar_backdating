"""Tests for `scripts.audit.coj_audit_join` (ISSUE-08).

Pure-logic module: strata derivation (incl. exclusions), seeded sampling
determinism, the margin/routing rule, conservative-bounds contradiction
logic (both signs), and the self-gate math (>95% known-sign threshold +
noise-floor accounting). No network, no filesystem.
"""
from __future__ import annotations

import pytest

from scripts.audit.coj_audit_join import (
    EXCLUDED_STATUSES,
    build_audit_bit,
    classify_bit,
    classify_stratum,
    compute_gates,
    contradicts_absent,
    contradicts_present,
    expected_sign,
    sample_pilot,
)


# ---------------------------------------------------------------------------
# classify_stratum
# ---------------------------------------------------------------------------


def _row(status, start, end, anchor_id="a1"):
    return {
        "anchor_id": anchor_id,
        "status": status,
        "install_interval_start": start,
        "install_interval_end": end,
    }


def test_stratum1_done_appears_pre_2019():
    row = _row("done_appears", "2016-01-01", "2018-12-31")
    assert classify_stratum(row) == "s1_known_present_pre2019"


def test_stratum1_already_present_before_geid_history_pre_2019():
    row = _row("done_already_present_before_geid_history", "2010-01-01", "2015-06-01")
    assert classify_stratum(row) == "s1_known_present_pre2019"


def test_stratum1_already_present_but_end_after_2019_is_not_stratum1():
    # install_interval_end must be < 2019-01-01 for stratum 1.
    row = _row("done_already_present_before_geid_history", "2010-01-01", "2019-06-01")
    assert classify_stratum(row) is None


def test_stratum2_done_appears_2019_to_2023():
    row = _row("done_appears", "2019-06-01", "2022-11-30")
    assert classify_stratum(row) == "s2_known_present_2019_2023"


def test_stratum2_boundary_inclusive_start_exclusive_end():
    # end == 2019-01-01 boundary is stratum2 start-inclusive
    row_at_2019 = _row("done_appears", "2018-06-01", "2019-01-01")
    assert classify_stratum(row_at_2019) == "s2_known_present_2019_2023"
    # end == 2023-01-01 is NOT stratum2 (exclusive upper bound)
    row_at_2023 = _row("done_appears", "2022-06-01", "2023-01-01")
    assert classify_stratum(row_at_2023) is None


def test_stratum3_done_appears_after_2023():
    row = _row("done_appears", "2024-03-01", "2024-06-01")
    assert classify_stratum(row) == "s3_known_absent_2023"


def test_stratum3_requires_start_strictly_after_2023_12_31():
    row = _row("done_appears", "2023-12-31", "2024-06-01")
    assert classify_stratum(row) is None


@pytest.mark.parametrize("status", sorted(EXCLUDED_STATUSES))
def test_excluded_statuses_never_classified(status):
    # Even if the dates would otherwise match stratum 1/2/3, exclusions win.
    row = _row(status, "2016-01-01", "2018-12-31")
    assert classify_stratum(row) is None


def test_other_ambiguous_statuses_not_in_known_strata():
    for status in [
        "done_ambiguous_no_recent_anchor",
        "done_ambiguous_nonmonotonic",
        "done_ambiguous_gemini_failed",
    ]:
        row = _row(status, "2016-01-01", "2018-12-31")
        assert classify_stratum(row) is None


# ---------------------------------------------------------------------------
# expected_sign
# ---------------------------------------------------------------------------


def test_expected_sign_stratum1_present_both_years():
    assert expected_sign("s1_known_present_pre2019", 2019) is True
    assert expected_sign("s1_known_present_pre2019", 2023) is True


def test_expected_sign_stratum2_present_2023_unknown_2019():
    assert expected_sign("s2_known_present_2019_2023", 2023) is True
    assert expected_sign("s2_known_present_2019_2023", 2019) is None


def test_expected_sign_stratum3_absent_both_years():
    assert expected_sign("s3_known_absent_2023", 2023) is False
    assert expected_sign("s3_known_absent_2023", 2019) is False


def test_expected_sign_ambiguous_stratum_always_none():
    assert expected_sign("s4_ambiguous", 2019) is None
    assert expected_sign("s4_ambiguous", 2023) is None


# ---------------------------------------------------------------------------
# classify_bit (margin rule)
# ---------------------------------------------------------------------------


def test_classify_bit_high_margin_absent():
    assert classify_bit(0.0) == "absent"
    assert classify_bit(0.30) == "absent"


def test_classify_bit_high_margin_present_no_classifier():
    assert classify_bit(0.95) == "present"
    assert classify_bit(1.0) == "present"


def test_classify_bit_low_margin_middle_band():
    assert classify_bit(0.30001) == "low_margin"
    assert classify_bit(0.5) == "low_margin"
    assert classify_bit(0.94999) == "low_margin"


def test_classify_bit_present_demoted_by_classifier_disagreement():
    # detector says present (S=0.98) but classifier disagrees (pv_prob < 0.5)
    assert classify_bit(0.98, classifier_pv_prob=0.1) == "low_margin"


def test_classify_bit_present_confirmed_by_classifier():
    assert classify_bit(0.98, classifier_pv_prob=0.9) == "present"


def test_classify_bit_absent_ignores_classifier():
    # margin rule only checks classifier corroboration for would-be PRESENT bits
    assert classify_bit(0.1, classifier_pv_prob=0.99) == "absent"


# ---------------------------------------------------------------------------
# contradiction logic (conservative, year-only vintage bounds)
# ---------------------------------------------------------------------------


def test_contradicts_present_true_when_pipeline_claims_absent_past_year():
    # pipeline says still-absent until 2024-03-01 (> 2023-12-31) yet layer
    # shows PV in 2023 -> contradiction.
    assert contradicts_present("2024-03-01", 2023) is True


def test_contradicts_present_false_when_interval_start_within_or_before_year():
    assert contradicts_present("2023-06-01", 2023) is False
    assert contradicts_present("2023-12-31", 2023) is False


def test_contradicts_absent_true_when_pipeline_claims_present_before_year():
    # pipeline says present as early as 2022-01-01 (< 2023-01-01) yet layer
    # shows no PV in 2023 -> contradiction.
    assert contradicts_absent("2022-01-01", 2023) is True


def test_contradicts_absent_false_when_interval_end_within_or_after_year():
    assert contradicts_absent("2023-01-01", 2023) is False
    assert contradicts_absent("2023-06-01", 2023) is False


# ---------------------------------------------------------------------------
# build_audit_bit — end-to-end per (anchor, year) row
# ---------------------------------------------------------------------------


def test_build_audit_bit_low_margin_never_contradicts():
    row = {
        "anchor_id": "a1",
        "stratum": "s3_known_absent_2023",
        "year": 2023,
        "score": 0.5,  # low margin
        "install_interval_start": "2024-03-01",
        "install_interval_end": "2024-06-01",
    }
    bit = build_audit_bit(row)
    assert bit["bit"] == "low_margin"
    assert bit["contradiction"] is False
    assert bit["routed_to_queue"] is True


def test_build_audit_bit_high_margin_present_contradicts_known_absent_stratum():
    row = {
        "anchor_id": "a1",
        "stratum": "s3_known_absent_2023",
        "year": 2023,
        "score": 0.99,
        "install_interval_start": "2024-03-01",
        "install_interval_end": "2024-06-01",
    }
    bit = build_audit_bit(row)
    assert bit["bit"] == "present"
    assert bit["expected"] is False
    assert bit["agrees"] is False
    assert bit["contradiction"] is True
    assert bit["routed_to_queue"] is False


def test_build_audit_bit_high_margin_absent_agrees_with_known_absent_stratum():
    row = {
        "anchor_id": "a2",
        "stratum": "s3_known_absent_2023",
        "year": 2023,
        "score": 0.1,
        "install_interval_start": "2024-03-01",
        "install_interval_end": "2024-06-01",
    }
    bit = build_audit_bit(row)
    assert bit["bit"] == "absent"
    assert bit["expected"] is False
    assert bit["agrees"] is True
    assert bit["contradiction"] is False


def test_build_audit_bit_ambiguous_stratum_no_expected_but_can_contradict():
    row = {
        "anchor_id": "a3",
        "stratum": "s4_ambiguous",
        "year": 2023,
        "score": 0.99,
        "install_interval_start": "2024-06-01",
        "install_interval_end": "2024-09-01",
    }
    bit = build_audit_bit(row)
    assert bit["expected"] is None
    assert bit["agrees"] is None  # no known sign to agree/disagree with
    assert bit["contradiction"] is True  # contradiction logic is independent of stratum


# ---------------------------------------------------------------------------
# compute_gates
# ---------------------------------------------------------------------------


def test_gate_a_known_sign_agreement_above_threshold():
    bits = []
    # 20 agreeing high-margin bits, 0 disagreeing -> 100% >= 95%
    for i in range(20):
        bits.append(
            {
                "anchor_id": f"a{i}",
                "stratum": "s3_known_absent_2023",
                "year": 2023,
                "bit": "absent",
                "expected": False,
                "agrees": True,
                "contradiction": False,
                "routed_to_queue": False,
            }
        )
    gates = compute_gates(bits)
    stratum_gate = gates["known_sign_agreement"]["s3_known_absent_2023"][2023]
    assert stratum_gate["n_high_margin"] == 20
    assert stratum_gate["agreement_rate"] == pytest.approx(1.0)
    assert stratum_gate["passes_95pct"] is True


def test_gate_a_below_threshold_fails():
    bits = []
    for i in range(20):
        agree = i < 18  # 18/20 = 90% < 95%
        bits.append(
            {
                "anchor_id": f"a{i}",
                "stratum": "s1_known_present_pre2019",
                "year": 2019,
                "bit": "present" if agree else "absent",
                "expected": True,
                "agrees": agree,
                "contradiction": False,
                "routed_to_queue": False,
            }
        )
    gates = compute_gates(bits)
    stratum_gate = gates["known_sign_agreement"]["s1_known_present_pre2019"][2019]
    assert stratum_gate["agreement_rate"] == pytest.approx(0.9)
    assert stratum_gate["passes_95pct"] is False


def test_gate_a_excludes_low_margin_from_denominator():
    bits = [
        {
            "anchor_id": "a1",
            "stratum": "s1_known_present_pre2019",
            "year": 2019,
            "bit": "present",
            "expected": True,
            "agrees": True,
            "contradiction": False,
            "routed_to_queue": False,
        },
        {
            "anchor_id": "a2",
            "stratum": "s1_known_present_pre2019",
            "year": 2019,
            "bit": "low_margin",
            "expected": True,
            "agrees": None,
            "contradiction": False,
            "routed_to_queue": True,
        },
    ]
    gates = compute_gates(bits)
    stratum_gate = gates["known_sign_agreement"]["s1_known_present_pre2019"][2019]
    assert stratum_gate["n_high_margin"] == 1
    assert stratum_gate["n_low_margin"] == 1
    assert stratum_gate["agreement_rate"] == pytest.approx(1.0)


def test_gate_b_noise_floor_monotonicity_violation_counted():
    # present@2019 then absent@2023, both high-margin, same anchor -> violation
    bits = [
        {
            "anchor_id": "a1",
            "stratum": "s1_known_present_pre2019",
            "year": 2019,
            "bit": "present",
            "expected": True,
            "agrees": True,
            "contradiction": False,
            "routed_to_queue": False,
        },
        {
            "anchor_id": "a1",
            "stratum": "s1_known_present_pre2019",
            "year": 2023,
            "bit": "absent",
            "expected": True,
            "agrees": False,
            "contradiction": True,
            "routed_to_queue": False,
        },
        # a2: present both years -> not a violation
        {
            "anchor_id": "a2",
            "stratum": "s1_known_present_pre2019",
            "year": 2019,
            "bit": "present",
            "expected": True,
            "agrees": True,
            "contradiction": False,
            "routed_to_queue": False,
        },
        {
            "anchor_id": "a2",
            "stratum": "s1_known_present_pre2019",
            "year": 2023,
            "bit": "present",
            "expected": True,
            "agrees": True,
            "contradiction": False,
            "routed_to_queue": False,
        },
    ]
    gates = compute_gates(bits)
    assert gates["within_audit_monotonicity"]["violations"] == 1
    assert gates["within_audit_monotonicity"]["violation_anchor_ids"] == ["a1"]


def test_gate_c_clamp_monotonicity_violation_is_present_2023_contradiction():
    bits = [
        {
            "anchor_id": "a1",
            "stratum": "s3_known_absent_2023",
            "year": 2023,
            "bit": "present",
            "expected": False,
            "agrees": False,
            "contradiction": True,
            "routed_to_queue": False,
        },
        {
            "anchor_id": "a2",
            "stratum": "s3_known_absent_2023",
            "year": 2023,
            "bit": "absent",
            "expected": False,
            "agrees": True,
            "contradiction": False,
            "routed_to_queue": False,
        },
        # a3: contradiction but at year 2019 -> does not count toward gate c
        {
            "anchor_id": "a3",
            "stratum": "s3_known_absent_2023",
            "year": 2019,
            "bit": "present",
            "expected": False,
            "agrees": False,
            "contradiction": True,
            "routed_to_queue": False,
        },
    ]
    gates = compute_gates(bits)
    assert gates["clamp_monotonicity"]["violations"] == 1
    assert gates["clamp_monotonicity"]["violation_anchor_ids"] == ["a1"]


# ---------------------------------------------------------------------------
# sample_pilot — seeded determinism
# ---------------------------------------------------------------------------


def _make_intervals(n, status, start, end, prefix):
    return [_row(status, start, end, anchor_id=f"{prefix}{i:04d}") for i in range(n)]


def test_sample_pilot_deterministic_for_fixed_seed():
    intervals = (
        _make_intervals(50, "done_appears", "2015-01-01", "2016-01-01", "s1_")
        + _make_intervals(50, "done_appears", "2020-01-01", "2021-01-01", "s2_")
        + _make_intervals(5, "done_appears", "2024-01-01", "2024-06-01", "s3_")
    )
    ambiguous = [{"anchor_id": f"amb_{i:04d}"} for i in range(30)]

    s_a = sample_pilot(intervals, ambiguous, seed=20260703, n_s1=30, n_s2=20, n_s4=15)
    s_b = sample_pilot(intervals, ambiguous, seed=20260703, n_s1=30, n_s2=20, n_s4=15)
    assert [r["anchor_id"] for r in s_a] == [r["anchor_id"] for r in s_b]


def test_sample_pilot_different_seed_can_differ():
    intervals = _make_intervals(50, "done_appears", "2015-01-01", "2016-01-01", "s1_")
    ambiguous: list[dict] = []
    s_a = sample_pilot(intervals, ambiguous, seed=1, n_s1=30, n_s2=0, n_s4=0)
    s_b = sample_pilot(intervals, ambiguous, seed=2, n_s1=30, n_s2=0, n_s4=0)
    ids_a = [r["anchor_id"] for r in s_a]
    ids_b = [r["anchor_id"] for r in s_b]
    assert ids_a != ids_b  # extremely unlikely to collide by chance at n=30/50


def test_sample_pilot_stratum3_takes_all_regardless_of_target():
    intervals = _make_intervals(5, "done_appears", "2024-01-01", "2024-06-01", "s3_")
    sample = sample_pilot(intervals, [], seed=1, n_s1=0, n_s2=0, n_s4=0)
    s3_rows = [r for r in sample if r["stratum"] == "s3_known_absent_2023"]
    assert len(s3_rows) == 5


def test_sample_pilot_excludes_forbidden_statuses():
    intervals = _make_intervals(
        10, "done_ambiguous_clamp_inverted", "2015-01-01", "2016-01-01", "bad_"
    )
    sample = sample_pilot(intervals, [], seed=1, n_s1=30, n_s2=20, n_s4=0)
    assert sample == []


def test_sample_pilot_s4_excludes_forbidden_statuses():
    # EXCLUDED_STATUSES anchors must not enter s4 even when they appear in
    # the ambiguous candidate pool (census2023 cohort joined back to
    # install_intervals can surface them at cohort scale).
    ambiguous = [
        {"anchor_id": "amb_ok", "status": "done_appears"},
        {"anchor_id": "amb_clamp", "status": "done_ambiguous_clamp_inverted"},
        {"anchor_id": "amb_marker", "status": "done_ambiguous_marker_missed_pv"},
        {"anchor_id": "amb_nostatus"},
    ]
    sample = sample_pilot([], ambiguous, seed=1, n_s1=0, n_s2=0, n_s4=10)
    ids = sorted(r["anchor_id"] for r in sample)
    assert ids == ["amb_nostatus", "amb_ok"]
    assert all(r["stratum"] == "s4_ambiguous" for r in sample)


def test_gate_c_ignores_excluded_status_bits():
    # A contradiction on a pre-known internally inconsistent interval is not
    # a new audit disagreement and must not count as a clamp violation.
    base = {
        "stratum": "s4_ambiguous",
        "year": 2023,
        "bit": "present",
        "expected": None,
        "agrees": None,
        "contradiction": True,
        "routed_to_queue": False,
    }
    bits = [
        {**base, "anchor_id": "a_ok", "status": "done_appears"},
        {**base, "anchor_id": "a_clamp", "status": "done_ambiguous_clamp_inverted"},
        {**base, "anchor_id": "a_marker", "status": "done_ambiguous_marker_missed_pv"},
    ]
    gates = compute_gates(bits)
    assert gates["clamp_monotonicity"]["violations"] == 1
    assert gates["clamp_monotonicity"]["violation_anchor_ids"] == ["a_ok"]
