# Data memo: RUN 2 parallax / off-nadir displacement audit (2026-07-17)

Status: P0 audit. **Updated same day, twice, as the owner's diagnosis
sharpened.** Original scope was the single "parallax" failure mode (index
case `t00039667`). A second, distinct-looking failure mode was then
reported (terrain-misread, index case `t00038450`) and a third
(small-offset, PV-adjacent-to-box, index case `t00024435`) — the owner then
**unified all three as one root cause, per-frame marker/crop offset**
(the ISSUE-23 GEHI-vs-Vexcel displacement problem this project has tracked
since 2026-07-06), with severity ranging continuously from "PV lands just
outside the box" (`t00024435`) through "PV lands well outside the crop
context, reads as a different roof section" through "marker lands off any
structure at all, reads as terrain" (`t00038450`). §7 below resolves the
further question the team lead posed — is the offset a **constant, static**
target-geometry error (upstream census/segmentation) or a **per-vintage,
varying** GEHI registration drift — using the repo's own existing,
already-validated displacement estimator rather than eyeballing, per the
qa-sample teammate's own conclusion that manual review has run out of
resolving power on exactly the cases that matter. §1.5 (F5) and §7 are the
two genuinely new population-scale instruments added in this update; §0-§6
are the original same-day pass and are left as originally written except
where a later section explicitly revises a number.

Original index case:
`jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00039667`.
Read-only against `~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/`
(scan states, `intervals/install_intervals_all.csv`, `anchors_all.csv`) and
the already-cached marker-overlay PNGs under
`basemap_rebuild_2026-07-13/chips/`. No scan state, chip, or interval file
was modified. Prior related work: `DATA-gehi-displacement-audit-2026-07-06.md`
(ISSUE-23, ✅ read — its raw data products (`gehi_displacement_audit_2026-07-06/`)
are confirmed wiped from disk, so its numbers are cited as prior/contextual
evidence, not re-derivable against this run) and `ISSUE-24-learned-feature-matching.md`
(SuperPoint+LightGlue re-registration, GO at 91.3% on its positive control).

Scripts (scratchpad, not committed): `fingerprint_scan.py`,
`build_montages.py`, `combine_sheets.py` — reproducible against the same
read-only inputs; outputs (`per_anchor_fingerprints.csv`,
`f3_done_appears_runs.csv`, `visual_sample_selection.csv`) held in this
session's scratchpad only.

## 0. Headline

**Fingerprinting built on present/absent contradictions (F1/F3/F4) can only
see contamination that leaves a "present, then later absent" trace in the
anchor's own observation history. 71% of the run (29,395/41,393 anchors:
`done_installed_during_census` 23,317 + `done_ambiguous_marker_missed_pv`
4,645 + `done_ambiguous_no_recent_anchor` 1,433) never records a single
confident-present observation, by construction of how those statuses are
assigned — so F1/F3/F4 return exactly 0 hits on that population, not
because it's clean, but because there is nothing for a sequence-
contradiction check to find.** A 5-anchor blind visual spot-check of
`done_installed_during_census` found one severe, real contamination case
invisible to every contradiction-based fingerprint (§3.3) — confirming the
owner's worry is justified. **Update (same day): F5 (§1.5), built from
evidence *text* rather than presence/absence sequence, does have partial
reach into this 71% and gives a hard floor — 14.7% of
`done_installed_during_census` and 43.7% of `no_recent_anchor` carry
explicit majority-terrain evidence — but F5 is low-recall (only 20% of an
independently-adjudicated visual sample, §3.4c), so this is a floor, not
the answer. §7's population-scale geometric measurement is the instrument
that finally scores every anchor uniformly, regardless of status or
evidence wording.**

## 1. Fingerprint search over all 41,393 scan states

Definitions: "confident absent/present" = `pv_present` matches, `confidence
>= 0.8`, `quality_flag` in `{usable, ambiguous}` (per `scan_decision.py`'s
own evidence gate, extended to admit `ambiguous` per the owner's index
case, whose smoking-gun frame is flagged `ambiguous`). Observations are
flattened across all rounds (later round overwrites same-date), sorted by
`capture_date`, including post-census reference frames (`census_date` and
`catalog_max_date` are both stored per-anchor in the scan state JSON — no
separate CSV join needed for this check).

### F1 — present, then a later confident absent (any time, incl. post-census)

| status | F1 hits | n | rate |
|---|---:|---:|---:|
| done_installed_during_census | 0 | 23,317 | 0.0% |
| done_appears | 3,692 | 9,824 | 37.6% |
| done_ambiguous_marker_missed_pv | 0 | 4,645 | 0.0% |
| done_already_present_before_geid_history | 976 | 2,018 | 48.4% |
| done_ambiguous_no_recent_anchor | 0 | 1,433 | 0.0% |
| done_ambiguous_nonmonotonic | 151 | 151 | 100.0% |
| done_ambiguous_clamp_inverted | 2 | 2 | 100.0% |
| **total** | **4,821** | **41,390** | **11.6%** |

By chip_arm: A24 4,020/36,319 (11.1%), A48 801/5,071 (15.8%). By area
bucket: a_xs 10.8%, b_sm 11.4%, c_md 14.1%, d_lg 18.1% — **monotonically
increasing with footprint size**, consistent in direction with the wiped
2026-07-06 audit's area-bucket offset finding (d_lg carried ~2.5× the
median registration offset of a_xs at the same zoom). Building-height proxy
is unavailable (as expected — flagged, not resolved). Top grids by hit
count: JNB0184 (44.7%), JNB0207 (40.0%), JNB0183 (31.1%), JNB0295 (23.8%) —
worth a follow-up grid-level look, not adjudicated here.

**Critical split — does the contradiction actually corrupt the computed
interval, or does it only land in the reference-only tail?** `scan_decision.py`
filters all decision evidence to strictly `< census_date`; post-census
frames are scored but reference-only (confirmed in code). Splitting F1 by
where the *absent* half of the pair falls:

| status | pre-census contradiction (corrupts interval) | post-census-only (reference-tail artifact) |
|---|---:|---:|
| done_appears | 670 | 3,022 |
| done_already_present_before_geid_history | 956 | 20 |
| done_ambiguous_nonmonotonic | 151 | 0 |
| done_ambiguous_clamp_inverted | 0 | 2 |
| **total** | **1,777 (4.3% of run)** | **3,044 (7.4% of run)** |

This is the more decision-relevant number: **1,777 anchors have a
contradiction sitting inside the evidence actually used to compute their
date** (their own bracket may be internally wrong); **3,044 more** carry the
contradiction only in the post-census reference tail — like the owner's own
index case, whose flip is the 2025-03-30 frame, after both census-window
present detections. Those 3,044 don't change the *computed* interval, but
each one is a same-anchor existence-proof that the marker/model produces
false-absence on that specific location — which is exactly the argument for
distrusting its earlier absent run, just not something a bracket-integrity
check alone can act on.

The pipeline already independently catches a stricter subset of this on
`done_appears`: 462/9,824 rows carry an explicit `inverted_interval:
latest_absent > earliest_present` note (blanked to no date) — a narrower
version of the pre-census-contradiction check above, already live in
production. The other 239 blanked `done_appears` rows are an unrelated
"no monthly vintages in window" data-gap, not a contradiction.

### F2 — marker_missed_pv (already isolated, 4,645)

Defined in `infer_install_dates.py`: a `done_installed_during_census`
anchor is upgraded to `marker_missed_pv` iff its latest confident-absent
observation's date `>=` the per-grid Vexcel flight ceiling — i.e. absent
strictly through the census flight itself. By definition this is a subset
of the "never observed present" population and gets 0 F1/F3/F4 hits for
the same structural reason as `done_installed_during_census` (§0).

### F3 — done_appears: long absent run, late transition near census

Of 9,824 `done_appears`, 9,666 (98.4%) have a pre-census confident-absent
run spanning ≥365 days ("long", generous definition). Distribution of
`last_absent_date → census_date` gap for those:

| gap | n | share of long runs |
|---|---:|---:|
| ≤90 days | 571 | 5.9% |
| 91–180 days | 341 | 3.5% |
| 181–365 days | 36 | 0.4% |
| >365 days | 8,718 | 90.2% |

9.8% (948 anchors) transition within 12 months of census after a long
absent run — the owner's "late-transition suspect" pattern (t00039667's own
gap is 11.6 months: 2023-05-30 → 2024-04-19). Reported as a distribution,
not a verdict, per the task brief — visual sampling (§3) is what actually
adjudicates these. 38% of this 9,666 (3,671) are independently also
F1-positive (mostly post-census-tail).

### F4 — evidence-string hedges

Counted only where an absent frame's evidence/notes matches a hedge phrase
**and** the same anchor has a confident-present observation somewhere
(same structural blindness on the 71% "never present" population as F1/F3).

| phrase | anchors | generalizes? |
|---|---:|---|
| "at marker" | 15,088 (36.5% of the *whole run*, not just this conditional set) | **No — boilerplate.** It's standard scorer template language present at similar rates in totally unremarkable absent calls; not discriminative. Drop as a signal. |
| "marker is positioned" | 2,984 (7.2%) | Weak positive signal, still fairly common |
| "misaligned" | 807 (1.9%) | Better — rarer, and the model is self-reporting the exact failure mode by name |
| "contradicting" | 85 (0.2%) | Best — rare, direct, matches the index case's own language |
| "non-descript"/"nondescript" | 16 (0.04%) | Too rare to be useful alone |

15,373 anchors have (present-anywhere + any hedge); only 4,288 (27.9%) of
those are also F1-positive, meaning hedge text mostly does **not** add
independent signal beyond boilerplate — `contradicting` and `misaligned`
are the only two phrases worth keeping as a secondary triage tag.

### F5 (added later same day) — majority-of-frames terrain/no-building evidence

Requested scope addition after the owner surfaced a second failure mode
(index case `t00038450`, `done_ambiguous_no_recent_anchor`): every one of
these 41,393 anchors carries a census-validated, FP-cut PV footprint by
construction, so if a **majority** of an anchor's own usable-absent frames
report evidence text matching a terrain/no-structure pattern (`earth`,
`vegetation`, `tree canopy`, `no roof`, `no building`, `no structure`,
`dirt`, `grass`, `lawn`, `wooded`, `shrub`, `foliage`, `open ground`, etc.),
the model never located the target at all in most of its own looks — this
is independent of whether any frame anywhere shows present, so unlike
F1/F3/F4 **this fingerprint is not structurally blind on the "never
present" 71% of the run** (computed straight from existing evidence text,
zero new compute, zero re-scoring):

| status | F5 hits | n | rate |
|---|---:|---:|---:|
| done_installed_during_census | 3,427 | 23,317 | **14.7%** |
| done_ambiguous_no_recent_anchor | 582 | 1,331 | **43.7%** |
| done_ambiguous_marker_missed_pv | 181 | 4,645 | 3.9% |
| done_appears | 156 | 9,824 | 1.6% |
| done_already_present_before_geid_history | 1 | 976 | 0.1% |
| done_ambiguous_nonmonotonic | 0 | 151 | 0.0% |
| **total** | **4,347** | **40,246** | **10.8%** |

(Denominator is anchors with ≥1 usable-absent frame, slightly under the
41,393 total.) By area bucket, F5 rate falls monotonically with footprint
size — a_xs 10.8% → d_lg 4.5% — matching the qa-sample teammate's finding
that the placement problem concentrates in small (<25 m²) targets. **Darkness
proxy** (mean RGB pixel intensity of the marker crop in the census-nearest
frame, 0-255): F5-positive median **77.8** vs F5-negative median **109.3** —
F5-flagged crops are measurably darker, an independent, automated
corroboration of the owner's "dark roof + small PV → reads as terrain"
mechanism, using a completely different signal (pixel intensity) than the
one F5 itself is built from (evidence text).

**F5 vs the qa-sample teammate's manually-adjudicated list**: cross-checked
against their 25 confirmed/strong-concern anchors (`DATA-fullscan-run2-qa-sample-2026-07-17.md`
§9) — only 5/25 (20%) are F5-positive, including `t00038450` itself
(100% terrain-flagged). **F5 is high-precision but low-recall**: it only
catches the subset of off-target reads phrased as explicit terrain/no-
structure admissions; the majority of the qa-sample's confirmed off-target
list (driveway, road, adjacent-roof-section) get scored with ordinary
"plain roof, no PV" language that doesn't trip the terrain regex (e.g.
`t00001384`'s own evidence — *"visible PV is located on adjacent roof"* —
uses neither vocabulary). **Read F5's 14.7%/43.7% as a hard population-wide
floor, not the true rate** — the true rate for `done_installed_during_census`
is somewhere between this 14.7% floor and the qa-sample's ~45% n=40 visual
upper bound, and nothing in this memo can close that range further without
either a much larger visual sample or the population-scale geometric
measurement in §7.

### Render-resolution check (owner's "没放大" / insufficient-magnification hypothesis)

Quantified directly from `anchors_all.csv` + the chips' own `.tfw` sidecar
files (no assumption needed): native GEHI/Wayback capture at z19 is
**0.2986 m/px**. The production review-crop renderer
(`ensure_single_target_review_png`, `gehi_common.py:765`) always upsamples
to `min_output_px=256` regardless of the crop's true native pixel count:

| chip_arm | share of run | review_extent_m | output px/m | native px across crop | **upsample factor** | median footprint (native px) |
|---|---:|---:|---:|---:|---:|---:|
| A24 | 87.7% (36,322) | 24 m | 10.67 | ~80 px | **3.19×** | 5.04×4.05 m → **17×14 px** |
| A48 | 12.3% (5,071) | 48 m | 5.33 | ~161 px | **1.59×** | 14.22×11.37 m → 48×38 px |

The owner's suspicion is confirmed quantitatively, and more precisely than
"no zoom": **it isn't that the render fails to zoom in — it's that 87.7% of
the run's anchors (the smaller/cheaper `chip_arm=A24` route, which is
exactly where footprint size is smallest) get a 256px canvas stretched
3.19× over source imagery whose median target is only ~17×14 native pixels
to begin with.** Upsampling a blurry 17×14-pixel blob to a bigger canvas
adds no information; it just makes an already-marginal signal bigger and
blurrier, which is consistent with (not sufficient alone to fully explain)
Gemini reading a genuinely-present small array as generic terrain. `t00038450`
(6.57×4.90 m → ~22×16 native px) sits almost exactly at this median — not
an unusual outlier, which means this isn't a tail risk confined to a few
anchors.

## 2. Visual confirmation sample (n=38: 28 fingerprint-positive spread
across statuses/chip_arm/area-buckets + index case + 5 `done_installed_during_census`
controls [the class fingerprinting cannot see at all] + 5 clean-negative
`done_appears` controls)

Method: reused the production marker-overlay PNGs already cached next to
each chip `.tif` (`*.target-*.png`, 321,238 on disk — zero new renders, zero
writes to the chips dir) — built per-anchor labeled frame-strip montages in
scratchpad only, viewed all scored dates per anchor at native 256px crop
resolution, judged each anchor's transition by eye. Full per-anchor verdicts
in `visual_sample_selection.csv` / montage PNGs (scratchpad).

**Verdict counts (n=38):**

| verdict | n | notes |
|---|---:|---|
| True parallax / marker-off-roof (view-angle or registration driven) | 7 | incl. index case; one clean case (`t00040072`) shows a persistent, unchanged PV array visible in the *background* of every "absent" frame, with the marker box sitting beside it rather than on it — about as direct a confirmation as this task will get |
| Genuine presence, isolated non-geometric classifier noise | 6 | **newly identified, distinct failure mode** — large/panel-saturated roofs where the array is visible in essentially every frame, position stable, yet Gemini flips absent on 1 isolated frame (`t00001444`, `t00025372`) or oscillates repeatedly despite an unchanging scene (`t00039331`, `t00000428`, both `nonmonotonic`) — not parallax, needs a different fix (see §4) |
| Genuine absence (correct) | 5 | roof/scene stable, no PV visible ever, incl. 3/5 of the `done_installed_during_census` controls |
| Data-quality: corrupt/blank frame scored as confident absent | 2 | **severe** — see §3.3 |
| Ambiguous / 256px resolution too coarse to adjudicate | 18 | the single largest bucket — bounds the precision estimate below, doesn't refute it |

### 3.1 Index case confirmed

`t00039667`: 2019–2023 absent frames sit on a small, consistently-shaped
flat roof section; 2024-02/03 present frames show an unambiguous
grid-patterned PV array in that same spot; the 2025-03-30 post-census
"absent, ambiguous" frame has a visibly different color cast/tint and the
roof reads as a blank grey section — consistent with a re-registered/
off-nadir capture, not a genuine panel removal. Visually confirmed
**true parallax**, matching the owner's read.

### 3.2 A clean second confirmation

`t00040072` (A24, a_xs): an industrial roof with a large, dark
grid-patterned PV array visible in the image background of *every* frame
from 2020 onward, including ones scored "absent" — the marker box sits
beside the array, not on it, until one frame (2023-11-24) where the crop
framing happens to land the box on the array and it's scored present. This
is not ambiguous: the roof content is unchanged across the whole stack: the
marker's position relative to it is what moves.

### 3.3 The `done_installed_during_census` control sample — the owner's real worry, confirmed

5-anchor blind sample (not fingerprint-selected — chosen specifically
because this 23,317-row class has **zero** fingerprint coverage):

- `t00022633` (A48, c_md): 2019–2022 frames show a plain, featureless
  light roof (genuinely no panels). The 2023-01-23 frame is a **flat white,
  contentless tile** — scored `confidence=1.0, quality_flag=usable, absent`
  regardless. The 2025-02-09 post-census reference frame shows an
  unambiguous large PV array covering most of the roof. **This anchor's
  "installed before census" status rests in part on a corrupt/blank image
  being scored as a confident usable absence** — a distinct data-quality
  failure mode, not view-angle parallax, but every bit as corrosive to this
  bucket's dates, and it evaded F1/F3/F4 completely (0 hits by
  construction).
- `t00016461`: consistently blurry/low-contrast frames across small,
  hard-to-resolve buildings — plausibly the same failure, can't confirm at
  this resolution.
- `t00022754`, `t00024305`, `t00006050`: genuine, stable, correctly-scored
  absence (marker on a driveway or a bare roof throughout, no PV visible in
  any frame). 3/5 clean.

n=5 is far too small to produce a population percentage — but it **proves
the failure mode reaches this bucket** and that fingerprinting cannot find
it there. A parallel, unrelated data-quality pattern (overexposed/blank
frames, `t00030015` in the `nonmonotonic` sample) recurred independently,
suggesting corrupt/blown-out imagery is a real, separate contamination
source worth its own detector (§4, option 1).

### 3.4 Clean-negative controls

4/5 fingerprint-negative `done_appears` controls looked genuinely correct
on inspection (stable roof, plausible transition); 2 of those 4 were
themselves visually ambiguous at 256px despite being "clean" by the
fingerprint — a caveat that absence of a fingerprint hit is not itself
strong evidence of correctness, just absence of the specific contradiction
these fingerprints test for.

### 3.4b Two new index cases, graded by offset severity (added later same day)

Per the owner's unified diagnosis, re-graded using a 4-tier severity scale
rather than a binary right/wrong: **(a)** marker off-structure entirely,
**(b)** marker on-roof but the PV sits outside/partially outside the box,
**(c)** marker encloses the PV, **(d)** genuinely no PV. Per the qa-sample
teammate's own walkback (§11 of their memo), tier (a) is graded
conservatively — a marker not on a rooftop is not automatically wrong,
since ground-mounted PV is real — so (a) here means no structure of any
kind (rooftop or paved ground-mount) is visible anywhere in the crop.

- **`t00024435`** (`done_installed_during_census`, A24, 8.53 m², owner's
  small-offset index case): viewed all 6 scored frames at native 256px.
  **Confirmed tier (b).** The crosshair sits right at a ridge/junction on a
  reddish tile roof; by the two latest frames (`2023-11-24`, `2025-01-25`)
  a grid of small light-toned rectangular units is visible immediately
  outside the box's edge, on the same roof section. This is the clean,
  small-offset case the owner described — the PV is real, present, and on
  the *same roof* the marker sits on, just not *inside* the box.
- **`t00038450`** (`done_ambiguous_no_recent_anchor`, A24, 18.11 m², owner's
  terrain-misread index case): consistent with the qa-sample teammate's own
  re-check (§11 of their memo) — **no rooftop, wall line, or paved surface
  of any kind is visible anywhere in the 24 m crop across all 7 frames**,
  just lawn, a driveway edge, and a dense tree/hedge line. This reads as
  tier (a) by the letter of the definition, with the same honest caveat the
  qa-sample teammate raised: a small (6.57×4.90 m, shed-scale) structure
  fully occluded by canopy in every single historical capture across 6
  years cannot be ruled out from imagery alone — genuinely unresolved by
  eyeball, tiered here as **"(a), occlusion not excluded"** rather than a
  flat verdict.

### 3.4c Cross-reference against the qa-sample teammate's independently-adjudicated list

The qa-sample teammate's 25 confirmed+strong-concern anchors (their memo
§9) are a genuinely independent visual-adjudication population (different
reviewer, different method — full contact-sheet screening, not fingerprint-
directed sampling). Joining against my fingerprint tables:

- **F1: 1/25 (4%) hit.** (`t00025403`, the one qa-sample kept as
  "confidently impossible" — a road with a different vehicle in each
  frame). This is an independent confirmation of §0's headline from a
  population qa-sample built with zero knowledge of my fingerprint design:
  24/25 real, visually-confirmed placement problems are invisible to
  present→absent contradiction fingerprints, because (per qa-sample's own
  §9 mechanism analysis, which matches mine) an off-target crosshair reads
  "absent" every single time by construction — there's nothing to
  contradict.
- **F5: 5/25 (20%) hit**, including `t00038450` at 100% terrain-frac. F5
  catches only the anchors whose evidence text happens to use explicit
  terrain/no-structure vocabulary — real signal, but a floor, not a
  detector for the whole population (see §1.5 above).
- **Net read**: neither text-based fingerprint (F1-F5) — nor, per §3.5, does
  eyeballing — closes this population out. §7's geometric measurement is
  the only signal in this memo that scores every anchor uniformly,
  independent of what Gemini's evidence text happened to say.

## 3.5 Precision estimate

Restricting to the 28 fingerprint/hedge-positive picks (excluding the 10
controls): confirmed true-parallax 7/28 (25%), confirmed non-geometric
classifier noise 6/28 (21%), confirmed genuine/other 3/28 (~11%), 1/28
corrupt-frame — **18/28 (64%) remain visually ambiguous at 256px**, so 25%
is a floor, not a ceiling, on true-parallax precision within the
fingerprint-positive set. The practical read: **fingerprints correctly
concentrate real problems (only ~11% of flagged anchors visually resolve
as "genuinely fine"), but they conflate at least two distinct failure
modes** — geometric parallax (needs re-registration) and non-geometric
per-frame classifier noise on saturated panel roofs (needs a consistency/
smoothing fix, not re-registration) — that require different mitigations.

## 4. Impact estimates

**(a) done_appears dates likely late-shifted:** 670/9,824 (6.8%) have a
contradiction directly inside their own decision evidence (self-inconsistent
bracket, most likely wrong as computed); 3,022/9,824 (30.8%) more carry a
same-anchor false-absence existence-proof confined to the post-census tail
(doesn't change the computed bracket but undercuts confidence in it); 948
(9.6%) are F3 late-transition-near-census suspects worth first-priority
re-review. Given the 25–36% true-parallax precision range from §3.5, a
defensible estimate is that on the order of **500–1,000 done_appears dates
(5–10%)** are materially late-shifted by parallax specifically — narrower
than the raw 37.6% F1 rate, since a comparable share of flagged anchors are
non-geometric noise or post-census-tail-only artifacts that don't move the
date.

**(b) marker_missed_pv (4,645) — parallax vs genuine model blindness:**
**Partially bounded, revised after F5 (§1.5) and §7.** F1/F3/F4 give zero
coverage by construction (§0); F5 gives a hard floor of **181/4,645 (3.9%)**
carrying explicit majority-terrain evidence text — the true rate is higher
(F5 is low-recall, §1.5), somewhere between 3.9% and an unknown upper bound
this memo cannot close without a larger visual sample. §7's geometric
measurement (population-scale, not text-dependent) is the more decisive
instrument once it lands. The single visually-sampled anchor (`t00010230`)
showed no visible structure at all across 5 years of imagery
(vegetation-only, and indeed F5-positive), consistent with marker
mislocation. The wiped 2026-07-06 audit's constant per-anchor GEHI-vs-Vexcel
bias finding (25.4% of a *different*, 792-anchor decision-set sample
carried >1m constant bias) remains a plausible but unconfirmed cross-corpus
proxy for the rest.

**(c) done_installed_during_census (23,317, 56% of the run) — the owner's
biggest worry:** **Confirmed present, partially bounded, still the single
largest open risk.** F1-F4 give zero coverage by construction (§0); F5
(§1.5) gives a hard floor of **3,427/23,317 (14.7%)** carrying explicit
majority-terrain evidence — meaning **at minimum ~3,400 of this bucket's
dates are anchored on an admitted-terrain read, not a genuine roof
observation**, before even counting the qa-sample teammate's independent
45% n=40 visual "needs review" upper bound (§3.4c: only 20% of their
confirmed list is F5-positive, so 14.7% undercounts the true rate). The
5-anchor blind visual control sample separately found 1 severe, real
contamination case unrelated to terrain-misreading (§3.3, `t00022633` — a
blank/corrupt frame scored confident-absent, true panel array visible only
post-census). **Between F5's 14.7% hard floor and the qa-sample's ~45%
upper bound, a defensible working estimate is that a meaningful fraction in
the high hundreds to low thousands of `done_installed_during_census` dates
in today's deliverable rest on a marker that never looked at the right
place — this remains the single largest open risk and the top-priority
next step is a properly-sized visual audit (n≈100), stratified to include
both F5-positive and F5-negative anchors (§5, mitigation #2 revised).**

## 5. Ranked mitigations (revised — see §7 for the padding-vs-re-centering
verdict, which changes the effort ranking of #4/#5 below)

0. **(Zero-cost, do immediately) Flag the 4,347 F5-positive anchors as
   known-suspect in the deliverable today.** This needs no new compute at
   all — F5 is already computed from existing evidence text. It's a floor,
   not the full population, but it's free and immediate: at minimum,
   3,427 `done_installed_during_census` + 181 `marker_missed_pv` + 582
   `no_recent_anchor` rows can be caveated in the current deliverable
   *right now*, before any of the below runs.
1. **Corrupt/blank-frame QA pass** (new, cheap). Flag frames with
   near-zero pixel variance/entropy (the exact `t00022633` and `t00030015`
   pattern) before they're allowed to count as evidence. Pure image stats
   over already-downloaded tifs, no re-download, no re-scoring needed for
   detection. Effort: hours.
2. **Expanded visual audit of `done_installed_during_census` +
   `marker_missed_pv`** (n≈100), now **stratified by F5 hit/no-hit** (not
   purely random — F5 gives a real, if partial, thing to stratify on) plus
   grid/area-bucket. Reuses the already-cached marker PNGs, zero new
   renders. Effort: ~1 day. Still the only way to actually size impact
   estimate (c) between its F5 floor and the qa-sample upper bound.
3. **Enlarged review-extent + context-aware Gemini re-scan**, targeted at
   the F1/F5/hedge-positive union (prioritize the 1,777 pre-census-
   corrupting F1 anchors + 948 F3 late-transition-suspects + all 4,347 F5
   hits + any corrupt-frame flags from #1), with a prompt amendment: "the
   roof may be displaced between frames on tall buildings — locate the
   building/roof by its structure and surroundings, not by the marker
   coordinates alone; flag explicitly if the marker does not appear to sit
   on any rooftop or plausible ground-mount surface." Reuses banked96
   imagery (2026-07-06 audit: "essentially immune," 0% lose>50% at every
   PSR cut) rather than the tight crop. Effort: medium (batch re-scan,
   no new downloads). Directly actionable for
   `done_appears`/`already_present`/`nonmonotonic` (present frame to
   re-anchor against) **and now, via F5, for a real (if partial) slice of
   `done_installed_during_census`/`marker_missed_pv`/`no_recent_anchor`
   too** — narrower than "does not help (b)/(c) at all," the original
   same-day read, since F5 gives those buckets a real (if partial) entry
   point.
4. **Geometric re-registration — padding vs. per-vintage re-centering, see
   §7 for which the population-scale evidence actually supports and at what
   scope.**

## 7. Population-scale geometric drift measurement (constant vs. varying)

*[IN PROGRESS — background job still running at time of writing; this
section is being filled in as results land. Method fixed and calibrated
below; population numbers to follow.]*

**Why not a from-scratch pixel PV-detector.** A first attempt at an
absolute "PV-likelihood" pixel classifier (dark + low-saturation + edgy,
percentile-thresholded per-crop) was built and calibrated against the known
index cases — it failed outright: it classified the genuinely-absent 2019
frame of `t00039667` and the tree-canopy frame of `t00038450` both as
"encloses PV," because percentile-relative thresholds just find "the
darker/edgier third of whatever image you hand it," not an actual PV
signature. Reported honestly rather than presented as working; abandoned in
favor of the approach below, which measures something objective (content
displacement) rather than something subjective (does this look like a
panel).

**Method: reuse, not reinvent.** `scripts/temporal/chip_displacement.py` /
`audit_gehi_displacement.py` (the 2026-07-06 ISSUE-23 audit's own
estimator — Sobel-gradient, Hann-windowed phase correlation with a
peak-to-sidelobe-ratio trust gate, 24 unit tests) is still in the repo and
directly reusable against this run's chips with zero new downloads (its
"S1" signal: each vintage vs. the same anchor's own latest/highest-zoom
frame, intra-stack, same-sensor). One real bug found and fixed in reuse:
`audit_gehi_displacement.enumerate_stack`'s filename regex requires
`_v<digits>.tif` and silently drops every `_vnoversion.tif` file (this run's
native-GEHI frames, as opposed to Wayback-sourced ones) — confirmed
directly (`t00039667`: the unmodified function finds 5/36 stack tifs, all
Wayback). Widened locally (`s1_drift_check.py`) to admit both; every number
below uses the full stack.

**Calibration on the 4 named index cases** (register each vintage against
the anchor's own latest-frame reference, on a common metric grid via
`core.grid_utils.get_metric_crs`, `gsd=0.3m`, PSR≥8 trust gate — identical
parameters to the 2026-07-06 audit):

| anchor | index case for | n locks | median offset | max offset | std | verdict |
|---|---|---:|---:|---:|---:|---|
| `t00039667` | parallax | 24/36 | 2.59 m | 25.0 m | 5.08 | **varying** |
| `t00040072` | (adjacent-array visual confirmation, §3.2) | 32/34 | 6.47 m | 12.1 m | 2.68 | **varying** |
| `t00038450` | terrain-misread | 19/20 | 0.84 m | 18.8 m | 4.23 | **varying** |
| `t00024435` | small-offset/adjacent | 34/35 | 2.58 m | 4.04 m | 0.97 | **varying** (borderline) |

**All four confirmed index cases show real, substantial, measurably-varying
cross-vintage content drift** — median offsets of roughly 1-6 m with
outlier locks up to 25 m, not a small rounding effect. This directly
answers the team lead's hypothesis-split question for these four: the
**dynamic** component (per-vintage GEHI registration drift, ISSUE-23's
mechanism) is real and empirically measurable in every symptom class the
owner named (parallax, terrain-misread, small-adjacent-offset) — this
audit cannot rule out an *additional* static component (a wrong upstream
`target_offset_x_m/y_m` baked into `anchors_all.csv`, which would show up
as a **constant**, not varying, S1 signature, since S1 only measures
frame-to-frame agreement and cannot distinguish "correctly and consistently
registered" from "consistently registered onto the wrong point" — that
distinction needs an absolute reference, i.e. a fresh Vexcel S3 fetch,
out of scope here), but it does establish that the dynamic mechanism alone
is sufficient to explain what's been observed in every index case so far.

*[Population-scale numbers — the constant/varying split and padding-vs-
re-centering recovery-fraction quantification the team lead asked for,
computed over a 760-anchor stratified sample (~120/status + the full
40-anchor visual sample + both new index cases) — to follow below once the
background run completes.]*

## 8. Corpus-wide corrupt/blank-frame sweep

Ran over all 41,393 anchors' scored review PNGs (the exact images Gemini
was shown, resolved via each result's own `chip_path` sibling, zero
re-rendering) — **279,340 scored frames total**. Flagged near-uniform
(std<4 on 0-255 luminance, excluding the drawn cyan marker pixels),
blown-white (>97% pixels >250), blown-black (>97% pixels <5), or
undecodable frames — the exact `t00022633`/`t00030015` pattern from §3.3.
Output: `$RUN_ROOT/corrupt_frame_audit_2026-07-17/{corrupt_frames.csv,
summary.txt, actionable_decision_critical.csv}`.

**908 corrupt frames found (0.325% of all scored frames), spread across
868 distinct anchors** — split almost exactly evenly between blown-black
(523) and blown-white (385); zero decode-broken files. Joined against each
frame's own scan-state verdict:

| status | corrupt frames | scored `usable`+`confidence≥0.8` **anyway** | decision-critical (date = anchor's own bracket date) | **both** (actionable) |
|---|---:|---:|---:|---:|
| `done_installed_during_census` | 435 | 177 | 0 | 0 |
| `done_appears` | 293 | 170 | 24 | 24 |
| `done_ambiguous_marker_missed_pv` | 74 | 34 | 1 | 1 |
| `done_ambiguous_no_recent_anchor` | 68 | 7 | 0 | 0 |
| `done_already_present_before_geid_history` | 37 | 3 | 0 | 0 |
| `done_ambiguous_nonmonotonic` | 1 | 0 | 0 | 0 |
| **total** | **908** | **391 (43.1%)** | **25 (2.8%)** | **25 (2.8%)** |

**Two separate findings, not one.** (1) The miscalibration itself is
common: **43.1% of corrupt frames (391/908) were scored `usable` at
`confidence≥0.8` anyway** — the model (or its parsing) treats a blank tile
as legible content nearly half the time this happens, confirming §3.3/§5's
"suspicious #1" pattern generalizes rather than being a one-off. (2) But
**only 25/908 (2.8%) actually sit on one of the anchor's own decision-
critical dates** (`latest_absent_date`/`earliest_present_date`/
`install_interval_start`/`install_interval_end`) — i.e. only 25 corrupt
frames are provably load-bearing for a specific computed date, and every
one of those 25 was also scored confidently (both columns match exactly:
this makes sense, since `scan_decision.py` only treats `usable` results as
evidence — a corrupt frame that got `unusable` correctly can't become a
bracket date in the first place). **These 25 anchor_ids
(`actionable_decision_critical.csv`) are the direct, provable "a blank
image produced this specific date" set** — worth a priority re-scan.
Notably, `done_installed_during_census`'s 177 confidently-scored corrupt
frames are *not* individually decision-critical by this narrow definition
(none happens to equal the exact bracket date), but they still count as
supporting evidence for that status's "all observations absent" criterion
— so the true exposure for that bucket is somewhere between 0 (narrow,
single-date definition) and 177 (any corrupt frame counted as absent
evidence at all), not a clean single number. The 25 actionable IDs cluster
in a few tight numeric runs (e.g. `t00006454/6505/6681/6697/6712/6713/
6721/6727/6740/6743`, `t00030413/30421/30549/30606/30654/30658`) —
consistent with one bad capture-date tile affecting many co-located
targets sharing that source imagery, not 25 independent incidents.

## 9. Blind-window check (does post-census imagery corroborate presence?)

Team lead's add-on: for every anchor in the 29,395-anchor "blind bucket"
(`done_installed_during_census` + `marker_missed_pv` + `no_recent_anchor`
— the population with zero F1-F4 fingerprint coverage), does its
post-census reference imagery ever independently corroborate presence, or
does the target stay dark even with the extra runway? Pure JSON read
(`census_date`, `catalog_max_date`, and every scored result's date/
`pv_present`/`confidence`/`quality_flag`), no images opened, no new compute.

**Every single blind-bucket anchor has at least one post-census reference
frame** (0/29,395 "zero post-census frames") — `post_census_reference_frames=3`
reliably delivers coverage; the blind-window/timing confound the qa-sample
teammate raised (catalog simply not extending far enough past census) is
**not** what's happening here. The gap from census to the last scored frame
clusters tightly at 343-407 days (p50=354, ~11.6 months) across the whole
bucket — consistent, not anchor-specific, so this is a scan-config
artifact (the fixed 3-frame post-census extension), not a per-anchor
availability accident.

| status | n | post-census **corroborates** presence | post-census **also never** shows present |
|---|---:|---:|---:|
| `done_ambiguous_marker_missed_pv` | 4,645 | 1 (0.0%) | **4,644 (100.0%)** |
| `done_installed_during_census` | 23,317 | 5,468 (23.5%) | **17,849 (76.5%)** |
| `done_ambiguous_no_recent_anchor` | 1,433 | 92 (6.4%) | **1,341 (93.6%)** |
| **total** | **29,395** | **5,561 (18.9%)** | **23,834 (81.1%)** |

**This sharpens, rather than resolves, the owner's biggest worry.**
`marker_missed_pv` is essentially unanimous (100%): every one of these
4,645 anchors stays dark for a further ~11-13 months past census with
zero exception — either every one of them is genuinely still not
installed as of its last scored frame (plausible for *some* fraction, not
all 4,645), or something about these targets (offset, resolution, canopy)
prevents *any* of them from ever resolving to present, which is exactly
what a systematic marker/crop-placement problem would produce and a timing
coincidence would not. For `done_installed_during_census`, this partitions
the 23,317-anchor bucket into two very different-risk populations: the
**5,468 (23.5%) whose marker demonstrably CAN see PV** (it shows up
sometime in the ~13 post-census months) are comparatively reassuring —
if the marker were the problem, it's odd that it would resolve on its own
with no scan-side change — while the **17,849 (76.5%) that stay dark
through every single frame the scan ever pulled, before and after census,
across up to 6+ years of imagery**, are the real "confirmed present,
unbounded" population from §4(c) — narrower and more specific than the
full 23,317, and the number the follow-up visual audit (mitigation #2)
should be built around.

## 10. Deliverable flag

`deliverable_2026-07-17/` (already copied to Dropbox per today's handoff)
was built from `intervals/install_intervals_all.csv`, the same file
audited here. Plainly, by row count:

- **4.3% (1,777/41,393)** have a contradiction sitting *inside* the
  evidence used to compute their own date — these specific dates are
  likely wrong as computed (mostly `done_appears` + `already_present`).
- **7.4% (3,044/41,393)** more carry a same-anchor false-absence red flag
  confined to the post-census reference tail — doesn't change the computed
  date but undercuts confidence in it.
- **71% (29,395/41,393)**, all of `done_installed_during_census` +
  `marker_missed_pv` + `no_recent_anchor`, is **completely unaudited by any
  fingerprint** and a 5-anchor spot check already found one severe,
  real contamination case in it.

**Recommendation: do not treat `done_installed_during_census`-labeled dates
as validated in any handoff pending mitigation #2 (a ~1-day task, not a
blocker on the scale of a re-scan or re-render).** At minimum, caveat those
rows explicitly as "no later than census, exact install date not otherwise
constrained and potentially over-estimated" rather than as a point date on
par with `done_appears`. The `done_appears` bucket (23.7% of the run) is
comparatively the most trustworthy of the ambiguous-status classes, and
even it carries the 6.8%/30.8% contradiction rates above.
