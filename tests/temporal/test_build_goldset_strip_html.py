"""ISSUE-10 WP-A — gold-set strip UI builder + verdict manifest loader tests.

Acceptance criteria covered (docs/replan_v2/ISSUE-10-design-2026-07-05.md):
  AC1 (self-contained offline HTML): golden structural asserts — every chip is a
       base64 data: URI, zero external URLs, verdict controls + SHIFT bracket +
       import/export controls present.
  AC2 (verdicts round-trip UI -> manifest -> records): construct a UI-export JSON
       as the JS emits, load_verdict_manifest_export -> write/read_verdict_manifest,
       assert fields intact (verdict, corrected bracket on SHIFT, annotator,
       adjudication_seconds, frames, strip_content_hash, dispute_target_ids).
  AC (timing capture): the loaded record carries adjudication_seconds/opened/
       submitted stamps.
  AC (dropped frame): a missing re-rendered chip becomes a PLACEHOLDER_PIXEL slot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.temporal.build_goldset_strip_html import (
    SHIFT_MISSING_BRACKET_CONFIRM_TEXT,
    build_group_html,
    load_verdict_manifest_export,
    main as build_main,
    select_scan_frames,
)
# PLACEHOLDER_PIXEL lives in build_phase0_qa_html.
from scripts.temporal.build_phase0_qa_html import PLACEHOLDER_PIXEL
from scripts.temporal.goldset_schema import (
    VERDICTS,
    VerdictRecord,
    read_sample_assignments,
    read_verdict_manifest,
    write_verdict_manifest,
)
from scripts.temporal.scan_state import load_scan_state

FIXTURES = Path(__file__).parent / "fixtures" / "goldset_strip"


# ---------------------------------------------------------------------------
# Frame selection
# ---------------------------------------------------------------------------


def test_select_scan_frames_orders_flanks_around_bounds() -> None:
    state = load_scan_state(FIXTURES / "scan_states" / "c0000542.json")
    roles = [role for role, _r in select_scan_frames(state)]
    assert roles == ["flank_before", "latest_absent", "earliest_present", "flank_after"]


def test_select_scan_frames_degrades_when_no_flanks() -> None:
    state = load_scan_state(FIXTURES / "scan_states" / "c0001985.json")
    roles = [role for role, _r in select_scan_frames(state)]
    assert roles == ["latest_absent", "earliest_present"]


# ---------------------------------------------------------------------------
# AC1 — builder golden / structural asserts (self-contained offline HTML)
# ---------------------------------------------------------------------------


def _build_annotator_a_page() -> str:
    rows = [r for r in read_sample_assignments(FIXTURES / "sample_assignments.csv")
            if r["annotator_id"] == "A"]
    return build_group_html(
        rows,
        scan_states_dir=FIXTURES / "scan_states",
        rerender_dir=FIXTURES / "rerender",
        thumbnail_size=48,
    )


def test_builder_emits_committed_structural_substrings() -> None:
    pytest.importorskip("PIL")
    html_text = _build_annotator_a_page()
    expected = (FIXTURES / "expected_strip_substrings.txt").read_text(encoding="utf-8").splitlines()
    for sub in expected:
        if sub:
            assert sub in html_text, f"missing golden substring: {sub!r}"


def test_builder_embeds_only_base64_no_external_urls() -> None:
    pytest.importorskip("PIL")
    html_text = _build_annotator_a_page()
    assert "data:image/png;base64," in html_text
    # No external image/script/style hosts (self-contained AC).
    assert "http://" not in html_text
    assert "https://" not in html_text
    assert "src='http" not in html_text


def test_builder_dropped_frame_becomes_placeholder() -> None:
    pytest.importorskip("PIL")
    rows = [r for r in read_sample_assignments(FIXTURES / "sample_assignments.csv")
            if r["anchor_id"] == "c0001985"]
    html_text = build_group_html(
        rows,
        scan_states_dir=FIXTURES / "scan_states",
        rerender_dir=FIXTURES / "rerender",
        thumbnail_size=48,
    )
    # earliest_present chip was never rendered -> placeholder + DROPPED caption.
    assert PLACEHOLDER_PIXEL in html_text
    assert "DROPPED" in html_text


def test_builder_is_timestamp_free_deterministic() -> None:
    pytest.importorskip("PIL")
    first = _build_annotator_a_page()
    second = _build_annotator_a_page()
    assert first == second, "builder must be deterministic (no wall-clock in static bytes)"
    # A UTC 'Z' timestamp of the current second must not appear in static bytes.
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:")
    assert stamp not in first


def test_cli_writes_per_annotator_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PIL")
    out_dir = tmp_path / "strips"
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_goldset_strip_html.py",
            "--assignments", str(FIXTURES / "sample_assignments.csv"),
            "--scan-states-dir", str(FIXTURES / "scan_states"),
            "--rerender-dir", str(FIXTURES / "rerender"),
            "--output-dir", str(out_dir),
            "--thumbnail-size", "48",
        ],
    )
    build_main()
    a = out_dir / "verdicts_batch01_A.html"
    b = out_dir / "verdicts_batch01_B.html"
    assert a.exists() and b.exists()
    assert "data-anchor-id='c0000542'" in a.read_text(encoding="utf-8")
    # B is the overlap annotator: gets the shared dispute anchor only.
    b_text = b.read_text(encoding="utf-8")
    assert "data-anchor-id='c0000542'" in b_text
    assert "data-anchor-id='c0001985'" not in b_text


def test_builder_context_carries_strip_hash_and_frames() -> None:
    pytest.importorskip("PIL")
    html_text = _build_annotator_a_page()
    # The embedded JS CONTEXT must include a strip_content_hash and frames per anchor
    # so the export carries the provenance lock.
    assert '"strip_content_hash":"' in html_text
    assert '"frames":[' in html_text
    assert '"dispute_target_ids":["t00034036"]' in html_text


def test_export_js_guards_shift_with_empty_corrected_bracket() -> None:
    """ISSUE-11 bug (2026-07-05): a real export shipped verdict=SHIFT with both
    corrected dates blank. The export handler must collect SHIFT verdicts missing
    either corrected date and gate the download behind a confirm() the annotator
    can cancel to go back and fix the bracket."""
    pytest.importorskip("PIL")
    html_text = _build_annotator_a_page()
    assert SHIFT_MISSING_BRACKET_CONFIRM_TEXT in html_text
    # The guard must actually be wired to a confirm() that can abort the export
    # (not just descriptive text), and must run before the Blob/download happens.
    assert "confirm(msg)" in html_text
    confirm_idx = html_text.index("confirm(msg)")
    blob_idx = html_text.index("new Blob([JSON.stringify(manifest")
    assert confirm_idx < blob_idx, "the SHIFT-bracket guard must run before the export Blob is built"


# ---------------------------------------------------------------------------
# AC2 — verdicts round-trip (UI export -> loader -> manifest -> records)
# ---------------------------------------------------------------------------


def test_load_verdict_manifest_export_skips_unadjudicated() -> None:
    recs = load_verdict_manifest_export(FIXTURES / "ui_export.json")
    # 3 entries in the fixture; one has verdict="" and must be dropped.
    ids = [r.anchor_id for r in recs]
    assert ids == ["c0000542", "c0001985"]
    assert all(r.verdict in VERDICTS for r in recs)


def test_verdict_export_roundtrip_preserves_fields(tmp_path: Path) -> None:
    recs = load_verdict_manifest_export(FIXTURES / "ui_export.json")
    out = tmp_path / "verdicts_batch01_A.jsonl"
    write_verdict_manifest(recs, out)
    back = read_verdict_manifest(out)

    assert len(back) == len(recs) == 2
    by_id = {r.anchor_id: r for r in back}

    shift = by_id["c0000542"]
    assert shift.verdict == "SHIFT"
    assert shift.corrected_interval_start == "2019-09-01"
    assert shift.corrected_interval_end == "2020-03-01"
    assert shift.annotator_id == "A"
    assert shift.is_overlap is True
    assert shift.is_dispute_forced is True
    assert shift.dispute_target_ids == ["t00034036"]
    assert shift.adjudication_seconds == 12.5
    assert shift.opened_ts_utc == "2026-07-05T09:59:00Z"
    assert shift.submitted_ts_utc == "2026-07-05T09:59:12Z"
    assert shift.strip_content_hash == "PLACEHOLDER_HASH"
    assert [(f.source, f.role, f.capture_date, f.version, f.chip_sha256) for f in shift.frames] == [
        ("scan_tm", "latest_absent", "2019-06-01", 101, "deadbeef"),
        ("coj_2019", "reference", "", None, "cafef00d"),
    ]

    confirm = by_id["c0001985"]
    assert confirm.verdict == "CONFIRM"
    assert confirm.corrected_interval_start == ""  # non-SHIFT keeps empty
    assert confirm.is_dispute_forced is False
    assert confirm.dispute_target_ids == []
    assert confirm.frames == []


def test_end_to_end_build_then_load_export(tmp_path: Path) -> None:
    """Build the page, synthesize the export the JS would emit from its CONTEXT,
    then load it back — the CONTEXT-embedded frames/hash survive the loader."""
    pytest.importorskip("PIL")
    rows = [r for r in read_sample_assignments(FIXTURES / "sample_assignments.csv")
            if r["annotator_id"] == "A"]
    html_text = build_group_html(
        rows,
        scan_states_dir=FIXTURES / "scan_states",
        rerender_dir=FIXTURES / "rerender",
        thumbnail_size=48,
    )
    # Extract the CONTEXT object the JS would merge into the export.
    marker = "const CONTEXT = "
    start = html_text.index(marker) + len(marker)
    end = html_text.index(";\n", start)
    context = json.loads(html_text[start:end])
    assert set(context) == {"c0000542", "c0001985"}

    # Emulate the browser export: attach a chosen verdict to each anchor's CONTEXT.
    export = {
        "schema_version": 1,
        "sample_batch_id": "batch01",
        "annotator_id": "A",
        "exported_ts_utc": "2026-07-05T10:00:00Z",
        "verdicts": [
            {**context["c0000542"], "verdict": "CONFIRM",
             "corrected_interval_start": "", "corrected_interval_end": "",
             "notes": "", "opened_ts_utc": "2026-07-05T09:59:00Z",
             "submitted_ts_utc": "2026-07-05T09:59:05Z", "adjudication_seconds": 5.0},
            {**context["c0001985"], "verdict": "UNDATABLE",
             "corrected_interval_start": "", "corrected_interval_end": "",
             "notes": "cloud", "opened_ts_utc": "2026-07-05T10:00:00Z",
             "submitted_ts_utc": "2026-07-05T10:00:08Z", "adjudication_seconds": 8.0},
        ],
    }
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export), encoding="utf-8")

    recs = load_verdict_manifest_export(export_path)
    assert {r.anchor_id for r in recs} == {"c0000542", "c0001985"}
    forced = next(r for r in recs if r.anchor_id == "c0000542")
    assert forced.is_dispute_forced is True
    assert forced.dispute_target_ids == ["t00034036"]
    # The provenance-lock hash the builder computed is carried through unchanged.
    assert forced.strip_content_hash == context["c0000542"]["strip_content_hash"]
    assert len(forced.frames) == len(context["c0000542"]["frames"]) > 0
