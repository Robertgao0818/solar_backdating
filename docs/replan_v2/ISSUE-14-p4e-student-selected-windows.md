# ISSUE-14: P4-E — student-selected windows, teacher scores

Status: ready-for-agent
Phase: 4 — Pipeline shape
Blocked by: ISSUE-05, ISSUE-12, ISSUE-13 (+ student scaffold, dinov3 slice 3)

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D13 (step E). User stories 28, 30.

## What to build

The first pipeline-shape migration step — kills search variance with **zero
trust in student verdicts**: a rough student head pre-scores each anchor's
complete drivable vintage stack; a **fixed deterministic rule** selects 2–3
windows bracketing the student's coarse transition estimate (plus a cheap
fallback coarse bracket so a badly-wrong student cannot lose the transition);
the teacher scores exactly those windows through the PresenceScorer seam
(cached via the verdict store). Teacher call volume stays ≈ today's adaptive
scan.

The student only ranks frames coarsely — a far weaker ask than matching the
teacher's decisions — so this ships before the fidelity gate, with the
scaffold head (even the DINOv2-S floor) sufficing.

Gate = rerun the mini-reliability two-arm protocol with arm B′ =
student-picked windows: since window choice is now a pure function of
(frozen student × frozen stack), B′ should collapse to the frozen-path arm's
reproducibility.

## Acceptance criteria

- [ ] Window selection is a pure function (property test: same student scores → same windows)
- [ ] Fallback coarse bracket bounds worst-case teacher calls (test with adversarially wrong student scores)
- [ ] Teacher call volume ≈ today's per-anchor volume (measured on the panel cohort)
- [ ] Arm B′ rep-to-rep ≥ 0.875 and undated-flip ≤ 0.171 (the frozen-arm bars)
- [ ] Provenance + verdict-store caching active end-to-end (second rep pays ~zero teacher calls)

## Blocked by

- ISSUE-05 (seam), ISSUE-12 (amended student plan), ISSUE-13 (catalog cache for full-stack prep); student scaffold from the dinov3 tracker (slice 3)
