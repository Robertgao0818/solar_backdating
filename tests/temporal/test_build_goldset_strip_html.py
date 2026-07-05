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
import re
from pathlib import Path

import pytest

from scripts.temporal.build_goldset_strip_html import (
    HEATER_SWAP_MISSING_DWELLING_CONFIRM_TEXT,
    SHIFT_MISSING_BRACKET_CONFIRM_TEXT,
    build_group_html,
    load_verdict_manifest_export,
    main as build_main,
    select_scan_frames,
)
# PLACEHOLDER_PIXEL lives in build_phase0_qa_html.
from scripts.temporal.build_phase0_qa_html import PLACEHOLDER_PIXEL
from scripts.temporal.goldset_schema import (
    DWELLING_CONTEXTS,
    SHIFT_REASONS,
    VERDICTS,
    VerdictRecord,
    read_sample_assignments,
    read_verdict_manifest,
    write_verdict_manifest,
)
from scripts.temporal.scan_state import load_scan_state

FIXTURES = Path(__file__).parent / "fixtures" / "goldset_strip"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


# ---------------------------------------------------------------------------
# WI-1 — verdict codebook (shift_reason / dwelling_context) + --page-size
# (docs/replan_v2/ISSUE-11-prep-design-2026-07-05.md §WI-1, ACs 1.1–1.5)
# ---------------------------------------------------------------------------


def test_ac1_1_shift_reason_roundtrip_and_invalid_raises(tmp_path: Path) -> None:
    """AC-1.1: a heater_swap SHIFT export round-trips shift_reason/dwelling_context
    through load -> write -> read; a non-empty invalid value raises from
    __post_init__, while "" is always accepted (CONFIRM / legacy manifests)."""
    recs = load_verdict_manifest_export(FIXTURES / "ui_export_heater_swap.json")
    by_id = {r.anchor_id: r for r in recs}

    hs = by_id["c0000542"]
    assert hs.verdict == "SHIFT"
    assert hs.shift_reason == "heater_swap"
    assert hs.dwelling_context == "detached_villa"

    out = tmp_path / "verdicts_batch01_A.jsonl"
    write_verdict_manifest(recs, out)
    back = {r.anchor_id: r for r in read_verdict_manifest(out)}
    assert back["c0000542"].shift_reason == "heater_swap"
    assert back["c0000542"].dwelling_context == "detached_villa"
    # a generic date_correction SHIFT with unset dwelling also survives
    assert back["c0007777"].shift_reason == "date_correction"
    assert back["c0007777"].dwelling_context == ""

    assert "heater_swap" in SHIFT_REASONS
    assert "detached_villa" in DWELLING_CONTEXTS

    # A non-empty value outside the vocabulary is rejected.
    with pytest.raises(ValueError):
        VerdictRecord(
            anchor_id="x", grid_id="", stratum="", terminal_status="",
            confidence="", any_contradiction="", verdict="SHIFT",
            pipeline_interval_start="", pipeline_interval_end="",
            shift_reason="not_a_real_reason",
        )
    with pytest.raises(ValueError):
        VerdictRecord(
            anchor_id="x", grid_id="", stratum="", terminal_status="",
            confidence="", any_contradiction="", verdict="SHIFT",
            pipeline_interval_start="", pipeline_interval_end="",
            dwelling_context="mansion",
        )
    # "" (unset) is always allowed — no exception.
    VerdictRecord(
        anchor_id="x", grid_id="", stratum="", terminal_status="",
        confidence="", any_contradiction="", verdict="CONFIRM",
        pipeline_interval_start="", pipeline_interval_end="",
        shift_reason="", dwelling_context="",
    )


def test_ac1_2_selectors_inside_shift_bracket_and_heater_swap_guard() -> None:
    """AC-1.2: both codebook <select>s live inside class='shift-bracket' (never in
    the CONFIRM verdict-controls row); the export JS carries a confirm() guard for
    heater_swap SHIFTs missing a dwelling label, positioned before the export Blob."""
    pytest.importorskip("PIL")
    html_text = _build_annotator_a_page()

    # Both selectors are inside the (hidden) SHIFT bracket panel.
    b_start = html_text.index("<div class='shift-bracket'")
    b_end = html_text.index("</div>", b_start)
    bracket = html_text[b_start:b_end]
    assert "data-shift-reason" in bracket
    assert "data-dwelling-context" in bracket
    # shift_reason defaults to date_correction; dwelling defaults to unset.
    assert "<option value='date_correction' selected>" in bracket
    assert "<option value='' selected>" in bracket

    # The CONFIRM row (verdict-controls) carries NO codebook control.
    v_start = html_text.index("<div class='verdict-controls'>")
    v_end = html_text.index("</div>", v_start)
    controls = html_text[v_start:v_end]
    assert "data-shift-reason" not in controls
    assert "data-dwelling-context" not in controls

    # heater_swap dwelling guard is a cancellable confirm() BEFORE the export Blob.
    assert HEATER_SWAP_MISSING_DWELLING_CONFIRM_TEXT in html_text
    assert "v.shift_reason==='heater_swap'" in html_text
    assert "confirm(msg2)" in html_text
    guard_idx = html_text.index("confirm(msg2)")
    blob_idx = html_text.index("new Blob([JSON.stringify(manifest")
    assert guard_idx < blob_idx, "the heater_swap dwelling guard must run before the export Blob"
    # The pre-existing empty-bracket guard still runs before the Blob too.
    assert SHIFT_MISSING_BRACKET_CONFIRM_TEXT in html_text
    assert html_text.index("confirm(msg)") < blob_idx


def test_ac1_3_backward_compat_legacy_manifest_loads(tmp_path: Path) -> None:
    """AC-1.3: a copy of the real verdicts_dryrun_20260705_A.json (no new keys)
    loads with shift_reason=="" / dwelling_context=="" on every record and
    re-serializes + reloads with no error and no field loss."""
    recs = load_verdict_manifest_export(FIXTURES / "verdicts_dryrun_20260705_A.json")
    assert len(recs) == 12
    assert all(r.shift_reason == "" and r.dwelling_context == "" for r in recs)

    out = tmp_path / "verdicts_dryrun_20260705_A.jsonl"
    write_verdict_manifest(recs, out)
    back = read_verdict_manifest(out)
    assert len(back) == len(recs)

    before = {r.anchor_id: r for r in recs}
    after = {r.anchor_id: r for r in back}
    assert set(before) == set(after)
    for aid, r0 in before.items():
        r1 = after[aid]
        assert r1.verdict == r0.verdict
        assert r1.shift_reason == "" and r1.dwelling_context == ""
        assert r1.corrected_interval_start == r0.corrected_interval_start
        assert r1.corrected_interval_end == r0.corrected_interval_end
        assert [(f.source, f.role, f.capture_date, f.version) for f in r1.frames] == \
               [(f.source, f.role, f.capture_date, f.version) for f in r0.frames]


def test_ac1_4_confirm_fast_path_unchanged() -> None:
    """AC-1.4: the keyboard '1'->CONFIRM submit is present and unconditional (no
    bracket/reason interaction), and a CONFIRM export entry round-trips with
    shift_reason=="" / dwelling_context=="" (buildManifest emits "" off SHIFT)."""
    pytest.importorskip("PIL")
    html_text = _build_annotator_a_page()
    assert "if(ev.key==='1')submitVerdict(current,'CONFIRM');" in html_text
    # buildManifest gates the codebook fields on SHIFT -> CONFIRM/UNDATABLE emit "".
    assert "shift_reason:r.verdict==='SHIFT'?(r.shift_reason||'date_correction'):''" in html_text
    assert "dwelling_context:r.verdict==='SHIFT'?(r.dwelling_context||''):''" in html_text

    recs = load_verdict_manifest_export(FIXTURES / "ui_export_heater_swap.json")
    confirm = next(r for r in recs if r.anchor_id == "c0008888")
    assert confirm.verdict == "CONFIRM"
    assert confirm.shift_reason == "" and confirm.dwelling_context == ""


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_ac1_5_page_size_zero_noop_and_chunking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-1.5: --page-size 0 is a byte-identical no-op (same filenames + bytes as
    the non-paginated single-file build and as the committed golden); --page-size k
    with a group of >k anchors emits disjoint _pNN pages whose union is the group,
    each self-contained, with a distinct page suffix and the true sample_batch_id."""
    pytest.importorskip("PIL")
    rows = read_sample_assignments(FIXTURES / "sample_assignments.csv")
    rows_a = [r for r in rows if r["annotator_id"] == "A"]
    rows_b = [r for r in rows if r["annotator_id"] == "B"]

    # --- no-op vs the live non-paginated core builder (page_suffix default "") ---
    ref_a = build_group_html(rows_a, scan_states_dir=FIXTURES / "scan_states",
                             rerender_dir=FIXTURES / "rerender", thumbnail_size=48)
    ref_b = build_group_html(rows_b, scan_states_dir=FIXTURES / "scan_states",
                             rerender_dir=FIXTURES / "rerender", thumbnail_size=48)
    out0 = tmp_path / "p0"
    monkeypatch.setattr("sys.argv", [
        "build_goldset_strip_html.py",
        "--assignments", str(FIXTURES / "sample_assignments.csv"),
        "--scan-states-dir", str(FIXTURES / "scan_states"),
        "--rerender-dir", str(FIXTURES / "rerender"),
        "--output-dir", str(out0),
        "--thumbnail-size", "48",
        "--page-size", "0",
    ])
    build_main()
    a0 = out0 / "verdicts_batch01_A.html"
    b0 = out0 / "verdicts_batch01_B.html"
    assert a0.exists() and b0.exists()
    assert not list(out0.glob("*_p*.html")), "page-size 0 must not emit _pNN files"
    assert _read(a0) == ref_a
    assert _read(b0) == ref_b

    # --- committed golden regression (relative-path form; run from repo root) ---
    monkeypatch.chdir(PROJECT_ROOT)
    out0b = tmp_path / "p0b"
    monkeypatch.setattr("sys.argv", [
        "build_goldset_strip_html.py",
        "--assignments", "tests/temporal/fixtures/goldset_strip/sample_assignments.csv",
        "--scan-states-dir", "tests/temporal/fixtures/goldset_strip/scan_states",
        "--rerender-dir", "tests/temporal/fixtures/goldset_strip/rerender",
        "--output-dir", str(out0b),
        "--thumbnail-size", "48",
    ])  # --page-size omitted -> defaults to 0
    build_main()
    golden = FIXTURES / "golden_pagesize0"
    assert _read(out0b / "verdicts_batch01_A.html") == _read(golden / "verdicts_batch01_A.html")
    assert _read(out0b / "verdicts_batch01_B.html") == _read(golden / "verdicts_batch01_B.html")

    # --- chunking: --page-size 1 splits A's 2 anchors into 2 disjoint _pNN pages ---
    out1 = tmp_path / "p1"
    monkeypatch.setattr("sys.argv", [
        "build_goldset_strip_html.py",
        "--assignments", str(FIXTURES / "sample_assignments.csv"),
        "--scan-states-dir", str(FIXTURES / "scan_states"),
        "--rerender-dir", str(FIXTURES / "rerender"),
        "--output-dir", str(out1),
        "--thumbnail-size", "48",
        "--page-size", "1",
    ])
    build_main()
    a_p01 = out1 / "verdicts_batch01_A_p01.html"
    a_p02 = out1 / "verdicts_batch01_A_p02.html"
    b_p01 = out1 / "verdicts_batch01_B_p01.html"
    assert a_p01.exists() and a_p02.exists() and b_p01.exists()

    def anchors(h: str) -> set[str]:
        return set(re.findall(r"data-anchor-id='([^']*)'", h))

    t1, t2, tb = _read(a_p01), _read(a_p02), _read(b_p01)
    s1, s2 = anchors(t1), anchors(t2)
    assert s1 == {"c0000542"} and s2 == {"c0001985"}
    assert s1.isdisjoint(s2)
    assert s1 | s2 == {"c0000542", "c0001985"}  # union == the A group

    for t in (t1, t2, tb):
        assert "http://" not in t and "https://" not in t  # self-contained
        assert "data:image/png;base64," in t
        assert 'const BATCH = "batch01"' in t               # sample_batch_id stays the tag
        assert '"sample_batch_id":"batch01"' in t
        assert "batch01_p" not in t                          # suffix never leaks into the tag
    # distinct LS_KEY page suffix per chunk
    assert 'const PAGE_SUFFIX = "_p01"' in t1
    assert 'const PAGE_SUFFIX = "_p02"' in t2
    assert 'const PAGE_SUFFIX = "_p01"' in tb
