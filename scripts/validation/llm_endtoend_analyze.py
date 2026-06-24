#!/usr/bin/env python3
"""Analyse the end-to-end from-scratch backdating rerun: agreement vs production + across reps.

Reads ``reference.csv`` (the production install interval each sampled installation shipped,
column ``prod_agree_key``) and one or more replica delivery CSVs (``--rep delivery.csv``),
then reports, per status_stratum + inventory-weighted (mirroring the §3 frozen-input
analyzer's weighting) + sample-unweighted:

  - rep1 vs production           single-run end-to-end reproducibility (the headline)
  - mean(rep_i vs production)    averaged over all supplied reps
  - 3-way mutual agreement       P(all reps produce the identical interval)
  - majority-of-3 vs production  P(the >=2/3 majority interval == production); no-majority = fail
  - routing-flip diagnostic      production layer -> replica layer transition counts (the seam
                                 documented in select_units: re-derived routing can change which
                                 layer dates an installation)
  - install-year cohort TVD      total-variation distance prod vs each rep (aggregate stability)

"Same install interval" is the canonical agree_key from llm_endtoend_build_reference.py:
  UNDATED  /  AP_BOUND<=|{end} (left-censored)  /  INTERVAL|{start}|{end}.

Outputs: <out-dir>/endtoend_summary.json and endtoend_table.md.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

PROVIDER_LAYER = {
    "gehi_main": "L0",
    "gehi_already_present_bound": "AP_bound",
    "gehi_pertarget": "L1",
    "gehi_census2023": "L_census",
    "": "undated",
}


def _norm_bound(v) -> str:
    s = str(v).strip()
    if s in ("", "nan", "None"):
        return "0"
    try:
        return str(int(float(s)))
    except ValueError:
        return "0"


def _s(v) -> str:
    s = str(v).strip()
    return "" if s in ("nan", "None") else s


def agree_key_raw(r: dict) -> str:
    """Mirror llm_endtoend_build_reference.agree_key on a raw delivery row."""
    if _s(r.get("undated_reason")):
        return "UNDATED"
    if not _s(r.get("date_provider")):
        return "UNDATED"
    if _norm_bound(r.get("date_is_bound")) == "1":
        return f"AP_BOUND<=|{_s(r.get('install_interval_end'))}"
    return f"INTERVAL|{_s(r.get('install_interval_start'))}|{_s(r.get('install_interval_end'))}"


def _layer(provider) -> str:
    return PROVIDER_LAYER.get(_s(provider), "undated")


def _year(r: dict) -> str:
    for c in ("install_date", "install_interval_end", "earliest_present_date"):
        v = _s(r.get(c))
        if len(v) >= 4 and v[:4].isdigit():
            return v[:4]
    return ""


def _tvd(a: Counter, b: Counter) -> float:
    na, nb = sum(a.values()) or 1, sum(b.values()) or 1
    keys = set(a) | set(b)
    return round(0.5 * sum(abs(a[k] / na - b[k] / nb) for k in keys), 4)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--rep", type=Path, action="append", required=True,
                    help="replica delivery.csv (repeatable; order = rep1, rep2, ...).")
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    ref = pd.read_csv(a.reference, dtype=str)
    ref["source_feature_id"] = ref["source_feature_id"].astype(int)
    ref["inv_weight"] = ref["inv_weight"].astype(float)
    prod_key = dict(zip(ref["source_feature_id"], ref["prod_agree_key"]))
    prod_layer = dict(zip(ref["source_feature_id"], ref["prod_date_provider"].map(_layer)))
    prod_year = dict(zip(ref["source_feature_id"], ref["prod_install_year"].fillna("")))
    stratum = dict(zip(ref["source_feature_id"], ref["status_stratum"]))
    invw = dict(zip(ref["source_feature_id"], ref["inv_weight"]))
    sfids = list(ref["source_feature_id"])

    # ---- load replicas ----
    rep_key: list[dict[int, str]] = []
    rep_layer: list[dict[int, str]] = []
    rep_year: list[dict[int, str]] = []
    for rp in a.rep:
        d = pd.read_csv(rp, dtype=str)
        d["source_feature_id"] = d["source_feature_id"].astype(int)
        d = d[d["source_feature_id"].isin(set(sfids))]
        recs = d.to_dict("records")
        rep_key.append({int(r["source_feature_id"]): agree_key_raw(r) for r in recs})
        rep_layer.append({int(r["source_feature_id"]): _layer(r.get("date_provider")) for r in recs})
        rep_year.append({int(r["source_feature_id"]): _year(r) for r in recs})
    nrep = len(a.rep)

    # ---- per-installation booleans ----
    def keys_for(sf):
        return [rk.get(sf, "MISSING") for rk in rep_key]

    rows = []
    for sf in sfids:
        ks = keys_for(sf)
        pk = prod_key[sf]
        vote = Counter(ks)
        maj_key, maj_n = vote.most_common(1)[0]
        has_majority = maj_n >= (nrep // 2 + 1) and nrep >= 2
        rows.append({
            "sf": sf, "stratum": stratum[sf], "w": invw[sf],
            "prod_key": pk,
            "rep1_vs_prod": int(ks[0] == pk),
            "anyrep_vs_prod": [int(k == pk) for k in ks],
            "mutual": int(len(set(ks)) == 1),
            "maj_exists": int(has_majority),
            "maj_vs_prod": int(has_majority and maj_key == pk),
        })

    # ---- aggregate (per stratum + weighted + unweighted) ----
    def agg(selector):
        """selector(row)->0/1 ; returns (unweighted_rate, inv_weighted_rate, per_stratum)."""
        per = defaultdict(lambda: [0, 0])  # stratum -> [hits, n]
        tot = [0, 0]
        for r in rows:
            v = selector(r)
            per[r["stratum"]][0] += v
            per[r["stratum"]][1] += 1
            tot[0] += v
            tot[1] += 1
        # inventory-weighted: per-stratum rate weighted by that stratum's inventory share
        strat_w = {st: ref.loc[ref.status_stratum == st, "inv_weight"].iloc[0] for st in per}
        wsum = sum(strat_w.values()) or 1.0
        wrate = sum(strat_w[st] * (h / n if n else 0) for st, (h, n) in per.items()) / wsum
        return (round(tot[0] / tot[1], 4) if tot[1] else None,
                round(wrate, 4),
                {st: {"n": n, "rate": round(h / n, 4) if n else None} for st, (h, n) in per.items()})

    m_rep1, m_rep1_w, m_rep1_by = agg(lambda r: r["rep1_vs_prod"])
    m_mut, m_mut_w, m_mut_by = agg(lambda r: r["mutual"])
    m_maj, m_maj_w, m_maj_by = agg(lambda r: r["maj_vs_prod"])
    m_majx, m_majx_w, _ = agg(lambda r: r["maj_exists"])
    # mean over reps of rep-vs-prod
    per_rep_vs_prod = []
    for i in range(nrep):
        rate, wrate, _ = agg(lambda r, i=i: r["anyrep_vs_prod"][i])
        per_rep_vs_prod.append({"rep": i + 1, "vs_prod_unweighted": rate, "vs_prod_inv_weighted": wrate})

    # ---- routing-flip diagnostic (prod layer -> rep layer) ----
    flips = Counter()
    flip_examples = defaultdict(list)
    for sf in sfids:
        pl = prod_layer[sf]
        for rl_map in rep_layer:
            rl = rl_map.get(sf, "MISSING")
            if rl != pl:
                flips[(pl, rl)] += 1
                if len(flip_examples[(pl, rl)]) < 5:
                    flip_examples[(pl, rl)].append(sf)
    n_flip_events = sum(flips.values())

    # ---- install-year cohort TVD (prod vs each rep), sample + inventory-weighted ----
    prod_hist = Counter(y for sf in sfids if (y := prod_year[sf]))
    prod_hist_w = Counter()
    for sf in sfids:
        if prod_year[sf]:
            prod_hist_w[prod_year[sf]] += invw[sf]
    tvd = []
    for i, ry in enumerate(rep_year):
        rh = Counter(y for sf in sfids if (y := ry.get(sf, "")))
        rh_w = Counter()
        for sf in sfids:
            y = ry.get(sf, "")
            if y:
                rh_w[y] += invw[sf]
        tvd.append({"rep": i + 1, "year_tvd_unweighted": _tvd(prod_hist, rh),
                    "year_tvd_inv_weighted": _tvd(prod_hist_w, rh_w)})

    report = {
        "experiment": "end-to-end from-scratch backdating rerun vs production (642-installation §3 sample)",
        "model_routing": "L0/L1 round1=gemini-3-flash round2=gemini-3-flash-agent; L_census=gemini-3-flash (production defaults)",
        "n_installations": len(sfids),
        "n_reps": nrep,
        "agree_unit": "install interval (UNDATED / AP_BOUND<= / INTERVAL|start|end)",
        "headline": {
            "rep1_vs_production_unweighted": m_rep1,
            "rep1_vs_production_inv_weighted": m_rep1_w,
            "three_way_mutual_unweighted": m_mut if nrep >= 2 else None,
            "three_way_mutual_inv_weighted": m_mut_w if nrep >= 2 else None,
            "majority_vs_production_unweighted": m_maj if nrep >= 2 else None,
            "majority_vs_production_inv_weighted": m_maj_w if nrep >= 2 else None,
            "majority_exists_rate": m_majx if nrep >= 2 else None,
        },
        "per_rep_vs_production": per_rep_vs_prod,
        "by_stratum": {
            "rep1_vs_prod": m_rep1_by,
            "three_way_mutual": m_mut_by,
            "majority_vs_prod": m_maj_by,
        },
        "routing_flips": {
            "n_flip_events": n_flip_events,
            "n_rep_installation_pairs": len(sfids) * nrep,
            "transitions": {f"{pl}->{rl}": c for (pl, rl), c in flips.most_common()},
            "examples": {f"{pl}->{rl}": ex for (pl, rl), ex in flip_examples.items()},
        },
        "install_year_cohort_tvd": tvd,
    }
    (a.out_dir / "endtoend_summary.json").write_text(json.dumps(report, indent=2))

    # ---- per-installation csv (audit trail) ----
    audit = pd.DataFrame([{
        "source_feature_id": r["sf"], "stratum": r["stratum"],
        "prod_key": r["prod_key"],
        **{f"rep{i+1}_key": rep_key[i].get(r["sf"], "MISSING") for i in range(nrep)},
        "rep1_vs_prod": r["rep1_vs_prod"], "mutual": r["mutual"],
        "maj_vs_prod": r["maj_vs_prod"],
    } for r in rows])
    audit.to_csv(a.out_dir / "endtoend_per_installation.csv", index=False)

    # ---- markdown table ----
    L = ["| metric | unweighted | inventory-weighted |", "|---|--:|--:|",
         f"| rep1 vs production (single-run) | {m_rep1} | {m_rep1_w} |"]
    if nrep >= 2:
        L += [f"| {nrep}-way mutual agreement | {m_mut} | {m_mut_w} |",
              f"| majority-of-{nrep} vs production | {m_maj} | {m_maj_w} |",
              f"| majority exists | {m_majx} | {m_majx_w} |"]
    L += ["", "| stratum | n | rep1-vs-prod | mutual | maj-vs-prod |", "|---|--:|--:|--:|--:|"]
    for st in sorted(m_rep1_by, key=lambda s: -m_rep1_by[s]["n"]):
        L.append(f"| {st} | {m_rep1_by[st]['n']} | {m_rep1_by[st]['rate']} "
                 f"| {m_mut_by.get(st,{}).get('rate')} | {m_maj_by.get(st,{}).get('rate')} |")
    L += ["", f"routing-flip events (prod-layer != rep-layer): {n_flip_events} "
          f"over {len(sfids)*nrep} installation-rep pairs", ""]
    for k, c in flips.most_common(12):
        L.append(f"- {k[0]} -> {k[1]}: {c}")
    L += ["", "install-year cohort TVD (prod vs rep):"]
    for t in tvd:
        L.append(f"- rep{t['rep']}: unweighted {t['year_tvd_unweighted']}, inv-weighted {t['year_tvd_inv_weighted']}")
    (a.out_dir / "endtoend_table.md").write_text("\n".join(L) + "\n")

    print(json.dumps(report["headline"], indent=2))
    print(f"\nrouting-flip events: {n_flip_events} / {len(sfids)*nrep} pairs")
    print(f"wrote {a.out_dir}/endtoend_summary.json + endtoend_table.md + endtoend_per_installation.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
