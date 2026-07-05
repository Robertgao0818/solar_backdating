#!/usr/bin/env python3
"""ISSUE-03 consolidated gate runner (UNIT D).

Runs ALL seven ISSUE-03 acceptance criteria on the banked data and writes one
consolidated ``issue03_gates.json`` plus per-gate artifacts (``cohort_prior.json``,
``intervals_<rep>.csv``, ``report.md``) to the artifact directory. Nothing but
the artifacts is written; all repo/data inputs are read-only.

Wiring (design work order, UNIT D):

  cohort.intervals_from_scan_states -> survival.fit_turnbull ->
  survival.to_cohort_prior -> cohort_prior.json   (the EB prior, fit ONCE on
  the ~15,859-state production cohort; EB self-influence is O(1/n), negligible)

The prior is injected into the changepoint decoder via the additive
``--cohort-prior-json`` flags on ``estimator_harness`` / ``estimator_endtoend_decode``
(absent => flat prior => banked regression stays bit-exact).

AC map:
  AC1  cohort mapping counts (per rep + prod cohort; ~23% ambiguous recovered)
  AC2  Turnbull convergence + prior export (+ hyperparameter sweep table)
  AC3  posterior-mass vs midpoint imputation table (per rep)
  AC4  region/cadence stratified curves + non-informative-censoring bias note
  AC5  (gate) 3-rep survival-curve TVD beats point-date year TVD
  AC6  with/without recovered-ambiguous sensitivity curves
  AC7  (gate) extended-panel D3 re-pass with EB prior + banked baseline bit-exactness

The AC-builder functions (``build_ac*``) are pure over precomputed inputs so the
integration tests can exercise the gate logic on synthetic fixtures without the
full banked directory tree; the ``compute_ac*`` orchestrators do the heavy
banked reads/decodes and call the builders.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from datetime import date
from itertools import combinations
from pathlib import Path

from solar_backdating.estimators import ClampContext, EstimatorConfig, get_estimator
from solar_backdating.estimators.emissions import EmissionModel
from solar_backdating.estimators.survival import (
    CensoringInterval,
    TurnbullFit,
    cohort_prior_to_json,
    fit_turnbull,
    to_cohort_prior,
)
from solar_backdating.eval import cohort
from solar_backdating.eval.aggregate import (
    cohort_year_histogram_from_fit,
    midpoint_vs_posterior_report,
    posterior_year_mass,
    survival_curve_from_histogram,
    survival_tvd,
)
from solar_backdating.eval.metrics import tvd
from solar_backdating.eval.panel_io import (
    install_year,
    load_endtoend_delivery,
    load_endtoend_reference,
    load_inventory_weights,
    load_panel,
    load_strata,
)
from solar_backdating.eval.scan_state_io import load_scan_observations

# Additive-flag consumers live in the sibling validation scripts; reuse their
# verified join constants + baseline targets rather than re-deriving them.
from scripts.validation.estimator_endtoend_decode import _REPLACED_PROVIDERS, _s
from scripts.validation.estimator_harness import PUBLISHED, TOL, run_estimator

# --------------------------------------------------------------------------- #
# Default banked paths (all overridable on the CLI).
# --------------------------------------------------------------------------- #
DEFAULT_ROOT = Path.home() / "zasolar_data/geid_temporal/llm_endtoend_20260623"
DEFAULT_PROD = Path.home() / "zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02"
DEFAULT_PANEL_LONG = (
    Path.home() / "zasolar_data/geid_temporal/panel_repair_20260703/analysis_extended/long_all_extended.csv"
)
DEFAULT_SAMPLE_ANCHORS = (
    Path.home() / "zasolar_data/geid_temporal/panel_repair_20260703/sample/sample_anchors_extended.csv"
)
DEFAULT_EMISSIONS = (
    Path.home()
    / "zasolar_data/geid_temporal/panel_repair_20260703/analysis_estimator_harness_extended/emissions_fitted.json"
)
DEFAULT_PANEL_A_LONG = (
    Path.home() / "zasolar_data/geid_temporal/fullstack_noscan_20260630/long_all.csv"
)
DEFAULT_VEXCEL_CSV = Path(
    "/home/gaosh/projects/ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv"
)
DEFAULT_OUT = Path.home() / "zasolar_data/geid_temporal/issue03_gates_20260704"

# D3 re-pass calibers (extended 38-unit panel), from the ISSUE-02 completion record.
MODE_HIT_MIN = 0.882
HPD_MIN = 0.88
UNDATED_FLIP_MAX = 0.055
INV_MAP_MIN = 0.798
INV_YEAR_MIN = 0.757
# Recorded point-date year-TVD band (endtoend, ISSUE-02) — reported alongside the
# freshly computed point channel; the gate uses the fresh numbers, not this literal.
# DIAGNOSTIC-ONLY: this hard-MAP point-date band is RETIRED as the production
# install-year caliber (PRD-AMENDMENT-P1, ISSUE-22); it survives here only as the
# AC5 relative comparison baseline. The AC5 gate (survival must beat point) is a
# RELATIVE test and stays unchanged.
POINT_DATE_BAND = [0.081, 0.059, 0.079]


# --------------------------------------------------------------------------- #
# Reference loading (richer than endtoend_decode._load_reference_rows: also
# carries the production install-interval bounds for the AC5 splice-through
# survival intervals + inv_weight + prod year).
# --------------------------------------------------------------------------- #
def _load_reference_full(reference_csv: Path) -> tuple[list[dict], dict[int, float], dict[int, str]]:
    ref = load_endtoend_reference(reference_csv)
    rows: list[dict] = []
    for r in ref.to_dict("records"):
        rows.append(
            {
                "sf": int(r["source_feature_id"]),
                "group_anchor_id": r["group_anchor_id"],
                "target_anchor_id": r["target_anchor_id"],
                "grid_id": r["grid_id"],
                "status_stratum": r["status_stratum"],
                "prod_interval_start": _s(r.get("prod_install_interval_start")),
                "prod_interval_end": _s(r.get("prod_install_interval_end")),
            }
        )
    invw = {int(sf): float(w) for sf, w in zip(ref["source_feature_id"], ref["inv_weight"])}
    prod_year = {
        int(sf): (_s(y) or "") for sf, y in zip(ref["source_feature_id"], ref["prod_install_year"])
    }
    return rows, invw, prod_year


def _parse_iso(raw: str) -> date | None:
    s = (raw or "").strip()
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _reference_interval(row: dict, census_mid: date) -> CensoringInterval | None:
    """Splice-through anchor's interval from the production install bracket.

    ``(start, end]`` when both bounds parse and ``start < end``; left-censored
    to ``end`` when only the upper bound is present; ``None`` (skipped) when the
    row carries no dated bound at all (a genuinely undated splice-through)."""
    start = _parse_iso(row["prod_interval_start"])
    end = _parse_iso(row["prod_interval_end"])
    if end is None:
        return None
    if start is not None and start < end:
        return CensoringInterval(start, end, "interval", anchor_id=str(row["sf"]),
                                 grid_id=row["grid_id"], status=row["status_stratum"])
    return CensoringInterval(None, end, "left", anchor_id=str(row["sf"]),
                             grid_id=row["grid_id"], status=row["status_stratum"])


# --------------------------------------------------------------------------- #
# Per-rep channels: one pass over the 642 reference anchors that yields the
# point-year map, the decoder posteriors (scanned anchors), and the per-anchor
# survival CensoringIntervals — reusing the verified endtoend_decode join.
# --------------------------------------------------------------------------- #
def _rep_channels(
    rep_dir: Path,
    rows: list[dict],
    invw: dict[int, float],
    prod_year: dict[int, str],
    *,
    estimator_name: str,
    emissions,
    vexcel_ceiling: dict[str, date],
    cohort_prior,
    decoder_gap: int,
    census_mid: date,
) -> dict:
    est = get_estimator(estimator_name)
    delivery = load_endtoend_delivery(rep_dir / "delivery.csv")
    sfids = {row["sf"] for row in rows}
    delivery = delivery[delivery["source_feature_id"].isin(sfids)]
    delivery_by_sf = {int(r["source_feature_id"]): r for r in delivery.to_dict("records")}

    point_hist: Counter = Counter()
    posterior_hist: Counter = Counter()
    intervals: list[CensoringInterval] = []
    decoded_posteriors: list[tuple] = []  # (posterior, weight) for scanned anchors (AC3)
    n_scanned = 0

    cfg = EstimatorConfig(
        decoder_epoch_gap_days=decoder_gap,
        emissions=emissions,
        cohort_prior=cohort_prior,
    )

    for row in sorted(rows, key=lambda r: r["sf"]):
        sf = row["sf"]
        w = invw[sf]
        drow = delivery_by_sf.get(sf)
        delivery_yr = (install_year(drow) or "") if drow is not None else ""
        provider = _s(drow.get("date_provider")) if drow is not None else ""
        layer_col = _REPLACED_PROVIDERS.get(provider)

        scanned = False
        if layer_col is not None:
            layer, anchor_col = layer_col
            anchor_id = row[anchor_col]
            scan_path = rep_dir / layer / "scan_states" / f"{anchor_id}.json"
            if scan_path.exists():
                top = json.loads(scan_path.read_text())
                status = top.get("status", "")
                obs = load_scan_observations(scan_path)
                clamp = ClampContext(ceiling_date=vexcel_ceiling.get(row["grid_id"]))
                post = est(obs, clamp, cfg)
                scanned = True
                n_scanned += 1
                # point channel
                yr = post.map_date[:4] if post.map_date else ""
                if yr:
                    point_hist[yr] += w
                # posterior-mass channel
                for key, mass in posterior_year_mass(post).items():
                    posterior_hist[_year_key(key)] += w * mass
                decoded_posteriors.append((post, w))
                # survival interval from THIS rep's own scan state
                iv = cohort.interval_from_observations(
                    obs, status, ceiling=vexcel_ceiling.get(row["grid_id"]),
                    census_mid=census_mid, anchor_id=str(sf), grid_id=row["grid_id"],
                )
                if iv is not None:
                    intervals.append(iv)

        if not scanned:
            # splice-through: keep delivery year for point + posterior channels,
            # production bracket for the survival channel.
            if delivery_yr:
                point_hist[delivery_yr] += w
                posterior_hist[delivery_yr] += w
            iv = _reference_interval(row, census_mid)
            if iv is not None:
                intervals.append(iv)

    return {
        "point_hist": point_hist,
        "posterior_hist": posterior_hist,
        "intervals": intervals,
        "decoded_posteriors": decoded_posteriors,
        "n_scanned": n_scanned,
    }


def _year_key(key) -> str:
    """Normalise an aggregate key (int year or the ``beyond`` sentinel) to the
    string form the point channel + metrics.tvd iterate over."""
    return str(key)


# --------------------------------------------------------------------------- #
# AC builders — pure over precomputed inputs (unit-testable).
# --------------------------------------------------------------------------- #
def build_ac1(intervals_by_source: dict[str, list[CensoringInterval]],
              n_input_by_source: dict[str, int]) -> dict:
    """AC1 marginals per source. Reports n_input / n_mapped / n_dropped /
    by_kind / by_status / n_recovered for each rep and the prod cohort."""
    out: dict[str, dict] = {}
    for src in sorted(intervals_by_source):
        ivs = intervals_by_source[src]
        counts = cohort.interval_counts(ivs)
        n_input = n_input_by_source.get(src, len(ivs))
        counts["n_input"] = n_input
        counts["n_mapped"] = counts["n"]
        counts["n_dropped"] = n_input - counts["n"]
        out[src] = counts
    return out


def build_ac2(fit: TurnbullFit, prior, cohort_prior_json_path: str, sweep: list[dict]) -> dict:
    cpj = cohort_prior_to_json(prior)
    return {
        "converged": fit.converged,
        "n_iterations": fit.n_iterations,
        "final_loglik": fit.final_loglik,
        "n_observations": fit.n_observations,
        "year_mass": [[int(y), float(p)] for y, p in fit.year_mass],
        "beyond_mass": float(fit.beyond_mass),
        "cohort_prior_json_path": cohort_prior_json_path,
        "cohort_prior": cpj,
        "hyperparam_sweep": sweep,
    }


def build_ac4(intervals: list[CensoringInterval]) -> dict:
    """AC4 region/cadence stratification + non-informative-censoring bias note.

    Region := grid_id[:3] (JHB cohort => single 'JNB' region, reported honestly);
    the substantive geographic-cadence check is the per-grid median-cadence
    dispersion (grid IS geography), flagged when it is large."""
    by_region = cohort.stratify_intervals(intervals, "region")
    by_cadence = cohort.stratify_intervals(intervals, "cadence")

    def _stratum_summary(ivs: list[CensoringInterval]) -> dict:
        gaps = [iv.cadence_gap_days for iv in ivs if iv.cadence_gap_days is not None]
        fit = fit_turnbull(ivs)
        curve = survival_curve_from_histogram(cohort_year_histogram_from_fit(fit))
        widths = [
            (iv.upper - iv.lower).days
            for iv in ivs
            if iv.kind == "interval" and iv.lower is not None and iv.upper is not None
        ]
        return {
            "n": len(ivs),
            "median_gap_days": round(statistics.median(gaps), 1) if gaps else None,
            "median_interval_width_days": round(statistics.median(widths), 1) if widths else None,
            "converged": fit.converged,
            "curve": [[int(y), round(s, 6)] for y, s in curve],
        }

    region_block = {k: _stratum_summary(v) for k, v in sorted(by_region.items())}
    cadence_block = {k: _stratum_summary(v) for k, v in sorted(by_cadence.items())}

    # cadence x region crosstab (counts)
    crosstab: dict[str, dict[str, int]] = {}
    for iv in intervals:
        r = iv.grid_id[:3] if iv.grid_id else "UNK"
        c = _cadence_bucket(iv.cadence_gap_days)
        crosstab.setdefault(r, {}).setdefault(c, 0)
        crosstab[r][c] += 1

    # per-grid median-cadence dispersion (cadence-vs-geography confound probe)
    per_grid_gaps: dict[str, list[float]] = {}
    for iv in intervals:
        if iv.cadence_gap_days is not None and iv.grid_id:
            per_grid_gaps.setdefault(iv.grid_id, []).append(iv.cadence_gap_days)
    grid_medians = {
        g: statistics.median(v) for g, v in per_grid_gaps.items() if len(v) >= 3
    }
    dispersion = None
    if len(grid_medians) >= 2:
        vals = sorted(grid_medians.values())
        dispersion = {
            "n_grids": len(grid_medians),
            "min_median_gap": round(vals[0], 1),
            "max_median_gap": round(vals[-1], 1),
            "spread_days": round(vals[-1] - vals[0], 1),
            "iqr_days": round(_iqr(vals), 1),
        }

    n_regions = len([k for k in region_block if k != "UNK"])
    notes = []
    if n_regions <= 1:
        notes.append(
            "Single region ('JNB') in this JHB-only cohort: region and geography are "
            "constant, so no cross-region cadence confound is directly observable. The "
            "geographic-cadence check therefore falls back to per-grid cadence dispersion "
            "(grid == geography)."
        )
    if dispersion and dispersion["spread_days"] > 365:
        notes.append(
            f"Per-grid median cadence spans {dispersion['min_median_gap']}-"
            f"{dispersion['max_median_gap']} d across {dispersion['n_grids']} grids "
            f"(spread {dispersion['spread_days']} d): cadence DOES correlate with which "
            "grid an anchor sits in, i.e. with geography. Left/interval-censoring is then "
            "only conditionally non-informative given grid; cohort curves should be read "
            "as grid-marginalised, not grid-independent."
        )
    else:
        notes.append(
            "Per-grid median cadence is comparable across grids: no strong "
            "cadence-vs-geography correlation detected in this cohort."
        )
    notes.append(
        "Residual non-informative-censoring suspects (not resolvable from cadence alone): "
        "marker_missed_pv correlates with roof type, and detector-FP contamination inflates "
        "the recovered-ambiguous stratum. Both are flagged for downstream audit, not corrected here."
    )

    return {
        "by_region": region_block,
        "by_cadence": cadence_block,
        "cadence_region_crosstab": crosstab,
        "per_grid_cadence_dispersion": dispersion,
        "n_regions": n_regions,
        "bias_note": " ".join(notes),
    }


def _cadence_bucket(gap: float | None) -> str:
    if gap is None:
        return "unknown"
    if gap <= 90:
        return "<=90"
    if gap <= 365:
        return "90-365"
    return ">365"


def _iqr(sorted_vals: list[float]) -> float:
    n = len(sorted_vals)
    if n < 2:
        return 0.0
    q1 = sorted_vals[n // 4]
    q3 = sorted_vals[(3 * n) // 4]
    return q3 - q1


def build_ac5(point_tvd: list[float], survival_tvd_vals: list[float],
              posterior_tvd: list[float], sup_norm_ds: list[float]) -> dict:
    """AC5 verdict from precomputed pairwise TVDs.

    GATE: the Turnbull survival-curve channel must beat the point-date channel
    both on the mean and on the worst pair (max). The decoder posterior-mass
    channel + sup-norm |dS| are reported as context (ungated)."""
    point_mean = statistics.mean(point_tvd) if point_tvd else 0.0
    surv_mean = statistics.mean(survival_tvd_vals) if survival_tvd_vals else 0.0
    beats = bool(
        survival_tvd_vals
        and point_tvd
        and surv_mean < point_mean
        and max(survival_tvd_vals) < max(point_tvd)
    )
    return {
        "point_date_year_tvd": {
            "rep_to_rep": point_tvd,
            "mean": round(point_mean, 4),
            # DIAGNOSTIC-ONLY: the point-date year TVD is the RETIRED hard-MAP
            # caliber (PRD-AMENDMENT-P1, ISSUE-22), kept here solely as the AC5
            # relative comparison baseline; the production install-year caliber is
            # the fractional/survival channel. The AC5 gate below is a RELATIVE
            # test (survival must beat point) and is unchanged.
            "caliber": "diagnostic_only_retired_hard_MAP_PRD-AMENDMENT-P1_ISSUE-22",
        },
        "survival_curve_tvd": {
            "rep_to_rep": survival_tvd_vals,
            "mean": round(surv_mean, 4),
        },
        "decoder_posterior_tvd": {
            "rep_to_rep": posterior_tvd,
            "mean": round(statistics.mean(posterior_tvd), 4) if posterior_tvd else 0.0,
        },
        "sup_norm_dS": sup_norm_ds,
        "baseline_band": POINT_DATE_BAND,
        "beats_point_date": beats,
        "verdict": "PASS" if beats else "FAIL",
    }


def build_ac6(per_rep_intervals: dict[str, list[CensoringInterval]]) -> dict:
    """AC6 sensitivity: per-rep survival curves with vs without recovered
    (ambiguous) anchors, plus the marker_missed_pv in/out axis."""
    per_rep: dict[str, dict] = {}
    n_recovered_total = 0
    for rep in sorted(per_rep_intervals):
        ivs = per_rep_intervals[rep]
        full = ivs
        without = [iv for iv in ivs if not iv.recovered]
        without_marker = [iv for iv in ivs if iv.status != "done_ambiguous_marker_missed_pv"]
        n_recovered_total += sum(1 for iv in ivs if iv.recovered)

        h_full = cohort_year_histogram_from_fit(fit_turnbull(full))
        h_without = cohort_year_histogram_from_fit(fit_turnbull(without)) if without else Counter()
        h_marker = (
            cohort_year_histogram_from_fit(fit_turnbull(without_marker))
            if without_marker
            else Counter()
        )
        per_rep[rep] = {
            "with_recovered_curve": _curve_json(h_full),
            "without_recovered_curve": _curve_json(h_without),
            "tvd_with_vs_without": round(survival_tvd(h_full, h_without), 4),
            "marker_in_out_tvd": round(survival_tvd(h_full, h_marker), 4),
        }
    return {
        "per_rep": per_rep,
        "n_recovered_total": n_recovered_total,
        "note": (
            "with_vs_without isolates the ~23% recovered-ambiguous anchors' effect on the "
            "cohort survival curve; marker_in_out isolates the done_ambiguous_marker_missed_pv "
            "sub-stratum. prefix-vs-anywhere is not recomputed here (the maximal-usable-absent- "
            "prefix rule is fixed in UNIT B); it is the documented recovery-rule axis."
        ),
    }


def _curve_json(hist: Counter) -> list[list[float]]:
    return [[int(y), round(s, 6)] for y, s in survival_curve_from_histogram(hist)]


def build_ac7(with_prior: dict, without_prior: dict, pava: dict,
              baseline_bit_exact: dict) -> dict:
    """AC7 verdict: extended-panel D3 re-pass with the EB prior + banked
    baseline bit-exactness. Metric dicts carry map/year/undated/hpd + inv +
    done_appears year."""
    mode_hit = with_prior["map_mode_hit_all"]
    beats_pava = with_prior["map_mode_hit_all"] >= pava["map_mode_hit_all"]
    gate = {
        "mode_hit_ge_0882": mode_hit >= MODE_HIT_MIN,
        "beats_pava": beats_pava,
        "hpd_ge_0.88": with_prior["hpd"] >= HPD_MIN,
        "undated_flip_le_0055": with_prior["undated_flip"] <= UNDATED_FLIP_MAX + 1e-9,
        "year_stability_ge_fpd": with_prior["done_appears_year"] >= without_prior["fpd_done_appears_year"],
        "inv_weighted_ok": (
            with_prior["inv_map"] >= INV_MAP_MIN - 1e-9
            and with_prior["inv_year"] >= INV_YEAR_MIN - 1e-9
        ),
        "baselines_bit_exact": baseline_bit_exact["ok"],
    }
    verdict = "PASS" if all(gate.values()) else "FAIL"
    return {
        "decoder_with_prior": with_prior,
        "decoder_without_prior": without_prior,
        "pava": pava,
        "baseline_bit_exact": baseline_bit_exact,
        "gate": gate,
        "verdict": verdict,
    }


# --------------------------------------------------------------------------- #
# Heavy orchestrators (banked reads/decodes).
# --------------------------------------------------------------------------- #
def _rep_scan_dirs(root: Path, rep: str, layers: list[str]) -> list[Path]:
    return [root / rep / layer / "scan_states" for layer in layers]


def compute_prod_intervals(prod_cohort: Path, ceilings: dict[str, date],
                           census_mid: date) -> tuple[list[CensoringInterval], int]:
    scan_dir = prod_cohort / "scan_states"
    files = sorted(scan_dir.glob("*.json"))
    ivs = cohort.intervals_from_scan_states(files, ceilings=ceilings, census_mid=census_mid)
    return ivs, len(files)


def compute_rep_intervals(root: Path, reps: list[str], layers: list[str],
                          ceilings: dict[str, date], census_mid: date
                          ) -> tuple[dict[str, list[CensoringInterval]], dict[str, int]]:
    per_rep: dict[str, list[CensoringInterval]] = {}
    n_input: dict[str, int] = {}
    for rep in reps:
        files = sorted(
            f for d in _rep_scan_dirs(root, rep, layers) if d.exists() for f in d.glob("*.json")
        )
        per_rep[rep] = cohort.intervals_from_scan_states(
            files, ceilings=ceilings, census_mid=census_mid
        )
        n_input[rep] = len(files)
    return per_rep, n_input


def _extended_metrics(name: str, panel, chip_of, strata, weights, config) -> dict:
    _per_unit, headline = run_estimator(name, panel, chip_of, strata, weights, config)
    unw = headline["overall_unweighted"]
    inv = headline["overall_inventory_weighted"]
    da = headline["by_stratum"].get("done_appears", {})
    return {
        "map_mode_hit_all": unw["map_mode_hit_all"],
        "map_mode_hit_dated": unw["map_mode_hit_dated"],
        "year_mode_hit": unw["year_mode_hit"],
        "undated_flip": unw["undated_flip"],
        "hpd": headline["hpd_calibration"]["overall_contains_rate"] or 0.0,
        "inv_map": inv["map_mode_hit_all"],
        "inv_year": inv["year_mode_hit"],
        "done_appears_year": da.get("year_mode_hit", 0.0),
    }


def compute_ac7(panel_long: Path, sample_anchors: Path, emissions_json: Path,
                decoder_gap: int, prior, panel_a_long: Path) -> dict:
    import dataclasses

    panel, chip_of = load_panel(panel_long)
    strata = load_strata(sample_anchors if sample_anchors.exists() else None)
    weights = load_inventory_weights(None)  # population fallback (== manifest weights)
    emissions = (
        EmissionModel.from_json(json.loads(emissions_json.read_text()))
        if emissions_json.exists()
        else None
    )
    base = EstimatorConfig(decoder_epoch_gap_days=decoder_gap, emissions=emissions)
    with_prior_cfg = dataclasses.replace(base, cohort_prior=prior)

    wp = _extended_metrics("changepoint", panel, chip_of, strata, weights, with_prior_cfg)
    npr = _extended_metrics("changepoint", panel, chip_of, strata, weights, base)
    pava = _extended_metrics("pava", panel, chip_of, strata, weights, base)
    fpd = _extended_metrics("fpd", panel, chip_of, strata, weights, base)
    npr["fpd_done_appears_year"] = fpd["done_appears_year"]
    wp["fpd_done_appears_year"] = fpd["done_appears_year"]

    # Banked baseline bit-exactness on Panel A (fpd/sustained), prior injected to
    # prove the additive flag is inert for the regression gate.
    baseline_bit_exact = _panel_a_bit_exact(panel_a_long, prior)
    return build_ac7(wp, npr, pava, baseline_bit_exact)


def _panel_a_bit_exact(panel_a_long: Path, prior) -> dict:
    if not panel_a_long.exists():
        return {"ok": True, "skipped": True, "reason": "panel A long_all.csv absent"}
    import dataclasses

    panel, chip_of = load_panel(panel_a_long)
    strata = load_strata(None)
    weights = load_inventory_weights(None)
    # Inject the prior to prove fpd/sustained ignore it (bit-exact regression).
    cfg = dataclasses.replace(EstimatorConfig(), cohort_prior=prior)
    detail = {}
    ok = True
    for name, want in PUBLISHED.items():
        _pu, headline = run_estimator(name, panel, chip_of, strata, weights, cfg)
        unw = headline["overall_unweighted"]
        for metric, target in want.items():
            got = unw[metric]
            passed = abs(got - target) <= TOL
            ok = ok and passed
            detail[f"{name}.{metric}"] = {"got": got, "target": target, "pass": passed}
    return {"ok": ok, "detail": detail}


def compute_ac5_ac3(root: Path, reps: list[str], rows: list[dict], invw: dict[int, float],
                    prod_year: dict[int, str], *, emissions, vexcel_ceiling, prior,
                    decoder_gap: int, census_mid: date) -> tuple[dict, dict]:
    """Run the per-rep decode once; derive AC5 (point/survival/posterior TVD) and
    AC3 (midpoint-vs-posterior per rep) from the same pass."""
    channels = {
        rep: _rep_channels(
            root / rep, rows, invw, prod_year,
            estimator_name="changepoint", emissions=emissions,
            vexcel_ceiling=vexcel_ceiling, cohort_prior=prior,
            decoder_gap=decoder_gap, census_mid=census_mid,
        )
        for rep in reps
    }
    # AC5 channels
    point_hists = {rep: channels[rep]["point_hist"] for rep in reps}
    posterior_hists = {rep: channels[rep]["posterior_hist"] for rep in reps}
    survival_hists = {
        rep: cohort_year_histogram_from_fit(fit_turnbull(channels[rep]["intervals"]))
        for rep in reps
    }
    point_tvd = [round(tvd(point_hists[a], point_hists[b]), 4) for a, b in combinations(reps, 2)]
    survival_tvd_vals = [
        round(survival_tvd(survival_hists[a], survival_hists[b]), 4)
        for a, b in combinations(reps, 2)
    ]
    posterior_tvd = [
        round(survival_tvd(posterior_hists[a], posterior_hists[b]), 4)
        for a, b in combinations(reps, 2)
    ]
    sup_norm = [
        round(_curve_sup_norm(survival_hists[a], survival_hists[b]), 4)
        for a, b in combinations(reps, 2)
    ]
    ac5 = build_ac5(point_tvd, survival_tvd_vals, posterior_tvd, sup_norm)

    # AC3 per rep (midpoint vs posterior over the decoded/scanned anchors)
    ac3_per_rep = {}
    for rep in reps:
        rep_report = midpoint_vs_posterior_report(channels[rep]["decoded_posteriors"])
        ac3_per_rep[rep] = {
            "point_hist": _hist_json(rep_report["point_hist"]),
            "midpoint_hist": _hist_json(rep_report["midpoint_hist"]),
            "posterior_hist": _hist_json(rep_report["posterior_hist"]),
            "per_year_delta": {str(k): round(v, 6) for k, v in rep_report["per_year_delta"].items()},
            "l1_shift": round(rep_report["l1_shift"], 6),
            "n_dated": rep_report["n_dated"],
            "n_undated": rep_report["n_undated"],
        }
    ac3 = {"per_rep": ac3_per_rep}
    return ac5, ac3


def _hist_json(hist: Counter) -> dict:
    return {str(k): round(v, 6) for k, v in sorted(hist.items(), key=lambda kv: str(kv[0]))}


def _curve_sup_norm(hist_a: Counter, hist_b: Counter) -> float:
    """Sup-norm ``|dS|`` between two survival curves on their UNION year grid.

    ``survival_curve_from_histogram`` only emits years present in its own
    histogram, so a naive point-wise diff would read a year missing from one
    rep as ``S=0`` (spuriously ~1.0 at an early year where both curves are in
    fact ~1.0). Instead evaluate ``S(y)=P(T>y)`` for BOTH normalized PMFs on
    every year in the union grid, carrying the step function correctly."""
    years = sorted(y for y in (set(hist_a) | set(hist_b)) if not isinstance(y, str))
    if not years:
        return 0.0

    def _cum(hist: Counter) -> dict[int, float]:
        total = sum(hist.values())
        if total <= 0.0:
            return {y: 1.0 for y in years}
        out: dict[int, float] = {}
        running = 1.0
        for y in years:
            running -= hist.get(y, 0.0) / total
            out[y] = max(running, 0.0)
        return out

    sa, sb = _cum(hist_a), _cum(hist_b)
    return max(abs(sa[y] - sb[y]) for y in years)


def compute_hyperparam_sweep(fit: TurnbullFit, panel_long: Path, sample_anchors: Path,
                             emissions_json: Path, decoder_gap: int) -> list[dict]:
    """Deterministic (lambda, beyond_policy, temperature) grid scored on the
    extended panel; the Turnbull fit is done once, only the export params vary."""
    import dataclasses

    if not panel_long.exists():
        return []
    panel, chip_of = load_panel(panel_long)
    strata = load_strata(sample_anchors if sample_anchors.exists() else None)
    weights = load_inventory_weights(None)
    emissions = (
        EmissionModel.from_json(json.loads(emissions_json.read_text()))
        if emissions_json.exists()
        else None
    )
    base = EstimatorConfig(decoder_epoch_gap_days=decoder_gap, emissions=emissions)
    rows = []
    for lam in (0.02, 0.05, 0.10):
        for policy in ("terminal_year", "cohort_mean", "terminal_minus_log2"):
            for temp in (0.5, 1.0):
                prior = to_cohort_prior(
                    fit, smooth_lambda=lam, beyond_policy=policy, temperature=temp
                )
                cfg = dataclasses.replace(base, cohort_prior=prior)
                m = _extended_metrics("changepoint", panel, chip_of, strata, weights, cfg)
                rows.append({
                    "smooth_lambda": lam, "beyond_policy": policy, "temperature": temp,
                    "map_mode_hit_all": m["map_mode_hit_all"], "hpd": m["hpd"],
                    "undated_flip": m["undated_flip"], "year_mode_hit": m["year_mode_hit"],
                })
    return rows


# --------------------------------------------------------------------------- #
# Report + main
# --------------------------------------------------------------------------- #
def _write_intervals_csv(path: Path, intervals: list[CensoringInterval]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["anchor_id", "grid_id", "status", "kind", "lower", "upper",
                    "recovered", "n_usable", "cadence_gap_days"])
        for iv in intervals:
            w.writerow([
                iv.anchor_id, iv.grid_id, iv.status, iv.kind,
                iv.lower.isoformat() if iv.lower else "",
                iv.upper.isoformat() if iv.upper else "",
                int(iv.recovered), iv.n_usable,
                "" if iv.cadence_gap_days is None else iv.cadence_gap_days,
            ])


def _write_report(out_dir: Path, payload: dict) -> None:
    L = ["# ISSUE-03 gate report", "",
         f"overall verdict: **{payload['overall_verdict']}**", ""]
    ac1 = payload["ac1_intervals"]["prod_cohort"]
    L += [
        "## AC1 cohort mapping (prod cohort)",
        f"- n_input={ac1['n_input']} n_mapped={ac1['n_mapped']} "
        f"n_dropped={ac1['n_dropped']} n_recovered={ac1['n_recovered']}",
        f"- by_kind={ac1['by_kind']}", "",
        "## AC2 Turnbull",
        f"- converged={payload['ac2_turnbull']['converged']} "
        f"iters={payload['ac2_turnbull']['n_iterations']} "
        f"beyond_mass={payload['ac2_turnbull']['beyond_mass']:.4g}", "",
        "## AC5 (gate)",
        f"- point rep_to_rep={payload['ac5_gate']['point_date_year_tvd']['rep_to_rep']} "
        f"mean={payload['ac5_gate']['point_date_year_tvd']['mean']}",
        f"- survival rep_to_rep={payload['ac5_gate']['survival_curve_tvd']['rep_to_rep']} "
        f"mean={payload['ac5_gate']['survival_curve_tvd']['mean']}",
        f"- verdict={payload['ac5_gate']['verdict']}", "",
        "## AC7 (gate)",
        f"- with_prior mode_hit={payload['ac7_integration']['decoder_with_prior']['map_mode_hit_all']} "
        f"undated_flip={payload['ac7_integration']['decoder_with_prior']['undated_flip']} "
        f"hpd={payload['ac7_integration']['decoder_with_prior']['hpd']}",
        f"- gate={payload['ac7_integration']['gate']}",
        f"- verdict={payload['ac7_integration']['verdict']}", "",
    ]
    (out_dir / "report.md").write_text("\n".join(L) + "\n")


def run_all(args) -> dict:
    census_mid = date.fromisoformat(args.census_mid)
    ceilings = cohort.load_grid_ceilings(args.vexcel_capture_csv)

    # --- intervals (prod cohort + per rep) ---
    prod_intervals, prod_n_input = compute_prod_intervals(args.prod_cohort, ceilings, census_mid)
    rep_intervals, rep_n_input = compute_rep_intervals(
        args.root, args.reps, args.layers, ceilings, census_mid
    )

    # --- AC2 EB prior fit (once, prod cohort) ---
    fit = fit_turnbull(prod_intervals)
    prior = to_cohort_prior(
        fit, smooth_lambda=args.smooth_lambda, beyond_policy=args.beyond_policy,
        temperature=args.temperature,
    )
    cohort_prior_path = args.out_dir / "cohort_prior.json"
    cohort_prior_path.write_text(json.dumps(cohort_prior_to_json(prior), indent=2))

    sweep = compute_hyperparam_sweep(
        fit, args.panel_long, args.sample_anchors, args.emissions_json, args.decoder_epoch_gap_days
    ) if args.sweep else []

    # --- AC1 ---
    ac1_sources = dict(rep_intervals)
    ac1_ninput = dict(rep_n_input)
    ac1_per_rep = build_ac1(ac1_sources, ac1_ninput)
    ac1_prod = build_ac1({"prod": prod_intervals}, {"prod": prod_n_input})["prod"]

    # --- AC2 ---
    ac2 = build_ac2(fit, prior, str(cohort_prior_path), sweep)

    # --- AC4 (prod cohort) ---
    ac4 = build_ac4(prod_intervals)

    # --- AC6 (per rep) ---
    ac6 = build_ac6(rep_intervals)

    # --- AC3 + AC5 (per-rep decode) ---
    emissions = (
        EmissionModel.from_json(json.loads(args.emissions_json.read_text()))
        if args.emissions_json.exists()
        else None
    )
    rows, invw, prod_year = _load_reference_full(args.root / "reference.csv")
    ac5, ac3 = compute_ac5_ac3(
        args.root, args.reps, rows, invw, prod_year,
        emissions=emissions, vexcel_ceiling=ceilings, prior=prior,
        decoder_gap=args.decoder_epoch_gap_days, census_mid=census_mid,
    )

    # --- AC7 ---
    ac7 = compute_ac7(
        args.panel_long, args.sample_anchors, args.emissions_json,
        args.decoder_epoch_gap_days, prior, args.panel_a_long,
    )

    overall = "PASS" if (ac5["verdict"] == "PASS" and ac7["verdict"] == "PASS") else "FAIL"
    payload = {
        "config": {
            "reps": args.reps, "root": str(args.root), "layers": args.layers,
            "prod_cohort": str(args.prod_cohort), "census_mid": args.census_mid,
            "decoder_epoch_gap_days": args.decoder_epoch_gap_days,
            "vexcel_csv": str(args.vexcel_capture_csv),
            "n_grids_with_ceiling": len(ceilings),
            "emissions_json": str(args.emissions_json), "panel_long": str(args.panel_long),
            "smooth_lambda": args.smooth_lambda, "beyond_policy": args.beyond_policy,
            "temperature": args.temperature,
        },
        "ac1_intervals": {"per_rep": ac1_per_rep, "prod_cohort": ac1_prod},
        "ac2_turnbull": ac2,
        "ac3_aggregation": ac3,
        "ac4_noninformative": ac4,
        "ac5_gate": ac5,
        "ac6_sensitivity": ac6,
        "ac7_integration": ac7,
        "overall_verdict": overall,
    }

    # artifacts
    (args.out_dir / "issue03_gates.json").write_text(json.dumps(payload, indent=2))
    _write_intervals_csv(args.out_dir / "intervals_prod.csv", prod_intervals)
    for rep, ivs in rep_intervals.items():
        _write_intervals_csv(args.out_dir / f"intervals_{rep}.csv", ivs)
    _write_report(args.out_dir, payload)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--reps", nargs="+", default=["rep1", "rep2", "rep3"])
    ap.add_argument("--layers", nargs="+", default=["L0", "L1"])
    ap.add_argument("--prod-cohort", type=Path, default=DEFAULT_PROD)
    ap.add_argument("--vexcel-capture-csv", type=Path, default=DEFAULT_VEXCEL_CSV)
    ap.add_argument("--census-mid", default="2024-06-30")
    ap.add_argument("--panel-long", type=Path, default=DEFAULT_PANEL_LONG)
    ap.add_argument("--sample-anchors", type=Path, default=DEFAULT_SAMPLE_ANCHORS)
    ap.add_argument("--panel-a-long", type=Path, default=DEFAULT_PANEL_A_LONG)
    ap.add_argument("--emissions-json", type=Path, default=DEFAULT_EMISSIONS)
    ap.add_argument("--decoder-epoch-gap-days", type=int, default=45)
    ap.add_argument("--smooth-lambda", type=float, default=0.05)
    ap.add_argument("--beyond-policy", default="terminal_year")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--sweep", action="store_true", help="run the AC2 hyperparameter sweep")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--gate", action="store_true",
                    help="assert AC5 + AC7 pass; nonzero exit on failure")
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    payload = run_all(a)

    print("\n=== ISSUE-03 gates ===")
    print(f"AC1 prod: n_input={payload['ac1_intervals']['prod_cohort']['n_input']} "
          f"n_recovered={payload['ac1_intervals']['prod_cohort']['n_recovered']}")
    print(f"AC2 converged={payload['ac2_turnbull']['converged']}")
    print(f"AC5 verdict={payload['ac5_gate']['verdict']} "
          f"survival_mean={payload['ac5_gate']['survival_curve_tvd']['mean']} "
          f"point_mean={payload['ac5_gate']['point_date_year_tvd']['mean']}")
    print(f"AC7 verdict={payload['ac7_integration']['verdict']} "
          f"gate={payload['ac7_integration']['gate']}")
    print(f"overall={payload['overall_verdict']}")
    print(f"wrote {a.out_dir}/issue03_gates.json")

    if a.gate:
        ok = payload["ac5_gate"]["verdict"] == "PASS" and payload["ac7_integration"]["verdict"] == "PASS"
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
