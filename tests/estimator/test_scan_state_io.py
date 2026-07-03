"""Tests for the scan_state.json -> VintageObservation adapter (ISSUE-02 end-to-end)."""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

from solar_backdating.eval.scan_state_io import anchor_id_of, load_scan_observations

ROOT = Path(
    os.environ.get(
        "LLM_ENDTOEND_DIR",
        Path.home() / "zasolar_data/geid_temporal/llm_endtoend_20260623",
    )
)
REP1_L0 = ROOT / "rep1/L0/scan_states"
REP1_L1 = ROOT / "rep1/L1/scan_states"
REFERENCE_CSV = ROOT / "reference.csv"


def _round(round_id, *results):
    return {
        "round_id": round_id,
        "round_type": "initial",
        "window_start_date": "2015-01-01",
        "window_end_date": "2025-01-01",
        "picks": [],
        "results": list(results),
        "completed": True,
        "failed": False,
        "notes": "",
    }


def _result(chip_index, capture_date, pv_present, *, confidence=0.9, quality_flag="usable"):
    return {
        "chip_index": chip_index,
        "capture_date": capture_date,
        "version": 1,
        "pv_present": pv_present,
        "confidence": confidence,
        "quality_flag": quality_flag,
        "decision_source": "gemini_batch",
        "evidence": "",
        "notes": "",
        "chip_path": "/dev/null",
        "actual_zoom": 19,
    }


def _write_scan_state(tmp_path, anchor_id, rounds, status="done_appears"):
    state = {
        "anchor_id": anchor_id,
        "region_key": "johannesburg",
        "grid_id": "JNB0001",
        "status": status,
        "rounds": rounds,
        "next_action": None,
        "started_at": "2026-06-23T00:00:00Z",
        "updated_at": "2026-06-23T00:00:01Z",
        "spec_version": "phase0_v2",
        "notes": "",
    }
    path = tmp_path / f"{anchor_id}.json"
    path.write_text(json.dumps(state))
    return path


# --------------------------------------------------------------------------- #
# Synthetic round/result shape (spec section "scan_state JSON schema")
# --------------------------------------------------------------------------- #
def test_flatten_maps_pv_present_and_carries_fields(tmp_path):
    rounds = [
        _round(
            1,
            _result(1, "2018-01-01", True, confidence=0.7, quality_flag="usable"),
            _result(2, "2019-06-15", False, confidence=0.6, quality_flag="usable"),
            _result(3, "2020-03-30", None, confidence=0.9, quality_flag="ambiguous"),
        ),
    ]
    path = _write_scan_state(tmp_path, "anchor_a", rounds)
    obs = load_scan_observations(path)
    assert [o.pv_present for o in obs] == ["1", "0", ""]
    assert [o.capture_date for o in obs] == [date(2018, 1, 1), date(2019, 6, 15), date(2020, 3, 30)]
    assert [o.confidence for o in obs] == [0.7, 0.6, 0.9]
    assert [o.quality_flag for o in obs] == ["usable", "usable", "ambiguous"]


def test_source_row_is_stable_round_major_enumeration(tmp_path):
    rounds = [
        _round(1, _result(1, "2020-01-01", True), _result(2, "2020-06-01", False)),
        _round(2, _result(1, "2021-01-01", True)),
    ]
    path = _write_scan_state(tmp_path, "anchor_b", rounds)
    obs = load_scan_observations(path)
    assert [o.source_row for o in obs] == [0, 1, 2]


def test_duplicate_capture_date_is_not_deduped(tmp_path):
    """Mirrors production's infer layer (scan_decision.collect_all_results /
    infer_install_dates._all_results): every RoundResult stays a distinct
    observation even when two rounds re-score the SAME calendar date. This is a
    deliberate divergence from panel_io.load_panel's CSV last-wins dedup — see
    the module docstring."""
    rounds = [
        _round(1, _result(1, "2020-01-01", False, confidence=0.5)),
        _round(2, _result(1, "2020-01-01", True, confidence=0.95)),  # re-scan, same date
    ]
    path = _write_scan_state(tmp_path, "anchor_c", rounds)
    obs = load_scan_observations(path)
    assert len(obs) == 2
    assert [o.pv_present for o in obs] == ["0", "1"]
    assert [o.capture_date for o in obs] == [date(2020, 1, 1), date(2020, 1, 1)]


def test_unparseable_capture_date_dropped(tmp_path):
    rounds = [_round(1, _result(1, "", True), _result(2, "2021-01-01", True))]
    path = _write_scan_state(tmp_path, "anchor_d", rounds)
    obs = load_scan_observations(path)
    assert len(obs) == 1
    assert obs[0].capture_date == date(2021, 1, 1)


def test_empty_rounds_and_missing_results_key(tmp_path):
    path = _write_scan_state(tmp_path, "anchor_e", [])
    assert load_scan_observations(path) == []


def test_anchor_id_of(tmp_path):
    rounds = [_round(1, _result(1, "2020-01-01", True))]
    path = _write_scan_state(tmp_path, "anchor_f", rounds)
    assert anchor_id_of(path) == "anchor_f"


def test_read_only_never_rewrites(tmp_path):
    rounds = [_round(1, _result(1, "2020-01-01", True))]
    path = _write_scan_state(tmp_path, "anchor_g", rounds)
    before = path.read_text()
    before_mtime = path.stat().st_mtime_ns
    load_scan_observations(path)
    load_scan_observations(path)
    assert path.read_text() == before
    assert path.stat().st_mtime_ns == before_mtime


# --------------------------------------------------------------------------- #
# skipif-guarded: real production scan states (rep1, L0 group scan + L1 per-target)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not REP1_L0.exists(), reason="llm_endtoend_20260623 rep1/L0 not present")
def test_real_l0_scan_state_shape():
    path = REP1_L0 / "jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_c0000017.json"
    assert path.exists()
    obs = load_scan_observations(path)
    assert len(obs) == 7  # 3 rounds (5+1+1 results), 7 total (verified 2026-07-03)
    assert obs[0].capture_date == date(2018, 3, 30)
    assert obs[0].pv_present == ""  # pv_present: null in this fixture round
    assert obs[0].quality_flag == "ambiguous"
    assert anchor_id_of(path) == "jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_c0000017"


@pytest.mark.skipif(not REP1_L1.exists(), reason="llm_endtoend_20260623 rep1/L1 not present")
def test_real_l1_pertarget_anchor_resolves():
    path = REP1_L1 / "jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00000646.json"
    assert path.exists()
    obs = load_scan_observations(path)
    assert obs  # non-empty (all-abstain here, but the raw rounds still flatten)
    assert all(o.pv_present in ("0", "1", "") for o in obs)
    assert all(o.pv_present == "" for o in obs)  # every result null in this fixture anchor


# --------------------------------------------------------------------------- #
# Tiny end-to-end fixture: build a synthetic reference + one rep's delivery +
# scan-state tree in tmp_path and run the decode script's decode_rep() directly.
# --------------------------------------------------------------------------- #
def _script_module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/validation"))
    import estimator_endtoend_decode as mod

    return mod


def test_tiny_endtoend_fixture_replaces_only_scanned_providers(tmp_path):
    mod = _script_module()
    from solar_backdating.estimators import EstimatorConfig, get_estimator

    root = tmp_path / "llm_endtoend_fixture"
    rep_dir = root / "rep1"
    (rep_dir / "L0/scan_states").mkdir(parents=True)
    (rep_dir / "L1/scan_states").mkdir(parents=True)

    # sf=1: dated by the L0 group scan -> decoder must REPLACE its year.
    _write_scan_state(
        rep_dir / "L0/scan_states",
        "group_a",
        [_round(1, _result(1, "2019-01-01", False), _result(2, "2020-06-01", True))],
    )
    # sf=2: dated by L1 per-target scan -> decoder must REPLACE its year.
    _write_scan_state(
        rep_dir / "L1/scan_states",
        "target_b",
        [_round(1, _result(1, "2017-01-01", False), _result(2, "2018-01-01", True))],
    )

    delivery_rows = [
        # sf, source_grid, ..., date_provider, ..., install_date, interval_start, interval_end,
        # earliest_present, install_confidence, source_anchor_id, undated_reason
        "1,JNB0001,0,0,1,1,gehi_main,done_appears,0,2020-06-01,2019-01-01,2020-06-01,2020-06-01,high,group_a,",
        "2,JNB0001,0,0,1,1,gehi_pertarget,done_appears,0,2018-01-01,2017-01-01,2018-01-01,2018-01-01,high,,",
        "3,JNB0001,0,0,1,1,gehi_census2023,done_appears,0,2023-05-01,2023-01-01,2023-05-01,2023-05-01,high,c9,",
    ]
    header = (
        "source_feature_id,source_grid,centroid_lon,centroid_lat,area_m2,confidence,"
        "date_provider,date_status,date_is_bound,install_date,install_interval_start,"
        "install_interval_end,earliest_present_date,install_confidence,source_anchor_id,"
        "undated_reason"
    )
    (rep_dir / "delivery.csv").write_text(header + "\n" + "\n".join(delivery_rows) + "\n")

    ref_rows = [
        {
            "sf": 1,
            "group_anchor_id": "group_a",
            "target_anchor_id": "target_a_unused",
            "grid_id": "JNB0001",
            "status_stratum": "done_appears",
        },
        {
            "sf": 2,
            "group_anchor_id": "group_b_unused",
            "target_anchor_id": "target_b",
            "grid_id": "JNB0001",
            "status_stratum": "done_appears",
        },
        {
            "sf": 3,
            "group_anchor_id": "group_c_unused",
            "target_anchor_id": "target_c_unused",
            "grid_id": "JNB0001",
            "status_stratum": "done_appears",
        },
    ]

    result = mod.decode_rep(
        rep_dir,
        ref_rows,
        "pava",
        epoch_gap_days=None,
        decoder_epoch_gap_days=None,
        emissions=None,
        vexcel_ceiling={},
    )
    assert result["n_replaced"] == 2  # sf=1 (L0) + sf=2 (L1); sf=3 (census) stays as-is
    assert result["n_scan_state_missing"] == 0
    # sf=1: decoder must match pava's own decode of the same observations.
    pava = get_estimator("pava")
    from solar_backdating.estimators import ClampContext

    expect1 = pava(
        mod.load_scan_observations(rep_dir / "L0/scan_states/group_a.json"),
        ClampContext(),
        EstimatorConfig(),
    ).map_date
    assert result["decode_year"][1] == (expect1[:4] if expect1 else "")
    # sf=3 (census splice-through) keeps the raw delivery year untouched.
    assert result["decode_year"][3] == "2023"
    assert result["baseline_year"][3] == "2023"


@pytest.mark.skipif(
    not REFERENCE_CSV.exists(), reason="llm_endtoend_20260623 reference.csv not present"
)
def test_unmodified_delivery_channel_matches_issue01_banked_tvd():
    """The 'unmodified delivery reference' channel must byte-reproduce ISSUE-01's
    banked pairwise rep-to-rep TVD ([0.0595, 0.0625, 0.0374]) — this is the
    join/splice replication sanity check called out in the ISSUE-02 spec."""
    mod = _script_module()
    reps = ["rep1", "rep2", "rep3"]
    if not all((ROOT / r / "delivery.csv").exists() for r in reps):
        pytest.skip("not all 3 end-to-end rep delivery.csv present")

    ref_rows, invw, prod_year = mod._load_reference_rows(REFERENCE_CSV)
    sfids = [row["sf"] for row in ref_rows]
    channel = mod._run_channel(
        "sanity",
        reps,
        ROOT,
        ref_rows,
        invw,
        prod_year,
        sfids,
        "pava",
        epoch_gap_days=None,
        decoder_epoch_gap_days=None,
        emissions=None,
        vexcel_ceiling={},
    )
    assert channel["unmodified_delivery_reference"]["rep_to_rep_tvd"] == [0.0595, 0.0625, 0.0374]
