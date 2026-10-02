"""Offline tests for the Vexcel H3-7 GEHI info reconnaissance catalog."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.temporal.gehi_common import GehiRunResult
from scripts.temporal.probe_vexcel_h3r7_gehi_info import (
    is_success,
    probe_one,
    summarize_records,
    unique_h3_cells,
)


def _cell(**overrides) -> dict:
    base = {
        "h3_index": "87ad360daffffff",
        "lat": -33.62775812,
        "lng": 18.42595030,
        "municipality": "cape_town",
        "municipalities": ["cape_town"],
        "municipalities_joined": "cape_town",
        "collections_joined": "za-wc-avondale-2024",
        "municipality_name": "City of Cape Town",
    }
    base.update(overrides)
    return base


def test_unique_h3_collapses_spill_and_keeps_both_memberships():
    rows = [
        {
            "h3_index": "aabb",
            "municipality": "johannesburg",
            "collection": "za-gp-johannesburg-2024",
            "lat": -26.2,
            "lng": 28.0,
        },
        {
            "h3_index": "aabb",
            "municipality": "ekurhuleni",
            "collection": "za-gp-johannesburg-2024",
            "lat": -26.2,
            "lng": 28.0,
        },
        {
            "h3_index": "ccdd",
            "municipality": "ethekwini",
            "collection": "za-kzn-durban-2026",
            "lat": -29.85,
            "lng": 31.02,
        },
    ]
    cells = unique_h3_cells(rows)
    assert len(cells) == 2
    spill = next(c for c in cells if c["h3_index"] == "aabb")
    assert spill["municipalities"] == ["johannesburg", "ekurhuleni"]
    assert spill["municipality"] == "johannesburg"
    assert "za-gp-johannesburg-2024" in spill["collections_joined"]


def test_probe_one_empty_catalog_is_ok_empty_not_failure():
    def _runner(*args, **kwargs):
        return GehiRunResult(
            args=("gehi", "info"),
            returncode=0,
            stdout="Level = 19, Path = 021\n",
            stderr="",
        )

    outcome = probe_one(_cell(), "TM", runner=_runner, catalog_cache=None)
    assert outcome["status"] == "ok_empty"
    assert outcome["n_dates"] == 0
    assert is_success(outcome["status"])


def test_probe_one_timeout_is_not_empty():
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="GEHistoricalImagery info", timeout=300.0)

    outcome = probe_one(_cell(), "Wayback", runner=_timeout, catalog_cache=None)
    assert outcome["status"] == "timeout"
    assert outcome["n_dates"] == 0
    assert not is_success(outcome["status"])


def test_probe_one_blocked_is_retryable():
    def _blocked(*args, **kwargs):
        return GehiRunResult(
            args=("gehi", "info"),
            returncode=1,
            stdout="",
            stderr="HTTP 403 Forbidden",
        )

    outcome = probe_one(_cell(), "TM", runner=_blocked, catalog_cache=None)
    assert outcome["status"] == "blocked"
    assert not is_success(outcome["status"])


def test_probe_one_parses_tm_dates_and_dedupes():
    stdout = (
        "Level = 19, Path = 021\n"
        "date = 2019/06/15, version = 296\n"
        "date = 2019/06/15, version = 297\n"
        "date = 2020/01/02, version = 300\n"
    )

    def _runner(*args, **kwargs):
        return GehiRunResult(args=("gehi", "info"), returncode=0, stdout=stdout, stderr="")

    outcome = probe_one(_cell(), "TM", runner=_runner, catalog_cache=None)
    assert outcome["status"] == "ok_nonempty"
    assert outcome["n_dates"] == 2
    assert outcome["dates"] == ["2019-06-15", "2020-01-02"]
    assert outcome["n_years"] == 2
    assert outcome["years"] == "2019,2020"


def test_summarize_explodes_spill_cells_and_keeps_empty_in_median():
    spill = _cell(
        h3_index="aabb",
        municipality="johannesburg",
        municipalities=["johannesburg", "ekurhuleni"],
        municipalities_joined="johannesburg|ekurhuleni",
        municipality_name="City of Johannesburg",
        lat=-26.2,
        lng=28.0,
    )
    other = _cell(
        h3_index="ccdd",
        municipality="johannesburg",
        municipalities=["johannesburg"],
        municipalities_joined="johannesburg",
        municipality_name="City of Johannesburg",
        lat=-26.21,
        lng=28.01,
    )
    outcomes = {
        ("aabb", "TM"): {
            "status": "ok_nonempty",
            "n_dates": 10,
            "n_years": 3,
            "min_date": "2019-01-01",
            "max_date": "2021-01-01",
            "years": "2019,2020,2021",
            "dates": ["2019-01-01", "2020-01-01", "2021-01-01"],
            "zoom": 19,
        },
        ("aabb", "Wayback"): {
            "status": "ok_empty",
            "n_dates": 0,
            "n_years": 0,
            "min_date": "",
            "max_date": "",
            "years": "",
            "dates": [],
            "zoom": 19,
        },
        ("ccdd", "TM"): {
            "status": "ok_empty",
            "n_dates": 0,
            "n_years": 0,
            "min_date": "",
            "max_date": "",
            "years": "",
            "dates": [],
            "zoom": 19,
        },
        ("ccdd", "Wayback"): {
            "status": "timeout",
            "n_dates": 0,
            "n_years": 0,
            "min_date": "",
            "max_date": "",
            "years": "",
            "dates": [],
            "zoom": 19,
        },
    }
    tables = summarize_records([spill, other], outcomes)
    jhb_tm = next(
        r
        for r in tables["city_rows"]
        if r["municipality"] == "johannesburg" and r["provider"] == "TM"
    )
    eku_tm = next(
        r
        for r in tables["city_rows"]
        if r["municipality"] == "ekurhuleni" and r["provider"] == "TM"
    )
    assert jhb_tm["n_h3_vexcel"] == 2
    assert jhb_tm["n_nonempty"] == 1
    assert jhb_tm["n_empty"] == 1
    assert jhb_tm["median_dates"] == 5.0  # (10 + 0) / 2
    assert eku_tm["n_h3_vexcel"] == 1
    assert eku_tm["n_nonempty"] == 1
    jhb_wb = next(
        r
        for r in tables["city_rows"]
        if r["municipality"] == "johannesburg" and r["provider"] == "Wayback"
    )
    assert jhb_wb["n_fail"] == 1
    assert jhb_wb["n_empty"] == 1
    assert tables["n_success_pairs"] == 3
    assert tables["n_expected_pairs"] == 4


def test_probe_writes_resume_sentinels(tmp_path: Path):
    from scripts.temporal.probe_vexcel_h3r7_gehi_info import probe_path, _write_json

    cell = _cell()
    path = probe_path(tmp_path, cell["h3_index"], "TM")
    _write_json(
        path,
        {"status": "ok_empty", "n_dates": 0, "h3_index": cell["h3_index"], "provider": "TM"},
    )
    assert path.is_file()
    assert is_success("ok_empty")
    assert not is_success("timeout")
