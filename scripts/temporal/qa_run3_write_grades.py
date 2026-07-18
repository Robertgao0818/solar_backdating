#!/usr/bin/env python3
"""Emit graded_anchors.csv = the human/assistant visual verdicts recorded during
the RUN3 coverage-gain QA (2026-07-19), joined to the fixed-seed manifest so a
reviewer can re-render each anchor's strip + pivotal frames and independently
re-grade. Grades keyed by anchor-id suffix (tNNNNNNNN); the manifest supplies
the full anchor_id and machine fields.

placement_tier  : a=marker off-structure / b=on-roof PV outside box /
                  c=PV enclosed at marker / d=on-roof genuinely no PV /
                  u=uncertain at available resolution
                  (DATA-fullscan-run2-qa-sample §12 rubric; u is this audit's
                  explicit abstention rather than forcing an a-d assignment).
date_verdict    : defensible / plausible / spurious_present / benign_drop /
                  uncertain / genuine_loss
vs_run2         : improved / same / regressed_benign / regressed_genuine / na
"""
from __future__ import annotations
import argparse, csv, pathlib

OUT = pathlib.Path.home()/"zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/run3_v2/coverage_qa_2026-07-19"

# suffix -> (placement_tier, date_verdict, vs_run2, note)
G = {
 # S1 census_bound->dated  (pivotal-confirmed at native 256px)
 "t00004237": ("c","defensible","improved","v1off45.9m laundered idc; R3 on-roof, panels appear 2023-01-23->2024-02-29 (pivotal-confirmed)"),
 "t00004310": ("c","defensible","improved","v1off18; clean absent->present, panels visible at marker 2024-02-29"),
 "t00012231": ("c","plausible","improved","v1off32; tight 1-mo bracket on dark-on-dark roof; 2025 frame confirms a panel exists"),
 "t00012762": ("c","defensible","improved","v1off23; thumbnail looked degraded but native-res shows clear panels 2024-03-30"),
 "t00012899": ("c","defensible","improved","v1off25; 1st-present 2024-02-29 clean panels; shadow only on later frame"),
 "t00013891": ("c","defensible","improved","v1off28; distinct dark array at marker 2023-11-24"),
 # S2 contradiction (marker_missed_pv) resolved
 "t00000894": ("c","plausible","improved","v1off27.6 (offset-bug fingerprint); pre-census absent, post-census present -> census_bound"),
 "t00002792": ("c","plausible","improved","v1off25.5; on-roof; census_bound; faint grid at 2022-10-30 borderline"),
 "t00004554": ("c","defensible","improved","v1off11.7; ALL frames present since 2019 -> already_present; cleanest contradiction resolution"),
 "t00006073": ("c","plausible","improved","v1off7.4; on-roof, census_bound"),
 "t00008713": ("c","defensible","improved","v1off30.7; absent->present 2024-02-29 panels at marker"),
 "t00008790": ("c","defensible","improved","v1off38.6; large offset fixed, real appears bracket"),
 # S3 undated->dated
 "t00006731": ("c","defensible","improved","v1off2.9; large commercial PV farm present from 2021-04-30; R2 undated on blown-white frame"),
 "t00007003": ("c","defensible","improved","v1off21.3; commercial roof, panels at 2022-10-30"),
 "t00008443": ("c","defensible","improved","v1off39.1; offset fixed, panels in present frames"),
 "t00012997": ("c","defensible","improved","v1off34.4; absent->present 2022-03-30"),
 "t00022532": ("c","defensible","improved","v1off0.5; panel visible in present frame; R2 undated (no monthly vintages gap)"),
 "t00024530": ("c","plausible","improved","v1off25; single clean present 2023-11-24, then amber"),
 # S4a dated->census_bound/undated (regression)
 "t00002849": ("b","genuine_loss","regressed_genuine","v1off0.1; PV real but on ADJACENT roof section, outside marker box -> R3 census_bound under-dates; census-side polygon precision, out of scope"),
 "t00008149": ("d","benign_drop","regressed_benign","v1off29.9; R3 on-roof all-absent; R2 appears was off-target artifact"),
 "t00008467": ("d","benign_drop","regressed_benign","v1off33.2; clean roof, no PV; R2 appears dropped correctly"),
 "t00012218": ("u","uncertain","na","v1off15.5; dark rectangular feature (skylight vs small PV) cannot be resolved at available resolution; abstain on placement/date"),
 "t00016804": ("a","benign_drop","regressed_benign","v1off1.13m; source_area=381.25m2, footprint=60.34x27.93m; malformed anchor (bridge over vegetation); R3 inverted-interval->undated is safe"),
 "t00016844": ("c","plausible","improved","v1off24.4; absent 2024-03-30 -> present 2025-03-30 (post-census); R3 census_bound more honest than R2's early off-target date"),
 # S4b dated->left_censored (supplemental regression audit)
 "t00002426": ("c","uncertain","na","v1off0.2; 2019 and 2025 look present but three intervening frames look absent; left-censor depends on repairing three dips"),
 "t00019441": ("c","plausible","improved","v1off10.0; panel-backed 2019 and 2022+ with one 2020 absent read; left-censor plausible, R2 2022 date likely late"),
 "t00021224": ("c","uncertain","na","v1off11.9; 2019/2025 look present but three intervening frames were scored absent; left-censor remains resolution-limited"),
 "t00024264": ("c","plausible","improved","v1off18.0; clear PV in 2019 and 2025; two blurred/off-nadir absent reads make left-censor plausible rather than certain"),
 "t00025094": ("c","defensible","improved","v1off10.3; PV visible in 2019, 2020, 2023 and 2025; isolated 2022 absent read is a credible dip-repair"),
 "t00031393": ("c","defensible","improved","v1off7.6; PV visible in every frame since 2019; clean left-censor and R2 midpoint was late"),
 # S5 redate >=180d
 "t00012878": ("c","defensible","improved","v1off48.4; R3 2022-01-13 earlier & panel-backed vs R2 2023-08-27 off-target"),
 "t00014145": ("c","plausible","improved","v1off5.3; R3 earlier (2021-10-15); busy roof"),
 "t00020575": ("c","plausible","improved","v1off23.3; offset fixed, earlier detection; blue grid at 2024-02/03"),
 "t00021635": ("c","plausible","same","v1off17.5; commercial roof, dip-repair on nonmonotonic blip"),
 "t00026768": ("c","uncertain","na","v1off6.6; large solar farm possibly present pre-2021; R3 2023-01-26 may under-date"),
 "t00027950": ("c","uncertain","na","v1off0.1 identical placement yet 13-mo redate; cross-run Gemini disagreement on 2023 frames"),
 # S6 census_bound persist (control)
 "t00007788": ("c","defensible","same","v1off15.1; on-roof, genuinely absent pre-census, present only post-census"),
 "t00007807": ("c","plausible","same","v1off32.1; bright roof, census_bound"),
 "t00008395": ("b","uncertain","na","v1off22.7; all-absent at marker; evidence calls the dark block skylight/vent, while obvious PV is outside the box or not confirmable"),
 "t00009805": ("c","defensible","same","v1off0.0; placement fine, genuine recent install, census_bound correct"),
 "t00011720": ("c","plausible","same","v1off28.9; flat white roof, all-absent, present post-census"),
 "t00012326": ("d","uncertain","na","v1off9.9; no visible PV in usable frames and final frame is amber; census_bound placement/date support is uncertain"),
 # S7 became_leftcensor
 "t00016220": ("c","uncertain","na","v1off23; present->absent->present nonmonotonic; already_present forced by 2019-present + dip-repair (possible parallax flicker)"),
 "t00018101": ("c","uncertain","na","v1off32; same nonmonotonic dip-repair pattern; 2019-present read may be parallax misread"),
 "t00029659": ("c","defensible","improved","v1off25.7; large solar farm present ALL frames since 2019 -> clean left_censored"),
 "t00030702": ("c","defensible","improved","v1off29; commercial roof panels all frames"),
 "t00031490": ("c","defensible","improved","v1off10.6; large solar farm all present"),
 "t00033778": ("c","plausible","improved","v1off3.9; all present, small offset"),
 # S8 dated_stable (negative control)
 "t00001987": ("c","defensible","same","v1off4.2; R2==R3 mid 2022-01-13; clean absent->present 2022-03-30"),
 "t00003959": ("c","defensible","same","v1off0.2; R2==R3 mid 2022-12-11; consistent"),
 "t00025852": ("c","defensible","same","v1off0.0; R2 2024-01-26 ~ R3 2024-01-26; clean"),
 "t00030244": ("c","defensible","same","v1off0.2; blue array clearly appears 2023-01-23; R2==R3 mid 2022-11-11"),
 "t00035323": ("c","plausible","same","v1off27.0; present 2023-08-28; classifier edge (109d midpoint shift landed in S8)"),
 "t00035819": ("c","defensible","same","v1off0.1; R2==R3 mid 2020-12-14; panels 2021-04-30"),
}

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--out-dir", type=pathlib.Path, default=OUT)
args = ap.parse_args()
man_path = args.out_dir / "qa_manifest.csv"
man = {r["anchor_id"]: r for r in csv.DictReader(open(man_path))}
by_suffix = {aid.split("_")[-1]: aid for aid in man}
rows = []
for suf, (tier, dv, vs, note) in G.items():
    aid = by_suffix.get(suf)
    if not aid:
        raise SystemExit(f"suffix {suf} not in manifest — sample drifted")
    m = man[aid]
    rows.append({
        "anchor_id": aid, "suffix": suf, "stratum": m["stratum"],
        "run2_class": m["run2_class"], "run3_class": m["run3_class"],
        "run2_mid": m["run2_mid"], "run3_mid": m["run3_mid"], "v1_offset_m": m["v1_offset_m"],
        "placement_tier": tier, "date_verdict": dv, "vs_run2": vs, "note": note,
    })
rows.sort(key=lambda r: (r["stratum"], r["suffix"]))
outp = args.out_dir/"graded_anchors.csv"
with outp.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print(f"wrote {outp}  ({len(rows)} graded anchors of {len(man)} sampled)")

import collections
for field in ("date_verdict", "vs_run2", "placement_tier"):
    print(f"\n{field}:", dict(collections.Counter(r[field] for r in rows)))
print("\nby stratum x date_verdict:")
tab = collections.Counter((r["stratum"], r["date_verdict"]) for r in rows)
for (s, d), n in sorted(tab.items()):
    print(f"  {s:26} {d:16} {n}")
