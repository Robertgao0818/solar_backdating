"""ISSUE-10 WP-C -> WP-A integration test (gold-set tooling).

Regression guard for the blocking integration bug (2026-07-05): WP-C's
``download_chip_with_zoom_ladder`` leaves scan chips nested under ``z<zoom>/`` as
``<anchor>_<YYYYMMDD>_v<ver>.tif``, but WP-A resolves scan frames with a
NON-recursive glob for ``scan_<date>_v<ver>.(png|tif)`` in the flat anchor dir.
Before the fix the strip embedded ZERO jump-window frames. This test wires the
two work packages end-to-end (WP-C with the fake-runner seam simulating the
z19/z20 .tif layout the real GEHI helper produces, then WP-A on WP-C's output
dir) and asserts scan frames now resolve and embed as non-placeholder base64.

Also covers:
* idempotency — a 2nd WP-C pass over already-normalized chips issues zero real
  fetches and the strip still embeds the frames;
* the UNDATABLE-candidate banner + machine-readable builder report for a
  degenerate scan_state (no usable dated rounds).

The GEHI subprocess is injected as a fake ``runner`` that writes a real,
PIL-openable RGB TIFF at the requested nested output path — no network / .NET.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from scripts.temporal.build_goldset_strip_html import (
    FULLSTACK_ROW_LABEL,
    SCALE_CAVEAT_TEXT,
    UNDATABLE_BANNER_TEXT,
    build_group_html,
    collect_undatable_anchors,
    main as build_main,
)
from scripts.temporal.build_phase0_qa_html import PLACEHOLDER_PIXEL
from scripts.temporal.gehi_common import GehiRunResult
from scripts.temporal.goldset_schema import (
    SAMPLE_ASSIGNMENT_FIELDS,
    rerender_chips_dir,
    write_sample_assignments,
)
from scripts.temporal.rerender_goldset_windows import (
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
ANCHOR_2 = "goldset_c0000002"  # degenerate (no usable rounds) in these tests

DATA_URL_RE = re.compile(r"data:image/png;base64,[A-Za-z0-9+/=]+")


# ---------------------------------------------------------------------------
# builders (mirror test_rerender_goldset_windows conventions)


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
        chip_path=f"/deleted/{capture_date}_v{version}.tif",  # retained-but-dead path
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


def _bracket_results() -> list[RoundResult]:
    # ascending; bracket = (2018-03-30 absent, 2019-06-01 present) + flanks
    return [
        _result("2016-01-10", present=False, version=100),
        _result("2017-05-20", present=False, version=200),
        _result("2018-03-30", present=False, version=296),  # latest_absent
        _result("2019-06-01", present=True, version=300),    # earliest_present
        _result("2020-08-15", present=True, version=310),
        _result("2021-11-02", present=True, version=320),
    ]


def _make_image_runner():
    """Fake GEHI runner that writes a REAL RGB TIFF at the nested output path.

    Simulates exactly what the live helper leaves on disk: a z<zoom>/ nested
    ``.tif`` that PIL can open, so WP-C's normalization can transcode it to the
    flat ``scan_<date>_v<ver>.png`` WP-A expects. Records every invocation.
    """
    from PIL import Image

    calls: list[dict[str, Any]] = []

    def runner(cmd_args, *, executable, timeout):
        info: dict[str, Any] = {}
        for i, arg in enumerate(cmd_args):
            if arg == "--zoom":
                info["zoom"] = int(cmd_args[i + 1])
            elif arg == "--output":
                info["out_path"] = Path(cmd_args[i + 1])
        calls.append(info)
        out = info.get("out_path")
        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (24, 24), (40, 120, 200)).save(out, format="TIFF")
            return GehiRunResult(args=tuple(str(a) for a in cmd_args), returncode=0, stdout="", stderr="")
        return GehiRunResult(args=tuple(str(a) for a in cmd_args), returncode=1, stdout="", stderr="no output")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _write_assignments(path: Path, anchor_ids: list[str]) -> None:
    rows = []
    for aid in anchor_ids:
        for annot in ("A", "B"):
            rows.append({
                "anchor_id": aid, "annotator_id": annot, "is_overlap": "True",
                "sample_batch_id": "batch_int", "sampler_seed": "1",
                "scan_state_path": "",
            })
    assert set(("anchor_id", "annotator_id")).issubset(SAMPLE_ASSIGNMENT_FIELDS)
    write_sample_assignments(rows, path)


def _annotator_rows(assignments: Path, annot: str) -> list[dict[str, str]]:
    from scripts.temporal.goldset_schema import read_sample_assignments

    return [r for r in read_sample_assignments(assignments) if r["annotator_id"] == annot]


# ---------------------------------------------------------------------------
# dispute full-stack extension (ISSUE-11 prep, WI-3) — WP-C fsarm -> WP-A second row

DISPUTE_ANCHOR = "goldset_c0000003"
DISPUTE_TARGET = "goldset_c0000003_t00099001"
DISPUTE_SHORT = "t00099001"
_STRIP_HASH_RE = re.compile(
    r"data-anchor-id='goldset_c0000003'[^>]*data-strip-hash='([0-9a-f]+)'"
)


def _write_dispute_assignments(path: Path, anchor_id: str, short_tid: str) -> None:
    rows = []
    for annot in ("A", "B"):
        rows.append({
            "anchor_id": anchor_id, "annotator_id": annot, "is_overlap": "True",
            "is_dispute_forced": "True", "dispute_target_ids": short_tid,
            "sample_batch_id": "batch_disp", "sampler_seed": "1", "scan_state_path": "",
        })
    write_sample_assignments(rows, path)


def _write_chipgroups(path: Path, anchor_id: str, target_id: str) -> None:
    import csv

    fields = [
        "anchor_id", "chip_id", "region_key", "grid_id", "centroid_lon", "centroid_lat",
        "chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max", "n_targets",
        "target_anchor_ids",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "anchor_id": anchor_id, "chip_id": "chip_3", "region_key": "johannesburg",
            "grid_id": "JNB01", "centroid_lon": "28.02000", "centroid_lat": "-26.19000",
            "chip_lon_min": "28.01980", "chip_lat_min": "-26.19018",
            "chip_lon_max": "28.02020", "chip_lat_max": "-26.18982",
            "n_targets": "1", "target_anchor_ids": target_id,
        })


def _write_fullstack_inputs(
    tmp_path: Path, c_anchor: str, target_id: str, *, modal_date: str, dates: list[str]
) -> tuple[Path, Path]:
    """Create frozen tifs + artifacts_fullstack.csv + per_unit_fullstack.csv (clean modal).

    Every ``dates`` entry becomes a real PIL-openable frozen ``.tif`` on disk (so WP-C
    copies, never re-fetches); ``per_unit`` carries 10 identical FPD reps at ``modal_date``.
    """
    import csv

    from PIL import Image

    frozen = tmp_path / "frozen" / c_anchor
    frozen.mkdir(parents=True, exist_ok=True)
    art = tmp_path / "artifacts_fullstack.csv"
    with art.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "chip_id", "capture_date", "version", "path", "actual_zoom", "status"])
        w.writeheader()
        for i, d in enumerate(dates):
            p = frozen / f"{c_anchor}_{d.replace('-', '')}_v{100 + i}.tif"
            Image.new("RGB", (16, 16), (20, 110, 180)).save(p, format="TIFF")
            w.writerow({"chip_id": c_anchor, "capture_date": d, "version": 100 + i,
                        "path": str(p), "actual_zoom": 18, "status": "ok"})
    units = tmp_path / "per_unit_fullstack.csv"
    reps = "|".join(f"FPD|{modal_date}" for _ in range(10))
    with units.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["unit", "chip_id", "fpd_reps"])
        w.writeheader()
        w.writerow({"unit": f"('{c_anchor}', '{target_id}', 'T01')",
                    "chip_id": c_anchor, "fpd_reps": reps})
    return art, units


def test_ac3_7_wpc_fullstack_seam_into_wpa_strip(tmp_path: Path) -> None:
    """AC-3.7: WP-C fsarm copy -> WP-A distinct full-stack row; strip_content_hash differs
    from the same anchor rendered without full-stack chips (provenance lock covers them)."""
    pytest.importorskip("PIL")
    scan_dir = tmp_path / "scan_states"
    # production gave up: degenerate scan_state (empty window).
    save_scan_state(_state(DISPUTE_ANCHOR, [], status="done_ambiguous_no_recent_anchor"),
                    state_path_for(DISPUTE_ANCHOR, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_dispute_assignments(assignments, DISPUTE_ANCHOR, DISPUTE_SHORT)
    chipgroups = tmp_path / "chipgroups.csv"
    _write_chipgroups(chipgroups, DISPUTE_ANCHOR, DISPUTE_TARGET)
    dates = ["2019-01-01", "2020-01-01", "2021-01-01", "2022-06-01", "2023-01-01"]
    art, units = _write_fullstack_inputs(
        tmp_path, DISPUTE_ANCHOR, DISPUTE_TARGET, modal_date="2021-01-01", dates=dates)
    root = tmp_path / "out"

    # WP-C: frozen copy only — the injected runner must NEVER fire (no GEHI network).
    calls: list[int] = []

    def _norunner(cmd_args, *, executable, timeout):
        calls.append(1)
        return GehiRunResult(args=tuple(str(a) for a in cmd_args), returncode=1, stdout="", stderr="x")

    rows = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=chipgroups, root=root,
        coj_chips_dir=None, fullstack_artifacts_csv=art, fullstack_units_csv=units,
        fullstack_flank=1, runner=_norunner,
    )
    assert calls == []
    fs_rows = [r for r in rows if r["source"] == "fullstack"]
    assert fs_rows and all(r["status"] == "fullstack_copied" for r in fs_rows)
    assert not [r for r in rows if r["source"] == "scan_tm"]  # empty production window
    flat_dir = rerender_chips_dir(root) / DISPUTE_ANCHOR
    assert sorted(p.name for p in flat_dir.glob("fsarm_*.png"))

    # WP-A: distinct full-stack row (label + caveat + own container) with FPD caption.
    html_fs = build_group_html(
        _annotator_rows(assignments, "A"),
        scan_states_dir=scan_dir, rerender_dir=root / "rerender", thumbnail_size=48,
    )
    assert FULLSTACK_ROW_LABEL in html_fs
    assert SCALE_CAVEAT_TEXT in html_fs
    assert "class='chip-strip fullstack'" in html_fs
    assert "FPD=2021-01-01" in html_fs
    assert UNDATABLE_BANNER_TEXT not in html_fs  # rescued by the full-stack row
    assert PLACEHOLDER_PIXEL not in html_fs
    assert '"source":"fullstack"' in html_fs
    fs_hash = _STRIP_HASH_RE.search(html_fs).group(1)

    # Same anchor, NO full-stack chips -> no second row + a DIFFERENT strip_content_hash.
    root2 = tmp_path / "out2"
    rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=chipgroups, root=root2,
        coj_chips_dir=None, runner=_make_image_runner(),  # no fullstack flags
    )
    html_no = build_group_html(
        _annotator_rows(assignments, "A"),
        scan_states_dir=scan_dir, rerender_dir=root2 / "rerender", thumbnail_size=48,
    )
    assert "class='chip-strip fullstack'" not in html_no
    no_hash = _STRIP_HASH_RE.search(html_no).group(1)
    assert fs_hash != no_hash


def test_dispute_anchor_no_fullstack_evidence_gets_banner(tmp_path: Path) -> None:
    """A dispute anchor absent from the frozen inventory stays UNDATABLE (banner + report
    with the ``_no_fullstack`` reason)."""
    pytest.importorskip("PIL")
    scan_dir = tmp_path / "scan_states"
    save_scan_state(_state(DISPUTE_ANCHOR, [], status="done_ambiguous_no_recent_anchor"),
                    state_path_for(DISPUTE_ANCHOR, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_dispute_assignments(assignments, DISPUTE_ANCHOR, DISPUTE_SHORT)
    chipgroups = tmp_path / "chipgroups.csv"
    _write_chipgroups(chipgroups, DISPUTE_ANCHOR, DISPUTE_TARGET)
    # frozen inventory covers a DIFFERENT c-anchor -> this dispute recovers nothing.
    art, units = _write_fullstack_inputs(
        tmp_path, "goldset_c9999999", "goldset_c9999999_t00000000",
        modal_date="2021-01-01", dates=["2020-01-01", "2021-01-01", "2022-01-01"])
    root = tmp_path / "out"

    rows = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=chipgroups, root=root,
        coj_chips_dir=None, fullstack_artifacts_csv=art, fullstack_units_csv=units,
        runner=_make_image_runner(),
    )
    assert not [r for r in rows if r["source"] == "fullstack"]  # nothing recovered
    flat_dir = rerender_chips_dir(root) / DISPUTE_ANCHOR
    assert not (list(flat_dir.glob("fsarm_*")) if flat_dir.is_dir() else [])

    rows_a = _annotator_rows(assignments, "A")
    html_text = build_group_html(
        rows_a, scan_states_dir=scan_dir, rerender_dir=root / "rerender", thumbnail_size=48,
    )
    assert UNDATABLE_BANNER_TEXT in html_text  # no frames from EITHER arm
    undatable = collect_undatable_anchors(rows_a, scan_dir, root / "rerender")
    assert [r["anchor_id"] for r in undatable] == [DISPUTE_ANCHOR]
    assert undatable[0]["reason"] == "no_usable_dated_rounds_no_fullstack"


# ---------------------------------------------------------------------------
# THE integration regression: WP-C nested tifs -> WP-A resolves + embeds


def test_wpc_output_resolves_and_embeds_in_wpa_strip(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    scan_dir = tmp_path / "scan_states"
    save_scan_state(_state(ANCHOR_1, _bracket_results()), state_path_for(ANCHOR_1, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_assignments(assignments, [ANCHOR_1])
    root = tmp_path / "out"

    runner = _make_image_runner()
    rows = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=None, runner=runner,
    )

    # WP-C must have normalized every recovered scan frame into the flat dir under
    # the builder's documented convention scan_<date>_v<ver>.png.
    window = resolve_window_frames(_state(ANCHOR_1, _bracket_results()))
    flat_dir = rerender_chips_dir(root) / ANCHOR_1
    for f in window:
        flat = flat_dir / f"scan_{f.capture_date}_v{f.version}.png"
        assert flat.exists() and flat.stat().st_size > 0, f"missing flat chip {flat.name}"
    # frame_report chip_path now points at the flat (browser-embeddable) chip.
    scan_rows = [r for r in rows if r["source"] == "scan_tm"]
    assert scan_rows and all(str(r["chip_path"]).endswith(".png") for r in scan_rows)

    # WP-A resolves those flat chips and embeds them as non-placeholder base64.
    html_text = build_group_html(
        _annotator_rows(assignments, "A"),
        scan_states_dir=scan_dir,
        rerender_dir=root / "rerender",
        thumbnail_size=48,
    )
    urls = DATA_URL_RE.findall(html_text)
    # one embedded chip per resolved window frame, all real (non-placeholder).
    assert len(urls) >= len(window)
    assert PLACEHOLDER_PIXEL not in html_text, "no scan frame should drop to a placeholder"
    assert any(len(u) > len(PLACEHOLDER_PIXEL) for u in urls), "expected real base64 payloads"
    # the jump-window roles actually rendered (proves scan frames, not just refs).
    assert "latest_absent" in html_text and "earliest_present" in html_text


def test_second_wpc_pass_is_idempotent_and_still_embeds(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    scan_dir = tmp_path / "scan_states"
    save_scan_state(_state(ANCHOR_1, _bracket_results()), state_path_for(ANCHOR_1, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_assignments(assignments, [ANCHOR_1])
    root = tmp_path / "out"

    runner1 = _make_image_runner()
    rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=None, runner=runner1,
    )
    assert runner1.calls, "first pass should fetch"

    # 2nd pass over already-downloaded z-nested tifs + already-normalized flat pngs.
    runner2 = _make_image_runner()
    rows2 = rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=None, runner=runner2,
    )
    assert runner2.calls == [], "skip-if-exists => zero real fetches on the 2nd pass"
    assert all(r["status"] == "skipped_existing" for r in rows2 if r["source"] == "scan_tm")

    # strip still embeds the frames after the idempotent re-run.
    html_text = build_group_html(
        _annotator_rows(assignments, "A"),
        scan_states_dir=scan_dir,
        rerender_dir=root / "rerender",
        thumbnail_size=48,
    )
    assert PLACEHOLDER_PIXEL not in html_text
    assert len(DATA_URL_RE.findall(html_text)) >= len(resolve_window_frames(_state(ANCHOR_1, _bracket_results())))


def test_degenerate_anchor_gets_banner_and_builder_report(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    scan_dir = tmp_path / "scan_states"
    # ANCHOR_1 is normal; ANCHOR_2 is degenerate (no usable dated rounds).
    save_scan_state(_state(ANCHOR_1, _bracket_results()), state_path_for(ANCHOR_1, scan_dir))
    save_scan_state(_state(ANCHOR_2, [], status="done_ambiguous_no_recent_anchor"),
                    state_path_for(ANCHOR_2, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_assignments(assignments, [ANCHOR_1, ANCHOR_2])
    root = tmp_path / "out"

    rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=None, runner=_make_image_runner(),
    )

    rows_a = _annotator_rows(assignments, "A")
    html_text = build_group_html(
        rows_a, scan_states_dir=scan_dir, rerender_dir=root / "rerender", thumbnail_size=48,
    )
    # degenerate anchor shows the explicit banner; normal anchor does not force it.
    assert UNDATABLE_BANNER_TEXT in html_text
    assert f"data-anchor-id='{ANCHOR_2}'" in html_text
    assert "data-undatable='true'" in html_text
    assert "data-undatable='false'" in html_text  # ANCHOR_1 remains datable

    # machine-readable builder report lists exactly the degenerate anchor.
    undatable = collect_undatable_anchors(rows_a, scan_dir)
    assert [r["anchor_id"] for r in undatable] == [ANCHOR_2]
    assert undatable[0]["reason"] == "no_usable_dated_rounds"


def test_cli_main_writes_builder_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PIL")
    scan_dir = tmp_path / "scan_states"
    save_scan_state(_state(ANCHOR_1, _bracket_results()), state_path_for(ANCHOR_1, scan_dir))
    save_scan_state(_state(ANCHOR_2, [], status="done_ambiguous_no_recent_anchor"),
                    state_path_for(ANCHOR_2, scan_dir))
    assignments = tmp_path / "sample_assignments.csv"
    _write_assignments(assignments, [ANCHOR_1, ANCHOR_2])
    root = tmp_path / "out"
    rerender_from_assignments(
        assignments, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS_CSV,
        root=root, coj_chips_dir=None, runner=_make_image_runner(),
    )
    out_dir = tmp_path / "strips"
    monkeypatch.setattr("sys.argv", [
        "build_goldset_strip_html.py",
        "--assignments", str(assignments),
        "--scan-states-dir", str(scan_dir),
        "--rerender-dir", str(root / "rerender"),
        "--output-dir", str(out_dir),
        "--thumbnail-size", "48",
    ])
    build_main()

    report = out_dir / "builder_report.csv"
    assert report.exists()
    import csv

    with report.open(encoding="utf-8") as fh:
        report_rows = list(csv.DictReader(fh))
    assert [r["anchor_id"] for r in report_rows] == [ANCHOR_2]
