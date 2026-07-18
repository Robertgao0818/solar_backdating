# DATA — RUN 2 (gemini-3.1-flash-lite) vs June production deliverable (gemini-3-flash), 2026-07-17

Owner's question: 比对这一次和上一次用 gemini-3-flash 的生产结果之间的差别和一致性
(quantify the differences and consistency between today's completed RUN 2 and the
June production deliverable).

## 1. Methodology

**Join.** `source_feature_id` (0..41392, stable positional row of the FP-cut gpkg)
is the join key on both sides. June side: the `with_census` deliverable CSV
(`jhb_full382_fpcut_install_dated_2026-06-05_with_census.csv`, 41,393 rows).
New side: `anchors_all.csv` maps `anchor_id` → `source_feature_id`; per-target
install intervals come from `infer_install_dates.py` run over both lanes,
produced by teammate `intervals-memo` with
`--vexcel-capture-csv data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv`
(the same flag frozen in the run doc). This memo initially concatenated the
two per-lane files (`install_intervals_a24.csv` + `install_intervals_a48.csv`);
`intervals-memo` subsequently published a canonical combined
`$RUN_ROOT/intervals/install_intervals_all.csv` (41,393 rows, `lane` column
added, sha256 `f5101c8a...c9c6`) — verified byte-identical to the concatenation
(0 cell diffs across all rows/columns), so all numbers below are unaffected;
the analysis script now reads the canonical combined file when present.
**Join coverage: 41,393 / 41,393 on both sides, 0 unjoined rows** (`outer`
join, validated `one_to_one`).

**Important nuance found in the new-run intervals:** the vexcel-clamp step inside
`infer_install_dates.py` further reclassifies the raw scan-state status — e.g. lane
a24's raw scan states show `done_installed_during_census=25,649` /
`done_appears=7,333` / `done_ambiguous_nonmonotonic=1,293`, but after clamping the
counts become `done_installed_during_census=21,406` / `done_appears=7,786` and a
new bucket `done_ambiguous_marker_missed_pv=4,243` appears (clamp catches cases
where the latest-absent observation is at/after the grid's census flight date,
contradicting the FP-cut detector's positive prior). **This memo uses the
post-clamp status as "the new run's answer"** since that is the actual usable
output, not the raw pre-clamp scan state. `intervals-memo`'s own memo
([`DATA-fullscan-run2-install-intervals-2026-07-17.md`](DATA-fullscan-run2-install-intervals-2026-07-17.md))
documents this same reclassification (dip-repair + a census-GT-contradiction
check) and reports 4,647 reclassified anchors (11.2%); this memo's own count
of `done_ambiguous_marker_missed_pv` from the canonical combined intervals file
is 4,645 — consistent to within rounding, not a discrepancy in the underlying
data.

**Exclusions in the "disagreement" analysis:** June rows dated before 2019-01-01
are analyzed separately as an *expected left-censor migration* (the new run's
window floors at 2019-01-01 and cannot see earlier history), not folded into the
general disagreement statistics.

Script: re-runnable, `/tmp/claude-1000/.../scratchpad/compare_june_vs_run2.py`
(not checked into the repo — scratch analysis script). Outputs:
`$RUN_ROOT/comparison_june_vs_run2_2026-07-17/` — `full_joined_comparison.csv`
(41,393 rows, every column from both sides), `crosswalk_matrix.csv` /
`crosswalk_top_flows.csv`, `both_dated_comparison.csv` (29,390 rows, the
intersection where both runs produce a date/bound), `expected_migration_pre2019.csv`,
`install_year_histograms.csv`, `top_disagreements.csv`,
`june_undated_breakdown.csv` / `new_undated_breakdown.csv` /
`both_undated_hard_cases.csv`.

## 2. Status crosswalk — top flows (of 41,393)

| June status | → New status | n | share of June bucket |
|---|---|---:|---:|
| done_appears (36,531 total) | done_installed_during_census | 20,378 | 55.8% |
| done_appears | done_appears | 9,012 | 24.7% |
| done_appears | done_ambiguous_marker_missed_pv | 4,029 | 11.0% |
| done_appears | done_already_present_before_geid_history | 1,766 | 4.8% |
| done_appears | done_ambiguous_no_recent_anchor | 1,205 | 3.3% |
| undated:no_recent_anchor (1,511 total) | done_installed_during_census | 1,006 | 66.6% |
| undated:clamp_inverted (1,455 total) | done_installed_during_census | 914 | 62.8% |
| already_present_lower_bound (1,016 total) | done_installed_during_census | 504 | 49.6% |

Full 7×8 matrix in `crosswalk_matrix.csv`.

## 3. Expected left-censor migration (June pre-2019 dated → new run's 2019 floor)

3,076 June rows have `install_date < 2019-01-01` (744 point-dated `dated`, 162
`already_present_lower_bound`, plus the analogous bound cohort — see table below).
**Only 906 (29.5%) cleanly migrate to `done_already_present_before_geid_history`
as hypothesized.** The rest split as:

| June class (pre-2019, n=3,076) | → already_present | → installed_during_census | → done_appears | → ambiguous_* |
|---|---:|---:|---:|---:|
| dated (n=2,295) | 744 | 924 | 310 | 317 |
| bound_lower (n=781) | 162 | 350 | 163 | 106 |

**41.4% of the pre-2019 cohort (1,274 rows) lands as `done_installed_during_census`
instead** — i.e. the new run's per-target scan finds *no PV at all* until the
census gap, flatly contradicting a June install date of 2009–2018. Section 6's
drill-down explains why: this is not new-run signal loss, it's June's group-level
`gehi_main` date-sharing assigning one shared (and often wrong) date to up to 4
members of a 96 m chip-group, one of which really did install decades earlier —
polluting the others. So the pre-2019 cohort is dominated by suspect June dates,
not by a clean migration.

## 4. Date consistency on the intersection where both runs produce a date

Intersection (June `done_appears` × new `done_appears`/`done_installed_during_census`):
**29,390** rows.

This 29,390 splits into two very different regimes and should not be read as one
number:

**(a) Apples-to-apples: new run also resolved a real bracket (`done_appears`), n=9,012.**
Both sides give a true bracket-midpoint estimate here.
- Same-year agreement: **53.9%**
- Interval-overlap rate: **67.2%**
- Midpoint delta (new − june), days: p10 **−195**, p50 **0**, p75 **241**, p90 **709**, mean 150.9
- Within 90 days: 47.4% · within 180 days: 53.8% · within 1 year: 71.9%

This is the fair like-for-like check and shows **solid agreement** — the median
delta is exactly zero.

**(b) Censored, not a point estimate: new run only reaches `done_installed_during_census`, n=20,378.**
Here `new_mid` is the **census upper bound** (no finer signal, per
`infer_install_dates.py`'s own docstring), not a symmetric bracket midpoint — it
is structurally biased late relative to a true install date. Comparing it
directly to June's midpoint is comparing an upper-censoring bound to a point
estimate.
- Same-year agreement: 5.2% (expected to be low — not a fair comparison)
- Interval-overlap rate: 56.9%
- Midpoint delta (new − june): median **+270 days**, mean +545 days

**Blended headline (both regimes combined, n=29,390, requested by owner):**
interval-overlap 59.9%, same-year agreement 20.1%, midpoint delta median +238
days / p90 +1,194 days / max +5,348 days. **This blended number is dominated by
regime (b) and should not be read as "the runs disagree by 8 months typically"**
— read (a) and (b) separately above.

Install-year histograms (`install_year_histograms.csv`): June's dated population
peaks hard at 2023 (17,516 / 47.9% of dated) due to the census2023-narrowing
layer; the new run's `done_appears` population (only 9,824 rows total) spreads
2019–2024 with a smaller 2023 peak (4,807), and its `done_installed_during_census`
population (23,317 rows) is entirely stamped at the per-grid census mid/flight
date (2024, since that's the definition of the status) — not a real distribution,
a censoring artifact.

## 5. Coverage delta

| class | June | New run |
|---|---:|---:|
| dated (real bracket midpoint) | 36,531 | 9,824 |
| dated_upper_bound (censored, installed_during_census) | — | 23,317 |
| bound_lower (already-present, left-censored) | 1,016 | 2,018 |
| undated (no usable signal) | 3,846 | 6,234 |

The new run resolves far fewer targets to a **true point bracket** (9,824 vs
36,531) but this is mostly the ISSUE-26 per-grid census cutoff doing its job —
most targets' true install genuinely falls inside the pre-census-to-flight gap
where no further imagery exists to narrow it, so `done_installed_during_census`
(23,317, a censored-but-honest answer) is the correct new-run counterpart to what
June's group-sharing/census2023-layer forced into a false point estimate.
**Undated rate is higher in the new run** (6,234 vs 3,846, +62%): 5,738 of these
are A24 lane (15.8% abstain rate) vs 496 A48 (9.8% abstain rate) — the wider
A48 crop (routed for `source_area_m2 >= 40`) resolves more cleanly, consistent
with per-target routing design intent. New-run undated targets skew slightly
larger on average (`source_area_m2` mean 22.9 vs June-undated mean 15.7,
median 13.1 vs 10.9) — larger/more complex rooftops are still harder for both
runs. 698 targets are undated in **both** runs (`both_undated_hard_cases.csv`) —
genuinely hard cases independent of methodology.

## 6. Directional bias

Blended midpoint delta (new − june) is positive (new dates later) in 23,990/28,783
valid pairs (83.3%) vs negative in 1,904 (6.6%) and exactly 0 in 2,889 (10.0%) —
**consistent, large late-skew when the censored `done_installed_during_census`
regime is included**, driven mechanically by that status's definition (census
upper bound). Restricted to the apples-to-apples `done_appears`×`done_appears`
regime, the median delta is exactly 0 with a mild positive skew (p90 +709d) —
**no strong directional bias survives once the two censoring artifacts (June's
pre-2019 floor difference and June's 2023-census-gap piling) are accounted for.**

By June confidence class, delta mean/median (days, blended): high 672/510,
medium 284/238, low 789/654 — no class reverses sign. By June provider: the
`gehi_census2023`-narrowed rows (n=13,377) show a much smaller mean/median delta
(169/197 days) than non-census2023 rows (656/510 days) — the 2023 census-narrowing
layer's brackets agree better with the new run's answers than June's plain
`gehi_main`/`gehi_pertarget` brackets do, which is itself a data point: the new
run's per-grid census-cutoff design and June's census2023 layer are answering a
similar question (bracket the install against the census gap) and agree with
each other much better than either agrees with June's older group-level dates.

## 7. Qualitative drill-down (top 10+ largest midpoint disagreements, excluding pre-2019 migrations)

All top-15 disagreements (`top_disagreements.csv`) are June dates in **2009–2013**
(`gehi_main` or `gehi_pertarget`, June confidence `high`) vs new-run
`done_installed_during_census`/`done_appears` landing in **2023–2024** — deltas of
3,961 to 5,348 days (11–15 years).

Read directly from the new run's scan_state JSONs (e.g.
`t00037270`/`t00036542`/`t00009818`/`t00004366`/`t00013905`): every round-1
observation from 2019 through 2021–2022 explicitly describes the **rooftop
surface at the marker** with no PV modules/grid pattern, at confidence 0.9–0.95
("Roof surface at marker is textured and grey/brown; no rectangular modules
visible", "marker is positioned over a dark-colored roof section with no visible
grid pattern"). These are direct, repeated, high-confidence roof observations —
not ambiguous frames — spanning 2–3 years of the target's *own* per-target crop.
June's 2009–2013 dates for the same `source_feature_id`s came from
`gehi_main`/`gehi_pertarget`, i.e. either a shared 96 m chip-group date (several
of the worst offenders share one June `source_anchor_id`, e.g. `c0000483` →
`source_feature_id` 37266/37268/37269) or a per-target rescan that still
predates the 2019 floor discipline.

**Verdict: the new run's answer looks better supported in essentially all of the
top-15 cases.** The pattern matches the design-difference already known and
flagged by the team lead — June's group-level `gehi_main` sharing (and even some
`gehi_pertarget` per-target recovery passes) let a genuinely-old panel's date leak
onto group-mates that installed much later; the new run's universal per-target
routing (A24/A48, no group sharing) directly observes each target's own roof and
does not exhibit this failure mode for these cases. (Caveat: a couple of the new
run's own `anchor_recovery`-round observations land on ground/pool/vegetation
rather than roof — i.e. the new run is not immune to marker drift either — but
its round-1 *initial* observations at the correct roof location are what carries
the verdict here, and those are consistent and high-confidence.)

## 8. What this comparison CANNOT tell us

- **Deliverable-level only.** June's raw scan_states were wiped 2026-07-12; there
  is no way to re-derive June's per-frame Gemini verdicts, only the final
  bracket/status per polygon. Any explanation of *why* a specific June date is
  what it is (beyond what `date_provider`/`undated_reason` encode) is inference
  from the new run's evidence plus the June README, not a direct re-read of
  June's own reasoning.
- **Confounded design change.** Model (gemini-3-flash → gemini-3.1-flash-lite),
  chip geometry (96 m chip-group → routed per-target A24/A48), window floor/ceiling
  (open pre-2019 history + report-layer clamp → hard 2019 floor + ISSUE-26
  per-grid census cutoff), and provider merge strategy (TM-primary +
  separate Wayback-2023 layer → unified TM+Wayback merge) all changed
  **simultaneously**. This memo can attribute broad *symptoms* (e.g. the pre-2019
  migration, the census-gap censoring) to specific known design changes because
  those symptoms are structurally tied to one change each, but it cannot isolate,
  e.g., how much of the apples-to-apples 9,012-row agreement rate is due to the
  model swap vs the chip-geometry swap — a controlled ablation would be needed.
- **No ground truth.** Neither run is validated against independently known
  install dates; "better supported" in the section 7 drill-down is a
  read of internal evidence consistency (repeated same-signal frames on the
  correct roof location), not a check against a labeled install date.
- **Single new-run rep.** The new run used 1 rep per target (frozen decision,
  §2 of the run doc); June's provenance for per-provider rep counts is not
  fully known from the deliverable CSV alone, so scan-instability effects
  cannot be separated from genuine model/geometry differences.
- **A48/A24 routing didn't exist in June** in this form, so the "coverage delta
  by chip_arm" (§5) has no June-side counterpart to compare against.

## Addendum (2026-07-17, evening) — interval-vs-interval comparison

Owner follow-up: June's point `install_date` is known-buggy (§7's group-leak
finding confirms it), but June's `install_interval_start/end` were **hand-corrected
by an agent under owner direction** (census-ceiling clamps etc.) and are trusted
more than June's points. Separately, the new run's `done_installed_during_census`
regime is **not** a blind "upper bound = census date" guess: ISSUE-26 gave every
anchor post-census reference frames that visually confirm presence, so
`[latest_absent, census_date]` in that regime is an imagery-backed genuine
interval. This addendum re-centers the comparison on interval-vs-interval
consistency and treats the censored regime as a first-class bracket, not a
"not comparable" bucket. New script:
`/tmp/claude-1000/.../scratchpad/compare_intervals_addendum.py` (scratch, reads
`full_joined_comparison.csv` from §1's join rather than rejoining). New outputs
in the same `comparison_june_vs_run2_2026-07-17/` dir: `interval_vs_interval_full.csv`
(31,384 rows), `disjoint_pairs.csv`, `containment_counts_all.csv` /
`containment_counts_finite.csv`, `regime_breakdown.csv`.

**Inclusion rule.** A pair is included when *both* sides emit an interval:
June `date_status ∈ {done_appears, already_present_lower_bound}` (36,531 + 1,016
of the 41,393; June `undated` excluded — no interval) crossed with new
`status ∈ {done_appears, done_installed_during_census, done_already_present_before_geid_history}`
(new `done_ambiguous_*` excluded — no interval emitted). **31,384 / 41,393
(75.8%) qualify.** Open-below intervals (June `already_present_lower_bound`,
new `done_already_present_before_geid_history`) have no real start observation;
for overlap/containment/IoU math their start is filled with a `1900-01-01`
sentinel (stands in for "unbounded/unobserved before this bound"), applied
symmetrically to both sides. 31,384 breaks down as June {dated: 30,549,
bound_lower: 835} × new {done_installed_during_census: 20,882, done_appears:
8,568, done_already_present_before_geid_history: 1,934}.

### A1. Overlap rate, IoU, containment (all 31,384 pairs)

- **Overlap rate: 58.6%.**
- **IoU (days) is strongly bimodal, not centered** — median is *not* a useful
  single number here. On the finite-both-sides subset (n=28,783, excludes
  open-below sentinel dilution): 54.5% of pairs have IoU exactly 0 (disjoint or
  touching), **26.6% have IoU exactly 1 (byte-identical brackets)**, and the
  remaining ~19% spread across partial overlap. p10=0.000, p50=0.000, p90=1.000.
  (On the full 31,384 including open-below pairs, the sentinel makes the union
  huge whenever one side is open-below, mechanically driving those pairs' IoU
  toward 0 regardless of real agreement — overlap/containment are the meaningful
  metrics for that subset, not IoU.)
- **Containment** (all 31,384): disjoint 12,980 (41.4%) · equal 7,647 (24.4%) ·
  new⊆june 5,241 (16.7%) · partial overlap 4,381 (14.0%) · june⊆new 1,135 (3.6%).
  Of the 7,647 **exact-equal** pairs, 6,558 (85.8%) have June
  `date_provider=gehi_census2023` — i.e. June's hand-corrected 2023-census-narrowed
  brackets and the new run's brackets land on the **identical** capture-date pair
  in most cases, which makes sense: both runs draw last-absent/first-present
  dates from the same underlying TM/Wayback capture history, so when both resolve
  to a real (non-censored) bracket in the same imagery gap they frequently pick
  the exact same two frames. This is the strongest positive validation signal in
  the whole comparison.

### A2. Endpoint deltas (start = last-absent, end = first-present/census)

- **Start-side** (only where *both* sides have a real, non-open start — the
  actual "did the two scans see absence-until-the-same-date" signal), n=28,783:
  delta (new − june) days: p10=**0**, p50=**+268**, p90=**+1,089**, mean +393.
  Positive median means the new run's latest-absent observation is typically
  *later* than June's — i.e. the new run keeps finding "still absent" in frames
  June's bracket had already called "present." This is consistent with the
  §7 group-leak finding (June's bracket starts too early for some group members).
- **End-side** (all 31,384, always finite): delta (new − june) days:
  p10=−20, p50=+30, p90=+1,142, mean +304. Broken out by new regime:
  `done_appears` mean +165/median **0** (tight agreement — expected, this is
  the real first-present frame on both sides); `done_installed_during_census`
  mean +424/median +85 (largely **census-clamp-driven**, as expected — new
  run's end is the per-grid census/flight date, which typically sits a bit
  later than June's report-layer clamp); `done_already_present_before_geid_history`
  mean **−377**/median **−196** (new run's already-present bound tends to be
  *earlier* than June's — consistent with the new run having a longer/denser
  pre-window scan floor picking up an earlier real capture as the earliest
  scored vintage).

### A3. Regime breakdown (treating censored as legitimate)

| June class | New status | n | overlap rate | IoU p50 |
|---|---|---:|---:|---:|
| bound_lower | done_already_present_before_geid_history | 168 | **100.0%** | 0.993 |
| bound_lower | done_appears | 163 | 9.2% | 0.000 |
| bound_lower | done_installed_during_census | 504 | 22.4% | 0.000 |
| dated | done_already_present_before_geid_history | 1,766 | 49.5% | 0.000 |
| **dated** | **done_appears** | **8,405** | **67.2%** | **0.304** |
| dated | done_installed_during_census | 20,378 | 56.9% | 0.000 |

**Censored-regime highlight** (new `done_installed_during_census`, n=20,882,
the population the owner asked to treat as first-class): overlap/consistency
rate **56.0%** — i.e. 56% of the time, June's hand-corrected interval overlaps
or sits inside the new run's imagery-backed `[latest_absent, census]` bracket.
Containment within this regime: disjoint 9,181 (44.0%) · equal 4,953 (23.7%) ·
new⊆june 3,704 (17.7%) · partial 3,028 (14.5%) · **june⊆new only 16 (0.1%)**.
June's interval is almost never a strict subset of the new run's bracket — when
they agree, it's almost always an exact match (both hit the same real frames),
not a case of June's tighter hand-correction nesting inside a looser new
bracket. The 44.0% disjoint rate in this regime is the main open question — see
A4.

### A4. Disjoint-pair analysis (12,980 pairs, 41.4% of all both-interval pairs)

Direction: **June-earlier 11,425 (88.0%)** vs new-earlier 1,555 (12.0%) — i.e.
when the two brackets don't touch at all, it is overwhelmingly June's bracket
that sits entirely *before* the new run's. Median gap magnitude: june-earlier
516 days, new-earlier 562 days (comparable typical size when it happens the
other way, just far rarer).

**Group-leak fingerprint, gap-size-graded** (does the June anchor's
`source_anchor_id` repeat across >1 `source_feature_id` — the mechanism §7
identified for shared/leaked dates?). Base rate of shared-group June anchors,
whole population: 66.9%. Among June-earlier disjoint pairs, shared-group rate
rises **monotonically with gap size**:

| June-earlier gap bucket | n | shared-group rate | enrichment vs base (66.9%) |
|---|---:|---:|---:|
| 0–180 days | 2,802 | 68.3% | 1.02× |
| 180–730 days | 4,484 | 75.9% | 1.13× |
| 730–1,825 days (2–5y) | 3,243 | 80.5% | 1.20× |
| 1,825+ days (5y+) | 896 | **87.7%** | **1.31×** |

Also, only **14.2%** of June-earlier disjoint pairs have `june_end < 2019-01-01`
(i.e. entirely outside the new run's observable window — a clean "can't compare,
window floor differs" case); the remaining **85.8%** are pairs where June's
bracket is fully *inside* the new run's observable window yet the two runs still
placed disjoint brackets — these are genuine disagreements, not window-floor
artifacts, and the largest-gap ones are disproportionately the shared-group
(chip-group-leaked) June anchors, corroborating §7's qualitative finding
quantitatively across the whole population rather than just the top-15 sample.
The blunt "all disjoint" enrichment (1.14×, i.e. 76.3% vs 66.9% base — see
`disjoint_pairs.csv`) understates this because it mixes in a majority of
small-gap disjoint pairs (adjacent-frame near-misses, not real group leaks)
with the large-gap ones; the gap-graded table is the more honest read.

### A5. Updated honest-limits (interval-specific)

- **The `1900-01-01` open-start sentinel is a modeling choice, not data.** It
  makes overlap/containment well-defined for bound rows but any date that far
  back is never realized in the actual dataset (no PV predates ~2005 in this
  market) — it's a mathematical device to make "unbounded before X" comparable,
  not a claim about when the true install could be.
- **IoU on this dataset is not usefully summarized by a single quantile** — it's
  bimodal (exact-match spike + disjoint spike), so the p50 figures quoted above
  (0.000) are technically correct but should always be read alongside the
  containment/overlap-rate breakdown, never alone.
- **Group-leak enrichment is correlational, not a per-row diagnosis.** A June
  anchor being "shared-group" raises the *probability* a large disjoint gap is a
  leak artifact; it does not prove any individual pair is one — some shared-group
  anchors have members that genuinely installed at different times (that's the
  whole point of a chip group), and some large gaps may be genuine new-run or
  June errors unrelated to group sharing.
- **"June's hand-corrected intervals are trusted" is an owner instruction, not
  independently re-verified here** — this addendum did not re-audit the
  hand-correction process itself, only compared its output against the new run.
