"""Adaptive scan configuration loaded from configs/geid_anchor_presence.yaml.

`adaptive_scan:` is the file's only live section (ISSUE-17 / PRD D16 audit,
2026-07-03): `load_config` reads nothing else. `unconsumed_keys()` + the
`CONSUMED_KEYS` registry below enforce that — any YAML key no code reads fails
tests/temporal/test_config_truth.py.

Defaults match the Phase-0 design lock-in:
- Round 1 floor year = 2018 (SA PV adoption window)
- Walk-back step = 5 years with shared boundary
- 5 picks per round (anchored two ends + 3 middle)
- Tail round when remaining vintage count < 3
- Case-E (Gemini failure) anchor-level threshold = >50%
- Vintage discovery uses bbox-complete availability at z19, then z18 fallback
- Download ladder z20->z19->z18 (TM adaptive scan); census2023_zoom_ladder
  z19->z18 is the divergent Wayback ladder (see YAML comment) consumed by
  run_census2023_scan.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AdaptiveScanConfig:
    round_1_floor_year: int = 2018
    walk_back_years: int = 5
    picks_per_round: int = 5
    tail_round_threshold: int = 3
    case_e_failure_pct: float = 50.0
    spec_version: str = "phase0_v1"
    download_zoom_ladder: tuple[int, ...] = (20, 19, 18)
    discovery_zoom_ladder: tuple[int, ...] = (19, 18)
    info_zoom: int = 19
    # Wayback census-narrowing ladder (run_census2023_scan.py). Diverges from
    # download_zoom_ladder because z20 is essentially unavailable on Wayback
    # (production Wayback corpus is 97% z19); see YAML comment.
    census2023_zoom_ladder: tuple[int, ...] = (19, 18)
    require_complete_coverage_for_catalog: bool = True
    require_complete_coverage_for_download: bool = True
    catalog_min_date: str = "2009-01-01"
    catalog_max_date: str = "2025-12-31"
    availability_parallel: int = 4
    gemini_max_dates_per_call: int = 5
    max_anchor_recovery_rounds: int = 2
    # GEHI imagery provider: "TM" (Google Earth Time Machine) or "Wayback"
    # (ESRI World Imagery). Usually set from run_adaptive_scan --provider, not
    # YAML. Wayback's availability lists layer-release dates (not captured
    # dates), so the completeness gate is disabled for it by the orchestrator.
    provider: str = "TM"


def _zoom_ladder(section: dict[str, Any], key: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = section.get(key)
    if value is None:
        return default
    return tuple(int(z) for z in value)


# Every (section, key) pair load_config() actually reads. The single place to
# update when a knob is added: a new AdaptiveScanConfig field that the loader
# reads from YAML MUST get a matching entry here, or unconsumed_keys() flags a
# real-config key it forgets and the field/registry cross-check test fails.
# Written as an explicit literal (not derived from the dataclass) so the
# cross-check against AdaptiveScanConfig.__dataclass_fields__ is meaningful.
# `provider` is read (section.get("provider")) though it is normally supplied by
# the --provider CLI, not the file. Enforced both directions by
# tests/temporal/test_config_truth.py.
CONSUMED_KEYS: frozenset[tuple[str, str]] = frozenset(
    {
        ("adaptive_scan", "spec_version"),
        ("adaptive_scan", "round_1_floor_year"),
        ("adaptive_scan", "walk_back_years"),
        ("adaptive_scan", "picks_per_round"),
        ("adaptive_scan", "tail_round_threshold"),
        ("adaptive_scan", "case_e_failure_pct"),
        ("adaptive_scan", "download_zoom_ladder"),
        ("adaptive_scan", "discovery_zoom_ladder"),
        ("adaptive_scan", "info_zoom"),
        ("adaptive_scan", "census2023_zoom_ladder"),
        ("adaptive_scan", "require_complete_coverage_for_catalog"),
        ("adaptive_scan", "require_complete_coverage_for_download"),
        ("adaptive_scan", "catalog_min_date"),
        ("adaptive_scan", "catalog_max_date"),
        ("adaptive_scan", "availability_parallel"),
        ("adaptive_scan", "gemini_max_dates_per_call"),
        ("adaptive_scan", "max_anchor_recovery_rounds"),
        ("adaptive_scan", "provider"),
    }
)


def unconsumed_keys(path: Path) -> list[tuple[str, str]]:
    """Return every (section, key) present in the YAML but absent from CONSUMED_KEYS.

    A whole unknown section counts: each of its keys is reported (a scalar/null
    section body is reported as ``(section, "")``). Pure function — no side
    effects, loads and inspects only the file at ``path``. An empty result means
    every key in the file is read by code.
    """
    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    unconsumed: list[tuple[str, str]] = []
    for section, body in raw.items():
        if isinstance(body, dict):
            for key in body:
                if (section, key) not in CONSUMED_KEYS:
                    unconsumed.append((section, key))
        elif (section, "") not in CONSUMED_KEYS:
            unconsumed.append((section, ""))
    return unconsumed


def load_config(path: Path | None = None) -> AdaptiveScanConfig:
    if path is None or not path.exists():
        return AdaptiveScanConfig()
    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    section = raw.get("adaptive_scan") or {}
    info_zoom = int(section.get("info_zoom", 19))
    download_ladder = _zoom_ladder(section, "download_zoom_ladder", AdaptiveScanConfig().download_zoom_ladder)
    census2023_ladder = _zoom_ladder(
        section, "census2023_zoom_ladder", AdaptiveScanConfig().census2023_zoom_ladder
    )
    if section.get("discovery_zoom_ladder") is not None:
        discovery_ladder = _zoom_ladder(
            section, "discovery_zoom_ladder", AdaptiveScanConfig().discovery_zoom_ladder
        )
    elif section.get("info_zoom") is not None:
        discovery_ladder = (info_zoom,)
    else:
        discovery_ladder = AdaptiveScanConfig().discovery_zoom_ladder
    return AdaptiveScanConfig(
        round_1_floor_year=int(section.get("round_1_floor_year", 2018)),
        walk_back_years=int(section.get("walk_back_years", 5)),
        picks_per_round=int(section.get("picks_per_round", 5)),
        tail_round_threshold=int(section.get("tail_round_threshold", 3)),
        case_e_failure_pct=float(section.get("case_e_failure_pct", 50.0)),
        spec_version=str(section.get("spec_version", "phase0_v1")),
        download_zoom_ladder=download_ladder,
        discovery_zoom_ladder=discovery_ladder,
        info_zoom=info_zoom,
        census2023_zoom_ladder=census2023_ladder,
        require_complete_coverage_for_catalog=bool(section.get("require_complete_coverage_for_catalog", True)),
        require_complete_coverage_for_download=bool(section.get("require_complete_coverage_for_download", True)),
        catalog_min_date=str(section.get("catalog_min_date", "2009-01-01")),
        catalog_max_date=str(section.get("catalog_max_date", "2025-12-31")),
        availability_parallel=int(section.get("availability_parallel", 4)),
        gemini_max_dates_per_call=int(section.get("gemini_max_dates_per_call", 5)),
        max_anchor_recovery_rounds=int(section.get("max_anchor_recovery_rounds", 2)),
        provider=str(section.get("provider", "TM")),
    )
