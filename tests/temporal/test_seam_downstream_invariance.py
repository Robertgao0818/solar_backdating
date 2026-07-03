"""ISSUE-05 PresenceScorer seam — cross-implementation downstream invariance.

The seam's core promise is *structure changes, answers don't*: which scorer
implementation produced an observation must be irrelevant to the persisted
result. This test proves that at the strongest level the acceptance criterion
asks for — TWO DIFFERENT ``PresenceScorer`` implementations (distinct ``name``,
distinct declared vocab, distinct declared ``failure_decision_sources``) that
return byte-for-byte identical observations drive ``run_one_anchor``'s real path
to **byte-identical** ``scan_state.json`` files.

This is stronger than the single-scorer determinism check in
``test_run_adaptive_scan_seam.py`` (same scorer twice): here the two byte
streams come from genuinely different objects, so any leakage of scorer identity
(name, failure-source set, vocab) into the persisted state would diverge the
bytes and fail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.temporal import gehi_common as _gehi_common
from scripts.temporal import gehi_download as _gehi_download
from scripts.temporal import presence_scorer as ps
from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal import scan_state as scan_state_mod
from scripts.temporal.gehi_download import DownloadResult
from scripts.temporal.run_adaptive_scan import VintageCatalog, run_one_anchor
from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_decision import VintageEntry
from scripts.validation.gemini_solar_image_review import GeminiObservation

# The observation vocabulary both scorers emit (identical answers). Register the
# non-seeded decision_source once so save_scan_state's write-time gate accepts it.
_OBS_DECISION_SOURCE = "stubscorer_ok"
_OBS_QUALITY_FLAG = "usable"  # seeded
ps.register_decision_source(_OBS_DECISION_SOURCE)


class _IdenticalObsScorer:
    """A PresenceScorer whose .batch emits a fixed, implementation-independent
    observation for every pick. Two instances with different identity metadata
    (name / vocab / failure sources) still emit identical observations."""

    def __init__(self, *, name: str, failure_decision_sources: frozenset[str]) -> None:
        self.name = name
        self.failure_decision_sources = failure_decision_sources
        self.quality_flags = frozenset({_OBS_QUALITY_FLAG})
        self.decision_sources = frozenset({_OBS_DECISION_SOURCE})
        self.batch_calls: list[list[Any]] = []

    def batch(
        self,
        picks,
        *,
        config,
        audit_writer=None,
        census_mid_date_iso=None,
        routing_salt=None,
        **_kwargs,
    ) -> list[GeminiObservation]:
        self.batch_calls.append(list(picks))
        # NOTE: deliberately does NOT write to audit_writer — audit JSONL is a
        # side file, not part of scan_state; keeping it out isolates the state
        # bytes from scorer identity.
        return [
            GeminiObservation(
                chip_index=p.chip_index,
                pv_present=True,
                confidence=0.9,
                quality_flag=_OBS_QUALITY_FLAG,
                evidence="identical evidence",
                notes="identical notes",
                decision_source=_OBS_DECISION_SOURCE,
            )
            for p in picks
        ]

    def score(self, picks, *, config, **_kwargs):  # pragma: no cover - unused here
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

    def fake_download(
        anchor, *, capture_date, version, zoom_ladder, output_root, provider="TM", vintage_check=None
    ):
        p = tmp_path / f"chip_{capture_date}.tif"
        p.write_bytes(b"TIF")
        return _ok_download(p)

    monkeypatch.setattr(ras, "_fetch_real_vintage_catalog", fake_catalog)
    monkeypatch.setattr(
        ras,
        "make_vintage_check",
        lambda anchor, *, available_dates_by_zoom, config: (lambda z, d: True),
    )
    monkeypatch.setattr(_gehi_download, "download_chip_with_zoom_ladder", fake_download)
    monkeypatch.setattr(
        _gehi_common, "ensure_review_png", lambda p: Path(str(p)).with_suffix(".png")
    )


def _run_and_read_state(scorer, subdir: str, tmp_path: Path, monkeypatch) -> bytes:
    vintages = [
        VintageEntry(capture_date="2020-06-15", version=100),
        VintageEntry(capture_date="2021-06-15", version=101),
    ]
    _install_gehi_stubs(monkeypatch, tmp_path, vintages)
    scan_states_dir = tmp_path / subdir
    run_one_anchor(
        {"anchor_id": "invariance_xcheck", "region_key": "johannesburg", "grid_id": "G1"},
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
    return (scan_states_dir / "invariance_xcheck.json").read_bytes()


def test_two_distinct_scorers_identical_observations_yield_identical_scan_state(
    tmp_path: Path, monkeypatch
) -> None:
    """Two structurally-different PresenceScorer impls returning identical
    observations produce byte-identical scan_state.json (timestamp frozen)."""
    monkeypatch.setattr(scan_state_mod, "now_iso", lambda: "2020-01-01T00:00:00Z")

    scorer_a = _IdenticalObsScorer(
        name="impl_alpha", failure_decision_sources=frozenset({"alpha_failed"})
    )
    scorer_b = _IdenticalObsScorer(
        name="impl_beta", failure_decision_sources=frozenset({"beta_failed", "beta_also_failed"})
    )

    bytes_a = _run_and_read_state(scorer_a, "run_alpha", tmp_path, monkeypatch)
    bytes_b = _run_and_read_state(scorer_b, "run_beta", tmp_path, monkeypatch)

    # Sanity: both scorers were actually exercised (not short-circuited).
    assert scorer_a.batch_calls, "scorer_a.batch was never called"
    assert scorer_b.batch_calls, "scorer_b.batch was never called"

    assert bytes_a == bytes_b, (
        "scorer identity leaked into the persisted scan_state: identical "
        "observations must produce byte-identical scan states"
    )
