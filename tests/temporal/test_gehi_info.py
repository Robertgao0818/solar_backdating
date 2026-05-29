"""Tests for scripts/temporal/gehi_info.py resilience + CSV field contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal import gehi_info


def _anchor() -> dict[str, object]:
    return {
        "anchor_id": "a000001",
        "region_key": "jhb",
        "grid_id": "G0816",
        "centroid_lat": -26.2041,
        "centroid_lon": 28.0473,
    }


def test_fetch_vintages_swallows_timeout(capsys):
    """A runner raising TimeoutExpired must not propagate; degrade to []."""

    def _timeout_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="GEHistoricalImagery info", timeout=300.0)

    result = gehi_info.fetch_vintages_for_anchor(
        _anchor(),
        runner=_timeout_runner,
        timeout=300.0,
    )

    assert result == []
    captured = capsys.readouterr()
    assert "a000001" in captured.err
    assert "timed out" in captured.err.lower()


def test_fetch_vintages_swallows_broad_exception(capsys):
    """Any runner exception (mirroring download resilience) degrades to []."""

    def _boom_runner(*args, **kwargs):
        raise RuntimeError("subprocess pipe died")

    result = gehi_info.fetch_vintages_for_anchor(
        _anchor(),
        runner=_boom_runner,
    )

    assert result == []
    captured = capsys.readouterr()
    assert "a000001" in captured.err


def test_fields_contains_dedupe_metadata():
    """Contract guard: dedupe metadata must survive into the CSV FIELDS."""
    assert "all_versions" in gehi_info.FIELDS
    assert "n_versions_at_date" in gehi_info.FIELDS
