"""Stratified delivery-review package: sample, overlay, fetch, and static page."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.temporal.build_delivery_review_package import (
    CENTROID_RGB,
    CT_CITY,
    DEFAULT_CT_CSV,
    DEFAULT_JHB_CSV,
    DEFAULT_SEED,
    JHB_CITY,
    NO_GEOREF_NOTE,
    OUTLINE_RGB,
    TOP52_NOTE,
    CityBundle,
    FetchResult,
    assert_sample_contract,
    build_package,
    burn_review_marks,
    load_csv,
    main,
    observation_slots_for_roof,
    place_package_image,
    render_review_html,
    resolve_local_or_fetch,
    sample_roofs_by_status,
    status_counts,
)

CT_STATUSES = (
    "done_installed_during_census",
    "done_already_present_before_geid_history",
    "done_ambiguous_nonmonotonic",
    "done_ambiguous_no_recent_anchor",
    "done_appears",
)
JHB_STATUSES = CT_STATUSES + (
    "done_ambiguous_marker_missed_pv",
    "done_ambiguous_clamp_inverted",
)


def _rows(city: str, counts: dict[str, int]) -> list[dict[str, str]]:
    rows = []
    serial = 0
    for status, count in counts.items():
        for _ in range(count):
            rows.append(
                {
                    "city": city,
                    "source_feature_id": str(serial),
                    "source_anchor_id": f"{city}_{serial}",
                    "scan_status": status,
                    "centroid_lon": "18.45",
                    "centroid_lat": "-33.95",
                    "install_interval_start": "2020-01-01",
                    "install_interval_end": "2020-06-01",
                    "latest_absent_date": "2020-01-01",
                    "earliest_present_date": "2020-06-01",
                    "cohort": "citywide_27wave",
                    "lane": "a24",
                    "left_censored": "0",
                    "census_bound": "0",
                }
            )
            serial += 1
    return rows


def _ids(rows):
    return [row["source_feature_id"] for row in rows]


def test_sample_is_seed_stable_stratified_and_city_independent():
    ct_counts = {
        "done_installed_during_census": 20,
        "done_already_present_before_geid_history": 3,
        "done_ambiguous_nonmonotonic": 8,
        "done_ambiguous_no_recent_anchor": 2,
        "done_appears": 15,
    }
    jhb_counts = {
        "done_installed_during_census": 12,
        "done_already_present_before_geid_history": 4,
        "done_ambiguous_nonmonotonic": 7,
        "done_ambiguous_no_recent_anchor": 2,
        "done_appears": 18,
        "done_ambiguous_marker_missed_pv": 9,
        "done_ambiguous_clamp_inverted": 1,
    }
    ct_rows = _rows(CT_CITY, ct_counts)
    jhb_rows = _rows(JHB_CITY, jhb_counts)
    ct_pick = sample_roofs_by_status(ct_rows, city=CT_CITY, seed=DEFAULT_SEED)
    jhb_pick = sample_roofs_by_status(jhb_rows, city=JHB_CITY, seed=DEFAULT_SEED)
    assert_sample_contract(ct_rows, ct_pick, city=CT_CITY)
    assert_sample_contract(jhb_rows, jhb_pick, city=JHB_CITY)
    assert set(status_counts(ct_pick)) == set(CT_STATUSES)
    assert set(status_counts(jhb_pick)) == set(JHB_STATUSES)
    assert status_counts(jhb_pick)["done_ambiguous_clamp_inverted"] == 1
    assert _ids(sample_roofs_by_status(ct_rows, city=CT_CITY, seed=DEFAULT_SEED)) == _ids(ct_pick)
    assert _ids(sample_roofs_by_status(list(reversed(ct_rows)), city=CT_CITY, seed=DEFAULT_SEED)) == _ids(ct_pick)
    mixed = ct_rows + jhb_rows
    assert _ids(sample_roofs_by_status(mixed, city=CT_CITY, seed=DEFAULT_SEED)) == _ids(ct_pick)
    assert _ids(sample_roofs_by_status(mixed, city=JHB_CITY, seed=DEFAULT_SEED)) == _ids(jhb_pick)
    assert len(ct_pick) < len(ct_rows)
    assert len(jhb_pick) < len(jhb_rows)
    assert _ids(ct_pick) != _ids(ct_rows)[: len(ct_pick)]
    appears = [row["source_feature_id"] for row in ct_rows if row["scan_status"] == "done_appears"]
    picked_appears = [row["source_feature_id"] for row in ct_pick if row["scan_status"] == "done_appears"]
    assert picked_appears != appears[: len(picked_appears)]
    assert all(row["area_band"] == "面积缺失" for row in ct_pick)


def test_real_delivery_tables_keep_every_status_including_clamp_inverted():
    if not DEFAULT_CT_CSV.is_file() or not DEFAULT_JHB_CSV.is_file():
        pytest.skip("frozen delivery tables are not on this machine")
    ct_rows = load_csv(DEFAULT_CT_CSV)
    jhb_rows = load_csv(DEFAULT_JHB_CSV)
    assert len(ct_rows) == 111801
    assert len(jhb_rows) == 41393
    ct_pick = sample_roofs_by_status(ct_rows, city=CT_CITY, seed=DEFAULT_SEED)
    jhb_pick = sample_roofs_by_status(jhb_rows, city=JHB_CITY, seed=DEFAULT_SEED)
    again = sample_roofs_by_status(ct_rows, city=CT_CITY, seed=DEFAULT_SEED)
    assert_sample_contract(ct_rows, ct_pick, city=CT_CITY)
    assert_sample_contract(jhb_rows, jhb_pick, city=JHB_CITY)
    assert _ids(again) == _ids(ct_pick)
    assert "done_ambiguous_clamp_inverted" in status_counts(jhb_rows)
    assert status_counts(jhb_pick)["done_ambiguous_clamp_inverted"] == 1
    assert len(ct_pick) < len(ct_rows)
    assert len(jhb_pick) < len(jhb_rows)
    assert _ids(ct_pick) != [row["source_feature_id"] for row in ct_rows[: len(ct_pick)]]
    assert _ids(jhb_pick) != [row["source_feature_id"] for row in jhb_rows[: len(jhb_pick)]]
    mixed = [dict(row, city=CT_CITY) for row in ct_rows] + [dict(row, city=JHB_CITY) for row in jhb_rows]
    assert _ids(sample_roofs_by_status(mixed, city=JHB_CITY, seed=DEFAULT_SEED)) == _ids(jhb_pick)
    assert _ids(sample_roofs_by_status(mixed, city=CT_CITY, seed=DEFAULT_SEED)) == _ids(ct_pick)
    assert all(row.get("cohort") != "top52" for row in ct_pick)
    assert len(ct_pick) == 30
    assert len(jhb_pick) == 37
    expected_bands = {"小于20平方米": 2, "20到100平方米": 2, "100平方米及以上": 2}
    for status in CT_STATUSES:
        bands = {}
        for row in ct_pick:
            if row["scan_status"] == status:
                bands[row["area_band"]] = bands.get(row["area_band"], 0) + 1
        assert bands == expected_bands
    for status in JHB_STATUSES:
        bands = {}
        for row in jhb_pick:
            if row["scan_status"] == status:
                bands[row["area_band"]] = bands.get(row["area_band"], 0) + 1
        if status == "done_ambiguous_clamp_inverted":
            assert bands == {"小于20平方米": 1}
        else:
            assert bands == expected_bands


def test_sample_drops_top52_and_draws_each_area_band():
    rows = []
    serial = 0
    for area in ("8", "12", "18", "30", "40", "55", "120", "250", "800"):
        for cohort in ("citywide_27wave", "top52"):
            rows.append(
                {
                    "city": CT_CITY,
                    "source_feature_id": str(serial),
                    "source_anchor_id": f"ct_{serial}",
                    "scan_status": "done_appears",
                    "cohort": cohort,
                    "area_m2": area,
                    "centroid_lon": "18.45",
                    "centroid_lat": "-33.95",
                }
            )
            serial += 1
    # A second status whose small roofs are only Top-52. Those roofs leave
    # the frame, and the status stays because the large roofs are eligible.
    for area, cohort in (("9", "top52"), ("11", "top52"), ("140", "citywide_27wave"), ("220", "citywide_27wave"), ("260", "citywide_27wave")):
        rows.append(
            {
                "city": CT_CITY,
                "source_feature_id": str(serial),
                "scan_status": "done_ambiguous_nonmonotonic",
                "cohort": cohort,
                "area_m2": area,
            }
        )
        serial += 1
    picked = sample_roofs_by_status(rows, city=CT_CITY, seed=DEFAULT_SEED)
    assert_sample_contract(rows, picked, city=CT_CITY)
    assert all(row["cohort"] == "citywide_27wave" for row in picked)
    appears = [row for row in picked if row["scan_status"] == "done_appears"]
    assert sorted(row["area_band"] for row in appears) == sorted(
        ["小于20平方米", "小于20平方米", "20到100平方米", "20到100平方米", "100平方米及以上", "100平方米及以上"]
    )
    other = [row for row in picked if row["scan_status"] == "done_ambiguous_nonmonotonic"]
    assert [row["area_band"] for row in other] == ["100平方米及以上", "100平方米及以上"]
    assert _ids(sample_roofs_by_status(list(reversed(rows)), city=CT_CITY, seed=DEFAULT_SEED)) == _ids(picked)


def test_scan_state_provider_fills_only_blank_observation_providers(tmp_path: Path):
    from scripts.temporal.build_delivery_review_package import enrich_providers_from_scan_states

    anchor = "anchor-a"
    state = tmp_path / f"{anchor}.json"
    state.write_text(
        json.dumps(
            {
                "rounds": [
                    {
                        "results": [
                            {"capture_date": "2019-01-15", "provider": "Wayback"},
                            {"capture_date": "2020-06-01", "provider": "TM"},
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rows = {
        anchor: [
            {"capture_date": "2019-01-15", "provider": ""},
            {"capture_date": "2020-06-01", "provider": "Wayback"},
        ]
    }
    enrich_providers_from_scan_states(rows, tmp_path)
    assert rows[anchor][0]["provider"] == "Wayback"
    assert rows[anchor][1]["provider"] == "Wayback"


def test_top52_keeps_only_frozen_endpoint_dates():
    row = {
        "cohort": "top52",
        "latest_absent_date": "2019-05-01",
        "earliest_present_date": "2021-02-01",
    }
    invented = [{"capture_date": "2020-01-01", "frame_role": "evidence", "code_repaired": "1"}]
    slots, note = observation_slots_for_roof(row, invented, city=CT_CITY)
    assert note == TOP52_NOTE
    assert [slot["capture_date"] for slot in slots] == ["2019-05-01", "2021-02-01"]
    assert all(slot["code"] == "not_in_delivery" for slot in slots)
    assert "2020-01-01" not in {slot["capture_date"] for slot in slots}


def test_observation_codes_follow_the_delivery_and_sort_by_date():
    ct_row = {"cohort": "citywide_27wave"}
    ct_obs = [
        {"capture_date": "2021-01-01", "frame_role": "evidence", "code_repaired": "u", "obs_index": "1"},
        {"capture_date": "2019-01-15", "frame_role": "evidence", "code_repaired": "0", "obs_index": "0"},
        {"capture_date": "2020-06-01", "frame_role": "reference", "code_repaired": "1", "obs_index": "2"},
    ]
    slots, note = observation_slots_for_roof(ct_row, ct_obs, city=CT_CITY)
    assert note == ""
    assert [(slot["capture_date"], slot["code"]) for slot in slots] == [
        ("2019-01-15", "0"),
        ("2021-01-01", "unusable"),
    ]
    jhb_row = {"lane": "a24"}
    jhb_obs = [
        {"capture_date": "2022-03-30", "pv_present": "1", "obs_order": "1"},
        {"capture_date": "2019-11-30", "pv_present": "", "obs_order": "0"},
        {"capture_date": "2021-03-31", "pv_present": "0", "obs_order": "2"},
    ]
    jhb_slots, _ = observation_slots_for_roof(jhb_row, jhb_obs, city=JHB_CITY)
    assert [(slot["capture_date"], slot["code"]) for slot in jhb_slots] == [
        ("2019-11-30", "unusable"),
        ("2021-03-31", "0"),
        ("2022-03-30", "1"),
    ]


def _pixel_center(transform, col: int, row: int) -> tuple[float, float]:
    x, y = transform * (col + 0.5, row + 0.5)
    return float(x), float(y)


def _write_geotiff(path: Path, *, crs: str | None) -> object:
    import rasterio
    from rasterio.transform import from_origin

    transform = from_origin(18.0, -34.0, 0.01, 0.01)
    array = np.zeros((3, 21, 21), dtype=np.uint8)
    array[0] = 10
    array[1] = 20
    array[2] = 30
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": 21,
        "width": 21,
        "count": 3,
        "dtype": "uint8",
        "transform": transform,
    }
    if crs:
        profile["crs"] = crs
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array)
    return transform


def _square(transform):
    from shapely.geometry import Polygon

    ring = [
        _pixel_center(transform, 2, 2),
        _pixel_center(transform, 8, 2),
        _pixel_center(transform, 8, 8),
        _pixel_center(transform, 2, 8),
    ]
    return Polygon(ring)


def test_local_aerial_crop_uses_the_tile_that_covers_the_roof(tmp_path: Path):
    from scripts.temporal.build_delivery_review_package import crop_aerial_from_tiles

    tile = tmp_path / "G1" / "G1_0_0_geo.tif"
    transform = _write_geotiff(tile, crs="EPSG:4326")
    west, south = _pixel_center(transform, 1, 18)
    east, north = _pixel_center(transform, 6, 3)
    # centers are inside the tile; swap so west < east and south < north
    bbox = (min(west, east), min(south, north), max(west, east), max(south, north))
    out = tmp_path / "crop.tif"
    tiles = [
        {
            "path": tile,
            "crs": "EPSG:4326",
            "left": 18.0,
            "bottom": -34.0 - 21 * 0.01,
            "right": 18.0 + 21 * 0.01,
            "top": -34.0,
        }
    ]
    cropped = crop_aerial_from_tiles(tiles, bbox, out)
    assert cropped == out
    assert out.stat().st_size > 0
    import rasterio

    with rasterio.open(out) as src:
        assert src.crs is not None
        assert src.width >= 2 and src.height >= 2
    missed = crop_aerial_from_tiles(tiles, (0.0, 0.0, 0.01, 0.01), tmp_path / "miss.tif")
    assert missed is None


def test_overlay_paints_polygon_and_centroid_and_skips_ungeoreferenced_images(tmp_path: Path):
    geotiff = tmp_path / "chip.tif"
    transform = _write_geotiff(geotiff, crs="EPSG:4326")
    polygon = _square(transform)
    centroid = _pixel_center(transform, 4, 15)
    marked = burn_review_marks(
        geotiff,
        polygon=polygon,
        polygon_crs="EPSG:4326",
        centroid_xy=centroid,
        centroid_crs="EPSG:4326",
        draw_outline=True,
        draw_centroid=True,
    )
    image = Image.open(__import__("io").BytesIO(marked.png_bytes))
    assert marked.outline_placed is True
    assert marked.centroid_placed is True
    assert marked.centroid_pixel == (4, 15)
    for col, row in ((2, 2), (8, 2), (8, 8), (2, 8)):
        assert image.getpixel((col, row)) == OUTLINE_RGB
    assert image.getpixel((4, 15)) == CENTROID_RGB
    assert image.getpixel((10, 10)) == (10, 20, 30)

    plain = tmp_path / "plain.png"
    Image.new("RGB", (21, 21), (10, 20, 30)).save(plain)
    before = list(Image.open(plain).getdata())
    unplaced = burn_review_marks(
        plain,
        polygon=polygon,
        polygon_crs="EPSG:4326",
        centroid_xy=centroid,
        centroid_crs="EPSG:4326",
    )
    after = Image.open(__import__("io").BytesIO(unplaced.png_bytes))
    assert unplaced.outline_placed is False
    assert unplaced.centroid_placed is False
    assert unplaced.centroid_pixel is None
    assert NO_GEOREF_NOTE in unplaced.note
    assert list(after.getdata()) == before
    assert after.getpixel((10, 10)) == (10, 20, 30)


def _png_bytes(color=(1, 2, 3)) -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_fetch_reuses_local_bytes_and_keeps_a_gap(tmp_path: Path):
    local = tmp_path / "reuse" / "anchor-a" / "z19" / "anchor-a_20190115_v1.png"
    local.parent.mkdir(parents=True)
    reused = _png_bytes((4, 5, 6))
    local.write_bytes(reused)
    calls = {"n": 0}

    def fetcher():
        calls["n"] += 1
        return _png_bytes((7, 8, 9))

    reused_result = resolve_local_or_fetch(local_path=local, fetcher=fetcher)
    assert reused_result.status == "reused"
    assert reused_result.data == reused
    assert calls["n"] == 0

    fetched = resolve_local_or_fetch(local_path=local.parent / "missing.png", fetcher=fetcher)
    assert fetched.status == "downloaded"
    assert fetched.data == _png_bytes((7, 8, 9))
    assert calls["n"] == 1
    again = resolve_local_or_fetch(local_path=None, fetcher=fetcher)
    assert again.data == fetched.data
    assert calls["n"] == 2

    def boom():
        raise RuntimeError("provider boom")

    gap = resolve_local_or_fetch(local_path=None, fetcher=boom)
    assert gap.status == "gap"
    assert "provider boom" in gap.error

    package = tmp_path / "share"
    relative = "images/cape_town/1/sat_2019-01-15.png"
    placed = place_package_image(package, relative, fetched)
    assert placed == relative
    assert (package / relative).read_bytes() == fetched.data
    assert place_package_image(package, "images/cape_town/1/sat_2020-01-01.png", gap) is None

    roof_rows = [
        {
            "source_feature_id": "1",
            "source_anchor_id": "anchor-a",
            "scan_status": "done_appears",
            "centroid_lon": "18.45",
            "centroid_lat": "-33.95",
            "install_interval_start": "2019-01-15",
            "install_interval_end": "2020-06-01",
            "earliest_present_date": "2020-06-01",
            "cohort": "citywide_27wave",
            "left_censored": "0",
            "census_bound": "0",
            "wave": "",
        }
    ]
    observations = {
        "anchor-a": [
            {"capture_date": "2019-01-15", "frame_role": "evidence", "code_repaired": "0", "obs_index": "0"},
            {"capture_date": "2020-06-01", "frame_role": "evidence", "code_repaired": "1", "obs_index": "1"},
            {"capture_date": "2021-01-01", "frame_role": "evidence", "code_repaired": "u", "obs_index": "2"},
        ]
    }
    network_calls = {"n": 0}

    def satellite_fetcher(request):
        network_calls["n"] += 1
        if request.capture_date == "2021-01-01":
            raise RuntimeError("provider boom")
        return _png_bytes((7, 8, 9))

    manifest = build_package(
        [
            CityBundle(
                city=CT_CITY,
                rows=roof_rows,
                observations=observations,
                metric_crs="EPSG:32734",
                observation_key="source_anchor_id",
            )
        ],
        package,
        seed=DEFAULT_SEED,
        max_per_status=1,
        cache_dir=tmp_path / "cache",
        satellite_fetcher=satellite_fetcher,
        aerial_fetcher=lambda request: (_ for _ in ()).throw(RuntimeError("aerial should be local")),
        satellite_roots=[tmp_path / "reuse"],
        aerial_reuse_root=_write_aerial_png(tmp_path / "aerial", "anchor-a"),
        aerial_tiles=[],
    )
    assert network_calls["n"] == 2
    roof = manifest["cities"][0]["roofs"][0]
    by_date = {slot["capture_date"]: slot for slot in roof["satellites"]}
    assert by_date["2019-01-15"]["origin"] == "reused"
    assert (package / by_date["2019-01-15"]["relative_path"]).read_bytes() == reused
    assert by_date["2020-06-01"]["origin"] == "downloaded"
    assert (package / by_date["2020-06-01"]["relative_path"]).read_bytes() == _png_bytes((7, 8, 9))
    assert by_date["2021-01-01"]["origin"] == "gap"
    assert "provider boom" in by_date["2021-01-01"]["error"]
    html = (package / "index.html").read_text(encoding="utf-8")
    assert "anchor-a" in html
    assert "provider boom" in html
    assert "done_appears" in html


def _write_aerial_png(root: Path, anchor_id: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{anchor_id}.png").write_bytes(_png_bytes((9, 9, 9)))
    return root


def _bundle(city: str, status: str, sfid: str, anchor: str, *, cohort: str = "citywide_27wave") -> dict[str, str]:
    row = {
        "source_feature_id": sfid,
        "source_anchor_id": anchor,
        "scan_status": status,
        "centroid_lon": "18.4505",
        "centroid_lat": "-34.0015",
        "install_interval_start": f"{city}-start",
        "install_interval_end": f"{city}-end",
        "left_censored": "0",
        "census_bound": "1",
        "wave": "",
    }
    if city == CT_CITY:
        row["latest_absent_date"] = "2019-03-01"
        row["earliest_present_date"] = "2020-07-01"
        row["cohort"] = cohort
    else:
        row["earliest_present_date"] = "2024-04-19"
        row["lane"] = "a48"
    return row


def test_webpage_separates_cities_and_uses_relative_images(tmp_path: Path):
    ct_anchor = "ct_anchor"
    jhb_anchor = "jhb_anchor"
    ct_rows = [_bundle(CT_CITY, "done_appears", "7", ct_anchor)]
    ct_rows.append(_bundle(CT_CITY, "done_appears", "8", "ct_other"))
    jhb_rows = [_bundle(JHB_CITY, "done_ambiguous_clamp_inverted", "3", jhb_anchor)]
    jhb_rows.append(_bundle(JHB_CITY, "done_ambiguous_clamp_inverted", "4", "jhb_other"))
    # Eight extra Cape Town rows of a second status keep the draw a subset.
    for index in range(8):
        ct_rows.append(_bundle(CT_CITY, "done_ambiguous_nonmonotonic", str(100 + index), f"ct_extra_{index}"))
    observations = {
        ct_anchor: [
            {"capture_date": "2020-07-01", "frame_role": "evidence", "code_repaired": "1", "obs_index": "1"},
            {"capture_date": "2019-03-01", "frame_role": "evidence", "code_repaired": "0", "obs_index": "0"},
        ],
        "ct_other": [
            {"capture_date": "2020-07-01", "frame_role": "evidence", "code_repaired": "1", "obs_index": "1"},
            {"capture_date": "2019-03-01", "frame_role": "evidence", "code_repaired": "0", "obs_index": "0"},
        ],
        "3": [
            {"capture_date": "2024-04-19", "pv_present": "1", "obs_order": "1", "provider": "TM"},
            {"capture_date": "2019-01-15", "pv_present": "0", "obs_order": "0", "provider": "TM"},
        ],
        "4": [
            {"capture_date": "2024-04-19", "pv_present": "1", "obs_order": "1", "provider": "TM"},
            {"capture_date": "2019-01-15", "pv_present": "0", "obs_order": "0", "provider": "TM"},
        ],
    }
    for index in range(8):
        observations[f"ct_extra_{index}"] = observations[ct_anchor]
    reuse = tmp_path / "sat"
    aerial = tmp_path / "aerial"
    picked_ct = sample_roofs_by_status(ct_rows, city=CT_CITY, seed=DEFAULT_SEED, max_per_status=1)
    picked_jhb = sample_roofs_by_status(jhb_rows, city=JHB_CITY, seed=DEFAULT_SEED, max_per_status=1)
    for row in picked_ct + picked_jhb:
        anchor = row["source_anchor_id"]
        for day in ("20190115", "20190301", "20200701", "20240419"):
            path = reuse / anchor / "z19" / f"{anchor}_{day}_v1.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_png_bytes())
        _write_aerial_png(aerial, anchor)
    package = tmp_path / "page"
    manifest = build_package(
        [
            CityBundle(
                city=CT_CITY,
                rows=ct_rows,
                observations=observations,
                metric_crs="EPSG:32734",
                observation_key="source_anchor_id",
            ),
            CityBundle(
                city=JHB_CITY,
                rows=jhb_rows,
                observations=observations,
                metric_crs="EPSG:32735",
                observation_key="source_feature_id",
            ),
        ],
        package,
        seed=DEFAULT_SEED,
        max_per_status=1,
        cache_dir=tmp_path / "cache",
        satellite_fetcher=lambda request: (_ for _ in ()).throw(RuntimeError("should reuse")),
        aerial_fetcher=lambda request: (_ for _ in ()).throw(RuntimeError("should reuse")),
        satellite_roots=[reuse],
        aerial_reuse_root=aerial,
        aerial_tiles=[],
    )
    html = (package / "index.html").read_text(encoding="utf-8")
    assert html == render_review_html(json.loads((package / "manifest.json").read_text(encoding="utf-8")))
    ct_at = html.index("id='cape_town'")
    jhb_at = html.index("id='johannesburg'")
    assert ct_at < jhb_at
    ct_roof = manifest["cities"][0]["roofs"][0]
    jhb_roof = manifest["cities"][1]["roofs"][0]
    assert {roof["fields"]["scan_status"] for roof in manifest["cities"][0]["roofs"]} == {
        "done_appears",
        "done_ambiguous_nonmonotonic",
    }
    assert jhb_roof["fields"]["scan_status"] == "done_ambiguous_clamp_inverted"
    for roof in (ct_roof, jhb_roof):
        for name, value in roof["fields"].items():
            assert value in html
            assert f"<th>{name}</th><td>{value}</td>" in html
    ct_dates = [slot["capture_date"] for slot in ct_roof["satellites"]]
    assert ct_dates == sorted(ct_dates)
    assert " · 0" in html and " · 1" in html
    srcs = re.findall(r"src='([^']+)'", html)
    assert srcs
    for src in srcs:
        assert not src.startswith(("http://", "https://", "/"))
        assert (package / src).is_file()
    assert "type=\"module\"" not in html
    assert "<script" not in html
    assert "https://" not in html
    assert "http://" not in html
    assert (package.parent / f"{package.name}.tar.gz").is_file()


def _entry_fixture(root: Path) -> dict[str, Path]:
    """Tiny two-city delivery the real CLI can build without network."""

    import geopandas as gpd
    from shapely.geometry import Polygon

    transform = _write_geotiff(root / "_template.tif", crs="EPSG:4326")
    polygon = _square(transform)
    centroid = _pixel_center(transform, 4, 15)
    ct_rows = []
    jhb_rows = []
    for index, status in enumerate(("done_appears", "done_appears", "done_appears")):
        ct_rows.append(
            {
                "source_feature_id": str(index),
                "source_anchor_id": f"ct{index}",
                "source_grid": "CPT0001",
                "scan_status": status,
                "centroid_lon": str(centroid[0]),
                "centroid_lat": str(centroid[1]),
                "install_interval_start": "2019-01-15",
                "install_interval_end": "2020-06-01",
                "latest_absent_date": "2019-01-15",
                "earliest_present_date": "2020-06-01",
                "cohort": "citywide_27wave",
                "left_censored": "0",
                "census_bound": "0",
                "wave": "",
            }
        )
    jhb_rows.append(
        {
            "source_feature_id": "0",
            "source_anchor_id": "jhb0",
            "source_grid": "JNB0001",
            "scan_status": "done_ambiguous_clamp_inverted",
            "centroid_lon": str(centroid[0]),
            "centroid_lat": str(centroid[1]),
            "install_interval_start": "2019-01-15",
            "install_interval_end": "2024-04-19",
            "earliest_present_date": "2024-04-19",
            "lane": "a24",
            "left_censored": "0",
            "census_bound": "1",
            "wave": "",
        }
    )
    for index in range(1, 4):
        jhb_rows.append(
            {
                **jhb_rows[0],
                "source_feature_id": str(index),
                "source_anchor_id": f"jhb{index}",
                "scan_status": "done_appears",
            }
        )

    def write_table(path: Path, rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    ct_csv = root / "ct.csv"
    jhb_csv = root / "jhb.csv"
    write_table(ct_csv, ct_rows)
    write_table(jhb_csv, jhb_rows)
    ct_obs = root / "ct_obs.csv"
    jhb_obs = root / "jhb_obs.csv"
    with ct_obs.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_anchor_id", "frame_role", "capture_date", "code_repaired", "obs_index"],
        )
        writer.writeheader()
        for row in ct_rows:
            writer.writerow(
                {
                    "source_anchor_id": row["source_anchor_id"],
                    "frame_role": "evidence",
                    "capture_date": "2020-06-01",
                    "code_repaired": "1",
                    "obs_index": "1",
                }
            )
            writer.writerow(
                {
                    "source_anchor_id": row["source_anchor_id"],
                    "frame_role": "evidence",
                    "capture_date": "2019-01-15",
                    "code_repaired": "0",
                    "obs_index": "0",
                }
            )
    with jhb_obs.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_feature_id", "capture_date", "pv_present", "provider", "obs_order"],
        )
        writer.writeheader()
        for row in jhb_rows:
            writer.writerow(
                {
                    "source_feature_id": row["source_feature_id"],
                    "capture_date": "2024-04-19",
                    "pv_present": "1",
                    "provider": "TM",
                    "obs_order": "1",
                }
            )
            writer.writerow(
                {
                    "source_feature_id": row["source_feature_id"],
                    "capture_date": "2019-01-15",
                    "pv_present": "0",
                    "provider": "TM",
                    "obs_order": "0",
                }
            )
    ct_pick = sample_roofs_by_status(ct_rows, city=CT_CITY, seed=DEFAULT_SEED, max_per_status=1)
    jhb_pick = sample_roofs_by_status(jhb_rows, city=JHB_CITY, seed=DEFAULT_SEED, max_per_status=1)
    frames = []
    for row in ct_pick + jhb_pick:
        frames.append((row["source_feature_id"], row["source_anchor_id"], polygon, centroid))
    ct_gpkg = root / "ct.gpkg"
    jhb_gpkg = root / "jhb.gpkg"
    ct_frame = gpd.GeoDataFrame(
        {"source_feature_id": [int(row["source_feature_id"]) for row in ct_rows]},
        geometry=[polygon] * len(ct_rows),
        crs="EPSG:4326",
    )
    jhb_frame = gpd.GeoDataFrame(
        {"source_feature_id": [int(row["source_feature_id"]) for row in jhb_rows]},
        geometry=[polygon] * len(jhb_rows),
        crs="EPSG:4326",
    )
    ct_frame.to_file(ct_gpkg, layer="ct_solar_install_dated", driver="GPKG")
    jhb_frame.to_file(jhb_gpkg, layer="solar_install_dated", driver="GPKG")
    reuse = root / "reuse"
    fetch = root / "fetch"
    aerial = root / "aerial"
    template = root / "_template.tif"
    for row in ct_rows + jhb_rows:
        anchor = row["source_anchor_id"]
        reused = reuse / anchor / "z19" / f"{anchor}_20190115_v1.tif"
        reused.parent.mkdir(parents=True, exist_ok=True)
        reused.write_bytes(template.read_bytes())
        for day in ("2020-06-01", "2024-04-19"):
            destination = fetch / anchor / f"{day}.tif"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(template.read_bytes())
        aerial_path = aerial / f"{anchor}.tif"
        aerial.mkdir(parents=True, exist_ok=True)
        aerial_path.write_bytes(template.read_bytes())
    return {
        "ct_csv": ct_csv,
        "jhb_csv": jhb_csv,
        "ct_obs": ct_obs,
        "jhb_obs": jhb_obs,
        "ct_gpkg": ct_gpkg,
        "jhb_gpkg": jhb_gpkg,
        "reuse": reuse,
        "fetch": fetch,
        "aerial": aerial,
        "empty": root / "empty",
    }


def test_entry_point_is_deterministic_and_shows_marks(tmp_path: Path):
    fixture = _entry_fixture(tmp_path / "fixture")
    output = tmp_path / "share"
    args = [
        "--output-dir",
        str(output),
        "--seed",
        str(DEFAULT_SEED),
        "--max-per-status",
        "1",
        "--ct-csv",
        str(fixture["ct_csv"]),
        "--jhb-csv",
        str(fixture["jhb_csv"]),
        "--ct-gpkg",
        str(fixture["ct_gpkg"]),
        "--jhb-gpkg",
        str(fixture["jhb_gpkg"]),
        "--ct-observations",
        str(fixture["ct_obs"]),
        "--jhb-observations",
        str(fixture["jhb_obs"]),
        "--ct-chip-root",
        str(fixture["empty"]),
        "--jhb-chip-root",
        str(fixture["empty"]),
        "--aerial-reuse-root",
        str(fixture["aerial"]),
        "--satellite-reuse-root",
        str(fixture["reuse"]),
        "--fixture-fetch-dir",
        str(fixture["fetch"]),
        "--cache-dir",
        str(tmp_path / "cache"),
    ]
    assert main(args) == 0
    first_manifest = (output / "manifest.json").read_bytes()
    first_html = (output / "index.html").read_text(encoding="utf-8")
    first_log = (output / "fetch_log.jsonl").read_text(encoding="utf-8") if (output / "fetch_log.jsonl").exists() else ""
    assert "done_ambiguous_clamp_inverted" in first_html
    assert "2019-01-15" in first_html and " · 0" in first_html
    assert "census centroid" in first_html
    assert "id='cape_town'" in first_html and "id='johannesburg'" in first_html
    manifest = json.loads(first_manifest)
    origins = []
    saw_outline = False
    saw_centroid = False
    for city in manifest["cities"]:
        for roof in city["roofs"]:
            aerial = roof["aerial"]
            assert aerial["origin"] == "reused"
            assert aerial["outline_placed"] is True
            aerial_image = Image.open(output / aerial["relative_path"])
            assert aerial_image.getpixel((2, 2)) == OUTLINE_RGB
            saw_outline = True
            for slot in roof["satellites"]:
                origins.append(slot["origin"])
                if slot["capture_date"] == "2019-01-15":
                    assert slot["origin"] == "reused"
                    assert slot["code"] == "0"
                else:
                    assert slot["origin"] == "downloaded"
                assert slot["centroid_placed"] is True
                sat_image = Image.open(output / slot["relative_path"])
                assert sat_image.getpixel((4, 15)) == CENTROID_RGB
                assert sat_image.getpixel((10, 10)) != CENTROID_RGB
                saw_centroid = True
    assert saw_outline and saw_centroid
    assert "reused" in origins and "downloaded" in origins
    assert main(args) == 0
    assert (output / "manifest.json").read_bytes() == first_manifest
    assert (output / "index.html").read_text(encoding="utf-8") == first_html
    second_log = (output / "fetch_log.jsonl").read_text(encoding="utf-8") if (output / "fetch_log.jsonl").exists() else ""
    assert second_log == first_log
    assert first_log.count("\n") >= 1
