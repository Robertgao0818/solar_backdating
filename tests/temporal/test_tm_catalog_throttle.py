"""--tm-catalog-interval plumbing: the throttled runner reaches TM catalog
calls only.

The TM (Google khmdb) metadata endpoint is the 403-ban surface (~104
calls/min sustained ~1h triggered the 2026-07-12 ban); Wayback (ESRI) has no
such limit. The full-population scan therefore injects a paced runner into
every live TM availability/info call while leaving Wayback calls and the
default (flag unset) behavior byte-identical.
"""

from __future__ import annotations

import pytest

import scripts.temporal.gehi_availability as ga
import scripts.temporal.gehi_info as gi
from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal.scan_config import AdaptiveScanConfig

ANCHOR = {
    "anchor_id": "throttle_test_anchor",
    "chip_lon_min": "28.000",
    "chip_lat_min": "-26.100",
    "chip_lon_max": "28.001",
    "chip_lat_max": "-26.099",
}

SENTINEL = object()


@pytest.fixture()
def captured_fetches(monkeypatch):
    calls: list[tuple[str, str, int, object]] = []

    def fake_info(anchor, *, zoom, provider, runner=None, **kwargs):
        calls.append(("info", provider, int(zoom), runner))
        return [{"capture_date": "2020-01-01"}]

    def fake_availability(anchor, *, zoom, provider, runner=None, **kwargs):
        calls.append(("availability", provider, int(zoom), runner))
        return [{"capture_date": "2020-01-01"}]

    monkeypatch.setattr(gi, "fetch_vintages_for_anchor", fake_info)
    monkeypatch.setattr(ga, "fetch_availability_for_anchor", fake_availability)
    return calls


def test_merged_catalog_routes_runner_only_to_tm(captured_fetches):
    config = AdaptiveScanConfig(provider="Merged")
    ras._fetch_real_vintage_catalog(ANCHOR, config, tm_catalog_runner=SENTINEL)

    tm_runners = {r for (_, prov, _, r) in captured_fetches if prov == "TM"}
    wb_runners = {r for (_, prov, _, r) in captured_fetches if prov == "Wayback"}
    assert tm_runners == {SENTINEL}
    assert wb_runners == {None}
    # Both call kinds must exist on the TM side (info discovery + complete
    # availability), i.e. the runner reached every live TM surface.
    tm_kinds = {kind for (kind, prov, _, _) in captured_fetches if prov == "TM"}
    assert tm_kinds == {"info", "availability"}


def test_merged_catalog_without_runner_is_unchanged(captured_fetches):
    config = AdaptiveScanConfig(provider="Merged")
    ras._fetch_real_vintage_catalog(ANCHOR, config)
    assert {r for (_, _, _, r) in captured_fetches} == {None}


def test_vintage_check_lazy_tm_fetch_uses_runner(captured_fetches):
    config = AdaptiveScanConfig(provider="TM")
    check = ras.make_vintage_check(
        ANCHOR,
        available_dates_by_zoom={19: {"2020-01-01"}},
        config=config,
        tm_catalog_runner=SENTINEL,
    )
    assert check(19, "2020-01-01")  # discovery zoom: no fetch
    assert not captured_fetches
    check(20, "2020-01-01")  # lazy zoom: live TM fetch through the runner
    assert captured_fetches == [("availability", "TM", 20, SENTINEL)]


def test_vintage_check_lazy_wayback_fetch_bypasses_runner(captured_fetches):
    import dataclasses

    config = dataclasses.replace(
        AdaptiveScanConfig(provider="Wayback"),
        require_complete_coverage_for_download=False,
    )
    check = ras.make_vintage_check(
        ANCHOR,
        available_dates_by_zoom={19: {"2020-01-01"}},
        config=config,
        tm_catalog_runner=SENTINEL,
    )
    check(20, "2020-01-01")
    assert captured_fetches == [("info", "Wayback", 20, None)]
