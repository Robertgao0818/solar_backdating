# Data memo: GEHI cross-vintage displacement + tight12 contamination audit (2026-07-06)

Status: P0 audit per `/tmp/handoff_gehi_displacement_audit_2026-07-06.md`
(blocks Phase-3 distillation re-render, whose default `chip_geom_v2_tight12`
has near-zero tolerance for metre-scale misregistration — PRD D18, ISSUE-19).
Provenance: `scripts/temporal/chip_displacement.py` (pure estimator, 24 unit
tests) + `scripts/temporal/audit_gehi_displacement.py` (batch run,
`~/zasolar_data/geid_temporal/gehi_displacement_audit_2026-07-06/run.log`,
`EXIT=0`) + `scripts/temporal/summarize_gehi_displacement.py`. All numbers
below are read directly off `per_chipdate_offsets.csv` (15,501 rows) /
`gehi_vs_vexcel_bias.csv` (753 rows) / `contamination_summary.csv` in that
directory — re-run `summarize_gehi_displacement.py` to reproduce.

## 0. Why this exists

Every GEHI vintage chip for one anchor shares the identical nominal
EPSG:4326 tile-snapped bbox (verified 2026-07-06 by inspecting a 143-frame
GEHI stack: bounds constant across 2009–2017 and 3 zoom levels). Real
cross-vintage misregistration is therefore invisible to the georeferencing
and lives entirely in pixel content. The architecture doc asked for
`best_offset_m`/`alignment_score` back in Phase 0
(`docs/geid_temporal_anchor_presence_architecture.md` §4, "Keep an
`alignment_score`/`best_offset_m` so large shifts can be flagged") but no
displacement-measuring code existed anywhere in the repo before this audit
(verified by full-repo grep, 2026-07-06).

## 1. Corpus (deviation from the original handoff sampling plan, flagged)

The handoff proposed stratified sampling over 15,859 production anchors ×
172,913 chip-dates. That raw scan corpus
(`jhb_full382_fpcut_scan_2026-06-02/chips/`) has since been cleaned off
disk — only downstream frozen subsets remain. Round-1 instead used the
**full available zero-download corpus**:

- **788 anchors** from `dinov3_distill_20260705/chips/` — this IS the
  Phase-3 tight12 re-render's actual decision set, so it is more directly
  decision-relevant than a blind stratified sample would have been.
- **4 anchors** from `panel_repair_20260703/chips_frozen/` — dense stacks
  (68–143 vintages each) used only for the internal-consistency check (§6).
- 35/792 anchors (4.4%) had zero readable GEHI tif on disk and were
  skipped (`no_stack` in `drops.txt`).

This is a decision-set audit, not a probability sample of all 15,859
production anchors — treat area/zoom/year breakdowns as descriptive of the
distill corpus, not as population estimates. A round-2 stratified sample
over the full production set would require re-downloading GEHI (ToS grey
area, slow) and is not done here.

## 2. Method (three reference signals, see `chip_displacement.py`)

All chips are reprojected onto a shared per-anchor UTM grid (metric CRS via
`core.grid_utils.get_metric_crs`, never hardcoded) at 0.15 m/px, then
registered by phase correlation on Sobel-gradient, Hann-windowed images
(gain/exposure-invariant). Displacement in pixels → true ground metres via
the grid's fixed GSD.

- **S1 (GEHI-latest)**: each vintage vs. the same stack's latest/highest-zoom
  frame — same-sensor intra-stack wobble, zero-download.
- **S3 (Vexcel, PRIMARY / decision signal)**: each vintage vs. a Vexcel 2024
  crop fetched **per-anchor** over the identical bbox via the Vexcel
  `/ortho/extract` endpoint (collection `za-gp-johannesburg-2024`).
  JHB's full-382-grid production census ran on `vexcel_2024`
  (`unified_reviewall_A_..._vexcel_2024_full382`, `configs/datasets/regions.yaml`),
  so the anchor centre a tight12 crop is drawn around **is** the Vexcel
  position — this is the one signal that measures the exact quantity the
  re-render decision needs. 792/792 anchor crops fetched successfully
  (0 failures; endpoint works outside the 25 locally-registered CBD grids).
- **S2 (CoJ true-date)**: each vintage vs. the CoJ 0.15 m municipal ortho of
  the nearest true-capture year (2015/2019/2023) — independent,
  license-clean cross-check, mainly useful for old vintages far from any
  Vexcel/GEHI epoch.

### Reliability gate: PSR, not NCC (mid-course correction, evidence-graded)

The first estimator design used post-alignment gradient NCC as the trust
score, calibrated only on synthetic data. On real GEHI pairs it failed
outright: genuinely well-locked real pairs scored NCC 0.10–0.32 (would be
rejected by any sane absolute-NCC threshold) because cross-vintage/cross-sensor
**appearance** differences suppress absolute correlation even at a correct
lock — confirmed by inspecting one anchor's full stack by hand (raw
disp/NCC printed 2026-07-06). Switched to **phase-correlation peak-to-sidelobe
ratio (PSR)**, which is content-robust: measured on real data, the noise floor
(chip vs white noise / vs shuffled-self, 651×651 grid) sits at PSR≈7, genuine
same-anchor locks span PSR 8.5–26.3, a synthetic perfect self-roll scores
PSR≈4900. Gate set to **PSR ≥ 8.0** (clears the floor, admits the weakest
real locks); the audit additionally reports contamination at PSR cuts
{8, 10, 12} so the conclusion's sensitivity to the gate is visible rather
than hidden behind one threshold choice.

Drop accounting (no silent caps): of rows attempted, S3 kept 7,804 /
dropped 1,022 low-PSR + 74 max-offset-guard (12.3% dropped); S1 kept 7,164 /
dropped 875 + 104 (12.0%); S2 kept 533 / dropped 57 (9.7%). Full counts in
`drops.txt`.

## 3. Finding 1 — absolute offset (S3, the decision-relevant signal)

| signal | n | p50 | p75 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| S1 (GEHI-latest) | 7,164 | 1.32 m | 2.30 m | 6.53 m | 11.47 m | 20.27 m | 26.57 m |
| S2 (CoJ) | 533 | 1.48 m | 3.42 m | 7.45 m | 11.86 m | 18.42 m | 24.58 m |
| **S3 (Vexcel)** | **7,804** | **0.96 m** | 1.68 m | 3.81 m | 7.34 m | 16.74 m | 26.57 m |

Median absolute misregistration against the exact census/crop frame is
**under 1 m**, but the tail is real: p95 = 7.3 m, p99 = 16.7 m. This
tail is what a fixed 12 m crop is exposed to.

By zoom (S3): p50 rises from 0.93 m (z20) → 1.18 m (z19) → 1.40 m (z18);
p95 rises 5.85 m → 11.62 m → 16.51 m. Lower zoom is both blurrier and more
displaced — consistent with, not independent of, the visibility-floor
finding in `DATA-visibility-gsd-area-2026-07-06.md`.

By year (S3, small-n years flagged): post-2021 vintages are tight
(p50 0.76–0.99 m, p95 2.3–7.3 m across 2021/2022/2024/2025, n=777–1587
each); pre-2018 vintages are both noisier and thin (p50 1.3–3.3 m but
p95 6.9–21.8 m on n=12–116 each) — wide-CI, descriptive only, not a
load-bearing claim.

**By install size — held-zoom check (not confounded):** zoom mix is
similar across area buckets (75–82% z20 in every bucket), so this is a real
effect, not a low-zoom-old-frame artifact:

| area bucket (footprint m²) | z20 p50 | z20 p90 |
|---|---:|---:|
| a_xs (<15) | 0.86 m | 2.24 m |
| b_sm (15–40) | 0.92 m | 2.77 m |
| c_md (40–100) | 1.59 m | 6.91 m |
| d_lg (≥100) | 2.18 m | 11.74 m |

Larger installations show ~2.5× the median offset of small ones at the
same zoom. Plausible mechanisms (not adjudicated here): larger/taller
rooftops carry more relief displacement in orthorectification, and a
larger scene may let phase correlation lock onto a nearby dominant feature
(ridge line, neighbouring roof) rather than the panel itself. Flagged as an
open question, not resolved by this audit.

## 4. Finding 2 — tight12 vs banked96 contamination (the decision question)

Contamination reported as **retained footprint fraction** (directional:
`footprint_retained_fraction`, not a binary exit flag — a binary flag reads
"any offset clips" once the crop is floored to a large footprint, which is
not decision-useful).

| PSR cut | n | tight12 retain mean | tight12 clip>25% | tight12 lose>50% | tight12 full loss | banked96 lose>50% |
|---:|---:|---:|---:|---:|---:|---:|
| ≥8 (default) | 7,804 | 93.4% | 7.6% | 5.3% | 2.8% | 0.0% |
| ≥10 | 6,320 | 96.3% | 4.0% | 2.5% | 1.3% | 0.0% |
| ≥12 (strict) | 5,019 | 97.4% | 2.9% | 1.6% | 0.7% | 0.0% |

**banked96 is essentially immune** (retain mean 0.9998–1.0, 0% lose>50% at
every PSR cut) — the 96 m chip's margin absorbs everything measured here.
No presence-covariate/offset-flag action needed on the banked96 channel
from this audit.

**tight12 contamination is real, non-trivial, and concentrated in
medium/large installs** — at the default PSR≥8 gate:

| area bucket | n | tight12 retain mean | clip>25% | lose>50% | full loss |
|---|---:|---:|---:|---:|---:|
| a_xs (<15 m²) | 3,920 | 95.6% | 5.1% | 4.0% | 3.1% |
| b_sm (15–40 m²) | 2,987 | 94.2% | 6.6% | 4.5% | 2.1% |
| c_md (40–100 m²) | 538 | 81.8% | 21.6% | 13.2% | 4.3% |
| d_lg (≥100 m²) | 359 | 80.5% | 21.5% | 14.8% | 4.2% |

For medium/large installs, roughly **1 in 7–8 frames loses over half the
panel footprint** out of the tight12 crop, and ~4% lose the whole thing —
stable in direction (though shrinking in magnitude) across PSR cuts 8→12,
so this is not an artifact of the reliability gate choice.

## 5. Finding 3 — constant per-anchor GEHI-vs-Vexcel bias

This is the term a GEHI-latest-only audit (S1 alone) structurally cannot
see: even with zero cross-vintage wobble, the whole GEHI stack could sit
offset from the Vexcel-anchored crop centre by a fixed amount.

n = 753 anchors with a usable present-day-GEHI-vs-Vexcel lock:
p50 = 0.77 m, p90 = 1.66 m, p95 = 2.66 m, max = 19.5 m.
25.4% of anchors carry >1 m constant bias, 7.6% >2 m, 4.0% >3 m, 1.2% >5 m.

## 6. Finding 4 — internal consistency (vector decomposition, not magnitude medians)

Three independently-measured signals must satisfy, per (anchor, vintage),
the vector identity `S3_disp ≈ S1_disp + bias_disp` (content(vint) =
content(ref) + S1_disp = content(Vexcel) + bias_disp + S1_disp, and
independently content(vint) = content(Vexcel) + S3_disp). Comparing
*medians of magnitudes* across signals is not a valid check (magnitudes
don't add; a first pass doing exactly that produced a false-looking
"contradiction" — median S1 1.32 m vs. median S3 0.96 m — that dissolved
once redone as vectors).

Checked per-(anchor, vintage) vectorially on n = 6,624 triples with all
three legs present: residual `|predicted_S3 − measured_S3|` p50 = 0.45 m,
p75 = 1.14 m, p90 = 4.82 m — small relative to the S3 signal itself
(p50 = 0.99 m, p90 = 3.81 m); 72.2% of triples close within 1 m, 83.8%
within 2 m. The three independently-fetched/measured signals are
internally consistent, not contradictory — this is a genuine validation of
the estimator pipeline, not just a sanity gate.

## 7. Finding 5 — independent-reference agreement (S2 CoJ vs S3 Vexcel)

On 486 (chip, capture_date) pairs where both a CoJ true-date lock and a
Vexcel lock exist for the same GEHI frame: vector difference between the
two offset estimates has p50 = 1.39 m, p90 = 6.05 m. Two independently
sourced absolute references (a 2015/2019/2023 municipal ortho vs. a 2024
commercial ortho) agree to ~1.4 m typical — consistent with genuine
registration measurement rather than noise, given each individual estimate
already carries sub-metre-to-metre uncertainty (§6).

## 8. Decision (per handoff §"决策规则")

- **banked96**: contamination is negligible (0% lose>50% at every PSR cut).
  No re-centering, no covariate/flag needed on this channel from this
  audit's evidence.
- **tight12**: contamination is NOT uniformly benign. It is small for
  small installs (a_xs/b_sm: ~2–4% full loss) but material for medium/large
  installs (c_md/d_lg: ~13–15% lose over half the footprint, ~4% full
  loss) — and larger installs plausibly carry more economic weight (kW) in
  the aggregate inventory this pipeline ultimately serves. Per the
  `chip_geometry.py` registry's own extension policy ("a future
  size-stratified follow-up is a second named fixed version, e.g.
  `chip_geom_v3_tight_scaled`, never a silent per-anchor formula"): **before
  the Phase-3 re-render runs on medium/large installs, either (a) register
  a size-stratified geometry version that widens the crop for c_md/d_lg
  footprints, or (b) add per-vintage local re-centering as a new named
  geometry version.** Either must be a new registered `geometry_version`
  per D18/ISSUE-19 — never a hotfix to cached tight12 rows or the frozen
  96 m builder.
- The `best_offset_m`/`alignment_score` fields the architecture doc asked
  for (§4.4) are now implemented as `per_chipdate_offsets.csv`, keyed
  `chip_id + capture_date + ref_kind` — joinable to any manifest without
  touching the frozen builder (leaves the in-flight `source_fid` /
  `source_feature_id` edit on `build_inventory_chip_groups.py`,
  a different concurrent thread, untouched).

## 9. Caveats

- Decision-set corpus (§1), not a stratified sample of all 15,859
  production anchors — a round-2 population estimate would require
  GEHI re-download.
- The area-bucket effect (§3) and its mechanism are observational, not
  adjudicated; worth a follow-up if size-stratified geometry work proceeds.
- PSR≥8 is a floor-clearing choice, not a precision-optimal one; §4's
  contamination direction is stable across PSR∈{8,10,12} but magnitude
  shrinks with a stricter gate — treat the PSR≥8 numbers as an upper bound
  on measured contamination, not a single ground truth.
- Vexcel crops were fetched at a fixed 96 m bbox per anchor purely for this
  audit (not persisted as production tiles); no imagery is redistributed,
  only derived CSV statistics (provider-legality constraint, handoff §约束).

## 10. Artifacts

- `scripts/temporal/chip_displacement.py` — pure estimator (PSR gate,
  px→m, `footprint_retained_fraction`, `crop_size_for_geometry`), 24 unit
  tests in `tests/temporal/test_chip_displacement.py`.
- `scripts/temporal/audit_gehi_displacement.py` — batch runner (S1/S2/S3,
  Vexcel fetch cached+threaded).
- `scripts/temporal/summarize_gehi_displacement.py` — reproduces all
  numbers in this memo from the audit's CSV outputs.
- Data: `~/zasolar_data/geid_temporal/gehi_displacement_audit_2026-07-06/`
  (`per_chipdate_offsets.csv`, `gehi_vs_vexcel_bias.csv`,
  `contamination_summary.csv`, `offset_quantiles_by_stratum.csv`,
  `drops.txt`, `run.log`).
