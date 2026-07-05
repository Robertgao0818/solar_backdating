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
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows
from scripts.temporal.goldset_schema import (
    SAMPLE_ASSIGNMENT_FIELDS,
    read_sample_assignments,
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
FS_FIX = Path(__file__).parent / "fixtures" / "goldset_fullstack"

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


# ---------------------------------------------------------------------------
# WI-3 WP-C: dispute anchors' full-stack recovered-window frames
# (docs/replan_v2/ISSUE-11-prep-design-2026-07-05.md §WI-3, ISSUE-11 prep handoff §4
# step 4). Fixtures: `fixtures/goldset_fullstack/` — chip `fs_c0000542` owns 4 dispute
# targets (t00000001/t00000002 share modal FPD 2015-01-30 -> dedup fixture for AC-3.1;
# t00000003 is a modal tie 2018-01-30/2020-01-30; t00000004 is all-UNDATED -> AC-3.6);
# chip `fs_c0009873` has no full-stack coverage at all (banner-only, not this file's
# concern — covered by test_goldset_fullstack_frames.py AC-3.4).

ANCHOR_FS_DISPUTE = "fs_c0000542"


def _resolved_artifacts_csv(tmp_path: Path, frozen_root: Path, *, alive: bool) -> Path:
    """Copy the fixture `artifacts_fullstack.csv`, resolving its `{FROZEN}` placeholder
    to `frozen_root`. `alive=True` also writes a small dead-simple file at every
    resolved path (exercises the frozen-tif COPY path, no GEHI); `alive=False` leaves
    every path pointing at a file that does not exist (dead frozen path -> AC-3.5's
    GEHI fallback).
    """
    rows = read_csv_rows(FS_FIX / "artifacts_fullstack.csv")
    out_rows = []
    for row in rows:
        resolved = row["path"].replace("{FROZEN}", str(frozen_root))
        if alive:
            p = Path(resolved)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f"FROZEN {row['chip_id']} {row['capture_date']}".encode())
        out_rows.append({**row, "path": resolved})
    out_csv = tmp_path / f"artifacts_fullstack_{'alive' if alive else 'dead'}.csv"
    write_csv_rows(out_csv, out_rows, ("chip_id", "capture_date", "version", "path", "actual_zoom", "status"))
    return out_csv


def _write_dispute_assignment(path: Path, anchor_id: str, target_ids: list[str], *, grid_id: str = "JNB01") -> None:
    """Minimal is_dispute_forced assignment (A + B rows) for one anchor/target set."""
    rows = []
    for annot in ("A", "B"):
        rows.append({
            "anchor_id": anchor_id, "grid_id": grid_id, "stratum": "s", "terminal_status": "x",
            "confidence": "low", "any_contradiction": "", "pipeline_interval_start": "",
            "pipeline_interval_end": "", "latest_absent_date": "", "earliest_present_date": "",
            "scan_state_path": "", "annotator_id": annot, "is_overlap": "True",
            "is_dispute_forced": "True", "dispute_target_ids": ";".join(target_ids),
            "sampler_seed": "1", "sample_batch_id": "batch_fs",
        })
    write_sample_assignments(rows, path)


def test_ac3_1_fullstack_copy_and_dedup(tmp_path: Path) -> None:
    """AC-3.1: a modal-FPD date shared by two targets (t00000001, t00000002 both claim
    2015-01-30) is copied exactly ONCE into an `fsarm_` chip, and no GEHI runner call
    happens because every frozen tif is alive on disk.
    """
    artifacts_csv = _resolved_artifacts_csv(tmp_path, tmp_path / "frozen", alive=True)
    scan_dir = tmp_path / "scan_states"  # empty -> scan_state_missing (harmless, no runner call)
    root = tmp_path / "out"

    runner = _make_runner(succeed=True)
    rows = rerender_from_assignments(
        FS_FIX / "sample_assignments.csv",
        scan_states_dir=scan_dir,
        chipgroups_csv=FS_FIX / "chip_groups_as_anchors.csv",
        root=root,
        fullstack_artifacts_csv=artifacts_csv,
        fullstack_units_csv=FS_FIX / "per_unit_fullstack.csv",
        runner=runner,
    )
    assert runner.calls == []  # every frozen tif alive on disk -> zero GEHI invocations

    fs_rows = [r for r in rows if r["source"] == "fullstack" and r["anchor_id"] == ANCHOR_FS_DISPUTE]
    assert fs_rows

    # the shared modal date claimed by both t00000001 and t00000002 appears exactly
    # once in the frame report (deduped), recovered via the frozen-tif copy path.
    shared = [r for r in fs_rows if r["capture_date"] == "2015-01-30"]
    assert len(shared) == 1
    assert shared[0]["status"] == "fullstack_copied"

    chip_dir = rerender_chips_dir(root) / ANCHOR_FS_DISPUTE
    shared_chip_files = list(chip_dir.glob("fsarm_2015-01-30_v*"))
    assert len(shared_chip_files) == 1  # ONE file materialized, not one per owning target

    # the caption sidecar records both owning targets for the deduped frame.
    sidecar = read_csv_rows(root / "rerender" / "fullstack_frames.csv")
    shared_sidecar = [r for r in sidecar if r["capture_date"] == "2015-01-30"]
    assert len(shared_sidecar) == 1
    assert set(shared_sidecar[0]["owning_target_ids"].split(";")) == {"t00000001", "t00000002"}
    assert shared_sidecar[0]["is_modal_fpd_frame"] == "True"


def test_ac3_5_gehi_fallback_for_missing_frozen_tif(tmp_path: Path) -> None:
    """AC-3.5: a dead frozen-tif path (nothing on disk) falls back to the injected
    `download_chip_with_zoom_ladder` runner — success -> `gehi_fallback_ok` + an
    `fsarm_` chip; every zoom rung failing -> `fullstack_source_missing`, no chip.
    """
    dead_root = tmp_path / "dead_frozen"  # never created -> every frozen path is dead
    artifacts_csv = _resolved_artifacts_csv(tmp_path, dead_root, alive=False)
    scan_dir = tmp_path / "scan_states"  # empty -> scan_state_missing (harmless)

    # restrict to a single target (t00000001, modal 2015-01-30) with flank=0 so
    # exactly ONE full-stack frame is in play.
    assignments = tmp_path / "sample_assignments.csv"
    _write_dispute_assignment(assignments, ANCHOR_FS_DISPUTE, ["t00000001"])

    ok_runner = _make_runner(succeed=True)
    root_ok = tmp_path / "out_ok"
    rows_ok = rerender_from_assignments(
        assignments,
        scan_states_dir=scan_dir,
        chipgroups_csv=FS_FIX / "chip_groups_as_anchors.csv",
        root=root_ok,
        fullstack_artifacts_csv=artifacts_csv,
        fullstack_units_csv=FS_FIX / "per_unit_fullstack.csv",
        fullstack_flank=0,
        runner=ok_runner,
    )
    fs_rows_ok = [r for r in rows_ok if r["source"] == "fullstack"]
    assert len(fs_rows_ok) == 1
    assert fs_rows_ok[0]["status"] == "gehi_fallback_ok"
    assert fs_rows_ok[0]["chip_path"]
    assert ok_runner.calls  # GEHI WAS invoked for the dead frozen path
    chip_files = list((rerender_chips_dir(root_ok) / ANCHOR_FS_DISPUTE).glob("fsarm_2015-01-30_v*"))
    assert len(chip_files) == 1

    fail_runner = _make_runner(succeed=False)  # every zoom rung fails
    root_fail = tmp_path / "out_fail"
    rows_fail = rerender_from_assignments(
        assignments,
        scan_states_dir=scan_dir,
        chipgroups_csv=FS_FIX / "chip_groups_as_anchors.csv",
        root=root_fail,
        fullstack_artifacts_csv=artifacts_csv,
        fullstack_units_csv=FS_FIX / "per_unit_fullstack.csv",
        fullstack_flank=0,
        runner=fail_runner,
    )
    fs_rows_fail = [r for r in rows_fail if r["source"] == "fullstack"]
    assert len(fs_rows_fail) == 1
    assert fs_rows_fail[0]["status"] == "fullstack_source_missing"
    assert fs_rows_fail[0]["chip_path"] == ""
    assert not list((rerender_chips_dir(root_fail) / ANCHOR_FS_DISPUTE).glob("fsarm_*"))


def test_ac3_6_all_undated_target_yields_no_window(tmp_path: Path) -> None:
    """AC-3.6: an owned dispute target whose `fpd_reps` are all UNDATED
    (t00000004-shaped) contributes no frames and emits exactly one
    `fullstack_no_window` report row; the batch does not crash.
    """
    artifacts_csv = _resolved_artifacts_csv(tmp_path, tmp_path / "frozen", alive=True)
    scan_dir = tmp_path / "scan_states"  # empty -> scan_state_missing (harmless)

    assignments = tmp_path / "sample_assignments.csv"
    _write_dispute_assignment(assignments, ANCHOR_FS_DISPUTE, ["t00000004"])  # all-UNDATED

    root = tmp_path / "out"
    runner = _make_runner(succeed=True)
    rows = rerender_from_assignments(  # must not crash
        assignments,
        scan_states_dir=scan_dir,
        chipgroups_csv=FS_FIX / "chip_groups_as_anchors.csv",
        root=root,
        fullstack_artifacts_csv=artifacts_csv,
        fullstack_units_csv=FS_FIX / "per_unit_fullstack.csv",
        runner=runner,
    )

    fs_rows = [r for r in rows if r["source"] == "fullstack"]
    assert len(fs_rows) == 1
    assert fs_rows[0]["status"] == "fullstack_no_window"
    assert fs_rows[0]["chip_path"] == ""
    assert "t00000004" in fs_rows[0]["error"]
    assert runner.calls == []  # no window resolved -> nothing to fetch

    assert not list((rerender_chips_dir(root) / ANCHOR_FS_DISPUTE).glob("fsarm_*"))
    sidecar = read_csv_rows(root / "rerender" / "fullstack_frames.csv")
    assert sidecar == []  # no on-disk chip -> no caption row


def _normalize_chip_root(rows: list[dict[str, str]], chips_root: Path) -> list[dict[str, str]]:
    root_str = str(chips_root)
    out = []
    for r in rows:
        r = dict(r)
        if r.get("chip_path"):
            r["chip_path"] = r["chip_path"].replace(root_str, "<CHIPS>")
        out.append(r)
    return out


def test_ac3_8_no_fullstack_flags_matches_pre_change_golden(tmp_path: Path) -> None:
    """AC-3.8: omitting the `--fullstack-*` flags is a byte-identical no-op —
    `frame_report.csv` and the chips dir match the pre-WI-3 tool's golden output on
    the ISSUE-10 fixture (`fixtures/goldset_fullstack/golden_noop/
    frame_report_anchor1.norm.csv`, captured from the same `_default_results()`
    fixture this file's other tests use, before the fullstack extension existed —
    confirmed additive-only against `git show HEAD:scripts/temporal/
    rerender_goldset_windows.py`, ISSUE-11 prep handoff §2/§4 step 4).
    """
    scan_dir = tmp_path / "scan_states"
    save_scan_state(_state(ANCHOR_1, _default_results()), state_path_for(ANCHOR_1, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_assignments(assignments, [ANCHOR_1])
    root = tmp_path / "out"

    runner = _make_runner(succeed=True)
    rows = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=None, runner=runner,
        # fullstack_artifacts_csv / fullstack_units_csv omitted -> byte-identical no-op
    )

    # no sidecar file at all when the flags are omitted, and no fullstack rows sneak in
    assert not (root / "rerender" / "fullstack_frames.csv").exists()
    assert all(r["source"] != "fullstack" for r in rows)

    chips_root = rerender_chips_dir(root)
    got = _normalize_chip_root(read_csv_rows(rerender_report_path(root)), chips_root)
    golden = read_csv_rows(FS_FIX / "golden_noop" / "frame_report_anchor1.norm.csv")
    assert got == golden

    # chips dir: same flat-file set as the golden's non-empty chip_paths (excludes the
    # nested z<zoom>/ raw-download subdir the zoom-ladder helper leaves behind — that
    # subdir is pre-existing GEHI-download plumbing, unrelated to WI-3), no fsarm_ files.
    actual_files = sorted(p.name for p in (chips_root / ANCHOR_1).iterdir() if p.is_file())
    expected_files = sorted({Path(r["chip_path"]).name for r in golden if r["chip_path"]})
    assert actual_files == expected_files
    assert not any(name.startswith("fsarm_") for name in actual_files)
