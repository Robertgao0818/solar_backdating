"""Human-readable roll-up for the CoJ cohort audit (ISSUE-09 WP-D).

Pure file-in / file-out: reads the join stage's long bit table
(``cohort_audit_bits.csv``), the gates stage's ``gates_report.json`` (which
carries the ``compute_cohort_gates`` output under ``gates`` and the
``coverage_report`` output under ``coverage``), and the build stage's
``dropped_units_summary.json``; writes

  * ``contradiction_rate_by_stratum.csv`` -- the HEADLINE per-stratum table
    (``coj_cohort_join.contradiction_rate_by_stratum`` serialised), and
  * ``cohort_report.md`` -- a human-readable roll-up (population, coverage,
    gate table, headline contradiction table, findings incl. clamp-finding
    anchor ids, dropped-units passthrough).

No network, no GPU, no model loads — every helper is exercised by
``tests/audit/test_coj_cohort_join.py`` on tmp CSVs. The orchestrator (WP-E)
calls :func:`build_cohort_report` after the gates stage.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from scripts.audit.coj_cohort_join import (
    NEGATIVE_CONTROL_STRATUM,
    RATE_FIELDNAMES,
    contradiction_rate_by_stratum,
)


# ---------------------------------------------------------------------------
# small I/O helpers
# ---------------------------------------------------------------------------


def load_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def _load_json(path: Path) -> dict:
    if path is None or not Path(path).exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _fmt_rate(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def _iter_dropped_rows(dropped_summary: dict) -> list[tuple[str, Any]]:
    """Flatten the dropped-units summary into ``(label, count)`` rows.

    ``dropped_units_summary.json`` mixes scalar counts with three nested-container
    values (``anchors_missing_bbox={count,anchor_ids}``, ``strata_counts``,
    ``layer_year_unit_counts``). Rendering those nested values straight into a
    single markdown cell prints raw Python dict/list reprs; instead expand each
    nested dict into ``parent.key`` sub-rows and collapse any list value to its
    length (so a long ``anchor_ids`` never blows up the cell).
    """
    rows: list[tuple[str, Any]] = []
    for reason, value in dropped_summary.items():
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (list, tuple, set)):
                    rows.append((f"{reason}.{k}", len(v)))
                elif isinstance(v, dict):
                    rows.append((f"{reason}.{k}", "; ".join(f"{kk}={vv}" for kk, vv in v.items())))
                else:
                    rows.append((f"{reason}.{k}", v))
        elif isinstance(value, (list, tuple, set)):
            rows.append((reason, len(value)))
        else:
            rows.append((reason, value))
    return rows


# ---------------------------------------------------------------------------
# headline CSV
# ---------------------------------------------------------------------------


def write_contradiction_rate_csv(bit_rows: list[dict], out_path: Path) -> Path:
    """Serialise ``contradiction_rate_by_stratum`` to CSV (the headline table)."""
    rate_rows = contradiction_rate_by_stratum(bit_rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(RATE_FIELDNAMES))
        w.writeheader()
        w.writerows(rate_rows)
    return out_path


# ---------------------------------------------------------------------------
# population summary (computed from the long bits table)
# ---------------------------------------------------------------------------


def population_summary(bit_rows: list[dict]) -> dict:
    anchors_by_stratum: dict[str, set[str]] = {}
    for b in bit_rows:
        anchors_by_stratum.setdefault(str(b.get("stratum")), set()).add(str(b.get("anchor_id")))
    per_stratum = {s: len(a) for s, a in sorted(anchors_by_stratum.items())}
    all_anchors = set()
    for a in anchors_by_stratum.values():
        all_anchors |= a
    nc = anchors_by_stratum.get(NEGATIVE_CONTROL_STRATUM, set())
    return {
        "n_anchors_total": len(all_anchors),
        "n_dated_anchors": len(all_anchors - nc),
        "n_negative_controls": len(nc),
        "n_planned_units": len(bit_rows),
        "anchors_per_stratum": per_stratum,
    }


# ---------------------------------------------------------------------------
# markdown renderer
# ---------------------------------------------------------------------------


def render_cohort_report_md(
    *,
    population: dict,
    coverage: dict,
    gates: dict,
    contradiction_rows: list[dict],
    dropped_summary: dict,
) -> str:
    lines: list[str] = []
    lines.append("# CoJ cohort true-date audit — roll-up (ISSUE-09)")
    lines.append("")
    lines.append(
        "Generated by `scripts/audit/coj_cohort_report.py`. Contradiction logic + "
        "margin rule are the frozen ISSUE-08 pilot primitives; known-sign "
        "expectation is per-unit (design amendment)."
    )
    lines.append("")

    # -- population --
    lines.append("## Population")
    lines.append("")
    lines.append(f"- Total anchors: **{population.get('n_anchors_total', 0)}** "
                 f"(dated {population.get('n_dated_anchors', 0)}, "
                 f"negative controls {population.get('n_negative_controls', 0)})")
    lines.append(f"- Planned fetch units: **{population.get('n_planned_units', 0)}**")
    lines.append("")
    lines.append("| stratum | anchors |")
    lines.append("|---|---|")
    for stratum, n in population.get("anchors_per_stratum", {}).items():
        lines.append(f"| {stratum} | {n} |")
    lines.append("")

    # -- coverage --
    lines.append("## Coverage")
    lines.append("")
    cov = coverage or {}
    lines.append(
        f"- Covered {cov.get('covered_anchors', 0)} / {cov.get('dated_anchors', 0)} "
        f"dated anchors = **{_fmt_rate(cov.get('coverage'))}** "
        f"({'PASS' if cov.get('passes_95pct') else 'FAIL'} at ≥0.95)"
    )
    lines.append(f"- Failed / not-fetched units: **{cov.get('n_failed_units', 0)}** "
                 f"(enumerated in `fetch_failures.csv`)")
    lines.append("")

    # -- gate table --
    lines.append("## Self-gates")
    lines.append("")
    gate_a = (gates or {}).get("gate_a", {})
    lines.append(f"### Gate (a) — present-side known-sign agreement "
                 f"({'PASS' if gate_a.get('passes') else 'FAIL'})")
    lines.append("")
    lines.append("| stratum | year | n | agree | rate | passes |")
    lines.append("|---|---|---|---|---|---|")
    for cell in gate_a.get("cells", []):
        lines.append(
            f"| {cell.get('stratum')} | {cell.get('year')} | {cell.get('n')} | "
            f"{cell.get('agree')} | {_fmt_rate(cell.get('rate'))} | "
            f"{'PASS' if cell.get('passes') else 'FAIL'} |"
        )
    lines.append("")

    gate_nc = (gates or {}).get("gate_nc", {})
    ci = gate_nc.get("wilson_ci") or ("", "")
    lines.append(f"### Gate (nc) — negative-control false-present "
                 f"({'PASS' if gate_nc.get('passes') else 'FAIL'})")
    lines.append("")
    lines.append(
        f"- Scored {gate_nc.get('n_scored_controls', 0)} / {gate_nc.get('n_controls', 0)} "
        f"controls (rate {_fmt_rate(gate_nc.get('scored_rate'))}; evaluable "
        f"{'yes' if gate_nc.get('evaluable') else 'NO — gate not exercised'} "
        f"at ≥ {gate_nc.get('scored_threshold')})"
    )
    lines.append(
        f"- {gate_nc.get('n_false_present', 0)} / {gate_nc.get('n_controls', 0)} controls "
        f"show a high-margin present bit (rate {_fmt_rate(gate_nc.get('rate'))}, "
        f"Wilson CI [{_fmt_rate(ci[0])}, {_fmt_rate(ci[1])}]); "
        f"threshold ≤ {gate_nc.get('threshold')}"
    )
    fp_ids = gate_nc.get("false_present_anchor_ids") or []
    if fp_ids:
        lines.append(f"- False-present control ids: {', '.join(map(str, fp_ids))}")
    lines.append("")

    gate_b = (gates or {}).get("gate_b", {})
    lines.append("### Gate (b) — within-audit monotonicity noise floor")
    lines.append("")
    lines.append(f"- {gate_b.get('violations', 0)} anchors with a high-margin "
                 f"present@earlier ∧ absent@later pair (noise floor, not a hard gate)")
    b_ids = gate_b.get("violation_anchor_ids") or []
    if b_ids:
        lines.append(f"- Anchor ids: {', '.join(map(str, b_ids))}")
    lines.append("")

    # -- headline contradiction table --
    lines.append("## Contradiction rate by stratum (headline)")
    lines.append("")
    lines.append("| level | stratum | year | flag | denom | contra | rate | CI |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in contradiction_rows:
        ci_lo = _fmt_rate(r.get("ci_low"))
        ci_hi = _fmt_rate(r.get("ci_high"))
        lines.append(
            f"| {r.get('level')} | {r.get('stratum')} | {r.get('year')} | "
            f"{r.get('flag')} | {r.get('n_denominator')} | {r.get('n_numerator')} | "
            f"{_fmt_rate(r.get('rate'))} | [{ci_lo}, {ci_hi}] |"
        )
    lines.append("")

    # -- findings --
    lines.append("## Findings")
    lines.append("")
    clamp = (gates or {}).get("clamp_findings", {})
    lines.append(f"- Clamp-monotonicity findings (high-margin present@2023 that "
                 f"contradict a clamped interval, excl. excluded statuses): "
                 f"**{clamp.get('count', 0)}**")
    clamp_ids = clamp.get("anchor_ids") or []
    if clamp_ids:
        lines.append(f"- Clamp-finding anchor ids: {', '.join(map(str, clamp_ids))}")
    total_contra = sum(
        int(r.get("n_numerator") or 0)
        for r in contradiction_rows
        if r.get("level") == "stratum"
    )
    lines.append(f"- Anchors with ≥1 contradiction (sum over strata): **{total_contra}**")
    lines.append("")

    # -- dropped-units passthrough --
    lines.append("## Deliberately dropped units (passthrough)")
    lines.append("")
    if dropped_summary:
        lines.append("| reason | count |")
        lines.append("|---|---|")
        for reason, count in _iter_dropped_rows(dropped_summary):
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("- (no `dropped_units_summary.json` present)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def build_cohort_report(
    *,
    output_root: Path,
    bits_csv: Path | None = None,
    gates_json: Path | None = None,
    dropped_summary_json: Path | None = None,
) -> dict:
    """Render the headline CSV + roll-up markdown from the join/gates outputs.

    Reads (defaults relative to ``output_root``): ``cohort_audit_bits.csv``,
    ``gates_report.json`` (``{gates, coverage}``), ``dropped_units_summary.json``.
    Writes ``contradiction_rate_by_stratum.csv`` + ``cohort_report.md``. Returns
    the written paths + the population summary.
    """
    output_root = Path(output_root)
    bits_csv = Path(bits_csv) if bits_csv else output_root / "cohort_audit_bits.csv"
    gates_json = Path(gates_json) if gates_json else output_root / "gates_report.json"
    dropped_summary_json = (
        Path(dropped_summary_json) if dropped_summary_json
        else output_root / "dropped_units_summary.json"
    )

    bit_rows = load_rows(bits_csv)
    report_bundle = _load_json(gates_json)
    gates = report_bundle.get("gates", report_bundle)  # tolerate a bare gates dict
    coverage = report_bundle.get("coverage", {})
    dropped_summary = _load_json(dropped_summary_json)

    rate_csv = output_root / "contradiction_rate_by_stratum.csv"
    write_contradiction_rate_csv(bit_rows, rate_csv)

    population = population_summary(bit_rows)
    contradiction_rows = contradiction_rate_by_stratum(bit_rows)
    md = render_cohort_report_md(
        population=population,
        coverage=coverage,
        gates=gates,
        contradiction_rows=contradiction_rows,
        dropped_summary=dropped_summary,
    )
    md_path = output_root / "cohort_report.md"
    md_path.write_text(md)

    return {
        "contradiction_rate_csv": rate_csv,
        "cohort_report_md": md_path,
        "population": population,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--bits-csv", type=Path, default=None)
    ap.add_argument("--gates-json", type=Path, default=None)
    ap.add_argument("--dropped-summary-json", type=Path, default=None)
    args = ap.parse_args()
    result = build_cohort_report(
        output_root=args.output_root,
        bits_csv=args.bits_csv,
        gates_json=args.gates_json,
        dropped_summary_json=args.dropped_summary_json,
    )
    print(f"[report] -> {result['cohort_report_md']}")
    print(f"[report] -> {result['contradiction_rate_csv']}")


if __name__ == "__main__":
    main()
