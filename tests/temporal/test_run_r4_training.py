"""Contract tests for the Run3-native R4 training runner (v2 H1 default)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "temporal"))

import run_r4_training as r4  # noqa: E402


def test_calibration_role_is_stable_and_disjoint():
    splits = pd.DataFrame(
        {"anchor_id": [f"a{i:03d}" for i in range(100)], "split": ["calibration"] * 100}
    )
    a = r4.build_calibration_roles(splits, enforce_counts=False)
    b = r4.build_calibration_roles(splits.sample(frac=1, random_state=3), enforce_counts=False)
    pd.testing.assert_frame_equal(a, b)
    assert set(a.r4_role) == {"cal_es", "cal_fit", "cal_select"}
    assert a.anchor_id.nunique() == len(a)


def test_test_access_guard_fails_closed():
    r4.assert_r4_split_access(pd.DataFrame({"split": ["train", "calibration"]}), context="ok")
    with pytest.raises(PermissionError, match="forbidden"):
        r4.assert_r4_split_access(pd.DataFrame({"split": ["train", "test"]}), context="bad")


@dataclass
class _Tlo:
    target_localized: bool
    abstain: bool


def test_effective_label_three_branch_gate():
    assert r4.effective_label("present", None) == ("present", True)
    assert r4.effective_label("absent", _Tlo(True, False)) == ("absent", False)
    assert r4.effective_label("present", _Tlo(True, True)) == ("uninformative", False)
    assert r4.effective_label("absent", _Tlo(False, False)) == ("uninformative", False)


def _anchor(status: str, labels: list[str], dates: list[str], census: str = "2023-01-01"):
    return pd.DataFrame(
        {
            "anchor_id": ["a"] * len(labels),
            "scan_status": [status] * len(labels),
            "effective_label": labels,
            "capture_date": dates,
            "chip_index": list(range(len(labels))),
            "census_date": [census] * len(labels),
        }
    )


def test_teacher_bracket_status_mapping():
    appears = r4.derive_teacher_bracket(
        _anchor("done_appears", ["absent", "absent", "present"], ["2019-01-01", "2020-01-01", "2021-01-01"])
    )
    assert appears == r4.TeacherBracket("interval", date(2020, 1, 1), date(2021, 1, 1))
    census = r4.derive_teacher_bracket(
        _anchor(
            "done_installed_during_census",
            ["absent", "present", "absent"],
            ["2020-01-01", "2022-01-01", "2024-01-01"],
        )
    )
    assert census == r4.TeacherBracket("census_bound", date(2020, 1, 1), date(2023, 1, 1))
    left = r4.derive_teacher_bracket(
        _anchor("done_already_present_before_geid_history", ["present"], ["2018-01-01"])
    )
    assert left == r4.TeacherBracket("left_censored", None, date(2018, 1, 1))
    assert r4.derive_teacher_bracket(
        _anchor("done_ambiguous_nonmonotonic", ["uninformative"], ["2020-01-01"])
    ) is None


def test_feature_normalizer_train_only_contract():
    x = np.zeros((3, r4.FEATURE_DIM), dtype=np.float32)
    x[1] = 1
    x[2] = 2
    mean, std = r4.feature_normalizer(x)
    assert mean.shape == std.shape == (r4.FEATURE_DIM,)
    np.testing.assert_allclose(mean, 1.0)
    np.testing.assert_allclose(std, np.sqrt(2 / 3))
    constant_mean, constant_std = r4.feature_normalizer(np.ones((2, r4.FEATURE_DIM), np.float32))
    np.testing.assert_allclose(constant_mean, 1.0)
    np.testing.assert_allclose(constant_std, 1.0)


def test_exact_head_parameter_count_and_biases():
    import torch

    torch.manual_seed(7)
    labels = ["absent"] * 4 + ["present"] * 2 + ["uninformative"] * 3
    model, meta = r4.make_head(labels)
    assert sum(p.numel() for p in model.parameters()) == r4.EXPECTED_PARAMS
    assert meta["quality_bias"] == pytest.approx(np.log(6 / 3))
    assert model.quality.bias.item() == pytest.approx(np.log(2))
    assert model.state.bias.tolist() == pytest.approx([np.log(4 / 6), np.log(2 / 6)])


def test_torch_phase0_matches_reference_decoder():
    import torch
    from solar_backdating.estimators.changepoint import estimate_changepoint
    from solar_backdating.estimators.emissions import FrameEmission
    from solar_backdating.estimators.seam import ClampContext, EstimatorConfig

    dates = [date(2019, 1, 1), date(2019, 1, 20), date(2020, 6, 1)]
    q = torch.tensor([0.6, 0.8, 0.9], dtype=torch.float64)
    probs = torch.tensor([[0.7, 0.3], [0.8, 0.2], [0.1, 0.9]], dtype=torch.float64)
    logits = torch.log(probs)
    got, bounds = r4.torch_phase0_posterior(q, logits, dates)
    frames = [
        FrameEmission(capture_date=d, q=float(qq), e0=float(p[0]), e1=float(p[1]), source_row=i)
        for i, (d, qq, p) in enumerate(zip(dates, q, probs))
    ]
    ref = estimate_changepoint(
        [], ClampContext(), EstimatorConfig(decoder_epoch_gap_days=45, frame_emissions=frames)
    )
    np.testing.assert_allclose(got.detach().numpy(), ref.posterior, atol=1e-12)
    assert bounds == [(c.start_date, c.end_date) for c in ref.epochs]


def test_torch_phase0_matches_reference_with_prior_and_census_cutoff():
    import torch
    from solar_backdating.estimators.changepoint import CohortPrior, estimate_changepoint
    from solar_backdating.estimators.emissions import FrameEmission
    from solar_backdating.estimators.seam import ClampContext, EstimatorConfig

    dates = [date(2019, 6, 1), date(2020, 6, 1), date(2024, 6, 1)]
    cutoff = date(2023, 1, 15)
    q = torch.tensor([0.8, 0.9, 0.99], dtype=torch.float64)
    probs = torch.tensor([[0.8, 0.2], [0.6, 0.4], [0.01, 0.99]], dtype=torch.float64)
    logits = torch.log(probs)
    prior = CohortPrior(
        year_log_mass=((2019, -2.0), (2020, -1.0), (2021, -1.5), (2022, -2.5), (2023, -3.0)),
        beyond_log_mass=-2.2,
    )
    got, bounds = r4.torch_phase0_posterior(q, logits, dates, prior, cutoff)
    frames = [
        FrameEmission(capture_date=d, q=float(qq), e0=float(p[0]), e1=float(p[1]), source_row=i)
        for i, (d, qq, p) in enumerate(zip(dates, q, probs))
    ]
    ref = estimate_changepoint(
        [], ClampContext(ceiling_date=cutoff),
        EstimatorConfig(decoder_epoch_gap_days=45, frame_emissions=frames, cohort_prior=prior),
    )
    np.testing.assert_allclose(got.detach().numpy(), ref.posterior, atol=1e-12)
    assert bounds == [(c.start_date, c.end_date) for c in ref.epochs]


def test_interval_cell_indices_and_left_boundary():
    bounds = [(None, date(2019, 1, 1)), (date(2019, 1, 1), date(2020, 1, 1)), (date(2020, 1, 1), None)]
    assert r4.interval_cell_indices(bounds, r4.TeacherBracket("left_censored", None, date(2018, 1, 1))) == [0]
    assert r4.interval_cell_indices(bounds, r4.TeacherBracket("interval", date(2019, 2, 1), date(2020, 1, 1))) == [1]


def test_interval_representability_audit_fails_same_epoch_teacher_bracket():
    rows = _anchor(
        "done_appears",
        ["absent", "present", "present"],
        ["2021-09-30", "2021-10-30", "2022-06-01"],
    )
    report = r4.audit_interval_representability(rows)
    assert report["pass"] is False
    assert report["empty_k_anchors"] == 1
    assert report["empty_k_by_status"] == {"done_appears": 1}


def test_interval_representability_audit_skips_frame_loss_only_shortgap():
    rows = _anchor(
        "done_appears",
        ["absent", "present", "present"],
        ["2021-09-30", "2021-10-30", "2022-06-01"],
    )
    rows["interval_loss_eligible"] = False
    report = r4.audit_interval_representability(rows)
    assert report["pass"] is True
    assert report["eligible_anchors"] == 0


def test_threshold_uses_lowest_qualifying_grid_point():
    anchors = pd.DataFrame(
        {
            "max_posterior": [0.8] * 400,
            "map_in_k": [True] * 400,
            "interval_eligible": [True] * 400,
            "source_area_m2": [20.0] * 200 + [60.0] * 200,
        }
    )
    selected, grid = r4.select_threshold(anchors)
    assert selected == 0.5
    assert grid[0]["qualifies"] is True


def test_hierarchical_platt_roundtrip_and_unknown_backoff():
    raw = np.linspace(-3, 3, 40)
    y = np.array([0] * 20 + [1] * 20)
    geometry = ["A24|small"] * 20 + ["A48|large"] * 20
    era = ["2019-2020", "2021-2022"] * 20
    quality = ["low"] * 10 + ["medium"] * 20 + ["high"] * 10
    cal = r4.fit_hierarchical_platt(raw, y, geometry, era, quality)
    p = r4.apply_hierarchical_platt(cal, raw, geometry, era, quality)
    assert np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all()
    unknown = r4.apply_hierarchical_platt(cal, [0.0], ["new"], ["new"], ["new"])
    expected = 1 / (1 + np.exp(-cal["intercept"]))
    assert unknown[0] == pytest.approx(expected)


def test_calibration_report_and_anchor_bootstrap_are_complete_and_deterministic():
    n = 240
    frames = pd.DataFrame({
        "anchor_id": [f"a{i:03d}" for i in range(n)],
        "capture_date": ["2020-01-01"] * 120 + ["2023-01-01"] * 120,
        "effective_label": (["uninformative", "absent", "present"] * 80),
        "raw_q": np.linspace(0.05, 0.95, n),
        "cal_q": np.linspace(0.10, 0.90, n),
        "raw_e1": np.linspace(0.05, 0.95, n),
        "cal_e1": np.linspace(0.10, 0.90, n),
        "chip_arm": ["A24"] * 120 + ["A48"] * 120,
        "area_bin": ["[15,40)"] * 120 + ["[40,100)"] * 120,
        "source_area_m2": [20.0] * 120 + [60.0] * 120,
        "era_bin": ["2019-2020"] * 120 + ["2023-2025"] * 120,
        "raw_quality_bin": ["low"] * 80 + ["medium"] * 80 + ["high"] * 80,
        "quality_flag": ["usable", "ambiguous", "unusable"] * 80,
    })
    report = r4.calibration_slice_report(frames, target="quality", train_prevalence=2 / 3)
    assert sum(row["count"] for row in report["overall"]["reliability_bins"]) == n
    assert set(report["slices"]) == {
        "chip_arm", "area_bin", "era_bin", "capture_year",
        "raw_q_quality_bin", "teacher_quality_flag", "under40_headline",
    }
    anchors = pd.DataFrame({
        "anchor_id": [f"a{i:03d}" for i in range(n)],
        "max_posterior": [0.9] * n,
        "map_in_k": [True] * 200 + [False] * 40,
        "interval_eligible": [True] * n,
        "source_area_m2": [20.0] * 120 + [60.0] * 120,
    })
    a = r4.bootstrap_anchor_rates(anchors, 0.5, n_resamples=50)
    b = r4.bootstrap_anchor_rates(anchors, 0.5, n_resamples=50)
    assert a == b
    assert a["intervals"]["coverage"]["point"] == 1.0


def test_smoke_writes_no_test_predictions(tmp_path):
    result = r4.smoke(tmp_path)
    assert result["status"] == "SMOKE_PASS"
    assert result["test_rows_read"] == 0
    assert result["threshold"] == 0.5
    assert (tmp_path / "metrics/R4_SMOKE.json").exists()
    assert (tmp_path / "seeds/2026072001/calibrators/quality.json").exists()
    assert (tmp_path / "seeds/2026072001/calibrators/state.json").exists()
    for role in ("cal_es", "cal_fit", "cal_select"):
        frames = pd.read_parquet(tmp_path / f"seeds/2026072001/predictions_{role}.parquet")
        anchors = pd.read_parquet(tmp_path / f"seeds/2026072001/anchors_{role}.parquet")
        assert r4.FRAME_PRED_COLUMNS <= set(frames.columns)
        assert r4.ANCHOR_PRED_COLUMNS <= set(anchors.columns)
    assert not list(tmp_path.rglob("*test*"))


def test_tiny_anchor_training_loop_selects_checkpoint(tmp_path):
    from solar_backdating.estimators.changepoint import CohortPrior

    rng = np.random.default_rng(11)

    def seq(anchor_id, role, offset):
        rows = pd.DataFrame(
                {
                    "anchor_id": [anchor_id] * 3,
                    "capture_date": ["2019-01-01", "2020-01-01", "2021-01-01"],
                    "chip_arm": ["A24"] * 3,
                    "area_bin": ["[15,40)"] * 3,
                    "source_area_m2": [25.0] * 3,
                    "src_tiff_sha256": [f"{anchor_id}-{i}" for i in range(3)],
                    "quality_flag": ["usable", "usable", "unusable"],
                    "label_v1": ["absent", "present", "uninformative"],
                    "scan_status": ["done_appears"] * 3,
                }
        )
        rows = r4.attach_quality_supervision(rows)
        return r4.AnchorSequence(
            anchor_id=anchor_id,
            split_role=role,
            features=rng.normal(offset, 1, (3, r4.FEATURE_DIM)).astype(np.float32),
            labels=["absent", "present", "uninformative"],
            capture_dates=[date(2019, 1, 1), date(2020, 1, 1), date(2021, 1, 1)],
            ceiling_date=None,
            bracket=r4.TeacherBracket("interval", date(2019, 1, 1), date(2020, 1, 1)),
            source_area_m2=25.0,
            frame_rows=rows,
        )

    sequences = [seq(f"tr{i}", "train", i / 10) for i in range(4)]
    sequences += [seq(f"es{i}", "cal_es", i / 10) for i in range(2)]
    sequences += [seq(f"fit{i}", "cal_fit", i / 10) for i in range(2)]
    sequences += [seq(f"sel{i}", "cal_select", i / 10) for i in range(2)]
    cohort_prior = CohortPrior(
        year_log_mass=((2019, -1.0), (2020, -1.0), (2021, -1.0)),
        beyond_log_mass=-1.0,
    )
    equivalence = r4.verify_manifest_decoder_equivalence(
        sequences, cohort_prior, tmp_path, sample_size=4,
    )
    assert equivalence["pass"] is True
    assert equivalence["max_abs_posterior_difference"] <= 1e-6
    assert (tmp_path / "locks/decoder_equivalence_200.json").exists()
    r4.atomic_json(tmp_path / "locks/RUN_LOCK.json", {"attempt_id": r4.ATTEMPT_ID})
    r4.atomic_json(tmp_path / r4.config_relpath(), r4.RESOLVED_CONFIG)
    labels = [label for anchor in sequences[:4] for label in anchor.labels]
    q_targets, q_eligible = r4._flatten_quality_supervision(sequences[:4])
    result = r4.train_one_seed(
        sequences,
        cohort_prior,
        r4.class_weights(labels, q_targets, q_eligible),
        r4.SEEDS[0],
        tmp_path,
        device="cpu",
        max_epochs=2,
        min_epochs=1,
        patience=1,
    )
    assert result["epoch"] in {1, 2}
    assert result["checkpoint_sha256"]
    assert (tmp_path / f"seeds/{r4.SEEDS[0]}/train.jsonl").exists()
    assert (tmp_path / f"seeds/{r4.SEEDS[0]}/checkpoints/selected.json").exists()
    first_log = json.loads((tmp_path / f"seeds/{r4.SEEDS[0]}/train.jsonl").read_text().splitlines()[0])
    assert {"train_total", "train_interval", "train_frame", "train_quality", "train_state", "wall_time_seconds"} <= set(first_log)
    calibrated = r4.calibrate_one_seed(
        sequences,
        cohort_prior,
        r4.SEEDS[0],
        result,
        tmp_path,
        device="cpu",
    )
    assert "quality" in calibrated and "state" in calibrated
    assert (tmp_path / f"seeds/{r4.SEEDS[0]}/predictions_cal_select.parquet").exists()


def test_health_and_run_lock_finalize_required_artifacts(tmp_path):
    r4.atomic_json(tmp_path / "locks/RUN_LOCK.json", {
        "git_dirty": False,
        "leakage_counts": {"x": 0},
        "test_access_guard": True,
        "manifest_decoder_equivalence": {"pass": True},
    })
    r4.atomic_json(tmp_path / "metrics/seed_summary.json", {"median_ranked_seed": r4.SEEDS[1]})
    results = []
    for seed in r4.SEEDS:
        results.append({
            "training": {
                "seed": seed, "cal_es_interval_nll": 1.0,
                "cal_es_frame_loss": 1.0, "interval_improvement_fraction": 0.02,
            },
            "calibration": {"calibration_failed": False, "selected_threshold": 0.5},
        })
    for path in r4._required_r4_artifacts(tmp_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("fixture\n")
    health = r4.evaluate_r4_health(tmp_path, results)
    assert health["verdict"] == "READY_FOR_R5"
    r4.atomic_json(tmp_path / "metrics/R4_HEALTH.json", health)
    lock = r4.finalize_run_lock(tmp_path)
    assert lock["status"] == "R4_COMPLETE"
    assert "metrics/R4_HEALTH.json" in lock["output_hashes"]
    assert r4.write_artifact_manifest(tmp_path) > 0
    assert (tmp_path / "artifacts.sha256").exists()


def test_quality_supervision_maps_flag_not_effective_label():
    rows = pd.DataFrame({
        "label_v1": ["present", "absent", "uninformative", "present"],
        "quality_flag": ["usable", "ambiguous", "unusable", "usable"],
        "scan_status": ["done_appears"] * 4,
        "a1_override": [False, False, False, False],
    })
    rows["label_v1_r0"] = rows["label_v1"]
    out = r4.attach_quality_supervision(rows)
    assert out["quality_supervision_eligible"].tolist() == [True, True, True, True]
    assert out["quality_target"].tolist() == [1.0, 0.0, 0.0, 1.0]
    assert out["quality_target_reason"].tolist() == [
        "usable_verdict", "quality_negative", "quality_negative", "usable_verdict",
    ]


def test_quality_supervision_excludes_a1_and_ambiguous_status():
    rows = pd.DataFrame({
        "label_v1": ["uninformative", "absent", "present"],
        "label_v1_r0": ["absent", "absent", "present"],
        "quality_flag": ["usable", "unusable", "usable"],
        "scan_status": [
            "done_appears",
            "done_appears",
            "done_ambiguous_nonmonotonic",
        ],
        "a1_override": [True, False, False],
    })
    out = r4.attach_quality_supervision(rows)
    assert out["quality_supervision_eligible"].tolist() == [False, True, False]
    assert np.isnan(out["quality_target"].iloc[0])
    assert out["quality_target"].iloc[1] == 0.0
    assert np.isnan(out["quality_target"].iloc[2])
    assert out["quality_target_reason"].tolist() == [
        "a1_empty_k_patch", "quality_negative", "d12iii_ambiguous_anchor",
    ]


def test_anchor_loss_skips_ineligible_quality_frames():
    import torch

    q_logits = torch.tensor([-8.0, -8.0, 8.0], dtype=torch.float64)
    state_logits = torch.zeros((3, 2), dtype=torch.float64)
    labels = ["absent", "absent", "present"]
    dates = [date(2019, 1, 1), date(2020, 1, 1), date(2021, 1, 1)]
    weights = {"quality_0": 1.0, "quality_1": 1.0, "absent": 1.0, "present": 1.0}
    _, _, q_all, _, _, _ = r4.anchor_loss(
        q_logits, state_logits, labels, dates, None, weights,
        quality_targets=[1.0, 0.0, 1.0],
        quality_eligible=[True, True, True],
    )
    _, _, q_masked, _, _, _ = r4.anchor_loss(
        q_logits, state_logits, labels, dates, None, weights,
        quality_targets=[1.0, 0.0, 1.0],
        quality_eligible=[False, True, False],
    )
    assert float(q_masked) < 0.01
    assert float(q_all) > 2.0


def test_class_weights_use_quality_targets_when_provided():
    labels = ["absent", "present", "uninformative", "absent"]
    v1 = r4.class_weights(labels)
    h1 = r4.class_weights(
        labels,
        quality_targets=[1.0, 0.0, 0.0, 1.0],
        quality_eligible=[True, True, False, True],
    )
    assert v1["quality_0"] != h1["quality_0"]
    assert h1["absent"] == v1["absent"]


def test_calibration_quality_uses_reconstructed_target():
    n = 240
    frames = pd.DataFrame({
        "anchor_id": [f"a{i:03d}" for i in range(n)],
        "capture_date": ["2020-01-01"] * 120 + ["2023-01-01"] * 120,
        "effective_label": (["uninformative", "absent", "present"] * 80),
        "raw_q": np.linspace(0.05, 0.95, n),
        "cal_q": np.linspace(0.10, 0.90, n),
        "raw_e1": np.linspace(0.05, 0.95, n),
        "cal_e1": np.linspace(0.10, 0.90, n),
        "chip_arm": ["A24"] * 120 + ["A48"] * 120,
        "area_bin": ["[15,40)"] * 120 + ["[40,100)"] * 120,
        "source_area_m2": [20.0] * 120 + [60.0] * 120,
        "era_bin": ["2019-2020"] * 120 + ["2023-2025"] * 120,
        "raw_quality_bin": ["low"] * 80 + ["medium"] * 80 + ["high"] * 80,
        "quality_flag": ["usable", "ambiguous", "unusable"] * 80,
        "quality_supervision_eligible": [True] * 200 + [False] * 40,
        "quality_target": [1.0] * 160 + [0.0] * 40 + [np.nan] * 40,
    })
    report = r4.calibration_slice_report(frames, target="quality", train_prevalence=0.8)
    assert report["overall"]["rows"] == 200
    assert report["overall"]["negatives"] == 40
    assert report["overall"]["positives"] == 160
