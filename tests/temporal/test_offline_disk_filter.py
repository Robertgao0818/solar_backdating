"""--offline-require-chip-on-disk: filter the offline TM/Wayback catalogs down
to entries with a real chip file on disk, before the adaptive picker or the
ISSUE-26 cutoff walk ever see them.

Root cause (2026-07-17 forensics addendum): a small set of "phantom" dates are
listed in the offline candidates CSV (the original live catalog call once
reported them) but were never actually captured to disk during the basemap
download. Even --no-live-gehi alone still lets the adaptive scan pick one of
these and burn a `download_failed` row every time. This filter removes them
before the picker ever sees them.
"""

from __future__ import annotations

from pathlib import Path

from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal.gehi_download import _chip_path_for
from scripts.temporal.scan_config import AdaptiveScanConfig

ZOOM_LADDER = (20, 19, 18)


def _touch_chip(chips_dir: Path, anchor_id: str, date: str, version: str, zoom: int) -> Path:
    p = _chip_path_for(chips_dir, anchor_id, date, version, zoom)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FAKE_TIFF_DATA_FOR_TEST")
    return p


# ---------------------------------------------------------------------------
# filter_offline_catalogs_to_disk
# ---------------------------------------------------------------------------


def test_tm_entry_missing_on_disk_is_dropped(tmp_path: Path) -> None:
    tm_dir = tmp_path / "tm"
    wb_dir = tmp_path / "wb"
    _touch_chip(tm_dir, "a1", "2020-01-01", ras.OFFLINE_TM_VERSION, 19)
    # "2020-06-06" has no chip file at any zoom -> must be dropped.
    tm_dates = {"a1": ["2020-01-01", "2020-06-06"]}
    filtered_tm, filtered_wb, stats = ras.filter_offline_catalogs_to_disk(
        tm_dates, {}, tm_chips_dir=tm_dir, wayback_chips_dir=wb_dir, zoom_ladder=ZOOM_LADDER
    )
    assert filtered_tm == {"a1": ["2020-01-01"]}
    assert filtered_wb == {}
    assert stats.tm_before == 2
    assert stats.tm_after == 1
    assert stats.tm_dropped_dates == {"2020-06-06": 1}


def test_wayback_entry_missing_on_disk_is_dropped(tmp_path: Path) -> None:
    tm_dir = tmp_path / "tm"
    wb_dir = tmp_path / "wb"
    _touch_chip(wb_dir, "a1", "2020-06-06", "20200606", 19)
    wayback_entries = {"a1": [("2020-06-06", "20200606"), ("2021-07-23", "20210801")]}
    filtered_tm, filtered_wb, stats = ras.filter_offline_catalogs_to_disk(
        {}, wayback_entries, tm_chips_dir=tm_dir, wayback_chips_dir=wb_dir, zoom_ladder=ZOOM_LADDER
    )
    assert filtered_wb == {"a1": [("2020-06-06", "20200606")]}
    assert filtered_tm == {}
    assert stats.wayback_before == 2
    assert stats.wayback_after == 1
    assert stats.wayback_dropped_dates == {"2021-07-23": 1}


def test_tm_resolves_via_noversion_sentinel(tmp_path: Path) -> None:
    """TM entries must resolve via the OFFLINE_TM_VERSION ("noversion") filename,
    not a real numeric version -- a chip filed under a real version must NOT
    count as present for an offline-TM pick."""
    tm_dir = tmp_path / "tm"
    _touch_chip(tm_dir, "a1", "2020-01-01", "12345", 19)  # wrong (real) version
    filtered_tm, _, stats = ras.filter_offline_catalogs_to_disk(
        {"a1": ["2020-01-01"]}, {}, tm_chips_dir=tm_dir, wayback_chips_dir=tmp_path / "wb", zoom_ladder=ZOOM_LADDER
    )
    assert filtered_tm == {"a1": []}
    assert stats.tm_dropped_dates == {"2020-01-01": 1}


def test_wayback_resolves_via_real_numeric_version(tmp_path: Path) -> None:
    wb_dir = tmp_path / "wb"
    _touch_chip(wb_dir, "a1", "2020-01-01", "20200101", 19)
    filtered_tm, filtered_wb, stats = ras.filter_offline_catalogs_to_disk(
        {}, {"a1": [("2020-01-01", "20200101")]},
        tm_chips_dir=tmp_path / "tm", wayback_chips_dir=wb_dir, zoom_ladder=ZOOM_LADDER,
    )
    assert filtered_wb == {"a1": [("2020-01-01", "20200101")]}
    assert stats.wayback_dropped_dates == {}


def test_any_ladder_zoom_counts_as_present(tmp_path: Path) -> None:
    """A chip cached at z18 only (not z20/z19) still counts -- same rule the
    download cache-scan in download_chip_with_zoom_ladder uses."""
    tm_dir = tmp_path / "tm"
    _touch_chip(tm_dir, "a1", "2020-01-01", ras.OFFLINE_TM_VERSION, 18)
    filtered_tm, _, stats = ras.filter_offline_catalogs_to_disk(
        {"a1": ["2020-01-01"]}, {}, tm_chips_dir=tm_dir, wayback_chips_dir=tmp_path / "wb", zoom_ladder=ZOOM_LADDER
    )
    assert filtered_tm == {"a1": ["2020-01-01"]}
    assert stats.tm_dropped_dates == {}


def test_empty_chip_file_does_not_count_as_present(tmp_path: Path) -> None:
    tm_dir = tmp_path / "tm"
    p = _chip_path_for(tm_dir, "a1", "2020-01-01", ras.OFFLINE_TM_VERSION, 19)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")  # zero-byte quarantined leftover
    filtered_tm, _, stats = ras.filter_offline_catalogs_to_disk(
        {"a1": ["2020-01-01"]}, {}, tm_chips_dir=tm_dir, wayback_chips_dir=tmp_path / "wb", zoom_ladder=ZOOM_LADDER
    )
    assert filtered_tm == {"a1": []}
    assert stats.tm_dropped_dates == {"2020-01-01": 1}


def test_anchor_key_preserved_when_every_entry_filtered_out(tmp_path: Path) -> None:
    """An anchor present in the CSV but with zero obtainable chips must map to
    an EMPTY list, not a missing key -- `_fetch_real_vintage_catalog` treats a
    missing key (`.get() is None`) as "anchor absent from the CSV entirely",
    a distinct --no-live-gehi hard-error case that must not be conflated with
    "was in the CSV, everything turned out undownloadable"."""
    filtered_tm, filtered_wb, _ = ras.filter_offline_catalogs_to_disk(
        {"a1": ["2020-01-01"]}, {"a1": [("2020-06-06", "20200606")]},
        tm_chips_dir=tmp_path / "tm", wayback_chips_dir=tmp_path / "wb", zoom_ladder=ZOOM_LADDER,
    )
    assert "a1" in filtered_tm and filtered_tm["a1"] == []
    assert "a1" in filtered_wb and filtered_wb["a1"] == []
    # Contrast: an anchor never present in either input dict stays absent.
    assert "a2" not in filtered_tm
    assert "a2" not in filtered_wb


def test_over_50pct_loss_warning_fires(tmp_path: Path) -> None:
    tm_dir = tmp_path / "tm"
    wb_dir = tmp_path / "wb"
    _touch_chip(tm_dir, "a1", "2020-01-01", ras.OFFLINE_TM_VERSION, 19)
    # a1: 1 of 4 total candidates survives -> 75% dropped.
    _, _, stats = ras.filter_offline_catalogs_to_disk(
        {"a1": ["2020-01-01", "2020-02-02"]},
        {"a1": [("2020-06-06", "20200606"), ("2020-07-07", "20200707")]},
        tm_chips_dir=tm_dir, wayback_chips_dir=wb_dir, zoom_ladder=ZOOM_LADDER,
    )
    assert len(stats.anchors_over_50pct_loss) == 1
    anchor_id, before_n, after_n, frac = stats.anchors_over_50pct_loss[0]
    assert anchor_id == "a1"
    assert before_n == 4
    assert after_n == 1
    assert frac == 0.75


def test_under_50pct_loss_does_not_warn(tmp_path: Path) -> None:
    tm_dir = tmp_path / "tm"
    _touch_chip(tm_dir, "a1", "2020-01-01", ras.OFFLINE_TM_VERSION, 19)
    _touch_chip(tm_dir, "a1", "2020-02-02", ras.OFFLINE_TM_VERSION, 19)
    _touch_chip(tm_dir, "a1", "2020-03-03", ras.OFFLINE_TM_VERSION, 19)
    _, _, stats = ras.filter_offline_catalogs_to_disk(
        {"a1": ["2020-01-01", "2020-02-02", "2020-03-03", "2020-04-04"]},
        {}, tm_chips_dir=tm_dir, wayback_chips_dir=tmp_path / "wb", zoom_ladder=ZOOM_LADDER,
    )
    assert stats.anchors_over_50pct_loss == []


def test_format_summary_reports_drops_and_warnings(tmp_path: Path) -> None:
    tm_dir = tmp_path / "tm"
    _touch_chip(tm_dir, "a1", "2020-01-01", ras.OFFLINE_TM_VERSION, 19)
    _, _, stats = ras.filter_offline_catalogs_to_disk(
        {"a1": ["2020-01-01", "2020-06-06", "2020-07-07"]},
        {}, tm_chips_dir=tm_dir, wayback_chips_dir=tmp_path / "wb", zoom_ladder=ZOOM_LADDER,
    )
    lines = ras.format_offline_disk_filter_summary(stats)
    joined = "\n".join(lines)
    assert "TM 2/3 entries dropped" in joined
    assert "2020-06-06" in joined
    assert "anchors losing >50%" in joined
    assert "a1: 3 -> 1 candidates" in joined


def test_format_summary_no_drops_is_quiet(tmp_path: Path) -> None:
    tm_dir = tmp_path / "tm"
    _touch_chip(tm_dir, "a1", "2020-01-01", ras.OFFLINE_TM_VERSION, 19)
    _, _, stats = ras.filter_offline_catalogs_to_disk(
        {"a1": ["2020-01-01"]}, {}, tm_chips_dir=tm_dir, wayback_chips_dir=tmp_path / "wb", zoom_ladder=ZOOM_LADDER
    )
    lines = ras.format_offline_disk_filter_summary(stats)
    joined = "\n".join(lines)
    assert "TM 0/1 entries dropped" in joined
    assert "top dropped TM dates" not in joined
    assert "anchors losing >50%: 0" in joined or "anchors losing >50% of their offline candidates to this filter: 0" in joined


# ---------------------------------------------------------------------------
# Composition with the ISSUE-26 cutoff walk: filtering BEFORE the walk changes
# which cutoff candidate is chosen, on purpose (see the docstring in
# filter_offline_catalogs_to_disk for the full reasoning).
# ---------------------------------------------------------------------------


def test_filter_before_cutoff_walk_advances_past_a_filtered_candidate(tmp_path: Path) -> None:
    """Unfiltered: the 3rd post-census date (2024-08-01) is the cutoff (matches
    test_offline_census_cutoff_keeps_reference_frames). With the disk filter
    removing 2024-08-01 (no chip on disk), the walk must advance to the NEXT
    post-census date instead of silently accepting only 2 reference frames."""
    raw_dates = ["2019-03-30", "2024-04-01", "2024-08-01", "2024-12-01", "2025-06-01"]
    config = AdaptiveScanConfig(provider="TM", post_census_reference_frames=3)

    # Unfiltered control: reproduces the existing documented behavior.
    unfiltered_catalog = ras._build_offline_tm_catalog(raw_dates, config, census_date="2024-02-21")
    assert unfiltered_catalog.catalog_max_date == "2024-12-01"

    # Only 2024-08-01 has no chip on disk.
    tm_dir = tmp_path / "tm"
    for d in raw_dates:
        if d != "2024-08-01":
            _touch_chip(tm_dir, "a1", d, ras.OFFLINE_TM_VERSION, 19)

    filtered_tm, _, stats = ras.filter_offline_catalogs_to_disk(
        {"a1": raw_dates}, {}, tm_chips_dir=tm_dir, wayback_chips_dir=tmp_path / "wb", zoom_ladder=ZOOM_LADDER
    )
    assert stats.tm_dropped_dates == {"2024-08-01": 1}
    assert filtered_tm["a1"] == ["2019-03-30", "2024-04-01", "2024-12-01", "2025-06-01"]

    filtered_catalog = ras._build_offline_tm_catalog(filtered_tm["a1"], config, census_date="2024-02-21")
    # The walk now needs 2025-06-01 to reach 3 post-census reference dates,
    # since 2024-08-01 (the unfiltered 2nd one) is gone.
    assert filtered_catalog.catalog_max_date == "2025-06-01"
    post = [v.capture_date for v in filtered_catalog.vintages if v.capture_date > "2024-02-21"]
    assert post == ["2024-04-01", "2024-12-01", "2025-06-01"]
