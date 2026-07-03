"""UNIT D tests — ISSUE-03 integration + gate runner.

Covers (1) the additive ``cohort_prior`` inertness contract on the decoder /
harness / endtoend seams (absent prior => bit-exact flat-prior behaviour),
(2) the pure ``build_ac*`` gate-decision logic on synthetic inputs, and (3) a
full ``run_all`` wiring pass over a hand-built endtoend tree so every AC block
is exercised without the banked data. Banked-data tests are skipped when the
real cohort is absent so CI stays green.

Inline synthetic factories, no conftest.
"""
from __future__ import annotations

import json
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

from scripts.validation import issue03_gates as g
from scripts.validation.estimator_endtoend_decode import _build_config
from scripts.validation.estimator_harness import run_estimator
from solar_backdating.estimators import ClampContext, EstimatorConfig, VintageObservation
from solar_backdating.estimators.changepoint import CohortPrior, estimate_changepoint
from solar_backdating.estimators.survival import (
    CensoringInterval,
    fit_turnbull,
    to_cohort_prior,
)

CENSUS_MID = date(2024, 6, 30)


def _obs(day: str, pv: str, *, quality: str = "usable", row: int = 0) -> VintageObservation:
    return VintageObservation(
        capture_date=date.fromisoformat(day), pv_present=pv,
        confidence=0.9, quality_flag=quality, source_row=row,
    )


def _iv(lower, upper, kind, *, status="done_appears", recovered=False,
        grid_id="JNB0001", cadence=None, anchor="a") -> CensoringInterval:
    return CensoringInterval(
        lower=date.fromisoformat(lower) if lower else None,
        upper=date.fromisoformat(upper) if upper else None,
        kind=kind, anchor_id=anchor, grid_id=grid_id, status=status,
        recovered=recovered, cadence_gap_days=cadence,
    )


# --------------------------------------------------------------------------- #
# 1. Flat CohortPrior is inert in the decoder (additive-inertness core).
# --------------------------------------------------------------------------- #
def test_flat_prior_inert_in_decoder():
    obs = [_obs("2022-01-01", "0", row=0), _obs("2022-06-01", "0", row=1),
           _obs("2023-01-01", "1", row=2), _obs("2023-06-01", "1", row=3)]
    flat = CohortPrior(
        year_log_mass=tuple((y, 0.0) for y in range(2018, 2026)), beyond_log_mass=0.0
    )
    none_cfg = EstimatorConfig(decoder_epoch_gap_days=45)
    flat_cfg = EstimatorConfig(decoder_epoch_gap_days=45, cohort_prior=flat)
    p_none = estimate_changepoint(obs, ClampContext(), none_cfg)
    p_flat = estimate_changepoint(obs, ClampContext(), flat_cfg)
    assert p_none.posterior == pytest.approx(p_flat.posterior)
    assert p_none.map_date == p_flat.map_date


# --------------------------------------------------------------------------- #
# 2/3. endtoend _build_config threads cohort_prior; None leaves it None.
# --------------------------------------------------------------------------- #
def test_endtoend_build_config_threads_cohort_prior():
    cp = CohortPrior(year_log_mass=((2023, -0.1),), beyond_log_mass=-2.0)
    cfg = _build_config(epoch_gap_days=None, decoder_epoch_gap_days=45, emissions=None,
                        cohort_prior=cp)
    assert cfg.cohort_prior is cp
    cfg_none = _build_config(epoch_gap_days=None, decoder_epoch_gap_days=45, emissions=None)
    assert cfg_none.cohort_prior is None


def test_harness_flat_prior_equals_none():
    unit = ("chip1", "aX", "pos")
    obs = [_obs("2022-01-01", "0"), _obs("2023-06-01", "1")]
    panel = {unit: {"rep1": obs, "rep2": obs}}
    chip_of = {unit: "chip1"}
    strata = {"chip1": "done_appears"}
    weights = {"done_appears": 1.0}
    flat = CohortPrior(
        year_log_mass=tuple((y, 0.0) for y in range(2018, 2026)), beyond_log_mass=0.0
    )
    base = EstimatorConfig(decoder_epoch_gap_days=45)
    import dataclasses
    flat_cfg = dataclasses.replace(base, cohort_prior=flat)
    _, h_none = run_estimator("changepoint", panel, chip_of, strata, weights, base)
    _, h_flat = run_estimator("changepoint", panel, chip_of, strata, weights, flat_cfg)
    assert h_none["overall_unweighted"] == h_flat["overall_unweighted"]


# --------------------------------------------------------------------------- #
# 4. build_ac1 counts (n_input / n_mapped / n_dropped / recovered).
# --------------------------------------------------------------------------- #
def test_build_ac1_counts():
    ivs = [
        _iv("2023-01-01", "2023-06-01", "interval", status="done_appears"),
        _iv(None, "2020-01-01", "left", status="done_already_present_before_geid_history"),
        _iv("2022-01-01", "2024-06-30", "interval",
            status="done_ambiguous_nonmonotonic", recovered=True),
    ]
    out = g.build_ac1({"rep1": ivs}, {"rep1": 5})
    assert out["rep1"]["n_input"] == 5
    assert out["rep1"]["n_mapped"] == 3
    assert out["rep1"]["n_dropped"] == 2
    assert out["rep1"]["n_recovered"] == 1
    assert out["rep1"]["by_kind"]["interval"] == 2
    assert out["rep1"]["by_kind"]["left"] == 1


# --------------------------------------------------------------------------- #
# 5. build_ac2 export (Turnbull fit -> block with prior json).
# --------------------------------------------------------------------------- #
def test_build_ac2_export():
    ivs = [_iv("2022-01-01", "2022-06-01", "interval"),
           _iv("2023-01-01", "2023-06-01", "interval")]
    fit = fit_turnbull(ivs)
    prior = to_cohort_prior(fit)
    out = g.build_ac2(fit, prior, "/tmp/cohort_prior.json", sweep=[{"smooth_lambda": 0.05}])
    assert out["converged"] is True
    assert out["cohort_prior"]["year_log_mass"]
    assert out["cohort_prior_json_path"] == "/tmp/cohort_prior.json"
    assert out["hyperparam_sweep"] == [{"smooth_lambda": 0.05}]


# --------------------------------------------------------------------------- #
# 6. build_ac4 bias note (per-grid cadence dispersion fires / clean).
# --------------------------------------------------------------------------- #
def test_build_ac4_dispersion_bias_note_fires():
    ivs = []
    for i in range(4):
        ivs.append(_iv("2022-01-01", "2022-06-01", "interval",
                       grid_id="JNB0001", cadence=30.0, anchor=f"a{i}"))
    for i in range(4):
        ivs.append(_iv("2020-01-01", "2023-06-01", "interval",
                       grid_id="JNB0002", cadence=800.0, anchor=f"b{i}"))
    out = g.build_ac4(ivs)
    assert out["per_grid_cadence_dispersion"] is not None
    assert out["per_grid_cadence_dispersion"]["spread_days"] > 365
    assert "correlate" in out["bias_note"]
    assert "JNB" in out["by_region"]


def test_build_ac4_clean_note_when_cadence_uniform():
    ivs = [
        _iv("2022-01-01", "2022-06-01", "interval", grid_id="JNB0001", cadence=30.0, anchor=f"a{i}")
        for i in range(3)
    ] + [
        _iv("2022-02-01", "2022-07-01", "interval", grid_id="JNB0002", cadence=32.0, anchor=f"b{i}")
        for i in range(3)
    ]
    out = g.build_ac4(ivs)
    assert "no strong" in out["bias_note"]


# --------------------------------------------------------------------------- #
# 7. build_ac5 gate: survival < point -> PASS; survival > point -> FAIL.
# --------------------------------------------------------------------------- #
def test_build_ac5_gate_pass():
    out = g.build_ac5(point_tvd=[0.081, 0.059, 0.079],
                      survival_tvd_vals=[0.010, 0.008, 0.012],
                      posterior_tvd=[0.03, 0.02, 0.04], sup_norm_ds=[0.02, 0.01, 0.03])
    assert out["beats_point_date"] is True
    assert out["verdict"] == "PASS"


def test_build_ac5_gate_fail():
    out = g.build_ac5(point_tvd=[0.05, 0.05, 0.05],
                      survival_tvd_vals=[0.10, 0.09, 0.11],
                      posterior_tvd=[0.06, 0.05, 0.07], sup_norm_ds=[0.1, 0.1, 0.1])
    assert out["beats_point_date"] is False
    assert out["verdict"] == "FAIL"


# --------------------------------------------------------------------------- #
# 8. build_ac6 sensitivity: dropping recovered changes the curve.
# --------------------------------------------------------------------------- #
def test_build_ac6_sensitivity_nonzero_when_recovered_present():
    ivs = [
        _iv("2020-01-01", "2020-06-01", "interval", status="done_appears", anchor="a"),
        _iv("2021-01-01", "2021-06-01", "interval", status="done_appears", anchor="b"),
        _iv("2024-01-01", "2024-06-01", "interval",
            status="done_ambiguous_nonmonotonic", recovered=True, anchor="c"),
        _iv("2024-01-01", "2024-06-01", "interval",
            status="done_ambiguous_marker_missed_pv", recovered=True, anchor="d"),
    ]
    out = g.build_ac6({"rep1": ivs})
    assert out["n_recovered_total"] == 2
    assert out["per_rep"]["rep1"]["tvd_with_vs_without"] > 0.0
    assert out["per_rep"]["rep1"]["marker_in_out_tvd"] >= 0.0


# --------------------------------------------------------------------------- #
# 9. build_ac7 gate PASS / FAIL.
# --------------------------------------------------------------------------- #
def _metrics(map_hit, undated, hpd, inv_map=0.80, inv_year=0.76, da_year=0.87):
    return {
        "map_mode_hit_all": map_hit, "map_mode_hit_dated": map_hit,
        "year_mode_hit": 0.87, "undated_flip": undated, "hpd": hpd,
        "inv_map": inv_map, "inv_year": inv_year, "done_appears_year": da_year,
        "fpd_done_appears_year": 0.56,
    }


def test_build_ac7_gate_pass():
    wp = _metrics(0.905, 0.055, 0.914)
    npr = {**_metrics(0.905, 0.055, 0.914), "fpd_done_appears_year": 0.56}
    pava = _metrics(0.889, 0.055, 0.941)
    out = g.build_ac7(wp, npr, pava, {"ok": True})
    assert out["gate"]["mode_hit_ge_0882"] is True
    assert out["gate"]["beats_pava"] is True
    assert out["gate"]["baselines_bit_exact"] is True
    assert out["verdict"] == "PASS"


def test_build_ac7_gate_fail_on_low_mode_hit():
    wp = _metrics(0.850, 0.055, 0.914)
    npr = {**_metrics(0.850, 0.055, 0.914), "fpd_done_appears_year": 0.56}
    pava = _metrics(0.889, 0.055, 0.941)
    out = g.build_ac7(wp, npr, pava, {"ok": True})
    assert out["gate"]["mode_hit_ge_0882"] is False
    assert out["verdict"] == "FAIL"


def test_build_ac7_gate_fail_on_baseline_drift():
    wp = _metrics(0.905, 0.055, 0.914)
    npr = {**_metrics(0.905, 0.055, 0.914), "fpd_done_appears_year": 0.56}
    pava = _metrics(0.889, 0.055, 0.941)
    out = g.build_ac7(wp, npr, pava, {"ok": False})
    assert out["verdict"] == "FAIL"


# --------------------------------------------------------------------------- #
# 10. run_all over a hand-built endtoend tree: every AC block present + typed.
# --------------------------------------------------------------------------- #
def _write_scan(path: Path, *, status, grid_id, anchor_id, results):
    path.write_text(json.dumps({
        "anchor_id": anchor_id, "grid_id": grid_id, "status": status,
        "rounds": [{"results": results}],
    }))


def _r(day, pv, quality="usable"):
    return {"capture_date": day, "pv_present": pv, "quality_flag": quality}


def _make_endtoend_tree(root: Path, reps):
    # reference.csv: one scanned (gehi_main, done_appears) + one splice-through.
    ref_header = [
        "group_anchor_id", "target_anchor_id", "source_feature_id", "grid_id",
        "centroid_lon", "centroid_lat", "source_area_m2", "status_stratum",
        "prod_date_provider", "prod_date_status", "prod_date_is_bound",
        "prod_install_date", "prod_install_interval_start", "prod_install_interval_end",
        "prod_earliest_present_date", "prod_install_confidence", "prod_undated_reason",
        "prod_install_year", "inv_weight", "prod_agree_key",
    ]
    rows = [
        ["grpA", "tgtA", "1001", "JNB0001", "0", "0", "10", "done_appears",
         "gehi_main", "ok", "0", "2023-05-01", "2023-01-01", "2023-05-01",
         "2023-05-01", "0.9", "", "2023", "0.69", "INTERVAL|2023-01-01|2023-05-01"],
        ["grpB", "tgtB", "1002", "JNB0002", "0", "0", "10", "done_appears",
         "gehi_census2023", "ok", "0", "2022-08-01", "2022-02-01", "2022-08-01",
         "2022-08-01", "0.9", "", "2022", "0.31", "INTERVAL|2022-02-01|2022-08-01"],
    ]
    with (root / "reference.csv").open("w", newline="") as fh:
        fh.write(",".join(ref_header) + "\n")
        for r in rows:
            fh.write(",".join(r) + "\n")

    dcols = ["source_feature_id", "source_grid", "centroid_lon", "centroid_lat", "area_m2",
             "confidence", "date_provider", "date_status", "date_is_bound", "install_date",
             "install_interval_start", "install_interval_end", "earliest_present_date",
             "install_confidence", "source_anchor_id", "undated_reason"]
    for i, rep in enumerate(reps):
        rdir = root / rep
        (rdir / "L0" / "scan_states").mkdir(parents=True)
        (rdir / "L1" / "scan_states").mkdir(parents=True)
        # scanned anchor: vary the earliest-present month across reps (endpoint jitter),
        # kept within 2023 so the point year is stable but bounds wobble.
        present_day = f"2023-0{4 + i}-01"
        _write_scan(rdir / "L0" / "scan_states" / "grpA.json", status="done_appears",
                    grid_id="JNB0001", anchor_id="grpA",
                    results=[_r("2023-01-01", False), _r(present_day, True)])
        with (rdir / "delivery.csv").open("w", newline="") as fh:
            fh.write(",".join(dcols) + "\n")
            fh.write(",".join(["1001", "JNB0001", "0", "0", "10", "0.9", "gehi_main",
                               "ok", "0", present_day, "2023-01-01", present_day,
                               present_day, "0.9", "grpA", ""]) + "\n")
            fh.write(",".join(["1002", "JNB0002", "0", "0", "10", "0.9", "gehi_census2023",
                               "ok", "0", "2022-08-01", "2022-02-01", "2022-08-01",
                               "2022-08-01", "0.9", "", ""]) + "\n")


def _make_prod_cohort(prod: Path):
    d = prod / "scan_states"
    d.mkdir(parents=True)
    for i in range(6):
        yr = 2020 + (i % 4)
        _write_scan(d / f"c{i}.json", status="done_appears", grid_id="JNB0001",
                    anchor_id=f"c{i}",
                    results=[_r(f"{yr}-01-01", False), _r(f"{yr}-06-01", True)])
    # one recovered-ambiguous so AC1 recovery count > 0
    _write_scan(d / "amb.json", status="done_ambiguous_nonmonotonic", grid_id="JNB0002",
                anchor_id="amb",
                results=[_r("2021-01-01", False), _r("2021-06-01", True),
                         _r("2022-01-01", False)])


def _make_panel(long_path: Path, sample_path: Path):
    cols = ["rep", "chip_id", "anchor_id", "target_label", "window_idx", "date_index",
            "capture_date", "pv_present", "pv_score", "quality_flag", "sequence_pattern",
            "sequence_confidence", "consistency_flag", "decision_source", "error"]
    lines = [",".join(cols)]
    for chip, status in (("chipA", "done_appears"), ("chipB", "done_appears")):
        for rep in ("rep1", "rep2"):
            for di, (day, pv) in enumerate(
                [("2022-01-01", "0"), ("2022-06-01", "0"), ("2023-06-01", "1")]
            ):
                lines.append(",".join([
                    rep, chip, f"anc_{chip}", "pos", "0", str(di), day, pv, "0.9",
                    "usable", "appear", "0.9", "ok", "src", "",
                ]))
    long_path.write_text("\n".join(lines) + "\n")
    sample_path.write_text(
        "anchor_id,status_stratum\nchipA,done_appears\nchipB,done_appears\n"
    )


@pytest.mark.integration
def test_run_all_synthetic_tree(tmp_path):
    root = tmp_path / "endtoend"
    root.mkdir()
    reps = ["rep1", "rep2", "rep3"]
    _make_endtoend_tree(root, reps)
    prod = tmp_path / "prod"
    _make_prod_cohort(prod)
    long_path = tmp_path / "long_all_extended.csv"
    sample_path = tmp_path / "sample_anchors.csv"
    _make_panel(long_path, sample_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    args = Namespace(
        root=root, reps=reps, layers=["L0", "L1"], prod_cohort=prod,
        vexcel_capture_csv=Path("/nonexistent.csv"), census_mid="2024-06-30",
        panel_long=long_path, sample_anchors=sample_path,
        panel_a_long=Path("/nonexistent_panel_a.csv"),
        emissions_json=Path("/nonexistent_emissions.json"),
        decoder_epoch_gap_days=45, smooth_lambda=0.05, beyond_policy="terminal_year",
        temperature=1.0, sweep=False, out_dir=out_dir,
    )
    payload = g.run_all(args)

    for block in ("config", "ac1_intervals", "ac2_turnbull", "ac3_aggregation",
                  "ac4_noninformative", "ac5_gate", "ac6_sensitivity",
                  "ac7_integration", "overall_verdict"):
        assert block in payload, f"missing {block}"

    # AC1: prod cohort recovered the ambiguous anchor, all terminal anchors mapped
    assert payload["ac1_intervals"]["prod_cohort"]["n_recovered"] >= 1
    assert payload["ac1_intervals"]["prod_cohort"]["n_dropped"] == 0
    # AC2: Turnbull converged, prior exported
    assert payload["ac2_turnbull"]["converged"] is True
    assert payload["ac2_turnbull"]["cohort_prior"]["year_log_mass"]
    # AC5/AC7 verdicts are PASS|FAIL strings; both channels present
    assert payload["ac5_gate"]["verdict"] in ("PASS", "FAIL")
    assert payload["ac7_integration"]["verdict"] in ("PASS", "FAIL")
    # baseline bit-exact was skipped (panel A absent) -> ok True
    assert payload["ac7_integration"]["baseline_bit_exact"]["ok"] is True
    # artifacts written
    assert (out_dir / "issue03_gates.json").exists()
    assert (out_dir / "cohort_prior.json").exists()
    assert (out_dir / "intervals_rep1.csv").exists()
    assert (out_dir / "report.md").exists()


# --------------------------------------------------------------------------- #
# 11. Banked (skipif): prod cohort recovery + Turnbull convergence.
# --------------------------------------------------------------------------- #
_PROD = Path.home() / "zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02"
_VEXCEL = Path(
    "/home/gaosh/projects/ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv"
)


@pytest.mark.skipif(
    not (_PROD / "scan_states").exists(),
    reason="banked production cohort scan states absent",
)
def test_banked_prod_cohort_recovers_and_fits():
    ceilings = g.cohort.load_grid_ceilings(_VEXCEL)
    intervals, n_input = g.compute_prod_intervals(_PROD, ceilings, CENSUS_MID)
    assert n_input > 10000
    counts = g.cohort.interval_counts(intervals)
    # ~23% ambiguous recovered as censored observations
    assert counts["n_recovered"] > 0
    assert counts["n"] == n_input  # every terminal anchor mapped
    fit = fit_turnbull(intervals)
    assert fit.converged
    prior = to_cohort_prior(fit)
    assert prior.year_log_mass
