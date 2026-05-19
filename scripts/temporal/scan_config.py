"""Adaptive scan configuration loaded from configs/geid_anchor_presence.yaml.

Defaults match the Phase-0 design lock-in:
- Round 1 floor year = 2018 (SA PV adoption window)
- Walk-back step = 5 years with shared boundary
- 5 picks per round (anchored two ends + 3 middle)
- Tail round when remaining vintage count < 3
- Case-E (Gemini failure) anchor-level threshold = >50%
- Bisection uses open interval; merged endpoints come from cached results
- Vintage discovery uses bbox-complete availability at z19, then z18 fallback
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
    bisection_window: str = "open"
    spec_version: str = "phase0_v1"
    download_zoom_ladder: tuple[int, ...] = (20, 19, 18)
    discovery_zoom_ladder: tuple[int, ...] = (19, 18)
    info_zoom: int = 19
    require_complete_coverage_for_catalog: bool = True
    require_complete_coverage_for_download: bool = True
    catalog_min_date: str = "2009-01-01"
    catalog_max_date: str = "2025-12-31"
    availability_parallel: int = 4
    gemini_max_dates_per_call: int = 5
    gemini_max_targets_per_call: int = 4
    gemini_hard_max_targets_per_call: int = 6
    gemini_hard_max_cells_per_call: int = 24
    max_anchor_recovery_rounds: int = 2


def _zoom_ladder(section: dict[str, Any], key: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = section.get(key)
    if value is None:
        return default
    return tuple(int(z) for z in value)


def load_config(path: Path | None = None) -> AdaptiveScanConfig:
    if path is None or not path.exists():
        return AdaptiveScanConfig()
    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    section = raw.get("adaptive_scan") or {}
    info_zoom = int(section.get("info_zoom", 19))
    download_ladder = _zoom_ladder(section, "download_zoom_ladder", AdaptiveScanConfig().download_zoom_ladder)
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
        bisection_window=str(section.get("bisection_window", "open")),
        spec_version=str(section.get("spec_version", "phase0_v1")),
        download_zoom_ladder=download_ladder,
        discovery_zoom_ladder=discovery_ladder,
        info_zoom=info_zoom,
        require_complete_coverage_for_catalog=bool(section.get("require_complete_coverage_for_catalog", True)),
        require_complete_coverage_for_download=bool(section.get("require_complete_coverage_for_download", True)),
        catalog_min_date=str(section.get("catalog_min_date", "2009-01-01")),
        catalog_max_date=str(section.get("catalog_max_date", "2025-12-31")),
        availability_parallel=int(section.get("availability_parallel", 4)),
        gemini_max_dates_per_call=int(section.get("gemini_max_dates_per_call", 5)),
        gemini_max_targets_per_call=int(section.get("gemini_max_targets_per_call", 4)),
        gemini_hard_max_targets_per_call=int(section.get("gemini_hard_max_targets_per_call", 6)),
        gemini_hard_max_cells_per_call=int(section.get("gemini_hard_max_cells_per_call", 24)),
        max_anchor_recovery_rounds=int(section.get("max_anchor_recovery_rounds", 2)),
    )
