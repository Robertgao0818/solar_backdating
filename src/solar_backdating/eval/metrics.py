"""Reproducibility metrics for install-date posteriors (ISSUE-01 D3/D8).

Exact reproductions of ``fullstack_noscan_analyze`` mechanics — parity with the
published numbers depends on them, so do NOT "improve" the rounding or the
mode-hit denominator. ``mode_hit`` rates are prefix-agnostic (only the equality
partition matters), which is why the ``MAP|`` token prefix reproduces the same
rates as the reference's ``FPD|``/``FSD|`` prefixes.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from solar_backdating.estimators import InstallDatePosterior

# Public metric keys carried on every per-unit record + weighted headline.
METRIC_KEYS = ("map_mode_hit_all", "map_mode_hit_dated", "year_mode_hit", "undated_flip")


def mode_hit(values: list[str]) -> tuple[str, float]:
    """Modal value + hit rate. Denominator = ALL values (``""`` counts against)."""
    vals = [v for v in values if v != ""]
    if not vals:
        return "", 0.0
    modal, n = Counter(vals).most_common(1)[0]
    return modal, n / len(values)


def tier(h: float) -> str:
    """rock_solid >= 0.8, wobbly >= 0.4, else chaotic."""
    return "rock_solid" if h >= 0.8 else ("wobbly" if h >= 0.4 else "chaotic")


def tvd(a: Counter, b: Counter) -> float:
    """Total-variation distance between two histograms: 0.5 * sum |share_a - share_b|."""
    na = sum(a.values()) or 1
    nb = sum(b.values()) or 1
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a[k] / na - b[k] / nb) for k in keys)


def install_tokens(p: InstallDatePosterior) -> tuple[str, str, str, int]:
    """(map_tok, year_tok, candidate_tok, undated_int) — the mode-hit encoding.

    ``map_tok`` is ``"UNDATED"`` when undated else ``"MAP|<date>"`` (a candidate).
    ``year_tok`` is ``""`` when undated (excluded as a candidate but counted in
    the denominator) else the 4-digit year — this is the mechanic that makes the
    year-hit strictly <= the map-hit.
    """
    undated = p.map_date == ""
    map_tok = "UNDATED" if undated else f"MAP|{p.map_date}"
    year_tok = "" if (undated or len(p.map_date) < 4) else p.map_date[:4]
    candidate_tok = "UNDATED" if undated else map_tok
    return map_tok, year_tok, candidate_tok, (1 if undated else 0)


def per_unit_metrics(tokens_by_rep: list[tuple[str, str, str, int]]) -> dict:
    """Per-unit reproducibility metrics from each rep's ``install_tokens``.

    Rates are rounded to 3 dp to mirror the reference's per-unit CSV storage, so
    the unweighted overall mean (mean of rounded per-unit values, re-rounded)
    reproduces ``summary.json`` bit-for-bit.
    """
    map_toks = [t[0] for t in tokens_by_rep]
    year_toks = [t[1] for t in tokens_by_rep]
    undated = [t[3] for t in tokens_by_rep]

    modal_map, map_all = mode_hit(map_toks)
    dated_map = [m for m, u in zip(map_toks, undated) if u == 0]
    map_dated = mode_hit(dated_map)[1] if dated_map else 0.0
    _, year_all = mode_hit(year_toks)
    undated_flip = sum(undated) / len(undated) if undated else 0.0

    return {
        "map_mode_hit_all": round(map_all, 3),
        "map_mode_hit_dated": round(map_dated, 3),
        "year_mode_hit": round(year_all, 3),
        "undated_flip": round(undated_flip, 3),
        "tier": tier(map_all),
        "modal_map": modal_map,
        "n_dated_reps": len(dated_map),
    }


def hpd_contains_rate(posteriors: list[InstallDatePosterior]) -> float | None:
    """HPD calibration proxy (D3): mean over ordered rep pairs (i != j) of whether
    rep i's credible interval contains rep j's ``map_date``.

    Open bound (``None``) is treated as +/-inf; both-undated counts as contained;
    one-undated (j has no date point) counts as not contained unless i is also
    undated. Meaningful only for ``pava`` — degenerate baseline posteriors
    collapse to point-equality.
    """
    n = len(posteriors)
    if n < 2:
        return None
    hits = 0
    tot = 0
    for i in range(n):
        lo = posteriors[i].credible_low_date
        hi = posteriors[i].credible_high_date
        i_undated = posteriors[i].map_date == ""
        for j in range(n):
            if i == j:
                continue
            tot += 1
            mj = posteriors[j].map_date
            if mj == "":
                if i_undated:
                    hits += 1
                continue
            dj = date.fromisoformat(mj)
            if (lo is None or lo <= dj) and (hi is None or dj <= hi):
                hits += 1
    return hits / tot if tot else None


def _mean(rows: list[dict], key: str) -> float:
    """Per-unit metric mean. The dated-only metric excludes fully-undated units
    (``n_dated_reps == 0``) per D8(3): those units have no dated reps, so the
    ``0.0`` sentinel ``per_unit_metrics`` stores for them must NOT enter the
    dated-only denominator — averaging it in dilutes the headline and conflates
    'no dated reps' with 'dated reps totally disagree'. Rows lacking
    ``n_dated_reps`` (e.g. hand-built fixtures) are treated as dated."""
    if key == "map_mode_hit_dated":
        rows = [r for r in rows if r.get("n_dated_reps", 1) > 0]
    n = len(rows) or 1
    return round(sum(r[key] for r in rows) / n, 3)


def _tier_counts(rows: list[dict]) -> dict:
    c = Counter(r["tier"] for r in rows)
    return {
        "n": len(rows),
        "rock_solid": c.get("rock_solid", 0),
        "wobbly": c.get("wobbly", 0),
        "chaotic": c.get("chaotic", 0),
    }


def weighted_headline(
    per_unit: list[dict], strata: dict[str, str], weights: dict[str, float]
) -> dict:
    """Unweighted + inventory-weighted overall, per-stratum table, tier counts.

    Inventory-weighted overall = sum_st w_st * mean_rate_st / sum_st w_st with
    ``w_st = weights.get(st, 0)`` (stratum-population weighting, matching
    ``llm_endtoend_analyze.agg``). Unweighted overall = plain per-unit mean.
    """
    overall_unweighted = {k: _mean(per_unit, k) for k in METRIC_KEYS}
    tier_counts = _tier_counts(per_unit)

    by_stratum: dict[str, dict] = {}
    for st in sorted({r["stratum"] for r in per_unit}):
        rows = [r for r in per_unit if r["stratum"] == st]
        by_stratum[st] = {**_tier_counts(rows), **{k: _mean(rows, k) for k in METRIC_KEYS}}

    def winv(key: str) -> float:
        num = 0.0
        den = 0.0
        for st, s in by_stratum.items():
            w = weights.get(st, 0.0)
            num += w * s[key]
            den += w
        return round(num / den, 3) if den else 0.0

    overall_inventory_weighted = {k: winv(k) for k in METRIC_KEYS}

    hpd_vals = [
        r["hpd_contains_rate"]
        for r in per_unit
        if r.get("hpd_contains_rate") is not None
    ]
    hpd_overall = round(sum(hpd_vals) / len(hpd_vals), 3) if hpd_vals else None

    return {
        "overall_unweighted": overall_unweighted,
        "overall_inventory_weighted": overall_inventory_weighted,
        "by_stratum": by_stratum,
        "tier_counts": tier_counts,
        "hpd_calibration": {"overall_contains_rate": hpd_overall, "n_units": len(hpd_vals)},
    }
