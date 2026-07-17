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


def test_cached_higher_zoom_must_pass_vintage_check(anchor, tmp_path: Path) -> None:
    """A stale cached z=20 chip must not bypass the bbox-complete gate."""
    z20_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2015-08-30", "200", 20)
    z20_path.parent.mkdir(parents=True, exist_ok=True)
    z20_path.write_bytes(b"STALE_Z20")
    z19_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2015-08-30", "200", 19)
    z19_path.parent.mkdir(parents=True, exist_ok=True)
    z19_path.write_bytes(b"VALID_Z19")

    def vintage_check(zoom: int, capture_date: str) -> bool:
        return zoom == 19 and capture_date == "2015-08-30"

    runner = _make_runner({}, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2015-08-30",
        version=200,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
        vintage_check=vintage_check,
    )
    assert result.status == "skipped_existing"
    assert result.actual_zoom == 19
    assert result.path == z19_path
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


def test_failed_attempt_partial_file_is_quarantined(anchor, tmp_path: Path) -> None:
    """A failed GEHI run that wrote a non-empty partial must not poison idempotent re-runs."""
    plan = {
        20: {"returncode": 2, "writes_file": True, "stderr": "broken at z=20 but wrote partial"},
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
    z20_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2015-08-30", "200", 20)
    assert not z20_path.exists(), "leftover partial at failed z=20 must be deleted"
    z19_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2015-08-30", "200", 19)
    assert z19_path.exists()


def test_idempotent_skip_does_not_pick_quarantined_partial(anchor, tmp_path: Path) -> None:
    """Re-running after a partial-cleanup must not silently use the bad file."""
    plan_run1 = {
        20: {"returncode": 2, "writes_file": True, "stderr": "broken"},
        19: {"returncode": 0, "writes_file": True},
    }
    runner1 = _make_runner(plan_run1, tmp_path)
    download_chip_with_zoom_ladder(
        anchor, capture_date="2015-08-30", version=200,
        zoom_ladder=(20, 19), output_root=tmp_path, runner=runner1,
    )
    runner2 = _make_runner({}, tmp_path)
    result2 = download_chip_with_zoom_ladder(
        anchor, capture_date="2015-08-30", version=200,
        zoom_ladder=(20, 19), output_root=tmp_path, runner=runner2,
    )
    assert result2.status == "skipped_existing"
    assert result2.actual_zoom == 19, "must NOT report z=20 since the partial was quarantined"
    assert len(runner2.calls) == 0


def test_runner_exception_quarantines_partial(anchor, tmp_path: Path) -> None:
    """Exception path must also clean up any partial file the runner wrote before raising."""
    z20_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2024-06-15", "999", 20)

    def runner(cmd_args, *, executable, timeout):
        zoom = None
        out_path = None
        for i, arg in enumerate(cmd_args):
            if arg == "--zoom":
                zoom = int(cmd_args[i + 1])
            elif arg == "--output":
                out_path = Path(cmd_args[i + 1])
        if zoom == 20:
            assert out_path is not None
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"PARTIAL_BEFORE_TIMEOUT")
            raise TimeoutError("simulated mid-write timeout")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"OK_AT_Z19")
        return GehiRunResult(args=tuple(str(a) for a in cmd_args), returncode=0, stdout="", stderr="")

    result = download_chip_with_zoom_ladder(
        anchor, capture_date="2024-06-15", version=999,
        zoom_ladder=(20, 19), output_root=tmp_path, runner=runner,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 19
    assert not z20_path.exists()


def test_vintage_check_skips_zoom_when_date_not_in_catalog(anchor, tmp_path: Path) -> None:
    """When provenance catalog says z=20 has no vintage for this date, skip without calling GEHI."""
    catalogs = {20: {"2024-06-15"}, 19: {"2015-08-30", "2024-06-15"}}

    def vintage_check(zoom: int, capture_date: str) -> bool:
        return capture_date in catalogs.get(zoom, set())

    plan = {19: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2015-08-30",
        version=200,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
        vintage_check=vintage_check,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 19
    assert [c["zoom"] for c in runner.calls] == [19], "z=20 must be skipped without GEHI call"


def test_vintage_check_passes_when_catalog_has_date(anchor, tmp_path: Path) -> None:
    catalogs = {20: {"2024-06-15"}}

    def vintage_check(zoom: int, capture_date: str) -> bool:
        return capture_date in catalogs.get(zoom, set())

    plan = {20: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=999,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
        vintage_check=vintage_check,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 20
    assert len(runner.calls) == 1


def test_vintage_check_all_zooms_excluded_returns_failed(anchor, tmp_path: Path) -> None:
    def always_false(_zoom: int, _date: str) -> bool:
        return False

    runner = _make_runner({}, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2030-01-01",
        version=1,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=runner,
        vintage_check=always_false,
    )
    assert result.status == "all_zooms_failed"
    assert "vintage_check_failed" in (result.error or "")
    assert len(runner.calls) == 0


def test_runner_exception_falls_through_to_next_zoom(anchor, tmp_path: Path) -> None:
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


def test_min_cache_zoom_forces_refetch_of_low_zoom_cache(anchor, tmp_path: Path) -> None:
    """THE acceptance test: a chip pinned at z=19 in the cache must be re-fetched
    when min_cache_zoom=20 forces the ladder to run live and upgrade it."""
    pre_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2024-06-15", "12345", 19)
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"CACHED_AT_Z19")
    plan = {20: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        min_cache_zoom=20,
        runner=runner,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 20
    assert len(runner.calls) == 1
    assert runner.calls[0]["zoom"] == 20


def test_control_without_min_cache_zoom_uses_cached_low_zoom(anchor, tmp_path: Path) -> None:
    """Control for the escape-hatch test: same staged z=19 cache, no min_cache_zoom,
    returns the cached chip (skipped_existing at z=19) with no GEHI call."""
    pre_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2024-06-15", "12345", 19)
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"CACHED_AT_Z19")
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
    assert result.actual_zoom == 19
    assert len(runner.calls) == 0


def test_min_cache_zoom_emits_skip_reason_log(anchor, tmp_path: Path) -> None:
    """Rejecting a below-min cached chip must emit a raw_log record carrying
    skip_reason='cache_below_min_zoom' and the pinned cached zoom."""
    pre_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2024-06-15", "12345", 19)
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"CACHED_AT_Z19")
    plan = {20: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    logs: list[dict[str, Any]] = []
    download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        min_cache_zoom=20,
        runner=runner,
        raw_log_callback=logs.append,
    )
    skip_logs = [r for r in logs if r.get("skip_reason") == "cache_below_min_zoom"]
    assert len(skip_logs) == 1
    assert skip_logs[0].get("cached_zoom") == 19
    assert skip_logs[0].get("min_cache_zoom") == 20


def test_min_cache_zoom_does_not_loosen_live_attempts(anchor, tmp_path: Path) -> None:
    """min_cache_zoom governs CACHE acceptance only: a fresh live z=19 download is
    still legal when z=20 has no vintage, even with min_cache_zoom=20 set."""
    plan = {
        20: {"returncode": 2, "writes_file": False, "stderr": "no z=20"},
        19: {"returncode": 0, "writes_file": True},
    }
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2015-08-30",
        version=200,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        min_cache_zoom=20,
        runner=runner,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 19
    assert [c["zoom"] for c in runner.calls] == [20, 19]


def test_overwrite_bypasses_even_with_min_cache_zoom(anchor, tmp_path: Path) -> None:
    """overwrite=True bypasses the cache entirely regardless of min_cache_zoom."""
    pre_path = _chip_path_for(tmp_path, anchor["anchor_id"], "2024-06-15", "12345", 20)
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"OLD_Z20")
    plan = {20: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        overwrite=True,
        min_cache_zoom=20,
        runner=runner,
    )
    assert result.status == "ok"
    assert len(runner.calls) == 1


def test_manifest_zoom_is_requested_not_actual(anchor, tmp_path: Path, monkeypatch) -> None:
    """The manifest `zoom` column records the REQUESTED zoom (ladder head),
    which is distinct from `actual_zoom` whenever the ladder falls back.

    Locks the fix that stopped `zoom` from being a dead alias of `actual_zoom`.
    """
    import csv as _csv
    import sys as _sys

    from scripts.temporal import gehi_download

    # z=20 fails -> falls back to z=19, so requested(20) != actual(19).
    plan = {
        20: {"returncode": 2, "writes_file": False, "stderr": "no z=20"},
        19: {"returncode": 0, "writes_file": True},
    }
    runner = _make_runner(plan, tmp_path)
    # main() drives the default `run_gehi`; redirect it to the simulated runner.
    monkeypatch.setattr(gehi_download, "run_gehi", runner)

    anchors_csv = tmp_path / "anchors.csv"
    with anchors_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = _csv.DictWriter(fh, fieldnames=list(anchor.keys()))
        writer.writeheader()
        writer.writerow(anchor)

    candidates_csv = tmp_path / "candidates.csv"
    with candidates_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = _csv.DictWriter(fh, fieldnames=["anchor_id", "capture_date", "version"])
        writer.writeheader()
        writer.writerow({"anchor_id": anchor["anchor_id"], "capture_date": "2015-08-30", "version": "277"})

    manifest_csv = tmp_path / "manifest.csv"
    raw_log = tmp_path / "raw.jsonl"
    output_dir = tmp_path / "chips"

    argv = [
        "gehi_download.py",
        "--anchors-csv", str(anchors_csv),
        "--candidates-csv", str(candidates_csv),
        "--output-dir", str(output_dir),
        "--manifest", str(manifest_csv),
        "--raw-log", str(raw_log),
        "--zoom", "20,19",
    ]
    monkeypatch.setattr(_sys, "argv", argv)
    gehi_download.main()

    with manifest_csv.open("r", newline="", encoding="utf-8") as fh:
        rows = list(_csv.DictReader(fh))
    assert len(rows) == 1
    row = rows[0]
    assert row["zoom"] == "20", "manifest `zoom` must be the requested ladder head"
    assert row["actual_zoom"] == "19", "manifest `actual_zoom` must be the served zoom"
    assert row["zoom"] != row["actual_zoom"], "zoom (requested) must differ from actual_zoom on fallback"
    assert row["requested_zoom_ladder"] == "20,19"


class _ZeroRng:
    def uniform(self, a: float, b: float) -> float:
        return 0.0


def _fake_limiter(sleeps: list[float]):
    """A GehiRateLimiter on a fake clock so tests never really sleep."""
    from scripts.temporal.gehi_common import GehiRateLimiter

    clock = {"t": 0.0}

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    return GehiRateLimiter(
        min_interval_s=0.0,
        soft_backoff_s=7.0,
        hard_backoff_s=100.0,
        hard_after=5,
        sleep_fn=sleep,
        monotonic_fn=lambda: clock["t"],
        rng=_ZeroRng(),
    )


def test_default_runner_late_binds_stubbed_run_gehi(anchor, tmp_path: Path, monkeypatch) -> None:
    """Calling without runner= must reach the module-global run_gehi at call time
    (so monkeypatch — and therefore main()'s test seam — works), wrapped in the
    throttle. The old def-time default froze the original run_gehi and made the
    stub unreachable."""
    from scripts.temporal import gehi_download

    plan = {19: {"returncode": 0, "writes_file": True}}
    stub = _make_runner(plan, tmp_path)
    monkeypatch.setattr(gehi_download, "run_gehi", stub)

    sleeps: list[float] = []
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2015-08-30",
        version="277",
        zoom_ladder=(19,),
        output_root=tmp_path,
        limiter=_fake_limiter(sleeps),
    )
    assert result.status == "ok"
    assert len(stub.calls) == 1


def test_blocked_response_retried_with_backoff(anchor, tmp_path: Path, monkeypatch) -> None:
    """A 403 block signal on stderr must be retried at the SAME zoom after the
    limiter's soft backoff, instead of burning the ladder rung."""
    from scripts.temporal import gehi_download

    calls: list[int] = []

    def stub(cmd_args, *, executable, timeout):
        calls.append(1)
        out_path = Path(cmd_args[list(cmd_args).index("--output") + 1])
        if len(calls) == 1:
            return GehiRunResult(
                args=tuple(str(a) for a in cmd_args),
                returncode=1,
                stdout="",
                stderr="Response status code does not indicate success: 403 (Forbidden)",
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"FAKE_TIFF_DATA_FOR_TEST")
        return GehiRunResult(args=tuple(str(a) for a in cmd_args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gehi_download, "run_gehi", stub)

    sleeps: list[float] = []
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2015-08-30",
        version="277",
        zoom_ladder=(19,),
        output_root=tmp_path,
        limiter=_fake_limiter(sleeps),
        max_attempts=3,
    )
    assert result.status == "ok"
    assert result.actual_zoom == 19
    assert len(calls) == 2
    assert 7.0 in sleeps, "soft backoff must fire after the blocked attempt"


# ---------------------------------------------------------------------------
# JPEG-in-GeoTIFF recompression (disk-size optimization, CoJ precedent)
# ---------------------------------------------------------------------------


def _write_real_geotiff(path: Path, *, count: int = 3, dtype: str = "uint8") -> None:
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    path.parent.mkdir(parents=True, exist_ok=True)
    gradient = np.linspace(0, 255, 64 * 64, dtype="float64").reshape(64, 64)
    data = np.stack([gradient + i for i in range(count)]).clip(0, 255).astype(dtype)
    with rasterio.open(
        path, "w", driver="GTiff", width=64, height=64, count=count, dtype=dtype,
        crs="EPSG:4326", transform=from_bounds(28.014, -26.184, 28.015, -26.183, 64, 64),
    ) as dst:
        dst.write(data)


def test_recompress_helper_preserves_georeferencing_and_shrinks(tmp_path: Path) -> None:
    import rasterio

    from scripts.temporal.gehi_download import _recompress_jpeg_in_geotiff

    chip = tmp_path / "chip.tif"
    _write_real_geotiff(chip)
    with rasterio.open(chip) as src:
        crs_before, transform_before = src.crs, src.transform
    size_before = chip.stat().st_size

    assert _recompress_jpeg_in_geotiff(chip) is None
    with rasterio.open(chip) as src:
        assert src.profile["compress"].upper() == "JPEG"
        assert src.crs == crs_before
        assert src.transform == transform_before
        assert src.count == 3
    assert chip.stat().st_size < size_before

    # Idempotent: already-JPEG chips are left untouched (resume safety).
    sha_after = chip.read_bytes()
    assert _recompress_jpeg_in_geotiff(chip) is None
    assert chip.read_bytes() == sha_after


def test_recompress_helper_refuses_unsupported_shapes(tmp_path: Path) -> None:
    from scripts.temporal.gehi_download import _recompress_jpeg_in_geotiff

    two_band = tmp_path / "two_band.tif"
    _write_real_geotiff(two_band, count=2)
    err = _recompress_jpeg_in_geotiff(two_band)
    assert err is not None and "band count" in err

    wide = tmp_path / "wide.tif"
    _write_real_geotiff(wide, dtype="uint16")
    err = _recompress_jpeg_in_geotiff(wide)
    assert err is not None and "dtype" in err


def test_ladder_recompresses_fresh_download(anchor, tmp_path: Path) -> None:
    import rasterio

    def runner(cmd_args, *, executable, timeout):
        out_path = Path(cmd_args[cmd_args.index("--output") + 1])
        _write_real_geotiff(out_path)
        return GehiRunResult(args=tuple(str(a) for a in cmd_args), returncode=0, stdout="", stderr="")

    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(19,),
        output_root=tmp_path,
        runner=runner,
        recompress=True,
    )
    assert result.status == "ok"
    with rasterio.open(result.path) as src:
        assert src.profile["compress"].upper() == "JPEG"
    # DownloadResult.sha256 must hash the recompressed bytes, not the originals.
    assert result.sha256 == __import__("hashlib").sha256(result.path.read_bytes()).hexdigest()


def test_ladder_recompress_failure_keeps_chip_and_logs(anchor, tmp_path: Path) -> None:
    plan = {19: {"returncode": 0, "writes_file": True}}  # writes non-TIFF fake bytes
    runner = _make_runner(plan, tmp_path)
    records: list[dict] = []
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(19,),
        output_root=tmp_path,
        runner=runner,
        recompress=True,
        raw_log_callback=records.append,
    )
    assert result.status == "ok"
    assert result.path.read_bytes() == b"FAKE_TIFF_DATA_FOR_TEST"
    recompress_records = [r for r in records if "recompress_error" in r]
    assert len(recompress_records) == 1


def test_ladder_recompress_defaults_off_for_library_callers(anchor, tmp_path: Path) -> None:
    plan = {19: {"returncode": 0, "writes_file": True}}
    runner = _make_runner(plan, tmp_path)
    records: list[dict] = []
    result = download_chip_with_zoom_ladder(
        anchor,
        capture_date="2024-06-15",
        version=12345,
        zoom_ladder=(19,),
        output_root=tmp_path,
        runner=runner,
        raw_log_callback=records.append,
    )
    assert result.status == "ok"
    assert result.path.read_bytes() == b"FAKE_TIFF_DATA_FOR_TEST"
    assert not any("recompress_error" in r for r in records)
