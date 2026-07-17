"""--no-live-gehi: guarantee the adaptive scan never spawns a GEHI subprocess.

Root cause this flag closes (2026-07-16 a24_v6 khmdb-ban stall): even with
both --offline-tm-catalog-csv and --offline-wayback set, a picked (date,
version) whose chip is missing from the pre-downloaded basemap on disk still
fell through to a LIVE `download_chip_with_zoom_ladder` GEHI call, which then
403/429-blocked and hung the worker in `GehiRateLimiter` backoff. A second,
narrower live-call surface is an anchor entirely absent from the offline
catalog CSV, which fell back to a live TM/Wayback catalog fetch.

Covers the three things --no-live-gehi must guarantee:
- missing chip on disk -> recorded via the EXISTING all_zooms_failed /
  download_failed path, with `runner` never invoked (a poison-pill runner
  would fail the test if called).
- an anchor absent from the offline catalog CSV -> `_fetch_real_vintage_catalog`
  raises loudly instead of falling back to a live fetch.
- `make_vintage_check`'s lazy per-zoom fetch (the other live-call surface)
  also raises loudly rather than degrading silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.temporal.gehi_availability as ga
import scripts.temporal.gehi_info as gi
from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal.gehi_download import _chip_path_for, download_chip_with_zoom_ladder
from scripts.temporal.scan_config import AdaptiveScanConfig

ANCHOR = {
    "anchor_id": "no_live_gehi_test_anchor",
    "region_key": "johannesburg",
    "grid_id": "G0001",
    "centroid_lat": "-26.18318",
    "centroid_lon": "28.01430",
    "chip_lon_min": "28.01412",
    "chip_lat_min": "-26.18335",
    "chip_lon_max": "28.01449",
    "chip_lat_max": "-26.18302",
}


def _poison_runner():
    """A runner that fails the test if `download_chip_with_zoom_ladder` ever
    calls it -- the whole point of `no_live_gehi` is that it must not."""

    def runner(cmd_args, *, executable, timeout):
        raise AssertionError(f"runner invoked under no_live_gehi=True: {cmd_args!r}")

    return runner


# ---------------------------------------------------------------------------
# download_chip_with_zoom_ladder: missing-chip path
# ---------------------------------------------------------------------------


def test_missing_chip_fails_without_calling_runner(tmp_path: Path) -> None:
    result = download_chip_with_zoom_ladder(
        ANCHOR,
        capture_date="2020-05-31",
        version="noversion",
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=_poison_runner(),
        no_live_gehi=True,
    )
    assert result.status == "all_zooms_failed"
    assert result.actual_zoom is None
    assert result.path is None
    assert "no_live_gehi" in (result.error or "")


def test_missing_chip_emits_raw_log_skip_reason(tmp_path: Path) -> None:
    logged: list[dict] = []
    download_chip_with_zoom_ladder(
        ANCHOR,
        capture_date="2020-05-31",
        version="noversion",
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=_poison_runner(),
        raw_log_callback=logged.append,
        no_live_gehi=True,
    )
    assert len(logged) == 1
    assert logged[0]["skip_reason"] == "no_live_gehi_chip_missing"
    assert logged[0]["requested_zoom_ladder"] == [20, 19]


def test_cached_chip_still_served_under_no_live_gehi(tmp_path: Path) -> None:
    """The idempotent cache scan runs BEFORE the no_live_gehi guard, so an
    already-downloaded chip is unaffected -- no_live_gehi only blocks the
    live-download fallback, not the happy (cache-hit) path."""
    pre_path = _chip_path_for(tmp_path, ANCHOR["anchor_id"], "2020-05-31", "noversion", 19)
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"FAKE_TIFF_DATA_FOR_TEST")

    result = download_chip_with_zoom_ladder(
        ANCHOR,
        capture_date="2020-05-31",
        version="noversion",
        zoom_ladder=(20, 19),
        output_root=tmp_path,
        runner=_poison_runner(),
        no_live_gehi=True,
    )
    assert result.status == "skipped_existing"
    assert result.actual_zoom == 19
    assert result.path == pre_path


def test_missing_chip_without_no_live_gehi_calls_runner(tmp_path: Path) -> None:
    """Control: without the flag, the existing behavior (live download attempt)
    is unchanged -- the poison runner IS invoked and its failure propagates as
    a `runner exception` all_zooms_failed, not a silent skip."""
    result = download_chip_with_zoom_ladder(
        ANCHOR,
        capture_date="2020-05-31",
        version="noversion",
        zoom_ladder=(19,),
        output_root=tmp_path,
        runner=_poison_runner(),
        no_live_gehi=False,
    )
    assert result.status == "all_zooms_failed"
    assert "runner exception" in (result.error or "")
    assert "no_live_gehi_chip_missing" not in (result.error or "")


# ---------------------------------------------------------------------------
# make_vintage_check: lazy per-zoom fetch guard
# ---------------------------------------------------------------------------


@pytest.fixture()
def captured_fetches(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_info(anchor, *, zoom, provider, **kwargs):
        calls.append(("info", provider))
        return [{"capture_date": "2020-01-01", "version": 100}]

    def fake_availability(anchor, *, zoom, provider, **kwargs):
        calls.append(("availability", provider))
        return [{"capture_date": "2020-01-01"}]

    monkeypatch.setattr(gi, "fetch_vintages_for_anchor", fake_info)
    monkeypatch.setattr(ga, "fetch_availability_for_anchor", fake_availability)
    return calls


def test_vintage_check_covered_zoom_unaffected_by_no_live_gehi(captured_fetches) -> None:
    config = AdaptiveScanConfig(provider="TM")
    check = ras.make_vintage_check(
        ANCHOR,
        available_dates_by_zoom={19: {"2020-01-01"}, 20: {"2020-01-01"}},
        config=config,
        no_live_gehi=True,
    )
    assert check(19, "2020-01-01") is True
    assert check(20, "2020-01-01") is True
    assert captured_fetches == []


def test_vintage_check_uncovered_zoom_raises_under_no_live_gehi(captured_fetches) -> None:
    config = AdaptiveScanConfig(provider="TM")
    check = ras.make_vintage_check(
        ANCHOR,
        available_dates_by_zoom={19: {"2020-01-01"}},
        config=config,
        no_live_gehi=True,
    )
    with pytest.raises(RuntimeError, match="no_live_gehi"):
        check(20, "2020-01-01")
    assert captured_fetches == []


def test_vintage_check_uncovered_zoom_without_flag_still_fetches_live(captured_fetches) -> None:
    """Control: the default (flag unset) lazy-fetch behavior is unchanged."""
    config = AdaptiveScanConfig(provider="TM")
    check = ras.make_vintage_check(
        ANCHOR,
        available_dates_by_zoom={19: {"2020-01-01"}},
        config=config,
    )
    assert check(20, "2020-01-01") is True
    assert captured_fetches == [("availability", "TM")]


# ---------------------------------------------------------------------------
# _fetch_real_vintage_catalog: CSV-missing anchor guard
# ---------------------------------------------------------------------------


def test_offline_tm_present_no_live_gehi_makes_zero_live_calls(captured_fetches) -> None:
    config = AdaptiveScanConfig(provider="TM")
    catalog = ras._fetch_real_vintage_catalog(
        ANCHOR, config, offline_tm_dates=["2019-03-30", "2020-05-31"], no_live_gehi=True
    )
    assert captured_fetches == []
    assert catalog.vintages


def test_offline_tm_missing_anchor_raises_under_no_live_gehi(captured_fetches) -> None:
    """The anchor-absent-from-CSV case: offline_tm_dates=None (as `handle()`
    passes when `offline_tm_catalog.get(anchor_id)` misses) must raise loudly
    instead of falling back to a live TM catalog fetch."""
    config = AdaptiveScanConfig(provider="TM")
    with pytest.raises(RuntimeError, match="no_live_gehi"):
        ras._fetch_real_vintage_catalog(
            ANCHOR, config, offline_tm_dates=None, no_live_gehi=True
        )
    assert captured_fetches == []


def test_offline_wayback_missing_anchor_raises_under_no_live_gehi(captured_fetches) -> None:
    config = AdaptiveScanConfig(provider="Wayback")
    with pytest.raises(RuntimeError, match="no_live_gehi"):
        ras._fetch_real_vintage_catalog(
            ANCHOR, config, offline_wayback_entries=None, no_live_gehi=True
        )
    assert captured_fetches == []


def test_merged_one_provider_missing_raises_even_if_other_covered(captured_fetches) -> None:
    """Merged (production topology): TM covered, Wayback anchor missing from
    its offline CSV -> still raises (the Wayback recursive call hits the
    guard), never silently falls back for just the Wayback half."""
    config = AdaptiveScanConfig(provider="Merged")
    with pytest.raises(RuntimeError, match="no_live_gehi"):
        ras._fetch_real_vintage_catalog(
            ANCHOR,
            config,
            offline_tm_dates=["2020-05-31"],
            offline_wayback_entries=None,
            no_live_gehi=True,
        )
    assert captured_fetches == []


def test_offline_missing_anchor_without_flag_falls_back_live(captured_fetches) -> None:
    """Control: without --no-live-gehi, a CSV-missing anchor's existing
    documented behavior (live fallback) is unchanged."""
    config = AdaptiveScanConfig(provider="TM")
    catalog = ras._fetch_real_vintage_catalog(
        ANCHOR, config, offline_tm_dates=None, no_live_gehi=False
    )
    assert catalog.vintages
    assert ("info", "TM") in captured_fetches


# ---------------------------------------------------------------------------
# CLI validation: --no-live-gehi requires both offline flags
# ---------------------------------------------------------------------------


def test_parse_args_no_live_gehi_flag_present(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["run_adaptive_scan.py", "--no-live-gehi"],
    )
    args = ras.parse_args()
    assert args.no_live_gehi is True


def test_parse_args_default_no_live_gehi_is_false(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_adaptive_scan.py"])
    args = ras.parse_args()
    assert args.no_live_gehi is False
