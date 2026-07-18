# DATA — n=100 visual audit of the 71%-blind census bucket (2026-07-18)

Parent: [`DATA-fullscan-run2-parallax-audit-2026-07-17.md`](DATA-fullscan-run2-parallax-audit-2026-07-17.md)
mitigation #2 (owner-approved). Companion: [`DATA-fullscan-run2-qa-sample-2026-07-17.md`](DATA-fullscan-run2-qa-sample-2026-07-17.md)
(the earlier 100-anchor stratified QA sample and its marker-rendering fix,
§7-12 of that memo). This is a separate, purpose-built random sample sized
specifically to bound the risk in the population the parallax-audit's own
fingerprints structurally cannot see: `done_installed_during_census`
(23,317), `done_ambiguous_marker_missed_pv` (4,645), and
`done_ambiguous_no_recent_anchor` (1,433) — 71% of the run, all of which
never records a single confident-present observation by construction of how
those statuses are assigned.

**This is the decision input for whether the `done_installed_during_census`
slice of the deliverable (56% of the run) ships as point-date estimates or
gets caveated to "no later than census."** Read the whole memo before acting
on the headline number — the honest answer has real texture (see §5).

## 0. Headline

**~33-46% of `done_installed_during_census`, ~65-70% of `marker_missed_pv`,
and ~80% of `no_recent_anchor` show a real placement/rendering problem in
this sample — overwhelmingly a marker that isn't on the roof at all (open
ground, driveway, road, pool edge, tree canopy), not a scoring failure by
Gemini.** Read literally, the fingerprint-invisible 71% of the run is worse,
not better, than the fingerprint-visible 29% audited in the parallax memo.
Zero genuine "model looks straight at a visible PV array and confidently
calls it absent" cases were confirmed anywhere in this sample once each
surprising case was cross-checked against the model's own evidence text —
every case that looked like a miss from the wide-chip view turned out, on
checking the per-frame evidence string, to be the model correctly reporting
a real array *adjacent to or on a different section of the same roof than*
the actual marker location. This is good news about Gemini's reliability
and bad news about the marker geometry: it means the fix is entirely
upstream (target polygon / marker offset), not a scorer/prompt problem.

## 1. Sampling design

Fixed seed `20260717002` (distinct from the earlier `20260717` QA-sample
seed), drawn from `intervals/install_intervals_all.csv` (post
`infer_install_dates.py` reclassification — this is the file that actually
has the `marker_missed_pv` label; the raw `scan_state.status` does not).
Quotas per the owner's brief (~70/20/10, not pure-proportional):

| status | population | sampled |
|---|--:|--:|
| `done_installed_during_census` | 23,317 | 70 |
| `done_ambiguous_marker_missed_pv` | 4,645 | 20 |
| `done_ambiguous_no_recent_anchor` | 1,433 | 10 |
| **total** | **29,395** | **100** |

## 2. Method

For every sampled anchor, two views per frame, side by side:

1. **The marker-crop review PNG** — the exact `review_extent_m`-cropped,
   marker-annotated image Gemini scored, resolved via the same
   `resolve_marker_crop_path`/`target_crop_review_png_path` mechanism used
   for the earlier QA-sample HTML fix (§7 of the companion memo) — a pure
   read of the already-cached production artifact, zero new renders.
2. **The wide, un-cropped 96m raw chip**, same dates, with the target's
   actual bbox drawn in magenta and the search-radius marker in cyan — lets
   a human confirm what building the tight crop is even looking at, and
   whether a visible PV array anywhere in frame belongs to the target or a
   neighbor.

Built as 25 combined contact sheets (4 anchors each) in scratchpad only, no
writes under the read-only chips directory (verified before/after via
`find <chips-dir> -newermt <window>` → 0 hits throughout). Graded each
anchor's worst/most-informative frame into:

- **clean** — genuinely on the correct roof, no PV ever visible (correct
  absence), or a plausible genuine transition with the array visible inside
  the box.
- **problem** — with subtype `off_target` (marker not on any roof: ground,
  driveway, road, pool, tennis court, parking lot), `canopy` (marker in tree
  canopy/dense vegetation, no roof visible), `corrupt_frame` (blank/blown-out
  frame scored as a confident observation), or `model_miss` (marker
  genuinely on a visible array, scored absent with no exculpatory evidence
  text) — **zero confirmed `model_miss` cases in this sample**, see §4.
- **unresolvable** — genuinely can't tell at this resolution (pool/patio
  boundary ambiguity, ornate roof detail, resolution-limited).

**Critical methodological correction made mid-audit, kept in because it
matters for how to read the results**: several anchors initially looked like
severe "Gemini stares at an obvious carport/rooftop solar array and calls it
absent" cases from the wide-chip view alone (`t00023745`, `t00027092`,
`t00032315`, `t00022212`, `t00031213`, `t00039215`). Cross-checking each
against its actual `evidence` string in the scan_state JSON overturned every
one: the model was explicitly and correctly saying things like *"marker is
on an asphalt parking lot surface with parked cars"*, *"the marker is
positioned over vegetation... not on any roof"*, *"neighboring roof sections
show installation but marker area is bare"*, and *"roof surface is
smooth/blank at the marker"* / *"roof vent or HVAC unit visible at
marker"* — i.e., the model was right about what it saw, and what it saw at
the *exact marker coordinates* genuinely was not the array, even though a
real array sat meters away in the same wide chip. **Lesson applied
throughout the rest of the audit: any anchor where a wide-chip array looked
suspiciously "missed" was evidence-text-verified before being called a model
failure; none survived that check.** This means the wide-chip visual
alone systematically over-reports "model miss" and under-reports "marker
offset" — a bias worth flagging to whoever designs the next round of
tooling, since it's the opposite direction of the more common failure
(marker on a driveway that just looks unremarkable and gets waved through).

## 3. Results

### 3.1 Grade counts and Wilson 95% CIs

| status | n | clean | problem | unresolvable | problem rate (95% CI) |
|---|--:|--:|--:|--:|---|
| `done_installed_during_census` | 70 | 38 | 23 | 9 | 32.9% [23.0%, 44.5%] |
| `done_ambiguous_marker_missed_pv` | 20 | 6 | 13 | 1 | 65.0% [43.3%, 81.9%] |
| `done_ambiguous_no_recent_anchor` | 10 | 2 | 8 | 0 | 80.0% [49.0%, 94.3%] |
| **all three combined** | **100** | **46** | **44** | **10** | **44.0% [34.7%, 53.8%]** |

Sensitivity bounds (how the 10 "unresolvable" anchors move the estimate if
forced to one side or the other):

| status | lower bound (unresolvable→clean) | upper bound (unresolvable→problem) |
|---|--:|--:|
| `done_installed_during_census` | 32.9% | 45.7% |
| `done_ambiguous_marker_missed_pv` | 65.0% | 70.0% |
| `done_ambiguous_no_recent_anchor` | 80.0% | 80.0% |

### 3.2 Problem subtypes

| status | off_target | canopy | corrupt_frame | model_miss |
|---|--:|--:|--:|--:|
| `done_installed_during_census` | 19 | 4 | 0 | 0 |
| `done_ambiguous_marker_missed_pv` | 10 | 3 | 0 | 0 |
| `done_ambiguous_no_recent_anchor` | 6 | 2 | 0 | 0 |

`off_target` (marker on open ground, driveway, road, pool edge, parking lot)
dominates every stratum — canopy-occlusion is a distant second, and no
sampled anchor's *chosen* worst frame was a clean corrupt/blank-frame case
(the sample did include correctly-handled corrupt frames — e.g.
`t00016803`, `t00010941`, `t00022726` all have a blown-out/black frame that
was correctly scored `unusable` and excluded — those don't count against
the anchor since the quality gate worked as designed).

### 3.3 Post-census reference-frame presence-proof tally

The one place this bucket can get a same-anchor "it really is PV" proof
without any fingerprint: does any post-census reference frame show
`pv_present=True`?

| status | n | post-census frame shows present | …of which graded clean | …of which graded problem |
|---|--:|--:|--:|--:|
| `done_installed_during_census` | 70 | 17 | 11 | 1 |
| `done_ambiguous_marker_missed_pv` | 20 | 0 | 0 | 0 |
| `done_ambiguous_no_recent_anchor` | 10 | 1 | 1 | 0 |

For `done_installed_during_census`, 11/17 anchors with a post-census present
read were independently graded clean on the full visual review (plausible
genuine transitions — the post-census frame is corroborating, not
contradicting, evidence for those). Only 1/17 was a graded problem (the
post-census present read didn't rescue an otherwise off-target anchor).
`marker_missed_pv` shows **zero** post-census presents in this sample — an
expected, mechanical consequence of its own definition (upgraded from
`done_installed_during_census` specifically because the *latest* confident
observation, which for most anchors includes any post-census reference
frame, was still absent) — so this stratum structurally cannot self-verify
via the reference tail; whatever ground truth exists for it is external to
the run's own imagery.

## 4. Is any of this "model miss"?

**No confirmed `model_miss` case survived evidence-text cross-checking in
this sample of 100.** Every anchor that looked like a miss from the wide
96m chip resolved, on checking the actual per-frame `evidence` string, into
one of: (a) marker literally on pavement/road/lawn with a real array visible
elsewhere in the same wide frame (`off_target`), or (b) marker on the
*correct* roof but at a non-panel sub-feature (a roof vent, an HVAC unit, a
blank gap) immediately surrounded by a real array on the rest of that same
roof (`t00031213`, `t00039215` — graded `off_target` here since that's the
closest of the four requested subtypes, but the more precise description is
"on-roof, wrong sub-section, adjacent to real PV"). This is a meaningfully
different, and more actionable, story than "the scorer can't see obvious
panels": **the marker/target-polygon geometry is what's wrong, consistently,
across both `done_appears`-adjacent (parallax memo) and always-absent
(`this memo`) populations** — the scorer is behaving correctly given the
coordinates it's handed.

## 5. Overall read — precision-of-language guidance for the deliverable

- **This is not "the corpus is broken."** 46/100 (with 10 more ambiguous)
  sampled anchors are genuinely clean: correctly-placed markers with a
  correct, stable absence read, or a plausible, internally-consistent
  genuine transition confirmed by the post-census reference frame. The
  clean fraction is a real majority-to-plurality depending on stratum, not
  a rounding error.
- **It is also not "trust the point dates."** With a `done_installed_during_census`
  problem rate whose 95% CI runs from 23% to 45% (and a defensible upper
  bound near 46% once the 9 unresolvable anchors are folded in), and
  `marker_missed_pv`/`no_recent_anchor` running meaningfully worse (65-80%),
  a flat point-date claim across this 71% of the run is not supportable by
  what this sample found. The correct calibration is stratum-specific, not
  a single blanket caveat:
  - `done_installed_during_census` (56% of the run): defensible as
    "no later than census, with a materially elevated (~1-in-3, possibly
    higher) chance the underlying observation history is corrupted by a
    mislocated marker" — closer to usable-with-a-loud-caveat than to
    fully-untrustworthy.
  - `done_ambiguous_marker_missed_pv` (11.2% of the run): closer to
    "presumed-PV-by-census-record, position/date not otherwise
    corroborated by this run's own imagery" than to a real interval — the
    65-80% problem rate plus the *zero* post-census self-verification rate
    (§3.3) means this stratum is the weakest link in the whole audit.
  - `done_ambiguous_no_recent_anchor` (3.5% of the run): treat as
    "unresolved," which is already how the pipeline names it — the 80%
    problem rate in a small (n=10) sample is consistent with, not a
    reversal of, that existing label.
- **Root cause is upstream and singular.** Every problem subtype found
  here (`off_target`, `canopy`) and in the parallax memo's `done_appears`
  audit traces to the same place: the target polygon / marker offset
  computation, not the Gemini scorer or the scan orchestration logic. A fix
  that re-registers or re-validates marker placement (ISSUE-23/ISSUE-24
  tooling already exists per the parallax memo's mitigation #4) is the
  single highest-leverage next step for both the fingerprint-visible and
  fingerprint-invisible halves of the run.

## 6. Deliverables

- Graded anchor CSV (100 rows: `anchor_id, lane, status, grade, subtype,
  note, census_date, catalog_max_date, post_census_frame_shows_present,
  pre_census_all_absent_or_unusable, interval_start, interval_end`):
  `~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/qa_census_audit_2026-07-17/graded_anchors.csv`
- This memo.
- Coordinate with the parallax-audit teammate's parallel corrupt-frame pixel
  sweep for the mechanical rate; this memo is the human-grade visual rate
  and does not attempt to reproduce that sweep.
