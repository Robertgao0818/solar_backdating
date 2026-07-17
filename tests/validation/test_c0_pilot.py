"""Unit tests for the Path C0 pilot (ISSUE-09) — decode math, anchor rule, gate rules.

Prereg: docs/dinov3_scorer/DATA-c0-reverse-template-prereg-2026-07-12.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.temporal.chip_geometry import resolve_chip_geometry
from scripts.validation.check_student_path_gate import check_c0_r1, check_c0_s0
from scripts.validation.pilot_c0_reverse_template_2026_07_12 import (
    BASEMAP_TIF_RE,
    C0Config,
    TfwAffine,
    _labels_from_csv,
    _project_polygon_to_grid,
    bayes_single_changepoint,
    emit_label_template,
    enumerate_basemap_stack,
    glr_changepoint,
    isotonic_step,
    parse_tfw,
    persistence_ok,
    pick_reference_basemap,
    rank_auc,
    run_smoke,
    sample_label_template_targets,
    select_anchor,
    shift_mask,
)


# ---------------------------------------------------------------- decode math

DATES = np.array([0, 200, 400, 600, 800, 1000])  # days, irregular ok


def test_bayes_finds_clean_step():
    s = np.array([0.02, -0.01, 0.00, 0.55, 0.60, 0.58])
    out = bayes_single_changepoint(s, DATES)
    assert out["map_gap"] == 2
    assert out["q"] > 0.8
    assert out["p_nochange"] < 0.1


def test_bayes_flat_series_prefers_nochange():
    s = np.array([0.30, 0.31, 0.29, 0.30, 0.32, 0.30])
    out = bayes_single_changepoint(s, DATES)
    assert out["p_nochange"] > 0.5


def test_bayes_short_series_abstains():
    out = bayes_single_changepoint(np.array([0.1, 0.9]), DATES[:2])
    assert out["map_gap"] is None
    assert out["p_nochange"] == 1.0


def test_bayes_weights_downweight_outlier():
    # one cloudy spike pre-step; with low weight it must not steal the MAP gap
    s = np.array([0.00, 0.70, 0.02, 0.60, 0.62, 0.61])
    w = np.array([1.0, 0.05, 1.0, 1.0, 1.0, 1.0])
    out = bayes_single_changepoint(s, DATES, w)
    assert out["map_gap"] == 2


def test_glr_and_isotonic_agree_on_step():
    s = np.array([0.0, 0.01, -0.02, 0.5, 0.55, 0.52])
    assert glr_changepoint(s) == 2
    assert isotonic_step(s) == 2


def test_isotonic_rejects_downward_step():
    s = np.array([0.5, 0.55, 0.52, 0.0, 0.01, -0.02])
    assert isotonic_step(s) is None


def test_persistence_rule():
    s = np.array([0.0, 0.0, 0.6, 0.65, 0.62])
    usable = np.ones(5, dtype=bool)
    assert persistence_ok(s, usable, 1, 2)
    # only one usable post-step frame -> not persistent
    usable2 = np.array([True, True, True, False, False])
    assert not persistence_ok(s, usable2, 1, 2)


# ------------------------------------------------------------- anchor rule

T_C = 10_000


def test_anchor_right_nearest_wins():
    dates = [T_C - 900, T_C - 100, T_C + 50, T_C + 400]
    sel = select_anchor(dates, [True] * 4, T_C, delta_left_days=365)
    assert sel["anchor_idx"] == 2 and sel["anchor_side"] == "right"


def test_anchor_left_nearest_wins_within_window():
    dates = [T_C - 900, T_C - 30, T_C + 200]
    sel = select_anchor(dates, [True] * 3, T_C, delta_left_days=365)
    assert sel["anchor_idx"] == 1 and sel["anchor_side"] == "left"


def test_anchor_left_too_far_falls_to_right():
    dates = [T_C - 900, T_C + 200]
    sel = select_anchor(dates, [True] * 2, T_C, delta_left_days=365)
    assert sel["anchor_idx"] == 1 and sel["anchor_side"] == "right"


def test_anchor_consistency_gate_rejects_left():
    dates = [T_C - 30, T_C + 200]
    sel = select_anchor(dates, [True] * 2, T_C, delta_left_days=365,
                        left_right_cos=0.10, theta_anchor=0.5)
    assert sel["anchor_side"] == "right"
    assert sel["anchor_left_rejected"] is True


def test_anchor_left_only_flagged():
    dates = [T_C - 400, T_C - 30]
    sel = select_anchor(dates, [True] * 2, T_C, delta_left_days=365)
    assert sel["anchor_side"] == "left_only"


def test_anchor_none_when_nothing_eligible():
    dates = [T_C - 900, T_C - 800]
    sel = select_anchor(dates, [True] * 2, T_C, delta_left_days=365)
    assert sel["anchor_idx"] is None and sel["anchor_side"] == "none"


# -------------------------------------------------------------- small utils

def test_rank_auc_perfect_and_tied():
    assert rank_auc([1.0, 2.0], [0.0, 0.5]) == 1.0
    assert rank_auc([1.0], [1.0]) == 0.5
    assert np.isnan(rank_auc([], [1.0]))


def test_shift_mask_no_wrap():
    m = np.zeros((5, 5), dtype=bool)
    m[0, 0] = True
    out = shift_mask(m, -1, -1)  # would wrap; must vanish instead
    assert not out.any()
    out2 = shift_mask(m, 2, 3)
    assert out2[2, 3] and out2.sum() == 1


def test_config_hash_changes_with_knobs():
    assert C0Config().hash() != C0Config(shift_radius_patches=4).hash()


# ---------------------------------------------------------------- gate rules

C0_S0_GO = {"c0_smoke_auc": 0.81, "c0_smoke_n_anchors": 72}

# Amendment 2026-07-16: rule 2 is a Jeffreys posterior gate on the integer win
# count, min n raised to 150. 72/160 = 0.45 -> P(p > 1/3) ≈ 0.999.
C0_R1_GO = {
    "c0_replicate_equiv_delta_pp": -1.2,
    "c0_replicate_equiv_delta_ci_low_pp": -4.0,
    "c0_adjudication_correct_share": 0.45,
    "c0_adjudication_correct_n": 72,
    "c0_adjudication_n": 160,
    "c0_safety_polarity_rate": 0.0,
    "c0_safety_all_inspected": 1,
}


def test_c0_s0_go_when_all_criteria_met():
    ok, lines = check_c0_s0(C0_S0_GO)
    assert ok and any("GO" in ln for ln in lines)


@pytest.mark.parametrize("key,bad", [
    ("c0_smoke_auc", 0.70),
    ("c0_smoke_n_anchors", 30),
])
def test_c0_s0_kill_on_violation(key, bad):
    ok, _ = check_c0_s0(dict(C0_S0_GO, **{key: bad}))
    assert not ok


def test_c0_s0_fails_closed_on_missing_keys():
    m = dict(C0_S0_GO)
    del m["c0_smoke_auc"]
    ok, lines = check_c0_s0(m)
    assert not ok
    assert any("FAIL-CLOSED" in ln and "c0_smoke_auc" in ln for ln in lines)


def test_c0_r1_go_when_all_criteria_met():
    ok, lines = check_c0_r1(C0_R1_GO)
    assert ok and any("GO" in ln for ln in lines)


@pytest.mark.parametrize("key,bad", [
    ("c0_replicate_equiv_delta_ci_low_pp", -6.5),
    ("c0_safety_polarity_rate", 0.02),
    ("c0_safety_all_inspected", 0),
])
def test_c0_r1_kill_on_violation(key, bad):
    ok, _ = check_c0_r1(dict(C0_R1_GO, **{key: bad}))
    assert not ok


def test_c0_r1_kill_when_posterior_below_gate():
    # 54/160 = 0.3375 clears the OLD point-estimate bar (>1/3) but the
    # Jeffreys posterior P(p > 1/3) ≈ 0.55 fails the 0.95 gate.
    m = dict(
        C0_R1_GO,
        c0_adjudication_correct_n=54,
        c0_adjudication_correct_share=0.3375,
    )
    ok, lines = check_c0_r1(m)
    assert not ok
    assert any("posterior" in ln and "FAIL" in ln for ln in lines)


def test_c0_r1_kill_when_n_below_150_even_with_strong_share():
    # 20/40 = 0.50 has posterior 0.986 but the amended min sample is 150.
    m = dict(
        C0_R1_GO,
        c0_adjudication_correct_n=20,
        c0_adjudication_correct_share=0.50,
        c0_adjudication_n=40,
    )
    ok, _ = check_c0_r1(m)
    assert not ok


def test_c0_r1_fails_closed_on_share_count_mismatch():
    m = dict(C0_R1_GO, c0_adjudication_correct_share=0.50)  # k/n is 0.45
    ok, lines = check_c0_r1(m)
    assert not ok
    assert any("inconsistent" in ln for ln in lines)


def test_c0_r1_fails_closed_on_invalid_counts():
    m = dict(C0_R1_GO, c0_adjudication_correct_n=200)  # k > n
    ok, lines = check_c0_r1(m)
    assert not ok
    assert any("invalid adjudication counts" in ln for ln in lines)


@pytest.mark.parametrize("key", ["c0_adjudication_n", "c0_adjudication_correct_n"])
def test_c0_r1_fails_closed_on_missing_keys(key):
    m = dict(C0_R1_GO)
    del m[key]
    ok, lines = check_c0_r1(m)
    assert not ok
    assert any("FAIL-CLOSED" in ln and key in ln for ln in lines)


# ------------------------------------------- basemap96 stack adapter (2026-07-13)

def _write_tfw(path: Path, a=1.0, d=0.0, b=0.0, e=-1.0, c=0.0, f=0.0) -> None:
    path.write_text(f"{a}\n{d}\n{b}\n{e}\n{c}\n{f}\n")


def _write_tif(path: Path, size=(4, 4)) -> None:
    from PIL import Image

    Image.new("RGB", size, color=(10, 20, 30)).save(path, format="TIFF")


def test_basemap_tif_re_parses_target_capture_vintage():
    m = BASEMAP_TIF_RE.match(
        "jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00000001_20230123_v20230613.tif")
    assert m is not None
    assert m.group("target") == "jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00000001"
    assert m.group("capture") == "20230123"
    assert m.group("vintage") == "20230613"


def test_parse_tfw_roundtrip_and_known_origin(tmp_path):
    tfw_path = tmp_path / "frame.tfw"
    _write_tfw(tfw_path, a=0.29858214173896974, d=0, b=0, e=-0.29858214173896974,
               c=3126979.4819457387, f=-3013698.0094010425)
    tfw = parse_tfw(tfw_path)
    # pixel (0, 0) center IS the world-file origin (upper-left pixel center).
    x, y = tfw.to_map(0, 0)
    assert x == pytest.approx(3126979.4819457387)
    assert y == pytest.approx(-3013698.0094010425)
    # to_pixel is the exact inverse of to_map over a grid of sample points.
    for col, row in [(0, 0), (10.5, 3.2), (100, 200)]:
        x, y = tfw.to_map(col, row)
        col2, row2 = tfw.to_pixel(x, y)
        assert col2 == pytest.approx(col)
        assert row2 == pytest.approx(row)


def test_parse_tfw_rejects_malformed_file(tmp_path):
    bad = tmp_path / "bad.tfw"
    bad.write_text("only\nthree\nlines\n")
    with pytest.raises(ValueError):
        parse_tfw(bad)


def test_enumerate_basemap_stack_parses_good_frames(tmp_path):
    target_dir = tmp_path / "target_t01"
    z19 = target_dir / "z19"
    z19.mkdir(parents=True)
    for capture, vintage in [("20230123", "20230613"), ("20231124", "20241212")]:
        stem = f"target_t01_{capture}_v{vintage}"
        _write_tif(z19 / f"{stem}.tif")
        _write_tfw(z19 / f"{stem}.tfw")

    good, skipped = enumerate_basemap_stack(target_dir)
    assert skipped == []
    assert len(good) == 2
    # sorted ascending by capture_ymd
    assert [e["capture_ymd"] for e in good] == [20230123, 20231124]
    assert good[0]["vintage_ymd"] == 20230613
    assert good[0]["zoom"] == 19
    assert good[0]["img_w"] == 4 and good[0]["img_h"] == 4
    assert isinstance(good[0]["tfw"], TfwAffine)


def test_enumerate_basemap_stack_skips_missing_tfw(tmp_path):
    target_dir = tmp_path / "target_t02"
    z19 = target_dir / "z19"
    z19.mkdir(parents=True)
    stem = "target_t02_20230123_v20230613"
    _write_tif(z19 / f"{stem}.tif")
    # no .tfw written

    good, skipped = enumerate_basemap_stack(target_dir)
    assert good == []
    assert len(skipped) == 1
    assert skipped[0]["skip_reason"] == "missing_tfw"


def test_enumerate_basemap_stack_skips_unreadable_tif(tmp_path):
    """Simulates a partial/mid-write download: the .tif exists but is garbage."""
    target_dir = tmp_path / "target_t03"
    z19 = target_dir / "z19"
    z19.mkdir(parents=True)
    stem = "target_t03_20230123_v20230613"
    (z19 / f"{stem}.tif").write_bytes(b"not a tiff, partial download")
    _write_tfw(z19 / f"{stem}.tfw")

    good, skipped = enumerate_basemap_stack(target_dir)
    assert good == []
    assert len(skipped) == 1
    assert skipped[0]["skip_reason"].startswith("unreadable_tif:")


def test_enumerate_basemap_stack_skips_name_mismatch(tmp_path):
    target_dir = tmp_path / "target_t04"
    z19 = target_dir / "z19"
    z19.mkdir(parents=True)
    _write_tif(z19 / "not_a_gehi_filename.tif")

    good, skipped = enumerate_basemap_stack(target_dir)
    assert good == []
    assert skipped[0]["skip_reason"] == "name_no_match"


def test_pick_reference_basemap_latest_at_highest_zoom():
    stack = [
        {"path": Path("a"), "capture_ymd": 20230101, "zoom": 19},
        {"path": Path("b"), "capture_ymd": 20230601, "zoom": 19},
        {"path": Path("c"), "capture_ymd": 20240101, "zoom": 18},  # lower zoom, later date
    ]
    ref = pick_reference_basemap(stack)
    assert ref["path"] == Path("b")


def test_pick_reference_basemap_empty_stack_returns_none():
    assert pick_reference_basemap([]) is None


def test_project_polygon_to_grid_known_polygon():
    """Identity-ish TFW (1 map-unit/px, north-up), square image == input_size
    (no resize stretch), so patch (gy, gx) center lands exactly at pixel
    (gx+0.5, gy+0.5) and map (gx+0.5, -(gy+0.5))."""
    from shapely.geometry import box

    tfw = TfwAffine(a=1.0, d=0.0, b=0.0, e=-1.0, c=0.0, f=0.0)
    grid_side, img_w, img_h, input_size = 4, 4, 4, 4
    # Covers map x in [0, 2), map y in (-2, 0] -> patch centers (0.5,-0.5) and
    # (1.5,-0.5) and (0.5,-1.5) and (1.5,-1.5) i.e. the top-left 2x2 patches.
    poly = box(0.0, -2.0, 2.0, 0.0)
    mask = _project_polygon_to_grid(poly, tfw, grid_side, img_w, img_h, input_size)
    expected = np.zeros((4, 4), dtype=bool)
    expected[0:2, 0:2] = True
    assert np.array_equal(mask, expected)


def test_project_polygon_to_grid_accounts_for_nonsquare_resize_stretch():
    """A non-square source image (img_w != img_h) gets forced-square-resized
    by the scorer; the same map-space polygon must select a DIFFERENT patch
    footprint once img_w/img_h differ, proving the sx/sy stretch is applied."""
    from shapely.geometry import box

    tfw = TfwAffine(a=1.0, d=0.0, b=0.0, e=-1.0, c=0.0, f=0.0)
    # Covers the left half of the *original* 8-px-wide image (x in [0, 4)),
    # a fixed map-space window independent of img_w_px.
    poly = box(0.0, -8.0, 4.0, 0.0)
    grid_side, input_size = 4, 4
    mask_square = _project_polygon_to_grid(poly, tfw, grid_side, 4, 4, input_size)
    mask_wide = _project_polygon_to_grid(poly, tfw, grid_side, 8, 4, input_size)
    assert not np.array_equal(mask_square, mask_wide)
    # img_w_px=4 (no stretch): patch centers at x=0.5,1.5,2.5,3.5 -> all 4 cols hit.
    assert mask_square.all()
    # img_w_px=8 (2x stretch): patch centers at x=1,3,5,7 -> only 2 cols hit.
    assert mask_wide[:, :2].all() and not mask_wide[:, 2:].any()


def test_geometry_version_basemap96_hashes_differently_from_tight12():
    """Config-hash provenance separation (2026-07-13): a basemap96 curve run
    must never be mistaken for a tight12 one, so their artifact tags differ."""
    tight12 = C0Config(geometry_version="chip_geom_v2_tight12")
    basemap96 = C0Config(geometry_version="basemap96_z19_v1")
    assert tight12.hash() != basemap96.hash()
    # both resolve in the chip-geometry registry (fails loudly otherwise)
    resolve_chip_geometry("chip_geom_v2_tight12")
    resolve_chip_geometry("basemap96_z19_v1")


# --------------------------------------- smoke label source (amendment 2026-07-13)

def test_labels_from_csv_parses_and_excludes_unsure(tmp_path):
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text(
        "target_id,frame_date,label\n"
        "t1,2023-01-01,absent\n"
        "t1,2023-06-01,present\n"
        "t1,2023-09-01,unsure\n"
        "t2,2023-01-01,present\n"
    )
    labels = _labels_from_csv(csv_path)
    assert labels["t1"] == {20230101: False, 20230601: True}
    assert labels["t2"] == {20230101: True}


def test_labels_from_csv_bad_label_raises(tmp_path):
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text("target_id,frame_date,label\nt1,2023-01-01,maybe\n")
    with pytest.raises(SystemExit):
        _labels_from_csv(csv_path)


def test_labels_from_csv_missing_columns_raises(tmp_path):
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text("target_id,label\nt1,present\n")
    with pytest.raises(SystemExit):
        _labels_from_csv(csv_path)


def test_labels_from_csv_missing_file_raises(tmp_path):
    with pytest.raises(SystemExit):
        _labels_from_csv(tmp_path / "does_not_exist.csv")


def _write_pilot2023_candidates(path: Path, anchor_ids: list[str], dates_per_anchor: int = 2) -> None:
    rows = []
    for aid in anchor_ids:
        for i in range(dates_per_anchor):
            rows.append({"anchor_id": aid, "capture_date": f"2023-0{i + 1}-01"})
    pd.DataFrame(rows).to_csv(path, index=False)


def test_sample_label_template_targets_is_deterministic(tmp_path):
    candidates = tmp_path / "pilot2023.csv"
    anchor_ids = [f"t{i:03d}" for i in range(50)]
    _write_pilot2023_candidates(candidates, anchor_ids)

    sample_a = sample_label_template_targets(candidates, n=10, seed=20_260_713)
    sample_b = sample_label_template_targets(candidates, n=10, seed=20_260_713)
    assert sample_a == sample_b
    assert len(sample_a) == 10
    assert set(sample_a) <= set(anchor_ids)

    sample_c = sample_label_template_targets(candidates, n=10, seed=1)
    assert sample_c != sample_a  # different seed -> (overwhelmingly likely) different draw


def test_sample_label_template_targets_caps_at_pool_size(tmp_path):
    candidates = tmp_path / "pilot2023.csv"
    _write_pilot2023_candidates(candidates, ["t1", "t2", "t3"])
    sample = sample_label_template_targets(candidates, n=100, seed=1)
    assert sorted(sample) == ["t1", "t2", "t3"]


def test_emit_label_template_writes_rows_and_reserved_list(tmp_path):
    candidates = tmp_path / "pilot2023.csv"
    anchor_ids = [f"t{i:03d}" for i in range(20)]
    _write_pilot2023_candidates(candidates, anchor_ids, dates_per_anchor=3)
    out_csv = tmp_path / "template.csv"
    reserved_csv = tmp_path / "reserved.csv"

    emit_label_template(candidates, out_csv, n=5, seed=42, reserved_out=reserved_csv)

    template = pd.read_csv(out_csv)
    assert list(template.columns) == ["target_id", "frame_date", "label"]
    assert template["label"].isna().all() or (template["label"] == "").all()
    sampled_targets = set(template["target_id"])
    assert len(sampled_targets) == 5
    # 3 distinct capture dates per target in the fixture -> 3 template rows each
    assert len(template) == 15

    reserved = pd.read_csv(reserved_csv)
    assert set(reserved["target_id"]) == sampled_targets


# --------------------------------------------- run_smoke: labels-csv path (2026-07-13)

def _smoke_args(out: Path, *, labels_csv: str | None, scan_states: str | None) -> argparse.Namespace:
    return argparse.Namespace(
        out=str(out), arm="dinov2_floor", labels_csv=labels_csv, scan_states=scan_states,
        min_confidence=0.7)


def test_run_smoke_with_labels_csv_end_to_end(tmp_path):
    cfg = C0Config()
    tag = f"dinov2_floor_{cfg.facet}_{cfg.hash()}"

    # 4 synthetic anchors, each perfectly separated (absent < 0 < present) so
    # the pooled AUC is exactly 1.0 -- a deterministic, easy-to-check target.
    curve_rows = []
    label_lines = ["target_id,frame_date,label"]
    for i in range(4):
        anchor_id = f"anchor_{i}"
        frames = [
            (f"2023-0{k + 1}-01", -1.0 - 0.1 * k, "absent") for k in range(2)
        ] + [
            (f"2023-0{k + 7}-01", 1.0 + 0.1 * k, "present") for k in range(1)
        ]
        for frame_date, s_val, label in frames:
            capture_date = int(frame_date.replace("-", ""))
            curve_rows.append({
                "anchor_id": anchor_id, "capture_date": capture_date,
                "s": s_val, "s_noshift": s_val, "cos_fp": 0.5, "cos_ring": 0.1,
                "shift_dy": 0, "shift_dx": 0, "reg_suspect": False,
                "usable": True, "weight": 1.0, "is_anchor": False,
            })
            label_lines.append(f"{anchor_id},{frame_date},{label}")

    pd.DataFrame(curve_rows).to_csv(tmp_path / f"curves_{tag}.csv", index=False)
    pd.DataFrame([{"anchor_id": f"anchor_{i}", "source_area_m2": 10.0} for i in range(4)]
                ).to_csv(tmp_path / f"curve_anchors_{tag}.csv", index=False)
    labels_csv = tmp_path / "labels.csv"
    labels_csv.write_text("\n".join(label_lines) + "\n")

    args = _smoke_args(tmp_path, labels_csv=str(labels_csv), scan_states=None)
    run_smoke(args, cfg)

    metrics = json.loads((tmp_path / f"smoke_metrics_{tag}.json").read_text())
    assert metrics["c0_smoke_label_source"] == "manual_csv_2026_07_13"
    assert metrics["c0_smoke_n_anchors"] == 4
    assert metrics["c0_smoke_auc"] == pytest.approx(1.0)


def test_run_smoke_missing_labels_csv_fails_closed(tmp_path):
    cfg = C0Config()
    tag = f"dinov2_floor_{cfg.facet}_{cfg.hash()}"
    pd.DataFrame(columns=["anchor_id", "capture_date", "s", "s_noshift", "usable"]
                ).to_csv(tmp_path / f"curves_{tag}.csv", index=False)
    pd.DataFrame(columns=["anchor_id", "source_area_m2"]
                ).to_csv(tmp_path / f"curve_anchors_{tag}.csv", index=False)
    args = _smoke_args(tmp_path, labels_csv=str(tmp_path / "nope.csv"), scan_states=None)
    with pytest.raises(SystemExit):
        run_smoke(args, cfg)
