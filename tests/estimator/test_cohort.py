"""UNIT B tests — cohort builder (eval/cohort.py).

Maps every anchor (scan-state JSON or banked panel row) to a
``CensoringInterval``, mirroring ``infer_install_dates.infer_one``'s bound rules
and status reclassification for non-ambiguous statuses, and applying the
maximal-usable-absent-prefix recovery rule to every ``done_ambiguous_*`` status.

Inline synthetic factories (no conftest). Banked-data tests are skipped when the
real endtoend rep is absent so CI stays green without data.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from solar_backdating.estimators import VintageObservation
from solar_backdating.estimators.survival import CensoringInterval
from solar_backdating.eval import cohort

CENSUS_MID = date(2024, 6, 30)


def _obs(day: str, pv: str, *, quality: str = "usable", row: int = 0) -> VintageObservation:
    """Build a usable-by-default VintageObservation. ``pv`` is canonical '1'/'0'/''."""
    return VintageObservation(
        capture_date=date.fromisoformat(day),
        pv_present=pv,
        confidence=0.9,
        quality_flag=quality,
        source_row=row,
    )


def _iv(observations, status, *, ceiling=None, grid_id="JNB0001", anchor_id="a"):
    return cohort.interval_from_observations(
        observations,
        status,
        ceiling=ceiling,
        census_mid=CENSUS_MID,
        anchor_id=anchor_id,
        grid_id=grid_id,
    )


# --------------------------------------------------------------------------- #
# 1. done_appears 0->1
# --------------------------------------------------------------------------- #
def test_done_appears_basic_interval():
    obs = [_obs("2023-01-01", "0", row=0), _obs("2023-06-01", "1", row=1)]
    iv = _iv(obs, "done_appears")
    assert iv.kind == "interval"
    assert iv.lower == date(2023, 1, 1)
    assert iv.upper == date(2023, 6, 1)
    assert iv.recovered is False
    assert iv.status == "done_appears"
    assert iv.n_usable == 2


# --------------------------------------------------------------------------- #
# 2. done_appears present clamp
# --------------------------------------------------------------------------- #
def test_done_appears_present_clamped_to_ceiling():
    obs = [_obs("2023-01-01", "0"), _obs("2024-07-01", "1")]
    iv = _iv(obs, "done_appears", ceiling=date(2024, 4, 19))
    assert iv.kind == "interval"
    assert iv.lower == date(2023, 1, 1)
    assert iv.upper == date(2024, 4, 19)  # clamped


# --------------------------------------------------------------------------- #
# 3. done_appears clamp inverts -> recovery, prefix rule, never inverted
# --------------------------------------------------------------------------- #
def test_done_appears_clamp_inverted_all_post_flight_left():
    # Both absent and present post-date the flight date -> clamp inverts.
    obs = [_obs("2024-06-01", "0"), _obs("2024-07-01", "1")]
    iv = _iv(obs, "done_appears", ceiling=date(2024, 4, 19))
    assert iv.status == "done_ambiguous_clamp_inverted"
    assert iv.recovered is True
    # post-flight absent dropped by the prefix rule -> no lower bound
    assert iv.kind == "left"
    assert iv.lower is None
    assert iv.upper == date(2024, 4, 19)


def test_done_appears_clamp_inverted_earlier_absent_interval():
    # An earlier pre-flight absent survives the prefix rule -> interval, never inverted.
    obs = [_obs("2024-01-01", "0"), _obs("2024-06-01", "0"), _obs("2024-07-01", "1")]
    iv = _iv(obs, "done_appears", ceiling=date(2024, 4, 19))
    assert iv.status == "done_ambiguous_clamp_inverted"
    assert iv.recovered is True
    assert iv.kind == "interval"
    assert iv.lower == date(2024, 1, 1)
    assert iv.upper == date(2024, 4, 19)
    assert iv.lower < iv.upper


# --------------------------------------------------------------------------- #
# 4. done_installed_during_census all-absent
# --------------------------------------------------------------------------- #
def test_census_all_absent_interval_to_ceiling():
    obs = [_obs("2023-01-01", "0"), _obs("2023-06-01", "0")]
    iv = _iv(obs, "done_installed_during_census", ceiling=date(2024, 4, 19))
    assert iv.kind == "interval"
    assert iv.lower == date(2023, 6, 1)
    assert iv.upper == date(2024, 4, 19)
    assert iv.recovered is False


def test_census_no_ceiling_uses_census_mid():
    obs = [_obs("2023-01-01", "0")]
    iv = _iv(obs, "done_installed_during_census", ceiling=None)
    assert iv.kind == "interval"
    assert iv.upper == CENSUS_MID


# --------------------------------------------------------------------------- #
# 5. census latest_absent >= census_end -> marker_missed_pv recovery
# --------------------------------------------------------------------------- #
def test_census_reclassified_marker_missed_pv_left():
    obs = [_obs("2024-08-01", "0")]  # after census mid, no ceiling
    iv = _iv(obs, "done_installed_during_census", ceiling=None)
    assert iv.status == "done_ambiguous_marker_missed_pv"
    assert iv.recovered is True
    assert iv.kind == "left"
    assert iv.lower is None
    assert iv.upper == CENSUS_MID


# --------------------------------------------------------------------------- #
# 6. done_already_present -> left-censored, clamped
# --------------------------------------------------------------------------- #
def test_already_present_left_censored():
    obs = [_obs("2020-05-01", "1"), _obs("2021-01-01", "1")]
    iv = _iv(obs, "done_already_present_before_geid_history")
    assert iv.kind == "left"
    assert iv.lower is None
    assert iv.upper == date(2020, 5, 1)  # earliest usable obs
    assert iv.recovered is False


def test_already_present_clamped():
    obs = [_obs("2024-07-01", "1")]
    iv = _iv(obs, "done_already_present_before_geid_history", ceiling=date(2024, 4, 19))
    assert iv.upper == date(2024, 4, 19)


# --------------------------------------------------------------------------- #
# 7. done_ambiguous_nonmonotonic 0,1,0,1 -> prefix rule
# --------------------------------------------------------------------------- #
def test_ambiguous_nonmonotonic_prefix_rule():
    obs = [
        _obs("2022-01-01", "0", row=0),
        _obs("2022-06-01", "1", row=1),
        _obs("2023-01-01", "0", row=2),
        _obs("2023-06-01", "1", row=3),
    ]
    iv = _iv(obs, "done_ambiguous_nonmonotonic")
    assert iv.kind == "interval"
    assert iv.lower == date(2022, 1, 1)  # first absent; post-present absent dropped
    assert iv.upper == CENSUS_MID  # no ceiling passed -> census_mid
    assert iv.recovered is True
    assert iv.status == "done_ambiguous_nonmonotonic"


# --------------------------------------------------------------------------- #
# 8. ambiguous with no usable absent -> left, recovered
# --------------------------------------------------------------------------- #
def test_ambiguous_no_absent_left_recovered():
    obs = [_obs("2022-01-01", "1"), _obs("2023-01-01", "1")]
    iv = _iv(obs, "done_ambiguous_no_recent_anchor")
    assert iv.kind == "left"
    assert iv.lower is None
    assert iv.recovered is True
    assert iv.status == "done_ambiguous_no_recent_anchor"


# --------------------------------------------------------------------------- #
# 9. scanning / unknown -> None (dropped)
# --------------------------------------------------------------------------- #
def test_scanning_dropped():
    obs = [_obs("2022-01-01", "0")]
    assert _iv(obs, "scanning") is None
    assert _iv(obs, "totally_unknown_status") is None


# --------------------------------------------------------------------------- #
# 10. non-usable obs excluded from bounds
# --------------------------------------------------------------------------- #
def test_non_usable_excluded():
    obs = [
        _obs("2023-01-01", "0"),
        _obs("2023-03-01", "0", quality="unusable"),  # excluded
        _obs("2023-03-15", "", quality="ambiguous"),  # pv blank -> excluded
        _obs("2023-06-01", "1"),
    ]
    iv = _iv(obs, "done_appears")
    assert iv.lower == date(2023, 1, 1)
    assert iv.upper == date(2023, 6, 1)
    assert iv.n_usable == 2


# --------------------------------------------------------------------------- #
# 11. intervals_from_scan_states on a fixture dir; files unmodified
# --------------------------------------------------------------------------- #
def _write_scan_state(path: Path, *, status: str, grid_id: str, anchor_id: str, results):
    doc = {
        "anchor_id": anchor_id,
        "grid_id": grid_id,
        "status": status,
        "rounds": [{"results": results}],
    }
    path.write_text(json.dumps(doc))


def test_intervals_from_scan_states(tmp_path):
    d = tmp_path / "scan_states"
    d.mkdir()
    _write_scan_state(
        d / "a.json",
        status="done_appears",
        grid_id="JNB0001",
        anchor_id="a",
        results=[
            {"capture_date": "2023-01-01", "pv_present": False, "quality_flag": "usable"},
            {"capture_date": "2023-06-01", "pv_present": True, "quality_flag": "usable"},
        ],
    )
    _write_scan_state(
        d / "b.json",
        status="done_ambiguous_nonmonotonic",
        grid_id="JNB0002",
        anchor_id="b",
        results=[
            {"capture_date": "2022-01-01", "pv_present": False, "quality_flag": "usable"},
            {"capture_date": "2022-06-01", "pv_present": True, "quality_flag": "usable"},
            {"capture_date": "2023-01-01", "pv_present": False, "quality_flag": "usable"},
        ],
    )
    files = sorted(d.glob("*.json"))
    mtimes = {f: f.stat().st_mtime_ns for f in files}

    ivs = cohort.intervals_from_scan_states(
        files, ceilings={"JNB0001": date(2024, 4, 19)}, census_mid=CENSUS_MID
    )
    assert len(ivs) == 2
    by_anchor = {iv.anchor_id: iv for iv in ivs}
    assert by_anchor["a"].status == "done_appears"
    assert by_anchor["a"].grid_id == "JNB0001"
    assert by_anchor["b"].recovered is True
    # read-only: no file was rewritten
    for f in files:
        assert f.stat().st_mtime_ns == mtimes[f]


def test_intervals_from_scan_states_bad_file_raises(tmp_path):
    d = tmp_path / "scan_states"
    d.mkdir()
    (d / "broken.json").write_text("{ this is not json")
    with pytest.raises(Exception):
        cohort.intervals_from_scan_states(
            list(d.glob("*.json")), ceilings={}, census_mid=CENSUS_MID
        )


# --------------------------------------------------------------------------- #
# 12. intervals_from_panel reproduces the scan-state interval (shared core)
# --------------------------------------------------------------------------- #
def test_intervals_from_panel_matches_core():
    unit = ("chip1", "anchorX", "pos")
    obs = [_obs("2023-01-01", "0"), _obs("2023-06-01", "1")]
    panel = {unit: {"rep1": obs}}
    chip_of = {unit: "chip1"}
    strata = {"chip1": "done_appears"}
    ivs = cohort.intervals_from_panel(
        panel, chip_of, strata, ceilings={}, census_mid=CENSUS_MID
    )
    assert len(ivs) == 1
    iv = ivs[0]
    assert iv.kind == "interval"
    assert iv.lower == date(2023, 1, 1)
    assert iv.upper == date(2023, 6, 1)
    assert iv.status == "done_appears"
    assert iv.anchor_id == "anchorX"


# --------------------------------------------------------------------------- #
# 13. interval_counts
# --------------------------------------------------------------------------- #
def test_interval_counts():
    ivs = [
        CensoringInterval(date(2023, 1, 1), date(2023, 6, 1), "interval",
                          status="done_appears", recovered=False),
        CensoringInterval(None, date(2020, 1, 1), "left",
                          status="done_already_present_before_geid_history", recovered=False),
        CensoringInterval(date(2022, 1, 1), CENSUS_MID, "interval",
                          status="done_ambiguous_nonmonotonic", recovered=True),
        CensoringInterval(None, CENSUS_MID, "left",
                          status="done_ambiguous_no_recent_anchor", recovered=True),
    ]
    counts = cohort.interval_counts(ivs)
    assert counts["n"] == 4
    assert counts["n_recovered"] == 2
    assert counts["by_kind"]["interval"] == 2
    assert counts["by_kind"]["left"] == 2
    assert counts["by_status"]["done_appears"] == 1
    assert counts["by_status"]["done_ambiguous_nonmonotonic"] == 1


# --------------------------------------------------------------------------- #
# 14. stratify_intervals
# --------------------------------------------------------------------------- #
def test_stratify_by_region():
    ivs = [
        CensoringInterval(None, CENSUS_MID, "left", grid_id="JNB0001"),
        CensoringInterval(None, CENSUS_MID, "left", grid_id="JNB0009"),
        CensoringInterval(None, CENSUS_MID, "left", grid_id=""),
    ]
    groups = cohort.stratify_intervals(ivs, "region")
    assert set(groups) == {"JNB", "UNK"}
    assert len(groups["JNB"]) == 2
    assert len(groups["UNK"]) == 1


def test_stratify_by_cadence():
    ivs = [
        CensoringInterval(None, CENSUS_MID, "left", cadence_gap_days=30.0),
        CensoringInterval(None, CENSUS_MID, "left", cadence_gap_days=200.0),
        CensoringInterval(None, CENSUS_MID, "left", cadence_gap_days=500.0),
        CensoringInterval(None, CENSUS_MID, "left", cadence_gap_days=None),
    ]
    groups = cohort.stratify_intervals(ivs, "cadence")
    assert len(groups["<=90"]) == 1
    assert len(groups["90-365"]) == 1
    assert len(groups[">365"]) == 1
    assert len(groups["unknown"]) == 1


def test_stratify_by_status():
    ivs = [
        CensoringInterval(None, CENSUS_MID, "left", status="done_appears"),
        CensoringInterval(None, CENSUS_MID, "left", status="done_appears"),
        CensoringInterval(None, CENSUS_MID, "left", status="done_ambiguous_nonmonotonic"),
    ]
    groups = cohort.stratify_intervals(ivs, "status")
    assert len(groups["done_appears"]) == 2
    assert len(groups["done_ambiguous_nonmonotonic"]) == 1


def test_cadence_gap_days_median():
    obs = [_obs("2023-01-01", "0"), _obs("2023-02-01", "0"), _obs("2023-06-01", "1")]
    iv = _iv(obs, "done_appears")
    # gaps: 31 days, 120 days -> median = 75.5
    assert iv.cadence_gap_days == pytest.approx(75.5)


def test_load_grid_ceilings_none_returns_empty():
    assert cohort.load_grid_ceilings(None) == {}
    assert cohort.load_grid_ceilings(Path("/nonexistent/does/not/exist.csv")) == {}


# --------------------------------------------------------------------------- #
# Banked-data test (skipif): real endtoend rep1
# --------------------------------------------------------------------------- #
_ENDTOEND_ROOT = Path.home() / "zasolar_data/geid_temporal/llm_endtoend_20260623"
_REP1_DIRS = [_ENDTOEND_ROOT / "rep1" / "L0" / "scan_states",
              _ENDTOEND_ROOT / "rep1" / "L1" / "scan_states"]
_VEXCEL_CSV = Path(
    "/home/gao/projects/ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv"
)


@pytest.mark.skipif(
    not all(d.exists() for d in _REP1_DIRS),
    reason="banked endtoend rep1 scan states not present",
)
def test_banked_rep1_recovers_ambiguous_and_maps_all():
    files = sorted(f for d in _REP1_DIRS for f in d.glob("*.json"))
    assert files, "expected scan-state JSONs"
    # Every rep1 anchor is a terminal (non-scanning) status.
    n_input = len(files)
    ceilings = cohort.load_grid_ceilings(_VEXCEL_CSV)
    assert ceilings, "expected per-grid ceilings"

    ivs = cohort.intervals_from_scan_states(files, ceilings=ceilings, census_mid=CENSUS_MID)
    counts = cohort.interval_counts(ivs)

    # AC1: recovered-ambiguous count > 0
    assert counts["n_recovered"] > 0
    # 100% retained: all terminal anchors mapped, none dropped
    assert counts["n"] == n_input
    # no negative-width interval ever emitted
    for iv in ivs:
        if iv.kind == "interval":
            assert iv.lower < iv.upper
    # regions all resolve to a 3-char prefix
    regions = cohort.stratify_intervals(ivs, "region")
    assert "UNK" not in regions  # all real JNB grids
