from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "temporal"))

import run_ct05_catalog_probe as ct05  # noqa: E402
from scripts.temporal.gehi_common import GehiRateLimiter, GehiRunResult  # noqa: E402


def anchor(anchor_id: str) -> dict[str, object]:
    return {
        "anchor_id": anchor_id,
        "region_key": "cape_town",
        "grid_id": "CPT0001",
        "centroid_lon": "18.4",
        "centroid_lat": "-34.0",
        "chip_lon_min": "18.399",
        "chip_lat_min": "-34.001",
        "chip_lon_max": "18.401",
        "chip_lat_max": "-33.999",
    }


def no_sleep_limiter() -> GehiRateLimiter:
    return GehiRateLimiter(
        min_interval_s=0,
        jitter_frac=0,
        soft_backoff_s=0,
        hard_backoff_s=0,
        sleep_fn=lambda _seconds: None,
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_route_plan_is_stable_complete_and_disjoint(tmp_path: Path) -> None:
    anchors = [anchor(f"a{i}") for i in range(31)]
    first = ct05.plan_routes(anchors, ct05.DEFAULT_ROUTES, tmp_path / "one")
    second = ct05.plan_routes(anchors, ct05.DEFAULT_ROUTES, tmp_path / "two")
    assert [row["anchor_count"] for row in first["shards"]] == [
        row["anchor_count"] for row in second["shards"]
    ]
    seen: list[str] = []
    for shard in first["shards"]:
        seen.extend(row["anchor_id"] for row in read_csv(Path(shard["path"])))
    assert sorted(seen) == sorted(row["anchor_id"] for row in anchors)
    assert len(seen) == len(set(seen))


def test_remaining_plan_excludes_only_fully_completed_anchors(tmp_path: Path) -> None:
    anchors = [anchor("done"), anchor("partial"), anchor("fresh")]
    outcome = tmp_path / "outcomes.jsonl"
    rows = []
    for spec in ct05.REQUIRED_SPECS:
        rows.append(
            {
                "anchor_id": "done",
                "provider": spec.provider,
                "zoom": spec.zoom,
                "query_kind": spec.query_kind,
                "status": "ok_nonempty",
            }
        )
    first = ct05.REQUIRED_SPECS[0]
    rows.append(
        {
            "anchor_id": "partial",
            "provider": first.provider,
            "zoom": first.zoom,
            "query_kind": first.query_kind,
            "status": "ok_nonempty",
        }
    )
    outcome.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = ct05.plan_remaining_lanes(
        anchors,
        [outcome],
        route_id="home_v4",
        lanes=2,
        out_dir=tmp_path / "remaining",
    )
    assert result["fully_completed_anchor_count"] == 1
    assert result["remaining_anchor_count"] == 2
    ids = []
    for shard in result["lane_shards"]:
        ids.extend(row["anchor_id"] for row in read_csv(Path(shard["path"])))
    assert sorted(ids) == ["fresh", "partial"]


def test_wayback_info_dedupes_by_date_not_internal_version() -> None:
    result = GehiRunResult(
        args=("gehi", "info"),
        returncode=0,
        stdout=(
            "Level = 19, Path = 1\n"
            "date = 2020/01/01, version = 777\n"
            "Level = 19, Path = 2\n"
            "date = 2021/02/02, version = 777\n"
        ),
        stderr="",
    )
    rows = ct05.parse_query_rows(
        "a1",
        ct05.QuerySpec("Wayback", 19, "info"),
        result,
        "home_v6",
        min_date="2009-01-01",
        max_date="2025-12-31",
    )
    assert [row["capture_date"] for row in rows] == ["2020-01-01", "2021-02-02"]


def test_probe_records_blocked_as_failure_not_empty(tmp_path: Path) -> None:
    def blocked(*_args, **_kwargs):
        return GehiRunResult(
            args=("gehi",),
            returncode=1,
            stdout="",
            stderr="HTTP 429 Too Many Requests",
        )

    summary = ct05.run_probe(
        [anchor("a1")],
        route_id="home_v4",
        out_dir=tmp_path,
        runner=blocked,
        limiter=no_sleep_limiter(),
        max_attempts=2,
    )
    assert summary["failed_this_run"] == 4
    outcomes = ct05.read_jsonl(tmp_path / "query_outcomes.jsonl")
    assert {row["status"] for row in outcomes} == {"blocked"}
    assert all(row["status"] != "ok_empty" for row in outcomes)
    assert len(ct05.read_jsonl(tmp_path / "raw_gehi.jsonl")) == 8


def write_route(
    route_dir: Path,
    anchor_id: str,
    *,
    tm_dates: list[str],
    wb_dates: list[tuple[str, str]],
) -> None:
    route_dir.mkdir(parents=True)
    outcomes = []
    catalog = []
    for spec in ct05.REQUIRED_SPECS:
        dates = tm_dates if spec.provider == "TM" else [date for date, _ in wb_dates]
        outcomes.append(
            {
                "anchor_id": anchor_id,
                "route_id": route_dir.name,
                "provider": spec.provider,
                "zoom": spec.zoom,
                "query_kind": spec.query_kind,
                "status": "ok_nonempty" if dates else "ok_empty",
                "n_rows": len(dates),
            }
        )
        if spec.zoom != 19:
            continue
        if spec.provider == "TM":
            pairs = [(date, "") for date in tm_dates]
        else:
            pairs = wb_dates
        for date, version in pairs:
            catalog.append(
                {
                    "anchor_id": anchor_id,
                    "capture_date": date,
                    "version": version,
                    "provider": spec.provider,
                    "zoom": spec.zoom,
                    "query_kind": spec.query_kind,
                    "route_id": route_dir.name,
                    "stdout_sha256": "abc",
                    "gehi_command": "gehi",
                }
            )
    (route_dir / "query_outcomes.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in outcomes)
    )
    (route_dir / "catalog_rows.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in catalog)
    )


def test_merge_wayback_only_path_is_release_eligible(tmp_path: Path) -> None:
    route = tmp_path / "home_v6"
    write_route(
        route,
        "a1",
        tm_dates=[],
        wb_dates=[("2018-01-01", "111"), ("2024-02-02", "222")],
    )
    summary = ct05.merge_routes([anchor("a1")], [route], tmp_path / "merged")
    outcomes = read_csv(tmp_path / "merged" / "anchor_catalog_outcomes.csv")
    candidates = read_csv(tmp_path / "merged" / "gehi_vintage_candidates_ct05.csv")
    assert summary["wayback_only_anchors"] == 1
    assert outcomes[0]["catalog_status"] == "wayback_only"
    assert outcomes[0]["release_eligible"] == "1"
    assert {row["provider"] for row in candidates} == {"Wayback"}


def test_merge_dedupes_globally_by_anchor_date_and_prefers_bbox_complete_tm(
    tmp_path: Path,
) -> None:
    route = tmp_path / "home_v4"
    write_route(
        route,
        "a1",
        tm_dates=["2020-01-01"],
        wb_dates=[("2020-01-01", "777"), ("2026-01-01", "888")],
    )
    ct05.merge_routes([anchor("a1")], [route], tmp_path / "merged")
    candidates = read_csv(tmp_path / "merged" / "gehi_vintage_candidates_ct05.csv")
    assert len(candidates) == 1
    assert candidates[0]["provider"] == "TM"
    assert candidates[0]["bbox_complete_at_catalog"] == "1"


def test_merge_emits_retry_manifest_for_unresolved_query(tmp_path: Path) -> None:
    route = tmp_path / "home_v4"
    write_route(route, "a1", tm_dates=["2020-01-01"], wb_dates=[])
    outcomes_path = route / "query_outcomes.jsonl"
    rows = [
        row
        for row in ct05.read_jsonl(outcomes_path)
        if not (row["provider"] == "Wayback" and row["zoom"] == 18)
    ]
    outcomes_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = ct05.merge_routes([anchor("a1")], [route], tmp_path / "merged")
    assert summary["operational_failure_anchors"] == 1
    retry = read_csv(tmp_path / "merged" / "retry_anchors.csv")
    assert [row["anchor_id"] for row in retry] == ["a1"]
    assert read_csv(tmp_path / "merged" / "gehi_vintage_candidates_ct05.csv") == []


def test_non_ct_anchor_rejected() -> None:
    bad = anchor("a1")
    bad["region_key"] = "johannesburg"
    with pytest.raises(ValueError, match="non-Cape Town"):
        ct05.validate_anchors([bad])
