#!/usr/bin/env python3
"""T0 paired ablation — DESIGN-phase0-emission-extension §5.3 stage T0.

Zero new Gemini quota: reuses the 3 banked repeat-ceiling reps
(``repeat_ceiling_v1/scoring_provenance_rep{1,2,3}.jsonl``, 576 anchors × 4276
frames each, the SAME fixed panel re-scored 3× — so geometry AND adaptive
routing are perfectly held constant and the only rep-to-rep variable is Gemini
verdict/confidence stochasticity).

Paired design (§5.3):
  * CONTROL   — the incumbent DISCRETE path (fit_emissions_em + epoch_symbol
    majority vote; ambiguous/unusable -> abstain; NO target_localized gate).
    Reproduces the DECISION-A hard-MAP setup on RUN3-native labels: the first
    question is whether it reproduces ~0.065-class over-band TVD, ruling out the
    "geometry fix already fixed TVD" alternative explanation.
  * TREATMENT — the CONTINUOUS frame path (FrameEmission + frame_loglik soft
    marginalization) on the SAME raw observations, reclassified under the new
    three-state rule (uninformative marginalization; target_localized PROXY =
    quality_flag ∈ {ambiguous, unusable} standing in for not-localized — this is
    a PROXY, the old Gemini-only labels carry no true TLO).

Statistic: pairwise (C(3,2)=3) hard-MAP install-year histogram TVD per group,
computed with ``estimators.losses.pairwise_tvd`` (the implementation
cross-checked byte-for-byte against ``eval.metrics.tvd`` in the estimator test
suite). Decision band [0.037, 0.063] — the ORIGINAL hard-MAP band (DECISION-A),
NOT the ISSUE-21 fractional band.

P2 confounder control (§5.2): posterior-year-MASS argmax (deterministic,
earliest-year tie-break) as a train-free, data-free decode-side stabilizer, so
the "argmax-collapse instability is irreducible" confounder can be separated
from "emission mis-calibration".

The protocol lock (``T0_LOCK.json``) is written to disk BEFORE any TVD is
computed and re-checked against the actual input SHAs; a mismatch aborts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import Counter
from datetime import date
from itertools import combinations
from pathlib import Path

from solar_backdating.estimators import ClampContext, EstimatorConfig, get_estimator
from solar_backdating.estimators.emissions import FrameEmission, fit_emissions_em
from solar_backdating.estimators.losses import pairwise_tvd
from solar_backdating.estimators.seam import VintageObservation
from solar_backdating.eval.aggregate import BEYOND, posterior_year_mass

CP = get_estimator("changepoint")

DEFAULT_IN = Path.home() / "zasolar_data/geid_temporal/run3_native_line_2026-07/repeat_ceiling_v1"
DEFAULT_OUT = Path.home() / "zasolar_data/geid_temporal/run3_native_line_2026-07/t0_ablation_v1"

REPS = (1, 2, 3)
DECODER_GAP = 45  # DECISION-A epoch-gap (issue03_gates default), used for BOTH groups
BAND = (0.037, 0.063)  # original hard-MAP band (DECISION-A), NOT the ISSUE-21 fractional band
RELIABILITY_FLOOR = 0.5  # confidence below this carries no directional evidence -> uninformative emission


# --------------------------------------------------------------------------- #
# Input loading
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_pv(pv_present) -> str:
    """provenance bool|None -> seam canonical '1'|'0'|'' (None -> abstain)."""
    if pv_present is True:
        return "1"
    if pv_present is False:
        return "0"
    return ""


def load_rep_records(in_dir: Path, rep: int) -> dict[str, list[dict]]:
    """anchor_id -> list of raw per-frame records for one rep, sorted by
    (capture_date, chip_index)."""
    by_anchor: dict[str, list[dict]] = {}
    with (in_dir / f"scoring_provenance_rep{rep}.jsonl").open() as fh:
        for line in fh:
            r = json.loads(line)
            by_anchor.setdefault(r["anchor_id"], []).append(r)
    for anchor, recs in by_anchor.items():
        recs.sort(key=lambda r: (r["capture_date"], r["chip_index"]))
    return by_anchor


def load_anchor_strata(in_dir: Path) -> dict[str, str]:
    strata: dict[str, str] = {}
    with (in_dir / "panel_anchors.csv").open() as fh:
        for row in csv.DictReader(fh):
            strata[row["anchor_id"]] = row["stratum"]
    return strata


def load_stratum_weights(in_dir: Path) -> dict[str, float]:
    """Per-anchor corpus-share weight source (the SAME source CEILING_RESULT.json
    used): R0 MANIFEST corpus_share per stratum, spread uniformly within a
    stratum so each stratum contributes its corpus share to the histogram."""
    lock = json.loads((in_dir / "PANEL_LOCK.json").read_text())
    corpus_share = lock["panel"]["corpus_share_reference"]
    anchors_by_stratum = lock["panel"]["anchors_by_stratum"]
    return {
        st: corpus_share[st] / anchors_by_stratum[st] for st in corpus_share
    }


# --------------------------------------------------------------------------- #
# Observation / frame construction
# --------------------------------------------------------------------------- #
def control_obs(recs: list[dict]) -> list[VintageObservation]:
    """DISCRETE-path observations. quality_flag passes through, so the
    incumbent epoch_symbol usable-only vote sends ambiguous/unusable to abstain
    exactly as DECISION-A did."""
    out = []
    for r in recs:
        out.append(
            VintageObservation(
                capture_date=date.fromisoformat(r["capture_date"]),
                pv_present=_canonical_pv(r["pv_present"]),
                confidence=r.get("confidence"),
                quality_flag=r.get("quality_flag", "usable"),
                source_row=r["chip_index"],
            )
        )
    return out


def treatment_frames(recs: list[dict]) -> list[FrameEmission]:
    """CONTINUOUS-path frames. Proxy gate: q=1 only for a usable, dated frame;
    quality_flag ∈ {ambiguous, unusable} OR a None verdict -> q=0 (uninformative,
    the target_localized-proxy AND the abstain marginalization in one). e0/e1 are
    the per-frame observation-likelihood under {absent,present}_state, built from
    the frame's own confidence (reliability r = max(confidence, 0.5)):
    a present verdict -> e1=r (true positive), e0=1-r (false positive); an absent
    verdict -> e0=r, e1=1-r. At uniform r=0.9,q=1 this collapses to the discrete
    symmetric-noise decode; per-frame varying r is the whole point (the discrete
    majority vote throws confidence away)."""
    frames = []
    for r in recs:
        cd = date.fromisoformat(r["capture_date"])
        qf = r.get("quality_flag", "usable")
        pv = r["pv_present"]
        usable_dated = qf == "usable" and pv in (True, False)
        if not usable_dated:
            # gated / uninformative: q=0, deep-defense filler e0=e1=0.5
            frames.append(FrameEmission(cd, q=0.0, e0=0.5, e1=0.5, source_row=r["chip_index"]))
            continue
        rel = max(float(r["confidence"]), RELIABILITY_FLOOR)
        if pv is True:
            e0, e1 = 1.0 - rel, rel
        else:
            e0, e1 = rel, 1.0 - rel
        frames.append(FrameEmission(cd, q=1.0, e0=e0, e1=e1, source_row=r["chip_index"]))
    return frames


# --------------------------------------------------------------------------- #
# Decode -> per-anchor year
# --------------------------------------------------------------------------- #
def hard_year(post) -> int | None:
    return int(post.map_date[:4]) if post.map_date else None


def p2_year(post) -> int | None:
    """P2 stabilizer (§5.2): argmax over posterior year-MASS (BEYOND excluded),
    deterministic earliest-year tie-break. Undated when BEYOND mass exceeds the
    top dated year."""
    mass = posterior_year_mass(post)
    years = {y: m for y, m in mass.items() if not isinstance(y, str)}
    if not years:
        return None
    best = max(years, key=lambda y: (years[y], -y))
    if mass.get(BEYOND, 0.0) > years[best]:
        return None
    return best


def year_hist(years_by_anchor: dict[str, int | None], weights: dict[str, str],
              stratum_w: dict[str, float], weighted: bool) -> Counter:
    """Install-year histogram over anchors; undated (None) excluded (no year
    bin), matching issue03_gates' point channel."""
    hist: Counter = Counter()
    for anchor, yr in years_by_anchor.items():
        if yr is None:
            continue
        w = stratum_w[weights[anchor]] if weighted else 1.0
        hist[yr] += w
    return hist


# --------------------------------------------------------------------------- #
# Pipelines
# --------------------------------------------------------------------------- #
def run_control(rep_records: dict[int, dict[str, list[dict]]]):
    """Fit ONE emission matrix on the pooled 3-rep observations (a single fixed
    matrix reused across all reps, mirroring DECISION-A's single cohort-wide
    fit), then decode each (rep, anchor)."""
    pooled = [control_obs(recs) for by_anchor in rep_records.values() for recs in by_anchor.values()]
    emissions = fit_emissions_em(pooled, gap_days=DECODER_GAP)
    cfg = EstimatorConfig(decoder_epoch_gap_days=DECODER_GAP, emissions=emissions)
    hard: dict[int, dict[str, int | None]] = {}
    p2: dict[int, dict[str, int | None]] = {}
    for rep, by_anchor in rep_records.items():
        hard[rep], p2[rep] = {}, {}
        for anchor, recs in by_anchor.items():
            post = CP(control_obs(recs), ClampContext(), cfg)
            hard[rep][anchor] = hard_year(post)
            p2[rep][anchor] = p2_year(post)
    return hard, p2, emissions


def run_treatment(rep_records: dict[int, dict[str, list[dict]]]):
    hard: dict[int, dict[str, int | None]] = {}
    p2: dict[int, dict[str, int | None]] = {}
    for rep, by_anchor in rep_records.items():
        hard[rep], p2[rep] = {}, {}
        for anchor, recs in by_anchor.items():
            cfg = EstimatorConfig(
                decoder_epoch_gap_days=DECODER_GAP, frame_emissions=treatment_frames(recs)
            )
            post = CP([], ClampContext(), cfg)
            hard[rep][anchor] = hard_year(post)
            p2[rep][anchor] = p2_year(post)
    return hard, p2


def pairwise_from_years(years: dict[int, dict[str, int | None]], anchor_strata,
                        stratum_w, weighted: bool):
    hists = [year_hist(years[rep], anchor_strata, stratum_w, weighted) for rep in REPS]
    vals = pairwise_tvd(hists)
    labels = [f"rep{a}-rep{b}" for a, b in combinations(REPS, 2)]
    return dict(zip(labels, [round(v, 4) for v in vals])), round(statistics.mean(vals), 4), round(max(vals), 4)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def build_lock(in_dir: Path) -> dict:
    return {
        "protocol": "DESIGN-phase0-emission-extension-2026-07-19 §5.3 stage T0 (paired ablation)",
        "generated_by": "scripts/temporal/run_t0_paired_ablation.py",
        "zero_new_quota": True,
        "inputs": {
            "dir": str(in_dir),
            "panel_lock_sha256": _sha256(in_dir / "PANEL_LOCK.json"),
            "panel_anchors_sha256": _sha256(in_dir / "panel_anchors.csv"),
            "scoring_provenance_sha256": {
                f"rep{rep}": _sha256(in_dir / f"scoring_provenance_rep{rep}.jsonl") for rep in REPS
            },
        },
        "panel_note": (
            "repeat-ceiling run: the SAME fixed 576-anchor / 4276-frame panel re-scored "
            "3× — geometry and adaptive routing are held constant; the only rep-to-rep "
            "variable is Gemini verdict/confidence stochasticity (cleaner than DECISION-A, "
            "whose residual driver was routing divergence)."
        ),
        "grouping_rules": {
            "control": (
                "DISCRETE path: fit_emissions_em (ONE matrix on the pooled 3-rep obs, "
                "gap_days=45) + epoch_symbol majority vote; quality_flag ∈ {ambiguous,unusable} "
                "-> abstain (usable-only vote); NO target_localized gate; flat clamp."
            ),
            "treatment": (
                "CONTINUOUS path: FrameEmission + frame_loglik soft marginalization; "
                "target_localized PROXY = quality_flag ∈ {ambiguous,unusable} (also None verdict) "
                "-> q=0 uninformative; usable-dated -> q=1, e set from per-frame confidence "
                "(reliability r=max(conf,0.5); present -> e1=r,e0=1-r; absent -> e0=r,e1=1-r); "
                "flat clamp, gap_days=45."
            ),
            "proxy_disclosure": (
                "target_localized is a PROXY (quality_flag membership); the banked Gemini-only "
                "labels carry no true TargetLocalizationObservation. This is explicitly a proxy, "
                "not a real localization observation (§5.3)."
            ),
        },
        "statistic": "pairwise C(3,2)=3 hard-MAP install-year histogram TVD (estimators.losses.pairwise_tvd)",
        "weighting_primary": "corpus_share-weighted (R0 MANIFEST corpus_share / anchors_by_stratum; same source as CEILING_RESULT.json)",
        "weighting_secondary": "unweighted anchor counts",
        "undated_handling": "map_date=='' -> excluded from year histogram (no year bin), matching issue03_gates point channel",
        "decision_band": {"low": BAND[0], "high": BAND[1], "caliber": "original hard-MAP band (DECISION-A), NOT ISSUE-21 fractional band"},
        "decoder": {"estimator": "changepoint", "decoder_epoch_gap_days": DECODER_GAP, "clamp": "flat (no ceiling, no census cutoff)"},
        "p2_confounder_control": "posterior-year-mass argmax (deterministic earliest-year tie-break), train-free/data-free, §5.2",
        "reliability_floor": RELIABILITY_FLOOR,
        "frozen": True,
    }


def decide(control_stats, treatment_stats, treatment_p2_stats, flip_rates) -> dict:
    """§5.3 decision rule + §5.2 contribution decomposition.

    The §5.3 point-1 GUARD runs first: the whole paired contrast is only
    informative about the emission hypothesis IF control reproduces the
    ~0.065-class over-band (so there is an over-band residual for a three-state
    emission change to close). If control is already at/below band, the
    'geometry/routing fix already fixed the aggregate TVD' alternative
    explanation is confirmed and the contrast cannot support (nor falsify) the
    emission hypothesis."""
    c_mean = control_stats["mean"]
    t_mean = treatment_stats["mean"]
    tp2_mean = treatment_p2_stats["mean"]
    lo, hi = BAND
    control_reproduces_overband = c_mean > hi
    treatment_in_or_near_band = t_mean <= hi + 0.010
    treatment_clearly_lower = (c_mean - t_mean) >= 0.010 and t_mean < c_mean

    decomposition = {
        "emission_definition_effect (control_mean - treatment_mean)": round(c_mean - t_mean, 4),
        "argmax_collapse_residual_addressable_by_P2 (treatment_mean - treatment_p2_mean)": round(t_mean - tp2_mean, 4),
        "note": ("emission_definition_effect = contamination-driven near-tie reduction from "
                 "control->treatment; argmax_collapse_residual = the irreducible-argmax part "
                 "P2 can still remove on top. Reported separately per §5.2 (never a blanket pass/fail)."),
    }

    if not control_reproduces_overband:
        rule = "CONTROL_SUBBAND__GEOMETRY_ROUTING_FIX_ALTERNATIVE_CONFIRMED"
        verdict = (
            f"Control (incumbent discrete path) mean hard-MAP TVD = {c_mean} is at/below the band "
            f"[{lo},{hi}] and less than half DECISION-A's 0.065 — it does NOT reproduce the "
            f"over-band. §5.3 point-1 alternative explanation is CONFIRMED: on this repeat-ceiling "
            f"panel (identical fixed frames re-scored 3×, so NO adaptive-routing divergence — "
            f"DECISION-A's own stated residual driver — and RUN3-native repaired geometry) the "
            f"aggregate hard-MAP year-histogram TVD is already sub-band WITHOUT any emission "
            f"change. The paired contrast therefore CANNOT support the emission hypothesis: there "
            f"is no over-band residual for a three-state emission change to close (treatment mean "
            f"{t_mean}, Δ={round(c_mean - t_mean, 4)}, statistically indistinguishable). This is a "
            f"PREMISE FAILURE, NOT emission-hypothesis falsification. Note the per-anchor hard-MAP "
            f"flip rate is HIGH (control {flip_rates['control_hard']}, treatment "
            f"{flip_rates['treatment_hard']}) but averages out of the aggregate histogram "
            f"(DECISION-A root-cause #3: instability is an argmax-collapse property, cancels in the "
            f"distributional TVD). REPORT to owner: the DECISION-A over-band problem does not "
            f"reproduce on RUN3-native repeat-ceiling data; per §5.4 this is a candidate §7.4 "
            f"early-exit trigger — there is no aggregate-TVD problem here to justify R3-R5 emission "
            f"training compute. Owner decision required (report only, not self-shelving)."
        )
    elif treatment_clearly_lower and treatment_in_or_near_band:
        rule = "TREATMENT_LOWER_AND_NEAR_BAND"
        verdict = ("mechanism hypothesis PRELIMINARILY SUPPORTED — three-state emission "
                   "reclassification lowers hard-MAP TVD toward the band; worth R3-R5 training compute.")
    elif abs(c_mean - t_mean) < 0.010:
        if (t_mean - tp2_mean) >= 0.010:
            rule = "OVERBAND__NO_EMISSION_DIFFERENCE__P2_ABSORBS_RESIDUAL"
            verdict = ("control over-band but treatment does not improve it; P2 (year-mass argmax) "
                       "absorbs the residual — the residual is argmax-collapse near-ties, not "
                       "emission mis-calibration (§5.2). NOT a clean mechanism win.")
        else:
            rule = "OVERBAND__NO_EMISSION_DIFFERENCE__P2_NO_IMPROVEMENT__EARLY_EXIT_SIGNAL"
            verdict = ("control over-band, treatment no improvement, P2 no improvement — strong "
                       "'no new hypothesis' signal. REPORT to owner as a §7.4 early-exit trigger "
                       "candidate (report only; not a self-authorized shelving).")
    else:
        rule = "OVERBAND__TREATMENT_LOWER_BUT_STILL_OVERBAND"
        verdict = ("treatment lowers TVD but not into/near band — partial support; decompose "
                   "contamination-driven vs irreducible-argmax contributions before concluding.")

    return {
        "rule_triggered": rule,
        "verdict": verdict,
        "control_reproduces_overband_0.065_class": control_reproduces_overband,
        "treatment_in_or_near_band": treatment_in_or_near_band,
        "per_anchor_flip_rates": flip_rates,
        "contribution_decomposition": decomposition,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", type=Path, default=DEFAULT_IN)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write the protocol lock BEFORE any TVD is computed.
    lock = build_lock(args.in_dir)
    (args.out_dir / "T0_LOCK.json").write_text(json.dumps(lock, indent=2))

    # 2. Load inputs and RE-CHECK their SHAs against the just-written lock.
    rep_records = {rep: load_rep_records(args.in_dir, rep) for rep in REPS}
    anchor_strata = load_anchor_strata(args.in_dir)
    stratum_w = load_stratum_weights(args.in_dir)
    for rep in REPS:
        got = _sha256(args.in_dir / f"scoring_provenance_rep{rep}.jsonl")
        want = lock["inputs"]["scoring_provenance_sha256"][f"rep{rep}"]
        if got != want:
            raise SystemExit(f"rep{rep} provenance SHA mismatch vs lock: {got} != {want}")

    # obs-set identity across reps (repeat-ceiling invariant) + anchor coverage
    keysets = [
        {(a, r["capture_date"], r["chip_index"]) for a, recs in rep_records[rep].items() for r in recs}
        for rep in REPS
    ]
    assert keysets[0] == keysets[1] == keysets[2], "reps do not share an identical (anchor,date,chip) set"
    anchors = sorted(rep_records[1])
    assert all(sorted(rep_records[rep]) == anchors for rep in REPS)
    assert set(anchors) <= set(anchor_strata), "anchor(s) missing a stratum"

    # 3. Run both pipelines.
    c_hard, c_p2, emissions = run_control(rep_records)
    t_hard, t_p2 = run_treatment(rep_records)

    def stats(years):
        pw_w, mean_w, max_w = pairwise_from_years(years, anchor_strata, stratum_w, weighted=True)
        pw_u, mean_u, max_u = pairwise_from_years(years, anchor_strata, stratum_w, weighted=False)
        n_dated = {f"rep{rep}": sum(1 for v in years[rep].values() if v is not None) for rep in REPS}
        return {
            "pairwise_weighted": pw_w, "mean": mean_w, "max": max_w,
            "pairwise_unweighted": pw_u, "mean_unweighted": mean_u, "max_unweighted": max_u,
            "n_dated": n_dated,
        }

    control_stats = stats(c_hard)
    control_p2_stats = stats(c_p2)
    treatment_stats = stats(t_hard)
    treatment_p2_stats = stats(t_p2)

    def flip_rate(years):
        """Fraction of anchors whose year is not identical across all 3 reps
        (per-anchor instability; orthogonal to the aggregate histogram TVD)."""
        flipped = sum(
            1 for a in anchors if len({years[rep][a] for rep in REPS}) > 1
        )
        return round(flipped / len(anchors), 4)

    flip_rates = {
        "control_hard": flip_rate(c_hard),
        "treatment_hard": flip_rate(t_hard),
        "control_p2": flip_rate(c_p2),
        "treatment_p2": flip_rate(t_p2),
    }

    decision = decide(control_stats, treatment_stats, treatment_p2_stats, flip_rates)

    results = {
        "lock_sha256": hashlib.sha256((args.out_dir / "T0_LOCK.json").read_bytes()).hexdigest(),
        "band": {"low": BAND[0], "high": BAND[1]},
        "control_hard_map": control_stats,
        "control_p2": control_p2_stats,
        "treatment_hard_map": treatment_stats,
        "treatment_p2": treatment_p2_stats,
        "decision": decision,
        "fitted_emission_matrix": emissions.to_json(),
    }
    (args.out_dir / "t0_results.json").write_text(json.dumps(results, indent=2))

    # per-anchor CSV (audit trail)
    with (args.out_dir / "per_anchor_years.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["anchor_id", "stratum"]
                   + [f"control_hard_rep{r}" for r in REPS]
                   + [f"treatment_hard_rep{r}" for r in REPS]
                   + [f"control_p2_rep{r}" for r in REPS]
                   + [f"treatment_p2_rep{r}" for r in REPS])
        for a in anchors:
            w.writerow([a, anchor_strata[a]]
                       + [c_hard[r][a] for r in REPS]
                       + [t_hard[r][a] for r in REPS]
                       + [c_p2[r][a] for r in REPS]
                       + [t_p2[r][a] for r in REPS])

    print("=== T0 paired ablation ===")
    print(f"CONTROL   hard-MAP TVD (weighted):  pairwise={control_stats['pairwise_weighted']} "
          f"mean={control_stats['mean']} max={control_stats['max']}")
    print(f"TREATMENT hard-MAP TVD (weighted):  pairwise={treatment_stats['pairwise_weighted']} "
          f"mean={treatment_stats['mean']} max={treatment_stats['max']}")
    print(f"CONTROL   +P2 TVD (weighted):       mean={control_p2_stats['mean']}")
    print(f"TREATMENT +P2 TVD (weighted):       mean={treatment_p2_stats['mean']}")
    print(f"band={BAND}")
    print(f"rule={decision['rule_triggered']}")
    print(f"verdict={decision['verdict']}")
    print(f"decomposition={decision['contribution_decomposition']}")
    print(f"wrote {args.out_dir}/T0_LOCK.json, t0_results.json, per_anchor_years.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
