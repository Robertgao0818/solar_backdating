"""Tests for the scan-state achieved-zoom re-summarizer (ISSUE-18).

Synthetic scan-state JSONs are written directly (raw dicts) so the tests can
exercise both the canonical `load_scan_state` happy path and the raw-json
fallback for an alien `spec_version` without depending on the vocab registry.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.temporal.scan_state import SPEC_VERSION
from scripts.temporal.summarize_scan_zoom import (
    ZoomHistogram,
    report_dict,
    summarize_dir,
    zoom_label,
)


def _write_state(
    scan_states_dir: Path,
    anchor_id: str,
    results: list[tuple[int | None, str]],
    *,
    spec_version: str = SPEC_VERSION,
) -> Path:
    """Write a minimal scan-state JSON carrying `results` as (actual_zoom, decision_source).

    The results are split across two rounds to prove the aggregator walks every
    round, not just the first.
    """
    scan_states_dir.mkdir(parents=True, exist_ok=True)

    def _mk_result(idx: int, actual_zoom: int | None, decision_source: str) -> dict:
        return {
            "chip_index": idx,
            "capture_date": "2020-01-01",
            "version": 100 + idx,
            "pv_present": True,
            "confidence": 0.9,
            "quality_flag": "usable",
            "decision_source": decision_source,
            "evidence": "",
            "notes": "",
            "chip_path": "",
            "actual_zoom": actual_zoom,
        }

    mid = len(results) // 2
    rounds = [
        {
            "round_id": 1,
            "round_type": "initial",
            "window_start_date": None,
            "window_end_date": None,
            "picks": [],
            "results": [_mk_result(i, z, ds) for i, (z, ds) in enumerate(results[:mid])],
            "completed": True,
            "failed": False,
            "notes": "",
        },
        {
            "round_id": 2,
            "round_type": "bisection",
            "window_start_date": None,
            "window_end_date": None,
            "picks": [],
            "results": [
                _mk_result(mid + i, z, ds) for i, (z, ds) in enumerate(results[mid:])
            ],
            "completed": True,
            "failed": False,
            "notes": "",
        },
    ]
    payload = {
        "anchor_id": anchor_id,
        "region_key": "johannesburg",
        "grid_id": "G0922",
        "status": "done_appears",
        "rounds": rounds,
        "next_action": None,
        "started_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
        "spec_version": spec_version,
        "notes": "",
    }
    path = scan_states_dir / f"{anchor_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_zoom_label() -> None:
    assert zoom_label(20) == "z20"
    assert zoom_label(None) == "unknown"
    assert zoom_label("19") == "z19"
    assert zoom_label("garbage") == "unknown"


def test_exact_histogram_with_null_zoom(tmp_path: Path) -> None:
    d = tmp_path / "states"
    # anchor A: z20 x2, z19 x1
    _write_state(d, "a01", [(20, "gemini_batch"), (20, "gemini_batch"), (19, "gemini_batch")])
    # anchor B: z19 x1, None x1
    _write_state(d, "a02", [(19, "gemini_per_image"), (None, "gemini_failed")])

    hist = summarize_dir(d)
    assert hist.n_files == 2
    assert hist.n_results == 5
    assert hist.n_parse_fallback == 0
    assert dict(hist.zoom_counts) == {"z20": 2, "z19": 2, "unknown": 1}
    assert dict(hist.decision_source_counts) == {
        "gemini_batch": 3,
        "gemini_per_image": 1,
        "gemini_failed": 1,
    }
    rep = report_dict(hist)
    # z20 = 2/5 = 40%, z19 = 40%, unknown = 20%
    assert rep["zoom_pct"]["z20"] == 40.0
    assert rep["zoom_pct"]["unknown"] == 20.0
    # sorted descending by zoom, unknown last
    assert list(rep["zoom_counts"].keys()) == ["z20", "z19", "unknown"]


def test_alien_spec_version_counted_via_fallback(tmp_path: Path) -> None:
    d = tmp_path / "states"
    _write_state(d, "good", [(20, "gemini_batch")], spec_version=SPEC_VERSION)
    _write_state(d, "future", [(19, "gemini_batch"), (18, "gemini_batch")], spec_version="phase0_v999")

    hist = summarize_dir(d)
    assert hist.n_files == 2
    assert hist.n_parse_fallback == 1  # only the alien-spec file needed fallback
    assert hist.n_parse_error == 0
    # results from BOTH files counted despite the spec bump
    assert dict(hist.zoom_counts) == {"z20": 1, "z19": 1, "z18": 1}


def test_combined_multi_dir_totals(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    _write_state(d1, "x1", [(20, "gemini_batch"), (20, "gemini_batch")])
    _write_state(d2, "y1", [(19, "gemini_batch"), (18, "gemini_batch")])

    h1 = summarize_dir(d1)
    h2 = summarize_dir(d2)
    combined = ZoomHistogram(label="COMBINED")
    combined.merge(h1)
    combined.merge(h2)

    assert combined.n_files == 2
    assert combined.n_results == 4
    assert dict(combined.zoom_counts) == {"z20": 2, "z19": 1, "z18": 1}


def test_corrupt_json_counted_as_parse_error(tmp_path: Path) -> None:
    d = tmp_path / "states"
    _write_state(d, "ok", [(20, "gemini_batch")])
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.json").write_text("{ this is not json", encoding="utf-8")

    hist = summarize_dir(d)
    assert hist.n_files == 2
    assert hist.n_parse_error == 1
    assert dict(hist.zoom_counts) == {"z20": 1}
