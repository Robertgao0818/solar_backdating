"""WP-C window-chip re-render tests (ISSUE-10 gold-set tooling).

Covers the WP-C acceptance criteria of docs/replan_v2/ISSUE-10-design-2026-07-05.md:

* frame resolution: latest-absent / earliest-present bounds (via `_anchor_frames`)
  plus +/- flanks, deduped, correct roles;
* idempotency: a 2nd run over already-rendered chips issues zero real GEHI
  fetches (`skipped_existing`) via an injected fake runner — NO network / .NET;
* drop-and-report: a frame that fails every zoom rung (`all_zooms_failed`) lands
  in `frame_report.csv` and leaves no chip on disk, without crashing the batch;
* bbox join: the GEHI bbox is taken from the chipgroups fixture, never invented;
  an anchor absent from chipgroups is reported (`bbox_missing`), not fatal;
* CoJ copy: municipal chips are copied idempotently; a missing CoJ vintage is
  `source_missing` (drop-and-report).

Test seam mirrors `test_gehi_zoom_ladder.py`: an injected `runner` simulates the
GEHI subprocess, so no download runs here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.temporal.gehi_common import GehiRunResult
from scripts.temporal.goldset_schema import (
    SAMPLE_ASSIGNMENT_FIELDS,
    rerender_chips_dir,
    rerender_report_path,
    write_sample_assignments,
)
from scripts.temporal.rerender_goldset_windows import (
    frame_zoom_ladder,
    load_bbox_index,
    rerender_anchor,
    rerender_from_assignments,
    resolve_window_frames,
)
from scripts.temporal.scan_state import (
    Round,
    RoundResult,
    ScanState,
    save_scan_state,
    state_path_for,
)

FIXTURES = Path(__file__).parent / "fixtures" / "goldset_rerender"
CHIPGROUPS_CSV = FIXTURES / "chip_groups_as_anchors.csv"

ANCHOR_1 = "goldset_c0000001"
ANCHOR_2 = "goldset_c0000002"


# ---------------------------------------------------------------------------
# builders

def _result(capture_date: str, *, present: bool | None, version: int, zoom: int = 19) -> RoundResult:
    return RoundResult(
        chip_index=1,
        capture_date=capture_date,
        version=version,
        pv_present=present,
        confidence=0.95 if present is not None else None,
        quality_flag="usable" if present is not None else "unusable",
        decision_source="gemini_batch",
        evidence=f"stub {capture_date}",
        chip_path=f"/deleted/{capture_date}_v{version}.tif",  # retained-but-dead path string
        actual_zoom=zoom,
    )


def _state(anchor_id: str, results: list[RoundResult], *, status: str = "done_appears") -> ScanState:
    state = ScanState(anchor_id=anchor_id, region_key="johannesburg", grid_id="JNB01")
    state.status = status
    state.rounds = [
        Round(
            round_id=1,
            round_type="initial",
            window_start_date=results[0].capture_date if results else None,
            window_end_date=results[-1].capture_date if results else None,
            results=results,
            completed=True,
        )
    ]
    return state


def _default_results() -> list[RoundResult]:
    # ascending capture_date; bracket = (2018-03-30 absent, 2019-06-01 present)
    return [
        _result("2016-01-10", present=False, version=100),  # flank_before (older)
        _result("2017-05-20", present=False, version=200),  # flank_before (nearer)
        _result("2018-03-30", present=False, version=296),  # latest_absent
        _result("2019-06-01", present=True, version=300),   # earliest_present
        _result("2020-08-15", present=True, version=310),   # flank_after (nearer)
        _result("2021-11-02", present=True, version=320),   # flank_after (older)
    ]


def _make_runner(*, succeed: bool):
    """GEHI subprocess stub. `succeed=True` writes the output file + rc=0.

    Records every invocation with its zoom, resolved output path, and the
    --lower-left / --upper-right bbox args so tests can assert the bbox join.
    """
    calls: list[dict[str, Any]] = []

    def runner(cmd_args, *, executable, timeout):
        info: dict[str, Any] = {}
        for i, arg in enumerate(cmd_args):
            if arg == "--zoom":
                info["zoom"] = int(cmd_args[i + 1])
            elif arg == "--output":
                info["out_path"] = Path(cmd_args[i + 1])
            elif arg == "--lower-left":
                info["lower_left"] = str(cmd_args[i + 1])
            elif arg == "--upper-right":
                info["upper_right"] = str(cmd_args[i + 1])
        calls.append(info)
        if succeed and info.get("out_path") is not None:
            info["out_path"].parent.mkdir(parents=True, exist_ok=True)
            info["out_path"].write_bytes(b"FAKE_TIFF")
            return GehiRunResult(args=tuple(str(a) for a in cmd_args), returncode=0, stdout="", stderr="")
        return GehiRunResult(
            args=tuple(str(a) for a in cmd_args), returncode=1, stdout="", stderr="no vintage",
        )

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _write_assignments(path: Path, anchor_ids: list[str]) -> None:
    rows = []
    for aid in anchor_ids:
        for annot in ("A", "B"):  # both annotator rows per anchor (dedup expected)
            rows.append({
                "anchor_id": aid, "annotator_id": annot, "is_overlap": "True",
                "sample_batch_id": "batch_test", "sampler_seed": "1",
            })
    assert set(("anchor_id", "annotator_id")).issubset(SAMPLE_ASSIGNMENT_FIELDS)
    write_sample_assignments(rows, path)


# ---------------------------------------------------------------------------
# frame resolution

def test_resolve_window_frames_bounds_and_flanks() -> None:
    state = _state(ANCHOR_1, _default_results())
    frames = resolve_window_frames(state, flank=2)
    by_role = {}
    for f in frames:
        by_role.setdefault(f.role, []).append(f.capture_date)

    assert by_role["latest_absent"] == ["2018-03-30"]
    assert by_role["earliest_present"] == ["2019-06-01"]
    # nearest-2 usable slots strictly before the absent bound, ascending
    assert by_role["flank_before"] == ["2016-01-10", "2017-05-20"]
    # nearest-2 usable slots strictly after the present bound, ascending
    assert by_role["flank_after"] == ["2020-08-15", "2021-11-02"]
    # every emitted frame is a scan_tm source and each slot appears once
    assert all(f.source == "scan_tm" for f in frames)
    assert len({(f.capture_date, f.version) for f in frames}) == len(frames)


def test_resolve_window_frames_flank_zero() -> None:
    state = _state(ANCHOR_1, _default_results())
    frames = resolve_window_frames(state, flank=0)
    assert {f.role for f in frames} == {"latest_absent", "earliest_present"}


def test_frame_zoom_ladder() -> None:
    assert frame_zoom_ladder(19) == (19, 18)
    assert frame_zoom_ladder(None) == (19, 18)
    assert frame_zoom_ladder(19, override=[20, 19]) == (20, 19)


def test_load_bbox_index() -> None:
    idx = load_bbox_index(CHIPGROUPS_CSV)
    assert ANCHOR_1 in idx and ANCHOR_2 in idx
    assert idx[ANCHOR_1]["chip_lon_min"] == "28.01412"


# ---------------------------------------------------------------------------
# idempotency

def test_idempotency_second_run_zero_fetches(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_states"
    save_scan_state(_state(ANCHOR_1, _default_results()), state_path_for(ANCHOR_1, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_assignments(assignments, [ANCHOR_1])
    root = tmp_path / "out"

    runner1 = _make_runner(succeed=True)
    rows1 = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=None, runner=runner1,
    )
    n_scan_frames = sum(1 for r in rows1 if r["source"] == "scan_tm")
    assert len(runner1.calls) == n_scan_frames  # one live fetch per scan frame
    assert all(r["status"] == "ok" for r in rows1 if r["source"] == "scan_tm")

    runner2 = _make_runner(succeed=True)
    rows2 = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=None, runner=runner2,
    )
    assert runner2.calls == []  # skip-if-exists => zero real fetches
    assert all(r["status"] == "skipped_existing" for r in rows2 if r["source"] == "scan_tm")

    # report file written and anchor deduped (A + B rows => one re-render pass)
    assert rerender_report_path(root).exists()
    assert {r["anchor_id"] for r in rows2} == {ANCHOR_1}


# ---------------------------------------------------------------------------
# drop-and-report

def test_drop_and_report_all_zooms_failed(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_states"
    save_scan_state(_state(ANCHOR_1, _default_results()), state_path_for(ANCHOR_1, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_assignments(assignments, [ANCHOR_1])
    root = tmp_path / "out"

    runner = _make_runner(succeed=False)  # every zoom rung fails
    rows = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=None, runner=runner,
    )
    scan_rows = [r for r in rows if r["source"] == "scan_tm"]
    assert scan_rows and all(r["status"] == "all_zooms_failed" for r in scan_rows)
    # no chip left on disk for the failed frames
    chips = list((rerender_chips_dir(root) / ANCHOR_1).rglob("*.tif"))
    assert chips == []
    # batch still produced a report (did not crash)
    assert rerender_report_path(root).exists()


# ---------------------------------------------------------------------------
# bbox join

def test_bbox_joined_from_chipgroups(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_states"
    save_scan_state(_state(ANCHOR_1, _default_results()), state_path_for(ANCHOR_1, scan_dir))
    root = tmp_path / "out"
    bbox_row = load_bbox_index(CHIPGROUPS_CSV)[ANCHOR_1]

    runner = _make_runner(succeed=True)
    rerender_anchor(
        ANCHOR_1, _state(ANCHOR_1, _default_results()), bbox_row,
        chips_root=rerender_chips_dir(root), coj_chips_dir=None, runner=runner,
    )
    # anchor_bbox_args formats lower-left = "lat_min,lon_min" at 10 dp
    assert runner.calls
    assert runner.calls[0]["lower_left"] == "-26.1833500000,28.0141200000"
    assert runner.calls[0]["upper_right"] == "-26.1830200000,28.0144900000"


def test_missing_bbox_reported_not_fatal(tmp_path: Path) -> None:
    root = tmp_path / "out"
    runner = _make_runner(succeed=True)
    rows = rerender_anchor(
        "goldset_unknown", _state("goldset_unknown", _default_results()), None,
        chips_root=rerender_chips_dir(root), coj_chips_dir=None, runner=runner,
    )
    assert len(rows) == 1 and rows[0]["status"] == "bbox_missing"
    assert runner.calls == []  # never attempted a fetch without a bbox


def test_missing_scan_state_reported_not_fatal(tmp_path: Path) -> None:
    root = tmp_path / "out"
    runner = _make_runner(succeed=True)
    bbox_row = load_bbox_index(CHIPGROUPS_CSV)[ANCHOR_1]
    rows = rerender_anchor(
        ANCHOR_1, None, bbox_row,
        chips_root=rerender_chips_dir(root), coj_chips_dir=None, runner=runner,
    )
    assert len(rows) == 1 and rows[0]["status"] == "scan_state_missing"


# ---------------------------------------------------------------------------
# CoJ copy

def test_coj_copy_idempotent_and_source_missing(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_states"
    save_scan_state(_state(ANCHOR_1, _default_results()), state_path_for(ANCHOR_1, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_assignments(assignments, [ANCHOR_1])
    root = tmp_path / "out"

    # only the 2019 municipal chip exists on disk for this anchor
    coj_dir = tmp_path / "coj_chips"
    (coj_dir / "2019").mkdir(parents=True)
    (coj_dir / "2019" / f"{ANCHOR_1}.tif").write_bytes(b"COJ_2019_TIFF")

    runner = _make_runner(succeed=True)
    rows = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=coj_dir, runner=runner,
    )
    coj_rows = {r["source"]: r for r in rows if str(r["source"]).startswith("coj_")}
    assert coj_rows["coj_2019"]["status"] == "copied"
    assert coj_rows["coj_2015"]["status"] == "source_missing"
    assert coj_rows["coj_2023"]["status"] == "source_missing"
    copied = rerender_chips_dir(root) / ANCHOR_1 / "coj_2019.tif"
    assert copied.exists() and copied.read_bytes() == b"COJ_2019_TIFF"

    # second run => the copied chip is skipped_existing (idempotent)
    rows2 = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=coj_dir, runner=_make_runner(succeed=True),
    )
    coj2 = {r["source"]: r for r in rows2 if str(r["source"]).startswith("coj_")}
    assert coj2["coj_2019"]["status"] == "skipped_existing"


# ---------------------------------------------------------------------------
# CLI smoke (offline: no frames => no download, no coj dir => source_missing)

def test_main_cli_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from scripts.temporal import rerender_goldset_windows as mod

    scan_dir = tmp_path / "scan_states"
    # anchor with zero usable observations => resolve_window_frames yields nothing
    save_scan_state(_state(ANCHOR_1, []), state_path_for(ANCHOR_1, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_assignments(assignments, [ANCHOR_1])
    root = tmp_path / "out"

    argv = [
        "rerender_goldset_windows.py",
        "--assignments", str(assignments),
        "--scan-states-dir", str(scan_dir),
        "--chipgroups-csv", str(CHIPGROUPS_CSV),
        "--tag", "cli_test",
        "--root", str(root),
    ]
    monkeypatch.setattr("sys.argv", argv)
    mod.main()  # must not touch the network (no scan frames) and must not crash

    report = rerender_report_path(root)
    assert report.exists()
    rows = mod.read_csv_rows(report)
    # only CoJ reference rows, all source_missing (no --coj-chips-dir)
    assert rows and all(r["status"] == "source_missing" for r in rows)
