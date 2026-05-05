"""Tests for `download_chip_with_zoom_ladder` (Task C).

Uses an injected `runner` to simulate GEHI subprocess outcomes — no .NET binary
required. Verifies:
- ladder fall-through on returncode != 0 / empty output
- idempotent skip when chip already exists at any ladder zoom
- DownloadResult bookkeeping (actual_zoom, status, error)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.temporal.gehi_common import GehiRunResult
from scripts.temporal.gehi_download import (
    DownloadResult,
    _chip_path_for,
    download_chip_with_zoom_ladder,
    parse_zoom_ladder,
)


@pytest.fixture
def anchor() -> dict[str, str]:
    return {
        "anchor_id": "test_anchor_a000001",
        "region_key": "johannesburg",
        "grid_id": "G0922",
        "centroid_lat": "-26.18318",
        "centroid_lon": "28.01430",
        "chip_lon_min": "28.01412",
        "chip_lat_min": "-26.18335",
        "chip_lon_max": "28.01449",
        "chip_lat_max": "-26.18302",
    }


def _make_runner(plan: dict[int, dict[str, Any]], output_root: Path):
    """Build a runner that simulates GEHI subprocess based on `plan` keyed by zoom.

    plan[zoom] = {
        "returncode": int,
        "writes_file": bool,    # whether the runner pretends GEHI created the output
        "stdout": str = "",
        "stderr": str = "",
    }
    """
    calls: list[dict[str, Any]] = []

    def runner(cmd_args, *, executable, timeout):
        zoom = None
        out_path = None
        for i, arg in enumerate(cmd_args):
            if arg == "--zoom":
                zoom = int(cmd_args[i + 1])
            elif arg == "--output":
                out_path = Path(cmd_args[i + 1])
        assert zoom in plan, f"runner asked for zoom={zoom} but no plan entry"
        entry = plan[zoom]
        calls.append({"zoom": zoom, "out_path": out_path})
        if entry.get("writes_file") and out_path is not None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"FAKE_TIFF_DATA_FOR_TEST")
        return GehiRunResult(
            args=tuple(str(a) for a in cmd_args),
            returncode=entry.get("returncode", 0),
            stdout=entry.get("stdout", ""),
            stderr=entry.get("stderr", ""),
        )

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_parse_zoom_ladder_single() -> None:
    assert parse_zoom_ladder("19") == (19,)


def test_parse_zoom_ladder_multi() -> None:
    assert parse_zoom_ladder("20,19") == (20, 19)
    assert parse_zoom_ladder("20, 19, 18") == (20, 19, 18)


def test_parse_zoom_ladder_empty_rejected() -> None:
    with pytest.raises(ValueError, match="empty zoom ladder"):
        parse_zoom_ladder("")


def test_zoom_ladder_succeeds_on_first_zoom(anchor, tmp_path: Path) -> None:
    plan = {20: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 20
    assert result.path is not None and result.path.exists()
    assert len(runner.calls) == 1
    assert runner.calls[0]["zoom"] == 20


def test_zoom_ladder_falls_back_on_returncode_failure(anchor, tmp_path: Path) -> None:
    plan = {
        20: {"returncode": 2, "writes_file": False, "stderr": "Vintage not at z=20"},
        19: {"returncode": 0, "writes_file": True},
    }
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2015-08-30",
        version=200,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 19
    assert len(runner.calls) == 2
    assert [c["zoom"] for c in runner.calls] == [20, 19]


def test_zoom_ladder_falls_back_on_empty_output(anchor, tmp_path: Path) -> None:
    plan = {
        20: {"returncode": 0, "writes_file": False},
        19: {"returncode": 0, "writes_file": True},
    }
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2020-01-15",
        version=300,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 19
    assert len(runner.calls) == 2


def test_zoom_ladder_all_zooms_failed(anchor, tmp_path: Path) -> None:
    plan = {
        20: {"returncode": 2, "writes_file": False, "stderr": "no z=20"},
        19: {"returncode": 2, "writes_file": False, "stderr": "no z=19"},
    }
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2010-06-01",
        version=400,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
    )
    assert result.status == "all_zooms_failed"
    assert result.actual_zoom is None
    assert result.path is None
    assert "z=19" in (result.error or "")


def test_idempotent_skip_returns_existing_at_higher_zoom(anchor, tmp_path: Path) -> None:
    pre_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2024-06-15", "12345", 20)
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"PRE_EXISTING_AT_Z20")
    runner = _make_runner({}, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
    )
    assert result.status == "skipped_existing"
    assert result.actual_zoom == 20
    assert result.path == pre_path
    assert len(runner.calls) == 0


def test_idempotent_skip_falls_back_to_lower_zoom_on_disk(anchor, tmp_path: Path) -> None:
    """When only z=19 has a cached chip, idempotent skip uses z=19 (no GEHI call)."""
    pre_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2015-08-30", "200", 19)
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"PRE_EXISTING_AT_Z19")
    runner = _make_runner({}, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2015-08-30",
        version=200,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
    )
    assert result.status == "skipped_existing"
    assert result.actual_zoom == 19
    assert len(runner.calls) == 0


def test_overwrite_bypasses_idempotent_skip(anchor, tmp_path: Path) -> None:
    pre_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2024-06-15", "12345", 20)
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"OLD")
    plan = {20: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        overwrite=True,
        runner=runner,
    )
    assert result.status == "ok"
    assert len(runner.calls) == 1


def test_empty_existing_file_does_not_count_as_skip(anchor, tmp_path: Path) -> None:
    """Zero-byte chip files (e.g., from interrupted prior run) trigger re-download."""
    pre_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2024-06-15", "12345", 20)
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"")
    plan = {20: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
    )
    assert result.status == "ok"
    assert len(runner.calls) == 1


def test_single_zoom_ladder_acts_as_pre_ladder_default(anchor, tmp_path: Path) -> None:
    plan = {19: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2018-06-15",
        version=500,
        zoom_ladder=(19,),
        output_root=tmp_path,
        runner=runner,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 19
    assert len(runner.calls) == 1


def test_runner_exception_falls_through_to_next_zoom(anchor, tmp_path: Path) -> None:
    plan = {
        20: {"returncode": 0, "writes_file": True},
        19: {"returncode": 0, "writes_file": True},
    }

    def raising_runner(cmd_args, *, executable, timeout):
        zoom = None
        for i, arg in enumerate(cmd_args):
            if arg == "--zoom":
                zoom = int(cmd_args[i + 1])
        if zoom == 20:
            raise TimeoutError("simulated timeout")
        out_path = None
        for i, arg in enumerate(cmd_args):
            if arg == "--output":
                out_path = Path(cmd_args[i + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"OK_AT_Z19")
        return GehiRunResult(
            args=tuple(str(a) for a in cmd_args),
            returncode=0,
            stdout="",
            stderr="",
        )

    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=999,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=raising_runner,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 19
