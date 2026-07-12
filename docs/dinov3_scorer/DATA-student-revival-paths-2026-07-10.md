# DATA — student revival paths under frozen-compute constraints (2026-07-10)

Status: decision memo, living statuses. Constraints in force: **single RTX
4070 (12 GB), no LoRA / no backbone training (user veto 2026-07-10), zero
new GT** — all supervision derives from existing banked Gemini labels.

Parent: [`DATA-fidelity-gate2-dual-fail-2026-07-10.md`](DATA-fidelity-gate2-dual-fail-2026-07-10.md)
(why the frozen linear-head student failed gate-2) ·
Checker: `scripts/validation/check_student_path_gate.py` (arithmetic
verdicts for the bars below — no discretion) ·
External sources: 2026-07-10 research sweep (citations inline).

## Ranked paths (cost-effectiveness order)

| # | Path | Cost | Evidence base | Status |
|---|------|------|---------------|--------|
| **A″** | **Same-sensor present template:** frozen backbone; GEHI candidate × **latest teacher-`present` GEHI**; patch-token diff head (pool last); question = "is this the known install?" (FP gate; no Vexcel) | Head-only ≤5M; reuse GEHI patch grids | DeepSolar++ polarity without cross-sensor jump; ChangeDINO-shaped head | **Closed — SMOKE_GO then R3 KILL** [`DATA-anchor-pair-a2-same-sensor-prereg-2026-07-10.md`](DATA-anchor-pair-a2-same-sensor-prereg-2026-07-10.md): same-sensor ranking works (s_pos 0.78 > s_abs 0.59 > s_neg 0.58), but Arm P TB FP **worsened** vs B (−24% red), FN +1.6%, overall agr 0.803 < 0.817. No gate-2 re-run |
| **A′** | **Pairing-v2 (Vexcel present template):** GEHI × **Vexcel census present crop** | Head-only ≤5M; banked Vexcel crops | DeepSolar++ polarity; ChangeDINO-shaped head | **Closed — H3 SMOKE_KILL** [`DATA-anchor-pair-v2-prereg-2026-07-10.md`](DATA-anchor-pair-v2-prereg-2026-07-10.md): median cos(GEHI present, Vexcel)=0.31 ≪ cos(Vexcel, other Vexcel)=0.89; domain gap. Heads not trained. **Do not re-hard-code Vexcel onto frozen DINO** |
| **A** | **Pairing-v1 (absent-anchor, pooled):** earliest teacher-absent + global-pooled emb + raw counts | (executed) | Weak test of the mechanism; wrong polarity for FP residual | **Closed — R1 KILL, scope-limited** [`DATA-anchor-pair-pilot-prereg-2026-07-10.md`](DATA-anchor-pair-pilot-prereg-2026-07-10.md) · review [`DATA-anchor-pair-pilot-review-2026-07-10.md`](DATA-anchor-pair-pilot-review-2026-07-10.md). Does **not** kill pairing |
| **B** | **Sequence-level distillation head:** frame-embedding sequence → small temporal head; distill teacher's *decoded interval* | Same — head-only | Kruitwagen CNN→RNN; Kim & Rush sequence-level KD | **Closed — corrected B-R1 KILL / frame-bar FAIL** [`DATA-sequence-head-pilot-prereg-2026-07-10.md`](DATA-sequence-head-pilot-prereg-2026-07-10.md): adopted Phase-0 teacher target; exact interval +9.4 pp (paired CI >0), FP improvement only 9.8% with CI crossing 0, FN worsened 71.7%, overall frame agreement regressed, and A reached only 0.711 transition / 0.821 overall vs 0.90 |
| **C** | **Frozen-encoder swap arms:** dinov2-with-registers; further, video-native V-JEPA2 | Free / re-extract | Register tokens (Darcet ICLR'24); VideoGLUE on temporal tasks | Not started; **fold into A″ arms if needed**, not standalone |
| **D** | **Diagnostic completion:** area_m2-stratified + decoded-only co-headline | One join | Size check | **DONE** — [`DATA-gate2-area-stratified-2026-07-10.md`](DATA-gate2-area-stratified-2026-07-10.md) |

### Ranking note (updated after A″ R3 KILL 2026-07-10)

- **All frozen head-only student revival paths are closed:** A, A′, A″, B.
  No licensed gate-2 re-run. Slice 7 stays blocked; Gemini remains default.
- **A″** cleared same-sensor smoke but failed R3: present-template pair head
  does not beat param-matched single-frame control (FP worsened).
- **A′** closed at cross-sensor domain-gap smoke (do not re-hard-code Vexcel
  onto frozen DINO).
- **A v1** scope-limited KILL (pooled absent anchor only).
- **B** B-R1 KILL (interval lift real; transition FP/FN + frame bar failed).
- **Next only under a new prereg:** ISSUE-25 teacher geometry + re-distill;
  caliber policy change; or user-lifted LoRA/backbone. Do not silently
  reopen A/A′/A″/B.

Shared discipline (from v1 review, binding on A″ / A′ / B):

- Control = **parameter-matched** (±10%), not merely same architecture.
- Verdict on **rates + multi-seed / CI**, not raw counts at n≈14.
- Guard **`present→unusable`** abstention escape.
- No self-pair / self-referential giveaways.
- Pooled global embeddings alone are **not** a fair ChangeDINO-style test.

## Stop discipline (machine-checked)

Rules in `scripts/validation/check_student_path_gate.py`:

1. **`anchor_pair_r1`** — v1 executed record (raw-count dual 30%).
   **Applied 2026-07-10 → KILL on v1.** Kept for audit; do not reuse for A′/A″.
2. **`anchor_pair_v2_r1`** — A′ bar (Vexcel present; rate-normalized dual
   ≥30% with CI low >0, overall no regression, present→unusable guard,
   unusable-recall guard, param match ±10%, n_seeds≥3). A′ stopped at smoke;
   rule kept for audit.
3. **`anchor_pair_a2_r1`** — **A″ bar** (same-sensor latest present Arm P vs
   param-matched B): same rate/CI/guard/param/seed structure as v2, keys
   use `_p` / `arm_p_*`. **Applied 2026-07-10 → KILL** (smoke GO; dual
   transition bar + overall regression).
4. **`frame_bar`** — advisory investment stop-loss: decided-agreement ≥0.90
   on **both** transition band and overall. Gates *investment*, not gate-2.
5. **`sequence_head_r1`** — path-B bar: exact interval delta and paired
   anchor-cluster CI both positive; transition FP/FN rate reductions each ≥30%
   with positive CI lows; no overall regression; present→unusable guard;
   parameter match ±10%; ≥3 seeds. **Applied to the corrected decoded-target run
   2026-07-10 → KILL** (interval lift +9.4 pp, FP below the effect bar/CI, FN
   materially worse, and `frame_bar` failed). The earlier hard-label-target run
   is invalidated and is not evidence for Path B.

Use the rule matching the artifact schema. For A″, run `--rule
anchor_pair_a2_r1` (and optionally `frame_bar`). `all` is only appropriate
when a metrics file intentionally contains every rule's keys.

## Two honest caveats (do not plan around them silently)

1. **Boundary ambiguity is structural.** Temporal-action literature is
   explicit that errors concentrate at transitions regardless of joint
   sequence modeling; the teacher ceiling 0.7724 is itself "Gemini disagrees
   with itself 23% of the time" (0.65–0.67 on the dominant <40 m² strata —
   see sibling memo). Even a 0.9+ frame student may land ~0.7 on the hard
   interval match; at that point the question becomes whether/how to soften
   the bar — a **caliber decision, not a model decision**, and out of scope
   for any pilot.
2. **No published answer exists to copy.** The largest published comparable
   (Microsoft Global Renewables Watch, 86k sites, arXiv 2503.14860) dates
   construction with a naive earliest-detection threshold — no decoder at
   all. "PV temporal VLM distillation" is a literature gap: nobody has
   de-risked this path for us, and a working pair/sequence student is a
   novel methods contribution (post-ESSD paper upside).

## Non-blocking reminder

This entire line is a **cost / version-drift optimization**, not a
dependency of the P0 DAG: Panel v2 rebuilds on Gemini regardless (storyline
v2 §5), and the ESSD scorer-fidelity gate refers to the Gemini pipeline's
own reproducibility. Pilots proceed at their own pace; ISSUE-25
(teacher-side geometry, `replan_v2/ISSUE-25`) runs in parallel and, on
adoption, re-banks labels — path A″/A′/B architecture verdicts carry over but
heads re-distill before any gate-2 re-run.
