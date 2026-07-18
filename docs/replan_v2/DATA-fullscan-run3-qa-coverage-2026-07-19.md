# DATA — RUN 3 coverage-gain QA: is the 78.4%→83.2% gain real accuracy? (2026-07-19)

Parent: [`RUN-fullscan-gemini-backdating-run3-2026-07-18.md`](RUN-fullscan-gemini-backdating-run3-2026-07-18.md)
§7 item 1 follow-up, and the standing caveat in
[`DATA-fullscan-run3-reconcile-vs-run2-2026-07-18.md`](DATA-fullscan-run3-reconcile-vs-run2-2026-07-18.md)
("raw status/coverage signal, not an independently validated accuracy
measurement — QA sampling still needed"). This memo adds directional visual
evidence; it does not estimate corpus-wide accuracy.

**Question.** RUN 3 (anchors-v2, ISSUE-27 offset fix) shifted the deliverable
coverage mix vs RUN 2: `dated` 22.0%→45.4%, `census_bound` 56.3%→37.8%,
bounded 78.4%→83.2%. Is that a **real accuracy improvement**, or did RUN 3 just
**swap one failure mode for another** (e.g. trade uninformative census-bound
fallbacks for confidently-wrong `appears` dates)?

**Verdict — evidence strongly supports a predominantly real, placement-driven
improvement rather than a wholesale failure-mode swap; the visual sample is
directional and does not fully quantify regression or corpus-wide accuracy.**
Two independent lines of evidence agree:
1. **Population dose-response (n=41,393, fully reproducible).** The coverage
   change is concentrated exactly where the RUN 2 marker was badly mis-placed
   by the stale-offset bug and RUN 3 fixed it. The recovery strata S1/S2/S3 sit
   at ~18–20 m median RUN 2 offset; the unchanged/stable control sits at ~4 m.
   A random new failure mode would not track placement error like this.
2. **Stratified visual grading (n=114 sampled, 54 convenience-selected and
   graded).** In the original 48, the corrected clean-PV count is **41/48
   (85%)**, not 43/48; after adding six explicit `dated→left_censored`
   regression cases it is 47/54. The 14 graded new-date cases in S1/S2/S3 are
   all defensible/plausible. Twelve regression cases were checked across S4a
   and S4b; one S4a case is a genuine loss, while S4b contains two uncertain
   cases. This does **not** justify "exactly one" across all 1,950 date losses.

Residual risks are bounded and named in §6 — they do **not** overturn the
verdict but they do mean "coverage gain is real" ≠ "every RUN 3 date is
month-accurate."

---

## 1. How a reviewer verifies this memo

All inputs are immutable RUN 2 / RUN 3 products under `$FULLSCAN_ROOT`
(`~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07`). All three
QA scripts are deterministic and **read-only with respect to the frozen inputs
and chips**; they write only the listed derived artifacts under `$QA`. The
renderer resolves marker-crop review PNGs the scan already wrote for Gemini via
the production path function (verified 0 new files under the chips dir).

```bash
source scripts/activate_env.sh
FS=~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07
QA=$FS/run3_v2/coverage_qa_2026-07-19

# (1) transition matrix + offset dose-response + fixed-seed sample (§3, §4)
python3 scripts/temporal/qa_run3_coverage_sample.py \
  --run2-intervals $FS/intervals/install_intervals_all.csv \
  --run3-intervals $FS/run3_v2/intervals/install_intervals_all.csv \
  --v1-anchors     $FS/anchors_all.csv \
  --out-dir        $QA                         # seed 20260719

# (2) re-render grading artifacts (source chips remain read-only)
python3 scripts/temporal/qa_run3_render_sheets.py \
  --manifest   $QA/qa_manifest.csv \
  --v2-anchors $FS/anchors_v2/anchors_all.csv \
  --v1-anchors $FS/anchors_all.csv \
  --out-dir    $QA

# (3) the recorded per-anchor visual grades (§5)
python3 scripts/temporal/qa_run3_write_grades.py
```

### Input provenance (sha256)
| input | sha256 |
|---|---|
| `intervals/install_intervals_all.csv` (RUN2) | `f5101c8ad926316a7effb06f179cdc6e8720664aa51508c2d0c794a63fcb8cc6` |
| `run3_v2/intervals/install_intervals_all.csv` (RUN3) | `2ab5a1f2fd1a62301772d8a6c08f03a7ce144cc1e2284c7f4635000f2fffd11f` |
| `anchors_all.csv` (v1, stale offsets) | `ba8abc613a4c6ac3f51cb88d14a400ee12b8f0170b09b9effac87ab00ab5400a` |

### Output artifacts (under `$QA/`, sha256 of the derived tables)
| artifact | sha256 / note |
|---|---|
| `qa_manifest.csv` (106 original + 8 S4b supplemental samples) | `9da5dcbc5759e47d63d26bcb8e3a0aedfd62abf79e1f8132190cea8b5d5f8a49` |
| `graded_anchors.csv` (54 recorded visual verdicts) | `917ed5eda6a2f75e04d47a4f6fb6e750dac78f0f06dc32bbf3ab1fd3766c45b7` |
| `transition_offset_analysis.md` (§3+§4 tables) | `7d30006cbeba6be18f14ed7268f78bbcca15f789b8a2a0016d1019368ea17559` |
| `sheets/<stratum>_NN.png` | contact-sheet strips (visual grading input) |
| `pivotal/<stratum>_NN.png` | native-res last-absent + present frames + evidence |

Independent-check path: re-run (1)–(3) → confirm the three derived-table hashes
above match (this validates §3/§4 exactly), then open `sheets/` + `pivotal/`
and re-grade against `graded_anchors.csv` (this is the subjective §5 layer —
disagreement here is expected at the margins and welcome).

---

## 2. Framing: use the deliverable's four coverage classes, not "dated/undated"

The reconcile memo's headline ("bounded 78.4%→83.2%") bundles two very different
things. This QA uses the **deliverable** `coverage_reconcile` classes
(obtained by calling production
`build_install_dated_deliverable.py::classify_row` directly):

- **dated** = `done_appears` with a real absent→present bracket + midpoint
  (the high-value class — this is what an econ user actually dates).
- **census_bound** = `done_installed_during_census` — an *upper bound only*
  (install ≤ census flight); wide, low-information, and the exact bucket RUN 2's
  QA found ~45% off-target ([`DATA-fullscan-run2-qa-sample`](DATA-fullscan-run2-qa-sample-2026-07-17.md) §9/§12).
- **left_censored** = `done_already_present_before_geid_history` (install ≤2019).
- **undated** = ambiguous / inverted-interval-mislabeled.

Class totals reproduce the deliverable README exactly. An independent reviewer
also compared production `classify_row` row-by-row over the frozen RUN2/RUN3
inputs (82,786 calls) and found zero class mismatches; this is an input-specific
cross-check in addition to the script now using the full production gate.

| class | RUN2 | RUN3 | Δ |
|---|---:|---:|---:|
| dated | 9,123 (22.0%) | 18,783 (45.4%) | **+9,660** |
| census_bound | 23,317 (56.3%) | 15,664 (37.8%) | −7,653 |
| left_censored | 2,018 (4.9%) | 4,058 (9.8%) | +2,040 |
| undated | 6,935 (16.8%) | 2,888 (7.0%) | −4,047 |
| **bounded (dated+census_bound)** | **32,440 (78.4%)** | **34,447 (83.2%)** | **+2,007** |

The story the reviewer must validate is *not* the modest +2,007 bounded net —
it is the large internal churn: **+9,660 net `dated`** (real dates), drained out
of `census_bound` and `undated`. If those new `dated` rows are correct → real
improvement. If spurious → failure-mode swap.

---

## 3. Where the +9,660 `dated` comes from (transition matrix, n=41,393)

Full 16-cell matrix in `transition_offset_analysis.md`; the load-bearing cells:

| RUN2 → RUN3 | n | reading |
|---|---:|---|
| census_bound → dated | **8,533** | idc upper-bound narrows to a real appears date — the core claim |
| undated → dated | 2,752 | coverage recovery into a real date |
| left_censored → dated | 325 | — |
| dated → census_bound | −1,145 | real date lost to upper-bound (regression) |
| dated → undated | −410 | real date lost (regression) |
| dated → left_censored | −395 | reclassified ≤2019 |
| **net dated** | **+9,660** | 8,533+2,752+325 − 1,145−410−395 |
| census_bound → census_bound | 11,384 | stayed low-info (48.8% of RUN2 census_bound) |

So the entire quality question reduces to: are **`census_bound→dated` (8,533)**
and **`undated→dated` (2,752)** real (strata S1/S3), are the **regressions
(1,950 across S4a/S4b)** genuine losses, and is the **persistent census_bound
(11,384, S6)**
legitimate?

---

## 4. Quantitative backbone: the offset dose-response (n=41,393)

The RUN 2 review crop was centred on `anchors_all.csv`'s stale
`target_offset_x_m/y_m` (recenter-pilot §17 root cause); RUN 3's anchors-v2
zeroes it (verified: `|offset|` max 0.000 over all 41,393). So **RUN 2 offset
magnitude = how badly that anchor's marker was mis-placed**, and it is a free,
per-anchor, objective covariate. Cross-tab it against transition stratum:

| stratum (RUN2→RUN3) | n | median off | mean | >10m | >25m |
|---|---:|---:|---:|---:|---:|
| S1 census_bound→dated | 8,533 | **19.9** | 20.9 | 79.1% | 34.9% |
| S2 contradiction(marker_missed_pv)→resolved | 4,256 | 18.5 | 19.9 | 76.3% | 32.0% |
| S3 undated→dated | 1,048 | 19.8 | 20.2 | 72.1% | 35.3% |
| S7 census_bound/undated→left_censored | 1,863 | 19.7 | 21.3 | 81.2% | 36.3% |
| S6 census_bound persist | 11,384 | 14.5 | 16.5 | 60.7% | 26.6% |
| S4a dated→census_bound/undated | 1,555 | 9.8 | 13.3 | 49.3% | 18.0% |
| S4b dated→left_censored | 395 | 11.2 | 13.9 | 54.9% | 15.2% |
| S5 redate ≥180d | 1,602 | 8.5 | 11.7 | 44.4% | 12.5% |
| **S8 dated_stable (control)** | 5,571 | **4.2** | 7.2 | 25.4% | 5.6% |
| ALL | 41,393 | 14.3 | 16.4 | 61.3% | 25.5% |

**This is the crux.** The recovery strata S1/S2/S3 all sit at ~18–20 m median
offset with ~72–79% above 10 m. The corrected S7 is also placement-sensitive,
but it is a left-censor reclassification, not a coverage-gain stratum. The
**stable control** (S8:
was dated, stayed dated, date barely moved) sits at **4.2 m** median, 25% above
10 m. On a 96 m chip a 20 m marker error is ~21% of the frame — routinely off
the target building entirely. The dose-response is monotone and clean:
**coverage changed precisely where placement was broken and is now fixed; it did
not change where placement was already fine.** A "different failure mode"
(random spurious `appears` reads) has no reason to correlate with RUN 2 offset —
this correlation is the signature of a real, mechanistic fix.

The *regression/redate* strata S4a/S4b/S5 have lower offsets than the recovery
strata (median ~9–11 m). That contrast is consistent with the placement
mechanism, but it does not determine whether any individual lost RUN 2 date was
correct; the regression sample in §5 remains directional.

---

## 5. Visual grading (n=114 sampled; 54 convenience-selected and graded)

Sampler: `qa_run3_coverage_sample.py`, `random.Random(20260719)`, hand-set
quotas per stratum (manifest `qa_manifest.csv`). The original 106-anchor draw
is preserved. Its legacy S7 pool contained 395 `dated→left_censored` rows, but
none happened to be selected; the corrected sampler therefore adds a separate
8-anchor S4b draw, bringing the manifest to 114. Grading input is the exact
marker-crop review PNGs Gemini scored (offset==0 v2 geometry), rendered as
chronological strips (`sheets/`) plus native-≥256px pivotal frames with Gemini's
evidence text (`pivotal/`). Rubric =
[`DATA-fullscan-run2-qa-sample` §12](DATA-fullscan-run2-qa-sample-2026-07-17.md)
placement tiers (a off-structure / b on-roof PV-outside / c PV-enclosed / d
on-roof genuinely-no-PV); `u` is an explicit resolution-limited abstention.

**Selection caveat.** The 114 manifest rows are fixed-seed stratified draws,
but the recorded grades are the first six rows in each sorted stratum — the
`_01` sheet created jointly by manifest sorting and `PER=6` rendering. Thus the
54 grades are a deterministic **convenience subset**, biased toward the lowest
sampled anchor IDs within each stratum. Aggregate rates below describe only
these inspected rows; they are not unbiased stratum or corpus estimates.

**Aggregate (n=54 graded, descriptive only):**

- **placement_tier: c=47, d=3, b=2, a=1, u=1.** In the original 48 the
  corrected count is **c=41/48 (85%)**, after moving `t00008395` to b and
  `t00012326` to d; `t00012218` is u, not a definite d. The expanded 47/54
  remains a strong placement-improvement signal, but is not a 90% population
  estimate. For context, RUN 2's census bucket had 16/40 tier-a + 1 tier-b and
  0 tier-c in [qa-sample §12](DATA-fullscan-run2-qa-sample-2026-07-17.md).
- **date_verdict: defensible 26, plausible 15, benign_drop 3, uncertain 9,
  genuine_loss 1.** The load-bearing new-date strata S1/S2/S3 remain 14/14
  defensible-or-plausible (12 defensible, 2 plausible).
- **vs RUN2: improved 30, same 11, regressed_benign 3, regressed_genuine 1,
  na 9.** These labels are per-inspected-anchor judgments, not an estimated
  regression rate.

**Per-stratum:**
- **S1 census_bound→dated (6 graded, incl. 6 pivotal-confirmed):** 5 defensible
  + 1 plausible. At native resolution the last-absent frame shows a clean roof
  and the first-present frame shows a distinct dark PV grid *at the marker*
  (Gemini evidence concurs). My initial thumbnail-level worry that some present
  reads sat on "degraded" frames did **not** survive the native-res check
  (e.g. `t00012762`) — the RUN 2 §5 quality-flag-miscalibration risk did not
  materialise in this stratum. These had median v1 offset ~20 m → textbook
  offset-fix wins.
- **S2 contradiction resolved (6):** the `done_ambiguous_marker_missed_pv`
  cohort *is* the offset-bug fingerprint (census GT says PV, mis-placed marker
  read absent → contradiction). Fixing placement resolves it — cleanest case
  `t00004554` (v1off 11.7 m) reads **present in every frame since 2019** →
  correct `already_present`; `t00008713`/`t00008790` (v1off 30–39 m) get real
  appears brackets.
- **S3 undated→dated (6):** all defensible/plausible; incl. a large commercial
  PV farm (`t00006731`) RUN 2 left undated on a blown-white frame.
- **S4a dated→census_bound/undated (6, incl. pivotal):** the original
  regression counter-check. **Mostly
  benign** — `t00008149`/`t00008467` (v1off 30/33 m) are RUN 3 correctly reading
  a clean roof all-absent after fixing placement, i.e. RUN 3 *dropped an
  off-target RUN 2 `appears` artifact* (good). `t00016804` is a malformed anchor
  (bridge; v1 offset **1.13 m**, source area **381.25 m²**, footprint
  60.34×27.93 m) safely kicked to undated. **One observed genuine loss**:
  `t00002849` (v1off 0.1 m) — the PV is real but on the roof section *adjacent*
  to the marker box (tier b); RUN 3's census_bound under-dates it. Root cause is
  census-side target-polygon precision (recenter-pilot §17's residual 27.3%),
  **not** the offset fix and out of scope for this repo. This six-anchor result
  applies only to S4a's 1,555 rows.
- **S4b dated→left_censored (6 newly graded from 395):** 2 defensible, 2
  plausible, 2 uncertain. `t00025094` and `t00031393` have convincing PV in
  2019; `t00002426` and `t00021224` depend on repairing three intervening
  absent reads and remain uncertain. No sampled case was labeled a definite
  genuine loss, but this small convenience subset does not quantify S4b risk.
- **S5 redate ≥180d (6):** mostly offset-driven improvements (`t00012878`
  v1off 48 m → RUN 3 date earlier and panel-backed vs RUN 2's off-target date);
  a minority (`t00026768` v1off 6.6 m, `t00027950` v1off 0.1 m) show genuine
  cross-run Gemini scoring disagreement on the same frames — redate direction
  uncertain there.
- **S6 census_bound persist (6):** mixed but mostly compatible with an honest
  upper bound; `t00009805` is the clean example. It is **not** true that all six
  are present only in the final frame: `t00008395` is all-absent at the marker
  with obvious PV outside the box (tier b), and `t00012326` has no visible PV in
  usable frames plus an amber final frame (tier d/date uncertain).
- **S7 census_bound/undated→left_censored (6):** mixed. Clean wins are large
  solar farms present in every frame since 2019
  (`t00029659`/`t00030702`/`t00031490`). But 2/6
  (`t00016220`,`t00018101`) reach `already_present` via a single 2019 present
  read + `repaired_isolated_dip` over a present→absent→present sequence — a
  possible parallax-flicker mislabel (see §6).
- **S8 dated_stable control (6):** clean. Tiny offsets and equal RUN2/RUN3
  midpoints on several, with clear absent→present transitions. `t00035323` is
  the exception: its midpoint moved **109 days** (not 256) and correctly stays
  below the 180-day S5 threshold. Where placement was already right, RUN 3 was
  generally stable.

---

## 6. Residual risks (do not overturn the verdict; do bound the claim)

1. **Date precision is vintage-quantized.** Brackets cluster on shared catalog
   dates (e.g. many S1 flips are `2023-01-23 absent → 2024-02-29 present`). The
   *direction* is right; the *midpoint* can be off by up to the inter-vintage
   gap. "Coverage gain is real" ≠ "month-accurate install date."
2. **`census_bound` remains 37.8% of the corpus and low-information.** Its
   residual off-target rate is now bounded by the **census-side segmentation
   placement** problem (recenter-pilot §16: control blind-bucket anchors showed
   the target polygon itself off real PV), which the offset fix cannot touch —
   orthogonal failure mode, ZAsolar-census-team scope.
3. **`already_present` via dip-repair (S7/S4b).** A parallax/off-nadir 2019
   "present" flicker followed by years of "absent" can be forced to
   `already_present` by `infer_install_dates.py`'s isolated-dip repair — a small
   number of left_censored labels may be over-confident.
4. **Grading is partly subjective and selection is not random.** The same
   resolution floor documented in RUN 2 QA §11/§13 applies. The 54/114 graded
   rows are the first sorted six per stratum, not an unbiased subsample; the
   population offset argument (§4), not an aggregate visual rate, carries the
   headline.
5. **Unpatched inverted-interval defect** (RUN 2 = RUN 3, owner-accepted): the
   deliverable's `undated_reason=inverted_interval_mislabeled_appears` guard
   already catches these — not a new RUN 3 issue.

---

## 7. What this establishes / does not

**Establishes:** evidence strongly supports a predominantly real,
placement-driven improvement rather than a wholesale failure-mode swap. The
coverage changes track RUN 2 offset error in a clean dose-response, the
corrected inspected placement subset is much stronger than RUN 2's census
bucket, and all 14 inspected S1/S2/S3 new-date cases are visually PV-backed.

**Does not establish:** month-level date accuracy; the correctness of the 37.8%
`census_bound` bucket beyond "not off-target from the offset bug" (its ceiling is
set by census-side segmentation, out of scope); or a corpus-wide error *rate*
(the visual sample is directional and convenience-selected). It also does not
establish that only one of the 1,950 date losses is genuine.

**Recommended next steps (owner-optional, in leverage order):** (a) owner pass
over `sheets/`+`pivotal/` to ratify/override `graded_anchors.csv`; (b) if a
corpus-wide new-date or regression *rate* is needed, run purpose-sized random
audits of S1 and S4a/S4b with grading IDs randomized independently of sheet
order; (c) route the census-side polygon
precision finding (§6.2, S4a `t00002849`) to the ZAsolar census/segmentation team
— it is the last remaining structural limiter and outside this plugin's boundary.

## 8. Reviewer checklist
- [ ] Re-run script (1); confirm `transition_offset_analysis.md` hash matches §1.
- [ ] Confirm §2 class totals equal the deliverable README (dated 18,783 / 45.4%,
      bounded 83.2%).
- [ ] Confirm §4 dose-response: recovery strata S1/S2/S3 median ~18–20 m, S8
      control 4.2 m; confirm S4b contains 395 rows and S7 contains 1,863.
- [ ] Re-run scripts (2)+(3); open `sheets/S1_*`,`pivotal/S1_*` and spot-check
      that first-present frames show PV at the marker; inspect `S4b_*` and
      sample-check `graded_anchors.csv`.
- [ ] Confirm source chips remain read-only:
      `find $FS/basemap_rebuild_2026-07-13/chips -newermt '-10 minutes'` → 0.
