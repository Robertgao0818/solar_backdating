#!/usr/bin/env python3
"""Estimator reproducibility harness (ISSUE-01 PART 2).

Runs one or more registered install-date estimators (``fpd``, ``sustained``,
``pava``, ...) over the banked 10-rep panel and reports rep-to-rep
reproducibility the same way ``fullstack_noscan_analyze`` does — map-interval
mode-hit, dated-only mode-hit (D8), year mode-hit, undated flip, HPD calibration
proxy (D3) — both inventory-weighted and unweighted, with a per-stratum table.

``--gate`` asserts the ``fpd``/``sustained`` runs reproduce the published
``summary.json`` numbers (nonzero exit on failure), so the same acceptance
surface runs as pytest and as a CLI. ``--endtoend`` additionally computes the
install-year cohort TVD (rep-vs-production and the new pairwise rep-to-rep, D3/C7)
over the 3 end-to-end reps.

The estimators + metrics live in the ``solar_backdating`` package; this file is
a thin CLI over them (import ``solar_backdating.estimators`` /
``solar_backdating.eval``).
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
from collections import Counter
from itertools import combinations
from pathlib import Path

from solar_backdating.estimators import (
    ClampContext,
    EstimatorConfig,
    available_estimators,
    get_estimator,
)
from solar_backdating.estimators.emissions import EmissionModel, fit_emissions_em
from solar_backdating.eval.metrics import (
    METRIC_KEYS,
    hpd_contains_rate,
    install_tokens,
    per_unit_metrics,
    tvd,
    weighted_headline,
)
from solar_backdating.eval.panel_io import (
    DEFAULT_SAMPLE_ANCHORS,
    install_year,
    load_endtoend_delivery,
    load_endtoend_reference,
    load_inventory_weights,
    load_panel,
    load_strata,
)

DEFAULT_OUT = (
    Path.home()
    / "zasolar_data/geid_temporal/fullstack_noscan_20260630/analysis_estimator_harness"
)

# Published targets from fullstack_noscan_20260630/analysis/summary.json "overall"
# (unweighted per-unit means). Override via --targets JSON if the oracle moves.
PUBLISHED = {
    "fpd": {"map_mode_hit_all": 0.807, "undated_flip": 0.046, "year_mode_hit": 0.814},
    "sustained": {"map_mode_hit_all": 0.911, "year_mode_hit": 0.857},
}
TOL = 5e-4  # exact computation; +/-0.0005 only absorbs the published 3-dp rounding
# Published pairwise install-year cohort TVD band (derived_cuts.json rep_to_rep).
REP_TO_REP_TVD_BAND = (0.037, 0.063)


def run_estimator(
    name: str,
    panel: dict,
    chip_of: dict,
    strata: dict[str, str],
    weights: dict[str, float],
    config: EstimatorConfig,
) -> tuple[list[dict], dict]:
    """Run one estimator over the whole panel -> (per_unit rows, weighted headline)."""
    est = get_estimator(name)
    per_unit: list[dict] = []
    for unit, reps in sorted(panel.items()):
        chip = chip_of[unit]
        stratum = strata.get(chip, "unknown")
        tokens_by_rep = []
        posteriors = []
        for _rep, obs in sorted(reps.items()):
            p = est(obs, ClampContext(), config)
            posteriors.append(p)
            tokens_by_rep.append(install_tokens(p))
        row = {
            "unit": str(unit),
            "chip_id": chip,
            "stratum": stratum,
            "n_reps": len(reps),
            **per_unit_metrics(tokens_by_rep),
            "hpd_contains_rate": hpd_contains_rate(posteriors),
        }
        per_unit.append(row)
    return per_unit, weighted_headline(per_unit, strata, weights)


def _round3(v):
    return round(v, 3) if isinstance(v, float) else v


def _write_summary(out_dir: Path, name: str, headline: dict, config: EstimatorConfig,
                   weights_fallback: bool, emissions_source: str = "none") -> None:
    summary = {
        "estimator": name,
        "config": {
            "epoch_gap_days": config.epoch_gap_days,
            "flip_rate": config.flip_rate,
            "credible_mass": config.credible_mass,
            "prior_weight": config.prior_weight,
            "decoder_epoch_gap_days": config.decoder_epoch_gap_days,
            "cohort_prior_set": config.cohort_prior is not None,
        },
        # Provenance for the ISSUE-02 decoder's emission matrix (fitted / loaded
        # / none). Harmless no-op for fpd/sustained/pava, which ignore
        # config.emissions entirely.
        "emissions_source": emissions_source,
        "emissions": config.emissions.to_json() if config.emissions is not None else None,
        "weights_source": "fallback_constant" if weights_fallback else "sample_manifest",
        "overall_unweighted": headline["overall_unweighted"],
        "overall_inventory_weighted": headline["overall_inventory_weighted"],
        "dated_only": {
            "map_mode_hit_dated_unweighted": headline["overall_unweighted"]["map_mode_hit_dated"],
            "map_mode_hit_dated_inv_weighted": headline["overall_inventory_weighted"][
                "map_mode_hit_dated"
            ],
        },
        "hpd_calibration": {
            **headline["hpd_calibration"],
            "target": config.credible_mass,
        },
        "tier_counts": headline["tier_counts"],
        "by_stratum": headline["by_stratum"],
    }
    (out_dir / f"summary_{name}.json").write_text(json.dumps(summary, indent=2))


def _write_per_unit(out_dir: Path, name: str, per_unit: list[dict]) -> None:
    cols = [
        "unit", "chip_id", "stratum", "n_reps",
        "map_mode_hit_all", "map_mode_hit_dated", "year_mode_hit",
        "undated_flip", "tier", "modal_map", "hpd_contains_rate",
    ]
    with (out_dir / f"per_unit_{name}.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in per_unit:
            out = dict(r)
            if out.get("hpd_contains_rate") is not None:
                out["hpd_contains_rate"] = round(out["hpd_contains_rate"], 3)
            w.writerow(out)


def _write_report(out_dir: Path, name: str, headline: dict) -> None:
    unw = headline["overall_unweighted"]
    inv = headline["overall_inventory_weighted"]
    tc = headline["tier_counts"]
    L = [
        f"# Estimator reproducibility — `{name}`",
        "",
        f"- units: {tc['n']}   rock_solid: {tc['rock_solid']}   "
        f"wobbly: {tc['wobbly']}   chaotic: {tc['chaotic']}",
        "",
        "## Overall (weighted vs unweighted)",
        "",
        "| metric | unweighted | inventory-weighted |",
        "|---|--:|--:|",
    ]
    for k in METRIC_KEYS:
        L.append(f"| {k} | {unw[k]} | {inv[k]} |")
    hpd = headline["hpd_calibration"]["overall_contains_rate"]
    L += [
        f"| hpd_contains_rate | {hpd if hpd is not None else '-'} | - |",
        "",
        "## By status stratum",
        "",
        "| stratum | n | rock | wob | chaos | map-all | map-dated | year | undated-flip |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for st, s in sorted(headline["by_stratum"].items(), key=lambda kv: -kv[1]["n"]):
        L.append(
            f"| {st} | {s['n']} | {s['rock_solid']} | {s['wobbly']} | {s['chaotic']} "
            f"| {s['map_mode_hit_all']} | {s['map_mode_hit_dated']} | {s['year_mode_hit']} "
            f"| {s['undated_flip']} |"
        )
    (out_dir / f"report_{name}.md").write_text("\n".join(L) + "\n")


def _gate(results: dict[str, dict], targets: dict) -> bool:
    """Assert each estimator's overall_unweighted matches targets within TOL."""
    print("\n=== regression gate ===")
    print(f"{'estimator':<12} {'metric':<18} {'got':>8} {'target':>8} {'verdict':>8}")
    ok = True
    for name, want in targets.items():
        if name not in results:
            print(f"{name:<12} {'(not run)':<18}")
            ok = False
            continue
        unw = results[name]["overall_unweighted"]
        for metric, target in want.items():
            got = unw[metric]
            passed = abs(got - target) <= TOL
            ok = ok and passed
            print(
                f"{name:<12} {metric:<18} {got:>8.3f} {target:>8.3f} "
                f"{'PASS' if passed else 'FAIL':>8}"
            )
    print(f"gate: {'PASS' if ok else 'FAIL'}")
    return ok


def _run_endtoend(reference_csv: Path, rep_csvs: list[Path], out_dir: Path,
                  do_gate: bool) -> bool:
    """Install-year cohort TVD: rep-vs-production + pairwise rep-to-rep (inv-weighted)."""
    ref = load_endtoend_reference(reference_csv)
    sfids = list(ref["source_feature_id"])
    invw = dict(zip(ref["source_feature_id"], ref["inv_weight"]))
    prod_year = dict(zip(ref["source_feature_id"], ref["prod_install_year"].fillna("")))

    def hist(year_map: dict[int, str]) -> Counter:
        c: Counter = Counter()
        for sf in sfids:
            y = year_map.get(sf, "")
            if y:
                c[y] += invw[sf]
        return c

    prod_hist = hist({sf: (prod_year[sf] or "") for sf in sfids})
    rep_hists = []
    for rp in rep_csvs:
        d = load_endtoend_delivery(rp)
        d = d[d["source_feature_id"].isin(set(sfids))]
        year_map = {int(r["source_feature_id"]): (install_year(r) or "")
                    for r in d.to_dict("records")}
        rep_hists.append(hist(year_map))

    rep_vs_prod = [round(tvd(prod_hist, rh), 4) for rh in rep_hists]
    rep_to_rep = [round(tvd(a, b), 4) for a, b in combinations(rep_hists, 2)]
    payload = {
        "n_reps": len(rep_csvs),
        "rep_vs_prod_tvd": rep_vs_prod,
        "rep_to_rep_tvd": rep_to_rep,
        "rep_to_rep_band": list(REP_TO_REP_TVD_BAND),
    }
    (out_dir / "endtoend_tvd.json").write_text(json.dumps(payload, indent=2))
    print("\n=== end-to-end install-year cohort TVD (inventory-weighted) ===")
    print(f"rep_vs_prod: {rep_vs_prod}")
    print(f"rep_to_rep : {rep_to_rep}  band {list(REP_TO_REP_TVD_BAND)}")

    ok = True
    if do_gate and rep_to_rep:
        lo, hi = REP_TO_REP_TVD_BAND
        ok = all(lo <= t <= hi for t in rep_to_rep)
        print(f"endtoend gate (rep_to_rep in band): {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--long", type=Path, required=True, help="long_all.csv panel")
    ap.add_argument("--sample-anchors", type=Path, default=DEFAULT_SAMPLE_ANCHORS,
                    help="chip->stratum join table")
    ap.add_argument("--sample-manifest", type=Path, default=None,
                    help="sample_manifest.json for inventory_weight (fallback if absent)")
    ap.add_argument("--estimators", nargs="+",
                    default=["fpd", "sustained", "pava", "changepoint"])
    ap.add_argument("--epoch-gap-days", type=int, default=16)
    ap.add_argument(
        "--decoder-epoch-gap-days",
        type=int,
        default=30,
        help="changepoint decoder's epoch-collapse threshold (PAVA keeps --epoch-gap-days)",
    )
    ap.add_argument("--flip-rate", type=float, default=0.1)
    ap.add_argument("--credible-mass", type=float, default=0.90)
    ap.add_argument("--fit-emissions", action="store_true",
                    help="fit the changepoint decoder's EmissionModel via EM over the whole "
                         "loaded panel (unsupervised, no install dates used) and inject it "
                         "into the EstimatorConfig used for this run")
    ap.add_argument("--emissions-json", type=Path, default=None,
                    help="load a pre-fitted EmissionModel JSON instead of --fit-emissions")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--targets", type=Path, default=None,
                    help="optional JSON overriding the PUBLISHED gate targets")
    ap.add_argument("--gate", action="store_true", help="run regression assertions")
    ap.add_argument("--endtoend", action="store_true", help="also run 3-rep TVD sub-path")
    ap.add_argument("--reference", type=Path, default=None, help="end-to-end reference.csv")
    ap.add_argument("--rep", type=Path, action="append", default=None,
                    help="per-rep delivery.csv (repeatable; with --endtoend)")
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    unknown = [e for e in a.estimators if e not in available_estimators()]
    if unknown:
        ap.error(f"unknown estimators {unknown}; available: {available_estimators()}")
    if a.emissions_json and a.fit_emissions:
        ap.error("--emissions-json and --fit-emissions are mutually exclusive")

    config = EstimatorConfig(
        epoch_gap_days=a.epoch_gap_days,
        flip_rate=a.flip_rate,
        credible_mass=a.credible_mass,
        decoder_epoch_gap_days=a.decoder_epoch_gap_days,
    )
    panel, chip_of = load_panel(a.long)
    strata = load_strata(a.sample_anchors)
    weights = load_inventory_weights(a.sample_manifest)
    weights_fallback = a.sample_manifest is None or not Path(a.sample_manifest).exists()

    emissions_source = "none"
    if a.emissions_json:
        emissions_model = EmissionModel.from_json(json.loads(Path(a.emissions_json).read_text()))
        config = dataclasses.replace(config, emissions=emissions_model)
        emissions_source = "loaded"
        print(f"[emissions] loaded {a.emissions_json} (strata={list(emissions_model.strata)})")
    elif a.fit_emissions:
        all_sequences = [obs for reps in panel.values() for obs in reps.values()]
        emissions_model = fit_emissions_em(
            all_sequences, gap_days=config.decoder_epoch_gap_days
        )
        config = dataclasses.replace(config, emissions=emissions_model)
        emissions_source = "fitted"
        (a.out_dir / "emissions_fitted.json").write_text(
            json.dumps(emissions_model.to_json(), indent=2)
        )
        print(
            f"[emissions] fitted EM over {len(all_sequences)} sequences "
            f"(strata={list(emissions_model.strata)}) -> {a.out_dir}/emissions_fitted.json"
        )

    results: dict[str, dict] = {}
    for name in a.estimators:
        per_unit, headline = run_estimator(name, panel, chip_of, strata, weights, config)
        results[name] = headline
        _write_summary(a.out_dir, name, headline, config, weights_fallback, emissions_source)
        _write_per_unit(a.out_dir, name, per_unit)
        _write_report(a.out_dir, name, headline)
        unw = headline["overall_unweighted"]
        inv = headline["overall_inventory_weighted"]
        print(
            f"[{name}] map_all unw={unw['map_mode_hit_all']} invw={inv['map_mode_hit_all']} "
            f"| dated={unw['map_mode_hit_dated']} | year={unw['year_mode_hit']} "
            f"| undated_flip={unw['undated_flip']} "
            f"| tiers r/w/c={headline['tier_counts']['rock_solid']}/"
            f"{headline['tier_counts']['wobbly']}/{headline['tier_counts']['chaotic']}"
        )

    ok = True
    if a.gate:
        targets = PUBLISHED
        if a.targets:
            targets = json.loads(a.targets.read_text())
        ok = _gate(results, targets)

    endtoend_ok = True
    if a.endtoend:
        if not a.reference or not a.rep:
            ap.error("--endtoend requires --reference and at least one --rep")
        endtoend_ok = _run_endtoend(a.reference, a.rep, a.out_dir, a.gate)

    print(f"\nwrote {a.out_dir}/summary_*.json + per_unit_*.csv + report_*.md")
    return 0 if (ok and endtoend_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
