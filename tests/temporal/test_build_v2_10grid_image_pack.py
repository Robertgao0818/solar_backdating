"""Tests for the ten-grid image pack target list and local chip choice."""

from __future__ import annotations

from pathlib import Path

from scripts.temporal.build_v2_10grid_image_pack import (
    capture_date_from_chip_name,
    choose_local_chip,
    index_local_chips,
    satellite_targets,
)


def test_satellite_targets_merge_roles_and_skip_non_callable() -> None:
    targets = satellite_targets(
        [
            {
                "anchor_id": "a",
                "capture_date": "2019-01-30",
                "provider": "TM",
                "requested_zoom": "19",
                "version": "",
                "callable": "1",
            },
            {
                "anchor_id": "b",
                "capture_date": "2019-01-30",
                "provider": "TM",
                "requested_zoom": "19",
                "version": "",
                "callable": "0",
            },
        ],
        [
            {
                "anchor_id": "a",
                "capture_date": "2019-01-30",
                "kind": "satellite",
                "provider": "TM",
                "requested_zoom": "19",
                "version": "20191212",
            },
            {
                "anchor_id": "a",
                "capture_date": "2025-03-30",
                "kind": "satellite",
                "provider": "TM",
                "requested_zoom": "19",
                "version": "",
            },
            {
                "anchor_id": "a",
                "capture_date": "2025-01-31",
                "kind": "aerial",
                "provider": "census_aerial",
                "requested_zoom": "",
                "version": "",
            },
        ],
    )
    assert [(row["anchor_id"], row["capture_date"], row["roles"]) for row in targets] == [
        ("a", "2019-01-30", "candidate|reference"),
        ("a", "2025-03-30", "reference"),
    ]
    assert targets[0]["version"] == "20191212"


def test_capture_date_ignores_anchor_id_digits() -> None:
    name = "ct_full_inventory_2026_06_21_merged_t00001264_20190130_vnoversion.tif"
    assert capture_date_from_chip_name(name) == "2019-01-30"
    assert capture_date_from_chip_name("not-a-chip.tif") is None


def test_choose_local_chip_prefers_higher_zoom(tmp_path: Path) -> None:
    low = tmp_path / "z18" / "a_20190130_vnoversion.tif"
    high = tmp_path / "z19" / "a_20190130_vnoversion.tif"
    empty = tmp_path / "z20" / "a_20190130_vnoversion.tif"
    for path, payload in ((low, b"low"), (high, b"high-zoom"), (empty, b"")):
        path.parent.mkdir(parents=True)
        path.write_bytes(payload)
    assert choose_local_chip([low, high, empty]) == high


def test_index_local_chips_matches_only_requested_dates(tmp_path: Path) -> None:
    chip = tmp_path / "chips" / "anchor-a" / "z19" / "anchor-a_20190130_vnoversion.tif"
    other = tmp_path / "chips" / "anchor-a" / "z19" / "anchor-a_20190228_vnoversion.tif"
    chip.parent.mkdir(parents=True)
    chip.write_bytes(b"chip")
    other.write_bytes(b"other")
    found = index_local_chips(
        [tmp_path / "chips"],
        [{"anchor_id": "anchor-a", "capture_date": "2019-01-30"}],
    )
    assert found == {("anchor-a", "2019-01-30"): chip}
