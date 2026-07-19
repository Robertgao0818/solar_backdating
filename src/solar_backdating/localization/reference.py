"""Same-domain GEHI reference-frame selection (PRD §5.2: "参考影像用同域 GEHI
最新可靠 present 帧;Vexcel 只保留 polygon/census 日期/候选位置,不作 DINO 模板
(A' KILL:跨域 cos 0.31)").

Selects, per ``(anchor_id, chip_arm)``, the reference frame the registration
stages register against -- always drawn from this repo's own RUN 3-native
GEHI corpus (never Vexcel), and always excluding the observation being
scored itself (self-pair leakage guard, PRD §0.4).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class ReferenceCandidate:
    anchor_id: str
    capture_date: date
    chip_arm: str
    src_tiff_path: Path
    label_v1: str
    quality_flag: str
    confidence: float


def select_reference(
    candidates: list[ReferenceCandidate],
    *,
    exclude_date: date,
) -> ReferenceCandidate | None:
    """Picks the reference frame for one observation from its anchor+arm's
    sibling frames.

    Preference order: latest-dated ``present`` + ``usable`` frame; else
    latest-dated ``usable`` frame of any label; else ``None`` (no eligible
    reference -- the caller must not fabricate one). ``exclude_date`` is
    always dropped from the pool first, even if it would otherwise be the
    best candidate (self-pair guard).
    """
    pool = [c for c in candidates if c.capture_date != exclude_date]

    present_usable = [c for c in pool if c.label_v1 == "present" and c.quality_flag == "usable"]
    if present_usable:
        return max(present_usable, key=lambda c: (c.capture_date, c.confidence))

    usable = [c for c in pool if c.quality_flag == "usable"]
    if usable:
        return max(usable, key=lambda c: (c.capture_date, c.confidence))

    return None
