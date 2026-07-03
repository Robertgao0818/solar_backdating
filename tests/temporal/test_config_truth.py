"""ISSUE-17: config-truth guarantees for configs/geid_anchor_presence.yaml.

The repo's sole YAML loader (`scan_config.load_config`) reads only the
`adaptive_scan:` section. These tests lock that truth in three ways:

- consumed-keys: every key present in the real YAML is read by code, so a future
  dead knob fails a test instead of silently misleading the next editor.
- ladder single-source: the live zoom ladder in `adaptive_scan:` governs BOTH
  production orchestrators' download invocation (adaptive scan +
  census-narrowing scan), verified with a capturing download stub.
- default-parity: the cleaned config still yields the frozen production defaults.
"""

from __future__ import annotations

import types
from pathlib import Path

import yaml

from scripts.temporal import gehi_download as _gehi_download
from scripts.temporal.gehi_download import DownloadResult
from scripts.temporal.run_adaptive_scan import execute_round_real
from scripts.temporal.run_census2023_scan import (
    CensusJob,
    resolve_census_zoom_ladder,
    run_one_anchor,
)
from scripts.temporal.scan_config import (
    CONSUMED_KEYS,
    AdaptiveScanConfig,
    load_config,
    unconsumed_keys,
)
from scripts.temporal.scan_state import Pick, Round, RoundResult, ScanState, save_scan_state

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG = PROJECT_ROOT / "configs" / "geid_anchor_presence.yaml"


# ---------------------------------------------------------------------------
# consumed-keys registry
# ---------------------------------------------------------------------------

def test_real_config_has_no_unconsumed_keys() -> None:
    """Every (section, key) in the shipped YAML must be read by code."""
    assert unconsumed_keys(REAL_CONFIG) == []


def test_dummy_keys_are_flagged(tmp_path: Path) -> None:
    """Acceptance demonstration: adding a dummy key (and a dummy dead section)
    makes unconsumed_keys report both."""
    raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    raw["adaptive_scan"]["dummy_knob"] = 1
    raw["dummy_dead_section"] = {"ghost_key": 2}
    tmp = tmp_path / "cfg.yaml"
    tmp.write_text(yaml.safe_dump(raw), encoding="utf-8")

    result = unconsumed_keys(tmp)
    assert ("adaptive_scan", "dummy_knob") in result
    assert ("dummy_dead_section", "ghost_key") in result


def test_scalar_dummy_section_is_flagged(tmp_path: Path) -> None:
    """A whole unknown section counts even when it is a bare scalar/null."""
    raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    raw["dummy_scalar_section"] = 7
    tmp = tmp_path / "cfg.yaml"
    tmp.write_text(yaml.safe_dump(raw), encoding="utf-8")

    result = unconsumed_keys(tmp)
    assert ("dummy_scalar_section", "") in result


def test_registry_matches_adaptive_scan_config_fields() -> None:
    """Cross-check both directions: every registered adaptive_scan key is an
    AdaptiveScanConfig field, and every field is registered (single-source)."""
    registered = {key for section, key in CONSUMED_KEYS if section == "adaptive_scan"}
    fields = set(AdaptiveScanConfig.__dataclass_fields__)
    assert registered <= fields, f"registered but not a field: {registered - fields}"
    assert fields <= registered, f"field but not registered: {fields - registered}"


# These four knobs were populated into AdaptiveScanConfig by load_config but read
# by nothing downstream (unlike gemini_max_dates_per_call, consumed at
# run_adaptive_scan.py). Editing them silently did nothing — the exact D16 trap.
# The ISSUE-17 review removed them from all three surfaces; lock that they stay
# gone so a future editor cannot re-add the same dead lever unnoticed.
_REMOVED_DEAD_KNOBS = frozenset(
    {
        "bisection_window",
        "gemini_max_targets_per_call",
        "gemini_hard_max_targets_per_call",
        "gemini_hard_max_cells_per_call",
    }
)


def test_removed_dead_knobs_stay_gone() -> None:
    raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    yaml_keys = set(raw.get("adaptive_scan") or {})
    fields = set(AdaptiveScanConfig.__dataclass_fields__)
    registered = {key for section, key in CONSUMED_KEYS if section == "adaptive_scan"}

    assert _REMOVED_DEAD_KNOBS.isdisjoint(yaml_keys), (
        f"dead knob back in YAML: {_REMOVED_DEAD_KNOBS & yaml_keys}"
    )
    assert _REMOVED_DEAD_KNOBS.isdisjoint(fields), (
        f"dead knob back on AdaptiveScanConfig: {_REMOVED_DEAD_KNOBS & fields}"
    )
    assert _REMOVED_DEAD_KNOBS.isdisjoint(registered), (
        f"dead knob back in CONSUMED_KEYS: {_REMOVED_DEAD_KNOBS & registered}"
    )


# ---------------------------------------------------------------------------
# default parity (no behaviour change)
# ---------------------------------------------------------------------------

def test_default_parity_on_real_config() -> None:
    config = load_config(REAL_CONFIG)
    assert config.download_zoom_ladder == (20, 19, 18)
    assert config.census2023_zoom_ladder == (19, 18)


def test_dataclass_defaults_match_frozen_production() -> None:
    default = AdaptiveScanConfig()
    assert default.download_zoom_ladder == (20, 19, 18)
    assert default.census2023_zoom_ladder == (19, 18)


# ---------------------------------------------------------------------------
# ladder governs the adaptive-scan download invocation
# ---------------------------------------------------------------------------

def _ok_download(path: Path, *, zoom: int = 17) -> DownloadResult:
    return DownloadResult(
        anchor_id="a",
        capture_date="2020-06-15",
        version="100",
        requested_zoom_ladder=(zoom,),
        actual_zoom=zoom,
        path=path,
        sha256="deadbeef",
        status="ok",
        error=None,
        gehi_command="",
        download_stdout_sha256="",
    )


def _failed_download() -> DownloadResult:
    return DownloadResult(
        anchor_id="a",
        capture_date="2020-06-15",
        version="100",
        requested_zoom_ladder=(17,),
        actual_zoom=None,
        path=None,
        sha256="",
        status="all_zooms_failed",
        error="simulated",
        gehi_command="",
        download_stdout_sha256="",
    )


def test_download_ladder_governs_adaptive_scan(tmp_path: Path, monkeypatch) -> None:
    """A custom adaptive_scan.download_zoom_ladder reaches the orchestrator's
    download call verbatim."""
    raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    raw["adaptive_scan"]["download_zoom_ladder"] = [17]
    tmp = tmp_path / "cfg.yaml"
    tmp.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(tmp)
    assert config.download_zoom_ladder == (17,)

    captured: dict[str, object] = {}

    def fake_download(
        anchor, *, capture_date, version, zoom_ladder, output_root, provider="TM", vintage_check=None
    ):
        captured["zoom_ladder"] = zoom_ladder
        captured["provider"] = provider
        return _failed_download()

    monkeypatch.setattr(_gehi_download, "download_chip_with_zoom_ladder", fake_download)

    rnd = Round(
        round_id=0,
        round_type="initial",
        window_start_date=None,
        window_end_date=None,
        picks=[Pick(chip_index=1, capture_date="2020-06-15", version=100, requested_zoom=17)],
    )
    anchor = {"anchor_id": "A1", "region_key": "johannesburg"}

    returned = execute_round_real(
        rnd,
        anchor,
        config,
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=object(),
        vintage_check=None,
        census_mid_date_iso=None,
    )

    # All downloads failed -> no Gemini call, every pick recorded gemini_failed.
    assert captured["zoom_ladder"] == (17,)
    assert captured["provider"] == "TM"
    assert returned.completed is True
    assert all(r.decision_source == "gemini_failed" for r in returned.results)


# ---------------------------------------------------------------------------
# ladder governs the census-narrowing download invocation
# ---------------------------------------------------------------------------

def test_resolve_census_zoom_ladder_uses_config_when_no_cli() -> None:
    config = AdaptiveScanConfig(census2023_zoom_ladder=(15, 14))
    assert resolve_census_zoom_ladder(None, config) == (15, 14)


def test_resolve_census_zoom_ladder_cli_overrides_config() -> None:
    config = AdaptiveScanConfig(census2023_zoom_ladder=(15, 14))
    assert resolve_census_zoom_ladder("20,19,18", config) == (20, 19, 18)
    # A blank/whitespace CLI value falls back to the config value.
    assert resolve_census_zoom_ladder("  ", config) == (15, 14)


def test_census_config_ladder_reaches_download(tmp_path: Path, monkeypatch) -> None:
    """The config-resolved census ladder reaches run_one_anchor's Wayback
    download call."""
    import scripts.temporal.run_census2023_scan as census_mod

    # Cached anchor exemplars (one absent, one present) so _anchor_frames passes.
    absent = RoundResult(
        chip_index=1, capture_date="2019-01-01", version=1, pv_present=False,
        confidence=0.9, quality_flag="usable", decision_source="gemini_batch",
        chip_path="/nonexistent/absent.png", actual_zoom=19,
    )
    present = RoundResult(
        chip_index=2, capture_date="2021-06-01", version=2, pv_present=True,
        confidence=0.9, quality_flag="usable", decision_source="gemini_batch",
        chip_path="/nonexistent/present.png", actual_zoom=19,
    )
    rnd = Round(
        round_id=0, round_type="initial", window_start_date=None, window_end_date=None,
        picks=[], results=[absent, present], completed=True,
    )
    state = ScanState(anchor_id="cohort_c1", region_key="johannesburg", grid_id="G0922", rounds=[rnd])
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    save_scan_state(state, main_dir / "cohort_c1.json")

    job = CensusJob(
        {
            "anchor_id": "cohort_c1",
            "id_kind": "c",
            "grid_id": "G0922",
            "region_key": "johannesburg",
            "latest_absent_date": "2018-06-01",
            "install_interval_end": "2022-06-01",
            "wb_2023_dates": "2020-06-15",
            "centroid_lon": "28.0",
            "centroid_lat": "-26.2",
        }
    )

    captured: dict[str, object] = {}

    def fake_download(anchor, *, capture_date, version, zoom_ladder, output_root, provider, allow_nearest):
        captured["zoom_ladder"] = zoom_ladder
        captured["provider"] = provider
        return types.SimpleNamespace(status="all_zooms_failed", path=None, actual_zoom=None)

    monkeypatch.setattr(census_mod, "download_chip_with_zoom_ladder", fake_download)

    scan_cfg = AdaptiveScanConfig(census2023_zoom_ladder=(15, 14))
    zoom_ladder = resolve_census_zoom_ladder(None, scan_cfg)

    out = run_one_anchor(
        job,
        main_dir=main_dir,
        norecent_dir=tmp_path / "norecent",
        census_chips_dir=tmp_path / "chips",
        audit_dir=None,
        config=None,  # unused: the failing-download path returns before scoring
        zoom_ladder=zoom_ladder,
        max_tokens=8192,
        routing_salt_mode="none",
        limiter=None,
        scorer=None,  # unused on the kept_no_usable_2023 path (returns pre-scoring)
    )

    assert captured["zoom_ladder"] == (15, 14)
    assert captured["provider"] == "Wayback"
    assert out["census_decision"] == "kept_no_usable_2023"
