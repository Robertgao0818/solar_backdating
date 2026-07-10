# ISSUE-06 gate-1/2 run — reviewer package

Date: 2026-07-10 · Parent: [`ISSUE-06-fidelity-gate.md`](ISSUE-06-fidelity-gate.md) ·
Verdict: [`ISSUE-06-gate-verdict-2026-07-06.md`](ISSUE-06-gate-verdict-2026-07-06.md)

This note is the **reviewer entry point** for the gate run that closed slice 6.
It lists the code/docs diff, the offline artifacts, the headline numbers, and a
minimal re-verification checklist. Accuracy is out of scope (no install-date
truth); the gate is fidelity to Gemini.

---

## One-line outcome

| gate | DINOv3-L-SAT | DINOv2-S floor |
|---|---|---|
| 1 self-repro | **PASS 1.0** | **PASS 1.0** |
| 2 vs teacher ceiling 0.7724 | **FAIL A=0.6392** | **FAIL A=0.6542** |
| 3 chip fidelity (pre-locked) | 0.7971 EQUIVALENT | 0.7968 EQUIVALENT |

- Ceiling spread `S = 0.0116`
- R1 bet-survival: `A_LSAT − A_floor = −0.0150 ≯ S` → **SAT/L FALSIFIED**
- **Gemini stays default.** Slice 7 / P4 / Panel v2 full rescan remain on Gemini.
- Zero API spend (CPU re-decode banked teacher + local 4070 student re-score).

---

## Diff scope (repo)

| path | role |
|---|---|
| `scripts/validation/fidelity_gate.py` | tight12 nomarker re-render adapter; dual-pipeline render-drop; pollution stop-rule; separate teacher-ceiling walk (full banked frames) vs student-vs-teacher walk |
| `docs/dinov3_scorer/ISSUE-06-gate-verdict-2026-07-06.md` | filled `TBD-gate-run` sections only; LOCKED sections untouched |
| `docs/dinov3_scorer/ISSUE-06-fidelity-gate.md` | AC boxes checked; outcome note |
| `docs/dinov3_scorer/TRACKER.md` | slice 6 ✅, slice 7 ⛔, progress log |
| `docs/dinov3_scorer/ISSUE-06-gate-run-review-2026-07-10.md` | this handoff |

**Not in this package:** `scripts/validation/pilot_maj_unanimous_distill.py` (unrelated untracked).

### What changed in the harness (review focus)

1. **Student input contract (LOCKED 2026-07-06)** — banked chips are
   `chip_geom_v1_banked96`; heads trained on `chip_geom_v2_tight12` nomarker.
   Previously `_resolve_chip_path` was identity (OOD). Now
   `StudentChipResolver` re-renders via `ensure_single_target_review_png` +
   `resolve_chip_geometry`, default ON unless `--no-student-rerender` /
   `--teacher-only`.
2. **Like-for-like frame drop** — re-render failures drop the frame from
   **both** teacher and student observation sequences for gate-2; teacher
   ceiling still uses full banked frames (separate walk).
3. **Pollution stop-rule** — abort if drops > `--render-drop-stop-pct`
   (default 2%). This run: **0 / 14,002**.
4. **CLI** — `--chip-targets`, `--geometry-version`, `--no-student-rerender`,
   `--render-drop-stop-pct`.

Unit tests: `tests/validation/test_fidelity_gate.py` (11) still pass — they use
the identity resolver + fake scorer (no GPU, no re-render).

---

## Offline artifacts (not in git)

Root: `~/zasolar_data/geid_temporal/fidelity_gate_20260710/`

```
dinov3_lsat/
  gate1_self_repro.json
  gate2_agreement.json
  provenance.json
  render_drop_report.json
  summary.md
dinov2_floor/   # same layout
logs/
  dinov3_lsat.log
  dinov2_floor.log
smoke_dinov3_limit2/   # pre-flight only
```

Sibling nomarker PNGs were written next to banked GeoTIFFs under
`llm_endtoend_storebacked_20260704/rep{1,2,3}/**/*.nomarker.png` (idempotent
cache of the standard renderer; analysis outputs were **not** written into the
bank).

Head sha256 at run (verified):

- DINOv3 `bc053cbdc8ede680d2f3a90dc14933ebcd086b7669eeacfdc5bd99039adaca07`
- floor `38ecbf372ee536b2…` (see `head_v1.json` sidecars)

---

## Headline numbers (from `gate2_agreement.json`)

**Teacher ceiling** (inv-weighted all-units, decoded intersection):

| pair | n | inv-w | dated-only |
|---|--:|--:|--:|
| 1–2 | 222 | 0.7708 | 0.7709 |
| 1–3 | 218 | 0.7674 | 0.7685 |
| 2–3 | 233 | 0.7790 | 0.7790 |

**Student `A` (mean of per-rep inv-weighted all-units):**

| | rep1 | rep2 | rep3 | mean |
|---|--:|--:|--:|--:|
| L-SAT | 0.6653 | 0.6028 | 0.6495 | **0.6392** |
| floor | 0.6650 | 0.6345 | 0.6630 | **0.6542** |

Leakage drop: 14 rows / 2 group / 6 target (ids in verdict).

---

## How to review the diff

```bash
cd /home/gaosh/projects/solar_backdating
source scripts/activate_env.sh

# 1) code + docs diff for this package
git show --stat HEAD   # after the package commit lands
git show HEAD -- scripts/validation/fidelity_gate.py
git show HEAD -- docs/dinov3_scorer/

# 2) unit tests (CPU, ~1s)
python -m pytest tests/validation/test_fidelity_gate.py -q

# 3) read verdict (LOCKED sections must be byte-stable vs pre-run skeleton
#    except filled TBD blocks)
less docs/dinov3_scorer/ISSUE-06-gate-verdict-2026-07-06.md

# 4) spot-check machine numbers match the verdict tables
python - <<'PY'
import json
from pathlib import Path
root = Path.home() / "zasolar_data/geid_temporal/fidelity_gate_20260710"
for name in ("dinov3_lsat", "dinov2_floor"):
    g2 = json.loads((root/name/"gate2_agreement.json").read_text())
    g1 = json.loads((root/name/"gate1_self_repro.json").read_text())
    ceil = g2["teacher_rep_vs_rep_ceiling"]["spread"]
    As = [r["all_units"]["agreement_inv_weighted"]
          for r in g2["student_vs_teacher_per_rep"]]
    print(name, "gate1", g1["self_agreement"], "A", round(sum(As)/3, 4),
          "ceil_mean", ceil["mean"], "S", round(ceil["max"]-ceil["min"], 4),
          "drops", g2["render_drop"]["n_render_drop"])
PY
```

### Optional re-run (GPU, ~40 min L-SAT + ~7 min floor)

```bash
STORE=~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704
OUT=~/zasolar_data/geid_temporal/fidelity_gate_REVIEW_RERUN
python scripts/validation/fidelity_gate.py \
  --reference $STORE/reference.csv \
  --reps $STORE/rep1 $STORE/rep2 $STORE/rep3 \
  --scorer dinov3_frozen \
  --head ~/zasolar_data/models/dinov3_sat/head_v1_20260705/head_v1.pt \
  --estimator changepoint \
  --cohort-prior ~/zasolar_data/geid_temporal/issue03_gates_20260704/cohort_prior.json \
  --emissions ~/zasolar_data/geid_temporal/panel_repair_20260703/analysis_estimator_harness_extended/emissions_fitted.json \
  --drop-anchors-overlapping ~/zasolar_data/geid_temporal/dinov3_distill_20260705/chip_subset_anchors.json \
  --self-repro --device cuda \
  --out $OUT/dinov3_lsat
# then dinov2_floor with --scorer dinov2_floor + floor head
```

Nomarker PNGs already cached under the bank should make a re-run faster.

---

## Reviewer checklist

- [ ] Diff does **not** edit LOCKED sections of the verdict skeleton (only fills
      former `TBD-gate-run` blocks + status line).
- [ ] No hand-authored **0.74** bar in verdict or harness outputs.
- [ ] Gate-1 is demonstrated (two passes, mismatch count 0), not asserted.
- [ ] Gate-2 uses Phase-0 `changepoint` + EB prior, inv-weighted headline,
      dated-only alongside, leakage drop applied.
- [ ] Student re-render is active in provenance (`student_rerender_active: true`,
      geometry `chip_geom_v2_tight12`); render drops 0%.
- [ ] R1 arithmetic: `A_LSAT − A_floor` vs `S` — no discretion.
- [ ] Production implication matches numbers: Gemini default; slice 7 blocked.
- [ ] Unrelated untracked files are **not** in the commit.

---

## Patch export location

After the package commit:

```bash
# single-commit patch for offline review
git format-patch -1 HEAD -o ~/zasolar_data/geid_temporal/fidelity_gate_20260710/review_package/
# or unified diff
git show HEAD > ~/zasolar_data/geid_temporal/fidelity_gate_20260710/review_package/ISSUE-06-gate-run.diff
```

A companion `README.txt` is written next to the patch under
`~/zasolar_data/geid_temporal/fidelity_gate_20260710/review_package/`.
