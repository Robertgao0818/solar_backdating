# DATA — RUN3-native repeat-ceiling re-derivation (2026-07-19)

Status: **COMPLETE 2026-07-19** (panel frozen 04:52Z, 3 reps scored
05:45Z–05:56Z, all reconciled clean, ceiling computed 05:56:33Z). Owner-approved
quota (2026-07-19, small-scale, half-hour class, "thousands to 14,000 obs");
PRD ref: [`PRD-run3-native-local-line-2026-07-19.md`](PRD-run3-native-local-line-2026-07-19.md)
§7 ("独立 Gemini repeat ceiling (RUN 3-native 重导)").

**Headline: new RUN3-native teacher ceiling = 0.7708 (inv-weighted, spread
min/max/mean/std = 0.7599/0.7765/0.7708/0.0077, S=max−min=0.0166), vs the old
0.7724 (S=0.0116). The two are close (Δ=0.0016) but NOT the same corpus/geometry
— see §5.4 for why this is closer than it might look and where it differs
materially (the `<40 m²` layer).**

Task owner: repeat-ceiling teammate (this session). Files: this memo,
[`scripts/temporal/run3_repeat_ceiling.py`](../../scripts/temporal/run3_repeat_ceiling.py),
`~/zasolar_data/geid_temporal/run3_native_line_2026-07/repeat_ceiling_v1/`.

## 1. Why this exists

TRACKER slice 6 / [`ISSUE-06-gate-verdict-2026-07-06.md`](ISSUE-06-gate-verdict-2026-07-06.md)
computed a **teacher rep↔rep interval-agreement ceiling of 0.7724** (spread
`S=0.0116`) on the *old* distillation corpus (23,147-anchor / D12-amended
pool, pre-geometry-fix, 642-row reference cohort, banked rep1–3). RUN 3
(2026-07-18) rebuilt the anchor geometry from scratch (ISSUE-27 `anchors_v2`)
and produced a materially different, larger, cleaner corpus — the R0
manifest (311,195 obs / 41,393 anchors, frozen
`~/zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1/MANIFEST_LOCK.json`).
The PRD explicitly retires 0.7724 as this line's ceiling ("不再沿用 RUN 2
时代的 0.7724 作为本线 ceiling") and requires a fresh rep1–3 ceiling computed
**on RUN3-native data, same-named statistic**. This memo is that re-derivation.

## 2. Statistic definition — locked BEFORE computing (no post-hoc caliber choice)

Exact reuse of `scripts/validation/fidelity_gate.py`'s `pairwise_ceiling` /
`agg` / `posterior_to_agree_key`, confirmed against
[`ISSUE-06-gate-verdict-2026-07-06.md`](ISSUE-06-gate-verdict-2026-07-06.md)
§"Teacher rep↔rep ceiling" (0.7724 table, lines 141–150):

1. **Unit** = one anchor (one `(anchor_id)` install-interval decode), not one
   frame.
2. **Decode**: every scored frame for that anchor+rep is converted to a
   `VintageObservation` (`pv_present ∈ {'1','0',''}`, `confidence`,
   `quality_flag`) and run through the **ADOPTED Phase-0 decoder**
   (`changepoint` estimator, EB/Turnbull cohort prior, `decoder_epoch_gap_days=45`,
   fitted 3-symbol emissions — `scripts/validation/fidelity_gate.py::build_decoder()`,
   unchanged, reused verbatim) with `ClampContext(ceiling_date=vexcel_ceiling[grid_id])`
   (same per-grid Vexcel present-clamp CSV as the original gate).
3. **Key**: `posterior_to_agree_key(posterior)` → canonical
   `UNDATED` / `AP_BOUND<=|{end}` / `INTERVAL|{start}|{end}` string (reused
   verbatim, unmodified).
4. **Pairwise agreement**: for each of the 3 rep pairs (1–2, 1–3, 2–3), on the
   **decoded intersection** (anchors both reps produced a key for — in
   practice the full panel, since every panel anchor is scored end-to-end in
   every rep and reconciled before decode), `agree = 1[key_i == key_j]`.
5. **Inventory weighting**: `agg()`'s stratum-weighted rate — per-stratum
   agreement rate weighted by a fixed **inventory weight**, weights
   normalized to sum 1, formula reused byte-for-byte from
   `scripts/validation/fidelity_gate.py::agg()`.
6. **Ceiling** = mean of the 3 pairwise inventory-weighted rates. **Spread**
   `S` = max − min of the 3 pairs (old convention) — **this memo additionally
   reports std across the 3 pairs** (team-lead's ask), registered as an
   addition, not a substitution.
7. Reported alongside (never headline): **dated-only** denominator
   (restrict to pairs where both sides decode non-`UNDATED`), and
   **stratified** by chip_arm×area_bin with the `<40 m²` layer (A24 both
   area bins) co-headline per PRD §7.

### Registered deviation (the one required substitution)

The old statistic's inventory weight came from `reference.csv`'s
`status_stratum` → `inv_weight` column — a population-representativeness
weight from a **different pipeline** (the 642-target census-stratified
reference cohort used by `llm_endtoend_*`/D8). That reference.csv and its
`status_stratum` concept **do not exist for the RUN3-native manifest** — R0's
population stratification is `chip_arm × area_bin` (4 strata: `A24|[0,15)`,
`A24|[15,40)`, `A48|[40,100)`, `A48|[100,inf)`), with weights recorded as
`corpus_share` in
`r0_manifest_v1/MANIFEST_LOCK.json:split.stratum_balance.corpus_share`
(share of the full 311,195-obs corpus each stratum occupies). **This memo
substitutes `corpus_share` as the inventory weight**, keyed on the same 4
strata — the population-share analog on this corpus, computed the same way
`agg()` expects (a `stratum -> weight` dict). This is the only caliber
substitution; everything else (decoder, clamp, agree-key, pairing, spread
arithmetic) is byte-identical reuse.

## 3. Panel design (frozen; see PANEL_LOCK.json for exact numbers)

- **Source**: R0 **test split** only (5,403 anchors / 40,329 obs per
  `splits.parquet`), never train/calibration.
- **Sampling unit**: whole anchors (every scored frame for a sampled anchor
  is included — ceiling is interval-level, a partial frame set would corrupt
  decode).
- **Stratification**: `chip_arm|area_bin`, same 4 strata as MANIFEST_LOCK's
  `corpus_share`. Target anchor counts locked pre-sampling:
  `A24|[0,15)=278`, `A24|[15,40)=183`, `A48|[40,100)=62`, `A48|[100,inf)=53`
  (576 anchors total) — chosen so the `<40 m²` layer (A24 both bins, 461/576
  anchors ≈ 79% of panel obs) is unambiguously co-headline-sized, while the
  two smaller A48 strata still carry ≥53 anchors (~400+ obs) each for a
  standalone diagnostic read, not just a corpus_share-proportional trickle.
- **Seed**: `2026071901` (numpy `Generator.permutation` per stratum, sorted
  anchor-id pool before permutation for reproducibility).
- **Chip-availability gate**: every one of the 40,329 test-split obs was
  checked for `chip_png_path` existing on disk **before** sampling (0
  missing) — the panel draws only from the fully-available pool, so no
  anchor needed to be dropped/resampled after freeze.
- **Realized size**: 576 anchors, **4,276 obs/rep**, **12,828 obs for 3
  reps**, quota hard cap 14,000 → **1,172 obs (8.4%) retry reserve**.
- **Frozen at**: `~/zasolar_data/geid_temporal/run3_native_line_2026-07/repeat_ceiling_v1/PANEL_LOCK.json`
  (+ `panel_obs.parquet` / `panel_anchors.csv`, sha256-pinned in the lock).
  The panel was **not** reopened after freeze (`build-panel --force` never
  invoked post-freeze).
- **Model pin**: all 3 reps score with `gemini-3.1-flash-lite` (the exact
  RUN3 model, read from the panel's own `model_id` column and used to
  override the `.env.gemini.local` default rather than trust the env
  default — the env default resolved to a different model, `gemini-3-flash`,
  which would have silently changed the instrument being repeated).
- **Independence**: each rep is a fresh `score_batch_with_fallback` call
  (no response cache exists anywhere in the reused Gemini scoring path —
  confirmed by reading `gemini_solar_image_review.py`), with a per-rep
  routing salt (`gemini-3.1-flash-lite:{anchor_id}:repeatceiling_rep{N}_c{chunk_start}`)
  so reps are not pinned to the same gateway account. `GENERATION_TEMPERATURE
  = 0` in the shared scorer (unchanged, not something this task can or
  should alter) — this is the SAME setting the original 0.7724 ceiling was
  computed under, so whatever cross-call variance exists (serving
  nondeterminism, image-preprocessing jitter) is apples-to-apples with the
  old statistic, not a new source of bias.
- **T0 dual-use reservation**: every rep's `scoring_provenance_rep{N}.jsonl`
  keeps full raw per-frame verdict + confidence + quality_flag +
  `decision_source` (incl. `gemini_failed` rows, never dropped). **No T0
  paired-ablation analysis is performed here** — T0 itself is not yet
  owner-approved (`DESIGN-phase0-emission-extension-2026-07-19` §5.3 is
  still DRAFT). This is data retention only, so a future approved T0 run
  can reuse these 3 reps at zero new quota.

## 4. Execution

Wrapper: `scripts/temporal/run3_repeat_ceiling.py` (subcommands `build-panel`
/ `run-rep` / `reconcile` / `compute-ceiling`). Reuses, unmodified:
`scripts.temporal.run_adaptive_scan._load_gemini_config` /
`_routing_salt` / `_default_gemini_env`,
`scripts.validation.gemini_solar_image_review.{BatchPick,RateLimiter,score_batch_with_fallback}`,
`scripts.validation.fidelity_gate.{build_decoder,posterior_to_agree_key}`,
`scripts.validation.estimator_endtoend_decode.{DEFAULT_VEXCEL_CSV,_load_vexcel_ceiling}`.
No shared file was edited.

Run mode: `tmux new -d -s ceiling_reps <chain script>` — sequential
`run-rep 1 → reconcile 1 → run-rep 2 → reconcile 2 → run-rep 3 → reconcile 3
→ compute-ceiling`, `anchor-workers=20 qps=6`, resumable via per-anchor
checkpoint files (`rep{N}_checkpoint.json`) so a crash/restart re-scores only
the anchors not yet marked done for that rep (idempotent — `run-rep` is safe
to re-invoke). `reconcile` hard-fails (nonzero exit, stopping the chain) on
any mismatch between `PANEL_LOCK.json` expected counts and the actual
deduped provenance row/anchor counts, so the chain cannot silently proceed
past a partial rep.

Pre-flight validation (to protect the quota — the wrapper is new code, not
the shared production script): a 1-anchor / 6-obs live smoke test was run
first (`run-rep --rep 1 --limit-anchors 1`) and inspected row-by-row before
committing to the full panel; it produced clean `gemini_batch` provenance
with plausible verdicts. This anchor is **not wasted quota** — it is
anchor 1 of the real rep-1 checkpoint, counted in the totals below.

## 5. Results

Machine-readable: `~/zasolar_data/geid_temporal/run3_native_line_2026-07/repeat_ceiling_v1/CEILING_RESULT.json`
(+ `rep{1,2,3}_reconcile.json`).

### 5.1 Per-rep completion (actual vs expected)

| rep | expected obs | actual obs (deduped) | gemini_failed | anchors done | wall time | reconcile passed |
|---|--:|--:|--:|--:|--:|---|
| 1 | 4,276 | 4,276 | 0 | 576/576 | ~204s (incl. 6-obs smoke) | ✅ |
| 2 | 4,276 | 4,276 | 0 | 576/576 | ~200s | ✅ |
| 3 | 4,276 | 4,276 | 0 | 576/576 | ~198s | ✅ |

Total obs consumed: **12,828 / 14,000 cap (91.6%)**, zero retries needed (0
`gemini_failed` across all 3×4,276 = 12,828 scoring calls), 0 duplicate rows,
0 anchor-level errors. The 1,172-obs (8.4%) retry reserve was **not touched**.

### 5.2 Ceiling (overall, inventory-weighted, all-units — corpus_share weighted)

| pair | n_intersection | inv-weighted | unweighted | dated-only inv-weighted |
|---|--:|--:|--:|--:|
| 1–2 | 576 | 0.7599 | 0.7691 | 0.7599 |
| 1–3 | 576 | 0.7759 | 0.7847 | 0.7759 |
| 2–3 | 576 | 0.7765 | 0.7847 | 0.7765 |

- **New ceiling (mean, inv-weighted) = 0.7708** (vs old **0.7724**, Δ = −0.0016)
- **Spread**: min=0.7599, max=0.7765, mean=0.7708, **std=0.0077** (`S`=max−min
  = **0.0166**, vs old `S=0.0116`)
- `n_intersection` = 576 for every pair (= full panel) — every panel anchor
  decoded to a key in all 3 reps, so the decoded intersection is the whole
  panel, unlike the old ceiling's ~222–233-of-628 intersection (a real
  consequence of RUN3's geometry fix + this panel's design: every sampled
  anchor's full frame set was scored fresh in every rep, nothing was
  missing-from-delivery by construction).
- `dated-only` is **identical** to the all-units number for every pair —
  100% of decoded intervals were non-`UNDATED` in this panel (every anchor
  has a known `census_date` and enough frames to resolve an interval).
  This is a genuine finding, not a bug: the R0 test-split panel apparently
  contains no truly-undated anchors under this decoder/clamp combination.

### 5.3 Stratified (pooled mean rate across the 3 pairs; n = pair-level intersection, constant per stratum)

| stratum | n | pooled inv-weighted-input rate (mean of 3 pairs) |
|---|--:|--:|
| **`<40 m²` (A24 `[0,15)`+`[15,40)`, co-headline)** | **461** | **see §5.3.1 below (combined ceiling, not a simple mean-of-means)** |
| `A24\|[0,15)` | 278 | 0.7002 (0.6871 / 0.6978 / 0.7158) |
| `A24\|[15,40)` | 183 | 0.8288 (0.8197 / 0.8415 / 0.8251) |
| `A48\|[40,100)` | 62 | 0.8817 (0.8710 / 0.9194 / 0.8548) |
| `A48\|[100,inf)` | 53 | 0.9057 (0.9057 / 0.8868 / 0.9245) |

Per-stratum rates in the raw (unweighted-within-stratum) sense above are the
`by_stratum` rates `agg()` averages together with `corpus_share` weights to
produce the headline number — note the clear gradient: the smallest-area
stratum (`A24|[0,15)`, 51.4% of corpus) is both the hardest (lowest rate,
~0.70) and the largest weight, which is why it pulls the headline (0.7708)
and especially the `<40 m²` combined ceiling below the two A48 strata
(0.88–0.91).

#### 5.3.1 `<40 m²` combined ceiling (co-headline per PRD §7, own `corpus_share`-renormalized weight over just the 2 A24 strata)

| pair | inv-weighted (under-40m² renormalized) |
|---|--:|
| 1–2 | 0.7414 |
| 1–3 | 0.7568 |
| 2–3 | 0.7606 |

- **`<40 m²` ceiling (mean) = 0.7529**, spread min=0.7414 / max=0.7606
  (`S`=0.0192)
- This does **not** sit below the teacher's own overall self-consistency
  (0.7529 < 0.7708 overall, as expected — the PRD flags this stratum as the
  hard one) but it is **substantially higher** than the number the PRD
  itself cites as the prior expectation for this stratum: *"该层占 corpus
  87.7%，teacher 自一致性仅 0.65–0.67"* (PRD §4.3). This repeat-ceiling run
  puts RUN3-native `<40 m²` teacher self-consistency at **~0.75**, roughly
  **8–10 pp above** that cited 0.65–0.67 range. **Provenance of the old
  number and the source of this gap are investigated in §5.5 below**
  (team-lead-assigned follow-up, 2026-07-19) — short answer: same statistic
  (interval-level `agree_key` rep↔rep self-consistency, unweighted
  per-bucket), different corpus/geometry (pre-ISSUE-27 banked96 vs
  RUN3-native), and the gap is concentrated almost entirely in the
  15–40 m² sub-bucket, consistent with a real geometry-fix effect rather
  than a caliber artifact.

### 5.5 Provenance of the old "0.65–0.67" number (investigated 2026-07-19, team-lead-assigned)

**Task**: identify the first-hand source of the `<40 m²` teacher
self-consistency figure the PRD cites (§4.3: *"该层占 corpus 87.7%，teacher
自一致性仅 0.65–0.67"*) and account for the ~8–10 pp gap to this run's 0.7529.
Owner's decision is already made and unaffected by this section — RUN3-native
0.7529 is operative per PRD §7 ("不再沿用 RUN 2 时代的 0.7724 作为本线
ceiling"); this is purely a causal write-up, read-only except for this memo.

**First-hand source, traced and confirmed**:
[`DATA-gate2-area-stratified-2026-07-10.md`](DATA-gate2-area-stratified-2026-07-10.md)
§"Three discriminating signals" item 1 — *"Gemini's own rep↔rep
self-consistency falls from 0.82 (≥100 m²) to **0.65–0.67 (<40 m²)**"* — is
the origin; every other citation (PRD §4.3, `TRACKER.md`,
`DATA-student-revival-paths-2026-07-10.md` §"Two honest caveats") quotes this
same memo verbatim, not an independent measurement. Harness:
`scripts/validation/diagnose_gate2_area_strata.py`
(`per_bucket_teacher_ceiling`, read verbatim). The "0.65–0.67" range is
literally the **two separate per-bucket point values** `a_xs<15m²=0.666` /
`b_sm15–40m²=0.651` from that memo's headline table — never combined into one
number by the old memo.

**Exact caliber, confirmed by reading the harness code**:

| axis | old (`diagnose_gate2_area_strata.py`) | new (this memo) | same? |
|---|---|---|---|
| statistic type | interval-level `agree_key` (via the same `posterior_to_agree_key`/adopted changepoint decoder lineage) rep-vs-rep exact match | identical | ✅ same |
| stratification unit | per-target (anchor), boundaries `[0,15)/[15,40)/[40,100)/[100,inf)` | identical boundaries, identical unit | ✅ same |
| aggregation formula | `per_bucket_teacher_ceiling`: **pooled** `match/tot` summed across all 3 rep-pairs' (target×pair) comparisons in the bucket, unweighted | this memo's `by_stratum` rate = **mean of the 3 pairwise rates** | formally different, **but verified numerically identical on this run's data** (below) — not a real source of the gap here |
| inventory weighting | none (raw per-bucket rate, buckets reported separately, never combined) | none for the *per-stratum* rows in §5.3 (only the §5.3.1 combined `<40 m²` row is `corpus_share`-weighted) | ✅ same for the bucket-vs-bucket comparison below |
| decode-rate / denominator | **~62% of the 642/628-target reference cohort decoded overall** (392 of ~628 total after leakage drop, all buckets combined — dual-fail memo: "868 unit-rep rows / 392 targets... 46% decoded" of 1,884 possible); *`<40 m²`-specific total population size and thus stratum decode-rate is **not recoverable** — raw `unit_rows.jsonl`/`reference.csv` for this cohort are confirmed gone from disk (see below)* | **100%** of the 576-anchor panel decoded in every rep (§5.2) | ❌ **different, magnitude un-quantifiable** |
| corpus/geometry | `llm_endtoend_storebacked_20260704/reference.csv`, 642-target reference cohort, `chip_geom_v1_banked96` chips — **pre-ISSUE-27**, the geometry RUN3 rebuilt | R0 manifest, ISSUE-27 `anchors_v2`, `fullscan_target96_review{24,48}_v2` chips — **post-fix** | ❌ **different — the leading candidate** |

Old raw data (`~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/reference.csv`,
`~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag/`) is confirmed
**not on disk** (checked directly, both paths absent — consistent with the
07-19 cleanup TRACKER already documents for this era's checkpoints/caches).
This memo therefore **cannot** recompute the old cohort's per-bucket
denominator or verify its 3-rep-pair balance directly; it relies on the
published `DATA-gate2-area-stratified-2026-07-10.md` numbers as the primary
source, per the red line ("若考证发现旧数口径与新数同名同定义却仍差 8–10pp"
— see verdict below).

**Same-caliber recomputation on this run's own data** (zero new quota — reuses
the 3 reps' banked provenance, computed via `scripts/temporal/run3_repeat_ceiling.py`'s
`build_decoder`/`posterior_to_agree_key`, pooled per `per_bucket_teacher_ceiling`'s
exact formula):

| bucket | old (pooled, unweighted) | new (pooled, unweighted) | Δ | new 95% Wilson CI |
|---|--:|--:|--:|--:|
| `a_xs<15 m²` (`A24\|[0,15)`) | **0.666** | **0.7002** (584/834) | **+3.4 pp** | [0.668, 0.730] |
| `b_sm15–40 m²` (`A24\|[15,40)`) | **0.651** | **0.8288** (455/549) | **+17.8 pp** | [0.795, 0.858] |
| `<40 m²` combined (pooled, unweighted; old never reported this merged) | n/a | 0.7513 (1039/1383) | — | — |

Verified the "aggregation formula" caliber difference flagged above does
**not** matter for this run's numbers: mean-of-3-pairs and pooled-match/tot
are numerically identical here (0.7002/0.8288 both ways) because this run's
decoded intersection is exactly 576 (full panel) for every one of the 3 pairs
in every stratum — a balanced-denominator case where the two formulas
collapse to the same arithmetic. Whether the *old* cohort's 3 pairs were
similarly balanced per bucket cannot be checked (raw data gone), so a
residual (likely small) aggregation-formula uncertainty remains on the old
side only.

**Verdict on candidate causes** (per the red line: report evidence strength,
do not guess past what is recoverable):

1. **Stratification definition** — ruled out. Identical bucket boundaries,
   identical per-target unit on both sides (confirmed by direct code read).
2. **Frame-level vs interval-level statistic** — ruled out. Both are
   interval-level `agree_key` rep-vs-rep self-consistency via the same
   decoder lineage (confirmed by direct code read of
   `per_bucket_teacher_ceiling`); no frame-level recomputation was needed or
   performed.
3. **Aggregation formula (pooled vs mean-of-pairs)** — ruled out **on this
   run's data** (proven numerically identical above); **not fully
   ruled out on the old cohort's data** (unrecoverable — flagged, not
   claimed as zero).
4. **Decode-rate / selection effect** (old ≈62% overall decode rate vs new
   100%) — **plausible but unquantifiable**. The old `<40 m²` ceiling is
   conditioned on whichever ~221/141 targets per bucket actually decoded to
   a non-missing key out of an unknown larger `<40 m²` population; RUN3-native
   decodes 100% of its panel. Direction of bias (whether non-decoded old
   targets were easier or harder cases) cannot be determined without the
   missing raw data. **Flagged as unresolved, not zero.**
5. **Geometry fix (ISSUE-27, pre- vs post-)** — **leading candidate,
   moderate-to-strong evidence, not proof**. Supporting evidence: (a)
   `DATA-gate2-area-stratified-2026-07-10.md` itself names the "banked96"
   chip geometry as rendering small PV "at near-invisible scale for the
   teacher too" and cites this as direct motivation for the geometry pilot
   that became ISSUE-27/RUN3; (b) R1's independent QA on this run's corpus
   (`DATA-r1-crops-2026-07-19.md`) verifies byte-identical, <1-px-reprojection-error
   geometry on the same chips these reps scored; (c) **the improvement is
   concentrated, not uniform** — `b_sm15–40 m²` gained +17.8 pp while
   `a_xs<15 m²` gained only +3.4 pp (both n well over 100, CIs non-overlapping
   with the old point estimates for `b_sm`, plausibly overlapping for
   `a_xs`). A geometry/centering fix helping mid-small targets more than the
   very smallest (which may sit at an irreducible GSD/resolution floor
   around ~0.3 m/px regardless of centering accuracy — a ~3–4 m target is
   only ~10–13 px across even perfectly centered) is a physically coherent
   mechanism; a pure sampling/aggregation artifact would not predictably
   concentrate this way. **This is interpretation, not a proven causal
   claim** — no controlled pre/post-geometry-fix experiment on the same
   targets was run here.

**One-line conclusion for team-lead**: the old 0.65–0.67 and this run's 0.75
are the **same statistic on different (and non-recoverable-for-full-audit)
corpora** — stratification and statistic-type are ruled out as causes,
aggregation formula is ruled out on this run's own data, decode-rate
selection effect is a real but unquantifiable residual, and the geometry fix
is the best-evidenced explanation, especially for the `b_sm15–40 m²` bucket's
+17.8 pp jump — **if this holds, it is genuine positive evidence that
ISSUE-27's geometry rebuild materially improved teacher self-consistency on
mid-small targets**, which the PRD owner will likely want as supporting
evidence for the geometry-fix decision already made, not just a footnote.

### 5.4 Comparison to old 0.7724 and G1 gate implication

- **Headline ceiling is close**: 0.7708 (new) vs 0.7724 (old), a 0.16-pp
  difference — well inside either run's own spread (new `S`=0.0166, old
  `S`=0.0116). On the *overall inventory-weighted* caliber, RUN3's geometry
  fix did **not** materially change teacher self-consistency at the
  aggregate level, despite a much larger, differently-composed panel (576
  vs ~230 decoded anchors) and a fully independent corpus.
- **New spread is larger** (`S`=0.0166 vs 0.0116): plausibly just a sampling
  effect of a different/larger panel and stratum mix, not necessarily more
  "true" noise — not a claim this memo can decide with 3 reps.
- **`<40 m²` layer materially higher than the PRD's cited prior** (§5.3.1) —
  the most actionable new information in this run. Owner decision (already
  made, PRD §7): RUN3-native's 0.7529 is the operative `<40 m²`
  self-consistency floor, not 0.65–0.67. §5.5 traces the old number to its
  source and finds the same statistic on a different, pre-ISSUE-27-geometry
  corpus with an unrecoverable-for-audit ~62% decode rate; the gap is
  concentrated in the `15–40 m²` sub-bucket (+17.8 pp) far more than
  `<15 m²` (+3.4 pp), which reads as genuine evidence the geometry fix
  helped rather than a pure caliber artifact — flagged for the owner as
  possible supporting evidence for the ISSUE-27 decision, not just
  background.
- **Recommended `ceiling − S` value for the PRD §7 G1 gate** (recommendation
  only — binding sign-off is the PRD owner's call, not this teammate's):
  - Overall: **0.7708 − 0.0166 = 0.7542**
  - `<40 m²` co-headline floor: **0.7529 − 0.0192 = 0.7337** (this is the
    number a student's `<40 m²` performance would need to clear per G1's
    "not bottom out the `<40 m²` layer vs teacher's own same-layer
    self-consistency" clause, IF the PRD owner accepts the deviation
    reasoning in §2).

## 6. Final status

- Panel frozen: `2026-07-19T04:52:56Z` (PANEL_LOCK.json `generated_at_utc`),
  576 anchors / 4,276 obs-per-rep, chip availability 100% (0/40,329 test-split
  obs missing a chip).
- 3 reps scored 2026-07-19 05:45:37Z (rep1 start, incl. 1-anchor smoke) through
  05:56:30Z (rep3 done) — **total wall time ≈ 11 minutes** for all 3 reps
  combined (well inside the "half-hour class" quota window).
- All 3 `reconcile` runs passed clean on the first attempt — no re-runs, no
  partial-rep restarts were needed.
- `compute-ceiling` ran once, offline, immediately after rep 3 reconciled;
  output is `CEILING_RESULT.json` above.
- tmux session `ceiling_reps` — chain complete, session may be closed by
  team-lead at their convenience (nothing left running). Logs retained at
  `~/zasolar_data/geid_temporal/run3_native_line_2026-07/repeat_ceiling_v1/`
  (`chain_run.log`, `rep{1,2,3}_run.log`).

## 7. Open questions / interface seams for team-lead

- **T0 overlap**: this panel is a pure subset of the R0 **test** split. R2's
  localization-cascade replay (`scripts/temporal/replay_localization_cascade.py`)
  has not yet frozen its own sample lock as of this writing — no overlap
  conflict exists *yet*, but when R2 does freeze a sample, it should either
  reuse this panel's anchor list (to keep T0's future paired-ablation
  reuse clean) or be explicitly checked for disjointness. Flagging for
  team-lead coordination, not resolving unilaterally.
- **Panel size vs old cohort**: the old ceiling's decoded intersection was
  ~222–233 anchors (of a 642-row reference cohort); this panel is ~2.5×
  larger by design (576 anchors, cheaper quota headroom on RUN3-native) —
  the new ceiling's confidence interval should be tighter than the old one's
  even before any true score-quality difference; not a bias, but worth
  noting when comparing spreads.
- **Weight-source substitution** (§2) is the one thing that is NOT a pure
  byte-for-byte reuse of the old caliber — flagged for owner sign-off if the
  PRD wants a stricter equivalence proof later.
