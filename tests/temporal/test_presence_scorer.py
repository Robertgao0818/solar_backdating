"""ISSUE-05 seam tests: registry, vocab enforcement, dry-run stub, Gemini wrapper.

No network: the Gemini wrapper is exercised by stubbing the lazily-imported
`score_batch_with_fallback` (the same idiom the adaptive-scan tests use), and the
dry-run stub needs nothing external.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date

import pytest

from scripts.temporal import presence_scorer as ps
from scripts.temporal.presence_scorer import (
    DEFAULT_FAILURE_DECISION_SOURCES,
    DryRunPresenceScorer,
    GeminiPresenceScorer,
    Pick,
    PresenceObservation,
    available_scorers,
    get_scorer,
    known_decision_sources,
    known_quality_flags,
    register_decision_source,
    register_quality_flag,
    register_scorer,
    validate_decision_source,
    validate_observation,
    validate_quality_flag,
)
from scripts.temporal.scan_state import (
    Round,
    RoundResult,
    ScanState,
    save_scan_state,
    state_path_for,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_seeds_gemini_and_dry_run() -> None:
    assert "gemini" in available_scorers()
    assert "dry_run" in available_scorers()


def test_get_scorer_gemini_returns_gemini_scorer() -> None:
    scorer = get_scorer("gemini")
    assert isinstance(scorer, GeminiPresenceScorer)
    assert scorer.name == "gemini"
    assert scorer.failure_decision_sources == frozenset({"gemini_failed"})


def test_get_scorer_dry_run_requires_label() -> None:
    scorer = get_scorer("dry_run", label="all_present")
    assert isinstance(scorer, DryRunPresenceScorer)
    assert scorer.name == "dry_run"
    assert scorer.failure_decision_sources == frozenset()


def test_get_scorer_unknown_name_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="unknown scorer 'nope'"):
        get_scorer("nope")


def test_gemini_fingerprint_pins_request_identity_fields() -> None:
    """Provenance amendment 2026-07-16: temperature / preprocessing / image
    order are instruction identity and must enter the prompt-config hash."""
    fp = get_scorer("gemini").prompt_config_fingerprint("batch", None)
    assert fp["temperature"] == 0
    assert fp["image_preprocessing"] == "raw_bytes_base64_no_transform"
    assert fp["image_order_rule"] == "picks_order_chip_index_1based"


def test_register_scorer_is_additive() -> None:
    sentinel = object()

    def factory(**_kwargs):
        return sentinel

    register_scorer("_tmp_test_scorer", factory)
    try:
        assert get_scorer("_tmp_test_scorer") is sentinel
        assert "_tmp_test_scorer" in available_scorers()
    finally:
        ps._SCORER_FACTORIES.pop("_tmp_test_scorer", None)


# ---------------------------------------------------------------------------
# Dry-run stub — must reproduce dry_run_gemini_result semantics
# ---------------------------------------------------------------------------


def _picks(dates: list[str]) -> list[Pick]:
    return [
        Pick(chip_path="", capture_date=d, version=100 + i, actual_zoom=20, index=i + 1)
        for i, d in enumerate(dates)
    ]


def test_dry_run_all_present() -> None:
    scorer = DryRunPresenceScorer(label="all_present")
    obs = scorer.score(_picks(["2018-06-15", "2024-06-15"]), config=None)
    assert [o.pv_present for o in obs] == [True, True]
    assert all(o.decision_source == "dry_run_stub" for o in obs)
    assert all(o.quality_flag == "usable" for o in obs)
    assert all(o.pv_score == 0.95 for o in obs)


def test_dry_run_all_absent() -> None:
    scorer = DryRunPresenceScorer(label="all_absent")
    obs = scorer.score(_picks(["2018-06-15", "2024-06-15"]), config=None)
    assert [o.pv_present for o in obs] == [False, False]


def test_dry_run_appears_threshold_on_install_date() -> None:
    scorer = DryRunPresenceScorer(label="appears_2021", install_date=date(2021, 6, 15))
    obs = scorer.score(_picks(["2019-01-01", "2021-06-15", "2023-01-01"]), config=None)
    assert [o.pv_present for o in obs] == [False, True, True]
    assert obs[0].notes == "dry_run profile=appears_2021"
    assert obs[0].evidence == "stub evidence for appears_2021"
    assert [o.index for o in obs] == [1, 2, 3]


def test_dry_run_appears_requires_install_date() -> None:
    scorer = DryRunPresenceScorer(label="appears_2021", install_date=None)
    with pytest.raises(ValueError, match="requires an install_date"):
        scorer.score(_picks(["2019-01-01"]), config=None)


def test_dry_run_is_deterministic_downstream_invariance() -> None:
    """Identical picks -> byte-identical serialized observations (seam preserves answers)."""
    scorer = DryRunPresenceScorer(label="appears_2020", install_date=date(2020, 6, 15))
    picks = _picks(["2018-06-15", "2020-06-15", "2022-06-15"])
    a = scorer.score(picks, config=None)
    b = scorer.score(picks, config=None)
    assert json.dumps([asdict(o) for o in a]) == json.dumps([asdict(o) for o in b])


# ---------------------------------------------------------------------------
# Gemini wrapper — no network (stub the lazily-imported batch callable)
# ---------------------------------------------------------------------------


def test_gemini_score_maps_observations_without_network(monkeypatch) -> None:
    import scripts.validation.gemini_solar_image_review as gsir

    captured: dict[str, object] = {}

    def fake_batch(picks, *, config, audit_writer=None, poster=None, census_mid_date_iso=None, routing_salt=None):
        captured["picks"] = picks
        captured["config"] = config
        return [
            gsir.GeminiObservation(
                chip_index=p.chip_index,
                pv_present=True,
                confidence=0.88,
                quality_flag="usable",
                evidence="panels",
                notes="ok",
                decision_source="gemini_batch",
            )
            for p in picks
        ]

    monkeypatch.setattr(gsir, "score_batch_with_fallback", fake_batch)

    scorer = get_scorer("gemini")
    obs = scorer.score(_picks(["2018-06-15", "2024-06-15"]), config="CFG")

    assert captured["config"] == "CFG"
    assert len(captured["picks"]) == 2
    assert [o.pv_present for o in obs] == [True, True]
    assert [o.pv_score for o in obs] == [0.88, 0.88]
    assert {o.decision_source for o in obs} == {"gemini_batch"}
    assert [o.index for o in obs] == [1, 2]
    assert [o.capture_date for o in obs] == ["2018-06-15", "2024-06-15"]


def test_gemini_raw_callables_resolve_lazily() -> None:
    import scripts.validation.gemini_solar_image_review as gsir

    scorer = get_scorer("gemini")
    assert scorer.batch is gsir.score_batch_with_fallback
    assert scorer.sequence is gsir.score_single_target_sequence
    assert scorer.matrix is gsir.score_target_date_matrix


# ---------------------------------------------------------------------------
# Vocabulary registries
# ---------------------------------------------------------------------------


def test_seeded_vocab_covers_inventory() -> None:
    for flag in ("usable", "ambiguous", "unusable", "ok", "error", "sequence_failed"):
        assert flag in known_quality_flags()
    for src in (
        "gemini_batch",
        "gemini_per_image",
        "gemini_failed",
        "dry_run_stub",
        "manual",
        "gemini_sequence",
        "gemini_matrix",
        "manual_template",
        "sequence_pending",
    ):
        assert src in known_decision_sources()


def test_validate_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="unknown quality_flag"):
        validate_quality_flag("_never_seen_flag_")
    with pytest.raises(ValueError, match="unknown decision_source"):
        validate_decision_source("_never_seen_source_")


def test_validate_observation_checks_both_axes() -> None:
    good = PresenceObservation(
        pv_present=True,
        pv_score=0.9,
        quality_flag="usable",
        decision_source="gemini_batch",
    )
    validate_observation(good)  # no raise
    bad = PresenceObservation(
        pv_present=None,
        pv_score=None,
        quality_flag="usable",
        decision_source="_bogus_",
    )
    with pytest.raises(ValueError, match="unknown decision_source"):
        validate_observation(bad)


def test_additive_registration_of_new_vocab() -> None:
    assert "_new_scorer_failed_" not in known_decision_sources()
    with pytest.raises(ValueError):
        validate_decision_source("_new_scorer_failed_")
    register_decision_source("_new_scorer_failed_")
    try:
        validate_decision_source("_new_scorer_failed_")  # now passes
        register_quality_flag("_new_flag_")
        validate_quality_flag("_new_flag_")
    finally:
        ps._DECISION_SOURCES.discard("_new_scorer_failed_")
        ps._QUALITY_FLAGS.discard("_new_flag_")


def test_register_empty_value_rejected() -> None:
    with pytest.raises(ValueError):
        register_decision_source("")
    with pytest.raises(ValueError):
        register_quality_flag("")


def test_default_failure_decision_sources_is_gemini_failed() -> None:
    assert DEFAULT_FAILURE_DECISION_SOURCES == frozenset({"gemini_failed"})


# ---------------------------------------------------------------------------
# Write-time enforcement at the scan_state persistence choke-point
# ---------------------------------------------------------------------------


def _state_with_source(decision_source: str, quality_flag: str = "usable") -> ScanState:
    state = ScanState(anchor_id="a1", region_key="r", grid_id="g")
    state.status = "done_appears"
    state.rounds = [
        Round(
            round_id=1,
            round_type="initial",
            window_start_date="2018-06-15",
            window_end_date="2024-06-15",
            results=[
                RoundResult(
                    chip_index=1,
                    capture_date="2018-06-15",
                    version=1,
                    pv_present=True,
                    confidence=0.9,
                    quality_flag=quality_flag,
                    decision_source=decision_source,
                    actual_zoom=20,
                )
            ],
            completed=True,
        )
    ]
    return state


def test_save_scan_state_rejects_unknown_decision_source(tmp_path) -> None:
    state = _state_with_source("_bogus_source_")
    with pytest.raises(ValueError, match="unknown decision_source"):
        save_scan_state(state, state_path_for(state.anchor_id, tmp_path))


def test_save_scan_state_rejects_unknown_quality_flag(tmp_path) -> None:
    state = _state_with_source("gemini_batch", quality_flag="_bogus_flag_")
    with pytest.raises(ValueError, match="unknown quality_flag"):
        save_scan_state(state, state_path_for(state.anchor_id, tmp_path))


def test_save_scan_state_accepts_after_additive_registration(tmp_path) -> None:
    state = _state_with_source("_late_registered_source_")
    path = state_path_for(state.anchor_id, tmp_path)
    with pytest.raises(ValueError):
        save_scan_state(state, path)
    register_decision_source("_late_registered_source_")
    try:
        save_scan_state(state, path)  # now succeeds
        assert path.exists()
    finally:
        ps._DECISION_SOURCES.discard("_late_registered_source_")


def test_save_scan_state_accepts_all_seeded_adaptive_scan_vocab(tmp_path) -> None:
    for src, flag in (
        ("gemini_batch", "usable"),
        ("gemini_per_image", "ambiguous"),
        ("gemini_failed", "unusable"),
        ("dry_run_stub", "usable"),
    ):
        state = _state_with_source(src, quality_flag=flag)
        save_scan_state(state, state_path_for(f"{src}_{flag}", tmp_path))
