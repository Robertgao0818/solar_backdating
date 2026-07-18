# DATA — Fullscan run 2 QA strip-sample review (2026-07-17)

Parent: [`RUN-fullscan-gemini-backdating-2026-07-15.md`](RUN-fullscan-gemini-backdating-2026-07-15.md)
§8 step 3 (QA sample before declaring the corpus good for DINOv3 distillation,
v2 Phase 3). Population: 41,393/41,393 terminal, run completed 2026-07-17
19:31 (a24 lane 36,322 anchors, a48 lane 5,071 anchors).

**REVISED 2026-07-17 (v2, marker-annotated re-review) — supersedes the §4-6
conclusion below.** The first pass (§1-6) thumbnailed the raw, un-annotated
chip and judged transitions by eye without seeing where the target's marker
actually was. Once re-rendered with the exact marker-annotated,
review-extent-cropped PNG the scan fed to Gemini (§7), a large and
status-dependent fraction of sampled targets turned out to have their
crosshair sitting on a driveway, road, lawn, tree canopy, or patio — not on
any rooftop at all — invisible in the marker-less v1 thumbnails. **Corpus is
NOT distillation-ready as-is for `done_installed_during_census` and
`done_ambiguous_no_recent_anchor` without a targeted placement audit; the
other four statuses look fine.** See §7-10 for the full re-review; §1-6 is
kept for the sampling/tooling methodology, which is unchanged.

## 1. Sampling design

- Fixed seed `20260717` (`random.Random(20260717)`), Python stdlib
  `random.sample`, one draw per (status, lane) stratum — reproducible from
  `scripts/temporal/build_phase0_qa_html.py`'s data model plus the ad hoc
  sampler below (not committed; scratchpad only, since RUN-doc scope is a
  one-off review, not a repo tool).
- Strata are hand-set quotas (not pure proportional), to satisfy the review
  brief's floor of ≥10 for the four named statuses while keeping the two
  dominant statuses proportionally dominant and splitting each stratum
  across lanes roughly by each lane's share of that status:

| status | population (a24 / a48) | sampled (a24 / a48) | sampled total |
|---|--:|--:|--:|
| `done_installed_during_census` | 25,649 / 2,313 | 37 / 3 | 40 |
| `done_appears` | 7,333 / 1,837 | 20 / 5 | 25 |
| `done_ambiguous_nonmonotonic` | 1,293 / 470 | 9 / 3 | 12 |
| `done_already_present_before_geid_history` | 659 / 403 | 7 / 5 | 12 |
| `done_ambiguous_no_recent_anchor` | 1,385 / 48 | 7 / 1 | 8 |
| `done_ambiguous_orchestrator_error` | 3 / 0 | 3 / 0 | 3 |
| **total** | **36,322 / 5,071** | **83 / 17** | **100** |

Full manifest (anchor_id, lane, status, scan_state_path):
`~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/qa_sample_2026-07-17/qa_sample_manifest.csv`

## 2. HTML strip-review output

`~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/qa_sample_2026-07-17/qa_sample.html`
(self-contained, base64-embedded thumbnails, ~77 MB, 100 anchors, sorted by
status then anchor_id). Built by reusing
`scripts/temporal/build_phase0_qa_html.py`'s `render_anchor`/`render_html`
(imported, not modified) against a synthetic per-anchor state list drawn
from both lanes (no `install_intervals.csv` exists yet for this run —
`infer_install_dates.py` is a later post-run step — so interval rows are
empty and the page falls back to scan_state status/notes, which
`build_phase0_qa_html.py` already supports natively).

**Read-only note.** `build_phase0_qa_html.py`'s normal `_resolve_review_png`
path calls `gehi_common.ensure_review_png`, which writes a sibling `.png`
next to the `.tif` the first time it's invoked — fine for that script's
usual ad hoc use, but this run's chips
(`~/zasolar_data/geid_temporal/basemap_rebuild_2026-07-13/chips/`) were
explicitly in scope as read-only. The adapter monkeypatches
`_resolve_review_png` to hand the raw `.tif` straight to PIL in memory
instead (Pillow opens these single-frame RGB GeoTIFFs natively — verified);
confirmed zero new files written under the chips root during the whole
review (`find ... -newermt <10 min ago>` → 0 hits).

## 3. Screening method

Built one contact-sheet PNG per ~8 anchors (13 sheets total, all in-memory
from the raw `.tif`s, no chips-dir writes) — each anchor gets a labeled row
of every chip in chronological order, color-bordered by verdict (green =
present, red = usable absent, amber = unusable/ambiguous) — and read all 13
sheets plus targeted single-anchor JSON/pixel dives for anything that looked
off. This traded per-chip resolution for full-population-in-one-pass
coverage; every one of the 100 anchors got a direct visual look, with
follow-up raw-pixel/evidence-text checks on ~15 anchors whose row looked
surprising at first glance.

## 4. Screening tallies (n=100)

| bucket | count |
|---|--:|
| consistent (status/transition matches imagery) | 96 |
| suspicious (flagged below) | 4 |
| unreadable imagery (couldn't judge at all) | 0 |

All 4 "suspicious" entries are the *same* underlying pattern (quality-flag
miscalibration on degraded frames), not 4 independent defects, and none
changed the terminal status's correctness in this sample.

## 5. Suspicious anchors

1. **`t00011520`** (a24, `done_appears`) — frame `2020-04-30` is
   near-solid-white on direct pixel check (min 253 / max 255 / mean 254 of
   255), yet scored `quality_flag=usable`, `pv_present=False` with a
   specific, unhedged evidence string ("Marker is on a smooth, flat grey
   roof surface; no PV modules or grid patterns present"). The image
   contains no visible content to support that claim — looks like a
   generic/hallucinated read on a blank frame. Doesn't affect the terminal
   status: the actual absent→present bisection used other, clean frames
   (`2021-06-30` absent → `2022-08-30` present).
2. **`t00031576`** (a48, `done_already_present_before_geid_history`) — the
   post-census reference frame `2025-02-28` is heavily haze/cloud-degraded
   (confirmed by a side-by-side crop against the clean `2023-01-30` frame of
   the same building: the same warehouse roofline and PV array are faintly
   visible *under* the haze), yet scored `usable`, `pv_present=False`
   ("Marker is on a plain, light-colored roof surface; no PV modules are
   present"). All 4 pre-census frames (2019–2023) confidently and correctly
   show the array present, so the terminal status is right — but this one
   frame is a false "removal" read that would be wrong if anything ever
   keyed off it directly (it's currently excluded from the Case B
   classification by design, since only pre-census evidence counts).
3. **`t00003647`** (a24, `done_installed_during_census`) — frame
   `2021-04-30` evidence hedges ("Low resolution/blur; no distinct solar
   modules are visible") rather than confidently asserting a clean roof, so
   this is a milder version of the same pattern: quality self-assessment
   under-triggers on degraded-but-not-blank frames. Terminal status
   unaffected (Case C evidence is pre-census-only and all-absent regardless).
4. **`t00003055`** (a24, `done_appears`) — frame `2020-04-30` is smoke-
   obscured in the strip (visibly so) but scored `usable`/absent rather than
   `unusable`; same mild version of the pattern as #3. Terminal status
   (bisected at `2022-10-30`→ present) unaffected.

**Everything else** (96/100) — including all 12 `done_ambiguous_nonmonotonic`
and all 8 `done_ambiguous_no_recent_anchor` samples, which visually look
"weird" (flip-flopping verdicts, or long runs of absent broken up by
tree-shadow/corruption-flagged unusable frames) — checked out as the system
correctly recognizing genuine ambiguity and punting to the human-review
bucket, not misclassification. Spot-verified via raw JSON that:
`done_ambiguous_nonmonotonic` present→absent→present sequences carry
confident, non-hedged evidence text on both sides of each flip (plausible
look-angle/parallax noise across capture dates, not corrupted data); and
`done_ambiguous_no_recent_anchor` anchors all have a documented reason
(`notes: "all absent but latest_avail not usable after 2 anchor_recovery
rounds"`) tied to a real quality problem on the frame(s) closest to the
census date (tree occlusion, marker-overlay corruption, ambiguous roof
texture) — the classifier is deliberately refusing to borrow confidence from
older/newer frames to avoid mislabeling an unconfirmed census-time state,
which is the conservative behavior you want. The 3
`done_ambiguous_orchestrator_error` anchors are exactly the known cases
already documented in the RUN doc (2× "missing from offline TM catalog CSV",
1× "OSError: decoder error -2" on a corrupt chip) — no new information.

## 6. Overall read (v1 — superseded, see §7-10)

Corpus looks good to proceed to DINOv3 distillation labeling, pending the
human owner's own pass over `qa_sample.html` (or a broader sample if they
want more than 100). The one thing worth a product decision, not a blocking
bug: whether to tighten the Gemini per-chip quality-flag prompt (or add a
cheap pixel-level pre-filter — e.g. reject frames with near-uniform low
color variance / near-white or near-black mean before they ever reach
Gemini) so blown-out/hazy frames are caught by `quality_flag=unusable`
instead of occasionally slipping through as confident absent verdicts. Given
it affected 0/100 terminal statuses here (all 4 hits landed on
non-decision-critical or post-census-reference frames), this reads as a
latent-risk item for a future hardening pass, not a reason to re-run or
discard any part of this corpus.

**(Superseded — see §7-10 below. The marker-less thumbnails behind this
section could not actually confirm the model was looking at the right
building; §7-10's re-review found that assumption was wrong for a large
fraction of small-footprint targets.)**

---

## 7. v2 correction: rendering the exact marker-annotated crop Gemini saw

The owner reviewed the v1 HTML and reported two problems: (a) no marker was
visible, so a human reviewer can't tell *which* roof a strip is judging in
dense areas; (b) the first-rendered anchor looked wrong. Both traced to the
same root cause: v1's `_resolve_review_png` thumbnailed the raw `.tif`
directly, skipping the marker overlay + tight review-extent crop the
production scan actually fed to Gemini
(`make_fixed_extent_review_renderer` → `ensure_single_target_review_png` in
`scripts/temporal/run_adaptive_scan.py:459-503` /
`scripts/temporal/gehi_common.py:765`). That renderer crops to
`review_extent_m` (24 or 48 m, i.e. `chip_arm`-routed) centered on the
target's `(target_offset_x_m, target_offset_y_m)` from `anchors_all.csv`,
upscales to ≥256px, and draws the cyan crosshair + labeled bbox — a much
tighter, more precise view than v1's full-96m raw-chip thumbnail.

**Fix**: `_resolve_review_png` now resolves each frame to
`target_crop_review_png_path(...)` with the exact production parameters
(`crop_context_multiplier=0.01, min_crop_size_m=review_extent_m,
min_output_px=256, draw_marker=True`, bbox from `source_width_m`/
`source_height_m`). Confirmed **666/666 sampled frames already have this
PNG on disk** (the production scan had to materialize it to call Gemini),
so this is a pure read — zero new writes under the read-only chips dir
(verified: `find <chips-dir> -newermt <10 min ago>` → 0 hits, both before
and after the rebuild). `qa_sample.html` was rebuilt in place at the same
path with this fix; it now shows the marker + tight crop for all 100
anchors, 100/100 confirmed rendering correctly.

## 8. First anchor: `t00001384` — re-adjudicated, verdict LIKELY WRONG / not defensible as rendered

First anchor in the HTML's sort order (status, then anchor_id): **`t00001384`,
status `done_installed_during_census`, target `T03`, `source_area_m2=20.92`**.

With the marker visible, the crosshair sits in a walkway/HVAC-equipment gap
between two sections of a large commercial roof — **not on the panel array
that covers most of the same building**, confirmed by annotating the raw
full chip with the target's actual bbox (magenta rectangle) alongside the
visible array: the array is meters away, on a different section of the same
roof. Gemini's own evidence text at `2023-01-23` says *"the marker is
located on a plain, light-colored roof section with no grid pattern or
modules present; **visible PV is located on adjacent roof**"* — the model
noticed the mismatch itself. The lone `present=True` read (`2025-02-09`,
a post-census reference frame) most likely reflects the adjacent array
bleeding into the tight 24m review crop rather than genuine coverage of
target T03's own footprint.

**Verdict: not defensible as rendered.** The terminal status is technically
self-consistent with the state machine's Case C logic (all *pre-census*
observations at T03 read absent → `done_installed_during_census`), but the
target polygon itself does not appear to correspond to the panel array
anywhere in the sequence — this looks like an upstream census/segmentation
placement error (T03's footprint lands on non-PV roof infrastructure
adjacent to a real array), not a scoring bug. This is exactly the failure
mode the codebase already has a name for in a different direction
(`scan_state.py`'s `done_ambiguous_marker_missed_pv`), except the existing
automated check (`latest_absent_date >= census_imagery_mid_date`) does not
fire here because the timing happens to line up — so this specific instance
would slip through that guard.

## 9. Re-pass with markers on: systemic off-target finding

Per the owner's instruction, re-screened (with the marker visible) at
minimum the 25 `done_appears` + 12 `done_ambiguous_nonmonotonic` anchors —
then, because the first re-checks immediately turned up more hits, extended
the marker re-check to effectively the full 100-anchor sample (97 anchors
with imagery; the 3 `done_ambiguous_orchestrator_error` anchors have zero
rounds/images and are unaffected). Screening method: one contact-sheet strip
per anchor built from the resolved marker-crop PNGs (same read-only
resolution as §7), color-bordered by verdict, viewed directly; ambiguous
cases got a further high-resolution single-anchor render plus a check of
the round's `evidence` text.

**Finding: a large, status-dependent fraction of small-footprint targets
have their marker sitting on a driveway, road, parking lot, lawn, tree
canopy, or patio — not on any rooftop.** This was completely invisible in
the v1 marker-less thumbnails (which just showed "a house, plausible") and
is often confirmed by the model's own evidence text once you look for it
(*"the marker is located on a patch of vegetation/ground, no building or
rooftop structure visible"*; *"marker is located on a wooded or vegetated
area adjacent to a structure, not on a roof segment with PV modules"*).

| status | anchors checked | flagged (wrong + strong visual concern) | rate |
|---|--:|--:|--:|
| `done_installed_during_census` | 40 | 18 (6 confirmed off-target + 11 strong visual concern + 1 borderline, incl. `t00001384`) | **45%** |
| `done_ambiguous_no_recent_anchor` | 8 | 6 | **75%** |
| `done_appears` | 25 | 2 | 8% |
| `done_ambiguous_nonmonotonic` | 12 | 0 | 0% |
| `done_already_present_before_geid_history` | 11 | 0 | 0% |
| `done_ambiguous_orchestrator_error` | 3 | n/a (no imagery) | — |

**This is not evenly distributed noise — it is structurally concentrated by
design.** An off-target crosshair (driveway/road/tree/lawn) has no PV on it
by construction, so Gemini correctly and confidently reports "absent" at
every date — which is *exactly* the signal `scan_decision.py`'s Case C
(`all observations absent` → `done_installed_during_census`) and Case R
(`latest unusable/no usable obs` → `done_ambiguous_no_recent_anchor`) are
built to detect. The state machine has no way to distinguish "genuinely
absent PV at a real target" from "no target here at all" — both statuses
mechanically launder an off-target anchor into themselves. This also
explains the inverse: `done_already_present_before_geid_history` requires
*confident, repeated* present reads across 5-10 independent capture dates
spanning years, which is very unlikely to happen at a genuinely empty
location by chance — so that status is close to self-verifying, and indeed
0/11 checked show any placement problem. `done_ambiguous_nonmonotonic`
similarly requires at least two independent confident "present" reads
(before and after an "absent" blip), which an off-target location is
unlikely to produce even once, let alone twice — 0/12 checked show it.
`done_appears` needs exactly one confident present read to trigger, which
is rarer than "always absent" but not as rare as two — 2/25 (8%) checked.

**Scale implication.** `done_installed_during_census` is 27,962/41,393
(67.6%) of the *entire* corpus. Even allowing for wide sampling error on
n=40, a placement-concern rate anywhere near 45% in that status implies the
true corpus-wide count of potentially-mislocated targets in this bucket
alone is likely in the thousands, not a handful — this is a corpus-quality
issue at scale, not sampling noise to be waved off.

### Flagged anchors (marker-crop shows no rooftop, or target clearly on the wrong roof section)

**Confirmed off-target / no roof under the crosshair in any frame:**
`t00014261` (T02, area 5.47m², `done_appears` — crosshair on a driveway
next to a pool, all 10 frames), `t00025403` (T02, area 4.87m², `done_appears`
— crosshair on a road; the lone "present" read at `2023-08-28` is very
likely a parked truck/trailer roof mistaken for PV, not a building),
`t00004700` (T01, 15.34m², `done_installed_during_census` — road/vegetation,
Gemini's own evidence says so at `2024-03-30`), `t00005979` (T01, 11.76m²,
`done_installed_during_census` — tree canopy/driveway strip, Gemini's own
evidence says so at `2023-01-23`), `t00008105` (T02, 7.87m²,
`done_installed_during_census` — road/lawn), `t00012909` (T04, 12.53m²,
`done_installed_during_census` — parking lot), `t00015760` (T03, 36.13m²,
`done_installed_during_census` — road in front of the building, not the
roof itself), `t00006826`/`t00007067`/`t00012572`/`t00022767`/`t00038450`
(all `done_ambiguous_no_recent_anchor`, areas 7.9-18.1m² — trees/lawn/road).

**Borderline (adjacent to a real array, likely bleed-through, not clean
no-roof):** `t00001384` (§8, detailed above).

**Strong visual concern, not individually evidence-verified (same pattern,
smaller footprints, flagged for the same reason — crosshair appears to sit
on pavement/trees/patio rather than a roof edge):** `t00010562`,
`t00013046`, `t00015435`, `t00019345`, `t00020283`, `t00021499`,
`t00022766`, `t00030337`, `t00035728`, `t00038860`, `t00039476` (all
`done_installed_during_census`, areas 7.7-25.3m²), `t00007423` (
`done_ambiguous_no_recent_anchor`, 13.3m²).

## 10. Overall read (revised)

**Do not treat this corpus as distillation-ready for
`done_installed_during_census` or `done_ambiguous_no_recent_anchor` without
a targeted placement-sanity pass first.** `done_appears`,
`done_ambiguous_nonmonotonic`, and `done_already_present_before_geid_history`
look solid (0-8% concern rate, and the `done_appears` hits look like
isolated hallucinations rather than a structural pattern). Recommended next
steps, in order of leverage:

1. **Quantify at scale, cheaply, before any more manual sampling.** The
   marker's pixel coordinates are computable from `anchors_all.csv` alone
   (`target_offset_x_m/y_m`, `chip_size_m`) with no Gemini calls needed — a
   simple automated check (e.g., does the marker's crop have a rooftop
   segmentation/edge signature at all, or is it uniform pavement/grass/tree
   texture by some cheap classical CV heuristic) could estimate the
   corpus-wide rate in `done_installed_during_census` directly, without
   requiring a human to eyeball thousands of crops.
2. **Trace the root cause upstream, in the census/segmentation pipeline**
   (`chip_targets.csv` / `target_offset_x_m`/`target_offset_y_m` /
   `source_width_m`/`source_height_m` generation), not in this scan or its
   scorer — the scan and Gemini are behaving correctly given the target
   geometry they're handed; the geometry itself looks wrong for a
   meaningful slice of small-footprint (~<25 m²) targets.
3. Re-run/re-derive install dates only for the affected `anchor_id`s once
   the audit identifies them, rather than discarding or blindly trusting
   the whole `done_installed_during_census` bucket.

The §5-6 quality-flag-calibration finding (haze/blowout frames sometimes
scored `usable`) still stands as a secondary, lower-severity issue and does
not need to be re-prioritized above the placement finding above.

## 11. v3 recalibration (2026-07-17, same day): my "off-target" bucket conflated distinct failure modes

Owner review of the marker HTML surfaced two more precise index cases and a
correction to how §9's binary "wrong/OK" grading should work: don't just
check whether the verdict matches what's inside the box — check whether the
box is on the *right roof* (track the building across frames; a shifting
marker relative to a stable building outline is parallax/off-nadir
displacement, not the building moving), and separately, check whether "no
PV, just terrain" reads are actually terrain or a real small/occluded
structure. Re-examined both index cases and one of my own §9 calls at high
zoom (8x + contrast enhancement) plus the full un-cropped 96m chip for
building-outline comparison across all dates. Read-only throughout — same
resolution method as §7/§9, zero writes to the chips dir.

**`t00039667` (a24, `done_appears`, target T03, 17.10 m², index case for
parallax).** The marker sits at the boundary between two roof sections of a
flat industrial building. Across 2019-2022 (`absent`), I do **not** find any
solar array visible *anywhere* in the wider 96m frame, at any zoom I tried —
if an already-existing array were merely displaced out of the marker's tight
crop, I'd expect to see it elsewhere in the same frame, and I don't. So I
can't independently confirm the owner's "true install date much earlier"
read for those early frames from the imagery alone (their population-level
parallax audit may have other evidence, e.g. cross-referencing a
higher-confidence array outline, that I don't have access to). What I *can*
confirm: the final `2025-03-30` frame is a genuine, unexplained contradiction
— present at `2024-02-29`/`2024-03-30`, then `absent`/`ambiguous` again at
`2025-03-30` with Gemini's own evidence admitting this "contradict[s] the
expected ground truth." Panels don't get removed and reappear; this specific
flip is consistent with an off-nadir capture whose viewing angle projects
the (real, installed) array outside the marker's tight crop for that one
date — i.e. the parallax mechanism is real and visible here, just not
provably the explanation for the *early* absents in my own re-check.

**`t00038450` (a24, `done_ambiguous_no_recent_anchor`, target T01, 18.11 m²,
index case for terrain-misread).** Re-cropped tight (16m) at 8x magnification
with contrast/brightness enhancement on the two frames the owner called out
(`2023-11-24`, `2025-02-28`). Honest result: **I still cannot visually
confirm a rooftop or PV structure at the marker in either frame** — it reads
as dense tree canopy/dappled shadow to me too, with no straight roof edges
or grid pattern visible even enhanced. I'm flagging this discrepancy
transparently rather than either rubber-stamping the owner's read or
overriding it: the original SAM segmentation confidence for this target is
0.99 (very high), which is real evidence something PV-like exists at this
census's source imagery — and given the tiny footprint (6.57m × 4.90m, shed-
or small-outbuilding-sized) sitting right against what looks like a hedge/
tree line in every vintage I have, "a small structure that's genuinely
occluded by overhanging tree canopy in every historical capture I can pull"
is a live possibility I can't rule out from these images alone, distinct
from "Gemini is confidently misreading clear terrain as clear terrain." My
own eyeball tool has hit its resolution limit here; this is exactly the kind
of case the population-level parallax/occlusion audit is better positioned
to resolve than per-sample manual review.

**Walkback on one of my own §9 calls: `t00014261` (a24, `done_appears`,
target T02, 5.47 m², originally listed as "confirmed off-target").** Re-zoomed
at 8x: the "present" reads at `2023-11-24`/`2025-03-30` do show a distinct
dark rectangular shape with clean geometric edges on the driveway, absent in
the `2019-01-15` baseline — and it sits in the *same* position across both
present-dated frames 16 months apart, which argues against a parked vehicle
(those move) and toward a fixed installation. Ground-mounted PV arrays on
paved areas (driveways, poolside) are a real, if less common, installation
type. **Revising this from "confirmed wrong" to "uncertain — plausible
ground-mount PV, not confidently a hallucination."** This is the clearest
example of my original §9 pass being too quick to equate "not on a
rooftop" with "wrong": that equation only holds for locations where no PV
installation of any kind is physically possible (a road with moving traffic,
an open parking lot), not for any non-roof surface.

**Net effect on the §9 tally: split it into two tiers, not one.** Re-reading
my own flagged list with this distinction in mind:
- **High-confidence, no-PV-possible locations** (open parking lot with no
  structure anywhere nearby, or road with clearly *different* passing
  vehicles across different capture dates): `t00012909` (parking lot),
  `t00025403` (road — the "present" read is a different-shaped vehicle each
  time, unlike `t00014261`'s fixed shape). These two I'm confident are
  genuinely wrong reads, not just non-rooftop.
- **Real but unresolved by eyeball alone** (tree/lawn/road near real
  building(s) in the same frame, small footprint, could be a displaced-
  marker miss of a real nearby roof, an occluded tiny structure, or a
  legitimate non-standard installation): the remaining ~22 anchors listed in
  §9's tables, including both index cases. I'm not walking back the "these
  look concerning" read — the marker plausibly isn't showing Gemini the
  right thing in most of them — but I can no longer confidently sort them
  into "wrong" versus "actually fine, my tooling just can't see it" without
  higher-resolution imagery, a building-height model, or the population-
  level statistical approach the parallax-audit teammate is running. Treat
  §9's 45%/75% rates as an upper-bound "needs-review" signal, not a
  confirmed-wrong rate — the true wrong rate is somewhere between the 2
  high-confidence anchors above and that upper bound.

This doesn't reverse §9-10's core recommendation (don't trust
`done_installed_during_census`/`done_ambiguous_no_recent_anchor` without a
targeted audit) — if anything it reinforces needing the automated,
population-scale check over more manual eyeballing, since manual eyeballing
demonstrably runs out of resolving power exactly on the ambiguous cases that
matter most.

## 12. v4 recalibration: worst-frame offset-severity histogram (ISSUE-23)

Owner unified the root cause as per-frame marker **offset** (ISSUE-23
displacement): large per-frame offsets put the marker off the building
entirely (yards/lawns); small offsets leave the marker on the right building
but the real PV array just outside/at the edge of the box. Third index case:
`t00024435`. Re-graded every sampled anchor's *worst* frame into 4 tiers
instead of a binary wrong/OK:

- **(a) marker off-structure** — no roof/building edge under the box in any
  frame.
- **(b) on-roof but PV outside/partial** — box is on the correct building,
  but the real array only partially overlaps the box or sits just past its
  edge.
- **(c) PV enclosed** — box cleanly contains the visible array; the clean
  positive case.
- **(d) genuinely no PV** — box on the correct roof, and there really is no
  panel visible anywhere accessible to this scan (either a true negative, or
  the true install postdates the anchor's `catalog_max_date` — i.e. outside
  what this scan could have seen at all, not an offset artifact).

**Index case `t00024435` (a24, `done_installed_during_census`, T01, 8.53 m²)
— re-checked, but I can't confirm the specific (b) read.** Marker sits
cleanly on a real, consistent red-tile roof across all 6 frames (confirmed
against the wide 96m chip, not just the tight crop) — so this is clearly
*not* case (a). But I could not find a PV array anywhere in the wide chip,
on this roof or any neighboring one, at any of the 6 dates — Gemini's own
`2025-01-25` evidence ("the marker location does not coincide with any
visible panel array on this roof segment") reads to me as templated
"no panels found" phrasing rather than confirmation it saw an array
elsewhere. One relevant, non-offset explanation: this anchor's
`catalog_max_date` is `2025-01-25` (its `census_date` is `2024-02-17`) — if
the true install happened after `2025-01-25`, no frame this scan could ever
fetch would show it, which looks identical to an offset miss from the
outside but has nothing to do with marker placement. I'm grading this **(d)**
from my own re-check rather than (b), flagging the disagreement with the
stated diagnosis rather than silently adopting it — happy to be overruled by
the population-level audit if it has access to a sharper/current reference
image I don't.

### Severity histogram (worst frame per anchor, n=97 imaged anchors)

| status | (a) off-structure | (b) on-roof, PV outside/partial | (c) PV enclosed | (d) genuinely no PV / postdates scan | n |
|---|--:|--:|--:|--:|--:|
| `done_installed_during_census` | 16 | 1 (`t00001384`; `t00024435` graded (d), see above) | 0 | 23 | 40 |
| `done_ambiguous_no_recent_anchor` | 5 | 1 (`t00007423`) | 0 | 2 | 8 |
| `done_appears` | 1 (`t00025403`) | 2 (`t00014261`, `t00039667`) | 22 | 0 | 25 |
| `done_ambiguous_nonmonotonic` | 0 | 0 | 12 | 0 | 12 |
| `done_already_present_before_geid_history` | 0 | 0 | 11 | 0 | 11 |
| `done_ambiguous_orchestrator_error` | — | — | — | — | 3 (no imagery) |

Notes on how the ambiguous cells were graded, since (a)/(b)/(d) boundaries
are exactly where my own resolving power runs out (§11 already flagged
this): `t00038450` (index case, `done_ambiguous_no_recent_anchor`) is
counted in the 5 "(a)" for that row on strict visual grounds (no roof edge
visible under the box in any frame), even though I can't rule out a real,
tree-occluded small structure there (§11) — if that's the truth, the correct
cell is (b) or (d), not (a); I don't have the resolution to tell. Similarly,
several of the 16 `done_installed_during_census` "(a)" cells are the same
"strong visual concern, not individually evidence-verified" set from §9,
carried forward under the same caveat. Only `t00012909` (open parking lot,
no structure anywhere in frame) and `t00025403` (road, differently-shaped
vehicle each date) are (a) calls I'd defend with high confidence; the rest
of the (a) column is my best-effort read, not a confirmed tally — treat the
16 and 5 as upper bounds on true off-structure count, same caveat as §11's
45%/75%.

**Read**: `done_appears` is overwhelmingly clean (22/25 case (c), both
placement failure modes rare there) — matches the owner's read that
accuracy is good there. The other four non-error statuses split sharply:
`nonmonotonic` and `already_present` are 100% clean in this sample (0
placement issues found in either grading pass); `installed_during_census`
and `no_recent_anchor` carry essentially all of the (a)/(b) concern. No case
(b) was common anywhere except the two named index cases plus `t00001384` —
in this sample, when the marker has a problem it's usually the large,
off-structure kind (a), not a small in-frame miss (b); that itself might be
worth the population audit checking (is the offset distribution actually
bimodal — mostly-fine or grossly-off, with few partial misses — or is (b)
undercounted here because it's the hardest tier for a human eyeballing a
280px crop to catch)?

## 13. Follow-on: dedicated n=100 audit of the 71% fingerprint-blind bucket

§9-12 above sampled across all six terminal statuses. A separate,
purpose-sized random audit (n=100, fixed seed `20260717002`) of just the
three statuses with zero fingerprint coverage — `done_installed_during_census`,
`done_ambiguous_marker_missed_pv`, `done_ambiguous_no_recent_anchor`, 71% of
the run — was run as its own deliverable, since it's the specific decision
input for whether that 71% ships as point dates or gets caveated. See
[`DATA-fullscan-run2-census-bucket-audit-2026-07-17.md`](DATA-fullscan-run2-census-bucket-audit-2026-07-17.md)
for the full write-up: headline is a 33-46%/65-70%/80% problem rate across
the three strata respectively (overwhelmingly off-target markers, not model
misses), with graded CSV at
`qa_census_audit_2026-07-17/graded_anchors.csv`.
