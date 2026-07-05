#!/usr/bin/env python3
"""Interval-level Turnbull fractional cohort-year table (ISSUE-22 switch 2).

Reads the production cohort's interval-censored install observations (the
``intervals_prod.csv`` mapped product of the ISSUE-03 gate run — one
``CensoringInterval`` per anchor, already derived from
``infer_install_dates`` scan states) and fits the deterministic Turnbull
NPMLE (``estimators.survival.fit_turnbull``) over calendar-year atoms. From
the fit it emits the fractional cohort-year distribution via
``eval.aggregate.cohort_year_histogram_from_fit`` (i.e. ``TurnbullFit.year_mass``).

This is the **narrow-caliber, interval-level Turnbull route** required by
PRD-AMENDMENT-P1 §A2/(2): it aggregates EXISTING intervals; it does NOT
re-decode anchors, does NOT rebuild the cohort frame, and does NOT touch
``infer_install_dates`` / ``run_adaptive_scan``. The full-cohort per-anchor
re-decode is the deferred wide caliber (gated on ISSUE-10/11) and is out of
scope here.

Outputs (data products to ``~/zasolar_data/geid_temporal/<run>/``, never git):

- ``cohort_year_fractional_table.csv`` — cohort-level fractional year table:
  one row per calendar year with ``year_mass_fraction`` (the Turnbull PMF
  atom ``pi_y``), ``cohort_count`` (``pi_y * N``), and ascending cumulative
  columns. A ``BEYOND`` row is emitted only if ``beyond_mass > 0``.
- ``cohort_year_fractional_table.md`` — the same table with the A5 caveats +
  0.067 disclosure embedded VERBATIM in the header (non-negotiable per §A5).
- ``per_anchor_year_fractions.csv`` — optional interval-level per-anchor
  fractional detail: each anchor's fitted year posterior (the Turnbull E-step
  ``w_iy = pi_y a_iy / sum_k pi_k a_ik`` under the converged ``pi``). Column
  sums equal the cohort ``cohort_count`` by the M-step fixed point, giving a
  free cross-check.
- ``fit_metadata.json`` — Turnbull fit metadata + input provenance (source
  path + sha256 + estimator id + decision refs).

The year-mass semantics are IDENTICAL to ISSUE-03 AC2's ``year_mass`` channel
(same ``fit_turnbull`` -> ``cohort_year_histogram_from_fit`` path); this script
only scales the PMF to the cohort size and tabulates it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

from solar_backdating.estimators.survival import (
    CensoringInterval,
    TurnbullFit,
    _interval_year_weights,  # single-source interval->year compatibility a_iy
    fit_turnbull,
)
from solar_backdating.eval.aggregate import BEYOND, cohort_year_histogram_from_fit

# --------------------------------------------------------------------------- #
# Canonical inputs / provenance pins (ISSUE-22 forensic map).
# --------------------------------------------------------------------------- #
DEFAULT_INTERVALS_CSV = Path.home() / (
    "zasolar_data/geid_temporal/issue03_gates_20260704/intervals_prod.csv"
)
DEFAULT_OUT_DIR = Path.home() / "zasolar_data/geid_temporal/cohort_fractional_year_20260705"

ESTIMATOR_ID = "turnbull_npmle_interval_level"
DECISION_REFS = [
    "docs/replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md",
    "docs/replan_v2/DECISION-A-estimator-adoption-2026-07-04.md",
]
# Plausible imagery-coverage window for the JHB GEID/GEHI vintage stacks; used
# only as a sanity assertion (no mass may fall outside the observed years).
IMAGERY_COVERAGE_YEARS = (2000, 2025)

# --- A5 caveats + 0.067 disclosure, VERBATIM per PRD-AMENDMENT-P1 §A5. ------ #
A5_CAVEATS_MD = """\
> **A5 caveats — MUST travel with this fractional deliverable (verbatim, non-negotiable):**
>
> - The **C5 2024 prior-mass dip** (0.0135 « 2023's 0.656) is a **Vexcel
>   flight-date right-censoring artifact — never a market signal**. Do not read
>   it as an install-rate decline.
> - Survival curves are **grid-marginalised**: censoring is only conditionally
>   non-informative given grid (per-grid median cadence spans 30.5–624.5 d over
>   335 grids; cadence-stratified S(2021) spreads 0.081 vs 0.908). Read cohort
>   curves as grid-marginalised, not as a clean population survival function.
> - **D11 first-visible-appearance scope is unchanged.** Posterior mass is over
>   **first-visible-appearance epochs under imagery-cadence censoring**; it
>   claims **no** new precision about physical install dates (see §9.4).
>
> **0.067 disclosure:** The one **0.067** delivered-caliber (survival/fractional)
> pair that still exceeds the old absolute band is **disclosed** on every
> deliverable until the P3 re-band (§5) resolves it.
"""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    return date.fromisoformat(s)


def load_intervals(csv_path: Path) -> list[CensoringInterval]:
    """Reconstruct ``CensoringInterval``s from the ISSUE-03 ``intervals_prod.csv``.

    Columns: anchor_id,grid_id,status,kind,lower,upper,recovered,n_usable,
    cadence_gap_days. Only lower/upper/kind drive the fit; the rest are carried
    for the per-anchor detail and provenance.
    """
    out: list[CensoringInterval] = []
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f):
            gap = (row.get("cadence_gap_days") or "").strip()
            out.append(
                CensoringInterval(
                    lower=_parse_date(row["lower"]),
                    upper=_parse_date(row["upper"]),
                    kind=row["kind"].strip(),
                    anchor_id=row.get("anchor_id", "").strip(),
                    grid_id=row.get("grid_id", "").strip(),
                    status=row.get("status", "").strip(),
                    recovered=(row.get("recovered", "").strip() in ("1", "True", "true")),
                    n_usable=int(row["n_usable"]) if (row.get("n_usable") or "").strip() else 0,
                    cadence_gap_days=float(gap) if gap else None,
                )
            )
    return out


def cohort_table(fit: TurnbullFit, n: int) -> list[dict]:
    """Cohort-scale fractional year rows from the Turnbull PMF.

    ``cohort_year_histogram_from_fit`` returns the PMF (atoms sum to 1.0);
    scale by ``n`` for cohort counts and accumulate ascending.
    """
    pmf: Counter = cohort_year_histogram_from_fit(fit)
    years = sorted(k for k in pmf if not isinstance(k, str))
    rows: list[dict] = []
    cum_frac = 0.0
    cum_count = 0.0
    for y in years:
        frac = pmf[y]
        count = frac * n
        cum_frac += frac
        cum_count += count
        rows.append(
            {
                "year": y,
                "year_mass_fraction": frac,
                "cohort_count": count,
                "cumulative_fraction": cum_frac,
                "cumulative_count": cum_count,
            }
        )
    if pmf.get(BEYOND, 0.0) > 0.0:
        frac = pmf[BEYOND]
        count = frac * n
        cum_frac += frac
        cum_count += count
        rows.append(
            {
                "year": BEYOND,
                "year_mass_fraction": frac,
                "cohort_count": count,
                "cumulative_fraction": cum_frac,
                "cumulative_count": cum_count,
            }
        )
    return rows


def per_anchor_fractions(
    intervals: list[CensoringInterval], fit: TurnbullFit
) -> tuple[list[dict], Counter]:
    """Interval-level per-anchor fitted year posterior (Turnbull E-step).

    For each anchor: ``w_iy = pi_y a_iy / sum_k pi_k a_ik`` where ``a_iy`` is
    ``_interval_year_weights`` (the module's single-source compatibility) and
    ``pi`` the converged fit. Returns per-anchor rows and the column-sum
    Counter (== cohort counts at the M-step fixed point — the cross-check).
    """
    if not fit.year_mass:
        return [], Counter()
    years = [y for y, _ in fit.year_mass]
    pi = {y: m for y, m in fit.year_mass}
    pi_beyond = fit.beyond_mass
    col_sums: Counter = Counter()
    rows: list[dict] = []
    for iv in intervals:
        yw, bw = _interval_year_weights(iv, years)
        contrib = {y: pi[y] * w for y, w in yw.items() if pi[y] > 0.0 and w > 0.0}
        b_contrib = pi_beyond * bw if (pi_beyond > 0.0 and bw > 0.0) else 0.0
        denom = sum(contrib.values()) + b_contrib
        row = {
            "anchor_id": iv.anchor_id,
            "grid_id": iv.grid_id,
            "status": iv.status,
            "kind": iv.kind,
            "lower": iv.lower.isoformat() if iv.lower else "",
            "upper": iv.upper.isoformat() if iv.upper else "",
        }
        if denom <= 0.0:
            # degenerate (no compatible atom carries mass) — record zeros
            for y in years:
                row[str(y)] = 0.0
            row[BEYOND] = 0.0
            rows.append(row)
            continue
        for y in years:
            frac = contrib.get(y, 0.0) / denom
            row[str(y)] = frac
            if frac:
                col_sums[y] += frac
        b_frac = b_contrib / denom
        row[BEYOND] = b_frac
        if b_frac:
            col_sums[BEYOND] += b_frac
        rows.append(row)
    return rows, col_sums


def write_cohort_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "year",
                "year_mass_fraction",
                "cohort_count",
                "cumulative_fraction",
                "cumulative_count",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r["year"],
                    f"{r['year_mass_fraction']:.10f}",
                    f"{r['cohort_count']:.4f}",
                    f"{r['cumulative_fraction']:.10f}",
                    f"{r['cumulative_count']:.4f}",
                ]
            )


def write_per_anchor_csv(path: Path, rows: list[dict], years: list[int]) -> None:
    fields = ["anchor_id", "grid_id", "status", "kind", "lower", "upper"]
    fields += [str(y) for y in years] + [BEYOND]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{r[k]:.8f}" if isinstance(r.get(k), float) else r.get(k, "")) for k in fields})


def render_md(
    rows: list[dict],
    *,
    fit: TurnbullFit,
    n: int,
    src: Path,
    src_sha: str,
    sanity: dict,
) -> str:
    lines: list[str] = []
    lines.append("# Cohort fractional install-year table (interval-level Turnbull)")
    lines.append("")
    lines.append(
        "ISSUE-22 switch (2) — narrow caliber. Aggregates the EXISTING production "
        "cohort intervals (no per-anchor re-decode, no cohort-frame rebuild) with "
        "the deterministic Turnbull NPMLE, then tabulates the fractional cohort-year "
        "distribution (`year_mass` — same channel as ISSUE-03 AC2)."
    )
    lines.append("")
    lines.append(A5_CAVEATS_MD)
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- estimator_id: `{ESTIMATOR_ID}`")
    lines.append(f"- source intervals: `{src}`")
    lines.append(f"- source sha256: `{src_sha}`")
    lines.append(
        f"- cohort size N (intervals fitted): **{n}** "
        f"(≈ 15,859 production scan-states)"
    )
    lines.append(
        f"- Turnbull fit: n_observations={fit.n_observations}, "
        f"n_iterations={fit.n_iterations}, converged={fit.converged}, "
        f"final_loglik={fit.final_loglik:.4f}, beyond_mass={fit.beyond_mass:.6g}"
    )
    lines.append(f"- decision refs: {', '.join(DECISION_REFS)}")
    lines.append("")
    lines.append("## Fractional cohort-year distribution")
    lines.append("")
    lines.append(
        "| Year | year_mass (fraction) | Cohort count (frac × N) | Cumulative fraction | Cumulative count |"
    )
    lines.append("|---|---:|---:|---:|---:|")
    for r in rows:
        lines.append(
            f"| {r['year']} | {r['year_mass_fraction']:.4f} | {r['cohort_count']:.1f} "
            f"| {r['cumulative_fraction']:.4f} | {r['cumulative_count']:.1f} |"
        )
    lines.append("")
    lines.append("## Sanity checks")
    lines.append("")
    lines.append(
        f"- total mass ≈ cohort size: Σ cohort_count = **{sanity['total_count']:.4f}** "
        f"vs N = {n} (Δ = {sanity['total_count'] - n:+.4e})"
    )
    lines.append(
        f"- PMF sums to 1: Σ year_mass + beyond_mass = **{sanity['pmf_sum']:.10f}**"
    )
    lines.append(
        f"- year support: [{sanity['year_min']}, {sanity['year_max']}] "
        f"⊆ imagery coverage {IMAGERY_COVERAGE_YEARS} → "
        f"{'PASS' if sanity['coverage_ok'] else 'FAIL'}"
    )
    lines.append(
        f"- per-anchor E-step column sums vs cohort counts: max |Δ| = "
        f"**{sanity['per_anchor_max_abs_diff']:.4e}** "
        f"({'PASS' if sanity['per_anchor_ok'] else 'FAIL'})"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--intervals-csv", type=Path, default=DEFAULT_INTERVALS_CSV,
                    help="Production cohort interval table (issue03 intervals_prod.csv).")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                    help="Artifact dir under ~/zasolar_data/geid_temporal/ (never git).")
    ap.add_argument("--no-per-anchor", action="store_true",
                    help="Skip the optional per-anchor fractional detail CSV.")
    args = ap.parse_args()

    src: Path = args.intervals_csv
    if not src.exists():
        raise SystemExit(f"intervals CSV not found: {src}")
    src_sha = _sha256(src)

    intervals = load_intervals(src)
    n = len(intervals)
    fit = fit_turnbull(intervals)

    rows = cohort_table(fit, n)
    year_rows = [r for r in rows if not isinstance(r["year"], str)]
    years = [r["year"] for r in year_rows]

    # --- sanity ---------------------------------------------------------- #
    total_count = sum(r["cohort_count"] for r in rows)
    pmf_sum = sum(m for _, m in fit.year_mass) + fit.beyond_mass
    year_min, year_max = (min(years), max(years)) if years else (None, None)
    coverage_ok = (
        year_min is not None
        and IMAGERY_COVERAGE_YEARS[0] <= year_min
        and year_max <= IMAGERY_COVERAGE_YEARS[1]
    )

    per_anchor_rows: list[dict] = []
    per_anchor_max_abs_diff = 0.0
    per_anchor_ok = True
    if not args.no_per_anchor:
        per_anchor_rows, col_sums = per_anchor_fractions(intervals, fit)
        cohort_count_by_year = {r["year"]: r["cohort_count"] for r in rows}
        for key, colsum in col_sums.items():
            expected = cohort_count_by_year.get(key, 0.0)
            per_anchor_max_abs_diff = max(per_anchor_max_abs_diff, abs(colsum - expected))
        per_anchor_ok = per_anchor_max_abs_diff < 1e-6

    sanity = {
        "total_count": total_count,
        "pmf_sum": pmf_sum,
        "year_min": year_min,
        "year_max": year_max,
        "coverage_ok": coverage_ok,
        "per_anchor_max_abs_diff": per_anchor_max_abs_diff,
        "per_anchor_ok": per_anchor_ok,
    }

    # --- write ----------------------------------------------------------- #
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "cohort_year_fractional_table.csv"
    md_path = out_dir / "cohort_year_fractional_table.md"
    meta_path = out_dir / "fit_metadata.json"
    write_cohort_csv(csv_path, rows)
    md_path.write_text(
        render_md(rows, fit=fit, n=n, src=src, src_sha=src_sha, sanity=sanity)
    )
    meta = {
        "estimator_id": ESTIMATOR_ID,
        "decision_refs": DECISION_REFS,
        "source_intervals_csv": str(src),
        "source_intervals_sha256": src_sha,
        "cohort_size_n": n,
        "turnbull_fit": {
            "n_observations": fit.n_observations,
            "n_iterations": fit.n_iterations,
            "converged": fit.converged,
            "final_loglik": fit.final_loglik,
            "beyond_mass": fit.beyond_mass,
        },
        "sanity": sanity,
        "caliber_note": (
            "interval-level Turnbull NPMLE over existing production intervals; "
            "narrow caliber (no per-anchor re-decode, no cohort-frame rebuild); "
            "0.067 delivered-caliber pair disclosed per PRD-AMENDMENT-P1 §A5 "
            "until P3 re-band."
        ),
    }
    meta_path.write_text(json.dumps(meta, indent=2, default=str))

    per_anchor_path = None
    if not args.no_per_anchor and per_anchor_rows:
        per_anchor_path = out_dir / "per_anchor_year_fractions.csv"
        write_per_anchor_csv(per_anchor_path, per_anchor_rows, years)

    # --- stdout summary -------------------------------------------------- #
    print(f"[cohort-fractional] estimator={ESTIMATOR_ID} N={n}")
    print(f"  intervals: {src} (sha256 {src_sha[:12]}…)")
    print(f"  fit: iters={fit.n_iterations} converged={fit.converged} "
          f"loglik={fit.final_loglik:.4f} beyond_mass={fit.beyond_mass:.6g}")
    print(f"  Σ cohort_count = {total_count:.4f} (N={n}, Δ={total_count - n:+.4e})")
    print(f"  PMF sum = {pmf_sum:.10f}")
    print(f"  year support = [{year_min}, {year_max}] coverage_ok={coverage_ok}")
    print(f"  per-anchor col-sum max|Δ| = {per_anchor_max_abs_diff:.4e} ok={per_anchor_ok}")
    print(f"  wrote: {csv_path}")
    print(f"  wrote: {md_path}")
    print(f"  wrote: {meta_path}")
    if per_anchor_path:
        print(f"  wrote: {per_anchor_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
