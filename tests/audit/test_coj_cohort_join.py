"""Tests for `scripts.audit.coj_cohort_join` + `scripts.audit.coj_cohort_report` (ISSUE-09 WP-D).

Pure-logic + file-in/file-out modules for the cohort-scale CoJ audit: the
per-unit known-sign expectation (design AMENDMENT), the long bit-row builder
(extends the pilot's 3-value bit set with the two coverage states), the wide
per-anchor pivot, the four cohort gates, the per-stratum contradiction-rate
table, coverage accounting, the Wilson score interval, and the report
renderer. No network, no GPU, no model loads — plain dicts / lists / tmp CSVs
in the style of ``tests/audit/test_coj_audit_join.py``.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.audit.coj_cohort_join import (
    COHORT_STRATA,
    LAYER_YEARS,
    NEGATIVE_CONTROL_STRATUM,
    build_cohort_bit,
    compute_cohort_gates,
    contradiction_rate_by_stratum,
    coverage_report,
    expected_bit,
    pivot_anchors,
    wilson_ci,
)
from scripts.audit.coj_cohort_report import build_cohort_report, render_cohort_report_md

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_MD = REPO_ROOT / "docs" / "replan_v2" / "ISSUE-09-cohort-schema.md"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _unit(**kw) -> dict:
    """A long planned-unit input row (design 4a columns in)."""
    base = {
        "anchor_id": "a1",
        "stratum": "c_probe_2023",
        "year": 2023,
        "unit_purpose": "headline_2023",
        "status": "done_appears",
        "confidence": "high",
        "install_interval_start": "2022-01-01",
        "install_interval_end": "2024-06-01",
        "chip_path": "chips/2023/a1.tif",
        "fetch_outcome": "ok",
        "detector_S": 0.99,
        "classifier_pv_prob": 0.9,
    }
    base.update(kw)
    return base


def _bit(**kw) -> dict:
    """A built §4a bit-row (post build_cohort_bit) for gate/rate tests."""
    base = {
        "anchor_id": "a1",
        "stratum": "c_cal_present_pre2019",
        "year": 2023,
        "unit_purpose": "calibration_present",
        "status": "done_appears",
        "install_interval_start": "2016-01-01",
        "install_interval_end": "2018-06-01",
        "bit": "present",
        "expected": "present",
        "agrees": True,
        "contradicts_interval": False,
        "fetch_outcome": "ok",
        "detector_S": 0.99,
        "classifier_pv_prob": 0.9,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# expected_bit — per-unit known-sign expectation (AMENDMENT)
# ---------------------------------------------------------------------------


def test_expected_bit_dated_present_when_end_strictly_before_jan1_of_year():
    row = _unit(stratum="c_cal_present_pre2019", install_interval_end="2018-06-01")
    assert expected_bit(row, 2019) == "present"
    assert expected_bit(row, 2023) == "present"


def test_expected_bit_dated_none_when_end_on_jan1_boundary():
    # strictly-before rule: end == Jan-1-Y is NOT before Jan-1-Y.
    row = _unit(install_interval_end="2019-01-01")
    assert expected_bit(row, 2019) is None


def test_expected_bit_dated_none_when_end_after_year():
    row = _unit(install_interval_end="2022-06-01")
    assert expected_bit(row, 2019) is None


def test_expected_bit_dated_none_when_end_unparseable():
    assert expected_bit(_unit(install_interval_end=""), 2019) is None
    assert expected_bit(_unit(install_interval_end="nan"), 2019) is None


def test_expected_bit_negative_control_always_absent():
    row = _unit(stratum=NEGATIVE_CONTROL_STRATUM, anchor_id="nc_0001",
                status="", install_interval_end="")
    assert expected_bit(row, 2019) == "absent"
    assert expected_bit(row, 2023) == "absent"


def test_expected_bit_2015_consequence_of_amendment():
    # end < 2015-01-01 -> present@2015 expectation (the ~97 anchors)
    early = _unit(stratum="c_cal_present_pre2019", install_interval_end="2014-12-31")
    assert expected_bit(early, 2015) == "present"
    # end in [2015-01-01, 2019-01-01): NO expectation at 2015, but present at 2019
    mid = _unit(stratum="c_cal_present_pre2019", install_interval_end="2016-06-01")
    assert expected_bit(mid, 2015) is None
    assert expected_bit(mid, 2019) == "present"


# ---------------------------------------------------------------------------
# build_cohort_bit — margin rule + coverage states + §4a schema
# ---------------------------------------------------------------------------

LONG_COLUMNS = (
    "anchor_id", "stratum", "year", "unit_purpose", "status", "confidence",
    "install_interval_start", "install_interval_end", "chip_path", "fetch_outcome",
    "detector_S", "classifier_pv_prob", "bit", "expected", "agrees",
    "contradicts_interval", "routed_to_queue",
)


def test_build_cohort_bit_emits_full_4a_schema():
    out = build_cohort_bit(_unit())
    assert set(out.keys()) == set(LONG_COLUMNS)


def test_build_cohort_bit_high_margin_present():
    out = build_cohort_bit(_unit(detector_S=0.99, classifier_pv_prob=0.9))
    assert out["bit"] == "present"
    assert out["routed_to_queue"] is False


def test_build_cohort_bit_high_margin_absent():
    out = build_cohort_bit(_unit(detector_S=0.1))
    assert out["bit"] == "absent"
    assert out["routed_to_queue"] is False


def test_build_cohort_bit_low_margin_routed():
    out = build_cohort_bit(_unit(detector_S=0.5))
    assert out["bit"] == "low_margin"
    assert out["routed_to_queue"] is True
    assert out["contradicts_interval"] is False


def test_build_cohort_bit_classifier_demotes_present():
    out = build_cohort_bit(_unit(detector_S=0.99, classifier_pv_prob=0.1))
    assert out["bit"] == "low_margin"
    assert out["routed_to_queue"] is True


def test_build_cohort_bit_empty_fetch_is_no_coverage():
    out = build_cohort_bit(_unit(fetch_outcome="empty", detector_S=""))
    assert out["bit"] == "no_coverage"
    assert out["routed_to_queue"] is False
    assert out["contradicts_interval"] is False
    assert out["agrees"] == ""


@pytest.mark.parametrize("fo", ["http_error", "waf_challenge", "exception", "not_fetched", ""])
def test_build_cohort_bit_failed_fetch_is_fetch_failed(fo):
    out = build_cohort_bit(_unit(fetch_outcome=fo, detector_S=""))
    assert out["bit"] == "fetch_failed"
    assert out["routed_to_queue"] is False
    assert out["contradicts_interval"] is False


def test_build_cohort_bit_ok_but_unscorable_is_fetch_failed():
    # fetched ok but no scorable chip (detector_S blank) -> no usable signal
    out = build_cohort_bit(_unit(fetch_outcome="ok", detector_S=""))
    assert out["bit"] == "fetch_failed"


def test_build_cohort_bit_skipped_existing_is_scored():
    out = build_cohort_bit(_unit(fetch_outcome="skipped_existing", detector_S=0.99))
    assert out["bit"] == "present"


def test_build_cohort_bit_contradiction_all_three_years_present_side():
    # present@Y contradicts iff install_interval_start > Dec-31 of Y
    for year in (2015, 2019, 2023):
        start_after = f"{year + 1}-03-01"
        out = build_cohort_bit(
            _unit(year=year, detector_S=0.99, install_interval_start=start_after)
        )
        assert out["bit"] == "present"
        assert out["contradicts_interval"] is True


def test_build_cohort_bit_contradiction_all_three_years_absent_side():
    # absent@Y contradicts iff install_interval_end < Jan-1 of Y
    for year in (2015, 2019, 2023):
        end_before = f"{year - 1}-06-01"
        out = build_cohort_bit(
            _unit(year=year, detector_S=0.1, install_interval_end=end_before)
        )
        assert out["bit"] == "absent"
        assert out["contradicts_interval"] is True


def test_build_cohort_bit_present_within_year_no_contradiction():
    out = build_cohort_bit(_unit(year=2023, detector_S=0.99, install_interval_start="2023-06-01"))
    assert out["bit"] == "present"
    assert out["contradicts_interval"] is False


def test_build_cohort_bit_expected_and_agrees_present_side():
    # dated calibration unit: end < Jan-1-Y -> expect present
    row = _unit(stratum="c_cal_present_pre2019", year=2019,
                install_interval_end="2018-06-01", install_interval_start="2016-01-01",
                detector_S=0.99)
    out = build_cohort_bit(row)
    assert out["expected"] == "present"
    assert out["agrees"] is True
    # absent bit on the same unit disagrees
    out2 = build_cohort_bit({**row, "detector_S": 0.1})
    assert out2["bit"] == "absent"
    assert out2["agrees"] is False


def test_build_cohort_bit_nc_false_present_disagrees():
    row = _unit(stratum=NEGATIVE_CONTROL_STRATUM, anchor_id="nc_0001", year=2023,
                status="", install_interval_start="", install_interval_end="",
                detector_S=0.99, classifier_pv_prob=0.9)
    out = build_cohort_bit(row)
    assert out["expected"] == "absent"
    assert out["bit"] == "present"
    assert out["agrees"] is False  # a false-present
    out_abs = build_cohort_bit({**row, "detector_S": 0.1})
    assert out_abs["agrees"] is True


def test_build_cohort_bit_no_expectation_leaves_agrees_blank():
    row = _unit(stratum="c_probe_2023", install_interval_end="2024-06-01", detector_S=0.99)
    out = build_cohort_bit(row)
    assert out["expected"] == ""
    assert out["agrees"] == ""


# ---------------------------------------------------------------------------
# pivot_anchors — wide §4b + rollups + not_planned + schema conformance
# ---------------------------------------------------------------------------

WIDE_COLUMNS = (
    "anchor_id", "stratum", "status", "confidence",
    "install_interval_start", "install_interval_end",
    "latest_absent_date", "earliest_present_date", "grid_id", "is_negative_control",
    "bit_2015", "detector_S_2015", "classifier_pv_prob_2015", "contradicts_2015",
    "bit_2019", "detector_S_2019", "classifier_pv_prob_2019", "contradicts_2019",
    "bit_2023", "detector_S_2023", "classifier_pv_prob_2023", "contradicts_2023",
    "any_contradiction", "n_contradictions", "n_high_margin_bits", "first_present_year",
)


def _anchor_meta(anchor_id="a1", stratum="c_cal_present_pre2019", **kw) -> dict:
    base = {
        "anchor_id": anchor_id,
        "stratum": stratum,
        "status": "done_appears",
        "confidence": "high",
        "install_interval_start": "2016-01-01",
        "install_interval_end": "2018-06-01",
        "latest_absent_date": "2016-01-01",
        "earliest_present_date": "2018-06-01",
        "grid_id": "JNB0101",
        "is_negative_control": False,
    }
    base.update(kw)
    return base


def test_pivot_anchors_schema_conformance():
    bits = [build_cohort_bit(_unit(anchor_id="a1", year=2023, detector_S=0.99))]
    wide = pivot_anchors(bits, [_anchor_meta("a1")])
    assert len(wide) == 1
    assert set(wide[0].keys()) == set(WIDE_COLUMNS)


def test_pivot_anchors_not_planned_for_missing_years():
    # only a 2023 bit planned -> 2015 & 2019 are not_planned
    bits = [build_cohort_bit(_unit(anchor_id="a1", year=2023, detector_S=0.99))]
    wide = pivot_anchors(bits, [_anchor_meta("a1")])[0]
    assert wide["bit_2015"] == "not_planned"
    assert wide["bit_2019"] == "not_planned"
    assert wide["bit_2023"] == "present"
    assert wide["detector_S_2015"] == ""
    assert wide["contradicts_2019"] == ""


def test_pivot_anchors_rollups():
    bits = [
        build_cohort_bit(_unit(anchor_id="a1", year=2015, detector_S=0.99,
                               install_interval_start="2016-06-01")),  # present, contradicts (start>2015-12-31)
        build_cohort_bit(_unit(anchor_id="a1", year=2019, detector_S=0.1,
                               install_interval_end="2018-06-01")),    # absent, contradicts (end<2019-01-01)
        build_cohort_bit(_unit(anchor_id="a1", year=2023, detector_S=0.5)),  # low_margin
    ]
    wide = pivot_anchors(bits, [_anchor_meta("a1")])[0]
    assert wide["any_contradiction"] is True
    assert wide["n_contradictions"] == 2
    assert wide["n_high_margin_bits"] == 2  # present@2015 + absent@2019
    assert wide["first_present_year"] == 2015


def test_pivot_anchors_first_present_year_blank_when_none():
    bits = [build_cohort_bit(_unit(anchor_id="a1", year=2023, detector_S=0.1))]  # absent only
    wide = pivot_anchors(bits, [_anchor_meta("a1")])[0]
    assert wide["first_present_year"] == ""


def test_pivot_anchors_is_negative_control_derived_from_stratum():
    bits = [build_cohort_bit(_unit(anchor_id="nc_1", stratum=NEGATIVE_CONTROL_STRATUM,
                                   year=2023, status="", install_interval_start="",
                                   install_interval_end="", detector_S=0.1))]
    meta = _anchor_meta("nc_1", stratum=NEGATIVE_CONTROL_STRATUM)
    del meta["is_negative_control"]  # force derivation
    wide = pivot_anchors(bits, [meta])[0]
    assert wide["is_negative_control"] is True


def test_pivot_anchors_metadata_passthrough():
    bits = [build_cohort_bit(_unit(anchor_id="a1", year=2023, detector_S=0.99))]
    meta = _anchor_meta("a1", grid_id="JNB0299", latest_absent_date="2016-01-01",
                        earliest_present_date="2018-06-01")
    wide = pivot_anchors(bits, [meta])[0]
    assert wide["grid_id"] == "JNB0299"
    assert wide["latest_absent_date"] == "2016-01-01"
    assert wide["earliest_present_date"] == "2018-06-01"


# ---------------------------------------------------------------------------
# compute_cohort_gates
# ---------------------------------------------------------------------------


def test_gate_a_passes_at_100pct_across_years():
    bits = []
    for year in (2015, 2019, 2023):
        for i in range(10):
            bits.append(_bit(anchor_id=f"a{year}_{i}", year=year, bit="present",
                             expected="present", agrees=True))
    gates = compute_cohort_gates(bits, nc_anchor_ids=set())
    cells = gates["gate_a"]["cells"]
    assert len(cells) == 3  # one per year
    assert all(c["rate"] == pytest.approx(1.0) and c["passes"] for c in cells)
    assert gates["gate_a"]["passes"] is True


def test_gate_a_fails_below_95pct():
    bits = []
    for i in range(20):
        agree = i < 18  # 90%
        bits.append(_bit(anchor_id=f"a{i}", year=2023, bit="present" if agree else "absent",
                         expected="present", agrees=agree))
    gates = compute_cohort_gates(bits, nc_anchor_ids=set())
    cell = [c for c in gates["gate_a"]["cells"] if c["year"] == 2023][0]
    assert cell["rate"] == pytest.approx(0.9)
    assert cell["passes"] is False
    assert gates["gate_a"]["passes"] is False


def test_gate_a_excludes_low_margin_no_expectation_and_nc():
    bits = [
        _bit(anchor_id="a1", bit="present", expected="present", agrees=True),
        _bit(anchor_id="a2", bit="low_margin", expected="present", agrees=""),
        _bit(anchor_id="a3", bit="present", expected="", agrees=""),
        _bit(anchor_id="nc_1", stratum=NEGATIVE_CONTROL_STRATUM, bit="present",
             expected="absent", agrees=False),
    ]
    gates = compute_cohort_gates(bits, nc_anchor_ids={"nc_1"})
    cells = gates["gate_a"]["cells"]
    assert len(cells) == 1
    assert cells[0]["stratum"] == "c_cal_present_pre2019"
    assert cells[0]["n"] == 1
    assert cells[0]["agree"] == 1


def test_gate_nc_counts_false_presents_and_passes_at_threshold():
    nc_ids = {"nc_1", "nc_2", "nc_3", "nc_4", "nc_5"}
    bits = [
        _bit(anchor_id="nc_1", stratum=NEGATIVE_CONTROL_STRATUM, year=2023,
             bit="present", expected="absent", agrees=False),
        _bit(anchor_id="nc_2", stratum=NEGATIVE_CONTROL_STRATUM, year=2019,
             bit="present", expected="absent", agrees=False),
        _bit(anchor_id="nc_3", stratum=NEGATIVE_CONTROL_STRATUM, year=2023,
             bit="absent", expected="absent", agrees=True),
        # nc_4/nc_5 must also produce a scorable (absent) bit — an unscored
        # control is NOT silently clean (see the vacuous-pass tests below).
        _bit(anchor_id="nc_4", stratum=NEGATIVE_CONTROL_STRATUM, year=2023,
             bit="absent", expected="absent", agrees=True),
        _bit(anchor_id="nc_5", stratum=NEGATIVE_CONTROL_STRATUM, year=2023,
             bit="absent", expected="absent", agrees=True),
    ]
    gates = compute_cohort_gates(bits, nc_anchor_ids=nc_ids)
    nc = gates["gate_nc"]
    assert nc["n_controls"] == 5
    assert nc["n_scored_controls"] == 5
    assert nc["n_false_present"] == 2
    assert sorted(nc["false_present_anchor_ids"]) == ["nc_1", "nc_2"]
    assert nc["rate"] == pytest.approx(0.4)
    assert nc["evaluable"] is True
    assert nc["passes"] is True  # scored + 2 <= 2
    lo, hi = nc["wilson_ci"]
    assert 0.0 <= lo <= nc["rate"] <= hi <= 1.0


def test_gate_nc_vacuous_pass_rejected_when_no_controls():
    # No controls declared at all: the calibration gate cannot certify the
    # absent-side thresholds because the instrument was never exercised on any
    # control, so it must NOT report PASS (regression for the vacuous-pass bug).
    gates = compute_cohort_gates([], nc_anchor_ids=set())
    nc = gates["gate_nc"]
    assert nc["n_controls"] == 0
    assert nc["n_scored_controls"] == 0
    assert nc["evaluable"] is False
    assert nc["passes"] is False


def test_gate_nc_vacuous_pass_rejected_when_no_control_scored():
    # 300 declared controls whose 2019/2023 fetches all degraded to
    # fetch_failed / no_coverage -> zero produced a scorable presence bit.
    # k_fp is trivially 0, but the instrument never ran on a single control, so
    # the gate must NOT pass on that vacuous basis.
    nc_ids = {f"nc_{i:04d}" for i in range(300)}
    bits = []
    for i, aid in enumerate(sorted(nc_ids)):
        state = "fetch_failed" if i % 2 == 0 else "no_coverage"
        bits.append(_bit(anchor_id=aid, stratum=NEGATIVE_CONTROL_STRATUM, year=2019,
                         bit=state, expected="absent", agrees=""))
        bits.append(_bit(anchor_id=aid, stratum=NEGATIVE_CONTROL_STRATUM, year=2023,
                         bit=state, expected="absent", agrees=""))
    gates = compute_cohort_gates(bits, nc_anchor_ids=nc_ids)
    nc = gates["gate_nc"]
    assert nc["n_controls"] == 300
    assert nc["n_false_present"] == 0
    assert nc["n_scored_controls"] == 0
    assert nc["evaluable"] is False
    assert nc["passes"] is False


def test_gate_nc_requires_scored_fraction():
    # Only half the controls produced a scorable bit -> below the scored floor
    # -> not evaluable -> must NOT pass even with zero false-presents.
    nc_ids = {f"nc_{i:04d}" for i in range(10)}
    bits = []
    for i, aid in enumerate(sorted(nc_ids)):
        state = "absent" if i < 5 else "fetch_failed"
        bits.append(_bit(anchor_id=aid, stratum=NEGATIVE_CONTROL_STRATUM, year=2023,
                         bit=state, expected="absent"))
    gates = compute_cohort_gates(bits, nc_anchor_ids=nc_ids)
    nc = gates["gate_nc"]
    assert nc["n_scored_controls"] == 5
    assert nc["evaluable"] is False
    assert nc["passes"] is False


def test_gate_nc_passes_when_fully_scored_and_below_threshold():
    # Every control produced a scorable (absent) bit and there are no
    # false-presents -> evaluable + PASS.
    nc_ids = {f"nc_{i:04d}" for i in range(10)}
    bits = [
        _bit(anchor_id=aid, stratum=NEGATIVE_CONTROL_STRATUM, year=2023,
             bit="absent", expected="absent")
        for aid in sorted(nc_ids)
    ]
    gates = compute_cohort_gates(bits, nc_anchor_ids=nc_ids)
    nc = gates["gate_nc"]
    assert nc["n_scored_controls"] == 10
    assert nc["evaluable"] is True
    assert nc["passes"] is True


def test_gate_nc_fails_above_two_false_presents():
    nc_ids = {"nc_1", "nc_2", "nc_3"}
    bits = [
        _bit(anchor_id=a, stratum=NEGATIVE_CONTROL_STRATUM, year=2023, bit="present",
             expected="absent", agrees=False)
        for a in ("nc_1", "nc_2", "nc_3")
    ]
    gates = compute_cohort_gates(bits, nc_anchor_ids=nc_ids)
    assert gates["gate_nc"]["n_false_present"] == 3
    assert gates["gate_nc"]["passes"] is False


def test_gate_b_flags_present_earlier_absent_later_any_pair():
    # 2015-present ∧ 2023-absent
    bits = [
        _bit(anchor_id="a1", year=2015, bit="present"),
        _bit(anchor_id="a1", year=2023, bit="absent"),
        # a2: 2019-present ∧ 2023-absent
        _bit(anchor_id="a2", year=2019, bit="present"),
        _bit(anchor_id="a2", year=2023, bit="absent"),
        # a3: present both -> clean
        _bit(anchor_id="a3", year=2015, bit="present"),
        _bit(anchor_id="a3", year=2023, bit="present"),
    ]
    gates = compute_cohort_gates(bits, nc_anchor_ids=set())
    assert gates["gate_b"]["violations"] == 2
    assert gates["gate_b"]["violation_anchor_ids"] == ["a1", "a2"]


def test_clamp_findings_present_2023_contradiction_excluding_excluded_statuses():
    bits = [
        _bit(anchor_id="a_ok", year=2023, bit="present", contradicts_interval=True,
             status="done_appears"),
        _bit(anchor_id="a_clamp", year=2023, bit="present", contradicts_interval=True,
             status="done_ambiguous_clamp_inverted"),
        _bit(anchor_id="a_2019", year=2019, bit="present", contradicts_interval=True,
             status="done_appears"),  # wrong year
        _bit(anchor_id="a_abs", year=2023, bit="absent", contradicts_interval=False,
             status="done_appears"),
    ]
    gates = compute_cohort_gates(bits, nc_anchor_ids=set())
    assert gates["clamp_findings"]["count"] == 1
    assert gates["clamp_findings"]["anchor_ids"] == ["a_ok"]


# ---------------------------------------------------------------------------
# contradiction_rate_by_stratum
# ---------------------------------------------------------------------------


def test_contradiction_rate_by_stratum_anchor_and_bit_levels():
    bits = [
        # stratum c_findings_s3like: a1 contradicts@2023, a2 clean, a3 low-margin only
        _bit(anchor_id="a1", stratum="c_findings_s3like", year=2023, bit="present",
             contradicts_interval=True),
        _bit(anchor_id="a2", stratum="c_findings_s3like", year=2023, bit="absent",
             contradicts_interval=False),
        _bit(anchor_id="a3", stratum="c_findings_s3like", year=2023, bit="low_margin",
             contradicts_interval=False),
    ]
    rows = contradiction_rate_by_stratum(bits)
    stratum_rows = [r for r in rows if r["level"] == "stratum"
                    and r["stratum"] == "c_findings_s3like"]
    assert len(stratum_rows) == 1
    sr = stratum_rows[0]
    assert sr["n_denominator"] == 2  # a1, a2 have high-margin bits (a3 low only)
    assert sr["n_numerator"] == 1    # a1 contradicts
    assert sr["rate"] == pytest.approx(0.5)
    sy = [r for r in rows if r["level"] == "stratum_year"
          and r["stratum"] == "c_findings_s3like" and r["year"] == 2023][0]
    assert sy["n_denominator"] == 2  # 2 high-margin bits
    assert sy["n_numerator"] == 1


def test_contradiction_rate_by_stratum_in_fals_2019_flag_subrow():
    bits = [
        _bit(anchor_id="a1", stratum="c_probe_2023", year=2019, bit="present",
             unit_purpose="falsification", contradicts_interval=True),
        _bit(anchor_id="a2", stratum="c_probe_2023", year=2019, bit="absent",
             unit_purpose="falsification", contradicts_interval=False),
    ]
    rows = contradiction_rate_by_stratum(bits)
    flag_rows = [r for r in rows if r["level"] == "flag" and r["flag"] == "in_fals_2019"]
    assert len(flag_rows) == 1
    fr = flag_rows[0]
    assert fr["n_denominator"] == 2
    assert fr["n_numerator"] == 1
    assert fr["rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# coverage_report
# ---------------------------------------------------------------------------


def test_coverage_report_covered_and_failed_enumeration():
    planned = [
        {"anchor_id": "A", "year": 2019, "stratum": "c_cal_present_pre2019"},
        {"anchor_id": "A", "year": 2023, "stratum": "c_cal_present_pre2019"},
        {"anchor_id": "B", "year": 2019, "stratum": "c_probe_2023"},
        {"anchor_id": "B", "year": 2023, "stratum": "c_probe_2023"},
        {"anchor_id": "nc_1", "year": 2023, "stratum": NEGATIVE_CONTROL_STRATUM},
    ]
    bits = [
        {"anchor_id": "A", "year": 2019, "fetch_outcome": "ok"},
        {"anchor_id": "A", "year": 2023, "fetch_outcome": "empty"},   # no_coverage still counts covered
        {"anchor_id": "B", "year": 2019, "fetch_outcome": "ok"},
        {"anchor_id": "B", "year": 2023, "fetch_outcome": "http_error"},  # failure
        {"anchor_id": "nc_1", "year": 2023, "fetch_outcome": "ok"},
    ]
    cov = coverage_report(planned, bits)
    assert cov["dated_anchors"] == 2  # A, B (nc excluded)
    assert cov["covered_anchors"] == 1  # only A
    assert cov["coverage"] == pytest.approx(0.5)
    assert cov["passes_95pct"] is False
    failed = {(f["anchor_id"], f["year"], f["outcome"]) for f in cov["failed_units"]}
    assert ("B", 2023, "http_error") in failed


def test_coverage_report_missing_bit_is_a_gap():
    planned = [{"anchor_id": "A", "year": 2019, "stratum": "c_cal_present_pre2019"}]
    cov = coverage_report(planned, [])  # no bit row at all
    assert cov["covered_anchors"] == 0
    assert cov["n_failed_units"] == 1
    assert cov["failed_units"][0]["outcome"] == "not_fetched"


def test_coverage_report_all_covered_passes():
    planned = [{"anchor_id": f"A{i}", "year": 2023, "stratum": "c_probe_2023"} for i in range(20)]
    bits = [{"anchor_id": f"A{i}", "year": 2023, "fetch_outcome": "ok"} for i in range(20)]
    cov = coverage_report(planned, bits)
    assert cov["coverage"] == pytest.approx(1.0)
    assert cov["passes_95pct"] is True


def test_coverage_report_ok_but_unscorable_bit_is_a_gap():
    # An `ok` fetch that yielded no scorable chip -> build_cohort_bit assigns
    # bit='fetch_failed'. Coverage must key on that computed presence bit (the
    # schema doc says fetch_failed "Counts as a coverage gap"), NOT on the
    # fetch_outcome='ok', so the unit is a gap and gets enumerated instead of
    # silently counted covered.
    planned = [{"anchor_id": "A", "year": 2015, "stratum": "c_cal_present_pre2019"}]
    bits = [{"anchor_id": "A", "year": 2015, "fetch_outcome": "ok", "bit": "fetch_failed"}]
    cov = coverage_report(planned, bits)
    assert cov["covered_anchors"] == 0
    assert cov["coverage"] == pytest.approx(0.0)
    assert cov["passes_95pct"] is False
    assert cov["n_failed_units"] == 1
    f = cov["failed_units"][0]
    assert (f["anchor_id"], f["year"]) == ("A", 2015)
    assert f["outcome"] != "ok"  # not passed off as a clean fetch


def test_coverage_report_no_coverage_bit_still_covered():
    # bit='no_coverage' (empty imagery) is a real terminal answer -> covered.
    planned = [{"anchor_id": "A", "year": 2023, "stratum": "c_cal_present_pre2019"}]
    bits = [{"anchor_id": "A", "year": 2023, "fetch_outcome": "empty", "bit": "no_coverage"}]
    cov = coverage_report(planned, bits)
    assert cov["covered_anchors"] == 1
    assert cov["n_failed_units"] == 0


# ---------------------------------------------------------------------------
# wilson_ci — known values (precise, asserted to 1e-3)
# ---------------------------------------------------------------------------


def test_wilson_ci_zero_of_300():
    lo, hi = wilson_ci(0, 300)
    assert lo == pytest.approx(0.0, abs=1e-3)
    assert hi == pytest.approx(0.012643, abs=1e-3)


def test_wilson_ci_7_of_57():
    lo, hi = wilson_ci(7, 57)
    assert lo == pytest.approx(0.06078, abs=1e-3)
    assert hi == pytest.approx(0.23247, abs=1e-3)


def test_wilson_ci_zero_n_is_degenerate():
    assert wilson_ci(0, 0) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# schema doc conformance — every documented column is produced
# ---------------------------------------------------------------------------


def _md_table_columns(marker: str) -> list[str]:
    text = SCHEMA_MD.read_text()
    start = text.index(f"<!-- {marker}_START -->")
    end = text.index(f"<!-- {marker}_END -->")
    block = text[start:end].splitlines()
    cols = []
    for line in block:
        line = line.strip()
        if not line.startswith("|"):
            continue
        first = line.split("|")[1].strip()
        first = first.strip("`")
        if not first or first.lower() == "column" or set(first) <= set("-: "):
            continue
        cols.append(first)
    return cols


def test_schema_doc_documents_all_wide_columns():
    documented = _md_table_columns("WIDE_TABLE_COLUMNS")
    bits = [build_cohort_bit(_unit(anchor_id="a1", year=2023, detector_S=0.99))]
    produced = set(pivot_anchors(bits, [_anchor_meta("a1")])[0].keys())
    assert set(documented) == produced


def test_schema_doc_documents_all_long_columns():
    documented = _md_table_columns("LONG_TABLE_COLUMNS")
    produced = set(build_cohort_bit(_unit()).keys())
    assert set(documented) == produced


def test_cohort_strata_constant_matches_doc_partition():
    assert NEGATIVE_CONTROL_STRATUM in COHORT_STRATA
    assert LAYER_YEARS == (2015, 2019, 2023)


def test_example_csv_header_matches_documented_wide_columns():
    example = REPO_ROOT / "data" / "examples" / "coj_cohort_audit.example.csv"
    with open(example) as f:
        header = f.readline().strip().split(",")
    documented = _md_table_columns("WIDE_TABLE_COLUMNS")
    assert header == documented  # order-exact, faithful sample of the wide table


def test_example_csv_exercises_every_bit_state():
    example = REPO_ROOT / "data" / "examples" / "coj_cohort_audit.example.csv"
    seen = set()
    with open(example) as f:
        for row in csv.DictReader(f):
            for year in (2015, 2019, 2023):
                seen.add(row[f"bit_{year}"])
    assert {"present", "absent", "low_margin", "no_coverage",
            "fetch_failed", "not_planned"} <= seen


# ---------------------------------------------------------------------------
# coj_cohort_report — file-in/file-out smoke
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = sorted({k for r in rows for k in r.keys()})
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def test_build_cohort_report_renders_headline_table_and_csv(tmp_path):
    root = tmp_path / "cohort"
    root.mkdir()
    bits = [
        build_cohort_bit(_unit(anchor_id="a1", stratum="c_findings_s3like", year=2023,
                               detector_S=0.99, install_interval_start="2024-06-01")),
        build_cohort_bit(_unit(anchor_id="a2", stratum="c_findings_s3like", year=2023,
                               detector_S=0.1, install_interval_end="2024-06-01")),
    ]
    _write_csv(root / "cohort_audit_bits.csv", bits)

    gates = compute_cohort_gates(bits, nc_anchor_ids=set())
    cov = coverage_report(
        [{"anchor_id": "a1", "year": 2023, "stratum": "c_findings_s3like"},
         {"anchor_id": "a2", "year": 2023, "stratum": "c_findings_s3like"}],
        bits,
    )
    (root / "gates_report.json").write_text(
        json.dumps({"gates": gates, "coverage": cov}, default=str)
    )
    (root / "dropped_units_summary.json").write_text(
        json.dumps({"unfalsifiable_2019_within_year": 851})
    )

    result = build_cohort_report(output_root=root)

    md = (root / "cohort_report.md").read_text()
    assert "Contradiction rate by stratum" in md
    assert "c_findings_s3like" in md
    assert "851" in md  # dropped-units passthrough
    assert (root / "contradiction_rate_by_stratum.csv").exists()
    with open(root / "contradiction_rate_by_stratum.csv") as f:
        header = f.readline()
    assert "rate" in header and "ci_low" in header
    assert result["cohort_report_md"] == root / "cohort_report.md"


def test_render_cohort_report_md_flattens_nested_dropped_values():
    # dropped_units_summary.json carries three nested-container values
    # (anchors_missing_bbox={count,anchor_ids}, strata_counts, layer_year_unit_counts)
    # alongside scalar counts. The roll-up table must not render them as raw
    # Python dict/list reprs in the count column.
    dropped = {
        "dropped_2015_end_ge_2019": 12,
        "anchors_missing_bbox": {"count": 3, "anchor_ids": ["JNB0001", "JNB0002", "JNB0003"]},
        "strata_counts": {"c_probe_2023": 12, "c_findings_s3like": 8},
        "layer_year_unit_counts": {"2015": 40, "2019": 60, "2023": 90},
    }
    md = render_cohort_report_md(
        population={}, coverage={}, gates={},
        contradiction_rows=[], dropped_summary=dropped,
    )
    # scalar rows still render cleanly
    assert "| dropped_2015_end_ge_2019 | 12 |" in md
    # NO raw Python dict / list reprs leak into any table cell
    assert "{'" not in md
    assert "['" not in md
    assert "anchor_ids': " not in md
    # nested breakdowns are flattened into readable sub-rows
    assert "strata_counts.c_probe_2023" in md
    assert "anchors_missing_bbox.count" in md
    assert ("layer_year_unit_counts.2015" in md
            or "layer_year_unit_counts.2019" in md
            or "layer_year_unit_counts.2023" in md)


# ---------------------------------------------------------------------------
# schema-doc / reality consistency
# ---------------------------------------------------------------------------


def test_schema_doc_probe2023_row_documents_2019_bisector():
    # The c_probe_2023 stratum row must acknowledge that 2019 is also fetched as
    # a bisector (coj_cohort_build._plan_2019 fires the bisector branch
    # stratum-agnostically), not only for the falsification subsample.
    for line in SCHEMA_MD.read_text().splitlines():
        if line.strip().startswith("| `c_probe_2023`"):
            assert "bisector" in line
            break
    else:
        raise AssertionError("c_probe_2023 stratum row not found in schema doc")
