"""Unit tests for the Run3-native R0 manifest generator.

All tests use small synthetic fixtures written to a tmp dir -- no dependency on
the real ~/zasolar_data drive. They cover the load-bearing rules: three-state
label mapping (unusable -> uninformative), the three dedup rules, connected-
component split isolation (same legacy group / overlapping chips never split
across sets), TFW reconstruction, and non-zero exit on reconciliation failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.temporal import build_run3_native_manifest as m


# --------------------------------------------------------------------------
# Label mapping (PRD §3.3).
# --------------------------------------------------------------------------
def test_label_unusable_maps_to_uninformative():
    # unusable overrides a positive/negative verdict.
    assert m.classify_label(True, "unusable", "gemini_batch") == "uninformative"
    assert m.classify_label(False, "unusable", "gemini_batch") == "uninformative"


def test_label_present_absent_and_failed():
    assert m.classify_label(True, "usable", "gemini_batch") == "present"
    assert m.classify_label(False, "usable", "gemini_batch") == "absent"
    assert m.classify_label(True, "ambiguous", "gemini_batch") == "present"
    # failed scoring or a missing verdict -> uninformative
    assert m.classify_label(None, "usable", "gemini_failed") == "uninformative"
    assert m.classify_label(None, "usable", "gemini_batch") == "uninformative"


# --------------------------------------------------------------------------
# TFW reconstruction.
# --------------------------------------------------------------------------
def test_tfw_six_params_corner_convention():
    a, d, b, e, c, f = m.tfw_six_params(100.0, 200.0, 110.0, 210.0, 10, 10)
    assert a == pytest.approx(1.0)
    assert e == pytest.approx(-1.0)
    assert d == 0.0 and b == 0.0
    assert c == pytest.approx(100.0)  # minx, corner
    assert f == pytest.approx(210.0)  # maxy, corner


# --------------------------------------------------------------------------
# Leakage-component isolation.
# --------------------------------------------------------------------------
def _anchor(aid, group, grid, box, source_grids=None):
    x0, y0, x1, y1 = box
    return {
        "anchor_id": aid,
        "legacy_group_anchor_id": group,
        "grid_id": grid,
        "source_grids": source_grids or grid,
        "chip_lon_min": x0,
        "chip_lat_min": y0,
        "chip_lon_max": x1,
        "chip_lat_max": y1,
    }


def test_shared_legacy_group_same_component():
    rows = [
        _anchor("a1", "G", "grid1", (0, 0, 1, 1)),
        _anchor("a2", "G", "grid9", (50, 50, 51, 51)),  # far away, same group
    ]
    comp = m.build_leakage_components(rows, "chip_overlap")
    assert comp["a1"] == comp["a2"]


def test_overlapping_chips_same_component():
    rows = [
        _anchor("a1", "G1", "grid1", (0.0, 0.0, 1.0, 1.0)),
        _anchor("a2", "G2", "grid1", (0.5, 0.5, 1.5, 1.5)),  # overlaps a1
        _anchor("a3", "G3", "grid1", (10.0, 10.0, 11.0, 11.0)),  # disjoint
    ]
    comp = m.build_leakage_components(rows, "chip_overlap")
    assert comp["a1"] == comp["a2"]
    assert comp["a3"] != comp["a1"]


def test_split_never_tears_a_component():
    # Two anchors in one group must land in the same split whatever the ratios.
    rows = [_anchor(f"a{i}", "G" if i < 2 else f"G{i}", "grid1",
                    (10 * i, 10 * i, 10 * i + 1, 10 * i + 1)) for i in range(20)]
    comp = m.build_leakage_components(rows, "chip_overlap")
    assign, counts = m.assign_splits(comp, 0.7, 0.15, seed=1)
    assert assign["a0"] == assign["a1"]  # same legacy group G
    # every component is atomic
    from collections import defaultdict
    comp_splits = defaultdict(set)
    for aid, cid in comp.items():
        comp_splits[cid].add(assign[aid])
    assert all(len(s) == 1 for s in comp_splits.values())
    assert sum(counts.values()) == len(rows)


def test_stratified_greedy_beats_anchor_only_on_stratum_balance():
    # Adversarial-for-anchor-only layout: stratum A arrives only in size-5
    # components, stratum B only in singletons. The size-first anchor-only greedy
    # front-loads the big A components into the two largest-deficit splits,
    # leaving one split with zero A (max dev 0.5); the stratum-aware greedy
    # spreads the four A components across all splits (max dev ~0).
    comp_of = {}
    anchor_obs = {}
    anchor_stratum = {}
    for i in range(4):  # 4 stratum-A components of 5 anchors each
        for j in range(5):
            aid = f"A{i}_{j}"
            comp_of[aid] = f"cA{i}"
            anchor_obs[aid] = 1
            anchor_stratum[aid] = "A"
    for i in range(20):  # 20 stratum-B singleton components
        aid = f"B{i}"
        comp_of[aid] = f"cB{i}"
        anchor_obs[aid] = 1
        anchor_stratum[aid] = "B"

    a_only, _ = m.assign_splits(comp_of, 0.5, 0.25, seed=7)
    a_strat, _ = m.assign_splits_stratified(
        comp_of, anchor_obs, anchor_stratum, 0.5, 0.25, seed=7
    )
    _, _, dev_only = m.stratum_shares(a_only, anchor_obs, anchor_stratum)
    _, _, dev_strat = m.stratum_shares(a_strat, anchor_obs, anchor_stratum)
    assert dev_strat < dev_only
    assert dev_strat < 0.25  # stratum-aware keeps shares close to corpus
    # atomicity preserved
    from collections import defaultdict
    comp_splits = defaultdict(set)
    for aid, cid in comp_of.items():
        comp_splits[cid].add(a_strat[aid])
    assert all(len(s) == 1 for s in comp_splits.values())


def test_split_is_deterministic():
    rows = [_anchor(f"a{i}", f"G{i}", "grid1", (10 * i, 0, 10 * i + 1, 1))
            for i in range(30)]
    comp = m.build_leakage_components(rows, "chip_overlap")
    a1, _ = m.assign_splits(comp, 0.7, 0.15, seed=42)
    a2, _ = m.assign_splits(comp, 0.7, 0.15, seed=42)
    assert a1 == a2


def test_buffer_mode_is_reserved_not_implemented():
    rows = [_anchor("a1", "G1", "grid1", (0, 0, 1, 1))]
    with pytest.raises(NotImplementedError):
        m.build_leakage_components(rows, "buffer")


def test_cross_split_nn_distances_basic():
    # two splits, one pair 100m apart in x -> p50 ~ 100m
    anchor_split = {"a1": "train", "a2": "test"}
    # ~0.001 deg lon at Joburg ~ 100m; use small offsets
    centroids = {"a1": (28.0, -26.1), "a2": (28.001, -26.1)}
    out = m.cross_split_nn_distances(anchor_split, centroids)
    assert out["n"] == 2
    assert 50.0 < out["min_m"] < 200.0


def test_grid_mode_chains_shared_grid():
    # Under the coarse grid graph, sharing a tile links anchors even when chips
    # do not overlap -- the documented degenerate behaviour.
    rows = [
        _anchor("a1", "G1", "grid1", (0, 0, 1, 1)),
        _anchor("a2", "G2", "grid1", (100, 100, 101, 101)),
    ]
    comp = m.build_leakage_components(rows, "grid")
    assert comp["a1"] == comp["a2"]


# --------------------------------------------------------------------------
# Reconciliation gate.
# --------------------------------------------------------------------------
def test_reconcile_detects_mismatch():
    actual = {
        "observations": 10,
        "anchors": 3,
        "later_round_wins_removed": 0,
        "round_type": {"initial": 10},
        "decision_source": {"gemini_batch": 10},
    }
    diffs = m.reconcile(actual, m.EXPECTED_RUN3)
    assert diffs  # mismatched against the RUN 3 ledger


def test_reconcile_passes_on_match():
    exp = m.EXPECTED_RUN3
    actual = {
        "observations": exp["observations"],
        "anchors": exp["anchors"],
        "later_round_wins_removed": exp["later_round_wins_removed"],
        "round_type": dict(exp["round_type"]),
        "decision_source": dict(exp["decision_source"]),
    }
    assert m.reconcile(actual, exp) == []


# --------------------------------------------------------------------------
# End-to-end synthetic build (dedup + mapping + non-zero exit on reconcile).
# --------------------------------------------------------------------------
def _write_synthetic(root: Path):
    """Two anchors, a24 arm. a1: initial round + a later bisection round that
    RE-scores the same date (later round must win). a2: a gemini_failed then a
    later gemini_batch rescan in scoring_provenance (latest ts wins)."""
    arm = root / "a24"
    (arm / "scan_states").mkdir(parents=True)

    a1_state = {
        "anchor_id": "a1", "region_key": "jhb", "grid_id": "g1",
        "status": "done", "census_date": "2024-01-01",
        "catalog_max_date": "2025-01-01", "geometry_version": "gv",
        "rounds": [
            {"round_id": 1, "round_type": "initial", "results": [
                {"chip_index": 1, "capture_date": "2019-01-01", "version": 20220427,
                 "pv_present": False, "confidence": 0.9, "quality_flag": "usable",
                 "decision_source": "gemini_batch"},
                {"chip_index": 2, "capture_date": "2020-01-01", "version": "noversion",
                 "pv_present": True, "confidence": 0.8, "quality_flag": "unusable",
                 "decision_source": "gemini_batch"},
            ]},
            {"round_id": 2, "round_type": "bisection", "results": [
                # re-scores 2019-01-01: later round wins -> present
                {"chip_index": 1, "capture_date": "2019-01-01", "version": 20220427,
                 "pv_present": True, "confidence": 0.95, "quality_flag": "usable",
                 "decision_source": "gemini_batch"},
            ]},
        ],
    }
    a2_state = {
        "anchor_id": "a2", "region_key": "jhb", "grid_id": "g1",
        "status": "done", "census_date": "2024-01-01",
        "catalog_max_date": "2025-01-01", "geometry_version": "gv",
        "rounds": [
            {"round_id": 1, "round_type": "initial", "results": [
                {"chip_index": 1, "capture_date": "2019-01-01", "version": 20220427,
                 "pv_present": True, "confidence": 0.7, "quality_flag": "usable",
                 "decision_source": "gemini_batch"},
            ]},
        ],
    }
    (arm / "scan_states" / "a1.json").write_text(json.dumps(a1_state))
    (arm / "scan_states" / "a2.json").write_text(json.dumps(a2_state))

    # scoring_provenance: a2 has a failed row then a later successful rescan.
    sp_rows = [
        {"ts_utc": "2026-07-18T01:00:00Z", "scorer_name": "gemini",
         "model_id": "m", "prompt_config_hash": "ph", "scoring_mode": "batch",
         "anchor_id": "a1", "capture_date": "2019-01-01", "chip_path": "/c/a1_2019.png",
         "chip_sha256": "s1", "chip_sha256_error": None,
         "decision_source": "gemini_batch", "quality_flag": "usable"},
        {"ts_utc": "2026-07-18T01:00:00Z", "scorer_name": "gemini",
         "model_id": "m", "prompt_config_hash": "ph", "scoring_mode": "batch",
         "anchor_id": "a1", "capture_date": "2020-01-01", "chip_path": "/c/a1_2020.png",
         "chip_sha256": "s2", "chip_sha256_error": None,
         "decision_source": "gemini_batch", "quality_flag": "unusable"},
        {"ts_utc": "2026-07-18T01:00:00Z", "scorer_name": "gemini",
         "model_id": "m", "prompt_config_hash": "ph", "scoring_mode": "batch",
         "anchor_id": "a2", "capture_date": "2019-01-01", "chip_path": "/c/a2_old.png",
         "chip_sha256": "sx", "chip_sha256_error": None,
         "decision_source": "gemini_failed", "quality_flag": "usable"},
        {"ts_utc": "2026-07-18T09:00:00Z", "scorer_name": "gemini",
         "model_id": "m", "prompt_config_hash": "ph", "scoring_mode": "batch",
         "anchor_id": "a2", "capture_date": "2019-01-01", "chip_path": "/c/a2_new.png",
         "chip_sha256": "sy", "chip_sha256_error": None,
         "decision_source": "gemini_batch", "quality_flag": "usable"},
    ]
    (arm / "scoring_provenance.jsonl").write_text(
        "\n".join(json.dumps(r) for r in sp_rows) + "\n"
    )

    cp_rows = [
        {"anchor_id": "a1", "capture_date": "2019-01-01", "version": 20220427,
         "provider": "Wayback", "chip_path": "/c/a1_2019.tif", "chip_sha256": "t1",
         "raster_width_px": 10, "raster_height_px": 10, "raster_crs": "EPSG:3857",
         "extent_minx": 0.0, "extent_miny": 0.0, "extent_maxx": 10.0,
         "extent_maxy": 10.0, "gsd_x_m": 1.0, "gsd_y_m": 1.0, "achieved_zoom": 19,
         "raster_error": None},
        {"anchor_id": "a1", "capture_date": "2020-01-01", "version": "noversion",
         "provider": "TM", "chip_path": "/c/a1_2020.tif", "chip_sha256": "t2",
         "raster_width_px": 10, "raster_height_px": 10, "raster_crs": "EPSG:4326",
         "extent_minx": 0.0, "extent_miny": 0.0, "extent_maxx": 0.001,
         "extent_maxy": 0.001, "gsd_x_m": 0.1, "gsd_y_m": 0.1, "achieved_zoom": 19,
         "raster_error": None},
        {"anchor_id": "a2", "capture_date": "2019-01-01", "version": 20220427,
         "provider": "Wayback", "chip_path": "/c/a2_2019.tif", "chip_sha256": "t3",
         "raster_width_px": 10, "raster_height_px": 10, "raster_crs": "EPSG:3857",
         "extent_minx": 5.0, "extent_miny": 5.0, "extent_maxx": 15.0,
         "extent_maxy": 15.0, "gsd_x_m": 1.0, "gsd_y_m": 1.0, "achieved_zoom": 19,
         "raster_error": None},
    ]
    (arm / "chip_provenance.jsonl").write_text(
        "\n".join(json.dumps(r) for r in cp_rows) + "\n"
    )
    (root / "a48").mkdir()
    for name in ("scan_states",):
        (root / "a48" / name).mkdir()
    (root / "a48" / "scoring_provenance.jsonl").write_text("")
    (root / "a48" / "chip_provenance.jsonl").write_text("")

    anchors_csv = root / "anchors_all.csv"
    header = ("anchor_id,legacy_group_anchor_id,region_key,grid_id,source_grids,"
              "centroid_lon,centroid_lat,source_area_m2,chip_arm,review_extent_m,"
              "geometry_version,chip_lon_min,chip_lat_min,chip_lon_max,chip_lat_max\n")
    # a1 & a2 chips overlap (0-10 vs 5-15) -> same leakage component.
    body = (
        "a1,GRP,jhb,g1,g1,0,0,20.0,A24,24,gv,0.0,0.0,10.0,10.0\n"
        "a2,GRP,jhb,g1,g1,0,0,50.0,A24,24,gv,5.0,5.0,15.0,15.0\n"
    )
    anchors_csv.write_text(header + body)
    return anchors_csv


def _args(root, out, anchors_csv, **kw):
    ns = m.parse_args([
        "--data-root", str(root),
        "--anchors-csv", str(anchors_csv),
        "--out-dir", str(out),
        "--verify-tfw-sample", "0",
        *sum(([f"--{k.replace('_','-')}", str(v)] for k, v in kw.items()), []),
    ])
    return ns


def test_end_to_end_dedup_and_labels(tmp_path):
    root = tmp_path / "run3"
    anchors_csv = _write_synthetic(root)
    out = tmp_path / "out"
    ns = m.parse_args([
        "--data-root", str(root), "--anchors-csv", str(anchors_csv),
        "--out-dir", str(out), "--verify-tfw-sample", "0", "--skip-reconcile",
    ])
    rc = m.build_manifest(ns)
    assert rc == 0

    import pandas as pd
    df = pd.read_parquet(out / "manifest.parquet")
    # 3 unique observations (a1x2, a2x1); dedup collapsed the re-scores.
    assert len(df) == 3
    # a1/2019 re-scored by later bisection round -> present, round_type bisection
    r = df[(df.anchor_id == "a1") & (df.capture_date == "2019-01-01")].iloc[0]
    assert r["label_v1"] == "present"
    assert r["round_type"] == "bisection"
    # a1/2020 unusable -> uninformative regardless of pv_present True
    r = df[(df.anchor_id == "a1") & (df.capture_date == "2020-01-01")].iloc[0]
    assert r["label_v1"] == "uninformative"
    # a2/2019 latest ts_utc wins -> gemini_batch, new PNG sha
    r = df[df.anchor_id == "a2"].iloc[0]
    assert r["decision_source"] == "gemini_batch"
    assert r["chip_png_sha256"] == "sy"

    # overlapping chips -> a1 & a2 same split (no leakage)
    splits = pd.read_parquet(out / "splits.parquet")
    assert splits.set_index("anchor_id").loc["a1", "split"] == \
        splits.set_index("anchor_id").loc["a2", "split"]

    lock = json.loads((out / "MANIFEST_LOCK.json").read_text())
    assert lock["reconciliation"]["skipped"] is True
    assert "manifest.parquet" in lock["outputs"]
    assert lock["label_v1_totals"]["uninformative"] == 1


def test_reconcile_failure_nonzero_exit(tmp_path):
    root = tmp_path / "run3"
    anchors_csv = _write_synthetic(root)
    out = tmp_path / "out"
    # No --skip-reconcile: the 3-observation synthetic set cannot match the
    # 311,195 RUN 3 ledger, so the generator must exit non-zero.
    ns = m.parse_args([
        "--data-root", str(root), "--anchors-csv", str(anchors_csv),
        "--out-dir", str(out), "--verify-tfw-sample", "0",
    ])
    rc = m.build_manifest(ns)
    assert rc == 2
