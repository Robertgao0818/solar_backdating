"""Adaptive scan configuration loaded from configs/geid_anchor_presence.yaml.

Defaults match the Phase-0 design lock-in:
- Round 1 floor year = 2018 (SA PV adoption window)
- Walk-back step = 5 years with shared boundary
- 5 picks per round (anchored two ends + 3 middle)
- Tail round when remaining vintage count < 3
- Case-E (Gemini failure) anchor-level threshold = >50%
- Bisection uses open interval; merged endpoints come from cached results
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
    download_zoom_ladder: tuple[int, ...] = (20, 19)
    info_zoom: int = 19
    max_anchor_recovery_rounds: int = 2


def load_config(path: Path | None = None) -> AdaptiveScanConfig:
    if path is None or not path.exists():
        return AdaptiveScanConfig()
    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    section = raw.get("adaptive_scan") or {}
    ladder = section.get("download_zoom_ladder")
    if ladder is None:
        ladder_tuple: tuple[int, ...] = AdaptiveScanConfig.__dataclass_fields__[
            "download_zoom_ladder"
        ].default
    else:
        ladder_tuple = tuple(int(z) for z in ladder)
    return AdaptiveScanConfig(
        round_1_floor_year=int(section.get("round_1_floor_year", 2018)),
        walk_back_years=int(section.get("walk_back_years", 5)),
        picks_per_round=int(section.get("picks_per_round", 5)),
        tail_round_threshold=int(section.get("tail_round_threshold", 3)),
        case_e_failure_pct=float(section.get("case_e_failure_pct", 50.0)),
        bisection_window=str(section.get("bisection_window", "open")),
        spec_version=str(section.get("spec_version", "phase0_v1")),
        download_zoom_ladder=ladder_tuple,
        info_zoom=int(section.get("info_zoom", 19)),
        max_anchor_recovery_rounds=int(section.get("max_anchor_recovery_rounds", 2)),
    )
