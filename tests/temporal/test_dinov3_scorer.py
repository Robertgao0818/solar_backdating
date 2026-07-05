"""ISSUE-03 — frozen DINOv3-L-SAT PresenceScorer scaffold tests.

Covers the six acceptance seams:

a. registry / selection flag — ``get_scorer('dinov3_frozen')`` builds a
   protocol-conforming scorer and appears in ``available_scorers()``;
b. import hygiene — importing ``presence_scorer`` (and ``dinov3_scorer`` itself)
   in a fresh subprocess does not pull ``torch``;
c. backbone parity — real weights + real fixture chips: one observation per
   pick, in order, with valid vocabulary (shape/ordering, NOT accuracy);
d. determinism — two independently constructed scorers produce identical
   ``pv_score`` (bit-exact) on the same chips;
e. downstream invariance — the DINOv3 ``.batch`` mapping drives
   ``run_one_anchor`` to a byte-identical ``scan_state.json`` as a Gemini-mapped
   stub emitting identical observations (encoder stubbed, no weights);
f. missing-chip path — a nonexistent ``chip_path`` yields the ``dinov3_failed``
   abstain observation without raising (no weights).

Tests (c)/(d) need the real 303M backbone (first load downloads ~1.2 GB from the
HF hub into ``~/zasolar_data``); they skip cleanly when torch/timm are missing or
the hub is unreachable, but MUST run in the acceptance environment.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.temporal import presence_scorer as ps
from scripts.temporal.dinov3_scorer import (
    DECISION_SOURCE_FAILED,
    DECISION_SOURCE_OK,
    Dinov3PresenceScorer,
    _ChipVerdict,
    load_head_bundle,
)
from scripts.temporal.presence_scorer import (
    Pick,
    PresenceObservation,
    PresenceScorer,
    available_scorers,
    get_scorer,
    validate_emission,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "dinov3_chips"
_FIXTURE_CHIPS = [
    _FIXTURE_DIR / "chip_20090312.png",
    _FIXTURE_DIR / "chip_20090627.png",
    _FIXTURE_DIR / "chip_20090726.png",
]


def _deps_available() -> bool:
    """True iff the heavy backbone deps are importable (does NOT import torch)."""
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "timm", "numpy", "PIL"))


requires_backbone = pytest.mark.skipif(
    not _deps_available(), reason="torch/timm/numpy/PIL not installed"
)


def _fixture_picks() -> list[Pick]:
    return [
        Pick(
            chip_path=str(chip),
            capture_date=chip.stem.replace("chip_", ""),
            version=100 + i,
            actual_zoom=19,
            index=i + 1,
        )
        for i, chip in enumerate(_FIXTURE_CHIPS)
    ]


# ---------------------------------------------------------------------------
# a. Registry / selection flag
# ---------------------------------------------------------------------------


def test_dinov3_frozen_registered_in_available_scorers() -> None:
    assert "dinov3_frozen" in available_scorers()


def test_get_scorer_dinov3_frozen_is_protocol_conforming() -> None:
    scorer = get_scorer("dinov3_frozen", device="cpu")
    assert isinstance(scorer, Dinov3PresenceScorer)
    assert isinstance(scorer, PresenceScorer)  # runtime_checkable protocol
    assert scorer.name == "dinov3_frozen"
    assert scorer.failure_decision_sources == frozenset({DECISION_SOURCE_FAILED})
    assert scorer.decision_sources == frozenset({DECISION_SOURCE_OK, DECISION_SOURCE_FAILED})
    # ISSUE-04: the calibrated abstain band can now emit "ambiguous".
    assert scorer.quality_flags == frozenset({"usable", "ambiguous", "unusable", "missing_chip"})


def test_dinov3_decision_sources_registered_in_module_vocab() -> None:
    # Importing dinov3_scorer must additively register the two new sources so
    # scan_state's write-time gate accepts DINOv3-scored rounds.
    assert DECISION_SOURCE_OK in ps.known_decision_sources()
    assert DECISION_SOURCE_FAILED in ps.known_decision_sources()


def test_dinov3_fingerprint_is_device_independent_placeholder_identity() -> None:
    scorer = get_scorer("dinov3_frozen", device="cpu", head_seed=123, center_pool_k=3, input_size=256)
    fp = scorer.prompt_config_fingerprint("batch", config=None)
    assert fp["scorer"] == "dinov3_frozen"
    assert fp["backbone_model_id"] == "vit_large_patch16_dinov3.sat493m"
    assert fp["head"] == "placeholder_seed:123"
    assert fp["center_pool_k"] == 3
    assert fp["input_size"] == 256
    # Device-independent + secret-free: no device / api key / path leaks.
    assert "device" not in fp


# ---------------------------------------------------------------------------
# b. Import hygiene — presence_scorer (and dinov3_scorer) stay torch-free
# ---------------------------------------------------------------------------


def _subprocess_import_check(module: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    code = (
        f"import sys; import {module}; "
        "sys.exit(0 if 'torch' not in sys.modules else 1)"
    )
    return subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )


def test_importing_presence_scorer_does_not_import_torch() -> None:
    result = _subprocess_import_check("scripts.temporal.presence_scorer")
    assert result.returncode == 0, (
        "importing presence_scorer pulled torch:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_importing_dinov3_scorer_does_not_import_torch() -> None:
    result = _subprocess_import_check("scripts.temporal.dinov3_scorer")
    assert result.returncode == 0, (
        "importing dinov3_scorer pulled torch:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# c. Backbone parity (real weights, real fixture chips) — shape / ordering
# ---------------------------------------------------------------------------


def _score_fixture_or_skip(scorer: Dinov3PresenceScorer) -> list[PresenceObservation]:
    try:
        return scorer.score(_fixture_picks(), config=None)
    except Exception as exc:  # hub unreachable / weights unavailable → skip, not fail
        name = type(exc).__name__
        if any(t in name for t in ("Connection", "HTTP", "Timeout", "OSError", "URL")):
            pytest.skip(f"DINOv3 weights unavailable/offline: {name}: {exc}")
        raise


@requires_backbone
def test_backbone_parity_shape_and_ordering() -> None:
    scorer = Dinov3PresenceScorer(device="cpu")
    obs = _score_fixture_or_skip(scorer)

    assert len(obs) == len(_FIXTURE_CHIPS)
    assert [o.index for o in obs] == [1, 2, 3]
    assert [o.capture_date for o in obs] == ["20090312", "20090627", "20090726"]
    for o in obs:
        assert o.pv_present in (True, False, None)
        assert isinstance(o.pv_score, float)
        assert o.pv_score == o.pv_score  # finite (not NaN)
        assert float("-inf") < o.pv_score < float("inf")
        assert o.decision_source == DECISION_SOURCE_OK
        # Vocabulary is valid at the scan_state write gate.
        validate_emission(o.quality_flag, o.decision_source)


# ---------------------------------------------------------------------------
# d. Determinism — two independent scorers, bit-identical pv_score
# ---------------------------------------------------------------------------


@requires_backbone
def test_two_independent_scorers_produce_identical_scores() -> None:
    scorer_a = Dinov3PresenceScorer(device="cpu")
    obs_a = _score_fixture_or_skip(scorer_a)
    scorer_b = Dinov3PresenceScorer(device="cpu")
    obs_b = _score_fixture_or_skip(scorer_b)

    assert [o.pv_score for o in obs_a] == [o.pv_score for o in obs_b]
    assert [o.pv_present for o in obs_a] == [o.pv_present for o in obs_b]
    assert [o.quality_flag for o in obs_a] == [o.quality_flag for o in obs_b]


# ---------------------------------------------------------------------------
# f. Missing-chip path — abstain without raising (no weights, no stub needed)
# ---------------------------------------------------------------------------


def test_missing_chip_yields_dinov3_failed_abstain_without_raising() -> None:
    scorer = Dinov3PresenceScorer(device="cpu")
    picks = [
        Pick(chip_path="/nonexistent/does_not_exist.png", capture_date="2020-01-01", index=1),
        Pick(chip_path="", capture_date="2021-01-01", index=2),
    ]
    obs = scorer.score(picks, config=None)

    assert len(obs) == 2
    for o in obs:
        assert o.pv_present is None
        assert o.pv_score is None
        assert o.quality_flag == "missing_chip"
        assert o.decision_source == DECISION_SOURCE_FAILED
        assert o.error is not None
        validate_emission(o.quality_flag, o.decision_source)
    assert [o.index for o in obs] == [1, 2]


def test_missing_chip_does_not_load_weights() -> None:
    """The abstain path short-circuits before touching the backbone."""
    scorer = Dinov3PresenceScorer(device="cpu")
    scorer.score([Pick(chip_path="/nope.png", capture_date="2020-01-01", index=1)], config=None)
    assert scorer._encoder is None  # model never built for an all-missing batch


# ---------------------------------------------------------------------------
# e. Downstream invariance — DINOv3 .batch mapping vs Gemini-mapped stub
# ---------------------------------------------------------------------------
#
# Prior art: tests/temporal/test_seam_downstream_invariance.py drives
# run_one_anchor with two stub scorers emitting byte-identical observations and
# asserts byte-identical scan_state.json. Here one side is a *real*
# Dinov3PresenceScorer with its encoder/head inference (`_observe`) monkeypatched
# to emit canned values; the other is a Gemini-mapped stub emitting the identical
# GeminiObservation. If the DINOv3 observation-mapping code path added, lost, or
# reordered anything downstream, the bytes would diverge.

from scripts.temporal import gehi_common as _gehi_common  # noqa: E402
from scripts.temporal import gehi_download as _gehi_download  # noqa: E402
from scripts.temporal import run_adaptive_scan as ras  # noqa: E402
from scripts.temporal import scan_state as scan_state_mod  # noqa: E402
from scripts.temporal.gehi_download import DownloadResult  # noqa: E402
from scripts.temporal.run_adaptive_scan import VintageCatalog, run_one_anchor  # noqa: E402
from scripts.temporal.scan_config import AdaptiveScanConfig  # noqa: E402
from scripts.temporal.scan_decision import VintageEntry  # noqa: E402
from scripts.validation.gemini_solar_image_review import GeminiObservation  # noqa: E402

# Canned verdict both sides emit (identical answers). "dinov3_frozen" is already
# registered by importing dinov3_scorer above.
_CANNED_PRESENT = True
_CANNED_CONF = 0.9
_CANNED_QF = "usable"
_CANNED_DS = DECISION_SOURCE_OK


class _GeminiMappedStub:
    """A minimal PresenceScorer whose ``.batch`` emits the canned observation the
    DINOv3 scorer would map to — the reference side of the invariance check."""

    name = "gemini_mapped_stub"
    failure_decision_sources = frozenset({DECISION_SOURCE_FAILED})
    quality_flags = frozenset({_CANNED_QF})
    decision_sources = frozenset({_CANNED_DS})

    def batch(self, picks, *, config, audit_writer=None, census_mid_date_iso=None,
              routing_salt=None, **_kwargs):
        return [
            GeminiObservation(
                chip_index=p.chip_index,
                pv_present=_CANNED_PRESENT,
                confidence=_CANNED_CONF,
                quality_flag=_CANNED_QF,
                evidence="",
                notes="",
                decision_source=_CANNED_DS,
            )
            for p in picks
        ]

    def score(self, picks, *, config, **_kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def _ok_download(path: Path) -> DownloadResult:
    return DownloadResult(
        anchor_id="a",
        capture_date="2020-06-15",
        version="100",
        requested_zoom_ladder=(20, 19),
        actual_zoom=20,
        path=path,
        sha256="deadbeef",
        status="ok",
        error=None,
        gehi_command="",
        download_stdout_sha256="",
    )


def _install_gehi_stubs(monkeypatch, tmp_path: Path, vintages: list[VintageEntry]) -> None:
    def fake_catalog(anchor, config):
        return VintageCatalog(
            vintages=list(vintages),
            available_dates_by_zoom={19: {v.capture_date for v in vintages}},
        )

    def fake_download(anchor, *, capture_date, version, zoom_ladder, output_root,
                      provider="TM", vintage_check=None):
        p = tmp_path / f"chip_{capture_date}.tif"
        p.write_bytes(b"TIF")
        return _ok_download(p)

    monkeypatch.setattr(ras, "_fetch_real_vintage_catalog", fake_catalog)
    monkeypatch.setattr(
        ras, "make_vintage_check",
        lambda anchor, *, available_dates_by_zoom, config: (lambda z, d: True),
    )
    monkeypatch.setattr(_gehi_download, "download_chip_with_zoom_ladder", fake_download)
    monkeypatch.setattr(_gehi_common, "ensure_review_png", lambda p: Path(str(p)).with_suffix(".png"))


def _run_and_read_state(scorer, subdir: str, tmp_path: Path, monkeypatch) -> bytes:
    vintages = [
        VintageEntry(capture_date="2020-06-15", version=100),
        VintageEntry(capture_date="2021-06-15", version=101),
    ]
    _install_gehi_stubs(monkeypatch, tmp_path, vintages)
    scan_states_dir = tmp_path / subdir
    run_one_anchor(
        {"anchor_id": "dinov3_invariance", "region_key": "johannesburg", "grid_id": "G1"},
        AdaptiveScanConfig(),
        scan_states_dir,
        dry_run=False,
        force_restart=True,
        chips_dir=tmp_path / f"chips_{subdir}",
        audit_dir=tmp_path / f"audit_{subdir}",
        gemini_config=object(),
        scorer=scorer,
        census_mid_date_iso=None,
    )
    return (scan_states_dir / "dinov3_invariance.json").read_bytes()


def test_dinov3_mapping_matches_gemini_mapped_scan_state(tmp_path: Path, monkeypatch) -> None:
    """Canned DINOv3-mapped observations produce the same scan_state.json as the
    Gemini-mapped path for identical observation values (encoder stubbed)."""
    monkeypatch.setattr(scan_state_mod, "now_iso", lambda: "2020-01-01T00:00:00Z")

    # DINOv3 side: real scorer, inference stubbed to the canned verdict.
    dino = Dinov3PresenceScorer(device="cpu")
    monkeypatch.setattr(
        dino,
        "_observe",
        lambda chip_path: _ChipVerdict(_CANNED_PRESENT, _CANNED_CONF, _CANNED_QF, _CANNED_DS),
    )
    bytes_dino = _run_and_read_state(dino, "run_dino", tmp_path, monkeypatch)

    # Reference side: a Gemini-mapped stub emitting the identical observation.
    bytes_gemini = _run_and_read_state(_GeminiMappedStub(), "run_gemini", tmp_path, monkeypatch)

    assert dino._encoder is None, "downstream-invariance path must not load real weights"
    assert bytes_dino == bytes_gemini, (
        "DINOv3 observation-mapping diverged from the Gemini-mapped path: the "
        "mapping added/lost/reordered something downstream"
    )


# ===========================================================================
# ISSUE-04 — calibrated head-bundle wiring (Writer B)
# ===========================================================================
#
# All the tests below stay CPU-only, weights-free and fast: the real backbone is
# never built. The head-bundle contract (torch-free json sidecar + a torch .pt)
# lets construction, sentinel resolution, calibration mapping, the fingerprint and
# the integrity check all run without a single timm download. Only the two helpers
# that genuinely need torch (load_head_bundle round-trip, embed_chips batched
# forward against a fake encoder) are gated on torch/numpy being importable.

import hashlib  # noqa: E402
import json  # noqa: E402


def _mods_available(*mods: str) -> bool:
    return all(importlib.util.find_spec(m) is not None for m in mods)


requires_torch = pytest.mark.skipif(
    not _mods_available("torch", "numpy"), reason="torch/numpy not installed"
)
requires_numpy = pytest.mark.skipif(
    not _mods_available("numpy"), reason="numpy not installed"
)
requires_pil = pytest.mark.skipif(
    not _mods_available("PIL"), reason="PIL not installed"
)

# A valid bundle config (adopted when the ctor leaves the sentinels at None).
_BUNDLE_CONFIG = {
    "backbone_model_id": "vit_large_patch16_dinov3.sat493m",
    "input_size": 256,
    "center_pool_k": 3,
    "upscale_policy": "bilinear",
    "chip_render_variant": "marker_free_tight12",
}
_CALIB = {
    "lo": 0.4,
    "hi": 0.7,
    "rule": "global_band",
    "calib_anchors": 120,
    "decided_agreement": 0.93,
    "abstain_rate": 0.11,
}


def _write_bundle(
    tmp_path: Path,
    *,
    stem: str = "head",
    calibration: dict | None,
    config: dict | None = None,
    head_pt_sha256: str = "0" * 64,
    pt_bytes: bytes | None = b"placeholder-pt-bytes",
    write_sidecar: bool = True,
    class_order: list | None = None,
) -> Path:
    """Write a head-bundle (<stem>.pt [+ <stem>.json]); return the .pt path.

    When ``pt_bytes`` is not None a raw .pt file is written (opaque bytes are fine
    for every test that stubs inference / never triggers ``load_head_bundle``).
    ``write_sidecar=False`` produces a legacy checkpoint (no sidecar => meta None).
    """
    pt_path = tmp_path / f"{stem}.pt"
    if pt_bytes is not None:
        pt_path.write_bytes(pt_bytes)
    if write_sidecar:
        sidecar = {
            "schema_version": 1,
            "head_arch": "linear",
            "class_order": (
                class_order if class_order is not None else ["present", "absent", "unusable"]
            ),
            "calibration": calibration,
            "config": config if config is not None else dict(_BUNDLE_CONFIG),
            "provenance": {"note": "unit-test bundle"},
            "head_pt_sha256": head_pt_sha256,
        }
        pt_path.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")
    return pt_path


# ---------------------------------------------------------------------------
# Calibrated band mapping — all four outcomes + pv_score == P(present)
# ---------------------------------------------------------------------------


@requires_numpy
@pytest.mark.parametrize(
    "probs,exp_present,exp_flag,exp_score",
    [
        ([0.95, 0.03, 0.02], True, "usable", 0.95),      # P(present) > hi
        ([0.05, 0.93, 0.02], False, "usable", 0.05),     # P(present) < lo
        ([0.55, 0.42, 0.03], None, "ambiguous", 0.55),   # lo <= P(present) <= hi
        ([0.30, 0.10, 0.60], None, "unusable", 0.30),    # argmax == unusable
    ],
)
def test_calibrated_band_mapping(
    tmp_path, monkeypatch, probs, exp_present, exp_flag, exp_score
) -> None:
    import numpy as np

    pt = _write_bundle(tmp_path, calibration=_CALIB)
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    # Inference stubbed → the real encoder/head is never built (no .pt load).
    monkeypatch.setattr(
        scorer, "_infer_probs", lambda chip_path: np.asarray(probs, dtype=np.float64)
    )
    chip = tmp_path / "chip.png"
    chip.write_bytes(b"exists")
    obs = scorer.score(
        [Pick(chip_path=str(chip), capture_date="2020-01-01", index=1)], config=None
    )
    assert len(obs) == 1
    o = obs[0]
    assert o.pv_present is exp_present
    assert o.quality_flag == exp_flag
    assert o.decision_source == DECISION_SOURCE_OK
    # pv_score is P(present) in EVERY branch (including the unusable one).
    assert o.pv_score == pytest.approx(exp_score)
    assert scorer._encoder is None  # never built the backbone


@requires_numpy
def test_calibrated_band_boundaries_are_ambiguous(tmp_path, monkeypatch) -> None:
    """p == lo and p == hi land in the abstain band (strict > hi / < lo)."""
    import numpy as np

    pt = _write_bundle(tmp_path, calibration=_CALIB)
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    chip = tmp_path / "chip.png"
    chip.write_bytes(b"exists")
    for boundary in (_CALIB["lo"], _CALIB["hi"]):
        monkeypatch.setattr(
            scorer,
            "_infer_probs",
            lambda chip_path, b=boundary: np.asarray([b, 1 - b - 0.01, 0.01], dtype=np.float64),
        )
        o = scorer.score(
            [Pick(chip_path=str(chip), capture_date="2020-01-01", index=1)], config=None
        )[0]
        assert o.pv_present is None
        assert o.quality_flag == "ambiguous"


@requires_numpy
def test_uncalibrated_bundle_keeps_argmax_behavior(tmp_path, monkeypatch) -> None:
    """A bundle whose calibration is null falls back to today's argmax mapping."""
    import numpy as np

    pt = _write_bundle(tmp_path, calibration=None)
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    assert scorer._calibration is None
    chip = tmp_path / "chip.png"
    chip.write_bytes(b"exists")
    # [0.30, 0.10, 0.60] → argmax==unusable regardless of any band; present-prob 0.30.
    monkeypatch.setattr(
        scorer, "_infer_probs", lambda chip_path: np.asarray([0.30, 0.10, 0.60], dtype=np.float64)
    )
    o = scorer.score(
        [Pick(chip_path=str(chip), capture_date="2020-01-01", index=1)], config=None
    )[0]
    assert o.pv_present is None
    assert o.quality_flag == "unusable"
    assert o.pv_score == pytest.approx(0.30)
    # A present-argmax chip maps to (True, "usable") with no abstain band applied.
    monkeypatch.setattr(
        scorer, "_infer_probs", lambda chip_path: np.asarray([0.51, 0.30, 0.19], dtype=np.float64)
    )
    o2 = scorer.score(
        [Pick(chip_path=str(chip), capture_date="2020-01-01", index=1)], config=None
    )[0]
    assert o2.pv_present is True
    assert o2.quality_flag == "usable"


# ---------------------------------------------------------------------------
# Sidecar adoption / conflict / missing-sidecar legacy behavior
# ---------------------------------------------------------------------------


def test_bundle_config_adopted_when_ctor_sentinels_none(tmp_path) -> None:
    cfg = {
        "backbone_model_id": "m",
        "input_size": 128,
        "center_pool_k": 5,
        "upscale_policy": "bicubic",
        "chip_render_variant": "v",
    }
    pt = _write_bundle(tmp_path, calibration=_CALIB, config=cfg)
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    assert scorer.input_size == 128
    assert scorer.center_pool_k == 5
    assert scorer.upscale_policy == "bicubic"
    assert scorer._calibration is not None


def test_bundle_config_conflict_raises_input_size(tmp_path) -> None:
    pt = _write_bundle(
        tmp_path,
        calibration=None,
        config={**_BUNDLE_CONFIG, "input_size": 128},
    )
    with pytest.raises(ValueError, match="input_size"):
        Dinov3PresenceScorer(device="cpu", head_checkpoint=pt, input_size=256)


def test_bundle_config_conflict_raises_upscale_policy(tmp_path) -> None:
    pt = _write_bundle(
        tmp_path,
        calibration=None,
        config={**_BUNDLE_CONFIG, "upscale_policy": "bicubic"},
    )
    with pytest.raises(ValueError, match="upscale_policy"):
        Dinov3PresenceScorer(device="cpu", head_checkpoint=pt, upscale_policy="bilinear")


def test_explicit_ctor_arg_matching_bundle_is_accepted(tmp_path) -> None:
    """An explicit ctor value equal to the bundle config is NOT a conflict."""
    pt = _write_bundle(tmp_path, calibration=None, config={**_BUNDLE_CONFIG, "center_pool_k": 3})
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt, center_pool_k=3)
    assert scorer.center_pool_k == 3


def test_bundle_config_adopts_backbone_model_id(tmp_path) -> None:
    """A bundle recording a non-default backbone is adopted when the ctor leaves
    backbone_model_id unset — the head is scored on the backbone it was trained on,
    and the fingerprint reports THAT backbone (not the module default). Symmetric
    with the geometry keys, per the head-bundle contract v1 config. The adopted
    backbone also drives the patch-size derivation, so its geometry (input 518, a
    multiple of 14) must be patch-consistent — a DINOv2-shaped adoption case."""
    cfg = {
        **_BUNDLE_CONFIG,
        "backbone_model_id": "vit_small_patch14_dinov2.other",
        "input_size": 518,
    }
    pt = _write_bundle(tmp_path, calibration=_CALIB, config=cfg)
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    assert scorer.backbone_model_id == "vit_small_patch14_dinov2.other"
    assert scorer.patch_size == 14  # derived from the adopted backbone id
    fp = scorer.prompt_config_fingerprint("batch", None)
    assert fp["backbone_model_id"] == "vit_small_patch14_dinov2.other"


def test_bundle_config_conflict_raises_backbone_model_id(tmp_path) -> None:
    """An explicit backbone disagreeing with the bundle's is a construction error —
    the same adopt-or-conflict guard the geometry keys already get."""
    pt = _write_bundle(
        tmp_path,
        calibration=None,
        config={**_BUNDLE_CONFIG, "backbone_model_id": "some_other_backbone"},
    )
    with pytest.raises(ValueError, match="backbone_model_id"):
        Dinov3PresenceScorer(
            device="cpu",
            head_checkpoint=pt,
            backbone_model_id="vit_large_patch16_dinov3.sat493m",
        )


def test_explicit_backbone_matching_bundle_is_accepted(tmp_path) -> None:
    pt = _write_bundle(tmp_path, calibration=None, config={**_BUNDLE_CONFIG, "backbone_model_id": "bb"})
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt, backbone_model_id="bb")
    assert scorer.backbone_model_id == "bb"


def test_bundle_wrong_class_order_raises(tmp_path) -> None:
    """Contract v1 fixes the logit order (present/absent/unusable); _observe maps
    by index. A bundle sidecar declaring any other order would be silently
    mis-mapped on every verdict, so construction must reject it (torch-free)."""
    pt = _write_bundle(
        tmp_path, calibration=_CALIB, class_order=["absent", "present", "unusable"]
    )
    with pytest.raises(ValueError, match="class_order"):
        Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)


def test_bundle_correct_class_order_is_accepted(tmp_path) -> None:
    """The contract order passes the guard (regression: guard must not false-positive)."""
    pt = _write_bundle(
        tmp_path, calibration=_CALIB, class_order=["present", "absent", "unusable"]
    )
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    assert scorer._calib_lo is not None


def test_no_bundle_backbone_resolves_to_default(tmp_path) -> None:
    scorer = Dinov3PresenceScorer(device="cpu")
    assert scorer.backbone_model_id == "vit_large_patch16_dinov3.sat493m"


def test_missing_sidecar_is_legacy_behavior(tmp_path) -> None:
    pt = _write_bundle(tmp_path, calibration=None, write_sidecar=False)
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    assert scorer._head_meta is None
    assert scorer._calibration is None
    # Sentinels resolve to module defaults with no bundle to read.
    assert scorer.input_size == 256
    assert scorer.center_pool_k == 3
    assert scorer.upscale_policy == "bilinear"
    fp = scorer.prompt_config_fingerprint("batch", None)
    assert fp["head"] == f"checkpoint:{pt}"
    assert "head_sha256" not in fp
    assert "calibration" not in fp


# ---------------------------------------------------------------------------
# upscale_policy validation + PIL resampling selection
# ---------------------------------------------------------------------------


def test_invalid_upscale_policy_raises() -> None:
    with pytest.raises(ValueError, match="upscale_policy"):
        Dinov3PresenceScorer(device="cpu", upscale_policy="nearest")


def test_valid_upscale_policies_accepted() -> None:
    assert Dinov3PresenceScorer(device="cpu", upscale_policy="bilinear").upscale_policy == "bilinear"
    assert Dinov3PresenceScorer(device="cpu", upscale_policy="bicubic").upscale_policy == "bicubic"


@requires_pil
def test_pil_resample_selects_policy() -> None:
    from PIL import Image

    assert Dinov3PresenceScorer(device="cpu", upscale_policy="bilinear")._pil_resample() == Image.BILINEAR
    assert Dinov3PresenceScorer(device="cpu", upscale_policy="bicubic")._pil_resample() == Image.BICUBIC


# ---------------------------------------------------------------------------
# Fingerprint: always upscale_policy; bundle adds head_sha256 + calibration
# ---------------------------------------------------------------------------


def test_fingerprint_placeholder_has_upscale_policy_no_bundle_keys() -> None:
    scorer = Dinov3PresenceScorer(device="cpu")
    fp = scorer.prompt_config_fingerprint("batch", None)
    assert fp["upscale_policy"] == "bilinear"
    assert fp["head"] == f"placeholder_seed:{scorer.head_seed}"
    assert "head_sha256" not in fp
    assert "calibration" not in fp
    assert "device" not in fp  # stays device-independent


def test_fingerprint_bundle_carries_head_sha256_and_calibration(tmp_path) -> None:
    pt = _write_bundle(tmp_path, calibration=_CALIB, head_pt_sha256="cafef00d")
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    fp = scorer.prompt_config_fingerprint("batch", None)
    assert fp["upscale_policy"] == "bilinear"  # adopted from bundle config
    assert fp["head_sha256"] == "cafef00d"
    assert fp["calibration"] == {"lo": 0.4, "hi": 0.7}
    assert "device" not in fp


def test_fingerprint_bundle_null_calibration(tmp_path) -> None:
    pt = _write_bundle(tmp_path, calibration=None, head_pt_sha256="beadfeed")
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    fp = scorer.prompt_config_fingerprint("batch", None)
    assert fp["head_sha256"] == "beadfeed"
    assert fp["calibration"] is None


# ---------------------------------------------------------------------------
# sha256 integrity check (torch-free; raises before the backbone build)
# ---------------------------------------------------------------------------


def test_head_bundle_sha256_mismatch_raises_naming_both(tmp_path) -> None:
    content = b"real-head-bytes"
    pt = _write_bundle(tmp_path, calibration=None, head_pt_sha256="0" * 64, pt_bytes=content)
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    with pytest.raises(ValueError) as ei:
        scorer._ensure_model()  # integrity check fires before any timm/torch import
    msg = str(ei.value)
    actual = hashlib.sha256(content).hexdigest()
    assert actual in msg  # names the computed hash
    assert "0" * 64 in msg  # names the expected (sidecar) hash
    assert scorer._encoder is None  # never reached the backbone build


def test_head_bundle_sha256_match_passes(tmp_path) -> None:
    content = b"real-head-bytes"
    good = hashlib.sha256(content).hexdigest()
    pt = _write_bundle(tmp_path, calibration=None, head_pt_sha256=good, pt_bytes=content)
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    scorer._verify_head_integrity()  # torch-free, must not raise


def test_legacy_checkpoint_skips_integrity_check(tmp_path) -> None:
    """No sidecar → nothing to verify; _verify_head_integrity is a no-op."""
    pt = _write_bundle(tmp_path, calibration=None, write_sidecar=False)
    scorer = Dinov3PresenceScorer(device="cpu", head_checkpoint=pt)
    scorer._verify_head_integrity()  # must not raise (meta None)


# ---------------------------------------------------------------------------
# load_head_bundle helper — real torch round-trip (bundle + legacy)
# ---------------------------------------------------------------------------


@requires_torch
def test_load_head_bundle_reads_state_dict_and_meta(tmp_path) -> None:
    import torch
    from torch import nn

    head = nn.Linear(8, 3)
    pt = tmp_path / "head.pt"
    torch.save(
        {"schema_version": 1, "head_arch": "linear", "state_dict": head.state_dict()}, pt
    )
    _write_bundle(
        tmp_path, calibration=_CALIB, head_pt_sha256="x", pt_bytes=None
    )  # writes only the sidecar next to head.pt
    state, meta = load_head_bundle(pt)
    assert set(state.keys()) == {"weight", "bias"}
    assert state["weight"].shape == (3, 8)
    assert meta is not None
    assert meta["head_arch"] == "linear"
    assert meta["calibration"]["lo"] == 0.4


@requires_torch
def test_load_head_bundle_legacy_raw_state_dict_meta_none(tmp_path) -> None:
    import torch
    from torch import nn

    head = nn.Linear(8, 3)
    pt = tmp_path / "legacy.pt"
    torch.save(head.state_dict(), pt)  # raw state_dict, no wrapper, no sidecar
    state, meta = load_head_bundle(pt)
    assert set(state.keys()) == {"weight", "bias"}
    assert meta is None


# ---------------------------------------------------------------------------
# embed_chips — batched frozen forward (fake encoder, no weights)
# ---------------------------------------------------------------------------


class _FakeEncoder:
    """A stand-in encoder: patch tokens carry the per-chip input mean, so the
    center-pooled embedding for chip i is a constant row we can assert on."""

    num_features = 8
    num_prefix_tokens = 1

    def forward_features(self, batch):  # batch: [B, 3, H, W]
        import torch

        b = batch.shape[0]
        sig = batch.reshape(b, -1).mean(dim=1).reshape(b, 1, 1)  # [B,1,1]
        patch = sig * torch.ones(b, 16, self.num_features)  # 4x4 grid
        prefix = torch.zeros(b, self.num_prefix_tokens, self.num_features)
        return torch.cat([prefix, patch], dim=1)  # [B, 1+16, C]


def _install_fake_encoder(scorer, monkeypatch) -> None:
    def fake_ensure() -> None:
        scorer._encoder = _FakeEncoder()

    monkeypatch.setattr(scorer, "_ensure_model", fake_ensure)


@requires_torch
def test_embed_chips_shape_dtype_order_and_batching(tmp_path, monkeypatch) -> None:
    import numpy as np
    import torch

    scorer = Dinov3PresenceScorer(device="cpu", center_pool_k=3)
    _install_fake_encoder(scorer, monkeypatch)

    def fake_load(path):
        idx = int(str(path).split("_")[-1].split(".")[0])
        return torch.full((1, 3, 4, 4), float(idx + 1))  # per-chip constant

    monkeypatch.setattr(scorer, "_load_chip_tensor", fake_load)

    paths = [f"/x/chip_{i}.png" for i in range(5)]
    out = scorer.embed_chips(paths, batch_size=2)  # 3 batches: 2,2,1

    assert isinstance(out, np.ndarray)
    assert out.dtype == np.float32
    assert out.shape == (5, 8)
    # Order preserved across batch boundaries: row i is a constant (i+1) vector.
    for i in range(5):
        assert np.allclose(out[i], float(i + 1)), out[i]


@requires_torch
def test_embed_chips_raises_on_unreadable_chip(tmp_path, monkeypatch) -> None:
    """Training-time strictness: embed_chips raises (contrast with _observe abstain)."""
    scorer = Dinov3PresenceScorer(device="cpu")
    _install_fake_encoder(scorer, monkeypatch)

    def bad_load(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(scorer, "_load_chip_tensor", bad_load)
    with pytest.raises(FileNotFoundError):
        scorer.embed_chips(["/x/missing.png"])


@requires_torch
def test_load_chip_tensor_normalizes_on_model_device(tmp_path, monkeypatch) -> None:
    """Regression (slice 4, found on the first real CUDA run): _mean/_std live on
    self.device, so normalization must happen AFTER the chip tensor moves there.
    The old order (normalize on CPU, then .to(device)) raised a cross-device
    RuntimeError on any CUDA scorer. torch's 'meta' device reproduces the
    mismatch CPU-only: cpu_tensor - meta_tensor raises, meta - meta does not."""
    import torch
    from PIL import Image

    png = tmp_path / "chip.png"
    Image.new("RGB", (8, 8), (120, 90, 60)).save(png)

    scorer = Dinov3PresenceScorer(device="meta", input_size=16, center_pool_k=1)
    scorer._mean = torch.zeros(3, 1, 1, device="meta")
    scorer._std = torch.ones(3, 1, 1, device="meta")

    out = scorer._load_chip_tensor(str(png))  # old order: RuntimeError here
    assert out.device.type == "meta"
    assert tuple(out.shape) == (1, 3, 16, 16)


# ===========================================================================
# ISSUE-05 — patch-size generalization (shared machinery for the DINOv2 floor)
# ===========================================================================
#
# The scorer must DERIVE the encoder's patch size from the backbone id (both timm
# ids embed it: vit_large_patch16_* -> 16, vit_small_patch14_* -> 14) and validate
# input_size against THAT, not a hardcoded 16. The DINOv3 path (patch 16) must stay
# byte-identical; the SAME base class pointed at the DINOv2 id (patch 14) must newly
# accept multiples of 14 and reject non-multiples. All construction-time / torch-free
# except the two tests explicitly gated on torch.

_DINOV2_ID = "vit_small_patch14_dinov2.lvd142m"


def test_infer_patch_size_from_backbone_id() -> None:
    from scripts.temporal.dinov3_scorer import _infer_patch_size

    assert _infer_patch_size("vit_large_patch16_dinov3.sat493m") == 16
    assert _infer_patch_size(_DINOV2_ID) == 14
    # No patchNN token -> falls back to 16 (DINOv3-L-SAT's), never crashes.
    assert _infer_patch_size("some_backbone_without_a_patch_token") == 16


def test_input_size_validation_patch16_is_byte_identical() -> None:
    # DINOv3 path unchanged: multiples of 16 accepted, non-multiples / <=0 rejected.
    assert Dinov3PresenceScorer(device="cpu", input_size=256).input_size == 256
    assert Dinov3PresenceScorer(device="cpu", input_size=512).input_size == 512
    with pytest.raises(ValueError, match="16"):
        Dinov3PresenceScorer(device="cpu", input_size=252)  # 252 % 16 != 0
    with pytest.raises(ValueError, match="multiple of the patch size"):
        Dinov3PresenceScorer(device="cpu", input_size=0)


def test_default_backbone_derives_patch16() -> None:
    assert Dinov3PresenceScorer(device="cpu").patch_size == 16


def test_input_size_validation_tracks_patch14_for_dinov2_backbone() -> None:
    # The SAME base class, pointed at the DINOv2 backbone id, derives patch 14 and
    # validates against it — proving the generalization lives in shared machinery.
    scorer = Dinov3PresenceScorer(device="cpu", backbone_model_id=_DINOV2_ID, input_size=518)
    assert scorer.patch_size == 14
    assert scorer.input_size == 518
    with pytest.raises(ValueError, match="14"):
        Dinov3PresenceScorer(device="cpu", backbone_model_id=_DINOV2_ID, input_size=256)


def test_fingerprint_records_patch_size() -> None:
    fp16 = Dinov3PresenceScorer(device="cpu").prompt_config_fingerprint("batch", None)
    assert fp16["patch_size"] == 16
    fp14 = Dinov3PresenceScorer(
        device="cpu", backbone_model_id=_DINOV2_ID, input_size=518
    ).prompt_config_fingerprint("batch", None)
    assert fp14["patch_size"] == 14


@requires_torch
def test_center_pool_math_at_patch14_grid_and_embed_dim_384() -> None:
    """center-pool k×k math is correct on a 37×37 patch-token grid (DINOv2 @ input
    518) with embed_dim 384 — the shared _center_pool derives the grid from the token
    count, so patch 14 needs no code change beyond re-tuning k as a value."""
    import numpy as np
    import torch

    grid, k, channels = 37, 6, 384
    scorer = Dinov3PresenceScorer(
        device="cpu", backbone_model_id=_DINOV2_ID, input_size=518, center_pool_k=k
    )
    # token (r, c) carries the scalar (r*grid + c) broadcast across all 384 channels.
    vals = torch.arange(grid * grid, dtype=torch.float32).reshape(grid, grid)
    tokens = vals.reshape(1, grid * grid, 1).expand(1, grid * grid, channels).contiguous()
    pooled = scorer._center_pool(tokens)  # [1, 384]
    assert tuple(pooled.shape) == (1, channels)
    start = (grid - k) // 2  # 15
    expected = float(vals[start : start + k, start : start + k].mean())
    assert np.allclose(pooled.numpy(), expected)


@requires_torch
def test_ensure_model_passes_dynamic_img_size_true(tmp_path, monkeypatch) -> None:
    """The landmine: DINOv2 defaults dynamic_img_size=False and hard-crashes at any
    non-native input; DINOv3-L-SAT already defaults True. Passing it explicitly is
    byte-identical for DINOv3 and unblocks DINOv2 at the head's pinned input size.
    Weight-free: timm.create_model is stubbed so nothing downloads."""
    import timm

    captured: dict = {}

    class _FakeEnc:
        num_features = 8

        def requires_grad_(self, _flag):
            return self

        def eval(self):
            return self

        def to(self, _device):
            return self

    def fake_create_model(model_id, **kw):
        captured["model_id"] = model_id
        captured.update(kw)
        return _FakeEnc()

    monkeypatch.setattr(timm, "create_model", fake_create_model)
    monkeypatch.setattr(
        timm.data,
        "resolve_model_data_config",
        lambda enc: {"mean": (0.5, 0.5, 0.5), "std": (0.5, 0.5, 0.5)},
    )
    scorer = Dinov3PresenceScorer(device="cpu", weights_cache_dir=tmp_path)
    scorer._ensure_model()
    assert captured["dynamic_img_size"] is True
    assert captured["model_id"] == "vit_large_patch16_dinov3.sat493m"
