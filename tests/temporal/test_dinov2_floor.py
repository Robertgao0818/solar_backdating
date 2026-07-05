"""ISSUE-05 — DINOv2 ViT-S/14 falsification-floor scorer tests.

Mirrors the DINOv3 scaffold's registry / vocab / fingerprint / missing-chip /
meta-device seams against the DINOv2 floor (``dinov2_floor`` / ``dinov2_failed``).
The floor is a thin subclass of ``Dinov3PresenceScorer``: same seam, frozen
backbone + light head, anchor-conditioned identically (it inherits
``_center_pool`` / ``_load_chip_tensor`` / ``_observe`` / ``score`` / ``batch`` /
``embed_chips`` verbatim). Only the backbone identity (22M ViT-S/14, patch 14,
embed_dim 384) and the decision_source vocabulary differ. Weight-free unless a
test is explicitly gated on torch (no timm download in unit tests).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from scripts.temporal import presence_scorer as ps
from scripts.temporal.dinov3_scorer import (
    DECISION_SOURCE_DINOV2_FAILED,
    DECISION_SOURCE_DINOV2_OK,
    Dinov2PresenceScorer,
    Dinov3PresenceScorer,
)
from scripts.temporal.presence_scorer import (
    Pick,
    PresenceScorer,
    available_scorers,
    get_scorer,
    validate_emission,
)

_DINOV2_ID = "vit_small_patch14_dinov2.lvd142m"


def _mods_available(*mods: str) -> bool:
    return all(importlib.util.find_spec(m) is not None for m in mods)


requires_torch = pytest.mark.skipif(
    not _mods_available("torch", "numpy"), reason="torch/numpy not installed"
)
requires_numpy = pytest.mark.skipif(
    not _mods_available("numpy"), reason="numpy not installed"
)


# ---------------------------------------------------------------------------
# a. Registry / selection flag — SAME seam, Gemini stays the default
# ---------------------------------------------------------------------------


def test_dinov2_floor_registered_additively_gemini_unchanged() -> None:
    scorers = available_scorers()
    assert "dinov2_floor" in scorers
    # Purely additive: the pre-existing scorers stay registered, so the CLI default
    # (argparse default="gemini" at every call site) is unaffected.
    for expected in ("gemini", "dry_run", "dinov3_frozen"):
        assert expected in scorers


def test_get_scorer_dinov2_floor_is_protocol_conforming() -> None:
    scorer = get_scorer("dinov2_floor", device="cpu")
    assert isinstance(scorer, Dinov2PresenceScorer)
    assert isinstance(scorer, Dinov3PresenceScorer)  # thin subclass
    assert isinstance(scorer, PresenceScorer)  # runtime_checkable protocol
    assert type(scorer) is Dinov2PresenceScorer
    assert scorer.name == "dinov2_floor"
    assert scorer.failure_decision_sources == frozenset({DECISION_SOURCE_DINOV2_FAILED})
    assert scorer.decision_sources == frozenset(
        {DECISION_SOURCE_DINOV2_OK, DECISION_SOURCE_DINOV2_FAILED}
    )
    # quality_flags are backbone-agnostic — inherited unchanged from the base.
    assert scorer.quality_flags == frozenset({"usable", "ambiguous", "unusable", "missing_chip"})


def test_dinov2_decision_sources_are_distinct_and_registered_in_module_vocab() -> None:
    # Distinct from the DINOv3 pair, and additively registered so scan_state's
    # write-time gate accepts DINOv2-scored rounds.
    assert (DECISION_SOURCE_DINOV2_OK, DECISION_SOURCE_DINOV2_FAILED) == (
        "dinov2_floor",
        "dinov2_failed",
    )
    assert DECISION_SOURCE_DINOV2_OK in ps.known_decision_sources()
    assert DECISION_SOURCE_DINOV2_FAILED in ps.known_decision_sources()


# ---------------------------------------------------------------------------
# b. Backbone identity + patch-14 geometry
# ---------------------------------------------------------------------------


def test_dinov2_default_backbone_and_geometry() -> None:
    scorer = Dinov2PresenceScorer(device="cpu")
    assert scorer.backbone_model_id == _DINOV2_ID
    assert scorer.patch_size == 14
    assert scorer.input_size == 518  # DINOv2 native, a multiple of 14
    assert scorer.input_size % scorer.patch_size == 0


def test_dinov2_input_size_validation_is_multiple_of_14() -> None:
    assert Dinov2PresenceScorer(device="cpu", input_size=252).input_size == 252  # 18*14
    with pytest.raises(ValueError, match="14"):
        Dinov2PresenceScorer(device="cpu", input_size=256)  # 256 % 14 != 0


def test_dinov2_fingerprint_identity() -> None:
    fp = Dinov2PresenceScorer(device="cpu").prompt_config_fingerprint("batch", None)
    assert fp["scorer"] == "dinov2_floor"
    assert fp["backbone_model_id"] == _DINOV2_ID
    assert fp["patch_size"] == 14
    assert fp["input_size"] == 518
    assert "device" not in fp  # device-independent + secret-free


def test_dinov2_weights_cache_dir_is_distinct_from_dinov3() -> None:
    dv2 = Dinov2PresenceScorer(device="cpu").weights_cache_dir
    dv3 = Dinov3PresenceScorer(device="cpu").weights_cache_dir
    assert dv2 != dv3
    assert "dinov2" in str(dv2)


# ---------------------------------------------------------------------------
# c. Missing-chip abstain -> dinov2_failed (no weights, no stub)
# ---------------------------------------------------------------------------


def test_dinov2_missing_chip_yields_dinov2_failed_without_raising() -> None:
    scorer = Dinov2PresenceScorer(device="cpu")
    obs = scorer.score(
        [Pick(chip_path="/nonexistent/missing.png", capture_date="2020-01-01", index=1)],
        config=None,
    )
    assert len(obs) == 1
    o = obs[0]
    assert o.pv_present is None
    assert o.pv_score is None
    assert o.quality_flag == "missing_chip"
    assert o.decision_source == DECISION_SOURCE_DINOV2_FAILED
    assert o.error is not None
    validate_emission(o.quality_flag, o.decision_source)  # registered in module vocab
    assert scorer._encoder is None  # abstain short-circuits before the backbone


# ---------------------------------------------------------------------------
# d. Calibrated verdict stamps the DINOv2 vocab, not the DINOv3 vocab
# ---------------------------------------------------------------------------


def _write_dinov2_bundle(tmp_path: Path, *, calibration: dict | None) -> Path:
    """A minimal head-bundle pinned to the DINOv2 geometry (patch 14 @ input 518):
    placeholder ``.pt`` bytes + a contract-v1 ``.json`` sidecar. Inference is stubbed
    in the test, so the ``.pt`` is never loaded / integrity-checked."""
    pt = tmp_path / "head.pt"
    pt.write_bytes(b"placeholder")
    sidecar = {
        "schema_version": 1,
        "head_arch": "linear",
        "class_order": ["present", "absent", "unusable"],
        "calibration": calibration,
        "config": {
            "backbone_model_id": _DINOV2_ID,
            "input_size": 518,
            "center_pool_k": 6,
            "upscale_policy": "bilinear",
            "patch_size": 14,
            "chip_render_variant": "nomarker_bilinear518_k6",
        },
        "provenance": {"note": "unit-test dinov2 bundle"},
        "head_pt_sha256": "0" * 64,
    }
    pt.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")
    return pt


@requires_numpy
def test_dinov2_calibrated_present_stamps_dinov2_floor(tmp_path, monkeypatch) -> None:
    """A calibrated DINOv2 bundle stamps decision_source=dinov2_floor (NOT
    dinov3_frozen) on a present verdict — the shared _observe funnel reads the
    class's own vocabulary. The bundle config is adopted (backbone + patch 14)."""
    import numpy as np

    pt = _write_dinov2_bundle(tmp_path, calibration={"lo": 0.46, "hi": 0.50})
    scorer = Dinov2PresenceScorer(device="cpu", head_checkpoint=pt)
    assert scorer.backbone_model_id == _DINOV2_ID  # adopted from bundle config
    assert scorer.patch_size == 14
    assert scorer.input_size == 518
    monkeypatch.setattr(scorer, "_infer_probs", lambda chip_path: np.asarray([0.95, 0.03, 0.02]))
    chip = tmp_path / "chip.png"
    chip.write_bytes(b"exists")
    o = scorer.score(
        [Pick(chip_path=str(chip), capture_date="2020-01-01", index=1)], config=None
    )[0]
    assert o.pv_present is True
    assert o.quality_flag == "usable"
    assert o.decision_source == DECISION_SOURCE_DINOV2_OK
    assert o.pv_score == pytest.approx(0.95)
    assert scorer._encoder is None  # inference stubbed; backbone never built


# ---------------------------------------------------------------------------
# e. Slice-4-style meta-device regression on the subclass
# ---------------------------------------------------------------------------


@requires_torch
def test_dinov2_load_chip_tensor_normalizes_on_model_device(tmp_path) -> None:
    """The inherited CUDA-safe _load_chip_tensor holds for the subclass too: _mean /
    _std live on self.device, so normalization must happen AFTER the chip tensor
    moves there. input_size=14 is a legal patch-14 geometry; torch's 'meta' device
    reproduces the cross-device mismatch CPU-only (cpu - meta raises, meta - meta
    does not)."""
    import torch
    from PIL import Image

    png = tmp_path / "chip.png"
    Image.new("RGB", (8, 8), (120, 90, 60)).save(png)

    scorer = Dinov2PresenceScorer(device="meta", input_size=14, center_pool_k=1)
    scorer._mean = torch.zeros(3, 1, 1, device="meta")
    scorer._std = torch.ones(3, 1, 1, device="meta")

    out = scorer._load_chip_tensor(str(png))  # old order: RuntimeError here
    assert out.device.type == "meta"
    assert tuple(out.shape) == (1, 3, 14, 14)
