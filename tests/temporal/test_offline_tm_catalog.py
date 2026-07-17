"""--offline-tm-catalog-csv: TM vintage catalog built with zero live TM calls.

The download-phase candidates CSV already lists every anchor's TM capture
dates, and bbox-completeness is delegated to the downloaded basemap (the 2026-
07-16 mid-run amendment). The offline path must (a) never touch a live TM
metadata surface, (b) mark every discovery+download ladder zoom available so
`make_vintage_check` never lazy-fetches either, (c) stamp version="noversion"
so picks resolve the pre-downloaded `_vnoversion` chips, and (d) preserve the
ISSUE-26 coverage-gated census cutoff semantics.
"""

from __future__ import annotations

import pytest

import scripts.temporal.gehi_availability as ga
import scripts.temporal.gehi_info as gi
from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal.scan_config import AdaptiveScanConfig

ANCHOR = {
    "anchor_id": "offline_test_anchor",
    "chip_lon_min": "28.000",
    "chip_lat_min": "-26.100",
    "chip_lon_max": "28.001",
    "chip_lat_max": "-26.099",
}

OFFLINE_DATES = [
    "2018-06-01",  # below catalog_min_date floor -> dropped
    "2019-03-30",
    "2020-05-31",
    "2020-05-31",  # duplicate -> deduped
    "2024-04-01",
    "2024-08-01",
    "2024-12-01",
    "2025-06-01",
]


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


def test_offline_tm_makes_zero_live_tm_calls(captured_fetches):
    config = AdaptiveScanConfig(provider="Merged")
    catalog = ras._fetch_real_vintage_catalog(
        ANCHOR, config, offline_tm_dates=OFFLINE_DATES
    )
    tm_calls = [c for c in captured_fetches if c[1] == "TM"]
    wayback_calls = [c for c in captured_fetches if c[1] == "Wayback"]
    assert tm_calls == []
    # Wayback discovery is untouched (unthrottled surface, stays live).
    assert wayback_calls
    tm_vintages = [v for v in catalog.vintages if v.provider == "TM"]
    assert tm_vintages


def test_offline_entries_carry_noversion_and_floor():
    config = AdaptiveScanConfig(provider="TM")
    catalog = ras._build_offline_tm_catalog(
        OFFLINE_DATES, config, census_date=None
    )
    assert [v.capture_date for v in catalog.vintages] == sorted(
        {d for d in OFFLINE_DATES if d >= config.catalog_min_date}
    )
    assert {v.version for v in catalog.vintages} == {ras.OFFLINE_TM_VERSION}
    assert {v.provider for v in catalog.vintages} == {"TM"}


def test_offline_availability_covers_both_ladders():
    config = AdaptiveScanConfig(provider="TM")
    catalog = ras._build_offline_tm_catalog(
        OFFLINE_DATES, config, census_date=None
    )
    expected_zooms = set(config.discovery_zoom_ladder) | set(
        config.download_zoom_ladder
    )
    assert set(catalog.available_dates_by_zoom) == expected_zooms
    for dates in catalog.available_dates_by_zoom.values():
        assert dates == {v.capture_date for v in catalog.vintages}


def test_offline_census_cutoff_keeps_reference_frames():
    config = AdaptiveScanConfig(provider="TM", post_census_reference_frames=3)
    catalog = ras._build_offline_tm_catalog(
        OFFLINE_DATES, config, census_date="2024-02-21"
    )
    # Exactly 3 post-census reference dates retained; cutoff = 3rd newer date.
    assert catalog.catalog_max_date == "2024-12-01"
    post = [
        v.capture_date for v in catalog.vintages if v.capture_date > "2024-02-21"
    ]
    assert post == ["2024-04-01", "2024-08-01", "2024-12-01"]
    assert "2025-06-01" not in {v.capture_date for v in catalog.vintages}


def test_offline_census_cutoff_short_tail_uses_last_available():
    config = AdaptiveScanConfig(provider="TM", post_census_reference_frames=3)
    catalog = ras._build_offline_tm_catalog(
        ["2019-03-30", "2024-04-01"], config, census_date="2024-02-21"
    )
    # Only one post-census date exists -> cutoff degrades to it, like the live
    # coverage-gated walk exhausting its candidates.
    assert catalog.catalog_max_date == "2024-04-01"


def test_offline_census_cutoff_zero_keep_stops_at_census():
    config = AdaptiveScanConfig(provider="TM", post_census_reference_frames=0)
    catalog = ras._build_offline_tm_catalog(
        OFFLINE_DATES, config, census_date="2024-02-21"
    )
    assert catalog.catalog_max_date == "2024-02-21"
    assert all(v.capture_date <= "2024-02-21" for v in catalog.vintages)


def test_load_offline_tm_catalog_filters_tm_rows(tmp_path):
    csv_path = tmp_path / "candidates.csv"
    csv_path.write_text(
        "anchor_id,capture_date,version,provider\n"
        "a1,2020-01-01,,TM\n"
        "a1,2021-02-02,,TM\n"
        "a1,2020-06-06,20200606,Wayback\n"
        "a2,2019-09-09,,TM\n"
        ",2019-09-09,,TM\n"
        "a3,,,TM\n",
        encoding="utf-8",
    )
    catalog = ras.load_offline_tm_catalog(csv_path)
    assert catalog == {
        "a1": ["2020-01-01", "2021-02-02"],
        "a2": ["2019-09-09"],
    }
