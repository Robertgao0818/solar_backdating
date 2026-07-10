#!/usr/bin/env python3
"""Diagnose ISSUE-06 gate-2 dual-FAIL: disagreement taxonomy + frame-level gap.

Reuses ``fidelity_gate`` (same decoder, same tight12 nomarker re-render, same
leakage drop). Emits:

* per-unit teacher/student agree_keys (decoded units)
* disagreement taxonomy (key-class transitions, year-shift of INTERVAL ends)
* frame-level present/absent/unusable confusion (student vs banked teacher)
* stratum × R3 (unusable-bearing) tables
* headline vs ceiling decomposition (splice inflation)

Outputs under ``--out`` (JSON + markdown). Offline, no API.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validation import fidelity_gate as fg  # noqa: E402
from scripts.validation.estimator_endtoend_decode import (  # noqa: E402
    ADOPTED_DECODER_EPOCH_GAP_DAYS,
    ADOPTED_ESTIMATOR,
    DEFAULT_COHORT_PRIOR_JSON,
    DEFAULT_EMISSIONS_JSON,
    DEFAULT_VEXCEL_CSV,
    _REPLACED_PROVIDERS,
    _load_emissions,
    _load_vexcel_ceiling,
    _s,
)
from solar_backdating.eval.scan_state_io import load_scan_observations  # noqa: E402


def _key_class(k: str | None) -> str:
    if k is None:
        return "NONE"
    if k == "UNDATED":
        return "UNDATED"
    if k == fg._MISSING_KEY:
        return "MISSING"
    if k.startswith("AP_BOUND"):
        return "AP_BOUND"
    if k.startswith("INTERVAL"):
        return "INTERVAL"
    return "OTHER"


def _parse_interval(k: str) -> tuple[str | None, str | None]:
    # INTERVAL|start|end  or  AP_BOUND<=|end
    if k.startswith("INTERVAL|"):
        parts = k.split("|")
        return (parts[1] or None, parts[2] or None) if len(parts) >= 3 else (None, None)
    if k.startswith("AP_BOUND"):
        parts = k.split("|")
        return (None, parts[-1] or None) if len(parts) >= 2 else (None, None)
    return (None, None)


def _year(iso: str | None) -> int | None:
    if not iso or len(iso) < 4:
        return None
    try:
        return int(iso[:4])
    except ValueError:
        return None


def frame_confusion(
    scan_path: Path,
    scorer,
    resolver: fg.StudentChipResolver,
    cache: dict,
    scorer_key: str,
    stats: Counter,
) -> dict:
    """Per-frame teacher vs student present/quality confusion on one scan_state."""
    teacher_obs, student_obs, drop = fg.load_paired_observations(
        scan_path,
        scorer,
        cache,
        scorer_key=scorer_key,
        skip_missing=False,
        stats=stats,
        resolver=resolver,
    )
    conf = Counter()
    for t, s in zip(teacher_obs, student_obs):
        # map present flags
        tp = t.pv_present  # "present"/"absent"/""
        sp = s.pv_present
        tq = t.quality_flag or "usable"
        sq = s.quality_flag or "usable"
        # normalize empty -> abstain
        t_lab = "unusable" if tq == "unusable" else (tp or "abstain")
        s_lab = "unusable" if sq == "unusable" else (sp or "abstain")
        conf[(t_lab, s_lab)] += 1
    return {
        "n_frames": len(teacher_obs),
        "n_drop": drop.get("n_dropped", 0),
        "confusion": {f"{a}->{b}": n for (a, b), n in conf.items()},
        "agree_present_absent": sum(
            n
            for (a, b), n in conf.items()
            if a in ("present", "absent") and a == b
        ),
        "n_teacher_decided": sum(
            n for (a, b), n in conf.items() if a in ("present", "absent")
        ),
        "n_both_decided": sum(
            n
            for (a, b), n in conf.items()
            if a in ("present", "absent") and b in ("present", "absent")
        ),
        "both_decided_agree": sum(
            n
            for (a, b), n in conf.items()
            if a in ("present", "absent") and a == b
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--reps", type=Path, nargs="+", required=True)
    ap.add_argument("--scorer", choices=["dinov3_frozen", "dinov2_floor"], required=True)
    ap.add_argument("--head", type=Path, required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--cohort-prior", type=Path, default=DEFAULT_COHORT_PRIOR_JSON)
    ap.add_argument("--emissions", type=Path, default=DEFAULT_EMISSIONS_JSON)
    ap.add_argument("--vexcel-capture-csv", type=Path, default=DEFAULT_VEXCEL_CSV)
    ap.add_argument("--drop-anchors-overlapping", type=Path, required=True)
    ap.add_argument("--chip-targets", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--frame-sample-disagree",
        type=int,
        default=80,
        help="max disagreeing decoded units to open for frame-level confusion",
    )
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    chip_targets = a.chip_targets or (
        Path.home()
        / "zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_targets.csv"
    )
    ref_rows = fg.load_reference_rows(a.reference)
    ref_rows, leakage = fg.drop_overlapping_anchors(ref_rows, a.drop_anchors_overlapping)
    ref_by_sf = {r["sf"]: r for r in ref_rows}
    strat_w = fg.stratum_weights(ref_rows)

    from solar_backdating.estimators.survival import cohort_prior_from_json

    emissions = _load_emissions(a.emissions) if a.emissions else None
    cohort_prior = cohort_prior_from_json(json.loads(Path(a.cohort_prior).read_text()))
    vexcel = _load_vexcel_ceiling(a.vexcel_capture_csv)
    est, config = fg.build_decoder(
        estimator=ADOPTED_ESTIMATOR,
        emissions=emissions,
        cohort_prior=cohort_prior,
        decoder_epoch_gap_days=ADOPTED_DECODER_EPOCH_GAP_DAYS,
    )
    resolver = fg.StudentChipResolver(chip_targets_path=chip_targets)
    from scripts.temporal.presence_scorer import get_scorer

    scorer = get_scorer(a.scorer, device=a.device, head_checkpoint=str(a.head))
    head_sha = fg._sha256_file(a.head)
    scorer_key = f"{a.scorer}|{head_sha}"
    cache: dict = {}
    stats = fg.new_stats()

    # ---- per-rep keys + unit rows ----
    unit_rows: list[dict] = []
    for rep_i, rep_dir in enumerate(a.reps):
        delivery = fg.load_delivery_by_sf(rep_dir / "delivery.csv")
        km = fg.rep_keys(
            rep_dir,
            ref_rows,
            delivery,
            est=est,
            config=config,
            vexcel_ceiling=vexcel,
            teacher_only=False,
            scorer=scorer,
            cache=cache,
            scorer_key=scorer_key,
            skip_missing=False,
            stats=stats,
            resolver=resolver,
            apply_render_drop_to_teacher=True,
        )
        for sf, info in km.items():
            row = ref_by_sf[sf]
            drow = delivery.get(sf, {})
            provider = _s(drow.get("date_provider"))
            unit_rows.append(
                {
                    "rep": rep_i + 1,
                    "sf": sf,
                    "stratum": row["stratum"],
                    "w": row["w"],
                    "provider": provider,
                    "decoded": info["decoded"],
                    "had_unusable": info["had_unusable"],
                    "teacher_key": info["teacher_key"],
                    "student_key": info["student_key"],
                    "agree": int(
                        info["student_key"] is not None
                        and info["teacher_key"] == info["student_key"]
                    ),
                    "t_class": _key_class(info["teacher_key"]),
                    "s_class": _key_class(info["student_key"]),
                    "group_anchor_id": row["group_anchor_id"],
                    "target_anchor_id": row["target_anchor_id"],
                    "grid_id": row["grid_id"],
                }
            )

    decoded = [r for r in unit_rows if r["decoded"]]
    disagree = [r for r in decoded if not r["agree"]]
    agree = [r for r in decoded if r["agree"]]

    # ---- taxonomy ----
    class_trans = Counter((r["t_class"], r["s_class"]) for r in decoded)
    class_trans_disagree = Counter((r["t_class"], r["s_class"]) for r in disagree)

    end_year_deltas = Counter()
    start_year_deltas = Counter()
    interval_vs_interval = 0
    for r in disagree:
        if r["t_class"] == "INTERVAL" and r["s_class"] == "INTERVAL":
            interval_vs_interval += 1
            ts, te = _parse_interval(r["teacher_key"])
            ss, se = _parse_interval(r["student_key"])
            dy_e = None if _year(te) is None or _year(se) is None else _year(se) - _year(te)
            dy_s = None if _year(ts) is None or _year(ss) is None else _year(ss) - _year(ts)
            if dy_e is not None:
                end_year_deltas[dy_e] += 1
            if dy_s is not None:
                start_year_deltas[dy_s] += 1

    # stratum × unusable
    strat_r3 = defaultdict(lambda: {"n": 0, "agree": 0, "n_unusable": 0, "agree_unusable": 0, "n_clean": 0, "agree_clean": 0})
    for r in decoded:
        st = strat_r3[r["stratum"]]
        st["n"] += 1
        st["agree"] += r["agree"]
        if r["had_unusable"]:
            st["n_unusable"] += 1
            st["agree_unusable"] += r["agree"]
        else:
            st["n_clean"] += 1
            st["agree_clean"] += r["agree"]

    # headline decomposition: splice vs decoded
    splice = [r for r in unit_rows if not r["decoded"]]
    n_splice_agree = sum(r["agree"] for r in splice)
    # all-units unweighted
    all_rate = sum(r["agree"] for r in unit_rows) / len(unit_rows) if unit_rows else None
    dec_rate = sum(r["agree"] for r in decoded) / len(decoded) if decoded else None
    splice_rate = n_splice_agree / len(splice) if splice else None

    # inv-weighted rates via fg.agg
    def _rate(rows, pred):
        if not rows:
            return None
        return fg.agg(rows, pred, strat_w)[1]

    # ---- frame-level sample on disagreeing decoded units (rep1 preferred) ----
    frame_stats = Counter()
    frame_conf_total = Counter()
    sampled = 0
    # prefer rep1 for stability; take disagree then some agree for baseline
    candidates = sorted(disagree, key=lambda r: (r["rep"] != 1, r["sf"]))
    frame_rows = []
    for r in candidates:
        if sampled >= a.frame_sample_disagree:
            break
        provider = r["provider"]
        layer_col = _REPLACED_PROVIDERS.get(provider)
        if layer_col is None:
            continue
        layer, anchor_col = layer_col
        # resolve anchor from ref
        ref = ref_by_sf[r["sf"]]
        anchor_id = ref[anchor_col]
        rep_dir = a.reps[r["rep"] - 1]
        scan_path = rep_dir / layer / "scan_states" / f"{anchor_id}.json"
        if not scan_path.exists():
            continue
        fc = frame_confusion(scan_path, scorer, resolver, cache, scorer_key, stats)
        sampled += 1
        for k, n in fc["confusion"].items():
            frame_conf_total[k] += n
            frame_stats["n_frames"] += n
        frame_stats["n_units"] += 1
        frame_stats["both_decided"] += fc["n_both_decided"]
        frame_stats["both_decided_agree"] += fc["both_decided_agree"]
        frame_stats["teacher_decided"] += fc["n_teacher_decided"]
        frame_stats["teacher_decided_student_match"] += fc["agree_present_absent"]
        frame_rows.append(
            {
                "sf": r["sf"],
                "rep": r["rep"],
                "stratum": r["stratum"],
                "had_unusable": r["had_unusable"],
                "teacher_key": r["teacher_key"],
                "student_key": r["student_key"],
                "t_class": r["t_class"],
                "s_class": r["s_class"],
                **fc,
            }
        )

    # also a small agree sample for baseline frame agreement
    agree_frame_conf = Counter()
    agree_frame_n = 0
    agree_both_dec = 0
    agree_both_ok = 0
    for r in sorted(agree, key=lambda x: (x["rep"] != 1, x["sf"]))[:40]:
        provider = r["provider"]
        layer_col = _REPLACED_PROVIDERS.get(provider)
        if layer_col is None:
            continue
        layer, anchor_col = layer_col
        ref = ref_by_sf[r["sf"]]
        anchor_id = ref[anchor_col]
        scan_path = a.reps[r["rep"] - 1] / layer / "scan_states" / f"{anchor_id}.json"
        if not scan_path.exists():
            continue
        fc = frame_confusion(scan_path, scorer, resolver, cache, scorer_key, stats)
        agree_frame_n += 1
        for k, n in fc["confusion"].items():
            agree_frame_conf[k] += n
        agree_both_dec += fc["n_both_decided"]
        agree_both_ok += fc["both_decided_agree"]

    report = {
        "scorer": a.scorer,
        "head": str(a.head),
        "head_sha256": head_sha,
        "n_unit_rows": len(unit_rows),
        "n_decoded": len(decoded),
        "n_splice": len(splice),
        "n_decoded_agree": len(agree),
        "n_decoded_disagree": len(disagree),
        "rates": {
            "all_units_unweighted": all_rate,
            "decoded_unweighted": dec_rate,
            "splice_unweighted": splice_rate,
            "all_units_inv_weighted": _rate(unit_rows, lambda r: r["agree"]),
            "decoded_inv_weighted": _rate(decoded, lambda r: r["agree"]),
            "decoded_clean_inv_weighted": _rate(
                [r for r in decoded if not r["had_unusable"]], lambda r: r["agree"]
            ),
            "decoded_unusable_bearing_inv_weighted": _rate(
                [r for r in decoded if r["had_unusable"]], lambda r: r["agree"]
            ),
        },
        "class_transition_all_decoded": {
            f"{a}->{b}": n for (a, b), n in class_trans.most_common()
        },
        "class_transition_disagree_only": {
            f"{a}->{b}": n for (a, b), n in class_trans_disagree.most_common()
        },
        "interval_end_year_delta_student_minus_teacher": {
            str(k): end_year_deltas[k] for k in sorted(end_year_deltas)
        },
        "interval_start_year_delta_student_minus_teacher": {
            str(k): start_year_deltas[k] for k in sorted(start_year_deltas)
        },
        "n_interval_vs_interval_disagreements": interval_vs_interval,
        "stratum_r3": {
            st: {
                **v,
                "rate": round(v["agree"] / v["n"], 4) if v["n"] else None,
                "rate_unusable": round(v["agree_unusable"] / v["n_unusable"], 4)
                if v["n_unusable"]
                else None,
                "rate_clean": round(v["agree_clean"] / v["n_clean"], 4)
                if v["n_clean"]
                else None,
            }
            for st, v in sorted(strat_r3.items())
        },
        "frame_sample_disagree": {
            "n_units": frame_stats["n_units"],
            "n_frames": frame_stats["n_frames"],
            "confusion": dict(frame_conf_total),
            "both_decided_agreement": (
                round(frame_stats["both_decided_agree"] / frame_stats["both_decided"], 4)
                if frame_stats["both_decided"]
                else None
            ),
            "teacher_decided_student_match_rate": (
                round(
                    frame_stats["teacher_decided_student_match"]
                    / frame_stats["teacher_decided"],
                    4,
                )
                if frame_stats["teacher_decided"]
                else None
            ),
        },
        "frame_sample_agree_baseline": {
            "n_units": agree_frame_n,
            "confusion": dict(agree_frame_conf),
            "both_decided_agreement": (
                round(agree_both_ok / agree_both_dec, 4) if agree_both_dec else None
            ),
        },
        "leakage": leakage,
        "walk_stats": dict(stats),
    }

    (out / "diagnosis.json").write_text(json.dumps(report, indent=2))
    (out / "unit_rows.jsonl").write_text(
        "\n".join(json.dumps(r) for r in unit_rows) + "\n"
    )
    (out / "frame_sample_disagree.json").write_text(json.dumps(frame_rows, indent=2))

    # ---- markdown ----
    L = [
        f"# Gate-2 diagnosis — `{a.scorer}`",
        "",
        f"- head_sha256: `{head_sha}`",
        f"- unit-rows: {len(unit_rows)} (decoded {len(decoded)}, splice {len(splice)})",
        f"- decoded agree/disagree: {len(agree)} / {len(disagree)}",
        "",
        "## Headline decomposition",
        "",
        f"| caliber | unweighted | inv-weighted |",
        f"|---|--:|--:|",
        f"| all-units | {all_rate:.4f} | {report['rates']['all_units_inv_weighted']} |",
        f"| decoded-only | {dec_rate:.4f} | {report['rates']['decoded_inv_weighted']} |",
        f"| splice-through | {splice_rate:.4f} | (identical keys by construction) |",
        f"| decoded + clean (no teacher-unusable) | — | {report['rates']['decoded_clean_inv_weighted']} |",
        f"| decoded + unusable-bearing | — | {report['rates']['decoded_unusable_bearing_inv_weighted']} |",
        "",
        "Splice-through units force teacher_key == student_key (delivery key).",
        f"They are {len(splice)}/{len(unit_rows)} = {len(splice)/len(unit_rows):.1%} of rows and",
        "inflate the all-units headline toward 1.0. The real fidelity signal is decoded-only.",
        "",
        "## Key-class transitions (decoded units)",
        "",
        "### All decoded",
        "",
    ]
    for k, n in class_trans.most_common():
        L.append(f"- `{k[0]} → {k[1]}`: {n}")
    L += ["", "### Disagreements only", ""]
    for k, n in class_trans_disagree.most_common():
        L.append(f"- `{k[0]} → {k[1]}`: {n}")
    L += [
        "",
        f"INTERVAL↔INTERVAL disagreements: **{interval_vs_interval}**",
        "",
        "### End-year delta (student − teacher) on INTERVAL↔INTERVAL mismatches",
        "",
    ]
    for k in sorted(end_year_deltas):
        L.append(f"- Δend={k:+d}y: {end_year_deltas[k]}")
    L += ["", "### Start-year delta", ""]
    for k in sorted(start_year_deltas):
        L.append(f"- Δstart={k:+d}y: {start_year_deltas[k]}")
    L += ["", "## Stratum × R3 (decoded)", ""]
    L.append("| stratum | n | rate | clean n/rate | unusable-bearing n/rate |")
    L.append("|---|--:|--:|---|---|")
    for st, v in report["stratum_r3"].items():
        L.append(
            f"| {st} | {v['n']} | {v['rate']} | "
            f"{v['n_clean']}/{v['rate_clean']} | {v['n_unusable']}/{v['rate_unusable']} |"
        )
    L += [
        "",
        "## Frame-level confusion (sample of disagreeing units)",
        "",
        f"Units sampled: {frame_stats['n_units']}, frames: {frame_stats['n_frames']}",
        f"Both-decided agreement: {report['frame_sample_disagree']['both_decided_agreement']}",
        f"Teacher-decided student match: {report['frame_sample_disagree']['teacher_decided_student_match_rate']}",
        "",
        "Confusion (teacher→student):",
        "",
    ]
    for k, n in sorted(frame_conf_total.items(), key=lambda x: -x[1]):
        L.append(f"- `{k}`: {n}")
    L += [
        "",
        "## Frame-level baseline (sample of agreeing units)",
        "",
        f"Units: {agree_frame_n}, both-decided agreement: "
        f"{report['frame_sample_agree_baseline']['both_decided_agreement']}",
        "",
    ]
    for k, n in sorted(agree_frame_conf.items(), key=lambda x: -x[1]):
        L.append(f"- `{k}`: {n}")
    L += ["", f"Artifacts: `{out}`", ""]
    (out / "diagnosis.md").write_text("\n".join(L) + "\n")
    print(f"wrote {out}/diagnosis.{{json,md}} + unit_rows.jsonl")
    print(
        f"decoded rate={dec_rate:.4f} inv={report['rates']['decoded_inv_weighted']} "
        f"clean_inv={report['rates']['decoded_clean_inv_weighted']} "
        f"unusable_inv={report['rates']['decoded_unusable_bearing_inv_weighted']}"
    )
    print("class transitions (disagree):", dict(class_trans_disagree))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
