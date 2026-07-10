#!/usr/bin/env python3
"""Hard-example strips + counterfactual repair for gate-2 residual on done_appears.

Answers DATA-fidelity-gate2 next-step (a): can fixing **transition FP** and
**unusable forced-decide** lift ``done_appears`` interval agreement from ~0.45
toward the teacher self-consistency ceiling (~0.77)?

Inputs (reuses the locked gate-2 path):
  * banked storebacked reps + reference.csv
  * dinov2_floor (or dinov3) head + tight12 nomarker re-render
  * Phase-0 changepoint decoder (same emissions / cohort prior / Vexcel clamp)

Outputs under ``--out``:
  * ``counterfactual.json`` / ``.md`` — upper bounds if those frame errors vanish
  * ``hard_examples.csv`` — catalog of tagged error frames
  * ``strips/hard_examples.html`` — portable thumbnail strips (teacher vs student)

Offline once chips are banked; GPU only for student re-score (cacheable).
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
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
from solar_backdating.estimators.seam import ClampContext, VintageObservation  # noqa: E402

PLACEHOLDER = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
    "DUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
THUMB = 160


def _lab(obs: VintageObservation) -> str:
    q = obs.quality_flag or "usable"
    if q == "unusable":
        return "unusable"
    p = obs.pv_present or ""
    if p in ("1", "present", True):
        return "present"
    if p in ("0", "absent", False):
        return "absent"
    return "abstain"


def _tag_frame(t: VintageObservation, s: VintageObservation) -> str | None:
    """Return error class for this frame pair, or None if not an error of interest."""
    tl, sl = _lab(t), _lab(s)
    if tl == "unusable" and sl in ("present", "absent"):
        return "unusable_forced_decide"
    if tl == "absent" and sl == "present":
        return "transition_fp"
    if tl == "present" and sl == "absent":
        return "transition_fn"
    if tl in ("present", "absent") and sl == "unusable":
        return "student_unusable_overcall"
    if tl in ("present", "absent") and sl == "abstain":
        return "student_abstain"
    if tl == "abstain" and sl in ("present", "absent"):
        return "student_decides_teacher_abstain"
    return None


def _repair_obs(
    teacher: list[VintageObservation],
    student: list[VintageObservation],
    modes: set[str],
) -> list[VintageObservation]:
    """Overwrite student frames with teacher labels for selected error classes."""
    out: list[VintageObservation] = []
    for t, s in zip(teacher, student):
        tag = _tag_frame(t, s)
        if tag is not None and tag in modes:
            out.append(
                VintageObservation(
                    capture_date=s.capture_date,
                    pv_present=t.pv_present,
                    confidence=t.confidence,
                    quality_flag=t.quality_flag,
                    source_row=s.source_row,
                )
            )
        else:
            out.append(s)
    return out


def _match_teacher_all(
    teacher: list[VintageObservation],
    student: list[VintageObservation],
) -> list[VintageObservation]:
    """Oracle: student copies teacher every frame (sanity upper bound)."""
    return [
        VintageObservation(
            capture_date=s.capture_date,
            pv_present=t.pv_present,
            confidence=t.confidence,
            quality_flag=t.quality_flag,
            source_row=s.source_row,
        )
        for t, s in zip(teacher, student)
    ]


def _thumb(path: Path | None, size: int = THUMB) -> str:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return PLACEHOLDER
    try:
        from PIL import Image

        with Image.open(path) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img.thumbnail((size, size))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception:  # noqa: BLE001
        return PLACEHOLDER


def _css() -> str:
    return """
    body { font-family: system-ui, sans-serif; margin: 16px; background: #0f1115; color: #e8eaed; }
    h1,h2,h3 { color: #f1f3f4; }
    .meta { color: #9aa0a6; font-size: 13px; margin-bottom: 8px; }
    .unit { border: 1px solid #3c4043; border-radius: 8px; padding: 12px; margin: 16px 0;
            background: #1a1d23; }
    .unit.disagree { border-color: #c5221f; }
    .unit.agree { border-color: #137333; }
    .keys { font-family: ui-monospace, monospace; font-size: 12px; white-space: pre-wrap; }
    .strip { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    .frame { width: 170px; background: #202124; border-radius: 6px; padding: 6px;
             border: 1px solid #3c4043; }
    .frame.err-transition_fp { border-color: #f9ab00; }
    .frame.err-unusable_forced_decide { border-color: #a142f4; }
    .frame.err-transition_fn { border-color: #4285f4; }
    .frame img { width: 100%; height: auto; border-radius: 4px; display: block; }
    .cap { font-size: 11px; line-height: 1.35; margin-top: 4px; }
    .t { color: #8ab4f8; } .s { color: #f28b82; }
    .tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10px;
           background: #3c4043; margin-top: 2px; }
    .tag.transition_fp { background: #5c4300; color: #fdd663; }
    .tag.unusable_forced_decide { background: #3c1e6e; color: #d7aefb; }
    .tag.transition_fn { background: #174ea6; color: #aecbfa; }
    table { border-collapse: collapse; margin: 12px 0; font-size: 13px; }
    th, td { border: 1px solid #3c4043; padding: 4px 8px; text-align: right; }
    th { background: #202124; text-align: left; }
    """


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--reps", type=Path, nargs="+", required=True)
    ap.add_argument("--scorer", choices=["dinov3_frozen", "dinov2_floor"], default="dinov2_floor")
    ap.add_argument("--head", type=Path, required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--cohort-prior", type=Path, default=DEFAULT_COHORT_PRIOR_JSON)
    ap.add_argument("--emissions", type=Path, default=DEFAULT_EMISSIONS_JSON)
    ap.add_argument("--vexcel-capture-csv", type=Path, default=DEFAULT_VEXCEL_CSV)
    ap.add_argument("--drop-anchors-overlapping", type=Path, required=True)
    ap.add_argument("--chip-targets", type=Path, default=None)
    ap.add_argument("--unit-rows", type=Path, default=None,
                    help="optional precomputed unit_rows.jsonl from diagnose_fidelity_gate2")
    ap.add_argument("--stratum", default="done_appears")
    ap.add_argument("--prefer-rep", type=int, default=1)
    ap.add_argument("--max-strips", type=int, default=40,
                    help="max hard-example units to embed as HTML strips")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    strips_dir = out / "strips"
    strips_dir.mkdir(exist_ok=True)

    chip_targets = a.chip_targets or (
        Path.home()
        / "zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_targets.csv"
    )
    ref_rows = fg.load_reference_rows(a.reference)
    ref_rows, leakage = fg.drop_overlapping_anchors(ref_rows, a.drop_anchors_overlapping)
    ref_by_sf = {r["sf"]: r for r in ref_rows}

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

    # ---- unit universe: done_appears decoded from precomputed rows or recompute ----
    if a.unit_rows and a.unit_rows.exists():
        unit_rows = [json.loads(l) for l in a.unit_rows.open()]
    else:
        unit_rows = []
        for rep_i, rep_dir in enumerate(a.reps):
            delivery = fg.load_delivery_by_sf(rep_dir / "delivery.csv")
            km = fg.rep_keys(
                rep_dir, ref_rows, delivery,
                est=est, config=config, vexcel_ceiling=vexcel,
                teacher_only=False, scorer=scorer, cache=cache,
                scorer_key=scorer_key, skip_missing=False, stats=stats,
                resolver=resolver, apply_render_drop_to_teacher=True,
            )
            for sf, info in km.items():
                row = ref_by_sf[sf]
                drow = delivery.get(sf, {})
                unit_rows.append({
                    "rep": rep_i + 1, "sf": sf, "stratum": row["stratum"], "w": row["w"],
                    "provider": _s(drow.get("date_provider")),
                    "decoded": info["decoded"], "had_unusable": info["had_unusable"],
                    "teacher_key": info["teacher_key"], "student_key": info["student_key"],
                    "agree": int(info["student_key"] is not None and info["teacher_key"] == info["student_key"]),
                    "group_anchor_id": row["group_anchor_id"],
                    "target_anchor_id": row["target_anchor_id"],
                    "grid_id": row["grid_id"],
                })

    focus = [
        r for r in unit_rows
        if r.get("decoded") and r.get("stratum") == a.stratum
    ]
    if not focus:
        print(f"ERROR: no decoded rows for stratum={a.stratum}", file=sys.stderr)
        return 2

    # prefer one rep per unit for strip/catalog; counterfactual walks all focus rows
    by_sf: dict[int, list[dict]] = defaultdict(list)
    for r in focus:
        by_sf[int(r["sf"])].append(r)

    # ---- counterfactual: re-score each focus row once, apply repairs ----
    REPAIR_MODES = {
        "baseline": set(),
        "fix_transition_fp": {"transition_fp"},
        "fix_unusable_forced": {"unusable_forced_decide"},
        "fix_fp_and_unusable": {"transition_fp", "unusable_forced_decide"},
        "fix_fp_fn_unusable": {"transition_fp", "transition_fn", "unusable_forced_decide"},
        "oracle_match_teacher": None,  # special
    }

    # per-mode per-row agree flags
    mode_agree: dict[str, list[dict]] = {m: [] for m in REPAIR_MODES}
    frame_tag_counts = Counter()
    hard_catalog: list[dict] = []
    strip_units: list[dict] = []  # rich records for HTML

    # walk all focus unit-rows (rep × sf)
    n_scored = 0
    for r in sorted(focus, key=lambda x: (x["rep"] != a.prefer_rep, int(x["sf"]), x["rep"])):
        provider = r.get("provider") or ""
        layer_col = _REPLACED_PROVIDERS.get(provider)
        if layer_col is None:
            # shouldn't happen for decoded, but be safe
            for m in REPAIR_MODES:
                mode_agree[m].append({**r, "agree_cf": r["agree"]})
            continue
        layer, anchor_col = layer_col
        ref = ref_by_sf[int(r["sf"])]
        anchor_id = ref[anchor_col]
        rep_dir = a.reps[int(r["rep"]) - 1]
        scan_path = rep_dir / layer / "scan_states" / f"{anchor_id}.json"
        if not scan_path.exists():
            for m in REPAIR_MODES:
                mode_agree[m].append({**r, "agree_cf": r["agree"]})
            continue

        teacher_obs, student_obs, drop_info = fg.load_paired_observations(
            scan_path, scorer, cache,
            scorer_key=scorer_key, skip_missing=False, stats=stats, resolver=resolver,
        )
        n_scored += 1
        clamp = ClampContext(ceiling_date=vexcel.get(ref["grid_id"]))
        t_key = fg.posterior_to_agree_key(est(teacher_obs, clamp, config))

        # frame tags
        tags_here = Counter()
        frame_rows = []
        for i, (t, s) in enumerate(zip(teacher_obs, student_obs)):
            tag = _tag_frame(t, s)
            if tag:
                tags_here[tag] += 1
                frame_tag_counts[tag] += 1
            # resolve chip for strip later
            frame_rows.append({
                "i": i,
                "date": str(t.capture_date),
                "t_lab": _lab(t),
                "s_lab": _lab(s),
                "tag": tag,
                "chip_resolved": None,  # filled if selected for strip
            })

        for mode, modeset in REPAIR_MODES.items():
            if mode == "oracle_match_teacher":
                fixed = _match_teacher_all(teacher_obs, student_obs)
            elif not modeset:
                fixed = student_obs
            else:
                fixed = _repair_obs(teacher_obs, student_obs, modeset)
            s_key = fg.posterior_to_agree_key(est(fixed, clamp, config))
            mode_agree[mode].append({
                **r,
                "agree_cf": int(t_key == s_key),
                "teacher_key_cf": t_key,
                "student_key_cf": s_key,
                "n_fp": tags_here.get("transition_fp", 0),
                "n_unusable_fd": tags_here.get("unusable_forced_decide", 0),
                "n_fn": tags_here.get("transition_fn", 0),
            })

        # catalog one row per unit-rep with tag totals
        hard_catalog.append({
            "rep": r["rep"], "sf": r["sf"], "grid_id": ref["grid_id"],
            "anchor_id": anchor_id, "had_unusable": r["had_unusable"],
            "agree_baseline": r["agree"],
            "teacher_key": t_key, "student_key": r["student_key"],
            "n_frames": len(teacher_obs),
            "n_drop": drop_info.get("n_dropped", 0),
            **{f"n_{k}": tags_here.get(k, 0) for k in (
                "transition_fp", "transition_fn", "unusable_forced_decide",
                "student_unusable_overcall", "student_abstain",
                "student_decides_teacher_abstain",
            )},
            "subset": "unusable_bearing" if r["had_unusable"] else "clean",
        })

        # strip candidates: disagree + has fp or unusable_fd, prefer rep
        if (
            not r["agree"]
            and (tags_here.get("transition_fp", 0) + tags_here.get("unusable_forced_decide", 0)) > 0
            and len(strip_units) < a.max_strips
            and (r["rep"] == a.prefer_rep or len(strip_units) < a.max_strips // 2)
        ):
            # resolve paths for thumbs
            anchor_id_raw, raw_frames = fg._iter_raw_frames(scan_path)
            resolved = []
            for fr in raw_frames:
                rp, err = resolver.resolve(fr.chip_path, anchor_id_raw)
                if err or not rp:
                    continue
                resolved.append(rp)
            # align with kept obs length
            for i, fr in enumerate(frame_rows):
                if i < len(resolved):
                    fr["chip_resolved"] = resolved[i]
            strip_units.append({
                "r": r, "anchor_id": anchor_id, "grid_id": ref["grid_id"],
                "t_key": t_key, "s_key": r["student_key"],
                "tags": dict(tags_here), "frames": frame_rows,
                "subset": "unusable_bearing" if r["had_unusable"] else "clean",
            })

    # ---- aggregate rates ----
    def rate(rows: list[dict], key: str = "agree_cf") -> dict:
        if not rows:
            return {"n": 0, "rate": None}
        n = len(rows)
        hit = sum(int(x[key]) for x in rows)
        return {"n": n, "n_agree": hit, "rate": round(hit / n, 4)}

    def split_rates(rows: list[dict], key: str = "agree_cf") -> dict:
        clean = [x for x in rows if not x.get("had_unusable")]
        unus = [x for x in rows if x.get("had_unusable")]
        return {
            "all": rate(rows, key),
            "clean": rate(clean, key),
            "unusable_bearing": rate(unus, key),
        }

    results = {
        "scorer": a.scorer,
        "head": str(a.head),
        "head_sha256": head_sha,
        "stratum": a.stratum,
        "n_focus_unit_reps": len(focus),
        "n_scored": n_scored,
        "leakage": leakage,
        "frame_tag_totals": dict(frame_tag_counts),
        "baseline_from_unit_rows": split_rates(
            [{**r, "agree_cf": r["agree"]} for r in focus]
        ),
        "counterfactual": {
            mode: split_rates(mode_agree[mode]) for mode in REPAIR_MODES
        },
        "ceiling_reference": {
            "teacher_self_consistency_decoded_approx": 0.77,
            "note": "gate-2 teacher pairwise ceiling on decoded units (~0.7724)",
        },
        "interpretation": {},
    }

    # interpretation vs 0.77
    base_all = results["baseline_from_unit_rows"]["all"]["rate"] or 0
    base_clean = results["baseline_from_unit_rows"]["clean"]["rate"] or 0
    cf = results["counterfactual"]
    results["interpretation"] = {
        "question": "Can transition-FP + unusable-forced-decide repair lift done_appears from ~0.45 toward ~0.77?",
        "baseline_done_appears": base_all,
        "baseline_done_appears_clean": base_clean,
        "after_fix_fp": cf["fix_transition_fp"]["all"]["rate"],
        "after_fix_unusable": cf["fix_unusable_forced"]["all"]["rate"],
        "after_fix_fp_and_unusable": cf["fix_fp_and_unusable"]["all"]["rate"],
        "after_fix_fp_fn_unusable": cf["fix_fp_fn_unusable"]["all"]["rate"],
        "oracle_match_teacher": cf["oracle_match_teacher"]["all"]["rate"],
        "clean_after_fix_fp": cf["fix_transition_fp"]["clean"]["rate"],
        "clean_after_fix_fp_fn_unusable": cf["fix_fp_fn_unusable"]["clean"]["rate"],
        "gap_to_077_after_fp_unusable": round(
            0.77 - (cf["fix_fp_and_unusable"]["all"]["rate"] or 0), 4
        ),
        "gap_to_077_clean_after_fp": round(
            0.77 - (cf["fix_transition_fp"]["clean"]["rate"] or 0), 4
        ),
    }

    # write counterfactual json
    (out / "counterfactual.json").write_text(json.dumps(results, indent=2, default=str))

    # catalog csv
    if hard_catalog:
        keys = list(hard_catalog[0].keys())
        with (out / "hard_examples.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(hard_catalog)

    # markdown summary
    lines = [
        f"# Hard-example / counterfactual repair — `{a.stratum}` ({a.scorer})",
        "",
        f"n unit-reps scored: **{n_scored}** / focus {len(focus)}",
        "",
        "## Baseline",
        "",
        f"| subset | n | agree |",
        f"|---|--:|--:|",
        f"| all done_appears decoded | {results['baseline_from_unit_rows']['all']['n']} | {results['baseline_from_unit_rows']['all']['rate']} |",
        f"| clean (no teacher-unusable frame) | {results['baseline_from_unit_rows']['clean']['n']} | {results['baseline_from_unit_rows']['clean']['rate']} |",
        f"| unusable-bearing | {results['baseline_from_unit_rows']['unusable_bearing']['n']} | {results['baseline_from_unit_rows']['unusable_bearing']['rate']} |",
        "",
        "## Frame error totals (teacher vs student labels)",
        "",
        "| tag | n frames |",
        "|---|--:|",
    ]
    for k, v in frame_tag_counts.most_common():
        lines.append(f"| `{k}` | {v} |")
    lines += [
        "",
        "## Counterfactual interval agreement (student frames surgically set to teacher on tagged errors, then re-decode)",
        "",
        "| repair | all | clean | unusable-bearing |",
        "|---|--:|--:|--:|",
    ]
    for mode in REPAIR_MODES:
        s = cf[mode]
        lines.append(
            f"| `{mode}` | {s['all']['rate']} (n={s['all']['n']}) | "
            f"{s['clean']['rate']} (n={s['clean']['n']}) | "
            f"{s['unusable_bearing']['rate']} (n={s['unusable_bearing']['n']}) |"
        )
    inter = results["interpretation"]
    lines += [
        "",
        "## Can we hit ~0.77?",
        "",
        f"- Baseline done_appears: **{inter['baseline_done_appears']}** "
        f"(clean **{inter['baseline_done_appears_clean']}**)",
        f"- Fix transition_fp only: **{inter['after_fix_fp']}** "
        f"(clean **{inter['clean_after_fix_fp']}**)",
        f"- Fix unusable_forced_decide only: **{inter['after_fix_unusable']}**",
        f"- Fix fp + unusable: **{inter['after_fix_fp_and_unusable']}** "
        f"(gap to 0.77 = **{inter['gap_to_077_after_fp_unusable']}**)",
        f"- Fix fp + fn + unusable: **{inter['after_fix_fp_fn_unusable']}**",
        f"- Oracle (student ≡ teacher every frame): **{inter['oracle_match_teacher']}** "
        f"(sanity — must be ~1.0)",
        "",
        "Teacher ceiling reference ≈ **0.77** (decoded pairwise self-consistency).",
        "",
        f"HTML strips: `strips/hard_examples.html` ({len(strip_units)} units).",
        "",
    ]
    (out / "counterfactual.md").write_text("\n".join(lines))

    # ---- HTML strips ----
    body = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>hard examples — {html.escape(a.stratum)} / {html.escape(a.scorer)}</title>",
        f"<style>{_css()}</style></head><body>",
        f"<h1>Hard examples — {html.escape(a.stratum)} ({html.escape(a.scorer)})</h1>",
        "<p class='meta'>Yellow border = transition FP (teacher absent → student present). "
        "Purple = unusable forced-decide. Blue = FN. "
        "Student chips are tight12 nomarker re-renders (same as gate-2).</p>",
        f"<p class='meta'>Baseline all={inter['baseline_done_appears']} · "
        f"clean={inter['baseline_done_appears_clean']} · "
        f"after fp+unusable={inter['after_fix_fp_and_unusable']} · "
        f"ceiling≈0.77</p>",
    ]
    for u in strip_units:
        r = u["r"]
        cls = "disagree" if not r["agree"] else "agree"
        body.append(f"<div class='unit {cls}'>")
        body.append(
            f"<h3>sf={r['sf']} · rep={r['rep']} · {html.escape(u['grid_id'])} · "
            f"{html.escape(u['subset'])}</h3>"
        )
        body.append(
            f"<div class='keys'>teacher: {html.escape(str(u['t_key']))}\n"
            f"student: {html.escape(str(u['s_key']))}\n"
            f"tags: {html.escape(json.dumps(u['tags']))}</div>"
        )
        body.append("<div class='strip'>")
        for fr in u["frames"]:
            err = fr["tag"] or ""
            ecls = f" err-{err}" if err else ""
            path = Path(fr["chip_resolved"]) if fr["chip_resolved"] else None
            body.append(f"<div class='frame{ecls}'>")
            body.append(f"<img src='{_thumb(path)}' alt='{html.escape(fr['date'])}'/>")
            body.append(
                f"<div class='cap'><b>{html.escape(fr['date'])}</b><br>"
                f"<span class='t'>T: {html.escape(fr['t_lab'])}</span><br>"
                f"<span class='s'>S: {html.escape(fr['s_lab'])}</span>"
            )
            if err:
                body.append(f"<div class='tag {html.escape(err)}'>{html.escape(err)}</div>")
            body.append("</div></div>")
        body.append("</div></div>")
    body.append("</body></html>")
    (strips_dir / "hard_examples.html").write_text("\n".join(body))

    print(json.dumps(results["interpretation"], indent=2))
    print(f"\nwrote {out}/counterfactual.md")
    print(f"wrote {strips_dir}/hard_examples.html ({len(strip_units)} units)")
    print(f"frame tags: {dict(frame_tag_counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
