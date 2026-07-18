# Data memo: SuperPoint+LightGlue per-vintage re-centering PILOT (2026-07-17)

Status: owner-approved bounded **PILOT** on the RUN 2 backdating corpus's
parallax/offset contamination (`DATA-fullscan-run2-parallax-audit-2026-07-17.md`,
`DATA-fullscan-run2-qa-sample-2026-07-17.md`). Explicitly **not** a full
rollout and **not** a production `geometry_version` registration -- any
production commitment must still go through D18/ISSUE-19 policy separately.
Read-only against chips/scan states/`anchors_all.csv`; all real outputs under
`~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/recenter_pilot_2026-07-17/`
(`$OUT`). Zero writes under the chips root.

Parent lines: `DATA-gehi-displacement-audit-2026-07-06.md` (ISSUE-23, S3-Vexcel
absolute reference signal), `DATA-learned-matching-bounded-pilot-2026-07-09.md`
+ `DATA-learned-matching-gt-readjudication-2026-07-09.md` (ISSUE-24,
SuperPoint+LightGlue GO at 79.6%/91.3% after GT re-adjudication),
`DATA-weaklock-probe-2026-07-10.md` (characterization on the PSR<12 population).

## 0. Environment note: 2026-07-12 data wipe

The ISSUE-23/24 tooling (`scripts/temporal/chip_displacement.py`,
`audit_gehi_displacement.py`, `pilot_learned_match_2026-07-09.py`,
`probe_weaklock_2026-07-10.py`) survives in the repo untouched, but its input
data does not: `~/zasolar_data/` was wiped on/before 2026-07-12 (per
[[basemap-rebuild-2026-07]]), taking `gehi_displacement_audit_2026-07-06/`
(`per_chipdate_offsets.csv`, `vexcel_crops/`), `pilot_learned_match_2026-07-09/`,
`gt_readjudication_2026-07-09/`, `weaklock_probe_2026-07-10/`, and the old
`dinov3_distill_20260705`/`panel_repair_20260703` chip corpora the frozen
scripts pointed at -- all confirmed gone from disk. `lightglue` itself was
also uninstalled from the venv (only `torch`/`kornia`/`cv2` survived) and the
torch-hub weight cache (`superpoint_v1.pth`, `superpoint_lightglue_v0-1_arxiv.pth`)
was empty.

Recovery for this pilot: `pip install --no-deps git+https://github.com/cvg/LightGlue.git`
reinstalled the exact same commit ISSUE-24 originally pinned
(`eb42fee2d71449efb0aa5c10549752b5d75384d8`) without touching the pinned
`torch==2.10.0+cu128`/`kornia==0.8.2`; weights re-downloaded from the same
GitHub release on first matcher load. GPU (RTX 4070 Laptop, 8GB) verified
working (`torch.cuda.is_available()==True`) before any real-data run.

Because the old per-chip-group Vexcel crops and their bbox manifest
(`chip_targets.csv`) are gone, and the corpus itself moved from
per-chip-group to **per-target (ISSUE-25)** 96 m downloads during the
rebuild, this pilot re-fetches a fresh per-**anchor** Vexcel 2024 crop (same
`/ortho/extract` endpoint, same `za-gp-johannesburg-2024`/`urban`
collection/layer as the original ISSUE-23 audit) over each sampled anchor's
own bbox from `anchors_all.csv` -- ~200 crops, cheap, all downloaded/cached
in `$OUT/vexcel_crops/`. Vexcel credentials (`VEXCEL_USER`/`VEXCEL_PASSWORD`
in `ZAsolar/.env`) still mint tokens successfully.

## 1. Method

**Reference frame**: the freshly-fetched per-anchor Vexcel 2024 crop -- the
frozen pipeline's own reference choice (`ref=vexcel_crop, mov=gehi_vintage_chip`,
`pilot_learned_match_2026-07-09.py` docstring), reused rather than a
census-nearest self-referential frame specifically because an absolute,
externally-anchored reference is what lets this pilot separate the two
competing root-cause hypotheses from the QA memo (§4): a marker that is
displaced from Vexcel-truth by a **constant** amount across every vintage of
one anchor indicates a fixed nominal-placement bug (independent of capture
date); a marker whose displacement from Vexcel-truth **varies** frame to
frame indicates genuine per-vintage registration/parallax drift. Self-
referential (frame-vs-frame) registration cannot make this distinction: a
constant placement bug would show ~zero relative offset between any two
GEHI frames even though both are wrong by the same amount relative to truth.

**Matcher**: SuperPoint+LightGlue exactly as ISSUE-24 froze it
(`max_num_keypoints=2048`, 1-point exhaustive RANSAC translation fit,
`ransac_thresh_px=5.0`), imported via `importlib` from the frozen
`pilot_learned_match_2026-07-09.py` (`load_matcher`, `match_translation`) --
not modified. Both ref and mov are reprojected onto a shared per-anchor UTM
grid at GSD 0.15 m via `chip_displacement.reproject_to_grid`/
`utm_grid_for_bounds` (also imported unmodified). Metric CRS resolution
falls back to `EPSG:32735` (UTM 35S, JHB's actual zone) when the main repo's
task-grid registry lookup fails in this environment -- the same fallback the
frozen pilot's own `build_ref_cache` already used.

**Mandatory self-check** (before any real anchor): synthetic
`np.roll(ref, shift=(7,-4))` sign/convention check on the first successfully
fetched Vexcel crop. Result: estimated `(dy_px,dx_px)=(6.977,-4.026)` vs known
`(7,-4)`, `error_px=0.035`, `n_matches=1733` (100% inlier) -- reproduces the
original 2026-07-09 pilot's own self-check number (`error_px=0.035`) almost
exactly, confirming the reinstalled matcher behaves identically to the one
ISSUE-24 validated.

**Marker-offset convention** (`gehi_common.py:642-643`, confirmed by
inspection): `px = w*(0.5 + offset_x_m/chip_size_m)`,
`py = h*(0.5 - offset_y_m/chip_size_m)` (`offset_x_m` east-positive,
`offset_y_m` north-positive). `chip_displacement`'s `(dx_m, dy_m)` is "how far
the GEHI vintage frame's true content sits right/below the Vexcel reference's
content" on the shared north-up grid. The re-centered marker for a given
vintage is therefore `recentered_offset_x_m = orig_offset_x_m + est_dx_m`,
`recentered_offset_y_m = orig_offset_y_m - est_dy_m` (sign flip on the y term
because the grid's row-positive direction is south, while `offset_y_m` is
north-positive).

## 2. Tooling (scratchpad, not committed -- per this session's parallel-teammate
discipline; reproducible against the same read-only inputs)

- `sample_recenter_pilot.py` -- stratified sample builder.
- `recenter_match.py` -- Vexcel fetch + SuperPoint+LightGlue registration,
  resumable (skips `(anchor_id, capture_date)` keys already in
  `recenter_results.csv`).
- `recenter_analysis.py` -- offset distributions, half-box exceedance,
  static-vs-drift classification, F1-frame correlation.
- `build_recenter_html.py` -- visual deliverable.

Outputs: `$OUT/sample_manifest.csv`, `$OUT/vexcel_crops/`, `$OUT/self_check.txt`,
`$OUT/recenter_results.csv` (one row per anchor x scored-frame),
`$OUT/analysis_summary.txt`, `$OUT/per_anchor_rollup.csv`,
`$OUT/recenter_strips.html`.

## 3. Sample (n=198, fixed seed 20260717)

Regenerating the parallax-audit memo's own F1 definition (present, then a
later confident absent; `confidence>=0.8`, `quality_flag in {usable,ambiguous}`,
flattened across rounds) from scan states reproduced its exact population
size (**4,821**), cross-validating the audit's own numbers before drawing
from it.

| stratum | intent | n sampled |
|---|---|---:|
| a_f1_contradiction | present->later-absent contradiction population | 60 |
| b_blind_bucket | random `done_installed_during_census` + `done_ambiguous_marker_missed_pv` | 80 |
| c_qa_flagged_index | qa-sample index cases + Sec9 confirmed/strong-concern flagged anchors | 28 |
| d_clean_control | parallax-audit Sec3.3 clean controls + qa-sample clean residual + random `done_appears` top-up | 30 |
| **total (deduped)** | | **198** |

(c) came in at 28/33 named anchors -- 5 named qa-flagged anchors were not
found in `anchors_all.csv`/intervals (see run log for the exact list) and
were skipped rather than substituted, to keep the "index case" set exactly
matching the QA memo's own named anchors rather than a resampled proxy.

Registration run: 198/198 anchors processed, 0 skipped for missing Vexcel
crop or missing scored frames, 1,355 anchor x frame registrations total
(6.8 frames/anchor avg), end-to-end wall time ~4m12s (Vexcel fetch + matcher
load + self-check + all 198 anchors) on the RTX 4070 Laptop GPU -- comfortably
inside the tmux-job threshold, run anyway per house convention for resumability.

## 4. Headline: raw offsets look normal; a confidence gate is required to trust that

**Uncorrected (raw) offset distribution is close to the trusted June reference
population** -- p50=0.93 m, p90=3.30 m (ALL, n=1,355) vs the original ISSUE-23
audit's trusted `psr>=12` table (p50=0.96 m, p90=3.81 m). This is a good
sanity check on the pipeline, but it is **not** the number to act on: 21/1,355
rows (1.5%) have `n_inliers<20` (SuperPoint+LightGlue's own soft confidence
signal, per ISSUE-24's own calibration), and inspecting them shows this low-inlier
tail is where nearly all of the extreme (>15 m) "offsets" live -- these are
**not** parallax, they are the matcher failing (correctly, in a sense) to find
real correspondences and returning a near-degenerate 1-8-match fit.

**Concrete, decisive finding: most of the extreme-offset tail traces to one
specific bad-quality vintage, `2020-04-30`.** Of 27 rows dated 2020-04-30 in
this sample, several carry evidence text that is *literally* the Gemini
scorer describing unusable imagery -- `"obscured by heavy cloud
cover/atmospheric interference"`, `"entirely obscured by high-exposure/blank
white"`, `"severely occluded by cloud/haze"` -- several of which were still
scored `quality_flag=usable` despite that text (the exact quality-flag-
miscalibration failure mode both `DATA-fullscan-run2-parallax-audit-2026-07-17.md`
§5-6 mitigation #1 and the QA-sample memo §5 already flagged, now independently
confirmed and *quantified* by registration failure: `n_inliers` in the
single digits vs a corpus median of 282). This is squarely the ISSUE-24
"repetitive fabric / broken-instrument" discipline the task brief called out
-- treat any single-frame offset from a low-inlier match as an artifact of
the referee, not a real measurement, exactly as the GT-readjudication memo
did for phase-correlation's own aliasing failures.

**Gated (`n_inliers>=20`, drops only 21/1,355 = 1.5% of rows) is the number
this memo's conclusions are based on**, unless stated otherwise:

| stratum | n | p50 | p90 | max | half-box exceed |
|---|--:|--:|--:|--:|--:|
| a_f1_contradiction | 450 | 0.952m | 3.885m | 24.16m | 4/450 (0.9%) |
| b_blind_bucket | 472 | 0.861m | 1.943m | 8.37m | **0/472 (0.0%)** |
| c_qa_flagged_index | 182 | 0.858m | 2.336m | 22.97m | 4/182 (2.2%) |
| d_clean_control | 230 | 1.052m | 5.505m | 13.63m | 0/230 (0.0%) |
| **ALL (gated)** | **1,334** | **0.920m** | **3.070m** | 24.16m | **8/1,334 (0.60%)** |

## 5. Half-box exceedance, by distinct anchor (the recovery-relevant unit)

Frame-level exceedance above understates *how many anchors* are affected less
than it might look, and the raw (ungated) anchor count is inflated by the
`2020-04-30` artifact:

| view | anchors with >=1 exceeding frame | of 198 |
|---|---:|---:|
| RAW (no confidence gate) | 13 | 6.6% |
| **GATED (n_inliers>=20)** | **3** | **1.5%** |

The 10 anchors that only exceed under the raw view and disappear under the
gate (`t00001135, t00006477, t00006826, t00007067, t00010790, t00011203,
t00015760, t00019902, t00032668, t00040035`) all carry `n_inliers<=5` on
their flagging frame and/or explicit cloud/blank evidence text -- confirmed
matcher-failure artifacts, not real registration signal.

**The 3 anchors that survive the confidence gate** -- i.e. where SuperPoint+
LightGlue found a *high-confidence* (100+ inlier) match indicating the true
content sits more than half the review box away from the nominal crop centre:

| anchor | stratum | status | max gated offset |
|---|---|---|--:|
| `t00001384` | c_qa_flagged_index | done_installed_during_census | 22.97m |
| `t00027818` | a_f1_contradiction | done_appears | 19.90m |
| `t00027767` | a_f1_contradiction | done_ambiguous_nonmonotonic | 24.16m (2 frames) |

`t00001384` is the QA-sample memo's own index case (§8: "not defensible as
rendered", crosshair on a walkway/HVAC gap, real array on an adjacent roof
section). The visual strip (§9 below) confirms this directly: even the
re-centered marker, after a 23 m correction, lands at the *edge* of the real
array, not cleanly on it -- this is **not** a pure registration-drift case,
it is a segmentation/target-placement error large enough that per-vintage
re-centering alone cannot fully fix it (consistent with the QA memo's own
diagnosis: "this looks like an upstream census/segmentation placement error
... not a scoring bug").

**Notably, `b_blind_bucket` (the `done_installed_during_census` +
`marker_missed_pv` population -- 67.6% of the entire corpus, and the QA
memo's own "single largest open risk") shows *zero* gated exceedance in this
80-anchor sample (0/472 frames, 0/80 anchors).** This is the most
consequential finding in this pilot: it means that within this sample, the
blind bucket's contamination (which the QA memo traced to marker/segmentation
placement -- crosshair on a driveway/road/lawn/tree canopy, not a
registration problem) is **not** something SuperPoint+LightGlue re-centering
against Vexcel would catch, because registering GEHI-vs-Vexcel content
inside the *existing* bbox says nothing about whether the bbox itself is
centred on the right feature. Registration drift and marker/segmentation
placement are orthogonal failure modes, exactly as the QA memo's own severity
tiers (a)/(b) vs (a-structural) distinguished -- this pilot targets the
wrong failure mode for the blind bucket's dominant problem.

## 6. Static vs. drift verdict

Per-anchor classification (>=2 gated frames; "static" = offset vector
std-dev < 1.0m across all of the anchor's frames, i.e. every vintage agrees
on roughly the same displacement from Vexcel-truth; "drift" = std-dev >=1.0m,
i.e. the displacement genuinely varies vintage to vintage; "negligible" =
both mean and std-dev are small, no real offset either way):

| class | n anchors | share |
|---|--:|--:|
| static | 107 | 54.0% |
| drift | 86 | 43.4% |
| negligible | 5 | 2.5% |

**Both mechanisms are real and roughly co-equal in prevalence** -- this does
not cleanly resolve to one root cause. Practically: a "static" anchor's
displacement is the same in every vintage, which means (a) a single one-time
correction to the nominal target coordinate would fix every frame at once
(cheaper than per-vintage re-centering, and exactly the QA memo's own
recommendation #2, "trace root cause upstream"), and (b) per this pilot's own
gated numbers, static anchors' offsets are mostly small (well under half the
review box) so most don't need *any* action. A "drift" anchor genuinely needs
a per-vintage-varying correction -- which is what per-vintage re-centering
delivers by construction, regardless of whether the anchor is static or
drifting (this pilot computes a per-frame corrected position either way, so
the static/drift split matters for *diagnosis and prioritisation*, not for
whether the method works).

By stratum (gated): `a_f1_contradiction` splits evenly (30 static / 30
drift); `b_blind_bucket` leans static (47/32/1); `c_qa_flagged_index` and
`d_clean_control` both lean static too. The owner's index case `t00039667`
is unambiguously `drift` (mean 2.14m, vec_std 2.41m, offsets ranging 0.76m to
4.64m across 11 frames, no single dominant outlier frame) -- consistent with
genuine per-vintage capture-angle variation, matching the owner's and the QA
memo's own re-confirmed reading of that anchor.

## 7. Does the offset correlate with the F1 contradiction frame specifically?

Regenerating each F1 anchor's own contradiction pair (confident present, then
a later confident absent) and checking whether *that specific frame* carries
the anchor's own largest registration offset: **only 10/60 (16.7%)** of F1
anchors have their contradicting frame as their own max-offset frame --
barely above the ~11% (1/9 average frames per anchor) expected by chance
alone. Contradicting-frame offset (p50=1.04m) is only marginally higher than
other-frame offset in the same anchors (p50=0.93m). **This is a negative
result**: it does not support parallax/registration drift as *the*
explanation for most F1 contradictions -- consistent with the QA-sample
memo's own finding that F1-flagged anchors conflate multiple distinct failure
modes (true parallax ~25%, non-geometric classifier noise ~21%, corrupt
frame, genuinely ambiguous ~64% at 256px resolution), of which parallax is
only one and not even the majority.

## 8. Index case offsets (all 5, full per-frame detail in `analysis_summary.txt`)

| anchor | class | mean offset | vec_std | n_frames | note |
|---|---|--:|--:|--:|---|
| `t00039667` (owner's parallax index) | drift | 2.14m | 2.41m | 11 | real per-vintage drift confirmed; no single dominant frame (max 4.64m at 2022-03-30, not the 2025-03-30 contradiction frame at 2.56m) |
| `t00038450` (terrain-misread index) | static | 0.92m | 0.73m | 7 | small, consistent offset -- registration does NOT support a large placement problem here; if real, this anchor's issue (occlusion/no structure) is not something registration offset can see either way |
| `t00024435` (offset-severity index) | drift | 1.04m | 1.05m | 6 | modest, unremarkable offsets -- supports the QA reviewer's own re-grading to (d) "genuinely no PV / postdates scan" over (b) |
| `t00001384` (off-roof-section index) | drift | 13.58m | 15.79m | 6 | **the pilot's single largest confirmed finding** -- 22.97m max, 4/6 frames exceed half-box, high variance because the crosshair isn't on a coherent structure to lock onto |
| `t00040072` (clean parallax confirm) | drift | 3.08m | 4.22m | 10 | real, substantial drift (max 9.49m); visual strip shows the re-centered marker moving materially closer to the visible background array on several frames |

## 9. Failure modes

1. **Corrupt/blank/cloud-obscured frames produce degenerate, meaningless
   "offsets"** -- the dominant failure mode found in this pilot (§4), not a
   SuperPoint+LightGlue defect but a garbage-in artifact. `n_inliers<20` is
   an effective, cheap filter (drops 1.5% of rows, removes essentially all
   of the >15m tail). The parallax-audit memo's own mitigation #1
   (corrupt/blank-frame QA pass on pixel variance/entropy) should run
   **before** any future registration pass, both because it is independently
   valuable and because it would shrink this artifact class further upstream.
2. **Repetitive-fabric aliasing** (ISSUE-24's documented GT-aliasing trap):
   not directly observed as a *distinct* failure mode in this sample (the
   gated 3-anchor exceedance set all have plausible, structurally-grounded
   high-inlier matches, not aliased locks onto a neighbouring rowhouse), but
   the population is small (n=198) and this risk should stay on the checklist
   for any larger follow-up, per ISSUE-24's own discipline (verify a sample
   of large-offset high-confidence matches visually before trusting them at
   scale -- done here for the 3 gated anchors via the HTML strips, §10).
3. **Registration drift and marker/segmentation placement are orthogonal
   failure modes** -- the biggest single lesson of this pilot (§5). A
   registration re-centering pass, however well it works, cannot fix an
   anchor whose crosshair was never on the right feature in the first place;
   it can only correct genuine per-vintage optical/geometric drift around an
   already-correctly-placed nominal centre. `t00001384` shows both at once
   (a real, large registration-scale offset AND a segmentation-scale
   placement error) -- the re-centered marker moves 23m but still doesn't
   land cleanly on the array.

## 10. Visual deliverable

`$OUT/recenter_strips.html` (self-contained, base64 JPEGs, ~4.3 MB): 30
anchors x avg 7.2 frames = 216 thumbnails, production marker (cyan) vs
re-centered marker (magenta) on identical crops, red-outlined where the
re-centered offset exceeds half the review box. Selection: all 5 named index
cases + the max- and min-offset anchor per stratum (covers the 3
confidence-gate-surviving anchors and a spread of static/drift/negligible
examples) + fill-to-30. Zero writes under the chips root -- `PIL.Image.open`
reads the raw `.tif` directly in memory (same pattern the QA-sample HTML
used), draws both crosshairs on an in-memory crop, and only ever writes
inside `$OUT`.

Spot-checked visually: `t00001384` (§5/§9) shows the segmentation-error
finding directly -- neither marker sits on the array, confirming a pure
registration fix is insufficient there. `t00040072` shows the re-centered
marker moving materially toward the real background array across several
frames, a clean confirmation of genuine, correctable parallax.

## 11. Recovery estimate

Extrapolating this sample's gated, distinct-anchor exceedance rates (the most
decision-relevant number: "would re-centering plausibly change what lands in
the review crop") to the strata' full populations, with the caveat that
n=198 total (and n=80/28/30/60 per stratum) gives wide uncertainty, especially
on the zero-count blind bucket:

- **F1-contradiction population (4,821 anchors)**: 2/60 (3.3%) gated
  exceedance -> point estimate **~160 anchors** plausibly helped by
  re-centering; wide CI given n=60.
- **QA-flagged-style population**: 1/28 (3.6%) gated exceedance in a sample
  that was itself already enriched for suspicion by the QA reviewer's manual
  process -- not a number to extrapolate to a general population, reported
  for completeness only.
- **Blind bucket (`done_installed_during_census` + `marker_missed_pv`,
  27,962 anchors, 67.6% of the corpus)**: **0/80 (0%)** gated exceedance.
  A zero-count sample of this size bounds the true rate at roughly <3.7%
  (rule-of-three, 95% one-sided) but the point estimate is 0 -- registration
  re-centering is very unlikely to be the fix this population needs. The QA
  memo's own upstream-segmentation-audit recommendation remains the
  higher-leverage next step for this bucket, not this pilot's method.
- **Clean control (30 anchors, meant to be unremarkable)**: 0/30 (0%) gated
  exceedance -- consistent with the strata design (these were not expected to
  need correction).

**Overall: on the order of a few hundred anchors corpus-wide (concentrated
in the F1-contradiction and QA-flagged-style populations, essentially none in
the much larger blind bucket) would plausibly have their review crop
meaningfully changed by per-vintage re-centering** -- a small slice of the
41,393-anchor corpus, not a broad fix.

## 12. Cost projection for a full-population pass

Pure matching cost (SuperPoint+LightGlue + reprojection, no Vexcel fetch):
this pilot's 1,355 registrations completed in the same ~4m12s wall-clock
window as the Vexcel fetch + matcher load, i.e. comfortably under 1s/row
end-to-end on a single 8GB laptop GPU. Extrapolating linearly to the full
corpus (41,393 anchors x ~6.8 frames/anchor avg observed here = ~281,500
registrations): **on the order of 15-20 GPU-hours**, trivially parallelisable
across more/bigger GPUs if needed. Vexcel crop fetch (this pilot: 198 crops,
8 threads, well under a minute) extrapolates to roughly **1-2 hours**
network-bound at similar concurrency (untested at 41k scale -- Vexcel-side
rate limits are unknown and worth a smaller stress test before committing to
a full pass).

**The expensive part of a full rollout is not this pilot's method -- it is
the downstream re-render + re-scan** that a real `geometry_version`
registration would require per D18/ISSUE-19 (new crop manifest, re-fetch or
re-crop imagery at the corrected per-vintage centre, re-run Gemini scoring on
every affected frame). Given §11's recovery estimate, that downstream cost
should be scoped to **only the anchors whose gated exceedance actually
fires** (a cheap, computable filter from a full pass's own output), not a
blanket re-render of the 41,393-anchor corpus -- the vast majority of anchors
show sub-half-box, often sub-meter offsets that a re-render would not
meaningfully change.

## 13. Go/no-go recommendation

**NO-GO for registering a full-population production `geometry_version`
based on this pilot alone.** Reasons:

1. Measured benefit is concentrated in a small minority (~3% gated
   exceedance) of the already-partially-identified F1/QA-flagged
   populations, and **is essentially absent (0/80) in the blind bucket** --
   the single largest (67.6% of corpus) and most consequential population
   this work was meant to help.
2. A material share of the naive "large offset" signal (10/13 raw-flagged
   anchors) is matcher failure on corrupt/cloud-obscured imagery, not real
   parallax -- the cheaper, independently-valuable corrupt-frame QA pass
   (parallax-audit memo mitigation #1) has not been done yet and would
   likely need to run first regardless.
3. Registration drift and marker/segmentation placement are orthogonal
   failure modes (§9.3); this method cannot fix the latter, which the QA
   memo's own evidence suggests is the dominant problem in the blind bucket.

**Conditional GO for a narrow, targeted use**: run a full-population,
confidence-gated (`n_inliers>=20`) SuperPoint+LightGlue pass (cheap, §12) as
a **detector**, not a corrector -- flag the small set of anchors whose gated
half-box exceedance actually fires, and route only those into a scoped
re-crop + re-scan (still subject to D18/ISSUE-19 sign-off for the
`geometry_version` bump). Do the corrupt-frame QA pass first; it is cheaper,
independently useful, and will shrink the false-positive share of any future
large-offset flag list.

## 14. Artifacts

- Tooling (scratchpad, reproducible against the same read-only inputs, not
  committed): `sample_recenter_pilot.py`, `recenter_match.py`,
  `recenter_analysis.py`, `build_recenter_html.py`.
- Data: `~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/recenter_pilot_2026-07-17/`
  (`sample_manifest.csv`, `vexcel_crops/`, `self_check.txt`,
  `recenter_results.csv`, `analysis_summary.txt`, `per_anchor_rollup.csv`,
  `recenter_strips.html`, `run.log`).

## 15. Follow-up: census-side placement check (owner request, same day)

The 0/80 blind-bucket exceedance finding (§5) implies the QA-flagged
"marker on driveway/lawn" problem is not GEHI registration drift. The owner
asked for a direct test of the remaining hypothesis: is the detection bbox
itself on the wrong feature **already in the Vexcel 2024 frame** (the
imagery the census/segmentation pipeline actually ran on)? This partitions
the blind-bucket problem into census-side vs. GEHI-side and tells us whether
any fix lives in this repo at all.

### 15.1 Method

For each sampled anchor, rendered the same per-anchor Vexcel 2024 crop this
pilot already fetched (§0, reused from cache -- no new Vexcel calls needed
for anchors already covered) with the production detection marker (cyan
crosshair, `target_offset_x_m/y_m`) and detection bbox (magenta rectangle,
`source_width_m`/`source_height_m`) drawn on top, then graded each **by eye**
into:

- **(a)** PV clearly encloses the marker/bbox -- census placement fine.
- **(b1)** marker/bbox is on the *correct* roof/structure, but offset from
  where the visible array actually sits (same-structure near-miss, typically
  a few metres).
- **(b2)** PV is visible **elsewhere** in the crop, but the marker/bbox sits
  on a non-structure surface entirely (driveway, road, parking lot, lawn,
  pool patio, tree canopy) -- not just "wrong section of the right roof."
- **(c)** no PV visible anywhere in the crop at all.

(b1/b2 are a finer split of the task brief's single "(b)" category, added
because the two have very different implications: b1 is a plausible
segmentation-precision issue on an otherwise-correct detection; b2 is a
categorically wrong target location.)

**Sample**: 69 "flagged" anchors (union of `qa_census_audit_2026-07-17/
graded_anchors.csv`'s 44 `grade=="problem"` rows [35 `off_target` + 9
`canopy` subtype] and the qa-sample memo's own Sec9 flagged list [25
anchors] -- **zero overlap** between the two independently-produced lists,
confirmed before combining) + **20 control** anchors (random
`done_installed_during_census`/`marker_missed_pv`/`no_recent_anchor`
anchors, seed 20260718, excluded from both graded lists entirely).
89/89 anchors rendered (a Pillow bug -- `TypeError: cannot use a bytes
pattern on a string-like object` inside `TiffImagePlugin`'s EXIF/XMP parsing,
triggered by these specific Vexcel TIFFs' metadata during `.convert("RGB")`,
not hit by the GEHI-side chips elsewhere in this repo -- worked around by
reading via `rasterio` instead of `PIL.Image.open`, same library
`chip_displacement.reproject_to_grid` already uses).

Every one of the 89 renders was graded directly by eye (not automated) --
full per-anchor table in `census_placement_check/graded_anchors.csv`.

### 15.2 Result: the problem is real, census-side, and NOT concentrated in the flagged tail

| verdict | flagged (n=69) | control (n=20) |
|---|--:|--:|
| (a) clean, PV at marker | **0 (0.0%)** | **0 (0.0%)** |
| (b1) same-structure near-miss | 7 (10.1%) | 3 (15.0%) |
| (b2) PV elsewhere, marker off-structure | 24 (34.8%) | 9 (45.0%) |
| (c) no PV visible anywhere | 38 (55.1%) | 8 (40.0%) |
| **b2+c ("structural problem")** | **62 (89.9%)** | **17 (85.0%)** |

Two findings, in order of importance:

1. **Zero (a) verdicts in either group.** Not one of the 89 sampled anchors
   -- flagged or control -- showed the detection marker/bbox cleanly
   enclosing a visible panel array in the clean, high-resolution 2024 Vexcel
   reference. This directly answers the owner's question: **the problem is
   census-side, not GEHI-side.** It is visible in the exact frame the
   detection pipeline itself ran on, before any GEHI vintage or registration
   ever enters the picture -- consistent with, and considerably stronger
   than, the recenter pilot's own 0/80 blind-bucket exceedance finding (§5)
   and the QA-sample memo's marker-placement diagnosis.
2. **The control group is not meaningfully cleaner than the flagged group**
   (85.0% vs 89.9% "structural problem" rate; if anything the small-n
   control sample runs slightly *higher* on b2 specifically). This means the
   two independent manual QA passes that produced the 69 "flagged" anchors
   were **not strongly enriching for a rare tail** -- the same placement
   profile shows up at a comparable rate in a cold random draw. At face
   value this implies the true corpus-wide rate in the blind-bucket
   population (`done_installed_during_census` + `marker_missed_pv` +
   `no_recent_anchor`, ~29,395 anchors, 71% of the entire 41,393-anchor
   corpus per the parallax-audit memo's own accounting) could be far higher
   than either QA pass's own sample suggested, though n=20 on the control
   side is small and this needs a properly-sized follow-up (see §15.4)
   before being treated as a corpus-wide point estimate.

### 15.3 Caveats on this reading

- **Single-reviewer visual judgment**, same limitation the QA-sample memo's
  own re-review repeatedly flagged (§9-12 there): "no PV visible" (c) at
  this crop resolution cannot fully rule out an install too small/dark/
  low-contrast to resolve, though the crops here are Vexcel's clean 2024
  orthophoto (not a compressed 256px GEHI thumbnail), which should be a
  strictly easier read than the original scan's own imagery.
- b1 vs b2 is a judgment call at the margin (several b1 calls are "close but
  arguably still on the wrong section"); treat the 7/3 b1 counts as soft,
  the 89.9%/85.0% combined b2+c number as the robust one.
- The flagged set is a union of two different manual processes (this
  session's own qa-sample Sec9 pass and a separate `qa_census_audit`
  session's grading), not a single consistent protocol -- the 0% overlap
  is reassuring (two independent reviewers converged on largely disjoint
  anchors, i.e. neither exhausted the problem population), but it means
  "flagged" here is not one calibrated instrument.
- n=20 control is small; the 85.0% point estimate has a wide interval.
  Given how close it already sits to the flagged group's 89.9%, a larger
  control sample seems more likely to converge upward toward the flagged
  rate than to reveal the flagged set was meaningfully enriched -- but that
  is a prediction, not yet a measurement.

### 15.4 Implication for scope

Per this repo's own plugin-boundary rule (`CLAUDE.md`: "Sub-task only...
Do not redefine V1.4 task semantics here... downstream of ZAsolar's census
output"), a census-side segmentation/target-geometry problem is **out of
this repo's remit to fix** -- it lives in the upstream ZAsolar census/
segmentation pipeline that produced `anchors_all.csv`'s `target_offset_x_m/
y_m` and `source_width_m/height_m` in the first place, not in any GEHI
temporal-scanning or re-registration code here. This pilot's own SP+LightGlue
tooling (§0-14) cannot fix this class of error either, for the same reason
identified in §9.3: registration re-centering only corrects drift around an
already-correctly-placed nominal centre, and these markers were never on the
right feature to begin with.

**Recommended next step**: a properly-sized (n>=100), stratified-random
(not QA-flagged) sample of the blind-bucket population, graded with this
same a/b1/b2/c protocol against Vexcel crops, to get a corpus-wide point
estimate with a defensible confidence interval -- and if the rate holds
anywhere near 85-90%, this becomes a finding for the ZAsolar census/
segmentation team, not a follow-up pilot in this repo.

### 15.5 Artifacts

- Tooling (scratchpad, not committed): `census_placement_sample.py`,
  `census_placement_render.py`, `build_census_placement_html.py`.
- Data: `$OUT/census_placement_check/` (`sample_manifest.csv`,
  `graded_anchors.csv` [89-row per-anchor verdict + note],
  `renders/` [89 annotated Vexcel crops], `sheets/` [10 contact sheets used
  for grading], `census_placement_strips.html` [15-anchor curated HTML for
  the owner, colour-coded by verdict]).

## 16. Instrument validation (owner-requested correction, same day)

The owner correctly challenged §15's a=0%-in-both-groups result before
accepting it: that signature is consistent with a systematic measurement bug,
and it sits against three independent upstream facts (conf>=0.925 Gemini FP
review already passed, ISSUE-09's 1/300 false-present rate, and
`done_appears` grading elsewhere showing markers correctly enclosing PV in
22/25 cases). Ran the requested validation in order.

### 16.1 Fetch provenance (task 1)

Endpoint `POST .../ortho/extract`, `collection=za-gp-johannesburg-2024`,
`layer=urban`, `srid=4326`, bbox as WKT polygon from `anchors_all.csv`'s
`chip_lon_min/lat_min/lon_max/lat_max` -- unchanged from §0/ISSUE-23's own
established recipe. **New check run here**: every returned TIFF carries
Vexcel's own embedded XMP metadata (previously unread -- this is also what
caused the Pillow bug in §15.1), including `first-capture-date`/
`last-capture-date`. All 89 `census_placement_check` crops return the
identical pair (`2024-02-17` -> `2024-04-19`) -- this is the **collection's**
campaign span, not a per-tile timestamp, so it doesn't independently date
any single 96 m tile, but it lines up closely with the sampled anchors' own
`census_date` values (`2024-02-17`, `2024-02-21`, `2024-04-19` in
`graded_anchors.csv`) and with `first-publish-date`/`last-publish-date`
(`2024-05-15`/`2024-05-29`, i.e. a frozen archival product, not a live feed)
-- no evidence of a stale/pre-2024 vintage being served.

### 16.2 Positive control (task 2, decisive)

30 anchors (20 random `done_appears` + 10 random
`done_already_present_before_geid_history`, seed 20260718) through the
**identical** fetch+render+blind-grade pipeline as §15, output under
`$OUT/positive_control_check/`:

| verdict | positive control (n=30) | flagged (n=69) | control (n=20) |
|---|--:|--:|--:|
| (a) clean, PV at marker | **8 (26.7%)** | 0 (0.0%) | 0 (0.0%) |
| (b1) same-structure near-miss | 12 (40.0%) | 7 (10.1%) | 3 (15.0%) |
| (b2) PV elsewhere, off-structure | **0 (0.0%)** | 24 (34.8%) | 9 (45.0%) |
| (c) no PV visible anywhere | 10 (33.3%) | 38 (55.1%) | 8 (40.0%) |

**Verdict: the instrument is not broken.** (a) is clearly achievable
(26.7%) when the underlying anchor is genuinely good -- this alone refutes
"the instrument can't produce a clean hit." More importantly, **(b2) is
*exactly* zero across all 30 positive-control anchors**, against 34.8%/45.0%
in the flagged/control blind-bucket samples. This is the cleanest possible
signal: "marker sits on non-structure ground while real PV is visible
elsewhere in the same crop" essentially never happens to a confirmed-good
anchor, and happens to roughly a third to a half of blind-bucket anchors
regardless of whether they were QA-flagged. b2 is real, census-side, and not
an artifact of this pipeline.

**(c) needs a correction, though.** 33.3% of positive-control anchors --
anchors whose GEHI-side history already proves PV is/was really there --
*also* graded "no PV visible anywhere" in the Vexcel crop at this render
resolution. That is a real false-negative floor in my own visual grading at
this zoom/crop size (dark/low-contrast arrays, or genuinely small
installations, are sometimes not resolvable), not evidence the panel is
absent. §15's headline "b2+c = 89.9%/85.0% structural problem" therefore
overstates the confidently-attributable share: **only the b2 component
(34.8% flagged / 45.0% control) is fully validated as a real, non-artifact
finding**; the c component (55.1% flagged / 40.0% control) sits well above
the 33.3% baseline floor (suggesting a real elevated component within it
too, roughly 20 points for flagged, much smaller for control) but should be
read as directionally elevated, not as a precise rate.

### 16.3 Index-case cross-check (task 3)

Rendered `t00039667` (the owner's own parallax index case, not part of
either §15 sample) through the identical pipeline. Result: the marker/bbox
sits in a narrow blank strip directly between two massive, clearly-visible
PV arrays that fill almost the entire rest of the frame -- not "no PV
anywhere." This matches the owner's own narrative (real array very close,
small-to-moderate offset) and independently confirms the renderer correctly
displays real panel content when present (the same conclusion as 16.2's b2
result, from a single hand-picked case this time).

### 16.4 Geometry/CRS cross-check (task 4)

Compared the Vexcel response's own embedded `Vexcel:geometry` XMP tag
(ground-truth bbox of the returned pixels) against the bbox requested from
`anchors_all.csv` for two independent anchors (one from each §15 group):

| anchor | requested bbox (lon_min, lat_min, lon_max, lat_max) | XMP-returned bbox |
|---|---|---|
| `t00001384` | 28.069081, -26.132733, 28.070034, -26.131859 | 28.069081, -26.132733, 28.070034, -26.131859 |
| `t00034107` | 28.023339, -26.261258, 28.024292, -26.260384 | 28.023338, -26.261258, 28.024292, -26.260384 |

Match to 5-6 decimal places (sub-metre) on both -- no CRS/axis-order/window
bug in the fetch. Marker pixel placement was not separately re-derived from
the source `.gpkg` (task 4's specific ask), but the marker consistently
lands in geometrically sensible, non-random ground locations across all
~119 rendered anchors (§15+16.2) -- inconsistent with a pixel-math bug,
which would be expected to scatter markers arbitrarily rather than
consistently onto plausible (if often wrong) ground features.

### 16.5 Conclusion

**§15's core finding stands, on a corrected basis.** The blind bucket shows
a real, census-side (not GEHI-side, not registration, not rendering-artifact)
placement problem, validated by a clean instrument (positive control
achieves 26.7% clean hits and, decisively, 0% of the "off-structure but PV
visible elsewhere" pattern that dominates the blind bucket). The number to
carry forward is **b2 = 34.8% (flagged) / 45.0% (control)** as the
fully-validated rate of markers sitting on non-structure ground while real
PV is visible nearby -- not the earlier 89.9%/85.0% combined figure, which
conflated that clean signal with a noisier "no PV visible" component that
carries a ~33% baseline false-negative floor even on confirmed-good anchors.
The "control isn't cleaner than flagged" finding (§15.2 point 2) holds up
under this corrected lens too (b2: 45.0% control vs 34.8% flagged) --
if anything strengthening rather than weakening that observation.

### 16.6 Artifacts

- Tooling (scratchpad): `positive_control_sample.py`, `positive_control_render.py`
  (identical to `census_placement_render.py`, output dir only).
- Data: `$OUT/positive_control_check/` (`sample_manifest.csv`, `renders/`,
  `sheets/`).

## 17. Root-cause decomposition: a real pipeline bug, plus a genuine residual (owner request)

The owner's mechanism hypothesis for §15/§16 ("the marker is the detection
polygon's centroid; a multi-part/non-convex merge puts the centroid in the
gap between clusters") led directly to a bigger discovery while setting up
the requested check: **`target_offset_x_m`/`target_offset_y_m` in
`anchors_all.csv` is a stale field from the pre-ISSUE-25 legacy pipeline,
not the target's offset from its own current chip.**

### 17.1 The bug

`target_offset_x_m`/`target_offset_y_m` is computed exactly once in the
codebase, in `scripts/temporal/build_inventory_chip_groups.py:565-566`:
`target.centroid.x - group.center_x` -- the target's centroid offset from
the shared **chip-GROUP** center, under the legacy multi-target-per-chip
scheme. The historical anchor-table builder (archived at commit `b76c1d3` and
removed from the working tree by ISSUE-27) merged this column through
**unmodified** from `chip_targets.csv`
while overriding only the bbox corner fields (`chip_lon_min` etc.) with the
correct per-target values -- its own docstring even names the general
problem ("group-level chip_* corners ... do NOT match the on-disk chips")
but the fix only covers the bbox, not the offset.

**Verified empirically across 25+ random anchors**: every per-target 96 m
chip is centered *exactly* on that target's own polygon centroid (fractional
position 0.5000, 0.5000 within the bbox, to 4 decimal places, universally).
The correct offset for marker/crop placement in the current chip geometry is
therefore always ~(0,0) -- but the stored `target_offset_x_m/y_m` instead
carries the old group-relative value, observed ranging from ~0 to >30 m in
this sample.

**This is not confined to ad hoc analysis scripts.** `run_adaptive_scan.py:479-484`
(`make_fixed_extent_review_renderer`, docstring'd "the ISSUE-25
target-centred teacher renderer") reads `anchor.get("target_offset_x_m")`
the same way to build the **tight** review crop
(`crop_context_multiplier=0.01`, `min_crop_size_m=24`/`48`) that Gemini
actually scored for the entire RUN 2 corpus. If a target's stale offset is
large, the crop Gemini was shown was centred tens of metres from the real
target -- this is a plausible, and on today's evidence probably the
dominant, mechanism behind the "marker on driveway/road/lawn" pattern the
QA-sample memo (§9-12) and this memo's own §15 both found, corpus-wide, not
a census-side segmentation defect.

**Dose-response evidence** (offset magnitude by census-placement-check
grade, this memo's own §15 samples): positive control (n=30, confirmed-good
anchors) mean=7.6 m / median=4.6 m; `b2`-graded (n=33) mean=14.5 m /
median=14.1 m; `c`-graded "no PV visible" (n=46) mean=21.4 m / median=23.4 m
-- worse grade tracks bigger stale offset almost monotonically.

### 17.2 But the bug doesn't explain everything -- a real residual survives

Re-rendered `t00001384` and `t00039667` with the offset forced to `(0,0)`
(i.e. the marker placed at the true, current chip centre). Neither becomes
clean: `t00001384`'s marker still sits in the walkway/HVAC gap between two
roof sections; `t00039667`'s still sits in the blank strip between two
arrays (this anchor's own stale offset was only ~5 m to begin with, so
zeroing it barely moves anything). **A genuine placement problem, independent
of the stale-offset bug, survives for at least these two anchors** --
consistent with the owner's original mechanism, just not via the exact
"non-convex-merge pushes centroid outside the polygon" path (see 17.3).

### 17.3 Full decomposition (owner-requested polygon overlay + geometry stats)

Method: for all 33 `b2`-graded anchors (24 flagged + 9 control, §15) and
all 5 index cases, rendered three things on the same Vexcel crop: (1) the
PRODUCTION marker/bbox exactly as `run_adaptive_scan.py` draws it (stale
offset), (2) a TRUE-CENTROID marker with offset forced to 0, (3) the real
detection polygon outline, resolved via `source_feature_id` positional
index into the source GPKG (`.iloc[source_feature_id]`; verified this
indexing is correct -- centroid matches `anchors_all.csv`'s own
`centroid_lon/lat` to ~1e-6 for a spot-checked anchor). Graded each by eye:

- **(i)**: polygon + true centroid land cleanly on visible PV; only the
  production marker (stale offset) is off-structure -- **pure stale-offset
  artifact, fixable entirely in this repo, no upstream involvement.**
- **(ii)**: polygon/true-centroid itself misses the PV (or no PV is
  confirmable there at all) -- **a genuine placement issue that survives
  removing the offset bug.**

| group | n | (i) stale-offset artifact | (ii) genuine issue |
|---|--:|--:|--:|
| `b2_flagged` | 24 | 20 (83.3%) | 4 (16.7%) |
| `b2_control` | 9 | 4 (44.4%) | 5 (55.6%) |
| **all b2** | **33** | **24 (72.7%)** | **9 (27.3%)** |
| index cases | 5 | 0 (0%) | **5 (100%)** |

**Headline: ~73% of the `b2` finding (§15-16's "marker on non-structure
ground while PV visible elsewhere") is the stale-offset bug alone** --
fixing it (use the chip's own centre / offset zero for every ISSUE-25-rebuilt
anchor, since that's already proven to equal the true centroid) would
directly recover the crop-centring for roughly three-quarters of these
cases with no upstream change needed. **The remaining ~27% -- and, notably,
100% of the specific index cases every reviewer flagged as their strongest
exhibits -- reflect a genuine placement problem that persists at the true
centroid.** This makes sense in hindsight: index cases were selected
*because* they survived scrutiny as real problems, not because they were
registration artifacts.

**Geometry-stats comparison did not support the "non-convex merge" mechanism
as cleanly as hypothesized**: `n_merged>1` rate is 37.5% (`b2_flagged`) /
22.2% (`b2_control`) vs 30.0% (positive control) -- elevated for flagged,
*not* elevated for control, both close to the baseline. Solidity (area /
convex-hull area) medians are nearly identical across all three groups
(~0.93-0.94). **100% of polygons in every group contain their own centroid**
(`centroid_inside_polygon=True` universally) -- none of these are
severely non-convex enough to push the centroid fully outside the polygon
boundary. The two lowest-solidity anchors in the sample (`t00028019`,
solidity 0.743; `t00022766`, solidity 0.745) split one each way ((ii) and
(i) respectively) -- too small an n to confirm solidity as a predictor, but
directionally not the dominant driver the centroid-outside-polygon
mechanism would predict. What the (ii) cases actually show on inspection is
narrower than hypothesized: a detection polygon whose own boundary already
includes some non-PV area immediately adjacent to a real array edge (a
segmentation-precision issue at the array boundary, not a
centroid-in-a-distant-gap issue) -- `t00001384` and `t00040072` both show
the polygon straddling the panel/non-panel boundary with the centroid
landing just on the wrong side of it.

### 17.4 Revised conclusion

Two independent, compounding problems, not one:

1. **A real, severe, corpus-wide bug in this repo** (stale
   `target_offset_x_m`/`target_offset_y_m`, carried through the ISSUE-25
   per-target rebuild without being recomputed) that plausibly degraded the
   actual Gemini review crop for a meaningful share of the 41,393-anchor RUN
   2 corpus -- the dominant explanation (~73%) for this memo's own `b2`
   finding. **Fix**: for any ISSUE-25-rebuilt anchor, the correct
   offset is 0 (chip is already centred on the target); either zero these
   two columns at the source or have `make_fixed_extent_review_renderer`
   ignore them for target-centred chips. This is squarely in this repo's
   remit to fix, and per D18/ISSUE-19 discipline would need a documented
   `geometry_version`-style migration (re-crop + re-score the affected
   anchors), not a silent hotfix.
2. **A smaller, genuine residual** (~27% of `b2`, 100% of the specific
   index cases) where the true detection polygon itself is imprecise at the
   panel/non-panel boundary -- not explained by the offset bug, not cleanly
   predicted by `n_merged`/solidity either. This is closer to upstream
   segmentation precision than to anything this repo's crop-centring code
   controls, though a polygon-aware crop (e.g. sized/centred on the full
   polygon bounding box, or using `shapely`'s `representative_point()`
   instead of a raw centroid) could plausibly help even this residual
   without needing a census re-run.

The §15/§16 headline (this is a real, validated, census/target-placement
problem, not GEHI registration and not a rendering artifact) still stands --
but "census-side" was too coarse. The bulk of it is this repo's own stale
CSV column, cheaply fixable; only a minority is a genuine upstream
detection-precision issue.

### 17.5 Artifacts

- Tooling (scratchpad): `polygon_overlay_render.py`, `run_polygon_overlay.py`,
  `build_polygon_overlay_html.py`.
- Data: `$OUT/polygon_overlay_check/` (`polygon_stats.csv` [68-row geometry
  stats: n_merged, solidity, centroid-in-polygon, bbox dims, per anchor],
  `graded_anchors.csv` [38-row i/ii verdict + note], `renders/`, `sheets/`,
  `polygon_overlay_strips.html` [18-anchor curated HTML, colour-coded by
  verdict]).

