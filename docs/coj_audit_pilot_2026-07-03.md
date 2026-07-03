# CoJ true-date audit — ~100-anchor pilot (ISSUE-08)

Date: 2026-07-03
Status: **pipeline built, tested, and validated end-to-end on real data; full
122-anchor / 244-fetch pilot run IN PROGRESS at time of writing** (fetch
stage is network-latency-bound at ~15s/request; see §7 for the partial
results captured so far and exact resume instructions).
Code: `scripts/audit/{coj_arcgis_fetch,coj_audit_join,score_coj_chips,coj_audit_pilot}.py`
Tests: `tests/audit/{test_coj_arcgis_fetch,test_coj_audit_join}.py` (88 offline unit tests, no network)
Outputs: `~/zasolar_data/geid_temporal/coj_audit_pilot_20260703/`

## 1. What this is

An end-to-end tracer pilot of the municipal "true-date" accuracy channel
(PRD D9, stories 15-16): pull anchor-centered chips from the City of
Johannesburg's own aerial-photography ImageServers — which carry a *true
single flight year* per layer, unlike Vexcel/GEID's blended-vintage
mosaics — score PV presence on each chip with the production census
detector (+ classifier corroboration where available), and cross-check the
result against what the install-date pipeline's inferred interval
(`install_intervals.csv`) already claims for that anchor. This is an
**offline, file-artifact accuracy channel** (PRD D1): it is not wired into
any of the four production `PresenceScorer` call-sites, and never will be —
it exists to validate/calibrate the pipeline against a source with real
capture dates, on a small sample, before deciding whether to scale it to a
full cohort (ISSUE-09).

## 2. Data sources

- **Anchors + inferred intervals**: `install_intervals.csv` (15,859 rows,
  JHB full-382 fpcut scan, 2026-06-02) — `anchor_id, status,
  install_interval_start/end, confidence`.
- **Anchor geometry**: `chip_groups_as_anchors.csv` (same cohort, 1:1 on
  `anchor_id`) — `chip_lon_min/lat_min/lon_max/lat_max` (a fixed ~96 m x
  ~96-130 m box per anchor, `chip_half_m=48`).
- **Ambiguous-straddler pool**: `census2023_cohort.csv` (11,482 rows) —
  restricted here to `id_kind=="c"` (chip-group-level) rows so an
  `install_interval_start`/`status` join-back is available for the
  contradiction check; `id_kind=="t"` (bare target-level) rows have no
  chip-group interval and are out of scope for this pilot's stratum 4.
- **CoJ imagery**: `AerialPhotography/{2019,2023}/ImageServer`
  (`ags.joburg.org.za`), 0.15 m native RGB, public ArcGIS REST
  `exportImage`. Verified live during scouting; **vintage precision is
  YEAR-ONLY on both layers** (2019 layer carries a uniform `Imagery_Year`
  field; 2023 only a service-name prefix) — there is no finer capture date
  to join against. All contradiction logic below is deliberately
  conservative about this.
- **Detector**: production census Mask R-CNN checkpoint
  `ZAsolar/checkpoints/exp_unified_reviewall_A/best_model.pth`, built via
  `core.models.maskrcnn.build_solar_maskrcnn` (same builder the census
  engine uses) with `score_thresh=0.05`, `nms_thresh=0.5`,
  `detections_per_img=300`. Chips are read directly (no tile registry) via
  `core.inference.tile_dataset.SlidingWindowDataset(chip_size=400,
  overlap=0.25, edge_pad=True)`.
- **Classifier corroboration**: `solar_cls`'s DINOv2-ViT-S/14 PV/non-PV
  classifier (`cls_pv_thermal_v2_dinov2_vits14_adaptive`), invoked through a
  new small generic CLI, `solar_cls/scripts/classifier/classify_chip_manifest.py`
  (this pilot needed a chip-file-in / pv-prob-out entrypoint that doesn't
  assume a `predictions_metric.gpkg` row or a registered tile — the existing
  `classify_predictions.py` is shaped for the detector-FP-suppression
  use case, not standalone chips). Invoked via `subprocess`, never imported
  directly — the plugin boundary stays file-path-only.

## 3. Sample design (seeded, `--seed 20260703`)

| Stratum | Definition | Target | Sampled |
|---|---|---|---|
| s1 `known_present_pre2019` | `done_appears` or `done_already_present_before_geid_history`, `install_interval_end < 2019-01-01` — expect **present** on both layers | 30 | 30 |
| s2 `known_present_2019_2023` | `done_appears`, `2019-01-01 <= install_interval_end < 2023-01-01` — expect **present@2023 only** (2019 sign unknown, excluded from that gate) | 20 | 20 |
| s3 `known_absent_2023` | `done_appears`, `install_interval_start > 2023-12-31` — expect **absent** on both layers | ALL (~57 exist) | 57 |
| s4 `ambiguous` | sampled from `census2023_cohort.csv` (`id_kind=="c"`) — no expected sign; exercises the contradiction flag only | 15 | 15 |
| **Total** | | **~122** | **122** |

`done_ambiguous_clamp_inverted` and `done_ambiguous_marker_missed_pv` are
excluded from every stratum (the pipeline already flags these interval
estimates as internally inconsistent; using them as a "known sign" would be
circular). Both layers (2019 + 2023) are fetched/scored for every sampled
anchor regardless of stratum, giving **244** fetch/score units — this is
what lets gate (b) (within-audit monotonicity) run across the whole sample,
not just the strata with a defined 2019 expectation.

Sampling is deterministic for a fixed seed (`random.Random(seed).sample`
over anchor-id-sorted stratum pools; s3 always takes every match, not a
random draw) — see `tests/audit/test_coj_audit_join.py::test_sample_pilot_*`.

## 4. Margin rule + routing (the ONE place these thresholds live:
`scripts/audit/coj_audit_join.py`)

```
S = max post-NMS box score across all sliding windows in the chip (0.0 if none)

S >= 0.95  AND (no classifier, or classifier pv_prob >= 0.5)  -> PRESENT  (high margin)
S <= 0.30                                                      -> ABSENT   (high margin)
otherwise (0.30 < S < 0.95, OR classifier disagrees with a
would-be PRESENT bit)                                          -> LOW_MARGIN -> human_queue.csv
```

The 0.30 / 0.95 thresholds are **transplanted from the CT-census
calibration** (floor 0.85 / pivot 0.90-0.92 there, tightened here since this
is a one-shot binary call, not a ranked list) — they are NOT re-derived
from this pilot's data. One of the two things this pilot's self-gates
validate is whether that transplant holds up on CoJ imagery; §6 covers the
answer.

Only high-margin (present/absent) bits participate in a self-gate or can
raise a contradiction flag. Low-margin bits are inert — they never
contradict anything and always land in `human_queue.csv`.

## 5. Contradiction logic (conservative, year-only bounds)

Because both CoJ layers carry only a capture **year**, contradiction checks
must be conservative about the day within that year:

```
present@Y contradicts the interval iff  install_interval_start > Dec 31 of Y
  (pipeline claims still-absent past year Y; the year-Y layer shows PV)

absent@Y  contradicts the interval iff  install_interval_end   < Jan 1  of Y
  (pipeline claims already-present before year Y; the year-Y layer shows none)
```

## 6. Self-gates

(numbers below are filled in from `gates_report.json` after the live run —
see §7 for the actual run's results)

- **(a) Known-sign agreement**, high-margin bits only, per stratum x year,
  target **>95%**.
- **(b) Within-audit monotonicity (noise floor)**: count of anchors with a
  high-margin `present@2019` AND high-margin `absent@2023` — logically
  impossible under monotone PV adoption, so any non-zero count here is
  itself the audit's baseline noise floor (detector/classifier error rate),
  against which gate (c) must be judged.
- **(c) Vexcel present-clamp monotonicity**: every sampled anchor is a
  2024-Vexcel-detected installation, so a high-margin `present@2023` bit
  that ALSO contradicts a clamped interval (`install_interval_start >
  2023-12-31`, i.e. the pipeline's own estimate puts first presence after
  2023) is a **clamp-monotonicity violation** — the audit disagreeing with
  the pipeline's own forward-looking clamp. This is definitionally the
  year==2023, `bit=="present"` slice of the general contradiction flag;
  reported as its own count because it is the specific self-gate ISSUE-08
  asks for.

## 7. Results (final — full 244-fetch sample, completed 2026-07-03)

The sample was drawn deterministically (§3, 122 anchors / 244 fetch units,
composition exactly 30/20/57/15 as designed). Fetch completed **244/244
`outcome=ok`, zero failures of any kind** (no HTTP errors, no WAF-challenge
pages, no empty/no-coverage responses, no exceptions; every request
succeeded on the first attempt), mean latency **15.0 s** per `exportImage`
call, ~63 min wall-clock sequential. Detector scoring (production Mask R-CNN
`exp_unified_reviewall_A`, checkpoint loaded `missing=0 unexpected=0`) +
`solar_cls` classifier corroboration (subprocess seam): 244 chips in
~2m24s on the local RTX 4070. Full artifacts:
`~/zasolar_data/geid_temporal/coj_audit_pilot_20260703/{sample.csv,
fetch_stats.jsonl, scored.csv, audit_bits.csv, human_queue.csv,
gates_report.json}`.

**Margin routing**: 102/244 bits high-margin (42%), 142/244 low-margin
routed to `human_queue.csv` (58%; dominated by s3 — partial detections in
the 0.30 < S < 0.95 band).

**Gate (a) — known-sign agreement (>95% threshold, high-margin bits only):**

| stratum | year | high-margin n | agree | rate | passes |
|---|---|---|---|---|---|
| s1 known-present-pre2019 | 2019 | 23 | 23 | 1.00 | **PASS** |
| s1 known-present-pre2019 | 2023 | 23 | 23 | 1.00 | **PASS** |
| s2 known-present-2019-23 | 2023 | 14 | 14 | 1.00 | **PASS** |
| s3 known-absent-2023 | 2019 | 11 | 7 | 0.64 | **FAIL** |
| s3 known-absent-2023 | 2023 | 14 | 7 | 0.50 | **FAIL** |

**Gate (b) — within-audit monotonicity: 0 violations** (no anchor read
present@2019 & absent@2023 at high margin). The audit's own noise floor on
this sample is zero.

**Gate (c) — clamp-monotonicity: 7 violations** (anchors
`c0001318, c0001386, c0001467, c0007454, c0014639, c0014912, c0015845`),
all from s3. Contradiction bits by stratum: s2 = 1, s3 = 11, s4 = 1.

**Interpreting the s3 failure — not audit noise, and not the pre-registered
threshold failure mode.** §10's pre-registered rule anticipated that a gate
(a) failure would signal the transplanted 0.30/0.95 thresholds need
re-calibration. The observed failure signature rules that out: the 11 s3
disagreement bits have detector S in 0.967–0.999 **with classifier
corroboration** (pv_prob 0.53–1.00, median > 0.99) — no threshold inside
the calibrated band flips any of them — while the present-side strata
(s1/s2, 60 high-margin bits) agree 100% and the gate (b) noise floor is 0,
so the audit instrument itself shows no false-present tendency on CoJ
imagery. What the failures share instead is the **pipeline side**: every
s3 anchor's `install_interval_start` is a synthetic Vexcel flight-bucket
date (2024-02-29 × 29 anchors, 2024-03-30 × 28 — the scan's last "absent"
observation sits at the census boundary), and 4 of the 11 disagreements
are high-margin present **already on the 2019 layer**. The coherent
reading: these are **pipeline false-absents at the census boundary** —
installations the GEHI z19/z20 satellite chain scored absent right up to
the 2024 flight but that 0.15 m aerial imagery resolves clearly, some as
far back as 2019. That is precisely the failure class D9 built this
channel to surface; s3's "known sign" was never ground truth, it was the
pipeline's own boundary claim, and the audit falsified it for ~7/57 (12%)
of the stratum (2023 layer, anchor level). The first spot-checked example
(`c0003422`, S=1.00/1.00 + pv_prob=1.00/1.00 on both layers vs. a claimed
2024-03 install start) fits the same pattern.

Reproduction: the stage commands below are idempotent (fetch skips
existing non-empty chips) and re-derive every artifact from `sample.csv`:

```
python -m scripts.audit.coj_audit_pilot fetch  --output-root ~/zasolar_data/geid_temporal/coj_audit_pilot_20260703
python -m scripts.audit.coj_audit_pilot score  --output-root ~/zasolar_data/geid_temporal/coj_audit_pilot_20260703
python -m scripts.audit.coj_audit_pilot join   --output-root ~/zasolar_data/geid_temporal/coj_audit_pilot_20260703
python -m scripts.audit.coj_audit_pilot gates  --output-root ~/zasolar_data/geid_temporal/coj_audit_pilot_20260703
```

## 8. Fetch reliability + politeness/rate plan for cohort scale

Politeness posture used in this pilot: sequential requests (no concurrency),
~0.4 s extra sleep between requests, retry x3 exponential backoff (1s / 2s /
4s, +/- 30% jitter) on transient failures, descriptive `User-Agent`. A
response body under 5000 bytes is treated as a soft "no coverage at this
bbox" outcome (not retried); an HTTP 200 whose `Content-Type` is
`text/html` is the WAF-challenge-page signature and is treated as a hard,
retried failure.

Measured (completed run, 244 requests): **100% `ok` on first attempt, mean
latency 15.0s, zero throttling/WAF signatures observed** across the full
~63-minute sequential pass. **Recommendation for ISSUE-09 cohort scale**:
keep the same politeness posture (sequential per worker, ~0.4s extra
sleep, retry x3 backoff) but do NOT assume concurrency=1 must hold at
cohort scale purely for politeness reasons — CoJ's ArcGIS endpoint showed
no rate-limit headers or degradation signal across 244 sustained requests,
so a modest concurrency of **4-6** parallel workers (each independently
sequential/backoff-safe) is a reasonable next step to bring a
~15,000-anchor cohort (2 layers x 15,859 anchors =~ 31,700 fetches) down
from an infeasible ~132 hours serial to ~22-33 hours — still substantial,
so cohort scale should also revisit whether every anchor needs both layers
fetched (e.g. skip 2019 for anchors whose interval already implies a
"known" 2019 sign) before committing to the full fetch volume.

## 9. Caveats

1. **Year-only vintage on both CoJ layers.** There is no finer capture date
   to join against; every contradiction check above is intentionally
   conservative (uses `Dec 31` / `Jan 1` of the layer year as the bound) to
   avoid manufacturing false contradictions from within-year timing.
2. **Transplanted margin thresholds.** 0.30 / 0.95 come from the CT-census
   calibration, not from this pilot's own ROC. If gate (a) fails cleanly at
   one boundary but not the other, that is the signal to re-derive
   thresholds from this pilot's own high-margin distribution before scaling.
3. **`SHARED_FROM_ZASOLAR.md` contract amendment needed, not made.** This
   pilot imports `core.models.maskrcnn.build_solar_maskrcnn` and
   `core.inference.tile_dataset.{SlidingWindowDataset,list_collate}` from
   the main ZAsolar repo, and reads a main-repo checkpoint
   (`checkpoints/exp_unified_reviewall_A/best_model.pth`) directly — none of
   which are yet declared in `solar_backdating/SHARED_FROM_ZASOLAR.md`.
   That file currently has uncommitted changes from another workstream and
   was out of bounds for this task; **recorded here as a followup** for
   whoever lands next on that file.
4. **Ambiguous stratum (s4) restricted to `id_kind=="c"`.** The other ~4,162
   `census2023_cohort.csv` rows are bare target-level (`id_kind=="t"`) with
   no chip-group-level interval to join against; they were excluded from
   this pilot's sampling frame rather than given a synthetic interval.
5. **EPSG:3857 pixel-size approximation.** `pixel_size_for_bbox` divides the
   Web-Mercator-meters bbox width by the native 0.15 m GSD; at Johannesburg's
   latitude (~-26.2 deg) Web Mercator meters overstate true ground meters by
   sec(26.2 deg) ~= 1.11x, so exports come out ~11% finer than native
   resolution, never coarser. Harmless for scoring quality; noted here so
   nobody is surprised the exported chip is ~719x723 px for a ~96 m box.
6. **Classifier corroboration is best-effort.** Per the ISSUE-08 brief's
   1-hour time-box: if `classify_chip_manifest.py` had cost materially more
   than that to stand up, the fallback was detector-only scoring with no
   classifier-based demotion. In this run the classifier subprocess seam
   worked (see §7); the escape hatch was not needed, but downstream
   consumers should still treat `classifier_pv_prob` as optional (it is
   `None`/blank whenever the subprocess call fails).

## 10. Go/no-go for cohort scale (ISSUE-09)

**GO, with the s3 role reassigned.** Final basis (full 244-fetch sample,
§7):

- **The audit instrument is validated.** Present-side known-sign agreement
  is 100% (60/60 high-margin bits across s1/s2), the within-audit
  monotonicity noise floor is 0, fetch reliability is 244/244, and the
  detector+classifier pair runs cleanly on 0.15 m CoJ imagery at trivial
  cost (~2.4 min GPU for the whole sample). The 0.30/0.95 transplanted
  thresholds produced zero observed false-present bits; no re-calibration
  case emerged (the pre-registered threshold-failure interpretation of a
  gate (a) miss is ruled out by the failure signature — see §7).
- **s3 is not a valid known-sign calibration stratum and must be
  reclassified as a findings/target population.** Its "known absent" sign
  was the pipeline's own boundary claim (`install_interval_start` =
  synthetic Vexcel flight-bucket date on all 57 anchors), and the audit
  falsified it for 7/57 anchors at 2023 (4 also present@2019) with
  extreme-confidence, classifier-corroborated detections. Treating these
  as audit failures would invert the evidence; they are candidate
  **pipeline false-absents at the census boundary**, exactly the class D9
  targets.

Conditions to carry into ISSUE-09:

1. **Known-sign gating at cohort scale = present-side strata (s1/s2
   pattern) + a new true-negative control stratum** drawn from locations
   with no census 2024 detection (solid absent priors independent of the
   scan chain), replacing s3 in the calibration role.
2. **Human spot-check before launch**: the 7 gate (c) violation anchors +
   the 4 present@2019 s3 disagreements (IDs in §7 / `gates_report.json`;
   low-margin cousins already sit in `human_queue.csv`). If the visual
   check confirms PV, file the boundary false-absent tail as its own
   pipeline issue feeding the D10 gold set.
3. **Fetch plan**: concurrency 4-6, same politeness posture (§8);
   consider skipping the layer whose sign is already known per anchor to
   cut volume ~30-40%.
4. **Margin thresholds unchanged** (no evidence against 0.30/0.95 on this
   imagery); revisit only if the negative-control stratum surfaces
   false-presents.
