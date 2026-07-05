#!/usr/bin/env python3
"""ISSUE-10 WP-A — gold-set jump-critical-point strip UI + verdict manifest loader.

Sibling of ``build_phase0_qa_html.py`` (not an in-place edit — keeps the pure
Phase-0 QA page and its golden test untouched). This builder renders a *narrow
jump-window* review page per annotator: for every sampled anchor it shows the
claimed latest-absent / earliest-present scan frames plus 1-2 flanks, the CoJ
municipal true-date chips (2019/2023), the Vexcel present-day chip and any
in-window Wayback capture that WP-C re-rendered, then offers CONFIRM / SHIFT /
UNDATABLE verdict controls with keyboard shortcuts, localStorage autosave, a
Blob "Download manifest" export and an import-to-resume file picker.

Frame selection reuses ``run_census2023_scan._anchor_frames`` (the canonical
latest-absent / earliest-present picker); flanks are the immediately-adjacent
usable scan slots by ``capture_date``. Every field name, path and the verdict
vocabulary come from ``goldset_schema`` — this module never re-declares them.

The emitted HTML is **timestamp-free**: all per-anchor timing is read at runtime
in the browser (``Date.now()``), never baked into the static bytes, so the page
is deterministic for a fixed input and safe to golden-test.

Two consumers of the export live here too:

* ``load_verdict_manifest_export(json_path)`` — maps a browser-exported UI JSON
  into ``list[VerdictRecord]`` (the round-trip target ISSUE-11 loads).

Re-rendered chip layout (WP-C output, consumed read-only here)::

    <rerender>/chips/<anchor_id>/scan_<capture_date>_v<version>.(png|tif)
    <rerender>/chips/<anchor_id>/coj_<year>.(png|tif)
    <rerender>/chips/<anchor_id>/vexcel.(png|tif)
    <rerender>/chips/<anchor_id>/wayback_<capture_date>.(png|tif)

Resolution is glob-tolerant (matches on the capture_date / year substring) so a
minor WP-C filename variant still resolves; a frame with no file becomes a
captioned ``PLACEHOLDER_PIXEL`` slot (dropped, not a silent hole).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal import goldset_schema as gs
from scripts.temporal.build_phase0_qa_html import (
    PLACEHOLDER_PIXEL,
    _resolve_review_png,
    thumbnail_data_url,
)
from scripts.temporal.goldset_schema import (
    FrameIdentity,
    VerdictRecord,
    frame_content_hash,
)
from scripts.temporal.run_census2023_scan import _anchor_frames
from scripts.temporal.scan_state import RoundResult, ScanState, load_scan_state

THUMBNAIL_SIZE = 220
MAX_FLANKS = 2
_CHIP_SUFFIXES = (".png", ".tif", ".tiff", ".jpg", ".jpeg")


# ---------------------------------------------------------------------------
# Frame selection (reuses _anchor_frames for the two bounds, adds flanks)


def _usable_sorted(state: ScanState) -> list[RoundResult]:
    """Usable, decided observations with a (possibly dead) chip_path, by date."""
    usable = [
        r
        for rnd in state.rounds
        for r in rnd.results
        if r.quality_flag == "usable" and r.pv_present is not None and (r.chip_path or "")
    ]
    return sorted(usable, key=lambda r: r.capture_date)


def select_scan_frames(state: ScanState) -> list[tuple[str, RoundResult]]:
    """Return ``[(role, RoundResult), ...]`` for the jump window, in display order.

    Roles from ``goldset_schema.FRAME_ROLES``: ``flank_before`` (<=2 slots just
    before latest-absent), ``latest_absent``, ``earliest_present``,
    ``flank_after`` (<=2 slots just after earliest-present). Missing bounds are
    simply skipped (degrade, not crash).
    """
    usable = _usable_sorted(state)
    latest_absent, earliest_present = _anchor_frames(state)

    out: list[tuple[str, RoundResult]] = []
    la_date = latest_absent.capture_date if latest_absent else None
    ep_date = earliest_present.capture_date if earliest_present else None

    if la_date is not None:
        before = [r for r in usable if r.capture_date < la_date]
        for r in before[-MAX_FLANKS:]:
            out.append(("flank_before", r))
        out.append(("latest_absent", latest_absent))
    if ep_date is not None:
        out.append(("earliest_present", earliest_present))
        after = [r for r in usable if r.capture_date > ep_date]
        for r in after[:MAX_FLANKS]:
            out.append(("flank_after", r))
    return out


def anchor_is_undatable(state: ScanState | None) -> bool:
    """True when an anchor has NO usable jump-window scan frame to adjudicate.

    A degenerate scan_state (no usable dated rounds -> neither ``latest_absent``
    nor ``earliest_present`` bound) yields an empty jump window, so the human has
    nothing to CONFIRM/SHIFT — the anchor is an UNDATABLE candidate. Reference-only
    (CoJ / Wayback) frames do NOT make an anchor datable: there is no pipeline
    bracket for them to adjudicate. Kept separate from per-frame imagery drops
    (those are recorded in WP-C's ``frame_report.csv``); this flags the structural
    "no dated rounds" case so ISSUE-11's completeness accounting can see it.
    """
    if state is None:
        return True
    return not select_scan_frames(state)


# ---------------------------------------------------------------------------
# Re-rendered chip resolution (WP-C output, read-only)


def _anchor_chip_dir(rerender_dir: Path | None, anchor_id: str) -> Path | None:
    if rerender_dir is None:
        return None
    d = Path(rerender_dir) / "chips" / anchor_id
    return d if d.is_dir() else None


def _pick(candidates: list[Path]) -> Path | None:
    real = [p for p in candidates if p.suffix.lower() in _CHIP_SUFFIXES]
    return sorted(real)[0] if real else None


def resolve_scan_chip(chip_dir: Path | None, capture_date: str, version: int | None) -> Path | None:
    if chip_dir is None:
        return None
    if version is not None:
        exact = _pick(list(chip_dir.glob(f"scan_{capture_date}_v{version}.*")))
        if exact is not None:
            return exact
    # Fallback: any scan-ish file carrying the capture_date.
    loose = [
        p
        for p in chip_dir.glob(f"*{capture_date}*")
        if not p.name.startswith(("coj_", "wayback_", "vexcel"))
    ]
    return _pick(loose)


def resolve_reference_chip(chip_dir: Path | None, prefix: str, token: str) -> Path | None:
    """CoJ / Vexcel / Wayback lookup by ``<prefix>_<token>`` then loose token match."""
    if chip_dir is None:
        return None
    exact = _pick(list(chip_dir.glob(f"{prefix}_{token}.*"))) if token else None
    if exact is not None:
        return exact
    if token:
        return _pick([p for p in chip_dir.glob(f"{prefix}*") if token in p.name])
    return _pick(list(chip_dir.glob(f"{prefix}*")))


def _sha256_file(path: Path | None) -> str:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Per-anchor frame assembly -> (FrameIdentity list, [(FrameIdentity, data_url)])


def _reference_frames(chip_dir: Path | None) -> list[tuple[str, str, str]]:
    """Discover reference chips actually present. Returns [(source, token, prefix)].

    Only chips WP-C rendered are shown; nothing invented. CoJ carry the year in
    the source id (undated municipal -> capture_date=""), Wayback carries the
    capture date parsed from the filename.
    """
    out: list[tuple[str, str, str]] = []
    if chip_dir is None:
        return out
    for year in ("2015", "2019", "2023"):
        if resolve_reference_chip(chip_dir, "coj", year) is not None:
            out.append((f"coj_{year}", year, "coj"))
    if resolve_reference_chip(chip_dir, "vexcel", "") is not None:
        out.append(("scan_tm", "", "vexcel"))
    for p in sorted(chip_dir.glob("wayback_*")):
        if p.suffix.lower() not in _CHIP_SUFFIXES:
            continue
        stem = p.stem[len("wayback_"):]
        out.append(("wayback", stem, "wayback"))
    return out


def build_anchor_frames(
    state: ScanState | None,
    chip_dir: Path | None,
    thumbnail_size: int,
) -> tuple[list[FrameIdentity], list[tuple[FrameIdentity, str, str]]]:
    """Assemble a jump-window strip for one anchor.

    Returns ``(frames, display)`` where ``frames`` are the provenance-locked
    ``FrameIdentity`` records (feed ``frame_content_hash``) and ``display`` is
    ``[(frame, data_url, caption), ...]`` in render order.
    """
    frames: list[FrameIdentity] = []
    display: list[tuple[FrameIdentity, str, str]] = []

    if state is not None:
        for role, result in select_scan_frames(state):
            chip = resolve_scan_chip(chip_dir, result.capture_date, result.version)
            png = _resolve_review_png(str(chip)) if chip is not None else None
            data_url = thumbnail_data_url(png, thumbnail_size)
            sha = _sha256_file(chip)
            fi = FrameIdentity(
                source="scan_tm",
                role=role,
                capture_date=result.capture_date,
                version=result.version,
                chip_path=str(chip) if chip is not None else "",
                chip_sha256=sha,
            )
            frames.append(fi)
            caption = f"{role} · {result.capture_date} · z={result.actual_zoom or '—'}"
            if chip is None:
                caption += " · DROPPED"
            display.append((fi, data_url, caption))

    for source, token, prefix in _reference_frames(chip_dir):
        chip = resolve_reference_chip(chip_dir, prefix, token)
        png = _resolve_review_png(str(chip)) if chip is not None else None
        data_url = thumbnail_data_url(png, thumbnail_size)
        capture_date = token if source == "wayback" else ""
        fi = FrameIdentity(
            source=source,
            role="reference",
            capture_date=capture_date,
            version=None,
            chip_path=str(chip) if chip is not None else "",
            chip_sha256=_sha256_file(chip),
        )
        frames.append(fi)
        label = {"coj_2015": "CoJ 2015", "coj_2019": "CoJ 2019", "coj_2023": "CoJ 2023",
                 "scan_tm": "Vexcel", "wayback": f"Wayback {token}"}.get(source, source)
        caption = f"ref · {label}"
        if chip is None:
            caption += " · DROPPED"
        display.append((fi, data_url, caption))

    return frames, display


# ---------------------------------------------------------------------------
# HTML rendering


def _bool(value: str) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


#: Banner rendered for anchors with no usable jump-window frame (see
#: ``anchor_is_undatable``); grepped by the dry-run verification.
UNDATABLE_BANNER_TEXT = "NO USABLE FRAMES — UNDATABLE candidate"

#: confirm() dialog fragment shown at export time when a SHIFT verdict has an
#: empty corrected bracket (ISSUE-11 needs the corrected interval for every
#: SHIFT record). Embedded verbatim into the client JS below; grepped by the
#: structural test so the guard can't silently regress.
SHIFT_MISSING_BRACKET_CONFIRM_TEXT = "SHIFT verdict(s) missing corrected bracket"


def render_anchor_section(
    row: dict[str, str], display, frames, strip_hash: str, *, undatable: bool = False
) -> str:
    anchor_id = row.get("anchor_id", "")
    forced = _bool(row.get("is_dispute_forced", ""))
    overlap = _bool(row.get("is_overlap", ""))
    pip_start = row.get("pipeline_interval_start", "")
    pip_end = row.get("pipeline_interval_end", "")

    badges = [
        f"<span class='badge'>{html.escape(row.get('stratum', ''))}</span>",
        f"<span class='badge status'>{html.escape(row.get('terminal_status', ''))}</span>",
        f"<span class='badge conf'>{html.escape(row.get('confidence', ''))}</span>",
    ]
    if row.get("any_contradiction", "") not in ("", "False"):
        badges.append("<span class='badge contra'>contradiction</span>")
    if overlap:
        badges.append("<span class='badge overlap'>overlap</span>")
    if forced:
        tids = html.escape(row.get("dispute_target_ids", ""))
        badges.append(f"<span class='badge dispute'>dispute {tids}</span>")
    if undatable:
        badges.append("<span class='badge undatable'>UNDATABLE</span>")

    chips = []
    for _fi, data_url, caption in display:
        chips.append(
            "<figure class='chip'>"
            f"<img src='{data_url}' alt='{html.escape(caption)}' />"
            f"<figcaption>{html.escape(caption)}</figcaption>"
            "</figure>"
        )

    banner = (
        f"<div class='undatable-banner'>{html.escape(UNDATABLE_BANNER_TEXT)}</div>"
        if undatable
        else ""
    )
    section_cls = "anchor undatable" if undatable else "anchor"

    return (
        f"<section class='{section_cls}' data-anchor-id='{html.escape(anchor_id)}' "
        f"data-strip-hash='{html.escape(strip_hash)}' "
        f"data-undatable='{'true' if undatable else 'false'}'>"
        "<header class='anchor-head'>"
        f"<div class='anchor-title'>{html.escape(anchor_id)}</div>"
        f"<div class='anchor-meta'>{''.join(badges)}</div>"
        "</header>"
        f"{banner}"
        f"<div class='pipeline muted'>pipeline bracket = [{html.escape(pip_start)}, "
        f"{html.escape(pip_end)}]</div>"
        f"<div class='chip-strip'>{''.join(chips)}</div>"
        "<div class='verdict-controls'>"
        f"<button class='vbtn' data-verdict='CONFIRM'>CONFIRM <kbd>1</kbd></button>"
        f"<button class='vbtn' data-verdict='SHIFT'>SHIFT <kbd>2</kbd></button>"
        f"<button class='vbtn' data-verdict='UNDATABLE'>UNDATABLE <kbd>3</kbd></button>"
        "<span class='verdict-state muted'></span>"
        "</div>"
        "<div class='shift-bracket' hidden>"
        "<label>corrected start "
        "<input type='date' data-corrected='start' /></label>"
        "<label>corrected end "
        "<input type='date' data-corrected='end' /></label>"
        "</div>"
        "<div class='notes-row'>"
        "<input type='text' class='notes' placeholder='notes (optional)' /></div>"
        "</section>"
    )


CSS = """
:root{color-scheme:dark;--bg:#0d1117;--panel:#161b22;--line:#30363d;--text:#e6edf3;
  --muted:#7d8590;--green:#238636;--amber:#9e6a03;--red:#da3633;--blue:#2f81f7;--sel:#1f6feb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;font-size:13px}
.page-head{position:sticky;top:0;z-index:9;background:#010409;border-bottom:1px solid var(--line);
  padding:10px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.page-head h1{margin:0;font-size:16px}
.page-head .muted{font-size:12px}
.toolbar{margin-left:auto;display:flex;gap:8px}
.toolbar button{background:#21262d;color:var(--text);border:1px solid var(--line);
  border-radius:6px;padding:6px 10px;cursor:pointer;font-size:12px}
.toolbar button:hover{border-color:var(--blue)}
.wrap{padding:14px 16px 60px}
.anchor{border:1px solid var(--line);background:var(--panel);border-radius:6px;margin:0 0 16px;
  overflow:hidden;scroll-margin-top:60px}
.anchor.current{border-color:var(--sel);box-shadow:0 0 0 1px var(--sel)}
.anchor.answered{opacity:.92}
.anchor-head{padding:8px 10px;border-bottom:1px solid var(--line);display:flex;
  justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.anchor-title{font-weight:700;font-size:14px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.anchor-meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.badge{padding:2px 8px;border-radius:10px;font-size:11px;background:#21262d;border:1px solid var(--line)}
.badge.contra{background:#3d1f1f;border-color:var(--red)}
.badge.overlap{background:#1c2c3d;border-color:var(--blue)}
.badge.dispute{background:#3b2f0e;border-color:var(--amber)}
.badge.undatable{background:#3d1f1f;border-color:var(--red);font-weight:700}
.anchor.undatable{border-color:var(--red)}
.undatable-banner{margin:0;padding:8px 10px;background:#3d1f1f;border-top:1px solid var(--red);
  border-bottom:1px solid var(--red);color:#ffb3b0;font-weight:700;letter-spacing:.3px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.pipeline{padding:6px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.chip-strip{display:flex;gap:8px;overflow-x:auto;padding:4px 10px 8px}
.chip{margin:0;border:2px solid var(--line);border-radius:5px;padding:4px;background:#0d1117;
  min-width:160px;max-width:220px}
.chip img{width:100%;height:auto;aspect-ratio:1/1;object-fit:cover;display:block;border-radius:3px;background:#000}
.chip figcaption{margin-top:4px;font-size:11px;line-height:1.3;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.verdict-controls{padding:8px 10px;border-top:1px solid var(--line);display:flex;gap:8px;align-items:center}
.vbtn{background:#21262d;color:var(--text);border:1px solid var(--line);border-radius:6px;
  padding:6px 12px;cursor:pointer;font-size:13px}
.vbtn kbd{font-size:10px;color:var(--muted)}
.vbtn.sel[data-verdict=CONFIRM]{background:#0e3b1f;border-color:var(--green)}
.vbtn.sel[data-verdict=SHIFT]{background:#3b2f0e;border-color:var(--amber)}
.vbtn.sel[data-verdict=UNDATABLE]{background:#3d1f1f;border-color:var(--red)}
.verdict-state{margin-left:6px}
.shift-bracket{padding:0 10px 8px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.shift-bracket input{background:#0d1117;color:var(--text);border:1px solid var(--line);
  border-radius:5px;padding:4px 6px}
.notes-row{padding:0 10px 10px}
.notes{width:100%;background:#0d1117;color:var(--text);border:1px solid var(--line);
  border-radius:5px;padding:5px 7px}
.muted{color:var(--muted)}
"""


def _client_js(batch_id: str, annotator_id: str, context: dict) -> str:
    ctx_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return (
        "<script>\n"
        f"const CONTEXT = {ctx_json};\n"
        f"const BATCH = {json.dumps(batch_id)};\n"
        f"const ANNOTATOR = {json.dumps(annotator_id)};\n"
        "const LS_KEY = 'goldset_verdicts_' + BATCH + '_' + ANNOTATOR;\n"
        "const VERDICTS = ['CONFIRM','SHIFT','UNDATABLE'];\n"
        "function nowIso(){return new Date().toISOString().replace(/\\.\\d+Z$/,'Z');}\n"
        "let state = {};\n"
        "try{state = JSON.parse(localStorage.getItem(LS_KEY)) || {};}catch(e){state={};}\n"
        "let current = null;\n"
        "function saveLS(){try{localStorage.setItem(LS_KEY, JSON.stringify(state));}catch(e){}}\n"
        "function ensureRec(id){if(!state[id]){state[id]={verdict:'',corrected_interval_start:'',"
        "corrected_interval_end:'',notes:'',opened_ts_utc:'',submitted_ts_utc:'',"
        "adjudication_seconds:0};}return state[id];}\n"
        "function markOpened(id){const r=ensureRec(id);if(!r.opened_ts_utc){r.opened_ts_utc=nowIso();saveLS();}}\n"
        "function setCurrent(el){document.querySelectorAll('.anchor.current').forEach(a=>a.classList.remove('current'));"
        "el.classList.add('current');current=el;markOpened(el.dataset.anchorId);}\n"
        "function applyRec(el){const id=el.dataset.anchorId;const r=state[id];"
        "el.querySelectorAll('.vbtn').forEach(b=>b.classList.remove('sel'));"
        "const shift=el.querySelector('.shift-bracket');const st=el.querySelector('.verdict-state');"
        "const notes=el.querySelector('.notes');if(!r){shift.hidden=true;st.textContent='';return;}"
        "if(r.verdict){const b=el.querySelector('.vbtn[data-verdict=\"'+r.verdict+'\"]');if(b)b.classList.add('sel');}"
        "shift.hidden = (r.verdict!=='SHIFT');"
        "el.querySelector('[data-corrected=start]').value=r.corrected_interval_start||'';"
        "el.querySelector('[data-corrected=end]').value=r.corrected_interval_end||'';"
        "if(notes)notes.value=r.notes||'';"
        "el.classList.toggle('answered', !!r.verdict);"
        "st.textContent = r.verdict ? (r.verdict+' · '+(r.adjudication_seconds||0).toFixed(1)+'s') : '';}\n"
        "function submitVerdict(el, verdict){const id=el.dataset.anchorId;const r=ensureRec(id);"
        "markOpened(id);r.verdict=verdict;r.submitted_ts_utc=nowIso();"
        "const o=Date.parse(r.opened_ts_utc||r.submitted_ts_utc);const s=Date.parse(r.submitted_ts_utc);"
        "r.adjudication_seconds=Math.max(0,(s-o)/1000);saveLS();applyRec(el);}\n"
        "document.querySelectorAll('.anchor').forEach(el=>{\n"
        "  el.addEventListener('click',()=>setCurrent(el));\n"
        "  el.querySelectorAll('.vbtn').forEach(btn=>btn.addEventListener('click',ev=>{"
        "ev.stopPropagation();setCurrent(el);submitVerdict(el, btn.dataset.verdict);}));\n"
        "  el.querySelectorAll('[data-corrected]').forEach(inp=>inp.addEventListener('change',()=>{"
        "const r=ensureRec(el.dataset.anchorId);"
        "if(inp.dataset.corrected==='start')r.corrected_interval_start=inp.value;"
        "else r.corrected_interval_end=inp.value;saveLS();}));\n"
        "  const notes=el.querySelector('.notes');if(notes)notes.addEventListener('change',()=>{"
        "ensureRec(el.dataset.anchorId).notes=notes.value;saveLS();});\n"
        "  applyRec(el);\n"
        "});\n"
        "const io=new IntersectionObserver((es)=>{es.forEach(e=>{if(e.isIntersecting)markOpened(e.target.dataset.anchorId);});},{threshold:0.5});\n"
        "document.querySelectorAll('.anchor').forEach(el=>io.observe(el));\n"
        "document.addEventListener('keydown',ev=>{if(ev.target.tagName==='INPUT')return;"
        "if(!current){const first=document.querySelector('.anchor');if(first)setCurrent(first);}if(!current)return;"
        "if(ev.key==='1')submitVerdict(current,'CONFIRM');"
        "else if(ev.key==='2')submitVerdict(current,'SHIFT');"
        "else if(ev.key==='3')submitVerdict(current,'UNDATABLE');"
        "else if(ev.key==='j'||ev.key==='k'){const all=[...document.querySelectorAll('.anchor')];"
        "let i=all.indexOf(current);i+= (ev.key==='j'?1:-1);i=Math.max(0,Math.min(all.length-1,i));"
        "all[i].scrollIntoView({block:'center'});setCurrent(all[i]);}});\n"
        "function buildManifest(){const verdicts=[];for(const id in CONTEXT){const r=state[id];"
        "if(!r||!VERDICTS.includes(r.verdict))continue;"
        "verdicts.push(Object.assign({}, CONTEXT[id], {verdict:r.verdict,"
        "corrected_interval_start:r.verdict==='SHIFT'?(r.corrected_interval_start||''):'',"
        "corrected_interval_end:r.verdict==='SHIFT'?(r.corrected_interval_end||''):'',"
        "notes:r.notes||'',opened_ts_utc:r.opened_ts_utc||'',submitted_ts_utc:r.submitted_ts_utc||'',"
        "adjudication_seconds:r.adjudication_seconds||0}));}"
        "return {schema_version:1,sample_batch_id:BATCH,annotator_id:ANNOTATOR,"
        "exported_ts_utc:nowIso(),verdicts:verdicts};}\n"
        "document.getElementById('export-btn').addEventListener('click',()=>{"
        "const manifest=buildManifest();"
        "const missing=manifest.verdicts.filter(v=>v.verdict==='SHIFT'&&"
        "(!v.corrected_interval_start||!v.corrected_interval_end)).map(v=>v.anchor_id);"
        "if(missing.length){"
        f"const msg=missing.length+' {SHIFT_MISSING_BRACKET_CONFIRM_TEXT}: '+missing.join(', ')+'. Export anyway?';"
        "if(!confirm(msg))return;"
        "}"
        "const blob=new Blob([JSON.stringify(manifest,null,2)],{type:'application/json'});"
        "const a=document.createElement('a');a.href=URL.createObjectURL(blob);"
        "a.download='verdicts_'+BATCH+'_'+ANNOTATOR+'.json';a.click();URL.revokeObjectURL(a.href);});\n"
        "document.getElementById('import-input').addEventListener('change',ev=>{"
        "const f=ev.target.files[0];if(!f)return;const rd=new FileReader();rd.onload=()=>{"
        "try{const m=JSON.parse(rd.result);(m.verdicts||[]).forEach(v=>{const r=ensureRec(v.anchor_id);"
        "r.verdict=v.verdict||'';r.corrected_interval_start=v.corrected_interval_start||'';"
        "r.corrected_interval_end=v.corrected_interval_end||'';r.notes=v.notes||'';"
        "r.opened_ts_utc=v.opened_ts_utc||'';r.submitted_ts_utc=v.submitted_ts_utc||'';"
        "r.adjudication_seconds=v.adjudication_seconds||0;});saveLS();"
        "document.querySelectorAll('.anchor').forEach(applyRec);}catch(e){alert('import failed: '+e);}};"
        "rd.readAsText(f);});\n"
        "</script>\n"
    )


def render_page(batch_id: str, annotator_id: str, sections_html: list[str], context: dict) -> str:
    head = (
        "<div class='page-head'>"
        f"<h1>Gold-set jump review</h1>"
        f"<div class='muted'>batch {html.escape(batch_id)} · annotator {html.escape(annotator_id)} · "
        f"{len(sections_html)} anchors</div>"
        "<div class='toolbar'>"
        "<button id='export-btn'>Download manifest</button>"
        "<label class='muted' style='cursor:pointer'>Import manifest"
        "<input id='import-input' type='file' accept='application/json' hidden /></label>"
        "</div>"
        "</div>"
    )
    body = "<div class='wrap'>" + "".join(sections_html) + "</div>"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Gold-set jump review — {html.escape(batch_id)}/{html.escape(annotator_id)}</title>"
        f"<style>{CSS}</style></head><body>"
        f"{head}{body}"
        f"{_client_js(batch_id, annotator_id, context)}"
        "</body></html>"
    )


def _context_entry(row: dict[str, str], frames: list[FrameIdentity], strip_hash: str) -> dict:
    """Static per-anchor payload embedded for the JS export (merged with dynamics)."""
    seed = row.get("sampler_seed", "")
    try:
        seed_val: int | None = int(seed) if str(seed).strip() != "" else None
    except ValueError:
        seed_val = None
    return {
        "anchor_id": row.get("anchor_id", ""),
        "grid_id": row.get("grid_id", ""),
        "stratum": row.get("stratum", ""),
        "terminal_status": row.get("terminal_status", ""),
        "confidence": row.get("confidence", ""),
        "any_contradiction": row.get("any_contradiction", ""),
        "pipeline_interval_start": row.get("pipeline_interval_start", ""),
        "pipeline_interval_end": row.get("pipeline_interval_end", ""),
        "annotator_id": row.get("annotator_id", ""),
        "is_overlap": _bool(row.get("is_overlap", "")),
        "is_dispute_forced": _bool(row.get("is_dispute_forced", "")),
        "dispute_target_ids": gs.decode_target_ids(row.get("dispute_target_ids", "")),
        "sampler_seed": seed_val,
        "sample_batch_id": row.get("sample_batch_id", ""),
        "strip_content_hash": strip_hash,
        "frames": [
            {
                "source": f.source,
                "role": f.role,
                "capture_date": f.capture_date,
                "version": f.version,
                "chip_path": f.chip_path,
                "chip_sha256": f.chip_sha256,
            }
            for f in frames
        ],
    }


def build_group_html(
    rows: list[dict[str, str]],
    scan_states_dir: Path,
    rerender_dir: Path | None,
    thumbnail_size: int,
) -> str:
    """Render one annotator's strip page from their assignment rows."""
    batch_id = rows[0].get("sample_batch_id", "")
    annotator_id = rows[0].get("annotator_id", "")
    sections: list[str] = []
    context: dict = {}
    for row in rows:
        anchor_id = row.get("anchor_id", "")
        state_path = row.get("scan_state_path", "") or str(scan_states_dir / f"{anchor_id}.json")
        state = load_scan_state(Path(state_path)) if Path(state_path).exists() else None
        chip_dir = _anchor_chip_dir(rerender_dir, anchor_id)
        frames, display = build_anchor_frames(state, chip_dir, thumbnail_size)
        strip_hash = frame_content_hash(frames)
        undatable = anchor_is_undatable(state)
        sections.append(render_anchor_section(row, display, frames, strip_hash, undatable=undatable))
        context[anchor_id] = _context_entry(row, frames, strip_hash)
    return render_page(batch_id, annotator_id, sections, context)


# ---------------------------------------------------------------------------
# Builder report — surface UNDATABLE-candidate anchors for ISSUE-11 accounting

BUILDER_REPORT_FIELDS: tuple[str, ...] = (
    "anchor_id",
    "sample_batch_id",
    "is_dispute_forced",
    "dispute_target_ids",
    "reason",
)


def collect_undatable_anchors(
    rows: list[dict[str, str]], scan_states_dir: Path
) -> list[dict[str, str]]:
    """Unique anchors whose strip has no usable jump-window frame.

    Deduped by ``anchor_id`` (assignments carry two annotator rows per anchor).
    Reason distinguishes a missing/unloadable scan_state from a loadable-but-
    degenerate one (no usable dated rounds). Purely diagnostic — it surfaces the
    UNDATABLE candidates for ISSUE-11 completeness accounting and does NOT decide
    adjudication policy.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        anchor_id = row.get("anchor_id", "")
        if not anchor_id or anchor_id in seen:
            continue
        seen.add(anchor_id)
        state_path = row.get("scan_state_path", "") or str(scan_states_dir / f"{anchor_id}.json")
        state = load_scan_state(Path(state_path)) if Path(state_path).exists() else None
        if not anchor_is_undatable(state):
            continue
        out.append(
            {
                "anchor_id": anchor_id,
                "sample_batch_id": row.get("sample_batch_id", ""),
                "is_dispute_forced": row.get("is_dispute_forced", ""),
                "dispute_target_ids": row.get("dispute_target_ids", ""),
                "reason": "no_scan_state" if state is None else "no_usable_dated_rounds",
            }
        )
    return out


def write_builder_report(undatable_rows: list[dict[str, str]], path: Path) -> None:
    """Write the machine-readable builder report (UNDATABLE-candidate anchors)."""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(BUILDER_REPORT_FIELDS))
        writer.writeheader()
        for r in undatable_rows:
            writer.writerow({k: r.get(k, "") for k in BUILDER_REPORT_FIELDS})


# ---------------------------------------------------------------------------
# Verdict manifest loader (UI JSON export -> VerdictRecord)


def _frame_from_export(d: dict) -> FrameIdentity:
    known = {f for f in FrameIdentity.__dataclass_fields__}  # type: ignore[attr-defined]
    return FrameIdentity(**{k: v for k, v in d.items() if k in known})


def _record_from_export(entry: dict) -> VerdictRecord:
    seed = entry.get("sampler_seed", None)
    seed_val = int(seed) if isinstance(seed, (int, float)) and seed is not None else (
        None if seed in (None, "") else int(seed)
    )
    return VerdictRecord(
        anchor_id=entry.get("anchor_id", ""),
        grid_id=entry.get("grid_id", ""),
        stratum=entry.get("stratum", ""),
        terminal_status=entry.get("terminal_status", ""),
        confidence=str(entry.get("confidence", "")),
        any_contradiction=str(entry.get("any_contradiction", "")),
        verdict=entry.get("verdict", ""),
        pipeline_interval_start=entry.get("pipeline_interval_start", ""),
        pipeline_interval_end=entry.get("pipeline_interval_end", ""),
        corrected_interval_start=entry.get("corrected_interval_start", "") or "",
        corrected_interval_end=entry.get("corrected_interval_end", "") or "",
        annotator_id=entry.get("annotator_id", ""),
        is_overlap=bool(entry.get("is_overlap", False)),
        is_dispute_forced=bool(entry.get("is_dispute_forced", False)),
        dispute_target_ids=list(entry.get("dispute_target_ids", []) or []),
        adjudication_seconds=float(entry.get("adjudication_seconds", 0.0) or 0.0),
        opened_ts_utc=entry.get("opened_ts_utc", ""),
        submitted_ts_utc=entry.get("submitted_ts_utc", ""),
        strip_content_hash=entry.get("strip_content_hash", ""),
        frames=[_frame_from_export(fr) for fr in entry.get("frames", [])],
        sampler_seed=seed_val,
        sample_batch_id=entry.get("sample_batch_id", ""),
        notes=entry.get("notes", ""),
    )


def load_verdict_manifest_export(json_path: Path) -> list[VerdictRecord]:
    """Map a browser-exported UI manifest JSON to ``VerdictRecord`` list.

    Only entries carrying a valid ``verdict`` (one of ``goldset_schema.VERDICTS``)
    are materialised — a half-open page that never chose a verdict emits nothing
    for that anchor. This is the round-trip source ISSUE-11 loads.
    """
    obj = json.loads(Path(json_path).read_text(encoding="utf-8"))
    out: list[VerdictRecord] = []
    for entry in obj.get("verdicts", []):
        if entry.get("verdict") not in gs.VERDICTS:
            continue
        out.append(_record_from_export(entry))
    return out


# ---------------------------------------------------------------------------
# CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--assignments", type=Path, required=True,
                   help="sample_assignments.csv written by WP-B (goldset_schema fields).")
    p.add_argument("--scan-states-dir", type=Path, required=True,
                   help="Directory of <anchor_id>.json scan states (fallback when a row omits scan_state_path).")
    p.add_argument("--rerender-dir", type=Path, default=None,
                   help="WP-C rerender/ dir (chips/<anchor_id>/...). Omit -> all frames placeholder.")
    p.add_argument("--tag", type=str, default=None,
                   help="Gold-set tag; output defaults to strips_dir(goldset_root(tag)).")
    p.add_argument("--output-dir", type=Path, default=None,
                   help="Override output dir for the strip HTML pages.")
    p.add_argument("--thumbnail-size", type=int, default=THUMBNAIL_SIZE)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if not args.assignments.exists():
        raise SystemExit(f"Assignments CSV not found: {args.assignments}")
    if not args.scan_states_dir.exists():
        raise SystemExit(f"Scan states dir not found: {args.scan_states_dir}")

    if args.output_dir is not None:
        out_dir = args.output_dir
    elif args.tag:
        out_dir = gs.strips_dir(gs.goldset_root(args.tag))
    else:
        raise SystemExit("Provide --output-dir or --tag for the strip output location.")

    rows = gs.read_sample_assignments(args.assignments)
    if not rows:
        raise SystemExit(f"No assignment rows in {args.assignments}")

    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row.get("sample_batch_id", ""), row.get("annotator_id", ""))
        groups.setdefault(key, []).append(row)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for (batch_id, annotator_id), grp in sorted(groups.items()):
        page = build_group_html(grp, args.scan_states_dir, args.rerender_dir, args.thumbnail_size)
        out_path = out_dir / f"verdicts_{batch_id}_{annotator_id}.html"
        out_path.write_text(page, encoding="utf-8")
        written.append(out_path)
        print(f"Wrote strip page ({len(grp)} anchors) -> {out_path} "
              f"({out_path.stat().st_size / 1024:.1f} KB)")

    undatable = collect_undatable_anchors(rows, args.scan_states_dir)
    report_path = out_dir / "builder_report.csv"
    write_builder_report(undatable, report_path)
    if undatable:
        ids = ", ".join(r["anchor_id"] for r in undatable)
        print(f"UNDATABLE candidates ({len(undatable)}): {ids}")
    print(f"Builder report -> {report_path} ({len(undatable)} UNDATABLE candidate(s))")
    print(f"Done: {len(written)} strip page(s) in {out_dir}")


if __name__ == "__main__":
    main()
