# Data memo: RUN 2 targeted re-scan pilot (2026-07-17)

Status: pre-registered pilot, owner-approved before any full 4,821-anchor
rollout, extended mid-flight with an A5 arm once a second, larger production
defect was independently confirmed (§2). Read-only against
`~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/` (scan
states, chips, `anchors_all.csv`). All new outputs under
`rescan_pilot_2026-07-17/` (`$OUT` below): `fingerprints/`, `sample/`,
`csvs/`, `enlarged_pngs/`, `audit/`, `step0/`, `logs/`. No scan state or chip
was modified (enlarged/recentered crops render fresh into
`$OUT/enlarged_pngs`, never next to the source `.tif`; production crops are
read via `ensure_single_target_review_png`, which is mtime-gated and already
cached by the production run, so this pilot re-scores through zero new
writes under the chips root).

Parent: [`DATA-fullscan-run2-parallax-audit-2026-07-17.md`](DATA-fullscan-run2-parallax-audit-2026-07-17.md)
(mitigation #3) and [`DATA-fullscan-run2-qa-sample-2026-07-17.md`](DATA-fullscan-run2-qa-sample-2026-07-17.md)
(§11-12 marker-offset severity grading, the human-eyeball precedent for the
bug confirmed programmatically in §2 below). Gateway: sub2api
localhost:8080, run serially (no thread pool, one anchor at a time) at well
under the ~8 workers/4 qps ceiling that tripped a rate limit on
2026-07-17's full run.

Scripts (committed, not scratchpad):
`scripts/temporal/rescan_pilot_step0.py`,
`scripts/temporal/rescan_pilot_fingerprints.py`,
`scripts/temporal/rescan_pilot_sample.py`,
`scripts/temporal/rescan_pilot_sample_offset.py`,
`scripts/temporal/rescan_pilot_run.py`,
`scripts/temporal/rescan_pilot_select_a4.py` (built, not exercised — see
§3.3),
`scripts/temporal/rescan_pilot_analyze.py`.

## SELECTION-BIAS CAVEAT (read this first)

The F1/F3 fingerprint sample (§2-3, 150 anchors) is **selection-biased
toward small-offset anchors** — both fingerprints require at least one
confident-present observation to exist in an anchor's history (F1 = a
present->absent contradiction; F3 = an absent->present transition), and
`done_installed_during_census`/`marker_missed_pv`/`no_recent_anchor` (71% of
the whole run, zero present observations by construction) get exactly zero
fingerprint hits regardless of offset — see the parent parallax-audit memo
§0. A large stale offset mechanically produces "confident absent at every
frame" (the marker sits on pavement/lawn/a neighboring roof section, never
the real array), which is precisely the pattern that keeps an anchor OUT of
the F1/F3 population. **The 150-anchor pilot therefore systematically
under-samples the anchors an offset fix helps most** — §4's 30-anchor
large-offset `done_installed_during_census` draw exists specifically to
close that gap, and its recovery-rate number (§4) is the more decision-
relevant one for sizing the fix's population-wide effect, not the 150-anchor
F1/F3 numbers in §3.

## 0. Owner's model-hypothesis probe (Step 0)

Owner's hypothesis: for `t00024435` (a24, `done_installed_during_census`,
8.53m² tiny install) the PV IS there but too small for
`gemini-3.1-flash-lite` at this resolution, and `gemini-3-flash` (June
production model) would see it. Re-sent all 3 index anchors'
(`t00024435`, `t00039667`, `t00038450`) full frame histories to
{gemini-3.1-flash-lite, gemini-3-flash, gemini-3-flash-agent} x
{production crop, 2x-enlarged crop} through the exact production
`score_batch_with_fallback` path (same batch prompt + census-calibration
suffix). `gemini-3-flash-agent` IS served by the gateway.

**Verdict: model-swap part of the hypothesis CONFIRMED on the exact index
case; enlargement part REFUTED (and net-harmful in 2/3 cases).** (This
probe predates the §2 offset-bug discovery — all 3 index anchors' offsets
turned out to be small, so this finding is orthogonal to, not explained by,
the placement bug.)

- **`t00024435`** (8.53m²): `gemini-3.1-flash-lite` says confident absent at
  all 6 frames under both crops (byte-for-byte reproduction of production,
  no stochastic flip). `gemini-3-flash`/`gemini-3-flash-agent` flip
  `2023-11-24` and `2025-01-25` to present (conf 0.95) at the **production**
  (tight, 24m) crop — but revert to absent under the **enlarged** (48m)
  crop. Enlarging the context actively erases the signal a tight crop
  preserves for this tiny footprint.
- **`t00039667`** (17.1m², parallax index case): `gemini-3.1-flash-lite`/
  production exactly reproduces the original contradiction (absent
  2019-2023, present 2024-02/03, absent again 2025-03-30). `gemini-3-flash`/
  production moves the transition earlier (`2023-05-30` now present) *and*
  resolves the post-census contradiction (`2025-03-30` flips to present
  too). `gemini-3.1-flash-lite`/enlarged independently does something
  similar. But `gemini-3-flash`/enlarged and `gemini-3-flash-agent`/enlarged
  go the other way — they **lose** the `2024-02/03` presence entirely
  (flip to ambiguous/absent). No single arm is uniformly best here.
- **`t00038450`** (18.11m², terrain/occlusion index case): `flash-lite`
  reproduces baseline (confident absent, 0.9-1.0, "usable") under both
  crops — no change. `gemini-3-flash`/`gemini-3-flash-agent` flip to
  `pv_present=None` / `quality_flag=ambiguous`-or-`unusable` at every frame
  under **both** crops — refusing to call confident absence at all, matching
  the human high-zoom re-check in the QA-sample memo (§11).

Raw results: `$OUT/step0/step0_results.csv`, per-call audit JSONL in
`$OUT/step0/*.audit.jsonl`.

## 1. Fingerprint rebuild (read-only, from scan states)

`scripts/temporal/rescan_pilot_fingerprints.py` re-derives the parallax
audit's F1/F3 populations directly from the 41,393 scan states (no CSV
join needed — `census_date` is stored per-anchor in each scan_state.json).

| fingerprint | audit's count | this rebuild | match |
|---|---:|---:|---:|
| F1-decision-critical (pre-census present->absent contradiction) | 1,777 | 1,777 | exact |
| F3-late-transition (>=365d pre-census absent run, transition <=12mo of census) | 948 | 930 | 98.1% (see note) |

Note: F3's 18-row gap is most likely a slightly different "long run" span
definition (this rebuild measures first-absent-to-last-absent span within
the pre-census confident-absent set; the audit's "generous" definition may
differ at the margin) — close enough that the sampling frame is materially
the same population.

## 2. CRITICAL MID-FLIGHT FINDING: stale `target_offset_x_m`/`y_m` (added arm A5)

Confirmed mid-pilot (owner + team): `anchors_all.csv`'s
`target_offset_x_m`/`target_offset_y_m` was computed against the **legacy
group-chip center** (`build_inventory_chip_groups.py`), but the rebuilt
per-target 96m chips are actually centered on **each target's own
centroid**. The true offset is therefore ~(0,0), and
`run_adaptive_scan.py`'s `make_fixed_extent_review_renderer`
(`scripts/temporal/run_adaptive_scan.py:479-484`) fed the stale value into
the marker position AND the crop center of every production review PNG —
i.e. for any anchor with a large stale offset, Gemini was shown a crop
centered meters away from the target, with the marker crosshair drawn at
the wrong spot inside it.

Independently re-derived the corpus-wide scale from `anchors_all.csv` +
scan states (no Gemini calls, pure local computation):

| population | n | note |
|---|---:|---|
| `\|offset\|>12m` (all statuses) | 19,850 / 41,393 (48.0%) | |
| `\|offset\|>12m`, chip_arm=A24 only | 17,793 / 36,322 A24 anchors (49.0%) | matches team's independently-reported ~49% |
| `\|offset\|>12m` AND `status=done_installed_during_census` AND >=1 confident pre-census absent frame ("large-offset census core") | 15,882 | the population §4 samples from |
| `\|offset\|>6m` (all statuses) | 28,353 / 41,393 (68.5%) | the broader rollout candidate population (§6) |

**Added arm A5**: `gemini-3.1-flash-lite`, **same crop SIZE as production**
(`review_extent_m`, not enlarged), but the marker/crop center's
`offset_x_m`/`offset_y_m` zeroed (`dataclasses.replace(marker, offset_x_m=0,
offset_y_m=0)`) — isolates the placement fix from the enlargement mechanism.
This matters because A1 (2x extent) mechanically covers offsets up to half
the enlarged extent (24m covers offsets up to 12m) even with the crop
mis-centered, which is a *different, weaker* mechanism than actually
recentering — any A1 lift on large-offset anchors should be read as partial,
accidental offset compensation, not a genuine resolution/context effect.

## 3. Pre-registered mini-pilot: 150-anchor F1/F3 sample (arms A0-A3, A5)

Fixed seed `20260717`, `random.Random(20260717).sample(...)` per stratum:
**100 F1-critical + 50 F3-late-transition = 150 anchors**
(`$OUT/sample/manifest.csv`). Each anchor is re-scored on its full deduped
frame history (all rounds, later round wins on same date) — matches
production's batch-call shape exactly (`gemini_max_dates_per_call=5`
chunking, same census-calibration suffix). Total: 750 (anchor, arm) rows,
0 errors, 267 chunk calls per arm (~1,335 batch calls total across 5 arms),
~1,159 image-scores per arm (mean 7.73 frames/anchor).

Arms:

| arm | model | crop | prompt |
|---|---|---|---|
| A0 baseline replay | gemini-3.1-flash-lite | production | unchanged |
| A1 enlarged context | gemini-3.1-flash-lite | 2x review extent | unchanged |
| A2 parallax-aware prompt | gemini-3.1-flash-lite | production | + parallax addendum (locate the building/roof across frames, not the marker's pixel coordinates; flag ambiguous rather than confident-absent if the building can't be found) |
| A3 model swap | gemini-3-flash | production | unchanged |
| A5 offset fix (added mid-flight, §2) | gemini-3.1-flash-lite | production size, offset zeroed | unchanged |

Interval recomputation reuses `infer_install_dates.infer_one` against a
deep-copied `ScanState` with re-scored frames patched in; status
re-derivation is a simplified re-implementation of `scan_decision.py`'s
terminal Case A/B/C/D logic restricted to pre-census evidence (Case E/R's
failure-rate and anchor-recovery-round machinery are not re-simulated,
since this pilot re-scores existing frames only and never plans new
rounds — a known simplification, not a full adaptive-scan re-run).

**A4 (combo arm) was pre-registered but not run.** Once A5 turned out to
dominate every other arm's effect size by roughly an order of magnitude
(§3.1-3.2, §4) and to be essentially free (same model/crop-cost as
baseline), the decision-relevant question shifted from "which prompt/model
tweak helps" to "fix the placement bug first, re-evaluate the others after"
— spending more of the call budget on a `gemini-3-flash` + enlarged +
prompt combo whose two more-expensive ingredients (§3.1: A3 is ~3x slower,
A1 is mechanically confounded with offset per §2) looked unlikely to beat
A5 alone was deprioritized in favor of delivering the offset-fix number
(§4) the owner's rollout decision is waiting on. Flagged as a natural
follow-up once A5(+A2) is in production and residual failures are
characterized.

### 3.1 Per-arm summary (150-anchor F1/F3 sample)

| arm | model | crop | prompt | n | flip_rate¹ | status_changed_rate | bracket_changed_rate² | post_census_agreement³ | total_elapsed_sec | mean_elapsed_sec/anchor |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| A0 | gemini-3.1-flash-lite | production | prod | 150 | 17.3% | 56.0% | 56.7% | 0.727 | 1,093 | 7.3 |
| A1 | gemini-3.1-flash-lite | enlarged | prod | 150 | 22.0% | 65.3% | 64.0% | 0.720 | 1,272 | 8.5 |
| A2 | gemini-3.1-flash-lite | production | parallax | 150 | 27.3% | 52.7% | 50.7% | 0.760 | 1,010 | 6.7 |
| A3 | gemini-3-flash | production | prod | 150 | 22.0% | 63.3% | 63.3% | **0.447** | 3,055 | 20.4 |
| A5 | gemini-3.1-flash-lite | recentered | prod | 150 | **41.3%** | 52.0% | 63.3% | 0.693 | 1,084 | 7.2 |

¹ Flip rate = share of anchors whose contradiction frame (F1's `absent_date`
or F3's `last_absent_date`) flips absent->present under that arm.
² Bracket-changed is noisy on its own (a status-preserving re-score can
still nudge `latest_absent_date` by one frame); read alongside
status_changed_rate, not instead of it.
³ Mean agreement between the arm's verdict and the ORIGINAL production
verdict on post-census reference frames (frames dated >= census_date) —
these frames aren't part of the decision evidence, so high agreement here
is a sanity check that an arm isn't just randomly relabeling everything,
not a correctness measure by itself.

**A0's 17.3% flip rate is real stochastic noise, not zero** — this F1/F3
population is selected precisely because it already contains a borderline
contradiction, so even an identical replay re-rolls it close to 1-in-6 of
the time. Every other arm's flip rate should be read net of this floor, not
at face value:

| arm | flip rate | net lift over A0 baseline |
|---|---:|---:|
| A1 (enlarged) | 22.0% | +4.7pp |
| A2 (parallax prompt) | 27.3% | +10.0pp |
| A3 (model swap) | 22.0% | +4.7pp |
| A5 (offset fix) | 41.3% | **+24.0pp** |

A5's net lift is 2.4-5x every other single arm's, on a population that
under-samples exactly the anchors it should help most (see the caveat at
the top of this memo).

**A3's post-census agreement (0.447) is the one number in this table that
reads as a yellow flag, not a green one** — `gemini-3-flash` disagrees with
the original production verdict on nearly 6 in 10 post-census reference
frames, which are frames the model shouldn't need "fixing" (they're recent,
well-lit, and were the calibration reference in production's own prompt).
Step 0 showed `gemini-3-flash` sometimes catches genuine misses and
sometimes over-calls PV that isn't clearly there; this pilot cannot
distinguish those cases at scale, so A3's true value is unresolved — treat
its flip-rate lift as unvalidated until a human spot-check of a
disagreement sample confirms which direction it's erring.

### 3.2 Per-fingerprint flip rate

| arm | fingerprint | n | flip_rate |
|---|---|---:|---:|
| A0 | F1_critical | 100 | 22.0% |
| A0 | F3_late_transition | 50 | 8.0% |
| A1 | F1_critical | 100 | 22.0% |
| A1 | F3_late_transition | 50 | 22.0% |
| A2 | F1_critical | 100 | 34.0% |
| A2 | F3_late_transition | 50 | 14.0% |
| A3 | F1_critical | 100 | 21.0% |
| A3 | F3_late_transition | 50 | 24.0% |
| A5 | F1_critical | 100 | 39.0% |
| A5 | F3_late_transition | 50 | **46.0%** |

A5's lift is dramatically larger on F3 (late-transition-near-census
suspects, net +38pp over A0's 8.0% floor) than on F1 (net +17pp over A0's
22.0% floor) — consistent with the offset bug's mechanism: a stale crop
center is most damaging exactly when the true transition is close to the
census date (the marker has had the least chance to still be "close enough"
on an earlier, lower-resolution capture) and least visible in F1's simpler
present-then-absent contradictions, which can also arise from pure
per-frame classifier noise (parallax audit §3.5's "non-geometric noise"
failure mode) that an offset fix does nothing for.

### 3.3 A4 combo

Not run — see the note at the end of §3 above.

### 3.4 Cost / latency

`gemini-3.1-flash-lite` arms (A0, A1, A2, A5) average 6.7-8.5s/anchor
serially; `gemini-3-flash` (A3) averages **20.4s/anchor, ~3x slower**. All
arms issue the same 267 chunk calls per 150 anchors (1.78 calls/anchor mean)
since chunking depends only on frame count, not model.

## 4. HEADLINE NUMBER: offset-fix recovery rate on the large-offset census core

30-anchor draw (seed `20260718`, `scripts/temporal/rescan_pilot_sample_offset.py`)
from the 15,882-anchor "large-offset census core" (§2) — all originally
`status=done_installed_during_census`, all `|offset|>12m`. Paired A0
(baseline replay) vs A5 (offset zeroed), both `gemini-3.1-flash-lite`, same
crop size, same prompt. 60 rows, 0 errors.

| arm | contradiction-frame¹ flip rate (absent->present) | status changed from `done_installed_during_census` |
|---|---:|---:|
| A0 (baseline replay, stale offset) | **1/30 (3.3%)** | 2/30 (6.7%) |
| A5 (offset zeroed) | **14/30 (46.7%)** | 15/30 (50.0%) |

¹ Contradiction frame here = each anchor's own latest confident pre-census
absent observation (the frame right before the census cutoff — the one
most likely to gain a present read once the crop is correctly centered).

**Net recovery rate: 46.7% - 3.3% = ~43 percentage points** — an order of
magnitude above the A0 baseline noise floor, on the SAME anchors, SAME
model, SAME crop size, with only the marker/crop center corrected. This is
a large, clean, causally attributable effect, not noise.

A5's new-status breakdown on this population (n=30):

| new status | n | interpretation |
|---|---:|---|
| `done_installed_during_census` (unchanged) | 15 | genuinely no earlier-visible install even at the corrected location, OR still occluded/ambiguous below this pilot's resolution |
| `done_appears` | 11 | **recovered** — a real, dateable install-date bracket instead of a blanked "no later than census" placeholder |
| `done_ambiguous_nonmonotonic` | 3 | correctly punted to human review instead of silently mislabeled |
| `done_already_present_before_geid_history` | 1 | install predates the whole GEHI history |

**11/30 (36.7%) of this population goes from an undated, worst-case
"installed sometime before census" placeholder to an actual date bracket**
purely by fixing the crop center — no model or prompt change. A0's own
stability (28/30 unchanged, 93%) on the identical re-score confirms this
isn't re-roll noise.

## 5. Winner arm and recommendation

**A5 (offset correction) is the clear, unambiguous primary fix.** It is:
- the single largest effect measured in this pilot on every population
  tested (2.4-5x the net lift of A1/A2/A3 on the F1/F3 sample; the only
  arm tested directly on the population it should help most, where it
  recovers a dateable status for over a third of anchors);
- effectively **free** — same model (`gemini-3.1-flash-lite`), same crop
  size, same prompt, same call volume as the status quo. This is a bug fix
  to the renderer, not a model or prompt upgrade with its own cost/latency
  tradeoff.

**A2 (parallax-aware prompt) is a reasonable free rider** to bundle with
A5: same model, same crop, a longer prompt with negligible extra token
cost, and it shows the second-largest net lift on the F1/F3 sample (+10.0pp,
concentrated on F1 specifically — see §3.2). **This pilot did not test
A5+A2 combined** (each arm was isolated); recommend a small (~50-100
anchor) combo validation before folding A2 into the full rollout, rather
than assuming the lifts stack additively.

**A1 (enlarged context) is NOT recommended for the rollout.** Its measured
lift is the smallest of the four alternative arms (+4.7pp net), it is
mechanically confounded with the offset bug (§2 — enlarging partially and
accidentally compensates for a mis-centered crop, which A5 now fixes
directly and completely), and Step 0 showed enlargement can actively
regress detection on small/tiny footprints. Once A5 ships, A1's rationale
mostly disappears.

**A3 (model swap to `gemini-3-flash`) should NOT go into the primary
rollout.** It costs ~3x the latency/compute of every flash-lite arm and its
post-census agreement rate (0.447) is a real unresolved red flag — this
pilot cannot tell whether its extra flips are genuine catches or
over-calls. Recommend reserving `gemini-3-flash` for a small, human-
validated secondary pass on anchors that remain `done_installed_during_census`
or otherwise unresolved after A5(+A2), where Step 0 showed it can see PV
flash-lite misses on tiny footprints specifically (not a general-purpose
upgrade).

**Recommended rollout: A5 alone first (mandatory — it is a bug fix), with
A2 bundled in after a quick combo-validation pass.**

## 6. Projected effect + cost of full rollout

Extrapolated from this pilot's measured per-anchor timing (§3.4, §4) — not
new measurements. Two candidate populations per the team's framing:

| population | n | mean frames/anchor¹ | images scored | batch/chunk calls² | serial wall-time | wall-time @ 8 workers³ |
|---|---:|---:|---:|---:|---:|---:|
| (a) large-offset census core (§2, `\|offset\|>12m` + `done_installed_during_census`) | 15,882 | 6.0 | ~95,300 | ~31,800 | ~27.2h | **~3.5-4h** |
| (b) full `\|offset\|>6m` set, all statuses | 28,353 | 6.5 | ~184,300 | ~56,700 | ~51h | **~6.5-9h** |

¹ (a) measured directly on this pilot's own 30-anchor offset-extra draw
(6.0 frames/anchor, matching the population-wide mean of 6.0 computed
independently from all 15,882 scan states); (b) is the population-wide mean
computed from all 28,353 matching scan states (no extra Gemini calls),
extrapolating this pilot's per-frame timing.
² At `gemini_max_dates_per_call=5` chunking (2 chunks/anchor empirically
for population (a); ~2.0 assumed for (b) given its slightly higher mean
frame count).
³ Linear scaling from this pilot's serial per-anchor timing at the
gateway's stated safe worker ceiling (8 workers). The theoretical qps floor
(4 qps, ~31,800/4 ≈ 2.2h for population (a)) is NOT the binding constraint
at 8 workers given ~6-7s/call latency (8 workers / 6.5s ≈ 1.2 req/s, well
under 4 qps) — 8-worker wall-time is latency-bound, not qps-bound, so these
are the realistic estimates, not the qps floor.

**Recommendation: start with population (a), the 15,882-anchor large-
offset census core** — it is exactly the population §4 measured (46.7%
flip rate, 36.7% status recovery to a dateable bracket), the single
largest concentration of the parallax audit's flagged "confirmed present,
not bounded" risk (parent memo §4c), and completes in under half a day at
safe gateway pace. Population (b) is a natural second wave once (a) is
validated in production — it costs roughly 1.8x population (a)'s call
volume for a broader, more heterogeneous population this pilot did not
sample directly (it mixes `done_appears`/`already_present`/etc., which are
"comparatively the most trustworthy" per the parent parallax audit and were
not shown here to need the fix as urgently as the census bucket).

At A5's flip/recovery rate holding at the pilot's measured level, re-
scanning population (a) alone would be expected to convert roughly
**5,800 of the 15,882 anchors (36.7%) from an undated `done_installed_during_census`
placeholder to a real dateable bracket** — the single highest-leverage,
lowest-cost action available on this corpus before any further model or
prompt tuning.
