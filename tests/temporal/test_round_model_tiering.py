"""Regression guard for per-round Gemini model tiering in run_one_anchor.

Two routing modes, both selected in ``run_one_anchor`` before
``execute_round_real`` is called:

* **round_type routing (current default):** round_types in ``cheap_round_types``
  (initial/bisection/walk_back/tail — the routine present/absent questions) use
  the cheap tier (``gemini_config_round1``); everything else (anchor_recovery)
  escalates to the capable tier (``gemini_config``). bisection is ~86% of
  round>=2 volume and asks the same question as the initial round, so keeping it
  cheap is what conserves the scarce capable-tier quota.
* **legacy round_id routing (back-compat):** when ``cheap_round_types`` is unset,
  only round_id==1 uses the cheap tier.

We drive run_one_anchor offline with stubbed GEHI discovery + decision loop and
capture the ``gemini_config.model`` that reaches each round, plus that the shared
limiter and routing-salt mode are forwarded.
"""

from __future__ import annotations

from pathlib import Path

from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal.run_adaptive_scan import VintageCatalog, run_one_anchor
from scripts.temporal.scan_decision import (
    ExecuteRoundAction,
    TerminateAction,
    VintageEntry,
)
from scripts.temporal.scan_state import Round, RoundResult
from scripts.validation.gemini_solar_image_review import GeminiClientConfig, RateLimiter


def _round(round_id: int) -> Round:
    return Round(
        round_id=round_id,
        round_type="initial" if round_id == 1 else "bisection",
        window_start_date=None,
        window_end_date=None,
        picks=[],
    )


def _round_t(round_id: int, round_type: str) -> Round:
    return Round(
        round_id=round_id,
        round_type=round_type,
        window_start_date=None,
        window_end_date=None,
        picks=[],
    )


def test_round_one_uses_cheap_tier_round_two_plus_escalates(tmp_path: Path, monkeypatch) -> None:
    captured: list[tuple[int, str, str, object]] = []

    def fake_catalog(anchor, config):
        return VintageCatalog(
            vintages=[VintageEntry(capture_date="2020-06-15", version=100)],
            available_dates_by_zoom={19: {"2020-06-15"}},
        )

    def fake_vintage_check(anchor, *, available_dates_by_zoom, config):
        return lambda zoom, capture_date: True

    def fake_decide(state, vintages, config):
        n = len(state.rounds)
        if n == 0:
            return ExecuteRoundAction(kind="execute_round", round=_round(1))
        if n == 1:
            return ExecuteRoundAction(kind="execute_round", round=_round(2))
        return TerminateAction(kind="terminate", status="done_appears", notes="")

    def fake_execute(rnd, anchor, config, *, chips_dir, audit_dir, gemini_config,
                     vintage_check=None, census_mid_date_iso=None,
                     limiter=None, routing_salt_mode="none"):
        captured.append((rnd.round_id, gemini_config.model, routing_salt_mode, limiter))
        rnd.results = [
            RoundResult(
                chip_index=1, capture_date="2020-06-15", version=100,
                pv_present=True, confidence=0.9, quality_flag="usable",
                decision_source="gemini_batch", evidence="", notes="",
                chip_path="", actual_zoom=20,
            )
        ]
        rnd.completed = True
        rnd.failed = False
        return rnd

    monkeypatch.setattr(ras, "_fetch_real_vintage_catalog", fake_catalog)
    monkeypatch.setattr(ras, "make_vintage_check", fake_vintage_check)
    monkeypatch.setattr(ras, "decide_next_action", fake_decide)
    monkeypatch.setattr(ras, "execute_round_real", fake_execute)

    base = GeminiClientConfig(base_url="http://x", api_key="k", model="gemini-3-flash-agent")
    round1 = GeminiClientConfig(base_url="http://x", api_key="k", model="gemini-3-flash")
    limiter = RateLimiter(8.0)
    anchor = {"anchor_id": "A1", "region_key": "johannesburg", "grid_id": "G1"}

    state = run_one_anchor(
        anchor,
        config=object(),  # AdaptiveScanConfig is unused once decide/catalog are stubbed
        scan_states_dir=tmp_path / "scan_states",
        dry_run=False,
        force_restart=True,
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=base,
        gemini_config_round1=round1,
        limiter=limiter,
        routing_salt_mode="target",
        census_mid_date_iso=None,
    )

    assert state.status == "done_appears"
    assert [(rid, model) for rid, model, _, _ in captured] == [
        (1, "gemini-3-flash"),
        (2, "gemini-3-flash-agent"),
    ]
    # Limiter + salt mode forwarded to every round.
    assert all(salt_mode == "target" for _, _, salt_mode, _ in captured)
    assert all(lim is limiter for _, _, _, lim in captured)


def test_no_round1_config_keeps_single_tier(tmp_path: Path, monkeypatch) -> None:
    """Without gemini_config_round1, every round uses the base config (back-compat)."""
    captured: list[str] = []

    monkeypatch.setattr(
        ras, "_fetch_real_vintage_catalog",
        lambda anchor, config: VintageCatalog(
            vintages=[VintageEntry(capture_date="2020-06-15", version=100)],
            available_dates_by_zoom={19: {"2020-06-15"}},
        ),
    )
    monkeypatch.setattr(
        ras, "make_vintage_check",
        lambda anchor, *, available_dates_by_zoom, config: (lambda z, d: True),
    )

    def fake_decide(state, vintages, config):
        n = len(state.rounds)
        if n < 2:
            return ExecuteRoundAction(kind="execute_round", round=_round(n + 1))
        return TerminateAction(kind="terminate", status="done_appears", notes="")

    def fake_execute(rnd, anchor, config, *, chips_dir, audit_dir, gemini_config,
                     vintage_check=None, census_mid_date_iso=None,
                     limiter=None, routing_salt_mode="none"):
        captured.append(gemini_config.model)
        rnd.results = []
        rnd.completed = True
        rnd.failed = False
        return rnd

    monkeypatch.setattr(ras, "decide_next_action", fake_decide)
    monkeypatch.setattr(ras, "execute_round_real", fake_execute)

    base = GeminiClientConfig(base_url="http://x", api_key="k", model="gemini-3-flash-agent")
    run_one_anchor(
        {"anchor_id": "A2", "region_key": "johannesburg", "grid_id": "G1"},
        config=object(),
        scan_states_dir=tmp_path / "scan_states",
        dry_run=False,
        force_restart=True,
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=base,
        gemini_config_round1=None,
        limiter=None,
        routing_salt_mode="none",
        census_mid_date_iso=None,
    )

    assert captured == ["gemini-3-flash-agent", "gemini-3-flash-agent"]


def test_round_type_routing_keeps_bisection_cheap(tmp_path: Path, monkeypatch) -> None:
    """With cheap_round_types, bisection stays on the cheap tier and only
    anchor_recovery escalates — independent of round_id."""
    captured: list[tuple[int, str, str]] = []

    def fake_catalog(anchor, config):
        return VintageCatalog(
            vintages=[VintageEntry(capture_date="2020-06-15", version=100)],
            available_dates_by_zoom={19: {"2020-06-15"}},
        )

    def fake_decide(state, vintages, config):
        n = len(state.rounds)
        if n == 0:
            return ExecuteRoundAction(kind="execute_round", round=_round_t(1, "initial"))
        if n == 1:
            return ExecuteRoundAction(kind="execute_round", round=_round_t(2, "bisection"))
        if n == 2:
            return ExecuteRoundAction(kind="execute_round", round=_round_t(3, "anchor_recovery"))
        return TerminateAction(kind="terminate", status="done_appears", notes="")

    def fake_execute(rnd, anchor, config, *, chips_dir, audit_dir, gemini_config,
                     vintage_check=None, census_mid_date_iso=None,
                     limiter=None, routing_salt_mode="none"):
        captured.append((rnd.round_id, rnd.round_type, gemini_config.model))
        rnd.results = []
        rnd.completed = True
        rnd.failed = False
        return rnd

    monkeypatch.setattr(ras, "_fetch_real_vintage_catalog", fake_catalog)
    monkeypatch.setattr(
        ras, "make_vintage_check",
        lambda anchor, *, available_dates_by_zoom, config: (lambda z, d: True),
    )
    monkeypatch.setattr(ras, "decide_next_action", fake_decide)
    monkeypatch.setattr(ras, "execute_round_real", fake_execute)

    base = GeminiClientConfig(base_url="http://x", api_key="k", model="gemini-3-flash-agent")
    round1 = GeminiClientConfig(base_url="http://x", api_key="k", model="gemini-3-flash")

    run_one_anchor(
        {"anchor_id": "A3", "region_key": "johannesburg", "grid_id": "G1"},
        config=object(),
        scan_states_dir=tmp_path / "scan_states",
        dry_run=False,
        force_restart=True,
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=base,
        gemini_config_round1=round1,
        cheap_round_types=frozenset({"initial", "bisection", "walk_back", "tail"}),
        limiter=None,
        routing_salt_mode="none",
        census_mid_date_iso=None,
    )

    assert captured == [
        (1, "initial", "gemini-3-flash"),
        (2, "bisection", "gemini-3-flash"),       # cheap, despite round_id>=2
        (3, "anchor_recovery", "gemini-3-flash-agent"),  # only this escalates
    ]
