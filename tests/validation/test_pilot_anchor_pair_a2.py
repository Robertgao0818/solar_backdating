"""Unit tests for A″ same-sensor present-template helpers."""

from __future__ import annotations

import numpy as np

from scripts.validation.pilot_anchor_pair_a2_2026_07_10 import (
    earliest_absent_key,
    frame_key,
    latest_present_key,
    pair_eligibility,
    second_latest_present_key,
)


def _row(i, aid, date, label, version="296", split="train"):
    return {
        "i": i,
        "anchor_id": aid,
        "capture_date": date,
        "version": version,
        "label": label,
        "split": split,
        "png_path": f"/tmp/{aid}_{date}.png",
        "terminal_status": "done_appears",
    }


def test_latest_and_second_present_keys():
    rows = [
        _row(0, "a", "2018-01-01", "absent"),
        _row(1, "a", "2019-01-01", "present"),
        _row(2, "a", "2020-01-01", "present"),
        _row(3, "a", "2021-01-01", "present"),
    ]
    assert latest_present_key(rows) == ("2021-01-01", "296")
    assert second_latest_present_key(rows) == ("2020-01-01", "296")
    assert earliest_absent_key(rows) == ("2018-01-01", "296")


def test_single_present_has_no_second():
    rows = [
        _row(0, "a", "2018-01-01", "absent"),
        _row(1, "a", "2020-01-01", "present"),
    ]
    assert latest_present_key(rows) == ("2020-01-01", "296")
    assert second_latest_present_key(rows) is None


def test_pair_eligibility_self_pair_count():
    rows = [
        _row(0, "a1", "2018-01-01", "absent"),
        _row(1, "a1", "2019-01-01", "present"),
        _row(2, "a1", "2020-01-01", "present"),
        _row(3, "a2", "2019-01-01", "present"),  # no absent → ineligible
        _row(4, "a2", "2020-01-01", "present"),
    ]
    meta, pair_ok = pair_eligibility(rows)
    assert pair_ok == {"a1"}
    assert meta["n_pair_eligible"] == 1
    # a1 has 3 rows; latest present is self-pair excluded → 2 scorable
    assert meta["n_arm_p_scorable_rows"] == 2
    assert meta["n_arm_p_self_pair_excluded"] == 1


def test_frame_key_uses_version():
    r = _row(0, "a", "2020-01-01", "present", version="300")
    assert frame_key(r) == ("2020-01-01", "300")


def test_smoke_go_synthetic_ranking():
    """Synthetic grids: same-target present closer than absent and than other.

    Use enough heldout anchors that the report half (ceil(n/2) calib / rest
    report) still has ≥2 targets for s_neg.
    """
    from pathlib import Path
    import tempfile

    from scripts.validation.pilot_anchor_pair_a2_2026_07_10 import run_same_sensor_smoke

    rng = np.random.default_rng(0)
    dim = 8
    rows = []
    grids = []
    n_anchors = 12
    for ai in range(n_anchors):
        aid = f"a{ai:02d}"
        base = rng.normal(size=dim).astype(np.float32)
        base /= np.linalg.norm(base) + 1e-8
        other = rng.normal(size=dim).astype(np.float32)
        other /= np.linalg.norm(other) + 1e-8
        for j, (date, lab, vec) in enumerate(
            [
                ("2018-01-01", "absent", other),
                ("2019-01-01", "present", base * 0.9 + other * 0.1),
                ("2020-01-01", "present", base),
            ]
        ):
            i = ai * 3 + j
            rows.append(_row(i, aid, date, lab, split="heldout"))
            g = np.broadcast_to(vec.reshape(1, 1, dim), (3, 3, dim)).astype(np.float16)
            grids.append(g.copy())
    grids_arr = np.stack(grids, axis=0)
    pair_ok = {f"a{ai:02d}" for ai in range(n_anchors)}
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "smoke.json"
        smoke = run_same_sensor_smoke(
            rows=rows,
            pair_ok=pair_ok,
            cand_grids=grids_arr,
            out_path=out,
            rng_seed=0,
        )
    assert smoke["n_targets"] >= 2
    assert smoke["median_cos_nonref_present_ref"] is not None
    assert smoke["median_cos_early_absent_ref"] is not None
    assert smoke["median_cos_other_present_ref"] is not None
    assert smoke["median_cos_nonref_present_ref"] > smoke["median_cos_early_absent_ref"]
    assert smoke["verdict"] == "SMOKE_GO"
