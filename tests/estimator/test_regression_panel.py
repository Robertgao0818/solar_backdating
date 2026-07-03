"""Banked-panel regression gate: reproduce the published fullstack_noscan numbers.

Runs the estimator harness in-process on the 10-rep x 28-unit banked panel
(``fullstack_noscan_20260630/long_all.csv``) and asserts the ``fpd`` and
``sustained`` seam estimators reproduce ``analysis/summary.json`` to 3 dp:

    fpd.map_mode_hit_all   == 0.807   (published mean_interval_mode_hit)
    fpd.undated_flip       == 0.046   (published mean_undated_flip)
    fpd.year_mode_hit      == 0.814   (published mean_year_mode_hit)
    sustained.map_mode_hit_all == 0.911  (published mean_sustained_mode_hit)
    sustained.year_mode_hit    == 0.857  (published mean_sustained_year_mode_hit)

Skips when the read-only banked data is absent (CI-safe).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PANEL = Path(
    os.environ.get(
        "FULLSTACK_PANEL_DIR",
        Path.home() / "zasolar_data/geid_temporal/fullstack_noscan_20260630",
    )
)
SAMPLE = (
    Path.home()
    / "zasolar_data/geid_temporal/mini_reliability_20260624/sample/sample_anchors.csv"
)

pytestmark = pytest.mark.skipif(
    not (PANEL / "long_all.csv").exists() or not SAMPLE.exists(),
    reason="banked panel not present",
)

TOL = 5e-4
PUBLISHED_STRATA_N = {
    "done_already_present_before_geid_history": 5,
    "done_ambiguous_gemini_failed": 8,
    "done_ambiguous_no_recent_anchor": 3,
    "done_ambiguous_nonmonotonic": 5,
    "done_appears": 2,
    "done_installed_during_census": 5,
}


def _run(name: str):
    from solar_backdating.eval.panel_io import (
        load_inventory_weights,
        load_panel,
        load_strata,
    )
    from solar_backdating.estimators import EstimatorConfig
    from scripts.validation.estimator_harness import run_estimator

    panel, chip_of = load_panel(PANEL / "long_all.csv")
    strata = load_strata(SAMPLE)
    weights = load_inventory_weights(None)
    per_unit, headline = run_estimator(
        name, panel, chip_of, strata, weights, EstimatorConfig()
    )
    return per_unit, headline


def test_fpd_published_numbers():
    per_unit, headline = _run("fpd")
    unw = headline["overall_unweighted"]
    assert abs(unw["map_mode_hit_all"] - 0.807) <= TOL
    assert abs(unw["undated_flip"] - 0.046) <= TOL
    assert abs(unw["year_mode_hit"] - 0.814) <= TOL


def test_sustained_published_numbers():
    per_unit, headline = _run("sustained")
    unw = headline["overall_unweighted"]
    assert abs(unw["map_mode_hit_all"] - 0.911) <= TOL
    assert abs(unw["year_mode_hit"] - 0.857) <= TOL


def test_stratum_counts_match_published():
    per_unit, headline = _run("fpd")
    by_st = headline["by_stratum"]
    got = {st: v["n"] for st, v in by_st.items()}
    assert got == PUBLISHED_STRATA_N
    assert sum(got.values()) == 28


def test_full_panel_derive_install_parity():
    """Strongest like-for-like: seam map_date == derive_install fpd/fsd per (unit,rep)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/validation"))
    from fullstack_noscan_analyze import derive_install

    from solar_backdating.eval.panel_io import load_panel
    from solar_backdating.estimators import ClampContext, EstimatorConfig, get_estimator

    panel, _ = load_panel(PANEL / "long_all.csv")
    fpd = get_estimator("fpd")
    sus = get_estimator("sustained")
    for unit, reps in panel.items():
        for rep, obs in reps.items():
            profile = [(o.capture_date.isoformat(), o.pv_present) for o in obs]
            ref_fpd, ref_fsd, _ = derive_install(profile)
            assert fpd(obs, ClampContext(), EstimatorConfig()).map_date == ref_fpd
            assert sus(obs, ClampContext(), EstimatorConfig()).map_date == ref_fsd


@pytest.mark.integration
def test_cli_gate_exit_zero(tmp_path):
    """The gate must also pass as a CLI (exit 0) and write three artifacts."""
    harness = (
        Path(__file__).resolve().parents[2]
        / "scripts/validation/estimator_harness.py"
    )
    out = tmp_path / "harness_out"
    proc = subprocess.run(
        [
            sys.executable,
            str(harness),
            "--long",
            str(PANEL / "long_all.csv"),
            "--sample-anchors",
            str(SAMPLE),
            "--estimators",
            "fpd",
            "sustained",
            "--out-dir",
            str(out),
            "--gate",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for est in ("fpd", "sustained"):
        assert (out / f"summary_{est}.json").exists()
        assert (out / f"per_unit_{est}.csv").exists()
        assert (out / f"report_{est}.md").exists()
