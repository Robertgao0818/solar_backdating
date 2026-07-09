# ISSUE-24: Learned feature matching (SuperPoint+LightGlue / LoFTR) for weak-lock GEHI↔Vexcel registration

Status: done — KILL (2026-07-09). SuperPoint+LightGlue 68.6% (94/137, best
arm), LoFTR 60.6% (83/137); both under the pre-registered 70% bar. See
[`DATA-learned-matching-bounded-pilot-2026-07-09.md`](DATA-learned-matching-bounded-pilot-2026-07-09.md).
Phase: 3 — Registration / chip geometry
Blocked by: — (population + tooling precursor: ISSUE-23, done)

## Parent

[`DATA-gehi-displacement-audit-2026-07-06.md`](DATA-gehi-displacement-audit-2026-07-06.md)
(ISSUE-23; defines the population, PSR reliability gate, and the S1/S2/S3
signal framework) and
[`DATA-dino-coarse-bounded-kill-2026-07-08.md`](DATA-dino-coarse-bounded-kill-2026-07-08.md)
(closes the DINO-coarse dense-patch-token approach; establishes the
pre-registered bounded-kill discipline this issue must reuse).

## Background — why the previous approach failed and what's different here

`chip_displacement.estimate_shift` (Sobel-gradient + Hann-window phase
correlation) locks 64.3% of attempted S3 (vintage-vs-Vexcel) rows at
PSR≥12; the remaining 35.7% (2,785/7,804 rows, 679 anchors) is the
"weak-lock" population this issue targets. A three-session DINO-coarse
investigation (dense ViT patch-token argmax cosine-similarity match) tried
to rescue this population and was closed 2026-07-08 with a pre-registered
KILL: best-tested config (DINOv2-S/14 or a Gram-anchored, satellite-domain
-matched DINOv3 `sat493m` backbone, at two resolutions) recovered known
offsets in at most 39.4% of the *easiest* subset (PSR≥12, offset≥5m
positive control) — far below the 70–80% kill bar, and neither backbone
generation nor finer patch resolution moved the number (see the memo above,
§5, for the full negative-result analysis).

Mechanistic reason to expect better here: DINO's dense-token argmax is a
*semantic-similarity heatmap* over integer patch shifts — it was never
designed for sub-pixel geometric correspondence. SuperPoint+LightGlue and
LoFTR are purpose-built for exactly this task — they emit explicit keypoint
correspondences (sparse, detector-based for SuperPoint; dense,
detector-free for LoFTR) that a robust estimator (RANSAC) turns into a
translation/affine fit **with an inlier-count confidence signal**, giving a
PSR-analog for free. This is a different mechanism from anything tested in
the DINO-coarse line, not a bigger/better version of the same approach — the
KILL verdict's "no further ablation without a new hypothesis" clause does
not block this issue.

## What to build

A **bounded, pre-registered pilot**, following the same discipline the DINO
investigation should have used from session 1 (cheap positive-control test
first, cost-profile before any full-population run, kill bar fixed before
looking at results):

1. Start with **SuperPoint+LightGlue** only (lighter weight, faster than
   LoFTR, and RANSAC inlier count is a natural off-the-shelf confidence
   signal). Add LoFTR as a second arm only if SuperPoint+LightGlue's
   positive-control result doesn't clear the kill bar — LoFTR's detector-free
   dense transformer matching is heavier compute and worth paying for only
   if the cheaper detector-based approach demonstrably falls short.
2. Run against the **same 137-row positive control** the DINO experiment
   used (`ref_kind=="S3_vexcel" & psr>=12 & best_offset_m>=5.0`,
   `per_chipdate_offsets.csv`) — this directly benchmarks against the 39.4%
   DINO ceiling on identical rows, and against the same 70–80% kill bar.
3. Profile cost on a ~15-row subsample before committing to the full 137
   (the DINO bounded experiment measured ~1–5s/row depending on config —
   establish this matcher's actual cost before assuming it's comparably
   cheap; SuperPoint+LightGlue and LoFTR have different memory/compute
   profiles than a ViT forward pass + integer-shift argmax).
4. Only if the positive control clears the kill bar, extend to a bounded
   subsample (not the full 2,785-row set) of the actual PSR<12 weak-lock
   population before any production commitment.
5. Do **not** touch `dino_coarse_match.py`, `pilot_dino_rescue_2026-07-08.py`,
   or `diagnose_dino_positive_control_2026-07-08.py` — those are the closed
   investigation's frozen record, not a base to extend. Do not touch the
   frozen 96m GEHI download/builder or `chip_geometry.py`'s crop-render
   registry — this is a new candidate registration signal, analogous to
   S1/S2/S3 in `audit_gehi_displacement.py`, additive until proven, never a
   silent replacement.

## Acceptance criteria

- [ ] Kill bar written and committed **before** running anything (default:
      reuse 70–80% recovery on the 137-row positive control, consistent
      with the DINO precedent; deviate only with explicit justification)
- [ ] Cost profiled on a small subsample before any full-population run
- [ ] Full 137-row positive-control result reported with an explicit
      GO/KILL verdict against the pre-registered bar, and a RANSAC-inlier
      (or equivalent) confidence distribution reported alongside recovery
      rate, not recovery rate alone
- [ ] If GO: a second bounded test on a subsample of the actual PSR<12
      weak-lock population — explicitly not a full 2,785-row commitment in
      this same slice
- [ ] Written verdict memo (`DATA-*.md`, same structure as
      `DATA-dino-coarse-bounded-kill-2026-07-08.md`) produced regardless of
      GO/KILL outcome
- [ ] Existing S1/S2/S3 signals, the frozen 96m builder, and the chip
      geometry registry are unmodified

## Out of scope / do not conflate

The "arm 0" (widen phase-correlation's own search window) and "arm 1"
(accept + calibrate-correct the PSR 8–12 weak-lock band instead of
re-registering from scratch) levers flagged in the DINO-coarse memo are a
**separate, cheaper, non-learned-matching** approach and remain untested and
unrelated to this issue — don't fold them in here or let this issue's
outcome be read as resolving them.

## Blocked by

- — (no blocking dependency; population, join tables, and the PSR/positive-
  control pattern already exist from ISSUE-23 and the DINO-coarse
  investigation)
