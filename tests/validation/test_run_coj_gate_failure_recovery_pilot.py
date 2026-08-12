from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validation import run_coj_gate_failure_recovery_pilot as pilot


def response(**updates):
    value = {
        "target_match": "same_target",
        "coj_2019_state": "absent",
        "coj_2023_state": "present",
        "sequence_status": "monotonic_install",
        "first_pv_frame": "F03",
        "confidence": 0.9,
        "reason": "visible transition",
    }
    value.update(updates)
    return value


def rep(number, **updates):
    return {**response(**updates), "_rep": number}


def test_schema_and_blank_slot_constraints():
    got = pilot.validate_response(response(), {"F01", "F02", "F03"})
    assert got["first_pv_frame"] == "F03"
    with pytest.raises(ValueError, match="blank"):
        pilot.validate_response(response(first_pv_frame="F04"), {"F01", "F02", "F03"})
    with pytest.raises(ValueError, match="unclear target"):
        pilot.validate_response(response(target_match="unclear"), {"F01", "F02", "F03"})


def test_exact_full_tuple_consensus_and_third_rep():
    primary = [rep(1), rep(2)]
    assert not pilot.needs_third_rep(primary)
    assert pilot.consensus(primary)["sequence_status"] == "monotonic_install"
    split = [rep(1), rep(2, coj_2023_state="absent")]
    assert pilot.needs_third_rep(split)
    got = pilot.consensus([*split, rep(3, coj_2023_state="absent")])
    assert got["agreeing_reps"] == [2, 3]
    assert got["coj_2023_state"] == "absent"


def test_three_way_disagreement_fails_closed():
    got = pilot.consensus([
        rep(1), rep(2, first_pv_frame="F04"),
        rep(3, sequence_status="already_present_before_F01",
            first_pv_frame="before_F01"),
    ])
    assert got["target_match"] == "unclear"
    assert got["sequence_status"] == "unreviewable"
    assert got["agreeing_reps"] == []


def test_largest_remainder_is_exact_and_bounded():
    sizes = {("a",): 10, ("b",): 20, ("c",): 3}
    got = pilot.largest_remainder_allocation(sizes, 11)
    assert sum(got.values()) == 11
    assert all(0 <= got[key] <= sizes[key] for key in sizes)


def test_zero_allocations_are_explicit():
    sizes = {("large",): 1000, ("tiny",): 1}
    got = pilot.largest_remainder_allocation(sizes, 10)
    assert got[("tiny",)] == 0
    assert got[("large",)] == 10


def test_r0_selection_keeps_bounds_and_six_frames(tmp_path):
    import pandas as pd

    rows = []
    for index, date in enumerate(pd.date_range("2020-01-01", periods=10, freq="MS")):
        path = tmp_path / f"{index}.png"
        path.write_bytes(b"x")
        rows.append({"capture_date": date.date().isoformat(), "chip_png_path": str(path)})
    got = pilot._select_r0_rows(
        pd.DataFrame(rows), "2020-04-01", "2020-06-01"
    )
    assert len(got) == 6
    assert {"2020-04-01", "2020-06-01"} <= set(got["capture_date"])


def test_transport_contract():
    assert pilot.MODEL_ALIAS == "gemini-3.5-flash-low"
    assert pilot.MAX_OUTPUT_TOKENS == 8192
    assert pilot.DEFAULT_WORKERS == 30
    assert pilot.DEFAULT_QPS == 6
