"""ISSUE-05 PresenceScorer seam — adaptive-scan call-site injection tests.

Extends the fake-scorer injection pattern (previously only on the sequence
path) to the adaptive-scan path:

* a fake ``PresenceScorer`` is threaded through ``run_one_anchor`` /
  ``execute_round_real`` (no monkeypatch of the concrete Gemini module) and its
  verdicts flow into the persisted ``scan_state``;
* the >50%-failed ambiguity rule (Case E) triggers off a *non-Gemini* scorer's
  declared ``failure_decision_sources`` and stays inert under the default set;
* routing identical dry-run verdicts through the seam yields byte-identical
  scan states (timestamps frozen);
* unregistered quality_flag / decision_source values are rejected at write time
  and become writable after additive registration;
* the dry-run stub still reproduces the legacy ``dry_run_gemini_result``
  semantics byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.temporal import gehi_common as _gehi_common
from scripts.temporal import gehi_download as _gehi_download
from scripts.temporal import presence_scorer as ps
from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal import scan_state as scan_state_mod
from scripts.temporal.gehi_download import DownloadResult
from scripts.temporal.run_adaptive_scan import (
    VintageCatalog,
    merge_provider_catalogs,
    execute_round_real,
    run_one_anchor,
)
from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_decision import (
    TerminateAction,
    VintageEntry,
    decide_next_action,
)
from scripts.temporal.scan_state import Pick, Round, RoundResult, ScanState
from scripts.validation.gemini_solar_image_review import GeminiObservation


# ---------------------------------------------------------------------------
# Fake scorer: satisfies the PresenceScorer protocol with a declared, distinctly
# non-Gemini vocabulary + failure sentinel. Only `.batch` is exercised by the
# adaptive-scan real path.
# ---------------------------------------------------------------------------


class _FakeScorer:
    name = "fake_adaptive"
    failure_decision_sources = frozenset({"stubscorer_failed"})
    quality_flags = frozenset({"usable", "unusable"})
    decision_sources = frozenset({"stubscorer_ok", "stubscorer_failed"})

    def __init__(
        self,
        *,
        pv_present: bool | None,
        quality_flag: str,
        decision_source: str,
        confidence: float | None = 0.9,
    ) -> None:
        self._pv_present = pv_present
        self._quality_flag = quality_flag
        self._decision_source = decision_source
        self._confidence = confidence
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
        if audit_writer is not None:
            audit_writer({"stage": "fake_batch", "n_picks": len(picks)})
        return [
            GeminiObservation(
                chip_index=p.chip_index,
                pv_present=self._pv_present,
                confidence=self._confidence,
                quality_flag=self._quality_flag,
                evidence="fake evidence",
                notes="fake notes",
                decision_source=self._decision_source,
            )
            for p in picks
        ]

    # Contract entrypoint — unused by the batch-based adaptive path but present so
    # the object is a structurally complete PresenceScorer.
    def score(self, picks, *, config, **_kwargs):  # pragma: no cover - not exercised
        raise NotImplementedError


# Register the fake scorer's vocabulary once so save_scan_state accepts states it
# produces (registration is additive/global — intentional).
ps.register_quality_flag("usable")  # already seeded; harmless
ps.register_decision_source("stubscorer_ok")
ps.register_decision_source("stubscorer_failed")


# ---------------------------------------------------------------------------
# Shared scaffolding to drive run_one_anchor's real path offline.
# ---------------------------------------------------------------------------


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

    def fake_download(anchor, *, capture_date, version, zoom_ladder, output_root, provider="TM", vintage_check=None):
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


def _run_real(scorer, tmp_path: Path, monkeypatch, vintages: list[VintageEntry]) -> ScanState:
    _install_gehi_stubs(monkeypatch, tmp_path, vintages)
    anchor = {"anchor_id": "A_seam", "region_key": "johannesburg", "grid_id": "G1"}
    return run_one_anchor(
        anchor,
        AdaptiveScanConfig(),
        tmp_path / "scan_states",
        dry_run=False,
        force_restart=True,
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=object(),
        scorer=scorer,
        census_mid_date_iso=None,
    )


# ---------------------------------------------------------------------------
# 1. Injection into execute_round_real (direct, sequence-path-analogous).
# ---------------------------------------------------------------------------


def test_execute_round_real_routes_through_injected_scorer(tmp_path: Path, monkeypatch) -> None:
    """A fake scorer passed as `scorer=` produces the RoundResults — the concrete
    Gemini module is never consulted (its score fn is booby-trapped)."""
    from scripts.validation import gemini_solar_image_review as gsir

    def _boom(*_a, **_k):  # would fire only if the hardwired Gemini path survived
        raise AssertionError("hardwired score_batch_with_fallback must not be called")

    monkeypatch.setattr(gsir, "score_batch_with_fallback", _boom)

    def fake_download(anchor, *, capture_date, version, zoom_ladder, output_root, provider="TM", vintage_check=None):
        p = tmp_path / f"chip_{capture_date}.tif"
        p.write_bytes(b"TIF")
        return _ok_download(p)

    monkeypatch.setattr(_gehi_download, "download_chip_with_zoom_ladder", fake_download)
    monkeypatch.setattr(_gehi_common, "ensure_review_png", lambda p: Path(str(p)).with_suffix(".png"))

    scorer = _FakeScorer(pv_present=True, quality_flag="usable", decision_source="stubscorer_ok")
    picks = [
        Pick(chip_index=1, capture_date="2020-01-01", version=100, requested_zoom=20),
        Pick(chip_index=2, capture_date="2021-01-01", version=101, requested_zoom=20),
    ]
    rnd = Round(round_id=1, round_type="initial", window_start_date=None, window_end_date=None, picks=picks)

    returned = execute_round_real(
        rnd,
        {"anchor_id": "A1", "region_key": "johannesburg"},
        AdaptiveScanConfig(gemini_max_dates_per_call=5),
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=object(),
        scorer=scorer,
        vintage_check=None,
        census_mid_date_iso=None,
    )

    assert len(scorer.batch_calls) == 1
    assert returned.completed is True
    assert [r.decision_source for r in returned.results] == ["stubscorer_ok", "stubscorer_ok"]
    assert all(r.pv_present is True for r in returned.results)
    assert all(r.confidence == 0.9 for r in returned.results)


def test_execute_round_real_scores_the_requested_review_render(tmp_path: Path, monkeypatch) -> None:
    """Geometry pilots can replace the legacy full-chip PNG at the public seam."""

    def fake_download(anchor, *, capture_date, version, zoom_ladder, output_root, provider="TM", vintage_check=None):
        p = tmp_path / f"chip_{capture_date}.tif"
        p.write_bytes(b"TIF")
        return _ok_download(p)

    monkeypatch.setattr(_gehi_download, "download_chip_with_zoom_ladder", fake_download)

    rendered: list[tuple[Path, str]] = []

    def render_for_arm(path: Path, anchor: dict[str, str]) -> Path:
        out = path.with_name(f"{path.stem}.A24.png")
        out.write_bytes(b"PNG")
        rendered.append((path, anchor["anchor_id"]))
        return out

    scorer = _FakeScorer(pv_present=True, quality_flag="usable", decision_source="stubscorer_ok")
    rnd = Round(
        round_id=1,
        round_type="initial",
        window_start_date=None,
        window_end_date=None,
        picks=[Pick(chip_index=1, capture_date="2020-01-01", version=100, requested_zoom=20)],
    )

    execute_round_real(
        rnd,
        {"anchor_id": "A1", "region_key": "johannesburg"},
        AdaptiveScanConfig(gemini_max_dates_per_call=5),
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=object(),
        scorer=scorer,
        review_renderer=render_for_arm,
    )

    assert rendered == [(tmp_path / "chip_2020-01-01.tif", "A1")]
    assert str(scorer.batch_calls[0][0].chip_path).endswith(".A24.png")


def test_execute_round_real_routes_merged_picks_to_provider_caches(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, Path]] = []
    checks: list[tuple[str, bool]] = []

    def fake_download(anchor, *, capture_date, version, zoom_ladder, output_root, provider="TM", vintage_check=None):
        calls.append((provider, output_root))
        checks.append((provider, vintage_check(20, capture_date)))
        p = tmp_path / f"{provider}_{capture_date}.tif"
        p.write_bytes(b"TIF")
        return _ok_download(p)

    monkeypatch.setattr(_gehi_download, "download_chip_with_zoom_ladder", fake_download)
    monkeypatch.setattr(_gehi_common, "ensure_review_png", lambda p: Path(str(p)).with_suffix(".png"))

    scorer = _FakeScorer(pv_present=True, quality_flag="usable", decision_source="stubscorer_ok")
    rnd = Round(
        round_id=1,
        round_type="initial",
        window_start_date=None,
        window_end_date=None,
        picks=[
            Pick(chip_index=1, capture_date="2020-01-01", version=100, requested_zoom=20, provider="TM"),
            Pick(chip_index=2, capture_date="2021-01-01", version=101, requested_zoom=20, provider="Wayback"),
        ],
    )

    returned = execute_round_real(
        rnd,
        {"anchor_id": "A1", "region_key": "johannesburg"},
        AdaptiveScanConfig(provider="Merged", gemini_max_dates_per_call=5),
        chips_dir=tmp_path / "unused",
        provider_chips_dirs={"TM": tmp_path / "tm", "Wayback": tmp_path / "wayback"},
        audit_dir=tmp_path / "audit",
        gemini_config=object(),
        scorer=scorer,
        vintage_check={
            "TM": lambda _zoom, _date: True,
            "Wayback": lambda _zoom, _date: False,
        },
    )

    assert calls == [("TM", tmp_path / "tm"), ("Wayback", tmp_path / "wayback")]
    assert checks == [("TM", True), ("Wayback", False)]
    assert [result.provider for result in returned.results] == ["TM", "Wayback"]


def test_merge_provider_catalogs_unions_dates_with_tm_precedence() -> None:
    tm = VintageCatalog(
        vintages=[
            VintageEntry("2020-01-01", 10, provider="TM"),
            VintageEntry("2022-01-01", 12, provider="TM"),
        ],
        available_dates_by_zoom={20: {"2020-01-01", "2022-01-01"}},
        catalog_max_date="2022-01-01",
    )
    wayback = VintageCatalog(
        vintages=[
            VintageEntry("2021-01-01", 21, provider="Wayback"),
            VintageEntry("2022-01-01", 22, provider="Wayback"),
        ],
        available_dates_by_zoom={19: {"2021-01-01", "2022-01-01"}},
        catalog_max_date="2022-01-01",
    )

    merged = merge_provider_catalogs(tm, wayback)

    assert [(v.capture_date, v.version, v.provider) for v in merged.vintages] == [
        ("2020-01-01", 10, "TM"),
        ("2021-01-01", 21, "Wayback"),
        ("2022-01-01", 12, "TM"),
    ]
    assert merged.available_dates_by_provider_zoom == {
        "TM": tm.available_dates_by_zoom,
        "Wayback": wayback.available_dates_by_zoom,
    }


# ---------------------------------------------------------------------------
# 2. Injected scorer verdicts reach the persisted scan_state (round loop).
# ---------------------------------------------------------------------------


def test_run_one_anchor_persists_injected_scorer_verdicts(tmp_path: Path, monkeypatch) -> None:
    scorer = _FakeScorer(pv_present=True, quality_flag="usable", decision_source="stubscorer_ok")
    vintages = [
        VintageEntry(capture_date="2020-06-15", version=100),
        VintageEntry(capture_date="2021-06-15", version=101),
    ]
    state = _run_real(scorer, tmp_path, monkeypatch, vintages)

    # All-present monotonic scan with no older vintages -> terminates cleanly.
    assert state.status == "done_already_present_before_geid_history"
    persisted = [r for rnd in state.rounds for r in rnd.results]
    assert persisted, "expected at least one scored round"
    assert {r.decision_source for r in persisted} == {"stubscorer_ok"}
    assert all(r.pv_present is True for r in persisted)

    # Reload from disk to prove it round-tripped through save_scan_state's vocab gate.
    reloaded = scan_state_mod.load_scan_state(scan_state_mod.state_path_for("A_seam", tmp_path / "scan_states"))
    assert reloaded is not None
    assert reloaded.status == "done_already_present_before_geid_history"


# ---------------------------------------------------------------------------
# 3. Ambiguity rule (Case E) triggers off the scorer's declared failure source.
# ---------------------------------------------------------------------------


def test_ambiguity_rule_triggers_for_non_gemini_failure_source(tmp_path: Path, monkeypatch) -> None:
    """A non-Gemini scorer declaring failure_decision_sources={'stubscorer_failed'}
    trips Case E when >50% of results carry that source — status string stays the
    locked 'done_ambiguous_gemini_failed'."""
    scorer = _FakeScorer(pv_present=None, quality_flag="unusable", decision_source="stubscorer_failed")
    vintages = [
        VintageEntry(capture_date="2020-06-15", version=100),
        VintageEntry(capture_date="2021-06-15", version=101),
    ]
    state = _run_real(scorer, tmp_path, monkeypatch, vintages)
    assert state.status == "done_ambiguous_gemini_failed"


def test_case_e_sentinel_is_scorer_parameterized() -> None:
    """decide_next_action counts only the passed failure set. The same failed
    results trip Case E under the scorer's declared set but not under the default
    ({'gemini_failed'}) set (pre-ISSUE-05 behavior preserved)."""
    failed = [
        RoundResult(
            chip_index=i, capture_date=f"2020-0{i}-01", version=i,
            pv_present=None, confidence=None, quality_flag="unusable",
            decision_source="stubscorer_failed",
        )
        for i in (1, 2, 3)
    ]
    rnd = Round(round_id=1, round_type="initial", window_start_date=None,
                window_end_date=None, picks=[], results=failed, completed=True)
    state = ScanState(anchor_id="A", region_key="r", grid_id="g", rounds=[rnd])
    vintages = [VintageEntry(capture_date="2020-01-01", version=1)]
    config = AdaptiveScanConfig(case_e_failure_pct=50.0)

    triggered = decide_next_action(
        state, vintages, config, failure_decision_sources={"stubscorer_failed"}
    )
    assert isinstance(triggered, TerminateAction)
    assert triggered.status == "done_ambiguous_gemini_failed"

    # Default set: "stubscorer_failed" is not a failure -> Case E does NOT fire.
    default = decide_next_action(state, vintages, config)
    assert not (
        isinstance(default, TerminateAction) and default.status == "done_ambiguous_gemini_failed"
    )


# ---------------------------------------------------------------------------
# 4. Downstream invariance: identical verdicts -> byte-identical scan states.
# ---------------------------------------------------------------------------


def test_dry_run_scan_state_byte_identical_downstream_invariance(tmp_path: Path, monkeypatch) -> None:
    """Two dry-run scans of the same anchor through the seam produce byte-identical
    scan_state JSON once the write-time timestamp is frozen."""
    monkeypatch.setattr(scan_state_mod, "now_iso", lambda: "2020-01-01T00:00:00Z")

    anchor = {"anchor_id": "invariance_anchor", "region_key": "johannesburg", "grid_id": "G1"}
    config = AdaptiveScanConfig()

    def _one(dirname: str) -> bytes:
        d = tmp_path / dirname
        run_one_anchor(
            anchor, config, d,
            dry_run=True, force_restart=True,
            chips_dir=tmp_path / "chips", audit_dir=tmp_path / "audit",
            census_mid_date_iso=None,
        )
        return (d / "invariance_anchor.json").read_bytes()

    assert _one("run_a") == _one("run_b")


def test_run_one_anchor_records_per_anchor_census_catalog_bound(tmp_path: Path) -> None:
    anchor = {
        "anchor_id": "cutoff_provenance_anchor",
        "region_key": "johannesburg",
        "grid_id": "G1",
    }
    census_date = "2024-02-21"
    config = AdaptiveScanConfig(post_census_reference_frames=2)
    future_dates = sorted(
        v.capture_date
        for v in ras.dry_run_vintages(anchor["anchor_id"])
        if v.capture_date > census_date
    )

    state = run_one_anchor(
        anchor,
        config,
        tmp_path / "scan_states",
        dry_run=True,
        force_restart=True,
        census_mid_date_iso=census_date,
    )

    assert len(future_dates) >= 2
    assert state.census_date == census_date
    assert state.catalog_max_date == future_dates[1]
    assert state.post_census_reference_frames == 2
    assert max(p.capture_date for rnd in state.rounds for p in rnd.picks) <= future_dates[1]

    reloaded = scan_state_mod.load_scan_state(
        scan_state_mod.state_path_for(anchor["anchor_id"], tmp_path / "scan_states")
    )
    assert reloaded is not None
    assert reloaded.census_date == census_date
    assert reloaded.catalog_max_date == future_dates[1]
    assert reloaded.post_census_reference_frames == 2


# ---------------------------------------------------------------------------
# 5. Write-time vocab enforcement + additive registration through this path.
# ---------------------------------------------------------------------------


def test_unregistered_decision_source_rejected_then_registrable(tmp_path: Path, monkeypatch) -> None:
    novel = "seam_test_novel_source"
    assert novel not in ps.known_decision_sources()

    scorer = _FakeScorer(pv_present=True, quality_flag="usable", decision_source=novel)
    vintages = [VintageEntry(capture_date="2020-06-15", version=100)]

    with pytest.raises(ValueError, match="unknown decision_source"):
        _run_real(scorer, tmp_path, monkeypatch, vintages)

    # Additive registration makes the same verdict writable.
    ps.register_decision_source(novel)
    scorer2 = _FakeScorer(pv_present=True, quality_flag="usable", decision_source=novel)
    state = _run_real(scorer2, tmp_path / "after", monkeypatch, vintages)
    persisted = [r for rnd in state.rounds for r in rnd.results]
    assert {r.decision_source for r in persisted} == {novel}


# ---------------------------------------------------------------------------
# 6. Dry-run stub regression: byte-for-byte legacy semantics via the seam.
# ---------------------------------------------------------------------------


def test_dry_run_stub_reproduces_legacy_result_fields(tmp_path: Path) -> None:
    """The 'dry_run' scorer routed through run_one_anchor reproduces the old
    dry_run_gemini_result fields exactly (decision_source/confidence/quality/
    evidence/notes)."""
    # Pick an anchor whose deterministic profile is an 'appears_YYYY' interval so
    # the verdicts exercise the install_date branch, not a constant.
    anchor_id = "dry_regression_anchor"
    profile = ras.dry_run_profile_for(anchor_id)

    state = run_one_anchor(
        {"anchor_id": anchor_id, "region_key": "johannesburg", "grid_id": "G1"},
        AdaptiveScanConfig(),
        tmp_path / "scan_states",
        dry_run=True,
        force_restart=True,
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        census_mid_date_iso=None,
    )

    results = [r for rnd in state.rounds for r in rnd.results]
    assert results, "dry-run should have scored at least one round"
    for r in results:
        assert r.decision_source == "dry_run_stub"
        assert r.confidence == 0.95
        assert r.quality_flag == "usable"
        assert r.evidence == f"stub evidence for {profile.label}"
        assert r.notes == f"dry_run profile={profile.label}"
        assert r.chip_path == ""
