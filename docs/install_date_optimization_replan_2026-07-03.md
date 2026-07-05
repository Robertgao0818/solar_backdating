# Install-Date Optimization Replan v2 — 2026-07-03

Status: PROPOSED (supersedes the "Path 1 vs Path 2" framing of late June)
Provenance: 9-agent fact-check + exploration workflow run 2026-07-03 (5 fact-check
clusters against code/artifacts, 4 exploration lenses). Full structured output:
session workflow `wf_919a4155-86f`. Core project values unchanged: **data
accuracy + reproducibility**.

The two paths under consideration were:

- **Path 1** — kill the adaptive search; Gemini scores the complete vintage stack
  (`scripts/validation/fullstack_noscan_*.py`, experiment 2026-06-30).
- **Path 2** — swap the scorer backbone to frozen DINOv3-L-SAT distilled from
  Gemini (`docs/dinov3_sat_scorer_backbone_prd.md` + `docs/dinov3_scorer/ISSUE-01..08`).

The fact-check materially changes what each path is worth. This replan reframes
the work as **five layered phases** instead of a two-way choice.

---

## 1. Corrections register (what the fact-check changed)

Numbers below are from artifacts, not docs. Artifact roots:
`~/zasolar_data/geid_temporal/fullstack_noscan_20260630/`,
`~/zasolar_data/geid_temporal/mini_reliability_20260624/`,
`~/zasolar_data/geid_temporal/llm_endtoend_20260623/`.

### C1 — Path 1's headline gain is an *estimator* swap, not search removal

`summary.json` `paired_vs_armA`: on the **same FPD estimator**, full-stack
no-search scores **0.807 vs Arm A's 0.875 (delta −0.068)** — removing the search
alone is *worse* than the frozen-path baseline. The 0.911 belongs to the new
"sustained" estimator; 0.875/0.771 baselines were measured on FPD. The
0.436→0.046 undated-flip headline picks the worst arm as baseline (Arm A is
0.171→0.046, 3.7×, still real). **Implication: the estimator is separable from
the pipeline shape and is the cheap lever.** A sustained-style (or better)
estimator can be applied to adaptive-scan output too.

### C2 — The headline numbers don't survive inventory weighting

The 28-unit sample is equal-allocation over 6 `done_*` strata (2 anchors each),
but the population is 69% `done_appears` (10,930/15,859). All `summary.json`
overall means are **unweighted**. Inventory-weighted: sustained date mode-hit
**0.911 → ~0.80**, sustained year mode-hit **0.857 → ~0.47**, because
`done_appears` has sustained year-hit **0.30 vs FPD's 0.65** in that stratum —
estimated from **n=2 units**. Sustained trades date-stability for
year-instability on the population-dominant case, and the evidence base for the
dominant stratum is 2 anchors. **No cohort-wide estimator decision can be made
from this sample.**

### C3 — Sustained systematically over-dates

All **15/15** dated↔undated disagreements with production go one way
(full-stack dates what production left undated; 0 reverse;
`validity_per_unit.csv`). Favorable reading: it recovers search give-ups
(concentrated in `gemini_failed`/`installed_during_census`). Unfavorable
reading: it confidently dates anchors on evidence production abstained on, with
no truth to arbitrate. Plus the known semantic fork (`t00005236-40`: sustained
picks last sustained onset → 2024; production first-present → 2018).
**Only an external accuracy channel (Phase 2) can resolve this.**

### C4 — Path 1 pure is quota-infeasible at scale, and cost was never the bottleneck

Full-stack = mean **14.4 Gemini calls / 112 frames per anchor** vs production
adaptive's **2.38 calls / ~11 frames** (~6× calls, ~10× frames). There is no
per-call dollar price — the gateway is subscription-quota (≈3 accounts × 10
slots, prod qps 8), so 6× calls = 6× quota pressure; CT projection ~615k calls.
Meanwhile the **actual production wall-clock bottleneck was GEHI availability
catalog calls timing out at 300 s** (`run_8w_stdin_stall.log`), which neither
path touches. The "11-13 vintages/anchor" planning figure conflates *scored*
vintages with the drivable stack (**72–141/anchor**).

### C5 — Version drift is currently un-auditable, and no fix has shipped

`RoundResult` (`scripts/temporal/scan_state.py`) records **no model identity, no
prompt hash, no chip content hash**; the model id is resolved from env at run
time and discarded. Retroactive drift audit of the retained scans is impossible.
None of the repro study's recommended fixes (consensus, censoring estimator)
have landed in production — production still ships single-pass dates.

### C6 — Path 2's PRD rests on several wrong or untested inputs

- **"26,820 retained scan states" is wrong**: on disk = 15,859 chip-group +
  7,288 per-target + 11,419 census2023 + 1,641 wayback; **23,147 unique retained
  anchors / 250,502 rounds**. ISSUE-02 must re-derive its sizing.
- `scan_state.json` **lacks anchor coordinates**; chip re-render depends on
  `chip_targets.csv` (41,394 rows, intact) — an unnamed hard dependency.
- **27.6%** of retained anchors (6,399/23,147) are `done_ambiguous_*` — a much
  bigger bite out of the harvestable label pool than the PRD implies.
- The Q1 GSD-domain-match claim (0.25 m ≈ SAT's 0.6 m pretrain) is **untested**;
  the zerov2 ablation designed to isolate GSD-vs-domain-vs-freeze never ran.
- DINOv3 weights are ungated but under a **custom non-permissive Meta license**.
- `pv_score` does not exist in `scan_state.json` — the field is `confidence`.
  `QUALITY_FLAGS`/`DECISION_SOURCES` enums are declared but never enforced.
- ISSUE-01 undercounts the seam: **four** scorer call-sites exist
  (`run_adaptive_scan.py:384`, `run_census2023_scan.py:347`,
  `fullstack_noscan_run.py`, `score_chip_group_matrix.py:374`), and Case-E
  hardcodes the `"gemini_failed"` sentinel (`scan_decision.py:115`).

### C7 — The repro study's own numbers need re-derivation

The headline rep↔rep 0.74 exists only in `derived_cuts.json`, which **no script
writes** (hand-authored). The agreement metric counts both-UNDATED as a hit over
all 642 installations including deliberately-ambiguous strata (blends
`done_appears` 0.74 with `done_already_present` 0.038). L2>L3>L1 is an
**ordering, not an additive variance partition** (different tests, different
scales, L1/L2 entangled via abstain-triggered branching).

### Standing evaluation rules (adopt immediately, all future comparisons)

1. **Like-for-like estimators only** — never compare estimator A on pipeline X
   against estimator B on pipeline Y as a pipeline claim.
2. **Inventory-weighted metrics are the headline**; unweighted stratified means
   are diagnostics. Report both.
3. **Dated-only denominator** reported alongside the all-units agreement number.
4. Baselines: always report vs **Arm A** (best frozen arm), not only Arm B.
5. Minimum stratum support: no cohort decision off n=2 units in a 69% stratum.

---

## 2. Reframed problem

Three separable problems, mapped to what actually fixes them:

| Problem | Dominant evidence | Fixed by |
|---|---|---|
| Run-to-run variance (L2 search + estimator) | C1/C2: estimator swap moves +0.104 on frozen frames; fixed grid moves +0.036 (FPD, vs Arm B) | **Phase 0** (decoder) + **Phase 4** (fixed-grid shape) |
| Version drift + provenance | C5: model id discarded, no content hash | **Phase 1** (verdict store), later Phase 3 (frozen student) |
| Accuracy unknowable (teacher-circular metrics) | C3: 15/15 one-way disagreements, no arbiter | **Phase 2** (CoJ true-date audit + gold set) |

Path 1 pure (6× quota per re-run, drift re-exposed every run) and Path 2 pure
(fidelity-capped, leaves L2/L3 untouched by its own scoping note) are both
rejected **as standalone endgames**; their components are re-used below.

---

## 3. The plan

### Phase 0 — Estimator: monotone changepoint posterior + cohort survival prior
*(~1 week, zero API spend, all on banked data)*

Two exploration lenses independently converged here: formalize "sustained" into
a principled decoder.

- **P0a — Exact monotone changepoint posterior** per anchor: latent monotone
  absent→present step, changepoint τ ∈ {1..T+1} (T+1 = right-censored /
  "undated" becomes P(τ>T), not a status cliff). Exact O(T) enumeration — no HMM
  machinery needed. Emission = 3-symbol confusion matrix
  (present/absent/abstain | state), estimated cohort-wide by EM, stratified by
  quality_flag/zoom/imagery era. Collapse near-duplicate vintages into epochs
  (median gap 31 d; 42.5% of gaps ≤30 d) to avoid double-counting correlated
  errors. Consumes `long_all.csv` or production `scan_state.json` **as-is**.
  Emits: posterior over install epoch, MAP interval, HPD credible interval,
  P(undated). Sustained is the MAP under symmetric noise + flat prior — this
  strictly generalizes it. Subsumes dip-repair + the status case machine + the
  sustained heuristic in one deterministic likelihood.
- **P0b — Turnbull NPMLE / discrete-time hazard** over the cohort's censoring
  intervals (consumes `install_intervals.csv` as-is). Recovers the **23%
  `done_ambiguous_*` anchors** as censored observations instead of blank rows.
  The fitted hazard is the empirical-Bayes prior π(k) for P0a. Grid cells get
  expected counts by summing posterior mass (fractional counting) instead of
  midpoint imputation on median-475-day intervals. This is the event study's own
  action #1 + #4b.
- **P0c — PAVA / isotonic floor** as the mandatory cheap falsification baseline.

**Gates (all GT-free, on banked 10-rep × 28-unit panel + 3 end-to-end reps):**
MAP-interval mode-hit ≥ 0.911 and undated-flip ≤ 0.046 (beat sustained on its
own turf); HPD calibration proxy (rep-i's 90% interval contains rep-j's MAP
~90%); year-TVD within the 0.037–0.063 band; per-stratum no-worse than FPD on
`done_appears` year-stability (the sustained failure mode, C2); survival-curve
rep-to-rep TVD beats point-date TVD.

> **Correction (2026-07-04, record hygiene — superseded by the ISSUE-04
> re-anchor):** the `0.046` undated-flip figure is FPD's banked value, not
> sustained's; `fullstack_noscan_analyze.py`'s original banked table printed
> one shared flip field under both estimators' rows (bug also inherited by
> `panel_repair_d8_compare.py`, see the ISSUE-04 memo's
> [Correction](replan_v2/ISSUE-04-decision-memo-2026-07-03.md#correction-2026-07-04)).
> Sustained's true banked undated-flip is **0.075**. Live gate: the
> ISSUE-04-reanchored extended-panel bar (undated-flip ≤0.055, pass-at-parity).

**Deliverable**: credible intervals as a first-class product for the economic
event study — something point dates cannot give.

**Sample repair (small targeted API spend, decision gate for everything
downstream):** enlarge `done_appears` from n=2 to ~8–10 units in the
reliability panel before any cohort-wide estimator claim (C2). Piggyback the
crop-floor test already proposed (re-score the 8 `gemini_failed` units at 12 m /
256 px crop) — if they date cleanly, that stratum's noise is an under-resolution
artifact, which changes both the decoder's emission model and Phase 3's chip
spec.

### Phase 1 — Provenance + content-addressed verdict store
*(~3–5 days, the single highest reproducibility-per-effort move)*

- Record **scorer identity (model string), prompt/config hash, and chip content
  hash (sha256 of PNG bytes)** for every scoring call. Sidecar store first (no
  `SPEC_VERSION` bump); key = (chip_sha256 × scorer_identity × prompt_hash).
- Every scorer call behind the seam consults the store; re-runs pay only
  never-seen pairs. **A cached verdict can never drift** — this delivers the
  PRD's primary version-drift goal *before the student exists*, and makes any
  future multi-rep protocol ~free after rep 1.
- Content-hash keying (not (date,version) metadata) is the correctness
  condition — GEHI re-renders shift pixels under stable metadata.
- Churn monitor: hash-churn rate on a fixed sentinel chip set; churned frames
  become an escalation class.
- Fold in the ISSUE-01 scope corrections (C6): the seam must cover **all four**
  call-sites; Case-E's `"gemini_failed"` sentinel becomes scorer-parameterized;
  fix `pv_score`→`confidence` naming in PRD/issues; enforce the declared enums.

**Gate**: replay a completed cohort slice through the store → byte-identical
`scan_state.json`.

### Phase 2 — Accuracy channel: CoJ true-date audit + transition-window gold set
*(~2 weeks elapsed, mostly automated; the arbiter both paths lack)*

- **P2a — CoJ ArcGIS true-date bracketing audit (automated, cohort-scale, zero
  API).** The CoJ server holds true single-flight-date aerials
  (2000/03/06/09/12/15/**2019**/**2023**, 15 cm for recent layers), fetchable
  via existing `scripts/imagery/_arcgis_fetch.py`. For all ~11.8k dated JHB
  anchors: fetch 2019+2023 anchor-centered chips, score with the in-domain
  census detector/CLS (15 cm is in-domain, unlike z=19 GEHI), emit two
  independent **dated presence bits** per anchor → cohort-wide contradiction
  rate against inferred intervals. 2019/2023 brackets the load-shedding boom
  where most installs fall; median interval width 475 d means one 2023 bit can
  split or falsify a large share of intervals. Self-gating via known-sign
  strata + monotone consistency with the Vexcel-2024 clamp.
- **P2b — Human gold set (n=300–500, transition-window only).** Review only the
  `latest_absent`/`earliest_present` frames ±1–2 flanks + CoJ/Vexcel/Wayback
  overlays, in an extended `build_phase0_qa_html.py` strip UI. Verdicts:
  CONFIRM / SHIFT(corrected bracket) / UNDATABLE. Stratify by status ×
  confidence × P2a-contradiction flag; double-annotate 20%. ~15–20 person-hours
  total. Yields a **Wilson 95% CI of ±3.5–5.5 pp on interval-hit-rate** — enough
  to separate estimator variants ≥6–10 pp apart, and to finally arbitrate C3
  (are sustained's 15 recovered dates right?).
- Claims must be phrased as **first-visible-appearance** accuracy (censored by
  imagery cadence), not physical install date.
- SSEG `date_commissioned`: CT-only aggregate prior for the future CT run; not
  JHB. Permits/City Power/foreign academic datasets: recorded dead ends.

### Phase 3 — Student distillation (Path 2, corrected inputs)
*(the PRD's ISSUE-02..06, sequenced after Phases 0–2, with amendments)*

Keep: frozen backbone + light head, distill from Gemini labels, DINOv2-S
falsification floor, fidelity gate, anchor-split. Amend per fact-check:

- Re-derive cohort/label-pool sizing from **23,147 anchors / 250,502 rounds**;
  name `chip_targets.csv` as a hard dependency; plan explicitly for the 27.6%
  `done_ambiguous_*` fraction (C6).
- Add the **DINOv3 license review** as a task; decide compute (local RTX 4070
  8 GB is plausibly sufficient for frozen-backbone head training — verify — else
  RunPod per repo rules).
- Fidelity gate baselines updated to Phase-0's decoder (not raw sustained), on
  inventory-weighted metrics (standing rules).
- Run **co-teacher dual-scoring on the ~1k-anchor training cohort** as the
  calibration instrument: the student-teacher disagreement distribution per
  stratum/GSD-tier sets Phase 4's abstain band and escalation k for free.
- The GSD-domain-match hypothesis (C6) gets tested *implicitly and cheaply* by
  the mandated DINOv2-S floor — if 22M non-SAT matches 303M SAT at 0.25 m, the
  SAT bet is falsified at gate time; no separate ablation needed before then.

### Phase 4 — Pipeline shape migration: E → A → D
*(the endgame; each step ships value alone and is separately gated)*

- **P4-E — Student as searcher, teacher as scorer** (lowest-risk first
  production win): student pre-scores the full stack; a **fixed deterministic
  rule** picks the 2–3 windows bracketing the coarse transition; Gemini scores
  exactly those. Kills L2 (window choice becomes a pure function of frozen
  student × frozen stack) at ~today's call volume (~32–48k vs 36.5k), with
  **zero trust in student verdicts** — a rough head suffices, so this can ship
  before the full fidelity gate. Gate: rerun the mini-reliability protocol with
  arm B′ = student-picked windows; B′ rep↔rep ≥ Arm A 0.875, undated-flip
  ≤ 0.171.
- **P4-A — Bounded teacher adjudication**: student owns steady-state frames;
  teacher scores only (i) abstain-band frames, (ii) ±k frames around the
  detected transition, (iii) anomaly patterns — a pure function of student
  scores, so call count is enumerable up front (~16–32k cached calls per cohort,
  ≤ today's volume; expected escalation 7–18% per `per_frame_flip.csv`).
  Escalation-rate monitor doubles as a GT-free student-miscalibration alarm.
- **P4-D — Teacher decays to sentinel**: frozen student scores everything;
  a fixed stratified sentinel cohort (~228 chips, ~3.6k calls) is re-scored by
  current Gemini on a schedule; since the student is frozen, agreement drift
  isolates teacher/imagery drift. Human queue retained for high-value anchors.
- **Prerequisite workstream — GEHI throughput** (C4): full-stack downloads are
  the real new cost (~2.0M chip renders at JHB scale vs production's ~173k) and
  the availability-catalog 300 s timeouts are the known bottleneck. Build an
  availability-catalog cache (per tile/region, refreshed on schedule) before any
  full-stack cohort run; disk budget per `wsl_disk_hygiene` (~188 GB CT
  projection is a hard blocker → stage + delete, keep hashes).

Final architecture: **student-first fixed-grid scoring, bounded teacher
adjudication over a content-addressed verdict store, teacher decaying to
sentinel** — kills L2 by construction, kills drift twice (frozen student +
immutable cached teacher verdicts), and cuts hosted calls below today's volume
on first run and to ~zero on re-runs.

---

## 4. Rejected / deferred (with reasons, to stop re-investigation)

| Item | Verdict | Reason |
|---|---|---|
| Path 1 pure (Gemini full-stack in production) | REJECTED | 6× quota per re-run, drift re-exposed every run; its reproducibility gain is mostly the estimator (C1), which Phase 0 captures for free |
| Path 2 pure as immediate next build | REJECTED as standalone | Leaves L2/L3 untouched (PRD's own scoping note); inputs corrected in C6; becomes Phase 3 inside the composed plan |
| BOCPD | REJECTED | Wrong tool for single monotone changepoint on 11–141-frame sequences; P0a is exact and simpler |
| Bi-temporal consecutive-pair change scoring | REJECTED | Registration-noise-dominated for ~67 px targets; no distillation teacher for the pair task |
| SITS / temporal remote-sensing FMs (Presto, Prithvi, Clay…) | REJECTED | Fatal GSD/band/cadence mismatch with z=19 RGB stacks |
| Per-frame K-rep consensus voting at cohort scale | DEFERRED | Obsolete once the frozen student makes per-frame scoring deterministic; verdict store makes reps ~free anyway |
| Co-teacher dual-scoring as production shape | REJECTED (kept as calibration instrument) | Teacher still scores 100% of frames — fails the cost goal outright |
| Building permits / City Power records; BDAPPV/LBNL/UK-FiT datasets; GE Timelapse | DEAD ENDS | No SA coverage / no dated PV labels transferable to JHB |
| SSEG for JHB accuracy | DEFERRED to CT | CT-only, lag-biased; aggregate prior only |

---

## 5. Decision points for the owner

1. **Phase-0 sample repair budget**: enlarging `done_appears` to 8–10 units +
   crop-floor rescore ≈ a few thousand flash calls. Approve?
2. **Phase ordering**: Phases 0–2 are independent and can run concurrently
   (0 = decoder on banked data; 1 = infra; 2a = CoJ audit, zero API). Phase 3
   starts after 0+1 land (gate baselines + seam). Agree, or serialize?
3. **Gold-set annotation time** (~15–20 h human): schedule when?
4. Phase 3 compute: verify local 4070 first, or go straight to RunPod?

## 6. Gate summary

| Phase | Gate | Bar |
|---|---|---|
| 0 | decoder vs sustained on banked panel (weighted + per-stratum) | ≥0.911 mode-hit, ≤0.046¹ undated-flip, `done_appears` year ≥ FPD, HPD coverage ~nominal, TVD in band |
| 1 | replay test | byte-identical scan_state.json from cache |
| 2a | CoJ audit self-gates | known-sign strata >95%, Vexcel monotone consistency |
| 2b | gold set quality | inter-annotator ≥ agreed floor on 20% double-annotated |
| 3 | fidelity gate (ISSUE-06 amended) | self rep↔rep ≈1.0; vs-Gemini interval agreement ≈ teacher ceiling; DINOv2-S floor not matching L-SAT |
| 4-E | mini-reliability rerun, arm B′ | rep↔rep ≥0.875, undated-flip ≤0.171 |
| 4-A | hybrid vs Gemini-full-stack on 28-unit panel | agreement in 0.911 band, undated-flip ≤0.05¹, escalation 7–18% |
| 4-D | sentinel time series | agreement drift bounded; TVD vs frozen production in band |

¹ **Correction (2026-07-04, record hygiene):** both the row-0 `≤0.046` and the
row-4-A `≤0.05` bars were set from `fullstack_noscan_analyze.py`'s original
banked-panel table, which printed one shared undated-flip field under both
the FPD and sustained rows (0.046 is FPD's; the bug is also in
`panel_repair_d8_compare.py`, see the ISSUE-04 memo's
[Correction](replan_v2/ISSUE-04-decision-memo-2026-07-03.md#correction-2026-07-04)).
Sustained's true banked undated-flip is **0.075**. Both bars are superseded by
the ISSUE-04-reanchored extended-panel bar (undated-flip ≤0.055,
pass-at-parity).
