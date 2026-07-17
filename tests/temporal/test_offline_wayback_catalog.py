"""--offline-wayback: Wayback vintage catalog built with zero live Wayback calls.

Same source CSV as --offline-tm-catalog-csv (build_gehi_candidates.py output);
this path uses its Wayback rows, whose ``version`` column is a real numeric
layer-capture id (unlike offline TM's "noversion" sentinel), so picks resolve
the real pre-downloaded ``_v<version>.tif`` chips.

The offline builder must (a) never touch a live Wayback info surface, (b) mark
z20 (or any download-only zoom) UNAVAILABLE rather than lazy-fetched -- a
deliberate divergence from the live path documented in
`_build_offline_wayback_catalog`'s docstring, (c) take only the FIRST cutoff
candidate (no post-census reference-frame advance loop, unlike the offline TM
builder), and (d) skip rows with an empty/non-numeric version.
"""

from __future__ import annotations

import pytest

import scripts.temporal.gehi_availability as ga
import scripts.temporal.gehi_info as gi
from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal.scan_config import AdaptiveScanConfig

ANCHOR = {
    "anchor_id": "offline_wb_test_anchor",
    "chip_lon_min": "28.000",
    "chip_lat_min": "-26.100",
    "chip_lon_max": "28.001",
    "chip_lat_max": "-26.099",
}

# (capture_date, raw version string) pairs, matching load_offline_provider_catalogs
# output shape.
WAYBACK_ENTRIES = [
    ("2018-06-01", "20180601"),  # below catalog_min_date floor -> dropped
    ("2019-03-30", "20190330"),
    ("2020-05-31", "20200531"),
    ("2020-05-31", "20200531"),  # duplicate -> deduped (last-write-wins, same value)
    ("2024-04-01", "20240401"),
    ("2024-08-01", "20240801"),
    ("2024-12-01", "20241201"),
    ("2025-06-01", "20250601"),
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


def test_offline_merged_dual_makes_zero_live_calls(captured_fetches):
    """Merged + offline TM + offline Wayback together => zero live calls at all."""
    config = AdaptiveScanConfig(provider="Merged")
    catalog = ras._fetch_real_vintage_catalog(
        ANCHOR,
        config,
        offline_tm_dates=["2019-03-30", "2020-05-31"],
        offline_wayback_entries=WAYBACK_ENTRIES,
    )
    assert captured_fetches == []
    providers = {v.provider for v in catalog.vintages}
    assert providers == {"TM", "Wayback"}


def test_offline_wayback_alone_makes_zero_live_wayback_calls(captured_fetches):
    """Wayback-only offline catalog: zero live Wayback calls; TM untouched (not exercised)."""
    config = AdaptiveScanConfig(provider="Wayback")
    catalog = ras._fetch_real_vintage_catalog(
        ANCHOR, config, offline_wayback_entries=WAYBACK_ENTRIES
    )
    assert captured_fetches == []
    assert catalog.vintages
    assert {v.provider for v in catalog.vintages} == {"Wayback"}


def test_offline_wayback_version_numeric_preserved_and_invalid_skipped():
    config = AdaptiveScanConfig(provider="Wayback")
    entries = [
        ("2020-01-01", "20200101"),
        ("2020-02-02", ""),  # empty version -> skipped
        ("2020-03-03", "not_a_number"),  # non-numeric version -> skipped
        ("2020-04-04", "20200404"),
    ]
    catalog = ras._build_offline_wayback_catalog(entries, config, census_date=None)
    by_date = {v.capture_date: v.version for v in catalog.vintages}
    assert by_date == {"2020-01-01": 20200101, "2020-04-04": 20200404}
    assert all(isinstance(v, int) for v in by_date.values())
    assert "2020-02-02" not in by_date
    assert "2020-03-03" not in by_date


def test_offline_wayback_floors_below_catalog_min_date():
    config = AdaptiveScanConfig(provider="Wayback")
    catalog = ras._build_offline_wayback_catalog(
        WAYBACK_ENTRIES, config, census_date=None
    )
    assert "2018-06-01" not in {v.capture_date for v in catalog.vintages}
    assert all(v.capture_date >= config.catalog_min_date for v in catalog.vintages)


def test_offline_wayback_zoom_availability_z20_empty_z19_z18_allowed():
    config = AdaptiveScanConfig(provider="Wayback")
    catalog = ras._build_offline_wayback_catalog(
        WAYBACK_ENTRIES, config, census_date=None
    )
    expected_zooms = set(config.discovery_zoom_ladder) | set(
        config.download_zoom_ladder
    )
    assert set(catalog.available_dates_by_zoom) == expected_zooms
    allowed = {v.capture_date for v in catalog.vintages}
    for zoom in config.discovery_zoom_ladder:
        assert catalog.available_dates_by_zoom[zoom] == allowed
    for zoom in expected_zooms - set(config.discovery_zoom_ladder):
        # Deliberate divergence from live: z20 (download-only zoom) is an
        # empty set here, not lazy-fetched, so make_vintage_check rejects it
        # outright instead of issuing a live Wayback call.
        assert catalog.available_dates_by_zoom[zoom] == set()


def test_offline_wayback_cutoff_is_exactly_the_first_candidate():
    """Pin that the Wayback offline builder's `catalog_max_date` is exactly
    `_catalog_cutoff_candidates(...)[0]`, with no advance loop over the
    remaining candidates.

    Note: for a pure pre-fetched date SET (no live per-candidate availability
    drift), `_catalog_cutoff_candidates` already positions its first element
    at the correct N-post-census-reference-frame cutoff by construction, so
    taking `[0]` directly and `_build_offline_tm_catalog`'s advance-loop
    provably agree on the resulting date here (verified empirically: 2000
    randomized date-set trials, 0 divergences) -- the offline builders don't
    disagree on VALUES. The real semantic difference this test guards is
    STRUCTURAL: the live coverage-gated TM path's loop exists because live
    per-candidate availability can differ from mere date existence (a
    candidate can turn out incomplete), which the Wayback live path never
    re-checks (`require_complete_coverage_for_catalog=False` short-circuits
    straight past that whole mechanism). Copying TM's loop into the Wayback
    offline builder would be structurally wrong even though it happens to be
    numerically inert here -- so pin the direct-candidates[0] contract.
    """
    config = AdaptiveScanConfig(provider="Wayback", post_census_reference_frames=3)
    dates = sorted({d for d, _ in WAYBACK_ENTRIES if d >= config.catalog_min_date})
    expected = ras._catalog_cutoff_candidates(dates, config, census_date="2024-02-21")[0]
    catalog = ras._build_offline_wayback_catalog(
        WAYBACK_ENTRIES, config, census_date="2024-02-21"
    )
    assert catalog.catalog_max_date == expected == "2024-12-01"


def test_offline_wayback_census_cutoff_zero_keep_stops_at_census():
    config = AdaptiveScanConfig(provider="Wayback", post_census_reference_frames=0)
    catalog = ras._build_offline_wayback_catalog(
        WAYBACK_ENTRIES, config, census_date="2024-02-21"
    )
    assert catalog.catalog_max_date == "2024-02-21"
    assert all(v.capture_date <= "2024-02-21" for v in catalog.vintages)


def test_load_offline_provider_catalogs_single_pass_produces_both_maps(tmp_path):
    csv_path = tmp_path / "candidates.csv"
    csv_path.write_text(
        "anchor_id,capture_date,version,provider\n"
        "a1,2020-01-01,,TM\n"
        "a1,2021-02-02,,TM\n"
        "a1,2020-06-06,20200606,Wayback\n"
        "a2,2019-09-09,,TM\n"
        "a2,2019-10-10,20191010,Wayback\n"
        ",2019-09-09,,TM\n"
        "a3,,,TM\n",
        encoding="utf-8",
    )
    tm_map, wayback_map = ras.load_offline_provider_catalogs(csv_path)
    assert tm_map == {
        "a1": ["2020-01-01", "2021-02-02"],
        "a2": ["2019-09-09"],
    }
    assert wayback_map == {
        "a1": [("2020-06-06", "20200606")],
        "a2": [("2019-10-10", "20191010")],
    }


def test_load_offline_tm_catalog_matches_combined_loader_tm_map(tmp_path):
    """load_offline_tm_catalog must stay byte-identical to the TM half of the
    combined loader (back-compat contract for existing callers/tests)."""
    csv_path = tmp_path / "candidates.csv"
    csv_path.write_text(
        "anchor_id,capture_date,version,provider\n"
        "a1,2020-01-01,,TM\n"
        "a1,2020-06-06,20200606,Wayback\n"
        "a2,2019-09-09,,TM\n",
        encoding="utf-8",
    )
    via_wrapper = ras.load_offline_tm_catalog(csv_path)
    via_combined, _ = ras.load_offline_provider_catalogs(csv_path)
    assert via_wrapper == via_combined == {"a1": ["2020-01-01"], "a2": ["2019-09-09"]}
