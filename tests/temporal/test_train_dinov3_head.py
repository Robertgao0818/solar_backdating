"""ISSUE-04 — DINOv3 light-head train / calibrate / evaluate / co-teacher tests.

Regresses ``scripts/temporal/train_dinov3_head.py`` (Writer A). All fixtures are
tiny (``D=8``, tens of rows), the frozen backbone is NEVER touched (``embed_chips``
is stubbed via an injected ``scorer_factory``), and the linear-head forward is
pure numpy — so the whole file runs CPU-only in seconds with no network / no real
weights.

House conventions (tests/temporal): function-style, ``tmp_path`` for all IO,
heavy deps imported lazily inside the tests that need them.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.temporal import train_dinov3_head as th


# ---------------------------------------------------------------------------
# engineered-head fixtures: present_prob == sigmoid(feature[:,0]) with the
# unusable logit suppressed, so we can drive the calibration/decision math with
# exactly-known present probabilities (no backbone).
# ---------------------------------------------------------------------------
def _sigmoid_head(dim: int = 8, suppress: float = 100.0):
    import numpy as np

    weight = np.zeros((3, dim), dtype=np.float32)
    weight[0, 0] = 1.0  # present logit = feature[:,0]; absent logit = 0
    bias = np.array([0.0, 0.0, -suppress], dtype=np.float32)  # unusable never wins
    return weight, bias


def _feat_for_p(p: float, dim: int = 8):
    import numpy as np

    row = np.zeros(dim, dtype=np.float32)
    row[0] = math.log(p / (1.0 - p))  # sigmoid(logit(p)) == p
    return row


def _make_npz(
    anchor_labels,
    *,
    split_map=None,
    features=None,
    dim=8,
    capture_dates=None,
    sub_domains=None,
    zooms=None,
):
    """Build an in-memory feature-npz mapping (dict of numpy arrays)."""
    import numpy as np

    n = len(anchor_labels)
    if features is None:
        rng = np.random.default_rng(0)
        features = rng.standard_normal((n, dim)).astype(np.float32)
    anchors = [a for a, _ in anchor_labels]
    labels = [lbl for _, lbl in anchor_labels]
    split = [((split_map or {}).get(a, "train")) for a in anchors]

    def arr(vals):
        return np.array([str(v) for v in vals])

    return {
        "features": np.asarray(features, dtype=np.float32),
        "anchor_id": arr(anchors),
        "label_3class": arr(labels),
        "split": arr(split),
        "sub_domain": arr(sub_domains or ["cbd"] * n),
        "actual_zoom": arr(zooms or ["19"] * n),
        "terminal_status": arr(["done"] * n),
        "capture_date": arr(capture_dates or ["2023-01-01"] * n),
        "version": arr(["100"] * n),
        "png_path": arr([f"/chips/{a}.png" for a in anchors]),
    }


def _engineered_head_bundle(path: Path, weight, bias, calibration=None, backbone_model_id="bb"):
    import torch
    from torch import nn

    lin = nn.Linear(int(weight.shape[1]), int(weight.shape[0]))
    with torch.no_grad():
        lin.weight.copy_(torch.tensor(weight))
        lin.bias.copy_(torch.tensor(bias))
    th.save_head_bundle(
        path,
        lin.state_dict(),
        config={
            "backbone_model_id": backbone_model_id,
            "input_size": 256,
            "center_pool_k": 3,
            "upscale_policy": "bilinear",
            "chip_render_variant": "v",
        },
        provenance={"origin": "test"},
        calibration=calibration,
    )


# ---------------------------------------------------------------------------
# import hygiene — module + parser stay torch-free
# ---------------------------------------------------------------------------
def _subprocess(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)


def test_importing_module_does_not_import_torch() -> None:
    r = _subprocess(
        "import sys; import scripts.temporal.train_dinov3_head as th; "
        "sys.exit(0 if 'torch' not in sys.modules else 1)"
    )
    assert r.returncode == 0, f"module import pulled torch:\n{r.stderr}"


def test_building_parser_is_torch_free() -> None:
    r = _subprocess(
        "import sys; from scripts.temporal import train_dinov3_head as th; "
        "th.parse_args(['train','--features','x','--out','y']); "
        "sys.exit(0 if 'torch' not in sys.modules else 1)"
    )
    assert r.returncode == 0, f"parser build pulled torch:\n{r.stderr}"


# ---------------------------------------------------------------------------
# 1. extract-features — join + skip counts + version="" never joins
# ---------------------------------------------------------------------------
def test_join_manifest_provenance_pairs_and_skip_counts(tmp_path: Path) -> None:
    png_ok = tmp_path / "ok.png"
    png_ok.write_text("x", encoding="utf-8")
    png_gone = tmp_path / "gone.png"  # deliberately not created

    manifest = [
        {"anchor_id": "a", "capture_date": "2023-01-01", "version": "100",
         "label_3class": "present", "split": "train", "sub_domain": "cbd",
         "actual_zoom": "19", "terminal_status": "done"},  # matched + on disk
        {"anchor_id": "b", "capture_date": "2023-02-02", "version": "101",
         "label_3class": "absent", "split": "heldout", "sub_domain": "ncbd",
         "actual_zoom": "20", "terminal_status": "done"},  # matched, missing on disk
        {"anchor_id": "c", "capture_date": "2023-03-03", "version": "102",
         "label_3class": "present", "split": "train", "sub_domain": "cbd",
         "actual_zoom": "19", "terminal_status": "done"},  # no provenance
        {"anchor_id": "d", "capture_date": "2023-01-01", "version": "",
         "label_3class": "unusable", "split": "train", "sub_domain": "cbd",
         "actual_zoom": "", "terminal_status": "done"},  # census version="" -> never joins
    ]
    provenance = [
        {"anchor_id": "a", "capture_date": "2023-01-01", "version": "100", "png_path": str(png_ok)},
        {"anchor_id": "b", "capture_date": "2023-02-02", "version": "101", "png_path": str(png_gone)},
        {"anchor_id": "d", "capture_date": "2023-01-01", "version": "999", "png_path": str(png_ok)},
    ]
    rows, skips = th.join_manifest_provenance(manifest, provenance)

    assert [r["anchor_id"] for r in rows] == ["a"]
    assert rows[0]["png_path"] == str(png_ok)
    assert rows[0]["label_3class"] == "present"
    assert skips["skipped_no_chip"] == 2  # c (no prov) + d (version mismatch)
    assert skips["skipped_missing_on_disk"] == 1  # b


def test_assert_marker_free_provenance_rejects_marked_chips() -> None:
    # PRD-D4: slice-2 MARKED chips end in .png (not .nomarker.png) -> reject.
    rows = [
        {"anchor_id": "a", "png_path": "/chips/a.target-A-abc123.nomarker.png"},
        {"anchor_id": "b", "png_path": "/chips/b.target-A-def456.png"},  # marked
    ]
    with pytest.raises(SystemExit, match="MARKER-FREE"):
        th.assert_marker_free_provenance(rows)


def test_assert_marker_free_provenance_accepts_nomarker_chips() -> None:
    rows = [
        {"png_path": "/chips/a.target-A-abc123.nomarker.png"},
        {"png_path": "/chips/b.target-A-def456.nomarker.png"},
    ]
    th.assert_marker_free_provenance(rows)  # must not raise
    th.assert_marker_free_provenance([])  # empty is trivially marker-free


def test_cmd_extract_features_rejects_marked_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """extract-features on the slice-2 MARKED provenance aborts AFTER the join but
    BEFORE embedding — the frozen backbone is never constructed (PRD-D4 guard)."""
    import csv as _csv
    from types import SimpleNamespace

    manifest = tmp_path / "label_manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(
            fh,
            fieldnames=[
                "anchor_id", "capture_date", "version", "label_3class",
                "split", "sub_domain", "actual_zoom", "terminal_status",
            ],
        )
        w.writeheader()
        w.writerow({
            "anchor_id": "a", "capture_date": "2020-06-15", "version": "100",
            "label_3class": "present", "split": "train", "sub_domain": "cbd",
            "actual_zoom": "20", "terminal_status": "done",
        })

    png = tmp_path / "a.target-A-abc.png"  # MARKED (no .nomarker segment)
    png.write_bytes(b"PNG")
    prov = tmp_path / "chip_render_provenance.csv"
    with prov.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=["anchor_id", "capture_date", "version", "png_path"])
        w.writeheader()
        w.writerow({"anchor_id": "a", "capture_date": "2020-06-15", "version": "100", "png_path": str(png)})

    def _boom(*_a, **_k):
        raise AssertionError("backbone reached — guard failed to abort before embedding")

    monkeypatch.setattr(th, "embed_feature_rows", _boom)
    args = SimpleNamespace(
        manifest=manifest, provenance=[str(prov)], out=tmp_path / "f.npz",
        variant_label="marked", input_size=256, center_pool_k=3,
        upscale_policy="bilinear", device="cpu", batch_size=32,
        allow_marked_ablation=False,
    )
    with pytest.raises(SystemExit, match="MARKER-FREE"):
        th._cmd_extract_features(args)


def test_cmd_extract_features_marked_ablation_bypass_is_stamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--allow-marked-ablation (the D4 'marker = optional ablation' arm) bypasses the
    marker-free guard, and the cache meta is stamped marker_ablation=true so a marked
    cache can never masquerade as production-eligible."""
    import csv as _csv
    import json as _json
    from types import SimpleNamespace

    import numpy as np

    manifest = tmp_path / "label_manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(
            fh,
            fieldnames=[
                "anchor_id", "capture_date", "version", "label_3class",
                "split", "sub_domain", "actual_zoom", "terminal_status",
            ],
        )
        w.writeheader()
        w.writerow({
            "anchor_id": "a", "capture_date": "2020-06-15", "version": "100",
            "label_3class": "present", "split": "train", "sub_domain": "cbd",
            "actual_zoom": "20", "terminal_status": "done",
        })

    png = tmp_path / "a.target-A-abc.png"  # MARKED (no .nomarker segment)
    png.write_bytes(b"PNG")
    prov = tmp_path / "chip_render_provenance.csv"
    with prov.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=["anchor_id", "capture_date", "version", "png_path"])
        w.writeheader()
        w.writerow({"anchor_id": "a", "capture_date": "2020-06-15", "version": "100", "png_path": str(png)})

    def _fake_embed(feature_rows, **_k):
        return (
            np.zeros((len(feature_rows), 4), dtype=np.float32),
            {"backbone_model_id": "stub", "throughput_chips_per_s": 1.0, "peak_vram_bytes": None},
        )

    monkeypatch.setattr(th, "embed_feature_rows", _fake_embed)
    args = SimpleNamespace(
        manifest=manifest, provenance=[str(prov)], out=tmp_path / "f.npz",
        variant_label="marked_ablation", input_size=256, center_pool_k=3,
        upscale_policy="bilinear", device="cpu", batch_size=32,
        allow_marked_ablation=True,
    )
    th._cmd_extract_features(args)
    meta = _json.loads((tmp_path / "f.npz.meta.json").read_text(encoding="utf-8"))
    assert meta["marker_ablation"] is True


def test_write_features_npz_roundtrip_schema(tmp_path: Path) -> None:
    import numpy as np

    rows = [
        {"anchor_id": "a", "capture_date": "2023-01-01", "version": "100",
         "label_3class": "present", "split": "train", "sub_domain": "cbd",
         "actual_zoom": "19", "terminal_status": "done", "png_path": "/x/a.png"},
        {"anchor_id": "b", "capture_date": "2023-02-02", "version": "101",
         "label_3class": "absent", "split": "heldout", "sub_domain": "ncbd",
         "actual_zoom": "20", "terminal_status": "done", "png_path": "/x/b.png"},
    ]
    feats = np.arange(16, dtype=np.float32).reshape(2, 8)
    npz_path = th.write_features_npz(tmp_path / "f.npz", rows, feats)

    data = np.load(npz_path)
    assert data["features"].shape == (2, 8)
    assert data["features"].dtype == np.float32
    for col in th.FEATURE_STRING_COLS:
        assert len(data[col]) == 2
        assert data[col].dtype.kind == "U"  # unicode string array
    assert list(data["anchor_id"]) == ["a", "b"]


def test_embed_feature_rows_uses_scorer_and_records_meta() -> None:
    import numpy as np

    class _FakeScorer:
        backbone_model_id = "fake-bb"

        def __init__(self, **kw):
            self.kw = kw

        def embed_chips(self, chip_paths, batch_size=32):
            self.batch_size = batch_size
            return np.array([[float(len(p))] + [0.0] * 7 for p in chip_paths], dtype=np.float32)

    captured = {}

    def factory(**kw):
        captured.update(kw)
        return _FakeScorer(**kw)

    rows = [{"png_path": "/a/one.png"}, {"png_path": "/bb/two.png"}]
    feats, meta = th.embed_feature_rows(
        rows,
        input_size=256,
        center_pool_k=3,
        upscale_policy="bilinear",
        device="cpu",
        batch_size=4,
        scorer_factory=factory,
    )
    assert feats.shape == (2, 8)
    assert feats.dtype == np.float32
    assert meta["backbone_model_id"] == "fake-bb"
    assert meta["peak_vram_bytes"] is None  # cpu path never touches cuda
    assert captured["upscale_policy"] == "bilinear"
    assert captured["device"] == "cpu"
    assert captured["input_size"] == 256


# ---------------------------------------------------------------------------
# 2. train — leak guard, determinism, zero-count class, bundle contract
# ---------------------------------------------------------------------------
def test_assert_no_heldout_leak_raises_and_passes() -> None:
    with pytest.raises(SystemExit):
        th._assert_no_heldout_leak(["train", "heldout"], [True, True])
    # only train rows selected -> no raise
    th._assert_no_heldout_leak(["train", "heldout"], [True, False])


def _mixed_train_npz(dim=8):
    labels = ["present", "absent", "unusable"]
    anchor_labels = [(f"A{i:02d}", labels[i % 3]) for i in range(24)]
    return _make_npz(anchor_labels, dim=dim)


def test_train_is_deterministic_bit_identical_pt(tmp_path: Path) -> None:
    npz = _mixed_train_npz()
    out_a = tmp_path / "a" / "head.pt"
    out_b = tmp_path / "b" / "head.pt"
    th.train_head(npz, out_path=out_a, seed=20260705, max_epochs=40, patience=10, config={})
    th.train_head(npz, out_path=out_b, seed=20260705, max_epochs=40, patience=10, config={})

    assert out_a.read_bytes() == out_b.read_bytes()
    sha_a = json.loads(out_a.with_suffix(".json").read_text())["head_pt_sha256"]
    sha_b = json.loads(out_b.with_suffix(".json").read_text())["head_pt_sha256"]
    assert sha_a == sha_b


def test_train_zero_count_class_gets_zero_weight_and_warning(tmp_path: Path) -> None:
    # No 'unusable' rows anywhere -> the unusable class weight must be 0.0.
    anchor_labels = [(f"A{i:02d}", "present" if i % 2 == 0 else "absent") for i in range(20)]
    npz = _make_npz(anchor_labels)
    out = tmp_path / "head.pt"
    log = th.train_head(npz, out_path=out, seed=1, max_epochs=20, patience=5, config={})

    assert log["class_counts"]["unusable"] == 0
    assert log["class_weights"]["unusable"] == 0.0
    assert any("unusable" in w for w in log["warnings"])
    # the two populated classes keep positive weights
    assert log["class_weights"]["present"] > 0.0
    assert log["class_weights"]["absent"] > 0.0


def test_train_writes_bundle_conforming_to_contract(tmp_path: Path) -> None:
    import hashlib

    npz = _mixed_train_npz()
    out = tmp_path / "head.pt"
    th.train_head(
        npz,
        out_path=out,
        seed=7,
        max_epochs=20,
        patience=5,
        config={"backbone_model_id": "bb", "input_size": 256, "center_pool_k": 3,
                "upscale_policy": "bilinear", "chip_render_variant": "variantX"},
    )
    sidecar = json.loads(out.with_suffix(".json").read_text())
    assert set(sidecar) >= {
        "schema_version", "head_arch", "class_order", "calibration",
        "config", "provenance", "head_pt_sha256",
    }
    assert sidecar["schema_version"] == 1
    assert sidecar["head_arch"] == "linear"
    assert sidecar["class_order"] == ["present", "absent", "unusable"]  # FIXED order
    assert sidecar["calibration"] is None  # not calibrated yet
    assert sidecar["config"]["chip_render_variant"] == "variantX"
    # head_pt_sha256 matches the actual .pt bytes
    assert sidecar["head_pt_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    # train_log written
    assert out.with_name("head.train_log.json").exists()


def test_train_rejects_unknown_label_loudly(tmp_path: Path) -> None:
    """An unrecognized label_3class value fails loudly instead of silently coercing
    to 'unusable' — consistency with the file's other data-contract guards."""
    anchor_labels = [(f"A{i:02d}", "present" if i % 2 == 0 else "absent") for i in range(12)]
    anchor_labels[5] = ("A05", "typo_label")  # not in CLASS_ORDER
    npz = _make_npz(anchor_labels)
    with pytest.raises(SystemExit, match="unrecognized label_3class"):
        th.train_head(npz, out_path=tmp_path / "head.pt", seed=1, max_epochs=5, patience=3, config={})


def _write_train_npz_with_meta(tmp_path: Path, *, marker_ablation: bool) -> "SimpleNamespace":
    """Persist a mixed train npz + its meta sidecar; return _cmd_train args."""
    import json as _json
    from types import SimpleNamespace

    import numpy as np

    np.savez(tmp_path / "f.npz", **_mixed_train_npz())
    (tmp_path / "f.npz.meta.json").write_text(
        _json.dumps({
            "backbone_model_id": "stub", "input_size": 256, "center_pool_k": 3,
            "upscale_policy": "bilinear", "variant_label": "arm", "marker_ablation": marker_ablation,
        }),
        encoding="utf-8",
    )
    return SimpleNamespace(
        features=tmp_path / "f.npz", out=tmp_path / "head.pt", seed=1, lr=th.DEFAULT_LR,
        weight_decay=th.DEFAULT_WEIGHT_DECAY, batch_size=8, max_epochs=5, patience=3,
    )


def test_cmd_train_propagates_marker_ablation_into_bundle(tmp_path: Path, capsys) -> None:
    """marker_ablation from the feature-cache meta is carried into the head-bundle
    provenance (a machine-checkable diagnostic-only flag, PRD D4) and warned loudly,
    so a pin/rollout gate can reject a marked head as production."""
    args = _write_train_npz_with_meta(tmp_path, marker_ablation=True)
    th._cmd_train(args)
    sidecar = json.loads((tmp_path / "head.json").read_text())
    assert sidecar["provenance"]["marker_ablation"] is True
    assert "marker_ablation" in capsys.readouterr().err.lower()


def test_cmd_train_nomarker_stamps_false(tmp_path: Path) -> None:
    """A normal (nomarker) cache stamps marker_ablation=false in provenance — the
    flag is always present so the pin gate never has to guess."""
    args = _write_train_npz_with_meta(tmp_path, marker_ablation=False)
    th._cmd_train(args)
    sidecar = json.loads((tmp_path / "head.json").read_text())
    assert sidecar["provenance"]["marker_ablation"] is False


def test_train_head_state_dict_shape(tmp_path: Path) -> None:
    import torch

    npz = _mixed_train_npz(dim=8)
    out = tmp_path / "head.pt"
    th.train_head(npz, out_path=out, seed=3, max_epochs=15, patience=5, config={})
    obj = torch.load(out, map_location="cpu")
    state = obj["state_dict"]
    assert set(state.keys()) == {"weight", "bias"}
    assert tuple(state["weight"].shape) == (3, 8)
    assert tuple(state["bias"].shape) == (3,)


# ---------------------------------------------------------------------------
# 3. calibrate — sweep finds the band, tie-break determinism, guards, frontier
# ---------------------------------------------------------------------------
def _clean_calib_npz():
    """One held-out anchor with cleanly separated present/absent rows.

    Present probs 0.85 / absent 0.15 sit safely BETWEEN grid points 0.1 and 0.2,
    so the optimal-band tie-break is float-robust: the first feasible width-0 band
    is lo==hi==0.2 (0.1 would mis-decide the 0.15 absent row as present).
    """
    import numpy as np

    weight, bias = _sigmoid_head()
    rows = [
        ("H0", "present", 0.85),
        ("H0", "present", 0.85),
        ("H0", "absent", 0.15),
        ("H0", "absent", 0.15),
    ]
    feats = np.array([_feat_for_p(p) for _, _, p in rows], dtype=np.float32)
    npz = _make_npz(
        [(a, lbl) for a, lbl, _ in rows],
        split_map={"H0": "heldout"},
        features=feats,
    )
    return npz, weight, bias


def test_calibrate_finds_known_band_and_tiebreak_is_deterministic() -> None:
    npz, weight, bias = _clean_calib_npz()
    res1, frontier1 = th.calibrate_band(npz, weight, bias, coverage_floor=0.9, grid_step=0.1)
    res2, frontier2 = th.calibrate_band(npz, weight, bias, coverage_floor=0.9, grid_step=0.1)

    # perfectly separable -> full coverage + full agreement is achievable
    assert res1["decided_agreement"] == 1.0
    assert res1["abstain_rate"] == 0.0
    assert res1["calib_anchors"] == 1
    # tie-break (max agreement, max coverage, min width, min lo) -> degenerate band
    # at the smallest feasible threshold: lo==hi==0.2 (0.1 leaves the absent row
    # undecided; 0.2 is the first feasible min-lo width-0 band).
    assert (res1["lo"], res1["hi"]) == (0.2, 0.2)
    # fully deterministic across runs
    assert res1 == res2
    assert frontier1 == frontier2
    # frontier covers the upper triangle of an 11-point grid: 11+10+...+1 = 66
    assert len(frontier1) == 66


def test_calibrate_disjointness_guard_raises_on_overlap() -> None:
    import numpy as np

    weight, bias = _sigmoid_head()
    # anchor 'X' appears in BOTH splits -> calibration leak.
    feats = np.array([_feat_for_p(0.9), _feat_for_p(0.1)], dtype=np.float32)
    npz = _make_npz(
        [("X", "present"), ("X", "absent")],
        split_map={"X": "train"},  # first row train...
        features=feats,
    )
    npz["split"] = np.array(["train", "heldout"])  # ...second row heldout -> overlap
    with pytest.raises(SystemExit):
        th.calibrate_band(npz, weight, bias, coverage_floor=0.9, grid_step=0.1)


def test_run_calibrate_writes_frontier_and_updates_sidecar(tmp_path: Path) -> None:
    npz, weight, bias = _clean_calib_npz()
    feats_path = th.write_features_npz(
        tmp_path / "feats.npz",
        [
            {c: str(npz[c][i]) for c in th.FEATURE_STRING_COLS}
            for i in range(len(npz["anchor_id"]))
        ],
        npz["features"],
    )
    head_pt = tmp_path / "head.pt"
    _engineered_head_bundle(head_pt, weight, bias, calibration=None)

    result = th.run_calibrate(feats_path, head_pt, coverage_floor=0.9, grid_step=0.1)

    frontier_csv = tmp_path / "feats.calibration_frontier.csv"
    assert frontier_csv.exists()
    header = frontier_csv.read_text().splitlines()[0]
    assert header == "lo,hi,coverage,agreement"
    # sidecar calibration updated in place
    sidecar = json.loads(head_pt.with_suffix(".json").read_text())
    assert sidecar["calibration"]["lo"] == 0.2
    assert sidecar["calibration"]["hi"] == 0.2
    assert result["calibration"]["decided_agreement"] == 1.0


# ---------------------------------------------------------------------------
# student decision mapping (contract: p>hi present, p<lo absent, else ambiguous;
# argmax==unusable overrides)
# ---------------------------------------------------------------------------
def test_student_decisions_mapping() -> None:
    import numpy as np

    weight, bias = _sigmoid_head()
    feats = np.array([_feat_for_p(0.9), _feat_for_p(0.5), _feat_for_p(0.1)], dtype=np.float32)
    dec, pp = th.student_decisions(feats, weight, bias, lo=0.3, hi=0.7)
    assert dec == ["present", "ambiguous", "absent"]
    assert abs(float(pp[0]) - 0.9) < 1e-3
    assert abs(float(pp[1]) - 0.5) < 1e-3
    assert abs(float(pp[2]) - 0.1) < 1e-3

    # argmax==unusable overrides the present-prob band
    w2 = np.zeros((3, 8), dtype=np.float32)
    b2 = np.array([0.0, 0.0, 5.0], dtype=np.float32)  # unusable logit dominates
    dec2, _ = th.student_decisions(np.zeros((1, 8), dtype=np.float32), w2, b2, lo=0.3, hi=0.7)
    assert dec2 == ["unusable"]


# ---------------------------------------------------------------------------
# 4. evaluate — aggregation correctness + empty-stratum safety
# ---------------------------------------------------------------------------
def _rec(teacher, decision, zoom="19", term="done", sub="cbd", yb="ge_2023", split="heldout"):
    return {
        "teacher": teacher,
        "decision": decision,
        "actual_zoom": zoom,
        "terminal_status": term,
        "sub_domain": sub,
        "year_bucket": yb,
        "split": split,
    }


def test_compute_evaluation_hand_computed() -> None:
    records = [
        _rec("present", "present", zoom="19"),   # decided, agree
        _rec("present", "absent", zoom="19"),    # decided, disagree
        _rec("absent", "absent", zoom="20"),     # decided, agree
        _rec("absent", "ambiguous", zoom="20"),  # abstain (not pa-decided)
        _rec("unusable", "unusable", zoom="18"),  # not pa-decided
    ]
    m = th.compute_evaluation(records)
    ov = m["overall"]
    assert ov["n"] == 5
    assert ov["decided"] == 3  # present/absent student decisions
    assert ov["agree"] == 2
    assert abs(ov["coverage"] - 3 / 5) < 1e-12
    assert abs(ov["agreement"] - 2 / 3) < 1e-12

    # confusion cells
    assert m["confusion"]["present|present"] == 1
    assert m["confusion"]["present|absent"] == 1
    assert m["confusion"]["absent|ambiguous"] == 1
    assert m["confusion"]["unusable|unusable"] == 1
    assert m["confusion"]["present|unusable"] == 0

    # zoom breakdown: zoom 19 -> 2 decided, 1 agree
    z19 = m["by_actual_zoom"]["19"]
    assert z19["decided"] == 2 and z19["agree"] == 1
    assert abs(z19["agreement"] - 0.5) < 1e-12


def test_compute_evaluation_empty_decided_stratum_is_none_not_crash() -> None:
    # zoom '18' group has only an abstain -> no pa-decided -> agreement None.
    records = [
        _rec("present", "present", zoom="19"),
        _rec("absent", "ambiguous", zoom="18"),
    ]
    m = th.compute_evaluation(records)
    g18 = m["by_actual_zoom"]["18"]
    assert g18["n"] == 1
    assert g18["decided"] == 0
    assert g18["coverage"] == 0.0
    assert g18["agreement"] is None  # empty-decided -> None, never ZeroDivision


def test_run_evaluate_and_co_teacher_write_files(tmp_path: Path) -> None:
    import numpy as np

    weight, bias = _sigmoid_head()
    # 6 held-out anchors (3 present@0.9, 3 absent@0.1) + 2 train anchors.
    rows = []
    feats = []
    for i in range(3):
        rows.append((f"HP{i}", "present"))
        feats.append(_feat_for_p(0.9))
    for i in range(3):
        rows.append((f"HA{i}", "absent"))
        feats.append(_feat_for_p(0.1))
    for i in range(2):
        rows.append((f"TR{i}", "present"))
        feats.append(_feat_for_p(0.9))
    split_map = {a: ("train" if a.startswith("TR") else "heldout") for a, _ in rows}
    npz = _make_npz(rows, split_map=split_map, features=np.array(feats, dtype=np.float32))

    feats_path = th.write_features_npz(
        tmp_path / "feats.npz",
        [{c: str(npz[c][i]) for c in th.FEATURE_STRING_COLS} for i in range(len(rows))],
        npz["features"],
    )
    head_pt = tmp_path / "head.pt"
    _engineered_head_bundle(
        head_pt, weight, bias,
        calibration={"lo": 0.3, "hi": 0.7, "rule": "test", "calib_anchors": 3,
                     "decided_agreement": 1.0, "abstain_rate": 0.0},
    )

    metrics = th.run_evaluate(feats_path, head_pt, tmp_path / "eval")
    assert (tmp_path / "eval" / "evaluate_metrics.csv").exists()
    assert (tmp_path / "eval" / "evaluate_report.md").exists()
    # report-half rows are cleanly separated -> agreement 1.0 over decided rows
    assert metrics["overall"]["agreement"] == 1.0
    assert metrics["overall"]["coverage"] == 1.0

    strata = th.run_co_teacher(feats_path, head_pt, tmp_path / "ct")
    assert (tmp_path / "ct" / "co_teacher_disagreement.csv").exists()
    assert (tmp_path / "ct" / "co_teacher_summary.md").exists()
    assert len(strata) >= 1


def test_evaluate_report_title_reflects_backbone_id() -> None:
    """ISSUE-06 fix: the title is derived from the bundle's backbone, not the
    hardcoded 'DINOv3 head ... (ISSUE-04)' string — a DINOv2-floor bundle report
    must say DINOv2, never inherit the DINOv3/ISSUE-04 heading over its body."""
    dinov2_title = th._evaluate_report_title("vit_small_patch14_dinov2.lvd142m")
    assert dinov2_title.startswith("# DINOv2 floor head (vit_small_patch14_dinov2.lvd142m)")
    assert "ISSUE-04" not in dinov2_title

    dinov3_title = th._evaluate_report_title("vit_large_patch16_dinov3.sat493m")
    assert dinov3_title.startswith("# DINOv3 head (vit_large_patch16_dinov3.sat493m)")
    assert "ISSUE-04" in dinov3_title

    # None (un-stamped bundle) resolves to the historical DINOv3-L-SAT default.
    none_title = th._evaluate_report_title(None)
    assert none_title.startswith("# DINOv3 head (")
    assert "ISSUE-04" in none_title


def test_run_evaluate_titles_report_from_bundle_backbone(tmp_path: Path) -> None:
    """End-to-end: ``run_evaluate`` reads ``config.backbone_model_id`` off the head
    sidecar (no extra CLI arg) and writes it into the report title."""
    import numpy as np

    weight, bias = _sigmoid_head()
    rows = [(f"HP{i}", "present") for i in range(3)] + [(f"HA{i}", "absent") for i in range(3)]
    feats = [_feat_for_p(0.9)] * 3 + [_feat_for_p(0.1)] * 3
    npz = _make_npz(rows, features=np.array(feats, dtype=np.float32))
    feats_path = th.write_features_npz(
        tmp_path / "feats.npz",
        [{c: str(npz[c][i]) for c in th.FEATURE_STRING_COLS} for i in range(len(rows))],
        npz["features"],
    )
    head_pt = tmp_path / "head.pt"
    _engineered_head_bundle(
        head_pt, weight, bias,
        calibration={"lo": 0.3, "hi": 0.7, "rule": "test", "calib_anchors": 3,
                     "decided_agreement": 1.0, "abstain_rate": 0.0},
        backbone_model_id="vit_small_patch14_dinov2.lvd142m",
    )

    th.run_evaluate(feats_path, head_pt, tmp_path / "eval")
    title = (tmp_path / "eval" / "evaluate_report.md").read_text(encoding="utf-8").splitlines()[0]
    assert title == "# DINOv2 floor head (vit_small_patch14_dinov2.lvd142m) — held-out report-half evaluation"


# ---------------------------------------------------------------------------
# 5. co-teacher — per-stratum disagreement counts + empty-decided safety
# ---------------------------------------------------------------------------
def test_co_teacher_strata_hand_computed() -> None:
    records = [
        _rec("present", "present", sub="cbd"),    # agree
        _rec("present", "absent", sub="cbd"),     # disagree
        _rec("present", "ambiguous", sub="cbd"),  # abstain
        _rec("absent", "ambiguous", sub="ncbd"),  # abstain-only stratum
    ]
    strata = th.co_teacher_strata(records)
    by_sub = {s["sub_domain"]: s for s in strata}

    cbd = by_sub["cbd"]
    assert cbd["n"] == 3
    assert cbd["n_agree"] == 1
    assert cbd["n_disagree"] == 1
    assert cbd["n_abstain"] == 1
    assert abs(cbd["disagreement_rate_over_decided"] - 0.5) < 1e-12  # 1 / (present+absent)
    assert abs(cbd["abstain_rate_over_total"] - 1 / 3) < 1e-12
    assert abs(cbd["coverage_decided_over_total"] - 2 / 3) < 1e-12

    ncbd = by_sub["ncbd"]  # only an abstain -> no decided rows
    assert ncbd["n"] == 1
    assert ncbd["n_disagree"] == 0
    assert ncbd["disagreement_rate_over_decided"] == 0.0  # no ZeroDivision
    assert ncbd["coverage_decided_over_total"] == 0.0


# ---------------------------------------------------------------------------
# 6. head-bundle integration — Writer B's load_head_bundle reads A's bundle.
# Guarded: xfail (not fail) while Writer B is still in flight.
# ---------------------------------------------------------------------------
def test_load_head_bundle_integration_with_writer_b(tmp_path: Path) -> None:
    from scripts.temporal import dinov3_scorer as ds

    if not hasattr(ds, "load_head_bundle"):
        pytest.xfail("writer B in flight: dinov3_scorer.load_head_bundle not yet available")

    weight, bias = _sigmoid_head()
    head_pt = tmp_path / "head.pt"
    calibration = {"lo": 0.3, "hi": 0.7, "rule": "test", "calib_anchors": 5,
                   "decided_agreement": 0.95, "abstain_rate": 0.1}
    _engineered_head_bundle(head_pt, weight, bias, calibration=calibration)

    state, meta = ds.load_head_bundle(head_pt)
    assert set(state.keys()) == {"weight", "bias"}
    assert meta["class_order"] == ["present", "absent", "unusable"]
    assert meta["calibration"]["lo"] == 0.3
    assert meta["head_pt_sha256"] == json.loads(head_pt.with_suffix(".json").read_text())["head_pt_sha256"]

    # Constructing the scorer with the bundle adopts the sidecar config torch-free.
    try:
        scorer = ds.Dinov3PresenceScorer(head_checkpoint=head_pt)
        assert scorer.input_size == 256
        assert scorer.center_pool_k == 3
        fp = scorer.prompt_config_fingerprint("batch", config=None)
        assert fp is not None
    except (AttributeError, TypeError, KeyError):
        pytest.xfail("writer B in flight: sidecar-config adoption not yet wired")


# ---------------------------------------------------------------------------
# ISSUE-05 — backbone/geometry selectable at extract-features; patch size stamped
# ---------------------------------------------------------------------------
def test_extract_features_parser_accepts_backbone_and_weights_cache(tmp_path: Path) -> None:
    args = th.parse_args(
        [
            "extract-features",
            "--manifest", "m.csv",
            "--provenance", "p.csv",
            "--out", "o.npz",
            "--variant-label", "dinov2_floor_arm",
            "--backbone-model-id", "vit_small_patch14_dinov2.lvd142m",
            "--input-size", "518",
            "--center-pool-k", "6",
            "--weights-cache-dir", str(tmp_path / "hf_cache"),
        ]
    )
    assert args.backbone_model_id == "vit_small_patch14_dinov2.lvd142m"
    assert args.input_size == 518
    assert str(args.weights_cache_dir) == str(tmp_path / "hf_cache")


def test_extract_features_backbone_id_defaults_to_none() -> None:
    # Default None -> the scorer resolves the DINOv3-L-SAT module default, so the
    # existing DINOv3 extraction stays byte-identical.
    args = th.parse_args(
        ["extract-features", "--manifest", "m", "--provenance", "p", "--out", "o", "--variant-label", "v"]
    )
    assert args.backbone_model_id is None
    assert args.weights_cache_dir is None


def test_embed_feature_rows_threads_backbone_and_records_patch_size() -> None:
    import numpy as np

    class _FakeScorer:
        def __init__(self, **kw):
            self.kw = kw
            self.backbone_model_id = kw.get("backbone_model_id") or "default-bb"
            self.patch_size = 14 if "patch14" in str(self.backbone_model_id) else 16

        def embed_chips(self, chip_paths, batch_size=32):
            return np.zeros((len(chip_paths), 4), dtype=np.float32)

    captured = {}

    def factory(**kw):
        captured.update(kw)
        return _FakeScorer(**kw)

    feats, meta = th.embed_feature_rows(
        [{"png_path": "/a/one.png"}],
        input_size=518,
        center_pool_k=6,
        upscale_policy="bilinear",
        device="cpu",
        batch_size=4,
        backbone_model_id="vit_small_patch14_dinov2.lvd142m",
        weights_cache_dir="/tmp/dinov2_cache",
        scorer_factory=factory,
    )
    assert captured["backbone_model_id"] == "vit_small_patch14_dinov2.lvd142m"
    assert captured["weights_cache_dir"] == "/tmp/dinov2_cache"
    assert meta["backbone_model_id"] == "vit_small_patch14_dinov2.lvd142m"
    assert meta["patch_size"] == 14


def test_cmd_extract_features_stamps_backbone_patch_input_in_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """extract-features provenance meta records backbone id + patch size + input size
    (the backbone id + patch size come from the constructed scorer, not the args)."""
    import csv as _csv
    import json as _json
    from types import SimpleNamespace

    import numpy as np

    manifest = tmp_path / "label_manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(
            fh,
            fieldnames=[
                "anchor_id", "capture_date", "version", "label_3class",
                "split", "sub_domain", "actual_zoom", "terminal_status",
            ],
        )
        w.writeheader()
        w.writerow({
            "anchor_id": "a", "capture_date": "2020-06-15", "version": "100",
            "label_3class": "present", "split": "train", "sub_domain": "cbd",
            "actual_zoom": "20", "terminal_status": "done",
        })

    png = tmp_path / "a.target-A-abc.nomarker.png"  # marker-free
    png.write_bytes(b"PNG")
    prov = tmp_path / "student_chip_render_provenance.csv"
    with prov.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=["anchor_id", "capture_date", "version", "png_path"])
        w.writeheader()
        w.writerow({"anchor_id": "a", "capture_date": "2020-06-15", "version": "100", "png_path": str(png)})

    def _fake_embed(feature_rows, **kw):
        return (
            np.zeros((len(feature_rows), 4), dtype=np.float32),
            {
                "backbone_model_id": kw.get("backbone_model_id"),
                "patch_size": 14,
                "throughput_chips_per_s": 1.0,
                "peak_vram_bytes": None,
            },
        )

    monkeypatch.setattr(th, "embed_feature_rows", _fake_embed)
    args = SimpleNamespace(
        manifest=manifest, provenance=[str(prov)], out=tmp_path / "f.npz",
        variant_label="dinov2_floor_arm",
        backbone_model_id="vit_small_patch14_dinov2.lvd142m",
        input_size=518, center_pool_k=6, upscale_policy="bilinear",
        device="cpu", batch_size=32, weights_cache_dir=None,
        allow_marked_ablation=False,
    )
    th._cmd_extract_features(args)
    meta = _json.loads((tmp_path / "f.npz.meta.json").read_text(encoding="utf-8"))
    assert meta["backbone_model_id"] == "vit_small_patch14_dinov2.lvd142m"
    assert meta["patch_size"] == 14
    assert meta["input_size"] == 518


def test_cmd_train_carries_patch_size_into_bundle_config(tmp_path: Path) -> None:
    """The head-bundle config records patch_size (provenance), read from the
    feature-cache meta the extract step stamped."""
    import json as _json
    from types import SimpleNamespace

    import numpy as np

    np.savez(tmp_path / "f.npz", **_mixed_train_npz())
    (tmp_path / "f.npz.meta.json").write_text(
        _json.dumps({
            "backbone_model_id": "vit_small_patch14_dinov2.lvd142m",
            "input_size": 518, "center_pool_k": 6, "upscale_policy": "bilinear",
            "patch_size": 14, "variant_label": "dinov2_floor_arm", "marker_ablation": False,
        }),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        features=tmp_path / "f.npz", out=tmp_path / "head.pt", seed=1, lr=th.DEFAULT_LR,
        weight_decay=th.DEFAULT_WEIGHT_DECAY, batch_size=8, max_epochs=5, patience=3,
    )
    th._cmd_train(args)
    sidecar = _json.loads((tmp_path / "head.json").read_text())
    assert sidecar["config"]["patch_size"] == 14
    assert sidecar["config"]["backbone_model_id"] == "vit_small_patch14_dinov2.lvd142m"
