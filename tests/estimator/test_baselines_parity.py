"""Parity of fpd/sustained baselines against derive_install (bit-exact)."""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import date
from itertools import islice
from pathlib import Path

import pytest

from scripts.validation.fullstack_noscan_analyze import derive_install
from solar_backdating.estimators import (
    ClampContext,
    EstimatorConfig,
    get_estimator,
)
from solar_backdating.estimators.seam import VintageObservation

FPD = get_estimator("fpd")
SUSTAINED = get_estimator("sustained")

GOLDEN = Path(__file__).parent / "data" / "golden_mini_long.csv"


def _obs_from_profile(profile):
    """profile = sorted [(iso_date, pv)] -> VintageObservation list, source_row=idx."""
    out = []
    for i, (iso, pv) in enumerate(profile):
        y, m, d = (int(x) for x in iso.split("-"))
        out.append(
            VintageObservation(capture_date=date(y, m, d), pv_present=pv, source_row=i)
        )
    return out


SYNTHETIC_PROFILES = [
    [],  # empty
    [("2020-01-01", "0"), ("2020-06-01", "0"), ("2021-01-01", "0")],  # all absent
    [("2019-03-01", "1"), ("2020-03-01", "1")],  # all present
    [("2018-01-01", "1"), ("2019-01-01", "0"), ("2020-01-01", "0")],  # early blip
    [("2017-05-01", ""), ("2018-05-01", "0"), ("2019-05-01", "1"), ("2020-05-01", "")],
    [("2018-01-01", "0"), ("2019-01-01", "1"), ("2020-01-01", "0"), ("2021-01-01", "1")],
    [("2018-01-01", ""), ("2019-01-01", ""), ("2020-01-01", "")],  # all abstain
    [("2018-01-01", "0"), ("2019-01-01", "1")],  # single onset
]


@pytest.mark.parametrize("profile", SYNTHETIC_PROFILES)
def test_parity_synthetic(profile):
    fpd_ref, fsd_ref, undated_ref = derive_install(profile)
    obs = _obs_from_profile(profile)
    fpd_post = FPD(obs, ClampContext(), EstimatorConfig())
    sus_post = SUSTAINED(obs, ClampContext(), EstimatorConfig())

    assert fpd_post.map_date == fpd_ref
    assert sus_post.map_date == fsd_ref
    assert (fpd_post.map_date == "") == undated_ref


@pytest.mark.parametrize("profile", SYNTHETIC_PROFILES)
def test_degenerate_posterior_contract(profile):
    obs = _obs_from_profile(profile)
    for est in (FPD, SUSTAINED):
        post = est(obs, ClampContext(), EstimatorConfig())
        assert abs(sum(post.posterior) - 1.0) < 1e-9
        assert post.p_undated in (0.0, 1.0)
        assert (post.map_date == "") == (post.p_undated == 1.0)
        # point mass: exactly one cell carries 1.0
        assert sum(1 for x in post.posterior if x == 1.0) == 1


def test_fpd_interval_bounds():
    profile = [("2018-01-01", "0"), ("2019-01-01", "1"), ("2020-01-01", "0")]
    obs = _obs_from_profile(profile)
    post = FPD(obs, ClampContext(), EstimatorConfig())
    assert post.map_date == "2019-01-01"
    assert post.map_interval_start == date(2018, 1, 1)  # prior absent
    assert post.map_interval_end == date(2019, 1, 1)
    assert post.credible_low_date == date(2019, 1, 1)
    assert post.credible_high_date == date(2019, 1, 1)


def _load_golden():
    data = defaultdict(lambda: defaultdict(dict))
    with GOLDEN.open(newline="") as fh:
        for r in csv.DictReader(fh):
            unit = (r["chip_id"], r["anchor_id"], r["target_label"])
            data[unit][r["rep"]][r["capture_date"]] = r["pv_present"]
    return data


def test_parity_golden_fixture():
    data = _load_golden()
    assert len(data) == 4  # chipA..chipD
    for _unit, reps in data.items():
        for _rep, datemap in reps.items():
            profile = sorted(datemap.items())
            fpd_ref, fsd_ref, _ = derive_install(profile)
            obs = _obs_from_profile(profile)
            assert FPD(obs, ClampContext(), EstimatorConfig()).map_date == fpd_ref
            assert SUSTAINED(obs, ClampContext(), EstimatorConfig()).map_date == fsd_ref


def test_golden_covers_divergence_cases():
    """chipC: fpd dated but sustained undated (isolated early blip)."""
    data = _load_golden()
    reps = data[("chipC", "anchorC", "T01")]
    profile = sorted(next(iter(reps.values())).items())
    obs = _obs_from_profile(profile)
    assert FPD(obs, ClampContext(), EstimatorConfig()).map_date == "2018-01-01"
    assert SUSTAINED(obs, ClampContext(), EstimatorConfig()).map_date == ""


# --- real-data slice parity (skip if banked panel absent) ------------------

_PANEL = Path(
    os.environ.get(
        "FULLSTACK_PANEL_DIR",
        Path.home() / "zasolar_data/geid_temporal/fullstack_noscan_20260630",
    )
)
_LONG = _PANEL / "long_all.csv"


@pytest.mark.skipif(not _LONG.exists(), reason="banked panel not present")
def test_parity_real_slice():
    """Replicate load_long grouping and assert seam == derive_install on a
    slice of the real panel (first ~4000 rows -> several units x reps)."""
    data = defaultdict(lambda: defaultdict(dict))
    with _LONG.open(newline="") as fh:
        for r in islice(csv.DictReader(fh), 4000):
            unit = (r["chip_id"], r["anchor_id"], r["target_label"])
            data[unit][r["rep"]][r["capture_date"]] = r["pv_present"]
    assert data, "expected at least one unit in the slice"
    checked = 0
    for _unit, reps in data.items():
        for _rep, datemap in reps.items():
            profile = sorted(datemap.items())
            fpd_ref, fsd_ref, undated_ref = derive_install(profile)
            obs = _obs_from_profile(profile)
            fpd_post = FPD(obs, ClampContext(), EstimatorConfig())
            sus_post = SUSTAINED(obs, ClampContext(), EstimatorConfig())
            assert fpd_post.map_date == fpd_ref
            assert sus_post.map_date == fsd_ref
            assert (fpd_post.map_date == "") == undated_ref
            checked += 1
    assert checked > 0
