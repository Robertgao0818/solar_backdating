# RUN — R4 v2 / H1 quality-head supervision repair (2026-08-13)

Status: **FROZEN before any v2 seed** · Attempt ID: `r4_v2`
Alias under parent §8: this **is** the single `r4_g1c1` calibration-gate
correction. It consumes the one licensed correction (OWNER D3). A second
FAIL triggers OWNER D2 (one LoRA-level round with its own prereg;
backbone swap still vetoed).

Parent (unamended clauses remain binding):
[`RUN-r4-training-calibration-prereg-2026-07-20.md`](RUN-r4-training-calibration-prereg-2026-07-20.md)

Evidence lock (diagnosis, not a correction):
[`DATA-r4-v1-quality-head-diagnosis-2026-08-13.md`](DATA-r4-v1-quality-head-diagnosis-2026-08-13.md)

Owner decisions:
[OWNER_DECISIONS D2/D3](../replan_v2/OWNER_DECISIONS.md)

## 0. Failure signature and one hypothesis

**Observed (R4 v1, cal_select, three seeds):** quality-head AUROC
0.568 / 0.591 / 0.579; map-in-K 0.660–0.664; no threshold on the frozen
grid meets Wilson 0.7542 / 0.7337 at coverage ≥ 0.80 →
`CALIBRATION_FAILED`. State-head AUROC 0.93.

**Causal hypothesis (H1 only):** the quality-head target is
`effective_label != uninformative` after the A1 empty-K sidecar. That
target is a verdict-availability label, not D12.iii imagery-usability
supervision. 346/410 cal_select negatives (84%) are A1 boundary patches
with `quality_flag=usable`. The head is trained to treat readable
boundary frames as unusable imagery, so `q` entering Phase-0
`log(q·e+(1-q))` is near-prior noise.

**Bounded component changed:** quality-head *supervision source* (and
the class weights / quality-bias / quality calibrator that are
deterministic functions of that source). Nothing else.

**Expected directional effect:** quality AUROC on the reconstructed
target rises well above the v1 0.57–0.59 band; `q` becomes a usability
signal the decoder can use; at least one frozen-grid threshold qualifies
on `cal_select`.

**Kill rule (unchanged bars):** if after this one run no seed meets
parent §7 (Wilson overall ≥ 0.7542 and under40 ≥ 0.7337 and coverage
≥ 0.80), verdict is again `CALIBRATION_FAILED` and D2 applies. No
on-the-fly second tweak.

This is **not** reweighting-only. Inverse-sqrt class weights are
recomputed on the new target (D3: H3 allowed as a component of H1).

## 1. Inherited locks (byte-identical)

Unchanged from parent + A1:

| object | lock |
|---|---|
| R0 manifest / splits / MANIFEST_LOCK | parent §1.1 SHAs |
| calibration-role salt | `r4_v1_cal_roles@2026-07-20` (roles must not reshuffle) |
| realized role counts | cal_es=2,512 / cal_fit=1,799 / cal_select=1,898 |
| R3 feature cache | parent §1.3 SHA `86297de7…` |
| A1 sidecar | SHA `fefac6fe234e2a9e2c21ea77aa0bd7cc8b788ff19e89f89310cf0171da23a82a` |
| seeds | 2026072001 / 2026072002 / 2026072003 |
| head | 298,243 params, architecture unchanged |
| decoder / interval loss / optimizer / early stopping | parent §4–§5 |
| threshold grid and bars | parent §7: 0.7542 / 0.7337 / 0.80 |
| test-blind | parent §1.1; test still unreadable |

A1 still overrides the two teacher boundary frames to `uninformative`
and sets `interval_loss_eligible=false` for those 2,146 anchors. That
remains an interval-representability patch, **not** quality supervision.

## 2. The one changed rule — quality supervision

Rule id: `r4_v2_h1_teacher_quality_d12iii`.

After A1 is applied, keep the pre-override label as `label_v1_r0` and
mark `a1_override` on the exact sidecar rows. Then:

```text
quality_supervision_eligible =
    (not a1_override)
    AND scan_status ∉ {done_ambiguous_nonmonotonic,
                       done_ambiguous_no_recent_anchor}

q_target = 1  iff  quality_flag == "usable"
                   AND label_v1_r0 ∈ {present, absent}
q_target = 0  iff  eligible and not (q_target = 1)

quality BCE  = mean over eligible frames only
state CE     = unchanged (effective present/absent after A1)
interval loss = unchanged (A1 still disables those anchors)
```

Ineligible frames (A1 patches; D12.iii ambiguous-status series) do not
enter quality BCE, quality class weights, quality-head bias, the quality
Platt calibrator, or quality health metrics.

Locked train+calibration counts (must be recomputed at materialize and
must match; `allow_fixture` exempts):

| pool | eligible | q=0 | q=1 | excluded A1 | excluded ambiguous-status |
|---|---:|---:|---:|---:|---:|
| train+cal | 245,862 | 9,547 | 236,315 | 4,292 | 20,712 |
| train | 205,604 | 8,375 | 197,229 | — | — |
| cal_select | 12,487 | 387 | 12,100 | 346 | 964 |

q=0 composition (eligible only): `ambiguous` 7,672 + `unusable` 1,875.

Quality-head bias is `log(n_q1 / n_q0)` from **train eligible** rows
(`log(197229/8375) ≈ 3.159`). State-head bias stays on effective
present/absent counts.

Class weights: same inverse-sqrt / cap-10 / mean-one rule as parent §4,
with `quality_0`/`quality_1` counted on the new eligible q-targets and
`absent`/`present` still counted on effective labels.

## 3. Calibration and health (quality target only)

The hierarchical Platt quality calibrator (parent §6) fits **eligible
`cal_fit` rows** against `quality_target`, not `effective_label`.
Quality Brier / NLL / ECE / AUROC on `cal_select` use the same target
and the same eligible mask.

State calibrator, threshold selection, and `MAP-in-K` / coverage gates
are unchanged. Underpowered-slice rules unchanged.

`R4_HEALTH` quality checks (NLL not worse than raw by >0.005; ECE≤0.05;
Brier better than constant train prevalence) are evaluated on the
reconstructed target. That is a declared change of the *instrument*,
not of the pass bars.

Prediction Parquet gains three additive columns:
`quality_target`, `quality_supervision_eligible`, `quality_target_reason`
∈ {`usable_verdict`, `quality_negative`, `a1_empty_k_patch`,
`d12iii_ambiguous_anchor`}.

## 4. Artifact root

```text
~/zasolar_data/geid_temporal/run3_native_line_2026-07/r4_v2/
```

Same layout as parent §10 with `config/r4_v2.yaml` and
`attempt_id=r4_v2`. Do not write into `r4_v1/`.

`RUN_LOCK.json` must record the quality-supervision rule id, the locked
counts in §2, A1 SHA, calibration-role salt (still v1), and
`git_dirty=false`.

## 5. What this run is not

- Not H2 (accept-rule change). `max_posterior` threshold semantics stay.
- Not a standalone H3 (reweight-only).
- Not LoRA / backbone / cropgeo-v2 / TLO sidecar / test access.
- Not a bar move.
- Not R5. Test and the 576-anchor repeat panel stay sealed.

## 6. Pre-start checklist

- [ ] This document committed before any v2 seed
- [ ] Implementation tests for the §2 rule (A1 exclusion, ambiguous-status
      exclusion, flag→target mapping, ineligible frames drop out of BCE)
- [ ] Parent leakage / SHA / 298,243-param / decoder-equivalence checks
- [ ] Materialize recomputes and asserts the §2 locked counts
- [ ] `out-root` is `r4_v2/`; v1 artifacts untouched
- [ ] Clean worktree at the recorded commit (`git_dirty=false`)

## 7. Verdict language

Same three machine verdicts as parent §8. `READY_FOR_R5` still means
only “worth an untouched R5”, not production GO and not paper accuracy.
On `CALIBRATION_FAILED`, stop. D2 is the only licensed next scientific
attempt.
