#!/usr/bin/env python3
"""End-to-end decoder-based install-year TVD (ISSUE-02, scan-state track).

Like-for-like estimator swap on TOP of ISSUE-01's ``_run_endtoend`` (see
``estimator_harness.py::_run_endtoend``): for each of the 3 end-to-end reps,
start from that rep's ``delivery.csv`` install-year over the 642
``reference.csv`` sampled installations (byte-identical starting point to
ISSUE-01's TVD), then REPLACE the year for every reference anchor whose date
was produced by that rep's OWN scan (``L0`` group scan or ``L1`` per-target
scan) with a registered estimator's ``map_date[:4]`` decoded straight from that
anchor's raw ``scan_state.json`` (via ``solar_backdating.eval.scan_state_io``).
Anchors whose delivery value came from a different layer (``L_census``,
``gehi_already_present_bound``, or truly undated) are production
splice-through and stay exactly as delivery.csv has them — they are identical
build-inputs across reps by construction of the sampling/splice pipeline, so
they contribute ~0 to the pairwise TVD and are just counted, not decoded.

Anchor-id join (verified 2026-07-03 against llm_endtoend_20260623 rep1-3;
see the accompanying structured report for the exact counts):

* ``date_provider == "gehi_main"``: the record was dated by the L0 GROUP scan.
  ``reference.csv``'s ``group_anchor_id`` column IS the L0
  ``scan_state.json``'s top-level ``anchor_id`` (and, independently, matches
  ``delivery.csv``'s ``source_anchor_id`` column for these rows — verified
  261/261 exact string matches on rep1's sample). Scan state path:
  ``<rep_dir>/L0/scan_states/<group_anchor_id>.json``.
* ``date_provider == "gehi_pertarget"``: the record was dated by the L1
  PER-TARGET scan. **Do NOT use ``delivery.csv``'s ``source_anchor_id`` for
  this provider** — it is empty/NaN for every ``gehi_pertarget`` row. Root
  cause (production bug, not introduced here): ``merge_three_layers.py``'s
  ``date_record()`` reads ``row.get("anchor_id", "")`` for the L1 layer, but
  ``recovered_polygons_pertarget.csv``'s per-target id column is actually
  named ``target_anchor_id``, so the lookup always misses and silently writes
  ``""``. The adapter instead joins via ``reference.csv``'s own
  ``target_anchor_id`` column, which ``llm_endtoend_build_reference.py``
  populates directly from the per-target anchor id and is unaffected by that
  bug (verified: all 23 ``gehi_pertarget`` sample rows on rep1 resolve to an
  existing ``L1/scan_states/<target_anchor_id>.json``). Scan state path:
  ``<rep_dir>/L1/scan_states/<target_anchor_id>.json``.
* ``L_census`` has no ``scan_states`` dir at all (per the ISSUE-02 spec) —
  ``gehi_census2023``/``gehi_already_present_bound``/undated rows are never
  decoded, only passed through from ``delivery.csv``.

Band interpretation (per ISSUE-02 spec, explicit): the established pairwise
rep-to-rep TVD band from ISSUE-01/ISSUE-04 is ``[0.037, 0.063]``. A decoder
value BELOW 0.037 means MORE reproducible than the production reference
computation, not worse — so this script gates ONLY the upper edge (0.063) for
the decoder channel and reports (never fails) values below the lower edge as
"more reproducible than production." The unmodified-delivery reference
channel is reproduced verbatim as a sanity check and must exact-match the
ISSUE-01 banked numbers (rep_to_rep == [0.0595, 0.0625, 0.0374]); that channel
is NOT re-gated here (it already passed ISSUE-01's own gate).

NOTE (PRD-AMENDMENT-P1, ISSUE-22): the ``[0.037, 0.063]`` band is now RETIRED.
The upper-edge check is kept purely as a DIAGNOSTIC-ONLY report — the
``gate_pass_upper_edge_only`` field in the output JSON is informational and
``main()`` still always returns 0. This script never gated the process exit
code; the retirement only makes the JSON/print labeling explicit.

DEFAULT ESTIMATOR (PRD-AMENDMENT-P1 §A4, owner-signed 2026-07-05, Option A):
``--estimator`` now defaults to the **changepoint** posterior decoder + EB prior
at the signed working point (``--decoder-epoch-gap-days 45``, EM emissions,
``cohort_prior.json``); the flat-prior/PAVA path is opt-in via ``--estimator
pava`` (or ``--no-cohort-prior`` / ``--no-emissions``). An ``estimator_id`` +
input-hash provenance block is written into the output JSON (see
``_build_estimator_provenance``), mirroring ``scripts.temporal.scoring_provenance``.

Emissions: pass ``--emissions-json`` to load a pre-fitted
``solar_backdating.estimators.emissions.EmissionModel`` for a
config-emissions-aware estimator (e.g. ``changepoint``, once registered) —
imported lazily so this script has zero hard dependency on that module having
landed. Clamp: if ``--vexcel-capture-csv`` resolves (default: ZAsolar's
``data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv``, the exact
per-grid ceiling ``merge_three_layers.py`` itself uses), a per-anchor
``ClampContext(ceiling_date=...)`` is built from the anchor's ``grid_id``;
otherwise the run is unclamped. Either way the choice is recorded in the
output JSON, never silently assumed.

Output: ``<out-dir>/endtoend_decode_tvd.json``.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
from collections import Counter
from datetime import date
from itertools import combinations
from pathlib import Path

from solar_backdating.estimators import (
    ClampContext,
    EstimatorConfig,
    available_estimators,
    get_estimator,
)
from solar_backdating.eval.metrics import tvd
from solar_backdating.eval.panel_io import (
    install_year,
    load_endtoend_delivery,
    load_endtoend_reference,
)
from solar_backdating.eval.scan_state_io import load_scan_observations

DEFAULT_ROOT = Path.home() / "zasolar_data/geid_temporal/llm_endtoend_20260623"
DEFAULT_OUT = DEFAULT_ROOT / "analysis_decoder"
DEFAULT_VEXCEL_CSV = Path(
    "/home/gaosh/projects/ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv"
)

# --- DECISION-A / PRD-AMENDMENT-P1 (A4) signed production working point ------
# The validation-chain default estimator is now the changepoint posterior
# decoder + EB/Turnbull cohort prior (owner-signed 2026-07-05, Option A). The
# baselines (pava/fpd/sustained) IGNORE every field below (pava reads only
# ``epoch_gap_days``; fpd/sustained read no config), so passing an explicit
# ``--estimator pava``/``sustained`` reproduces the pre-flip numbers byte-for-byte
# even with these defaults populated — the decoder working point is inert for them.
ADOPTED_ESTIMATOR = "changepoint"
ADOPTED_DECODER_EPOCH_GAP_DAYS = 45  # A4 signed epoch-gap (code default is 30)
DEFAULT_COHORT_PRIOR_JSON = (
    Path.home() / "zasolar_data/geid_temporal/issue03_gates_20260704/cohort_prior.json"
)  # A4 EB/Turnbull prior fit once on the 15,859-state production cohort
DEFAULT_EMISSIONS_JSON = (
    Path.home()
    / "zasolar_data/geid_temporal/panel_repair_20260703/analysis_estimator_harness_extended/emissions_fitted.json"
)  # A4 EM-fitted 3-symbol emission matrix (== issue03_gates DEFAULT_EMISSIONS)
# Adopted code refs (DECISION-A gate table + PRD-AMENDMENT-P1 A4), pinned so the
# provenance sidecar records exactly which decoder/prior implementation shipped.
ADOPTED_CODE_REFS = {
    "issue02_store_backed_endtoend_decoder": "89496dd",
    "issue03_eb_prior_addon": "1daa61d",
}
DECISION_REFS = (
    "docs/replan_v2/DECISION-A-estimator-adoption-2026-07-04.md",
    "docs/replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md",
)

# Mirrors estimator_harness.REP_TO_REP_TVD_BAND (kept as a separate literal —
# this script is intentionally decoupled from the harness CLI's argument
# surface per the file whitelist in the ISSUE-02 spec). RETIRED as a live gate
# per PRD-AMENDMENT-P1 (ISSUE-22): the upper-edge check below is report-only.
REP_TO_REP_TVD_BAND = (0.037, 0.063)

# date_provider -> (scan layer subdir, reference.csv anchor-id column to join on).
_REPLACED_PROVIDERS = {
    "gehi_main": ("L0", "group_anchor_id"),
    "gehi_pertarget": ("L1", "target_anchor_id"),
}


def _s(v: object) -> str:
    s = str(v).strip()
    return "" if s in ("", "nan", "None") else s


def _load_vexcel_ceiling(path: Path | None) -> dict[str, date]:
    """``grid_id -> last_capture_date`` (present-side clamp ceiling). ``{}`` if absent."""
    if path is None or not Path(path).exists():
        return {}
    out: dict[str, date] = {}
    with Path(path).open(newline="") as fh:
        for row in csv.DictReader(fh):
            gid = _s(row.get("grid_id"))
            raw = _s(row.get("last_capture_date"))
            if not gid or not raw:
                continue
            try:
                out[gid] = date.fromisoformat(raw[:10])
            except ValueError:
                continue
    return out


def _load_emissions(path: Path | None):
    """Load a pre-fitted EmissionModel. Lazy import: ``emissions.py`` is a
    parallel ISSUE-02 workstream and may not exist yet at call time; only
    touched when ``--emissions-json`` is actually passed."""
    if path is None:
        return None
    from solar_backdating.estimators.emissions import EmissionModel  # noqa: PLC0415

    return EmissionModel.from_json(json.loads(Path(path).read_text()))


def _build_config(
    *, epoch_gap_days: int | None, decoder_epoch_gap_days: int | None, emissions,
    cohort_prior=None,
) -> EstimatorConfig:
    """Construct ``EstimatorConfig`` defensively via field introspection
    (getattr-with-default pattern) so this script works whether or not the
    ISSUE-02 decoder-specific fields (``decoder_epoch_gap_days`` /
    ``emissions`` / ``cohort_prior``) have landed on ``EstimatorConfig`` yet.

    ``cohort_prior`` (ISSUE-03) is threaded through the same introspection
    guard: it is only set when non-``None`` AND the field exists, so ``None``
    leaves the payload byte-identical to the pre-ISSUE-03 flat-prior run."""
    names = {f.name for f in dataclasses.fields(EstimatorConfig)}
    kwargs: dict = {}
    if epoch_gap_days is not None and "epoch_gap_days" in names:
        kwargs["epoch_gap_days"] = epoch_gap_days
    if decoder_epoch_gap_days is not None and "decoder_epoch_gap_days" in names:
        kwargs["decoder_epoch_gap_days"] = decoder_epoch_gap_days
    if emissions is not None and "emissions" in names:
        kwargs["emissions"] = emissions
    if cohort_prior is not None and "cohort_prior" in names:
        kwargs["cohort_prior"] = cohort_prior
    return EstimatorConfig(**kwargs)


def _load_reference_rows(
    reference_csv: Path,
) -> tuple[list[dict], dict[int, float], dict[int, str]]:
    ref = load_endtoend_reference(reference_csv)
    rows = [
        {
            "sf": int(r["source_feature_id"]),
            "group_anchor_id": r["group_anchor_id"],
            "target_anchor_id": r["target_anchor_id"],
            "grid_id": r["grid_id"],
            "status_stratum": r["status_stratum"],
        }
        for r in ref.to_dict("records")
    ]
    invw = {int(sf): float(w) for sf, w in zip(ref["source_feature_id"], ref["inv_weight"])}
    prod_year = {
        int(sf): (_s(y) or "") for sf, y in zip(ref["source_feature_id"], ref["prod_install_year"])
    }
    return rows, invw, prod_year


def decode_rep(
    rep_dir: Path,
    ref_rows: list[dict],
    estimator_name: str,
    *,
    epoch_gap_days: int | None,
    decoder_epoch_gap_days: int | None,
    emissions,
    vexcel_ceiling: dict[str, date],
    cohort_prior=None,
) -> dict:
    """One rep, one estimator: baseline (delivery) + decoded year per sfid + counts."""
    est = get_estimator(estimator_name)
    delivery = load_endtoend_delivery(rep_dir / "delivery.csv")
    sfids = {row["sf"] for row in ref_rows}
    delivery = delivery[delivery["source_feature_id"].isin(sfids)]
    delivery_by_sf = {int(r["source_feature_id"]): r for r in delivery.to_dict("records")}

    baseline_year: dict[int, str] = {}
    decode_year: dict[int, str] = {}
    provider_counts: Counter = Counter()
    n_replaced = 0
    n_scan_state_missing = 0
    n_undated_after_decode = 0
    missing_examples: list[str] = []

    for row in ref_rows:
        sf = row["sf"]
        drow = delivery_by_sf.get(sf)
        if drow is None:
            baseline_year[sf] = ""
            decode_year[sf] = ""
            provider_counts["missing_from_delivery"] += 1
            continue
        by = install_year(drow) or ""
        baseline_year[sf] = by
        decode_year[sf] = by

        provider = _s(drow.get("date_provider"))
        provider_counts[provider or "undated_or_other"] += 1
        layer_col = _REPLACED_PROVIDERS.get(provider)
        if layer_col is None:
            continue  # production splice-through (L_census / AP-bound / undated): keep as-is

        layer, anchor_col = layer_col
        anchor_id = row[anchor_col]
        scan_path = rep_dir / layer / "scan_states" / f"{anchor_id}.json"
        if not scan_path.exists():
            n_scan_state_missing += 1
            if len(missing_examples) < 5:
                missing_examples.append(str(scan_path))
            continue

        obs = load_scan_observations(scan_path)
        clamp = ClampContext(ceiling_date=vexcel_ceiling.get(row["grid_id"]))
        config = _build_config(
            epoch_gap_days=epoch_gap_days,
            decoder_epoch_gap_days=decoder_epoch_gap_days,
            emissions=emissions,
            cohort_prior=cohort_prior,
        )
        posterior = est(obs, clamp, config)
        year = posterior.map_date[:4] if posterior.map_date else ""
        decode_year[sf] = year
        n_replaced += 1
        if not year:
            n_undated_after_decode += 1

    return {
        "baseline_year": baseline_year,
        "decode_year": decode_year,
        "n_reference": len(ref_rows),
        "n_replaced": n_replaced,
        "n_scan_state_missing": n_scan_state_missing,
        "missing_examples": missing_examples,
        "n_undated_after_decode": n_undated_after_decode,
        "provider_counts": dict(provider_counts),
    }


def _hist(year_map: dict[int, str], sfids: list[int], invw: dict[int, float]) -> Counter:
    c: Counter = Counter()
    for sf in sfids:
        y = year_map.get(sf, "")
        if y:
            c[y] += invw[sf]
    return c


def _pairwise_tvd(hists: dict[str, Counter], reps: list[str]) -> list[float]:
    return [round(tvd(hists[a], hists[b]), 4) for a, b in combinations(reps, 2)]


def _band_report(rep_to_rep: list[float]) -> dict:
    lo, hi = REP_TO_REP_TVD_BAND
    within_upper_edge = all(v <= hi for v in rep_to_rep) if rep_to_rep else True
    n_below = sum(1 for v in rep_to_rep if v < lo)
    return {
        "band": list(REP_TO_REP_TVD_BAND),
        "values": rep_to_rep,
        # Report-only: the band is a RETIRED caliber (PRD-AMENDMENT-P1, ISSUE-22).
        # The key name is kept for artifact backward-compat, but this is no longer
        # a gate — it never affects the exit code (main() always returns 0); it is
        # purely a diagnostic upper-edge comparison.
        "gate_pass_upper_edge_only": within_upper_edge,
        "status": "diagnostic_only_band_retired_PRD-AMENDMENT-P1_ISSUE-22",
        "n_below_lower_edge": n_below,
        "interpretation": (
            "DIAGNOSTIC-ONLY (band retired per PRD-AMENDMENT-P1, ISSUE-22): the "
            "upper-edge (<=0.063) check is reported, not gated; values below 0.037 "
            "mean the decoder is MORE reproducible than the production reference "
            "computation and are reported as better, not failed"
        ),
    }


def _build_estimator_provenance(
    *,
    estimator_id: str,
    resolved_decoder_epoch_gap_days: int | None,
    resolved_epoch_gap_days: int | None,
    emissions_json: Path | None,
    cohort_prior_json: Path | None,
    clamp_used: bool,
) -> dict:
    """DECISION-A / PRD-AMENDMENT-P1 (A4) estimator-adoption provenance block.

    Mirrors ``scripts.temporal.scoring_provenance``'s sha256 discipline exactly:
    the cohort-prior / emissions inputs are digested with the bare-hex FILE
    sha256 (``ChipHasher.sha256`` — the same format as the DECISION-A
    ``cohort_prior.json`` pin ``dc67dc9c…``), and the assembled block is
    fingerprinted with ``canonical_hash`` (``"sha256:<hex>"``, order-independent)
    so one digest pins estimator id + working point + code refs + input hashes.

    Imported lazily (like emissions/survival elsewhere in this file) to keep the
    validation chain decoupled from the ``scripts.temporal`` scorer package at
    module load. ChipHasher never raises — a missing/unreadable input is recorded
    in ``*_sha256_error`` rather than aborting the run."""
    from scripts.temporal.scoring_provenance import (  # noqa: PLC0415
        ChipHasher,
        canonical_hash,
    )

    hasher = ChipHasher()
    prior_sha, prior_err = hasher.sha256(str(cohort_prior_json) if cohort_prior_json else None)
    emis_sha, emis_err = hasher.sha256(str(emissions_json) if emissions_json else None)
    block = {
        "estimator_id": estimator_id,
        "is_adopted_default": estimator_id == ADOPTED_ESTIMATOR,
        "adopted_default_estimator": ADOPTED_ESTIMATOR,
        "decision_refs": list(DECISION_REFS),
        "adopted_code_refs": dict(ADOPTED_CODE_REFS),
        "working_point": {
            "decoder_epoch_gap_days": resolved_decoder_epoch_gap_days,
            "epoch_gap_days": resolved_epoch_gap_days,
            "clamp_used": clamp_used,
            "cohort_prior_json": str(cohort_prior_json) if cohort_prior_json else None,
            "cohort_prior_sha256": prior_sha,
            "cohort_prior_sha256_error": prior_err,
            "emissions_json": str(emissions_json) if emissions_json else None,
            "emissions_sha256": emis_sha,
            "emissions_sha256_error": emis_err,
        },
    }
    # Order-independent digest over the whole block (before self-insertion).
    block["provenance_sha256"] = canonical_hash(block)
    return block


def _run_channel(
    channel_name: str,
    reps: list[str],
    root: Path,
    ref_rows: list[dict],
    invw: dict[int, float],
    prod_year: dict[int, str],
    sfids: list[int],
    estimator_name: str,
    **decode_kwargs,
) -> dict:
    per_rep = {
        rep: decode_rep(root / rep, ref_rows, estimator_name, **decode_kwargs) for rep in reps
    }
    decode_hists = {rep: _hist(per_rep[rep]["decode_year"], sfids, invw) for rep in reps}
    delivery_hists = {rep: _hist(per_rep[rep]["baseline_year"], sfids, invw) for rep in reps}
    prod_hist = _hist(prod_year, sfids, invw)

    decode_rep_vs_prod = [round(tvd(prod_hist, decode_hists[rep]), 4) for rep in reps]
    decode_rep_to_rep = _pairwise_tvd(decode_hists, reps)
    delivery_rep_vs_prod = [round(tvd(prod_hist, delivery_hists[rep]), 4) for rep in reps]
    delivery_rep_to_rep = _pairwise_tvd(delivery_hists, reps)

    return {
        "channel": channel_name,
        "estimator": estimator_name,
        "reps": reps,
        "per_rep_replacement_counts": {
            rep: {
                "n_reference": per_rep[rep]["n_reference"],
                "n_replaced": per_rep[rep]["n_replaced"],
                "n_scan_state_missing": per_rep[rep]["n_scan_state_missing"],
                "missing_examples": per_rep[rep]["missing_examples"],
                "n_undated_after_decode": per_rep[rep]["n_undated_after_decode"],
                "provider_counts": per_rep[rep]["provider_counts"],
            }
            for rep in reps
        },
        "decoder": {
            "rep_vs_prod_tvd": decode_rep_vs_prod,
            "rep_to_rep_tvd": decode_rep_to_rep,
            "band_verdict": _band_report(decode_rep_to_rep),
        },
        "unmodified_delivery_reference": {
            "rep_vs_prod_tvd": delivery_rep_vs_prod,
            "rep_to_rep_tvd": delivery_rep_to_rep,
            "note": (
                "identical computation to ISSUE-01 estimator_harness.py --endtoend "
                "(no scan-state decode); included here so both channels come from "
                "the SAME run for an apples-to-apples diff, and as an exact-match "
                "regression check against the ISSUE-01 banked numbers"
            ),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="llm_endtoend_20260623 root")
    ap.add_argument("--reps", nargs="+", default=["rep1", "rep2", "rep3"])
    ap.add_argument(
        "--estimator",
        default=ADOPTED_ESTIMATOR,
        help="primary estimator. DEFAULT flipped to 'changepoint' per PRD-AMENDMENT-P1 "
        "§A4 (owner-signed 2026-07-05). Pass 'pava'/'sustained'/'fpd' to override; those "
        "baselines ignore the decoder working-point defaults below, so an explicit "
        "baseline run is byte-identical to the pre-flip behaviour.",
    )
    ap.add_argument(
        "--no-pava-sanity",
        action="store_true",
        help="skip the always-on pava sanity column (default: run it alongside --estimator)",
    )
    ap.add_argument(
        "--emissions-json",
        type=Path,
        default=DEFAULT_EMISSIONS_JSON,
        help="EM-fitted EmissionModel JSON. DEFAULT = canonical A4 emissions; "
        "use --no-emissions for symmetric-noise (flat) emissions. Never opened for an "
        "explicit non-changepoint --estimator (ignored there regardless of this flag).",
    )
    ap.add_argument(
        "--no-emissions",
        action="store_true",
        help="disable emissions (symmetric noise); overrides --emissions-json default",
    )
    ap.add_argument(
        "--cohort-prior-json",
        type=Path,
        default=DEFAULT_COHORT_PRIOR_JSON,
        help="ISSUE-03 CohortPrior JSON injected into the decoder. DEFAULT = canonical "
        "A4 EB/Turnbull prior; use --no-cohort-prior for a flat prior. Never opened for "
        "an explicit non-changepoint --estimator (ignored there regardless of this flag).",
    )
    ap.add_argument(
        "--no-cohort-prior",
        action="store_true",
        help="disable the EB cohort prior (flat prior); overrides --cohort-prior-json default",
    )
    ap.add_argument(
        "--decoder-epoch-gap-days",
        type=int,
        default=ADOPTED_DECODER_EPOCH_GAP_DAYS,
        help="changepoint epoch-collapse threshold. DEFAULT = 45 per A4 signed working "
        "point (code/EstimatorConfig default is 30; PAVA keeps --epoch-gap-days).",
    )
    ap.add_argument("--epoch-gap-days", type=int, default=None)
    ap.add_argument("--vexcel-capture-csv", type=Path, default=DEFAULT_VEXCEL_CSV)
    ap.add_argument(
        "--no-clamp", action="store_true", help="force unclamped even if the CSV resolves"
    )
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args()

    # Escape hatches for the A4 default flip: clear the canonical prior/emissions
    # so the decoder can be run flat again. (No-op for pava/fpd/sustained, which
    # ignore both fields regardless.)
    if a.no_emissions:
        a.emissions_json = None
    if a.no_cohort_prior:
        a.cohort_prior_json = None

    # Load-gate on the adopted estimator: an explicit non-changepoint --estimator
    # must not touch the prior/emissions files at all (not even to open-then-ignore)
    # so a baseline run has zero file-existence dependency on the canonical A4
    # artifacts, same as before the default flip. This clears an explicitly-passed
    # --cohort-prior-json/--emissions-json too, not just the defaults: both fields
    # are equally inert for pava/fpd/sustained, so there is no numeric difference
    # to preserve either way, and skipping unconditionally is the simpler rule.
    if a.estimator != ADOPTED_ESTIMATOR:
        a.emissions_json = None
        a.cohort_prior_json = None

    if a.estimator not in available_estimators():
        ap.error(f"unknown estimator {a.estimator!r}; available: {available_estimators()}")

    a.out_dir.mkdir(parents=True, exist_ok=True)

    ref_rows, invw, prod_year = _load_reference_rows(a.root / "reference.csv")
    sfids = [row["sf"] for row in ref_rows]

    vexcel_ceiling = {} if a.no_clamp else _load_vexcel_ceiling(a.vexcel_capture_csv)
    clamp_used = bool(vexcel_ceiling)

    emissions = _load_emissions(a.emissions_json)
    cohort_prior = None
    if a.cohort_prior_json:
        from solar_backdating.estimators.survival import cohort_prior_from_json  # noqa: PLC0415

        cohort_prior = cohort_prior_from_json(json.loads(Path(a.cohort_prior_json).read_text()))

    decode_kwargs = dict(
        epoch_gap_days=a.epoch_gap_days,
        decoder_epoch_gap_days=a.decoder_epoch_gap_days,
        emissions=emissions,
        vexcel_ceiling=vexcel_ceiling,
        cohort_prior=cohort_prior,
    )

    # Resolve the ACTUAL gap values that will be threaded into every
    # _build_config() call below (CLI arg if given, else EstimatorConfig's
    # own default) so the output JSON can record which config produced it —
    # this CLI-controlled lever moves the TVD numbers materially, same as the
    # already-recorded clamp choice.
    _resolved_cfg = _build_config(
        epoch_gap_days=a.epoch_gap_days,
        decoder_epoch_gap_days=a.decoder_epoch_gap_days,
        emissions=None,
    )
    resolved_epoch_gap_days = getattr(_resolved_cfg, "epoch_gap_days", None)
    resolved_decoder_epoch_gap_days = getattr(_resolved_cfg, "decoder_epoch_gap_days", None)

    channels = [
        _run_channel(
            "primary",
            a.reps,
            a.root,
            ref_rows,
            invw,
            prod_year,
            sfids,
            a.estimator,
            **decode_kwargs,
        )
    ]
    if a.estimator != "pava" and not a.no_pava_sanity:
        channels.append(
            _run_channel(
                "pava_sanity",
                a.reps,
                a.root,
                ref_rows,
                invw,
                prod_year,
                sfids,
                "pava",
                **decode_kwargs,
            )
        )

    payload = {
        "n_reference_installations": len(ref_rows),
        "reps": a.reps,
        "gap_days": {
            "epoch_gap_days_cli": a.epoch_gap_days,
            "decoder_epoch_gap_days_cli": a.decoder_epoch_gap_days,
            "epoch_gap_days_resolved": resolved_epoch_gap_days,
            "decoder_epoch_gap_days_resolved": resolved_decoder_epoch_gap_days,
        },
        "clamp": {
            "used": clamp_used,
            "source_csv": str(a.vexcel_capture_csv) if clamp_used else None,
            "n_grids_with_ceiling": len(vexcel_ceiling),
        },
        "emissions_json": str(a.emissions_json) if a.emissions_json else None,
        "cohort_prior_json": str(a.cohort_prior_json) if a.cohort_prior_json else None,
        # DECISION-A / PRD-AMENDMENT-P1 (A4) estimator-adoption provenance: estimator
        # id + working point + code refs + bare-hex file sha256 of the prior/emissions
        # inputs + an order-independent canonical_hash fingerprint of the whole block.
        "provenance": _build_estimator_provenance(
            estimator_id=a.estimator,
            resolved_decoder_epoch_gap_days=resolved_decoder_epoch_gap_days,
            resolved_epoch_gap_days=resolved_epoch_gap_days,
            emissions_json=a.emissions_json,
            cohort_prior_json=a.cohort_prior_json,
            clamp_used=clamp_used,
        ),
        "channels": channels,
    }
    out_path = a.out_dir / "endtoend_decode_tvd.json"
    out_path.write_text(json.dumps(payload, indent=2))

    for ch in channels:
        print(f"\n=== channel={ch['channel']} estimator={ch['estimator']} ===")
        print(f"decoder rep_vs_prod : {ch['decoder']['rep_vs_prod_tvd']}")
        print(f"decoder rep_to_rep  : {ch['decoder']['rep_to_rep_tvd']}  "
              f"[diagnostic-only, band retired per PRD-AMENDMENT-P1/ISSUE-22] "
              f"upper<=0.063? {ch['decoder']['band_verdict']['gate_pass_upper_edge_only']}")
        print(f"delivery rep_vs_prod: {ch['unmodified_delivery_reference']['rep_vs_prod_tvd']}")
        print(f"delivery rep_to_rep : {ch['unmodified_delivery_reference']['rep_to_rep_tvd']}")

    prov = payload["provenance"]
    print(
        f"\nprovenance: estimator_id={prov['estimator_id']} "
        f"(adopted_default={prov['is_adopted_default']}) "
        f"gap={prov['working_point']['decoder_epoch_gap_days']} "
        f"prior_sha256={prov['working_point']['cohort_prior_sha256']} "
        f"{prov['provenance_sha256']}"
    )
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
