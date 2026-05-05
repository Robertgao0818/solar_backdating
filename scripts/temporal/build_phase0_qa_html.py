#!/usr/bin/env python3
"""Phase-0 QA HTML — pure display of scan_state.json + install_intervals.csv.

Renders one row per anchor (header: status / interval / confidence) followed
by per-round chip strips, each chip captioned with capture_date and the Gemini
pv_present/quality verdict. Thumbnails are embedded as inline base64 PNGs so
the page is portable (~few MB total for 10 anchors). The HTML re-runs nothing
— it consumes whatever the orchestrator and infer step already wrote.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import ensure_review_png
from scripts.temporal.scan_state import RoundResult, ScanState, load_scan_state

DEFAULT_SCAN_STATES_DIR = (
    Path.home() / "zasolar_data/geid_temporal/jhb_vexcel10_smoke/scan_states"
)
# --intervals-csv and --output defaults are resolved at runtime from
# <scan-states-dir>.parent so a custom --scan-states-dir doesn't silently
# read from / write into the JHB smoke run directory.
THUMBNAIL_SIZE = 200
PLACEHOLDER_PIXEL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
    "DUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scan-states-dir", type=Path, default=DEFAULT_SCAN_STATES_DIR)
    parser.add_argument(
        "--intervals-csv",
        type=Path,
        default=None,
        help="Install intervals CSV. Default: <scan-states-dir>/../install_intervals.csv "
        "(co-located with the scan run). If missing on disk, headers fall back to scan_state alone.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output HTML path. Default: <scan-states-dir>/../phase0_qa.html",
    )
    parser.add_argument("--thumbnail-size", type=int, default=THUMBNAIL_SIZE)
    parser.add_argument(
        "--max-anchors",
        type=int,
        help="Optional cap on anchor count for quick iteration",
    )
    parser.add_argument(
        "--allow-load-failures",
        action="store_true",
        help="Continue and emit HTML even if some scan_state files fail to load. "
        "Default behavior is to abort, which prevents producing a QA page that silently "
        "drops anchors due to spec_version mismatch or corrupt JSON.",
    )
    return parser.parse_args()


def load_intervals(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8") as fh:
        return {row["anchor_id"]: row for row in csv.DictReader(fh)}


def thumbnail_data_url(image_path: Path | None, size: int) -> str:
    if image_path is None or not image_path.exists() or image_path.stat().st_size == 0:
        return PLACEHOLDER_PIXEL
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img.thumbnail((size, size))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception:  # noqa: BLE001
        return PLACEHOLDER_PIXEL


def _resolve_review_png(chip_path: str) -> Path | None:
    if not chip_path:
        return None
    p = Path(chip_path)
    if not p.exists():
        return None
    if p.suffix.lower() in (".tif", ".tiff"):
        try:
            return ensure_review_png(p)
        except Exception:  # noqa: BLE001
            return p
    return p


def _verdict_class(result: RoundResult) -> str:
    if result.quality_flag != "usable" or result.pv_present is None:
        return "unusable"
    return "present" if result.pv_present else "absent"


def _verdict_label(result: RoundResult) -> str:
    if result.quality_flag != "usable" or result.pv_present is None:
        return f"unusable ({result.decision_source})"
    return "present" if result.pv_present else "absent"


def _confidence_class(value: str) -> str:
    return value if value in ("high", "medium", "low") else "low"


def _interval_summary(state: ScanState, interval_row: dict[str, str]) -> str:
    if interval_row:
        start = interval_row.get("install_interval_start", "") or "—"
        end = interval_row.get("install_interval_end", "") or "—"
        mid = interval_row.get("install_mid_estimate", "") or "—"
        conf = interval_row.get("confidence", "") or "—"
        return f"interval=[{start}, {end}]  mid={mid}  confidence={conf}"
    return f"status={state.status} (no interval row available)"


def render_round(rnd_index: int, rnd, thumbnail_size: int) -> str:
    out: list[str] = [
        f"<div class='round'>",
        f"<div class='round-head'>R{rnd.round_id} {html.escape(rnd.round_type)} "
        f"<span class='muted'>window=[{html.escape(rnd.window_start_date or '—')}, "
        f"{html.escape(rnd.window_end_date or '—')}]</span></div>",
        "<div class='chip-strip'>",
    ]
    for result in sorted(rnd.results, key=lambda r: r.capture_date):
        verdict = _verdict_class(result)
        review_png = _resolve_review_png(result.chip_path)
        thumb = thumbnail_data_url(review_png, thumbnail_size)
        evidence = html.escape((result.evidence or "")[:200])
        out.append(
            f"<figure class='chip {verdict}'>"
            f"<img src='{thumb}' alt='{html.escape(result.capture_date)}' />"
            f"<figcaption>"
            f"<div class='cap-date'>{html.escape(result.capture_date)}</div>"
            f"<div class='cap-verdict'>{html.escape(_verdict_label(result))}</div>"
            f"<div class='cap-zoom muted'>z={result.actual_zoom or '—'}</div>"
            f"<div class='cap-evidence muted'>{evidence}</div>"
            f"</figcaption>"
            f"</figure>"
        )
    out.append("</div></div>")
    return "".join(out)


def render_anchor(state: ScanState, interval_row: dict[str, str], thumbnail_size: int) -> str:
    confidence = interval_row.get("confidence", "") if interval_row else ""
    notes_text = interval_row.get("notes", "") if interval_row else state.notes
    parts: list[str] = [
        f"<section class='anchor'>",
        f"<header class='anchor-head'>",
        f"<div class='anchor-title'>{html.escape(state.anchor_id)}</div>",
        f"<div class='anchor-meta'>"
        f"<span class='status status-{html.escape(state.status)}'>{html.escape(state.status)}</span>"
        f"<span class='conf conf-{_confidence_class(confidence)}'>{html.escape(confidence or '—')}</span>"
        f"</div>",
        f"</header>",
        f"<div class='anchor-summary muted'>{html.escape(_interval_summary(state, interval_row))}</div>",
    ]
    if notes_text:
        parts.append(f"<div class='anchor-notes'>{html.escape(notes_text)}</div>")
    for i, rnd in enumerate(state.rounds):
        parts.append(render_round(i, rnd, thumbnail_size))
    parts.append("</section>")
    return "".join(parts)


CSS = """
:root{color-scheme:dark;--bg:#0d1117;--panel:#161b22;--line:#30363d;--text:#e6edf3;
  --muted:#7d8590;--green:#238636;--red:#da3633;--amber:#9e6a03;--blue:#2f81f7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;font-size:13px}
.page-head{position:sticky;top:0;z-index:5;background:#010409;border-bottom:1px solid var(--line);padding:10px 14px}
.page-head h1{margin:0;font-size:16px}
.page-head .muted{font-size:12px}
.wrap{padding:14px 16px 40px}
.anchor{border:1px solid var(--line);background:var(--panel);border-radius:6px;margin:0 0 14px;overflow:hidden}
.anchor-head{padding:8px 10px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.anchor-title{font-weight:700;font-size:14px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.anchor-meta{display:flex;gap:8px;align-items:center}
.status{padding:2px 8px;border-radius:10px;font-size:11px;background:#21262d;border:1px solid var(--line)}
.status-done_appears{background:#0e3b1f;border-color:var(--green)}
.status-done_installed_during_census{background:#3b1f0e;border-color:var(--amber)}
.status-done_already_present_before_geid_history{background:#1c2c3d;border-color:var(--blue)}
.status-done_ambiguous_nonmonotonic,.status-done_ambiguous_gemini_failed,.status-done_ambiguous_no_recent_anchor,.status-done_ambiguous_orchestrator_error{background:#3d1f1f;border-color:var(--red)}
.conf{padding:2px 8px;border-radius:10px;font-size:11px;background:#21262d;border:1px solid var(--line)}
.conf-high{background:#0e3b1f;border-color:var(--green)}
.conf-medium{background:#3b2f0e;border-color:var(--amber)}
.conf-low{background:#3d1f1f;border-color:var(--red)}
.anchor-summary{padding:6px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.anchor-notes{padding:6px 10px;border-top:1px dashed var(--line);font-size:12px;color:#f0c36d}
.round{border-top:1px solid var(--line);padding:8px 10px}
.round-head{font-size:12px;color:var(--muted);margin-bottom:6px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.chip-strip{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px}
.chip{margin:0;border:2px solid #30363d;border-radius:5px;padding:4px;background:#0d1117;min-width:160px;max-width:200px}
.chip.present{border-color:var(--green)}
.chip.absent{border-color:var(--red)}
.chip.unusable{border-color:var(--amber)}
.chip img{width:100%;height:auto;aspect-ratio:1/1;object-fit:cover;display:block;border-radius:3px;background:#000}
.chip figcaption{margin-top:4px;font-size:11px;line-height:1.35}
.cap-date{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:600}
.cap-verdict{margin-top:1px}
.cap-zoom{margin-top:1px;font-size:10px}
.cap-evidence{margin-top:2px;font-size:10px;max-height:34px;overflow:hidden}
.muted{color:var(--muted)}
"""


def render_html(states: list[tuple[ScanState, dict[str, str], Path]], thumbnail_size: int) -> str:
    by_status: dict[str, int] = {}
    for state, _row, _path in states:
        by_status[state.status] = by_status.get(state.status, 0) + 1
    summary_bits = [f"{html.escape(s)}={c}" for s, c in sorted(by_status.items())]
    summary = " · ".join(summary_bits) if summary_bits else "no anchors"
    head = (
        "<div class='page-head'>"
        f"<h1>Phase-0 QA</h1>"
        f"<div class='muted'>{html.escape(str(len(states)))} anchors &middot; {summary}</div>"
        "</div>"
    )
    body_parts: list[str] = ["<div class='wrap'>"]
    for state, interval_row, _state_path in states:
        body_parts.append(render_anchor(state, interval_row, thumbnail_size))
    body_parts.append("</div>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Phase-0 QA — solar_backdating</title>"
        f"<style>{CSS}</style></head><body>"
        f"{head}{''.join(body_parts)}"
        "</body></html>"
    )


def main() -> None:
    args = parse_args()
    if not args.scan_states_dir.exists():
        raise SystemExit(f"Scan states dir not found: {args.scan_states_dir}")

    intervals_csv = (
        args.intervals_csv
        if args.intervals_csv is not None
        else args.scan_states_dir.parent / "install_intervals.csv"
    )
    output_html = (
        args.output
        if args.output is not None
        else args.scan_states_dir.parent / "phase0_qa.html"
    )

    intervals = load_intervals(intervals_csv)
    state_files = sorted(args.scan_states_dir.glob("*.json"))
    if args.max_anchors is not None:
        state_files = state_files[: args.max_anchors]
    if not state_files:
        raise SystemExit(f"No scan_state JSON files in {args.scan_states_dir}")

    states: list[tuple[ScanState, dict[str, str], Path]] = []
    load_failures: list[tuple[Path, str]] = []
    for state_path in state_files:
        try:
            state = load_scan_state(state_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] failed to load {state_path}: {exc}", file=sys.stderr)
            load_failures.append((state_path, str(exc)))
            continue
        if state is None:
            continue
        interval_row = intervals.get(state.anchor_id, {})
        states.append((state, interval_row, state_path))

    if load_failures and not args.allow_load_failures:
        raise SystemExit(
            f"{len(load_failures)} scan_state file(s) failed to load: "
            f"{[str(p) for p, _ in load_failures]}. "
            f"Aborting to avoid emitting a QA page with missing anchors. "
            f"Pass --allow-load-failures to override."
        )
    if not states:
        raise SystemExit(
            f"Zero scan_states rendered from {len(state_files)} file(s); refusing to emit empty HTML."
        )

    html_text = render_html(states, args.thumbnail_size)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html_text, encoding="utf-8")
    print(f"Wrote Phase-0 QA HTML ({len(states)} anchors) -> {output_html}")
    print(f"Page size: {output_html.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
