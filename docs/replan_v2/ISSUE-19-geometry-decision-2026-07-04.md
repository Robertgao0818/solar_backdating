# ISSUE-19 Decision Memo — Chip-geometry policy at the Phase-3 re-render (2026-07-04)

Parent: [`ISSUE-19-chip-geometry-policy.md`](ISSUE-19-chip-geometry-policy.md) ·
PRD D18 / User story 38 ([`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md)) ·
Evidence: [`ISSUE-04-decision-memo-2026-07-03.md`](ISSUE-04-decision-memo-2026-07-03.md) Result 2 (corrected 2026-07-04)

## TL;DR

1. **Decision: adopt policy (b) — a single global tighter crop — as the
   Phase-3 re-render default.** The re-render ships under a named geometry
   version `chip_geom_v2_tight12` (target-centered ~12 m context floor / 256 px
   short side). `chip_geom_v1_banked96` (the frozen banked render) stays the
   **legacy default** and is never re-run.
2. **Frozen 96 m (a) is retained only as the banked-comparability baseline,
   not as the re-render policy.** The tight-crop arm eliminated the
   `gemini_failed` stratum's undated-flips (0.113 → 0.000 FPD, 0.125 → 0.000
   sustained) and abstains (0.025 → 0.001) and lifted sustained year-hit
   0.838 → 0.938 with sustained date-hit unchanged (0.938). That stratum's
   noise is an imaging artifact of render geometry (ISSUE-04 CONFIRMED), so
   keeping the coarse crop for the re-render would knowingly bake a fixable
   resolution artifact into the student's **input pixels**. (What the
   re-render cannot fix: the harvested teacher *labels* were produced on v1
   renders and carry that geometry's bias regardless of this choice — see
   "Distillation-label caveat" below.)
3. **Per-installation adaptive (c) is rejected/deferred: untested + it
   fragments both the emission model and cache comparability.** The orphaned
   pilot formula (`build_gt_anchor_manifest.py`) was never validated against
   scorer accuracy, and a per-anchor geometry degree of freedom gives every
   anchor its own render resolution — which shatters the zoom/geometry
   stratification the decoder's emission matrix (D2/D17) conditions on.
4. **Geometry is provenance-recorded, never a verdict-key component.** A
   geometry change re-renders different crop bytes → a different chip content
   hash → new verdict-store keys → old rows survive unreferenced (no silent
   reuse). `geometry_version` is written to the scoring-provenance `context`
   blob; it is **not** added to `build_verdict_key` and **not** added to the
   verdict key's `extras`. The re-key flows through the content hash and only
   the content hash — which is exactly how `verdict_store.py` already works.
5. **The GEHI download is untouched.** The 96 m chip stacks download exactly
   as today; only the scoring-crop render (`ensure_single_target_review_png`)
   changes. The frozen builder (`build_inventory_chip_groups.py`, 96 m) is
   also untouched — D18 constraint preserved.

## Evidence consumed (ISSUE-04 Result 2, corrected 2026-07-04)

Same 8 `gemini_failed` units, same frozen 493-chip vintage stacks, re-rendered
at the tight-crop arm (`--crop-context-multiplier 0.5 --min-crop-size-m 12
--min-output-px 256`) vs the banked render (effectively ~60 m context at
≥128 px):

| metric (mean over 8 units) | banked 60 m (v1) | tight 12 m (v2) | Δ | reads as |
|---|--:|--:|--:|---|
| FPD undated-flip rate | 0.113 | **0.000** | −0.113 | resolution artifact removed |
| sustained undated-flip rate | 0.125 | **0.000** | −0.125 | resolution artifact removed |
| abstain rate (frames) | 0.025 | **0.001** | −0.024 | "give-up" was censoring-with-cause |
| sustained date-hit | 0.938 | 0.938 | 0 | no date-level regression |
| sustained year-hit | 0.838 | **0.938** | +0.100 | the headline win |
| FPD date-hit | 0.675 | 0.662 | −0.013 | wobble **not** resolution-driven |
| scorer `sequence_confidence` | 0.954 | 0.936 | −0.018 | self-report carries **no** resolution signal |

Two per-unit facts that shape the decision beyond the means:

- **`c0010157/T02` — a recovered unit.** UNDATED (0.9 flip) at 60 m →
  dates cleanly to 2024-02-29 at 1.0 agreement at 12 m. A small dark panel on a
  dark roof: invisible at the coarse crop, unambiguous at the tight crop. This
  is the mechanism the decision is buying.
- **`c0010157/T04` — coarse crops shift emission BIAS, not just variance.**
  Banked confidently returned 2019-08-30 (1.0 agreement) off a frame that on
  inspection shows a **bare roof** — a coarse-crop *confidently-wrong* false
  positive. The tight crop moves it to 2021-10-30 at 0.5 agreement. The coarse
  crop was not merely noisier here; it was systematically wrong. This is why
  ISSUE-04 requires a resolution-*conditioned* emission model, not a variance
  inflation term — and why the geometry belongs in the emission conditioning
  vocabulary (below).

## Evidence limits (stated honestly)

- **n = 8, single stratum.** Every tested unit is from `gemini_failed` — the
  stratum whose flip/abstain behavior motivated the experiment. (ISSUE-04
  did not characterize this stratum's size profile; T02's recovered unit — a
  small dark panel on a dark roof — is consistent with small-install
  difficulty, but "single small installs, mixed vintage quality" describes
  the `done_appears` stratum, not this one.) The result licenses "the tight
  crop fixes the coarse-render failure stratum," not "the tight crop is
  uniformly better everywhere."
- **One arm, not a sweep.** Exactly one alternative geometry was tested
  (12 m / 256 px). There is no crop-size sweep and the adaptive formula was
  never scored. We cannot claim 12 m is optimal — only that it is
  decisively better than 60 m on the failure stratum and harmless on the
  6/8 well-resolved units (identical modal dates, tightened to ~1.0).
- **Why global-tight is nonetheless the right default (population coverage,
  verified on the manifest — corrected 2026-07-04, decision audit F1).** The
  render extent is
  `crop_size_m = min(96, max(min_crop_size_m, 2·radius·mult))`, and in the
  production manifest (`chip_targets.csv`, 41,393 rows) `search_radius_m` is
  a **constant 10.0 for every target** — a CLI arg of the legacy builder,
  not footprint-derived. So v1 renders **60 m for every target** and v2
  renders **12 m for every target** (pre-guard); there is no "large radius"
  regime, and every install, small or large, gets the identical 60→12 m
  shift. The defense of global-tight is therefore **population coverage**,
  not a radius-scaling mechanism: ~88% of manifest targets have a max
  footprint dimension ≤ 12 m (p50 = 6.2 m) and render in *exactly* the
  tested 12 m / 256 px configuration — the evidence covers the bulk of the
  population directly. The remaining ~12% (> 12 m; 3.1% > 24 m; p95 =
  19.4 m; max ≈ 138 m) would be clipped by a bare 12 m window, so **one
  guard follows**: the re-render must ensure the crop window contains the
  full installation footprint (`source_width_m`/`source_height_m` are in
  the manifest). That guard-sized band is a render geometry **never scored
  in any arm** — a stated residual risk: ISSUE-02's re-render QA must
  spot-check a sample of guard-affected anchors before training (and a bad
  band gets a new named version per the falsifier below, never a formula).

## Decision

Adopt **(b) global tighter crop** as the Phase-3 re-render default, versioned
`chip_geom_v2_tight12`. Retain **(a) frozen 96 m** as `chip_geom_v1_banked96`,
the legacy/banked default (never re-run — its rows stay authoritative for
banked-rep comparability). **Reject (c) per-installation adaptive** for this
program:

- **Untested** — the pilot clamp formula
  (`build_gt_anchor_manifest.py:147-148`,
  `max(chip_min_half_m, w/2+margin, h/2+margin)` capped at `chip_max_half_m`)
  was never scored against install-date accuracy; only the fixed 12 m arm has
  evidence.
- **Per-anchor degree of freedom fragments stratification** — the decoder's
  3-symbol confusion matrix is estimated conditioned on render geometry ×
  achieved zoom (D2/D17). One geometry per re-render cohort gives one
  emission matrix; a per-anchor geometry gives every anchor its own render
  resolution and destroys the stratified EM.
- **Cache comparability** — a single named geometry version yields one clean
  cache generation; per-anchor sizing yields as many crop hashes as there are
  anchors, with no shared baseline to diff against.

**Honesty note (decision audit F3):** the footprint-containment guard shipped
with (b) *is* bounded per-anchor variation — for the ~12% of targets whose
footprint exceeds 12 m, the crop window is footprint-sized, with the same
`max(floor, w, h)` shape as the rejected pilot clamp. The distinction that
keeps (b) coherent: under (b) the guard is a **containment floor on one named
version** — deterministic from the manifest, inactive for ~88% of anchors,
and yielding a *two-stratum* emission conditioning (fixed 12 m vs
guard-sized) that the decoder can stratify on; under (c) per-anchor sizing is
the **primary mechanism for every anchor**, a continuous degree of freedom
with no shared baseline stratum at all. The guard-sized band shares the
untested-geometry caveat above and is covered by the same QA spot-check and
falsifier.

Adaptive is deferred, not deleted: if a future size-stratified experiment
shows the fixed 12 m arm clips or over-crops a size band, the follow-up is a
**second named fixed version** (e.g. `chip_geom_v3_tight_scaled`), never a
silent per-anchor formula.

## Geometry version vocabulary (for the implementation agent)

All three params below are the render-crop params of
`ensure_single_target_review_png` (`gehi_common.py:547-636`); `chip_size_m`
comes from `chip_targets.csv` per target and is **96 m** for the production
manifest. The GEHI download extent (the 96 m chip stack) is **unchanged** in
every version — only the scoring-crop render differs.

| geometry_version | crop_context_multiplier | min_crop_size_m | min_output_px | effective render (constant 10 m radius) | role |
|---|--:|--:|--:|---|---|
| `chip_geom_v1_banked96` | 3.0 | 24.0 | 128 | 60 m context / ≥128 px, **all** targets | **legacy default** — the banked render; never re-run; rows stay authoritative |
| `chip_geom_v2_tight12` | 0.5 | 12.0 | 256 | 12 m context / 256 px, all targets **pre-guard** (~12% guard-widened) | **Phase-3 re-render default** — the ISSUE-04 tight-crop arm |

Notes for the wiring:
- These are exactly the three existing CLI flags (`--crop-context-multiplier`,
  `--min-crop-size-m`, `--min-output-px`) already plumbed through
  `score_target_sequence.py` and `fullstack_noscan_prep.py`; v2 is the same
  code path as v1 with different flag values (no code fork — the tight-crop
  rescore already proved this path).
- The four render params (`chip_size_m` + these three) are **already baked
  into the crop-cache filename token** (`target_crop_review_png_path`,
  `gehi_common.py:311-341`), so v1 and v2 write to distinct PNG filenames with
  zero collision against today's banked outputs.
- The registry maps the human-readable `geometry_version` string to this exact
  param triple, so the version name — not three loose flags — is what flows
  into provenance and into any re-render invocation.

## Cache / verdict-store consequence chain (precise)

This is the D18 "deliberate migration, not silent re-keying" guarantee,
traced end to end against the recon:

1. **Different render geometry → different crop bytes.**
   `ensure_single_target_review_png` produces a different PNG under v2 (tighter
   crop, 256 px upsample), written to a distinct cache filename (its path token
   bakes all four render params).
2. **The rendered crop is what gets hashed.** `scoring_provenance.py` hashes
   `chip_path` = `SequenceDatePick.chip_path` = the **rendered crop PNG**, not
   the source chip/GeoTIFF (module docstring is explicit; `_record` hashes
   `chip_path`). So `chip_sha256` changes under v2.
3. **`chip_sha256` is the only geometry-sensitive input to the verdict key.**
   `build_verdict_key(chip_sha256, scorer_name, model_id, api_format,
   prompt_config_hash, scoring_mode, extras)` has **no** geometry field; for
   window modes the key hashes `canonical_hash` of the ordered per-crop hashes.
   Geometry can reach the key **only** through `chip_sha256`.
4. **New keys; old rows survive, unreferenced.** v2 renders miss the v1 cache
   (different hash → different key), score fresh, and write new rows. The v1
   rows remain in the store, still valid for the banked reps that reference
   them. No v1 row is ever silently served to a v2 query, and no v2 query can
   accidentally reuse a v1 verdict.
5. **The migration is recorded, so it is auditable.** `geometry_version` is
   written into the provenance `context` blob (an unpromoted key → lands in
   `context` verbatim, **zero schema/RECORD_VERSION bump**), and named in this
   memo + the registry. A geometry change is therefore an explicit, dated,
   diffable event — never an accident of which builder ran.

**Hard constraint restated:** `geometry_version` is **recorded provenance,
not a verdict-key component**. It MUST NOT be added to
`build_verdict_key`'s signature, to the `extras` dict passed to it, or to
`_key_fields`. The verdict store re-keys through the content hash by design
(`verdict_store.py` docstring: "a re-rendered chip is simply a cache miss") —
adding geometry to the key would double-count it and break the store's own
correctness contract. A regression test should assert that `geometry_version`
changes the *rendered hash / key* only via `chip_sha256`, and is absent from
`key_fields` (the `test_verdict_key_changes_with_every_component` template
inverted).

## Decoder-emission-model implication (D2 / D17)

The decoder's 3-symbol (present/absent/abstain) emission matrix is estimated
conditioned on render geometry × achieved zoom (D2), keyed on **achieved**
zoom (D17). ISSUE-04's T04 case proves geometry moves the emission **bias**,
not just its variance — a coarse crop can be confidently wrong. Two direct
consequences the decoder build (**replan_v2 ISSUE-02**, the
changepoint-posterior decoder — not to be confused with dinov3 ISSUE-02, the
distillation training set, addressed in the next section) must honor:

- **`geometry_version` is part of the emission conditioning vocabulary.**
  Emissions estimated on `chip_geom_v2_tight12` renders must not be applied to
  `chip_geom_v1_banked96` verdicts, and vice versa. Mixing geometries in one
  EM pool re-introduces the exact confound the tight crop removed.
- **`gemini_failed` under v1 is censoring-with-cause, not a behavioral
  stratum.** Under v2 that stratum largely dissolves (flips/abstains → ~0), so
  the production failure sentinel must be read as "render too coarse," and the
  re-render is the intended fix — not a scorer quality flag to model as
  intrinsic scorer behavior.

## Distillation-label caveat (dinov3 ISSUE-02) — added 2026-07-04, decision audit F2

The named consumer of this default — dinov3 ISSUE-02 — builds its
distillation labels **"from work already done — no new LLM calls"**: teacher
verdicts harvested from the retained `scan_state.json` corpus, i.e. produced
on **v1 (coarse-geometry) renders**. Adopting `chip_geom_v2_tight12` for the
re-render changes the student's *input pixels*, not those harvested *labels*.
The result is cross-geometry supervision: v2 pixels paired with labels whose
v1 bias (T02: invisible small dark panels → UNDATED; T04: confidently-wrong
presence on a bare roof) survives this decision untouched, concentrated —
per the ISSUE-04 evidence — on exactly the small/hard units. By this memo's
own mixing rule (previous section), that confound must be handled, not
ignored. **Disposition assigned to dinov3 ISSUE-02** — choose explicitly, in
writing, one (or a combination) of:

1. **Accept-and-document** — argue the label-bias band is small enough for a
   match-not-beat fidelity target and record it as a known label-noise floor;
2. **Exclude or down-weight** strata where v1 labels are known-unreliable
   (`gemini_failed`-shaped units; this composes with the already-mandated
   27.6% `done_ambiguous_*` handling); or
3. **Re-score a calibration subset at v2 with the teacher** (bounded LLM
   spend) to measure the label-geometry disagreement distribution directly —
   this slots into the co-teacher dual-scoring instrument that ISSUE-04
   (dinov3) already plans.

Until one is chosen there, "the re-render fixes the resolution artifact" is a
claim about student inputs only — not about the training labels.

## What ISSUE-19 ships

- **A geometry registry module** mapping `geometry_version` string →
  `{crop_context_multiplier, min_crop_size_m, min_output_px}` (the two named
  versions above; extensible by adding a named entry, never by loose flags).
  Kept out of `scan_config.py`/`AdaptiveScanConfig` on purpose — those govern
  the scan/search path and are guarded by the D16 consumed-keys test; the
  render-crop params have always been CLI/function defaults, and a version
  registry follows that precedent (no dead-knob trap).
- **Renderer wiring** so the Phase-3 re-render selects a `geometry_version`
  (default `chip_geom_v2_tight12`) that resolves to the three existing crop
  params — plus the **footprint-containment guard** (crop window ≥ install
  footprint from `source_width_m`/`source_height_m`) so a small `radius`
  cannot clip a large install.
- **A provenance field**: `geometry_version` written into the scoring-provenance
  `context` blob (no `SCORING_PROVENANCE_FIELDS` change, no RECORD_VERSION
  bump). Explicitly not a verdict-key component.
- **Docs**: this memo + a version-vocabulary note; the ISSUE-19 acceptance
  boxes checked; ISSUE-02's "chip geometry recorded as explicit render
  parameter" box satisfied by consuming the registry.

## What ISSUE-19 does NOT ship

- **No re-render run.** ISSUE-19 ships the *policy + wiring + version*; the
  actual stratified ~500–1000-anchor re-render is
  [ISSUE-02 (dinov3)](../dinov3_scorer/ISSUE-02-distillation-training-set.md),
  which consumes this registry and default.
- **No re-keying of banked data.** `chip_geom_v1_banked96` rows are frozen and
  authoritative; nothing in this issue touches, migrates, or invalidates them.
- **No change to the legacy builder** (`build_inventory_chip_groups.py`, 96 m)
  and **no change to the GEHI download** (96 m chip stacks). D18 preserved.
- **No adaptive per-anchor sizing** (deferred to a possible future named fixed
  version, never a silent per-anchor formula).

## Implemented as (2026-07-04)

- Registry: `scripts/temporal/chip_geometry.py` — `ChipGeometry` (frozen),
  `resolve_chip_geometry()`, `LEGACY_GEOMETRY_VERSION` /
  `RERENDER_GEOMETRY_VERSION`, and the footprint-containment guard helper
  `contain_crop_to_footprint()`.
- Wiring: `scripts/temporal/score_target_sequence.py` — `--chip-geometry
  <version>` flag + `resolve_render_geometry()` (no-conflict rule with the raw
  crop flags; rejected in `--review-png-manifest` reuse mode, where no render
  runs so a caller-typed version must not be stamped onto provenance for pixels
  it did not produce; legacy omission is byte-identical); `geometry_version`
  threaded into `score_target_sequences()` and written to the scoring-provenance
  `context` blob.
- Tests: `tests/temporal/test_chip_geometry.py` (frozen param pins + the
  "not a verdict-key component" regression) and the ISSUE-19 block in
  `tests/temporal/test_score_target_sequence.py`.

## Decision audit (2026-07-04)

An independent adversarial audit of this memo (five axes: evidence fidelity,
inference strength, alternatives, internal consistency, consequence honesty)
returned **sound-with-caveats**: the decision's direction survives all five
axes; three material findings concerned the written justification and were
corrected in place the same day:

- **F1** — the original mechanism defense assumed footprint-scaled `radius`;
  in fact `search_radius_m` is a constant 10.0 across all 41,393 manifest
  rows (verified: p50 footprint 6.2 m, 12.0% > 12 m, 3.1% > 24 m, p95
  19.4 m, max ≈ 138 m). Replaced with the population-coverage argument +
  guard-band residual risk.
- **F2** — the teacher-label (v1 renders) vs student-chip (v2 renders)
  geometry mismatch was unstated. Added the "Distillation-label caveat"
  section with an explicit disposition assigned to dinov3 ISSUE-02.
- **F3** — the containment guard is itself bounded per-anchor variation,
  in tension with the (c) rejection. Added the honesty note distinguishing a
  containment floor on one named version from per-anchor sizing as a primary
  mechanism.

Minor fixes: disambiguated replan_v2 ISSUE-02 (decoder) from dinov3 ISSUE-02
(training set); corrected a stratum characterization transplanted from
`done_appears`. Evidence fidelity passed clean — every cited number traces to
the corrected ISSUE-04 memo.
