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
    assert scorer.quality_flags == frozenset({"usable", "unusable", "missing_chip"})


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
