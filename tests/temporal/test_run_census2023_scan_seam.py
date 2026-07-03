"""Fake-scorer injection tests for the census-narrowing scan (ISSUE-05).

Mirrors the sequence-path pattern in ``test_score_target_sequence.py`` but for
``run_census2023_scan.run_one_anchor``, which now routes every per-anchor Gemini
sequence call through an injected ``PresenceScorer`` (via ``scorer.sequence`` and
``scorer.failure_decision_sources``) instead of the hardwired
``score_single_target_sequence`` import.

Coverage:
  * the injected scorer's ``.sequence`` is the only scoring path (fake captures picks),
  * the >50%-failed / failure sentinel is scorer-parameterized (a non-Gemini scorer's
    declared failure source trips ``kept_gemini_failed``; a literal "gemini_failed"
    NOT in the scorer's set does not),
  * identical observations produce byte-identical persisted CENSUS_FIELDS output,
  * no hardwired scorer import remains at module level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import scripts.temporal.presence_scorer as ps
import scripts.temporal.run_census2023_scan as rc
from scripts.temporal.run_census2023_scan import CensusJob, run_one_anchor
from scripts.temporal.scan_state import Round, RoundResult, ScanState
from scripts.validation.gemini_solar_image_review import (
    GeminiClientConfig,
    GeminiSequenceObservation,
    GeminiSequenceResult,
)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

# Anchor exemplar frames (cached in scan_state) + cohort bracket + 2023 query.
ABSENT_ANCHOR_DATE = "2019-05-01"
PRESENT_ANCHOR_DATE = "2024-06-01"
BRACKET_A = "2020-06-01"          # cohort latest_absent_date
BRACKET_P = "2023-12-01"          # cohort install_interval_end (present-clamped)
WB_ABSENT = "2023-03-15"          # inside (A, P), read absent
WB_PRESENT = "2023-09-15"         # inside (A, P), read present


def _config() -> GeminiClientConfig:
    return GeminiClientConfig(base_url="https://stub.example", api_key="stub")


def _cohort_row() -> dict[str, str]:
    return {
        "anchor_id": "census_anchor_01",
        "id_kind": "c",
        "grid_id": "JNB0202",
        "region_key": "johannesburg",
        "latest_absent_date": BRACKET_A,
        "install_interval_end": BRACKET_P,
        "wb_2023_dates": f"{WB_ABSENT};{WB_PRESENT}",
        "centroid_lon": "28.0",
        "centroid_lat": "-26.0",
        "chip_lon_min": "27.99",
        "chip_lat_min": "-26.01",
        "chip_lon_max": "28.01",
        "chip_lat_max": "-25.99",
    }


def _fake_state() -> ScanState:
    """A cached scan_state carrying one usable-absent and one usable-present frame."""
    results = [
        RoundResult(
            chip_index=1, capture_date=ABSENT_ANCHOR_DATE, version=1,
            pv_present=False, confidence=0.9, quality_flag="usable",
            decision_source="gemini_batch", chip_path="/tmp/absent.tif", actual_zoom=20,
        ),
        RoundResult(
            chip_index=2, capture_date=PRESENT_ANCHOR_DATE, version=1,
            pv_present=True, confidence=0.9, quality_flag="usable",
            decision_source="gemini_batch", chip_path="/tmp/present.tif", actual_zoom=20,
        ),
    ]
    rnd = Round(
        round_id=1, round_type="initial", window_start_date=None, window_end_date=None,
        results=results, completed=True,
    )
    return ScanState(
        anchor_id="census_anchor_01", region_key="johannesburg", grid_id="JNB0202",
        status="done_appears", rounds=[rnd],
    )


@dataclass
class _FakeScorer:
    """A stub PresenceScorer whose ``.sequence`` returns a canned result and whose
    failure sentinel is configurable (to prove the census failure check reads the
    scorer's declared set, not a hardcoded literal)."""

    result: GeminiSequenceResult
    failure_decision_sources: frozenset[str] = frozenset({"gemini_failed"})
    name: str = "fake"
    quality_flags: frozenset[str] = field(default_factory=frozenset)
    decision_sources: frozenset[str] = field(default_factory=frozenset)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def sequence(self, picks, *, config, audit_writer=None, max_tokens=None, routing_salt=None):
        self.calls.append(
            {
                "dates": [p.capture_date for p in picks],
                "versions": [p.version for p in picks],
                "config": config,
                "max_tokens": max_tokens,
                "routing_salt": routing_salt,
            }
        )
        return self.result

    def score(self, picks, *, config, **kwargs):  # pragma: no cover - unused by census
        raise NotImplementedError


def _seq_result(
    *,
    decision_source: str = "gemini_sequence",
    consistency_flag: str = "monotonic",
    quality_flag: str = "usable",
    readings: list[tuple[str, bool | None]] | None = None,
) -> GeminiSequenceResult:
    if readings is None:
        readings = [
            (ABSENT_ANCHOR_DATE, False),
            (WB_ABSENT, False),
            (WB_PRESENT, True),
            (PRESENT_ANCHOR_DATE, True),
        ]
    obs = [
        GeminiSequenceObservation(
            date_index=i, capture_date=d, pv_present=v,
            pv_score=(0.9 if v else 0.1) if v is not None else None,
            evidence=f"date {i}", notes="",
        )
        for i, (d, v) in enumerate(readings, start=1)
    ]
    first_present = next((d for d, v in readings if v), None)
    return GeminiSequenceResult(
        sequence_pattern="-".join("1" if v else ("?" if v is None else "0") for _, v in readings),
        first_present_date=first_present,
        first_present_date_index=None,
        confidence=0.9,
        consistency_flag=consistency_flag,
        quality_flag=quality_flag,
        review_notes="stub",
        observations=obs,
        decision_source=decision_source,
    )


def _patch_io(monkeypatch, state: ScanState) -> None:
    """Stub out the disk/network side of run_one_anchor: scan_state load, Wayback
    download, and PNG rendering."""
    state_path_exists = {"seen": False}

    def _fake_load(path: Path) -> ScanState:
        state_path_exists["seen"] = True
        return state

    def _fake_download(anchor, *, capture_date, version, zoom_ladder, output_root, provider, allow_nearest):
        from types import SimpleNamespace

        return SimpleNamespace(status="ok", path=Path(f"/tmp/wb_{capture_date}.tif"), actual_zoom=19)

    monkeypatch.setattr(rc, "load_scan_state", _fake_load)
    monkeypatch.setattr(rc, "download_chip_with_zoom_ladder", _fake_download)
    monkeypatch.setattr(rc, "ensure_review_png", lambda p: Path(p))
    # run_one_anchor guards on state_path.exists() before load; make it True.
    monkeypatch.setattr(rc.Path, "exists", lambda self: True)


def _run(monkeypatch, tmp_path: Path, scorer) -> dict[str, object]:
    _patch_io(monkeypatch, _fake_state())
    job = CensusJob(_cohort_row())
    return run_one_anchor(
        job,
        main_dir=tmp_path / "main",
        norecent_dir=tmp_path / "norecent",
        census_chips_dir=tmp_path / "chips",
        audit_dir=None,
        config=_config(),
        zoom_ladder=(19, 18),
        max_tokens=8192,
        routing_salt_mode="none",
        limiter=rc.RateLimiter(0.0),
        scorer=scorer,
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_no_hardwired_scorer_import() -> None:
    """The module must not carry a top-level ``score_single_target_sequence`` name."""
    assert not hasattr(rc, "score_single_target_sequence")


def test_run_one_anchor_routes_through_injected_scorer(monkeypatch, tmp_path: Path) -> None:
    scorer = _FakeScorer(result=_seq_result())
    out = _run(monkeypatch, tmp_path, scorer)

    # The injected scorer's .sequence is the only scoring path, called exactly once
    # with the ordered picks (absent anchor, 2023 frames ascending, present anchor).
    assert len(scorer.calls) == 1
    assert scorer.calls[0]["dates"] == [
        ABSENT_ANCHOR_DATE, WB_ABSENT, WB_PRESENT, PRESENT_ANCHOR_DATE,
    ]
    assert scorer.calls[0]["max_tokens"] == 8192
    assert scorer.calls[0]["config"] is _config() or scorer.calls[0]["config"].base_url == "https://stub.example"

    # Monotonic absent->present 2023 readings strictly tighten (A,P) -> narrowed.
    assert out["census_decision"] == "narrowed"
    assert out["narrowed"] == "1"
    assert out["latest_absent_date"] == WB_ABSENT
    assert out["earliest_present_date"] == WB_PRESENT
    assert out["sequence_pattern"] == "0-0-1-1"
    assert out["wb_2023_readings"] == f"{WB_ABSENT}:absent;{WB_PRESENT}:present"


def test_failure_sentinel_is_scorer_parameterized(monkeypatch, tmp_path: Path) -> None:
    """A non-Gemini scorer's declared failure decision_source trips the same
    persisted ``kept_gemini_failed`` string the Gemini literal used to.

    The scorer's vocabulary must be registered (additively) for its emissions
    to pass write-time enforcement at the ingest point — the AC5 registration
    path exercised on a real call-site.
    """
    ps.register_decision_source("stubscorer_failed")
    try:
        scorer = _FakeScorer(
            result=_seq_result(decision_source="stubscorer_failed"),
            failure_decision_sources=frozenset({"stubscorer_failed"}),
        )
        out = _run(monkeypatch, tmp_path, scorer)
        assert out["census_decision"] == "kept_gemini_failed"
    finally:
        ps._DECISION_SOURCES.discard("stubscorer_failed")


def test_literal_gemini_failed_not_treated_as_failed_when_not_declared(
    monkeypatch, tmp_path: Path
) -> None:
    """Proof the hardcoded literal is gone: a result with decision_source
    ``gemini_failed`` is NOT treated as a failure unless the injected scorer
    declares it in ``failure_decision_sources``."""
    scorer = _FakeScorer(
        result=_seq_result(decision_source="gemini_failed"),
        failure_decision_sources=frozenset({"stubscorer_failed"}),
    )
    out = _run(monkeypatch, tmp_path, scorer)
    # It falls through the failure gate and reaches the narrow-or-keep logic.
    assert out["census_decision"] == "narrowed"


def test_persisted_output_byte_identical_for_identical_observations(
    monkeypatch, tmp_path: Path
) -> None:
    """Downstream invariance: identical observations -> byte-identical CENSUS_FIELDS
    CSV. (The census script writes its own CSV, not scan_state.json.)"""
    out_a = _run(monkeypatch, tmp_path, _FakeScorer(result=_seq_result()))
    out_b = _run(monkeypatch, tmp_path, _FakeScorer(result=_seq_result()))
    assert out_a == out_b

    path_a = tmp_path / "a.csv"
    path_b = tmp_path / "b.csv"
    rc._atomic_write(path_a, [out_a])
    rc._atomic_write(path_b, [out_b])
    assert path_a.read_bytes() == path_b.read_bytes()


def test_gemini_scorer_default_has_sequence_and_failure_source() -> None:
    """The default registry scorer exposes the raw ``.sequence`` accessor and the
    Gemini failure sentinel, without importing Gemini deps at construction."""
    scorer = rc.get_scorer("gemini")
    assert scorer.failure_decision_sources == frozenset({"gemini_failed"})
    assert hasattr(scorer, "sequence")


def test_unregistered_emission_rejected_at_ingest(monkeypatch, tmp_path: Path) -> None:
    """Write-time vocab enforcement (AC5): a scorer emitting an unregistered
    decision_source is rejected the moment its result enters the census write
    path, instead of flowing silently into the CSV."""
    scorer = _FakeScorer(result=_seq_result(decision_source="never_registered_source"))
    with pytest.raises(ValueError, match="never_registered_source"):
        _run(monkeypatch, tmp_path, scorer)
