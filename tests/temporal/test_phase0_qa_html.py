"""Phase-0 QA HTML renderer tests (pure display layer)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.temporal.build_phase0_qa_html import (
    PLACEHOLDER_PIXEL,
    load_intervals,
    render_anchor,
    render_html,
    render_round,
    thumbnail_data_url,
)
from scripts.temporal.scan_state import (
    Pick,
    Round,
    RoundResult,
    ScanState,
    save_scan_state,
    state_path_for,
)


def _result(capture_date: str, *, present: bool | None, quality: str = "usable", chip_path: str = "") -> RoundResult:
    return RoundResult(
        chip_index=1,
        capture_date=capture_date,
        version=0,
        pv_present=present,
        confidence=0.9 if present is not None else None,
        quality_flag=quality,
        decision_source="gemini_batch" if present is not None else "gemini_failed",
        evidence=f"stub evidence for {capture_date}",
        actual_zoom=20,
        chip_path=chip_path,
    )


def _state_with(status: str, results: list[RoundResult], *, notes: str = "") -> ScanState:
    state = ScanState(anchor_id="a000005", region_key="johannesburg", grid_id="G0922")
    state.status = status
    state.notes = notes
    state.rounds = [
        Round(
            round_id=1,
            round_type="initial",
            window_start_date=results[0].capture_date if results else None,
            window_end_date=results[-1].capture_date if results else None,
            picks=[Pick(chip_index=i + 1, capture_date=r.capture_date, version=0, requested_zoom=20) for i, r in enumerate(results)],
            results=results,
            completed=True,
        )
    ]
    return state


def test_thumbnail_data_url_returns_placeholder_when_path_missing(tmp_path: Path) -> None:
    out = thumbnail_data_url(None, 200)
    assert out == PLACEHOLDER_PIXEL


def test_thumbnail_data_url_returns_placeholder_for_zero_byte(tmp_path: Path) -> None:
    p = tmp_path / "empty.png"
    p.write_bytes(b"")
    out = thumbnail_data_url(p, 200)
    assert out == PLACEHOLDER_PIXEL


def test_thumbnail_data_url_encodes_real_png(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    p = tmp_path / "chip.png"
    Image.new("RGB", (300, 300), (10, 200, 30)).save(p, format="PNG")
    out = thumbnail_data_url(p, 64)
    assert out.startswith("data:image/png;base64,")
    assert len(out) > len(PLACEHOLDER_PIXEL)


def test_render_anchor_includes_anchor_id_and_status() -> None:
    state = _state_with("done_appears", [_result("2020-04-15", present=False), _result("2020-08-15", present=True)])
    interval = {
        "anchor_id": "a000005",
        "status": "done_appears",
        "install_interval_start": "2020-04-15",
        "install_interval_end": "2020-08-15",
        "install_mid_estimate": "2020-06-15",
        "confidence": "high",
        "notes": "",
    }
    h = render_anchor(state, interval, thumbnail_size=64)
    assert "a000005" in h
    assert "done_appears" in h
    assert "interval=[2020-04-15, 2020-08-15]" in h
    assert "mid=2020-06-15" in h
    assert "confidence=high" in h


def test_render_anchor_marks_chip_class_by_pv_present() -> None:
    state = _state_with(
        "done_appears",
        [
            _result("2020-04-15", present=False),
            _result("2020-08-15", present=True),
            _result("2020-10-15", present=None, quality="unusable"),
        ],
    )
    h = render_anchor(state, {}, thumbnail_size=64)
    assert "chip absent" in h
    assert "chip present" in h
    assert "chip unusable" in h


def test_render_anchor_renders_status_class_for_styling() -> None:
    state = _state_with("done_ambiguous_nonmonotonic", [_result("2020-06-15", present=True)], notes="present->absent flip")
    interval = {"confidence": "low", "notes": "present->absent flip"}
    h = render_anchor(state, interval, thumbnail_size=64)
    assert "status-done_ambiguous_nonmonotonic" in h
    assert "conf-low" in h
    assert "present-&gt;absent flip" in h or "present-&#x27;absent flip" in h or "flip" in h


def test_render_anchor_does_not_invent_interval_when_missing() -> None:
    state = _state_with("done_ambiguous_gemini_failed", [_result("2020-06-15", present=None, quality="unusable")])
    h = render_anchor(state, {}, thumbnail_size=64)
    assert "no interval row available" in h
    assert "interval=[" not in h


def test_render_round_orders_chips_chronologically() -> None:
    rnd = Round(
        round_id=1,
        round_type="initial",
        window_start_date="2018-06-15",
        window_end_date="2024-06-15",
        results=[
            _result("2024-06-15", present=True),
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=False),
        ],
    )
    h = render_round(0, rnd, thumbnail_size=64)
    pos_2018 = h.find("<div class='cap-date'>2018-06-15</div>")
    pos_2020 = h.find("<div class='cap-date'>2020-06-15</div>")
    pos_2024 = h.find("<div class='cap-date'>2024-06-15</div>")
    assert pos_2018 != -1 and pos_2020 != -1 and pos_2024 != -1
    assert pos_2018 < pos_2020 < pos_2024


def test_render_html_summary_counts_by_status(tmp_path: Path) -> None:
    s1 = _state_with("done_appears", [_result("2020-04-15", present=False), _result("2020-08-15", present=True)])
    s1.anchor_id = "a000001"
    s2 = _state_with("done_installed_during_census", [_result("2024-03-01", present=False)])
    s2.anchor_id = "a000002"
    s3 = _state_with("done_appears", [_result("2018-06-15", present=False), _result("2024-06-15", present=True)])
    s3.anchor_id = "a000003"
    h = render_html([(s1, {}, Path("/x")), (s2, {}, Path("/y")), (s3, {}, Path("/z"))], thumbnail_size=64)
    assert "3 anchors" in h
    assert "done_appears=2" in h
    assert "done_installed_during_census=1" in h


def test_load_intervals_returns_empty_when_csv_missing(tmp_path: Path) -> None:
    assert load_intervals(tmp_path / "missing.csv") == {}


def test_load_intervals_keys_by_anchor_id(tmp_path: Path) -> None:
    csv_path = tmp_path / "intervals.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["anchor_id", "status", "confidence"])
        w.writeheader()
        w.writerow({"anchor_id": "a", "status": "done_appears", "confidence": "high"})
        w.writerow({"anchor_id": "b", "status": "done_appears", "confidence": "medium"})
    out = load_intervals(csv_path)
    assert set(out) == {"a", "b"}
    assert out["a"]["confidence"] == "high"


def test_end_to_end_renders_valid_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive main() with synthetic scan_states and intervals; verify HTML is non-trivial."""
    states_dir = tmp_path / "scan_states"
    states_dir.mkdir()
    intervals_csv = tmp_path / "intervals.csv"
    out_html = tmp_path / "phase0_qa.html"

    s1 = _state_with("done_appears", [_result("2020-04-15", present=False), _result("2020-08-15", present=True)])
    s1.anchor_id = "a000001"
    save_scan_state(s1, state_path_for(s1.anchor_id, states_dir))
    s2 = _state_with("done_installed_during_census", [_result("2024-03-01", present=False)])
    s2.anchor_id = "a000002"
    save_scan_state(s2, state_path_for(s2.anchor_id, states_dir))

    with intervals_csv.open("w", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "anchor_id",
                "status",
                "install_interval_start",
                "install_interval_end",
                "install_mid_estimate",
                "confidence",
                "notes",
            ],
        )
        w.writeheader()
        w.writerow(
            {
                "anchor_id": "a000001",
                "status": "done_appears",
                "install_interval_start": "2020-04-15",
                "install_interval_end": "2020-08-15",
                "install_mid_estimate": "2020-06-15",
                "confidence": "high",
                "notes": "",
            }
        )
        w.writerow(
            {
                "anchor_id": "a000002",
                "status": "done_installed_during_census",
                "install_interval_start": "2024-03-01",
                "install_interval_end": "2024-06-30",
                "install_mid_estimate": "2024-06-30",
                "confidence": "high",
                "notes": "",
            }
        )

    monkeypatch.setattr(
        "sys.argv",
        [
            "build_phase0_qa_html.py",
            "--scan-states-dir", str(states_dir),
            "--intervals-csv", str(intervals_csv),
            "--output", str(out_html),
            "--thumbnail-size", "64",
        ],
    )
    from scripts.temporal.build_phase0_qa_html import main as build_main

    build_main()

    text = out_html.read_text(encoding="utf-8")
    assert "<!doctype html>" in text
    assert "a000001" in text
    assert "a000002" in text
    assert "done_appears" in text
    assert "done_installed_during_census" in text
    assert "interval=[2020-04-15, 2020-08-15]" in text
    assert "interval=[2024-03-01, 2024-06-30]" in text
    assert "data:image/png;base64," in text
