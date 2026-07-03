#!/usr/bin/env python3
"""Re-summarize the achieved-zoom distribution of scan-state corpora (ISSUE-18 / D17).

`RoundResult.actual_zoom` records the ladder rung GEHI actually served per scored
chip, but no scan summary ever surfaces the mix — the JHB census achieved z20 on
only 72.5% of its chip-dates (26.8% z19, 0.7% z18) and that fact was recoverable
only by hand-parsing 15k scan-state JSONs. This CLI streams one or more
`--scan-states-dir` directories and aggregates the achieved-zoom histogram
(per-dir and combined): count + percentage per rung, `None` -> "unknown", with a
`decision_source` breakdown when trivially available.

Design contract:
- Streams file-by-file: at most one scan-state JSON is held in memory at a time,
  so a 15k+ corpus summarizes without loading everything at once.
- Prefers `scan_state.load_scan_state`; on a spec-version `ValueError` it falls
  back to raw-json traversal so a future `SPEC_VERSION` bump can never make an
  existing production corpus unreadable. A corrupt file is counted, skipped, and
  never crashes the run.

The zoom stratification that D2's emission matrices key on MUST be the achieved
zoom this tool reports, never the requested ladder rung — see
`docs/resolution_provenance.md`.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.scan_state import load_scan_state

UNKNOWN_LABEL = "unknown"


def zoom_label(actual_zoom: object) -> str:
    """Map an achieved-zoom value to a histogram bucket label.

    `None` (all ladder rungs failed / no chip scored) -> "unknown"; an int-ish
    value -> "z{n}"; anything uncoercible -> "unknown".
    """
    if actual_zoom is None:
        return UNKNOWN_LABEL
    try:
        return f"z{int(actual_zoom)}"
    except (TypeError, ValueError):
        return UNKNOWN_LABEL


def _zoom_sort_key(label: str) -> tuple[int, int]:
    """Sort key placing higher zooms first (z20, z19, ...) and "unknown" last."""
    if label == UNKNOWN_LABEL:
        return (1, 0)
    try:
        return (0, -int(label[1:]))
    except (ValueError, IndexError):
        return (1, 0)


@dataclass
class ZoomHistogram:
    """Streaming accumulator for one corpus (or the combined view)."""

    label: str = ""
    n_files: int = 0
    n_results: int = 0
    n_parse_fallback: int = 0
    n_parse_error: int = 0
    zoom_counts: Counter = field(default_factory=Counter)
    decision_source_counts: Counter = field(default_factory=Counter)
    zoom_by_decision: dict[str, Counter] = field(default_factory=dict)

    def add_result(self, actual_zoom: object, decision_source: object) -> None:
        self.n_results += 1
        zl = zoom_label(actual_zoom)
        self.zoom_counts[zl] += 1
        ds = str(decision_source or "").strip() or UNKNOWN_LABEL
        self.decision_source_counts[ds] += 1
        self.zoom_by_decision.setdefault(zl, Counter())[ds] += 1

    def merge(self, other: ZoomHistogram) -> None:
        self.n_files += other.n_files
        self.n_results += other.n_results
        self.n_parse_fallback += other.n_parse_fallback
        self.n_parse_error += other.n_parse_error
        self.zoom_counts.update(other.zoom_counts)
        self.decision_source_counts.update(other.decision_source_counts)
        for zl, counter in other.zoom_by_decision.items():
            self.zoom_by_decision.setdefault(zl, Counter()).update(counter)


def _iter_raw_records(raw: dict) -> list[tuple[object, object]]:
    records: list[tuple[object, object]] = []
    for rnd in raw.get("rounds", []) or []:
        for res in rnd.get("results", []) or []:
            records.append((res.get("actual_zoom"), res.get("decision_source", "")))
    return records


def add_scan_state_file(hist: ZoomHistogram, path: Path) -> None:
    """Fold one scan-state file into `hist`, streaming (no cross-file retention).

    Canonical load first; on a spec-version `ValueError` fall back to raw-json
    traversal (counted as `n_parse_fallback`). Any other read/parse error is
    counted as `n_parse_error` and skipped so the corpus stays summarizable.
    """
    hist.n_files += 1
    try:
        state = load_scan_state(path)
    except ValueError:
        hist.n_parse_fallback += 1
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - one bad file must not abort the corpus
            hist.n_parse_error += 1
            return
        for actual_zoom, decision_source in _iter_raw_records(raw):
            hist.add_result(actual_zoom, decision_source)
        return
    except Exception:  # noqa: BLE001 - corrupt JSON / unexpected schema drift
        hist.n_parse_error += 1
        return
    if state is None:
        return
    for rnd in state.rounds:
        for res in rnd.results:
            hist.add_result(res.actual_zoom, res.decision_source)


def summarize_dir(scan_states_dir: Path) -> ZoomHistogram:
    """Stream every `*.json` under `scan_states_dir` into a fresh histogram."""
    hist = ZoomHistogram(label=str(scan_states_dir))
    for path in sorted(scan_states_dir.glob("*.json")):
        add_scan_state_file(hist, path)
    return hist


def report_dict(hist: ZoomHistogram) -> dict:
    """JSON-serializable histogram with per-rung counts and percentages."""
    total = hist.n_results
    zoom_sorted = sorted(hist.zoom_counts.items(), key=lambda kv: _zoom_sort_key(kv[0]))
    return {
        "label": hist.label,
        "n_files": hist.n_files,
        "n_results": hist.n_results,
        "n_parse_fallback": hist.n_parse_fallback,
        "n_parse_error": hist.n_parse_error,
        "zoom_counts": {k: v for k, v in zoom_sorted},
        "zoom_pct": {
            k: (round(100.0 * v / total, 4) if total else 0.0) for k, v in zoom_sorted
        },
        "decision_source_counts": dict(
            sorted(hist.decision_source_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "zoom_by_decision": {
            zl: dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))
            for zl, counter in sorted(hist.zoom_by_decision.items(), key=lambda kv: _zoom_sort_key(kv[0]))
        },
    }


def format_table(hist: ZoomHistogram) -> str:
    total = hist.n_results
    lines = [
        f"== {hist.label} ==",
        f"  files={hist.n_files:,}  results={hist.n_results:,}  "
        f"fallback={hist.n_parse_fallback:,}  parse_error={hist.n_parse_error:,}",
        "  achieved-zoom distribution:",
    ]
    for label, count in sorted(hist.zoom_counts.items(), key=lambda kv: _zoom_sort_key(kv[0])):
        pct = 100.0 * count / total if total else 0.0
        lines.append(f"    {label:>8}: {count:>12,}  ({pct:5.1f}%)")
    if hist.decision_source_counts:
        lines.append("  decision_source:")
        for ds, count in sorted(hist.decision_source_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            pct = 100.0 * count / total if total else 0.0
            lines.append(f"    {ds:>18}: {count:>12,}  ({pct:5.1f}%)")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--scan-states-dir",
        type=Path,
        action="append",
        required=True,
        dest="scan_states_dirs",
        help="Directory of *.json scan states. Repeat for multiple corpora (per-dir + combined).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the histogram (per-dir + combined) as JSON.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    per_dir: list[ZoomHistogram] = []
    combined = ZoomHistogram(label="COMBINED")
    for scan_states_dir in args.scan_states_dirs:
        if not scan_states_dir.exists():
            raise SystemExit(f"scan-states dir not found: {scan_states_dir}")
        hist = summarize_dir(scan_states_dir)
        per_dir.append(hist)
        combined.merge(hist)
        print(format_table(hist))
        print()
    if len(per_dir) > 1:
        print(format_table(combined))
        print()
    if args.output is not None:
        payload = {
            "per_dir": [report_dict(h) for h in per_dir],
            "combined": report_dict(combined),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote histogram JSON -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
