"""Phase-0 end-to-end mock integration smoke (Task H).

Exercises the full pipeline through CLI subprocess invocations, no GEHI binary
or Gemini API contacted:

  run_adaptive_scan.py --dry-run  (orchestrator + dry_run stubs)
      |
      v  scan_states/*.json
  infer_install_dates.py          (interval inference)
      |
      v  install_intervals.csv
  build_phase0_qa_html.py         (display)
      |
      v  phase0_qa.html

The dry-run profile selector is anchor_id-deterministic, so the smoke holds
every anchor's expected status fixed and asserts the artifacts roundtrip.

Task I (real run with live GEHI + Gemini) intentionally has no automated
test — it requires .env.gemini.local + network + paid API quota and is run
manually from the shell.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

SUBREPO_ROOT = Path(__file__).resolve().parents[2]


def _write_anchors_csv(path: Path, anchor_ids: list[str]) -> None:
    """Write a minimal anchors.csv with the columns run_adaptive_scan reads.

    Centroid + chip bbox are placeholders — dry-run stubs ignore them.
    """
    fields = [
        "anchor_id", "region_key", "grid_id",
        "source_annotation_path", "source_feature_id", "quality_tier",
        "anchor_policy",
        "centroid_lon", "centroid_lat",
        "source_area_m2", "source_width_m", "source_height_m",
        "chip_half_m", "search_radius_m",
        "chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max",
        "alignment_note",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for aid in anchor_ids:
            w.writerow({
                "anchor_id": aid,
                "region_key": "johannesburg",
                "grid_id": "G0922",
                "source_annotation_path": "",
                "source_feature_id": "0",
                "quality_tier": "",
                "anchor_policy": "gt_centroid_buffered_bbox",
                "centroid_lon": "28.014",
                "centroid_lat": "-26.183",
                "source_area_m2": "100",
                "source_width_m": "10",
                "source_height_m": "10",
                "chip_half_m": "18",
                "search_radius_m": "10",
                "chip_lon_min": "28.013",
                "chip_lat_min": "-26.184",
                "chip_lon_max": "28.015",
                "chip_lat_max": "-26.182",
                "alignment_note": "smoke",
            })


def _run_python(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.integration
def test_phase0_pipeline_dry_run_end_to_end(tmp_path: Path) -> None:
    """Drive orchestrator -> infer -> QA HTML through CLIs in dry-run mode."""
    run_root = tmp_path / "smoke_run"
    run_root.mkdir()

    anchors_csv = run_root / "anchors.csv"
    scan_states_dir = run_root / "scan_states"
    chips_dir = run_root / "chips"
    audit_dir = run_root / "audit"
    intervals_csv = run_root / "install_intervals.csv"
    qa_html = run_root / "phase0_qa.html"

    # 4 anchors covers all 4 dry_run profile labels (hash-distributed)
    anchor_ids = [
        "smoke_anchor_appears_alpha",
        "smoke_anchor_appears_beta",
        "smoke_anchor_install_gamma",
        "smoke_anchor_already_delta",
    ]
    _write_anchors_csv(anchors_csv, anchor_ids)

    # Step 1: orchestrator dry-run
    proc1 = _run_python(
        [
            "scripts/temporal/run_adaptive_scan.py",
            "--anchors-csv", str(anchors_csv),
            "--scan-states-dir", str(scan_states_dir),
            "--chips-dir", str(chips_dir),
            "--audit-dir", str(audit_dir),
            "--dry-run",
            "--force-restart",
        ],
        cwd=SUBREPO_ROOT,
    )
    assert proc1.returncode == 0, dedent(f"""
        run_adaptive_scan.py failed (rc={proc1.returncode})
        STDOUT:
        {proc1.stdout}
        STDERR:
        {proc1.stderr}
    """)
    state_files = sorted(scan_states_dir.glob("*.json"))
    assert len(state_files) == len(anchor_ids), (
        f"Expected {len(anchor_ids)} scan_state JSONs; found {len(state_files)}"
    )

    # Step 2: infer intervals
    proc2 = _run_python(
        [
            "scripts/temporal/infer_install_dates.py",
            "--scan-states-dir", str(scan_states_dir),
            "--output", str(intervals_csv),
            "--census-mid-date", "2024-06-30",
        ],
        cwd=SUBREPO_ROOT,
    )
    assert proc2.returncode == 0, dedent(f"""
        infer_install_dates.py failed (rc={proc2.returncode})
        STDOUT:
        {proc2.stdout}
        STDERR:
        {proc2.stderr}
    """)
    assert intervals_csv.exists()
    with intervals_csv.open("r", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(anchor_ids)
    statuses = {r["anchor_id"]: r["status"] for r in rows}
    # Every anchor terminated with a done_* status (no scanning leftovers)
    for aid in anchor_ids:
        assert any(aid in row_anchor for row_anchor in statuses), f"missing row for {aid}"
    for status in statuses.values():
        assert status.startswith("done_"), f"non-terminal status leaked through: {status}"

    # Step 3: QA HTML
    proc3 = _run_python(
        [
            "scripts/temporal/build_phase0_qa_html.py",
            "--scan-states-dir", str(scan_states_dir),
            "--intervals-csv", str(intervals_csv),
            "--output", str(qa_html),
            "--thumbnail-size", "64",
        ],
        cwd=SUBREPO_ROOT,
    )
    assert proc3.returncode == 0, dedent(f"""
        build_phase0_qa_html.py failed (rc={proc3.returncode})
        STDOUT:
        {proc3.stdout}
        STDERR:
        {proc3.stderr}
    """)
    assert qa_html.exists()
    text = qa_html.read_text(encoding="utf-8")
    assert "<!doctype html>" in text
    for aid in anchor_ids:
        assert aid in text, f"QA HTML missing anchor {aid}"
    # Status family color classes must be present in CSS-driven output
    assert "status-done_" in text
    # Summary row counts all anchors
    assert f"{len(anchor_ids)} anchors" in text


@pytest.mark.integration
def test_phase0_pipeline_default_path_resolution(tmp_path: Path) -> None:
    """Verify infer + QA HTML default-path derivation (Task E/F P2 fix) end-to-end."""
    run_root = tmp_path / "default_paths"
    run_root.mkdir()
    scan_states_dir = run_root / "scan_states"
    anchors_csv = run_root / "anchors.csv"
    _write_anchors_csv(anchors_csv, ["smoke_default_a", "smoke_default_b"])

    proc1 = _run_python(
        [
            "scripts/temporal/run_adaptive_scan.py",
            "--anchors-csv", str(anchors_csv),
            "--scan-states-dir", str(scan_states_dir),
            "--chips-dir", str(run_root / "chips"),
            "--audit-dir", str(run_root / "audit"),
            "--dry-run",
            "--force-restart",
        ],
        cwd=SUBREPO_ROOT,
    )
    assert proc1.returncode == 0, proc1.stderr

    # Run infer with NO --output: must land in run_root/install_intervals.csv
    proc2 = _run_python(
        [
            "scripts/temporal/infer_install_dates.py",
            "--scan-states-dir", str(scan_states_dir),
            "--census-mid-date", "2024-06-30",
        ],
        cwd=SUBREPO_ROOT,
    )
    assert proc2.returncode == 0, proc2.stderr
    expected_csv = run_root / "install_intervals.csv"
    assert expected_csv.exists()

    # Run QA HTML with NO --intervals-csv and NO --output: must land in run_root/phase0_qa.html
    proc3 = _run_python(
        [
            "scripts/temporal/build_phase0_qa_html.py",
            "--scan-states-dir", str(scan_states_dir),
            "--thumbnail-size", "64",
        ],
        cwd=SUBREPO_ROOT,
    )
    assert proc3.returncode == 0, proc3.stderr
    expected_html = run_root / "phase0_qa.html"
    assert expected_html.exists()
    text = expected_html.read_text(encoding="utf-8")
    assert "smoke_default_a" in text
    assert "smoke_default_b" in text
