# Data memo: pre-registered bounded kill experiment for DINO-coarse recentering — KILL (2026-07-08, session 3)

Status: final verdict for the DINO-coarse rescue side-investigation opened in
session 1 (`pilot_dino_rescue_2026-07-08.py`, NO-GO 1.2% rescue) and revised
in session 2 (`diagnose_dino_positive_control_2026-07-08.py`, instrument
check — ruled out both cheap bug hypotheses, left fix candidates #3/#4
untested). This memo closes the line.

**Correction (2026-07-08, later same day):** an earlier draft of this memo
stated `chip_geom_v3_tight_scaled` "ships widening-only" — **false**, checked
directly against `chip_geometry.py`'s `_REGISTRY`, which contains only
`chip_geom_v1_banked96` and `chip_geom_v2_tight12`. `chip_geom_v3_tight_scaled`
is a *foreseen name only* (comment in `chip_geometry.py`, "a future
size-stratified follow-up is a second named fixed version") for one of two
still-**undecided** options recorded in `TRACKER.md`'s slice-23 note: (A)
register a size-stratified geometry version under that foreseen name, or (B)
add per-vintage local re-centering as a new named `geometry_version`. This
memo's DINO-coarse pilot was testing option (B) — its KILL closes off (B) as
a DINO-based approach specifically, which makes (A) the more likely remaining
path, but (A) is **not built or shipped**; it remains an open TRACKER item.
Nothing downstream is currently shipping any correction for the tight12
contamination this line was meant to fix — see `TRACKER.md`'s slice-23 note
and `DATA-gehi-displacement-audit-2026-07-06.md` for the still-open problem.

**Correction (2026-07-10): this memo's 39.4% recovered rate was measured
against a GT positive control now known to be partially contaminated.** A
GT re-adjudication for the sibling ISSUE-24 learned-matching pilot
(`DATA-learned-matching-gt-readjudication-2026-07-09.md`) found that the
same 137-row `psr>=12 & best_offset_m>=5.0` positive control's phase-
correlation GT is aliased (locks onto the wrong nearby rowhouse unit) on a
material share of rows. **This memo's KILL verdict is NOT reopened** —
SuperPoint+LightGlue (a different, purpose-built mechanism) dominates
DINO-coarse's dense-token-argmax approach regardless of the GT contamination,
and no new hypothesis about DINO-coarse itself has been raised — but the
39.4% figure is understated: an unknown share of DINO-coarse's own
"failures" against this GT may be additional zero-pinned-style aliasing
artifacts rather than genuine DINO-coarse misses. This is recorded for
comparability, not as grounds to re-run or re-ablate the DINO-coarse line.

Pre-registration (verbatim mandate, session 3): test fix #4 (finer
`input_size`, ~1.2m/cell) and a DINOv3 Gram-anchored, `sat493m`
satellite-domain-matched backbone, on the same 137-row positive control used
in session 2. **Kill criterion, fixed before running:** if the best-tested
config's recovered rate on the ≥5m-offset positive control does not reach
70–80%, close the entire DINO-coarse recentering line — no further
backbone/resolution ablation without a new hypothesis. Population: `psr>=12
& ref_kind=="S3_vexcel" & best_offset_m>=5.0` (n=137, unchanged from session
2) — phase-correlation already trusts these locks; this is the *easiest*
version of the rescue problem, not the PSR<12 population DINO-coarse would
actually need to fix in production.

## 1. Model/resolution identification

- DINOv3 in this `timm==1.0.25` install exposes 18 pretrained tags under
  `*dinov3*`; the only `sat493m` (Gram-anchored, satellite-domain-pretrained)
  checkpoints are `vit_large_patch16_dinov3.sat493m` (303.1M params, patch16,
  embed_dim=1024, depth=24, 5 prefix tokens) and a 7B variant — **no
  `vit_small` sat493m exists**, so the domain-matched arm is necessarily a
  ~14× larger backbone than DINOv2-S/14 (22.1M params), a confound the kill
  criterion doesn't control for but the timm registry leaves no smaller
  option. `timm/vit_large_patch16_dinov3.sat493m` on HF: **not gated**, no
  `HF_TOKEN` needed. `load_backbone`/`patch_token_grid` in
  `dino_coarse_match.py` needed **zero changes** — `num_prefix_tokens=5`
  (register tokens) is read generically and every tested `input_size` produced
  a perfect square token grid.
- Chip extent is ~constant across the population (97.50–97.80m, mean
  97.66m) — `input_size` → `cell_m` is deterministic and population-wide, not
  per-anchor-variable. Verified against real `RefCache` geometry, not assumed
  from the session-1→2 handoff's rough 1036 estimate.
- Two resolution tiers actually run (both hit exact-integer `side`,
  `input_size % patch_size == 0`):

| tier | DINOv2 patch14 `input_size` | DINOv3 patch16 `input_size` | `side` | `cell_diag_m` | recovered tol (1.5 cells) |
|---|---:|---:|---:|---:|---:|
| baseline (matches session 2) | 518 | 592 | 37×37 | 3.733 | 5.599 m |
| finer (fix #4 target, ~1.2m/cell) | 1120 | 1280 | 80×80 | 1.726 | 2.590 m |

  DINOv2@592-equivalent isn't separately run — 592/14 is not integer, so
  Arm B instead reuses 592 for DINOv3/patch16 to land on the *same* 37×37
  grid as the DINOv2 baseline, making Arm B a clean backbone-only swap
  (identical resolution, identical tolerance) rather than a confounded
  resolution+backbone change.

## 2. Cost profile (measured, not estimated)

15-row `--limit` smoke on each arm before committing to the full 137:
DINOv2@1120 ≈1.1s/row, DINOv3@592 ≈1.0s/row, DINOv3@1280 ≈5.2s/row (vit_large
forward + O(shift²×side²) search at side=80). Full 137-row wall time: Arm A
141s, Arm B 62s, Arm C 579s. Peak GPU memory at the heaviest single forward
(DINOv3@1280) = 1.55GB — comfortable headroom on the 8GB laptop GPU (5.7GB
free at start). All three arms together: ~14 minutes, matching the
handoff's "trivially cheap" framing.

## 3. Results (n=137 throughout; `recovered` = error_m ≤ 1.5×cell_diag_m)

| arm | backbone | `input_size` | tol | raw recovered | centered recovered | best |
|---|---|---:|---:|---:|---:|---:|
| baseline (session 2) | DINOv2-S/14 | 518 | 5.599 m | 36.5% (50/137) | 39.4% (54/137) | **39.4%** |
| A — fix #4 alone | DINOv2-S/14 | 1120 | 2.590 m | 15.3% (21/137) | 14.6% (20/137) | 15.3% |
| B — backbone swap alone | DINOv3-L/16 sat493m | 592 | 5.599 m | 35.0% (48/137) | 38.7% (53/137) | 38.7% |
| C — both combined | DINOv3-L/16 sat493m | 1280 | 2.590 m | 25.5% (35/137) | 29.9% (41/137) | 29.9% |

Outputs: `~/zasolar_data/geid_temporal/diagnose_dino_bounded_2026-07-08/arm_{A,B,C}_*/{diagnostic_results.csv,diagnostic_summary.txt}`.

**Best-tested configuration across all 5 runs (session 2 + session 3):
baseline centered = 39.4% (54/137).** No lever tried this session beat it.

## 4. Kill criterion applied

39.4% << 70–80%. **Verdict: KILL.** Close the DINO-coarse recentering line
entirely — no fix #3 (intermediate-layer tokens), no further
backbone/resolution ablation, no revisiting without a genuinely new
hypothesis (not "try a bigger model" or "try a finer grid" — both variants of
exactly that were just tested and both failed). This is unambiguous under the
pre-registered bar; the tolerance-in-metres table in §1 rules out "the 70–80%
figure is ambiguous because tolerance shrank with resolution" as a reason to
relitigate — Arm B matches the baseline's tolerance exactly (5.599m, same
37×37 grid) and still doesn't move the number.

Business NO-GO (session 1) is unaffected either way — it was already settled
independent of this experiment. See the correction note in the header: this
KILL closes off DINO-based per-vintage recentering (option B of the still-open
tight12-contamination decision in `TRACKER.md` slice 23); it does not itself
ship anything, and the size-stratified-widening alternative (option A,
foreseen name `chip_geom_v3_tight_scaled`) remains unbuilt and undecided.

## 5. Why it failed — directional findings (diagnostic value, not further scope)

- **Fix #4 (finer resolution) makes DINOv2 worse, not better**: 36.5%→15.3%
  raw (−21.2pp), 39.4%→14.6% centered (−24.8pp). The tolerance did shrink
  (5.6m→2.6m, a genuinely tighter bar) but recovery fell by more than half
  while the bar only tightened by ~54% — a real signal loss, not just a
  stricter pass line. Finer patch cells did not sharpen the correlation
  peak; if anything the correlation surface got noisier per-cell.
- **DINOv3 Gram-anchored + `sat493m` domain match, alone, is statistically
  flat vs. DINOv2** at matched resolution: 35.0% vs 36.5% raw, 38.7% vs 39.4%
  centered (Arm B vs. baseline) — deltas within single-run noise despite a
  14× larger, satellite-pretrained, Gram-anchored backbone. This was the
  best-motivated lever across all 3 sessions (Gram anchoring's whole point is
  preserving dense/local patch quality, which is the diagnosed bottleneck)
  and it produced no measurable lift.
- **Combining both (Arm C) partially rescues the resolution penalty**
  (15.3%/14.6% → 25.5%/29.9%) but still can't clear the coarse baseline —
  confirms the resolution penalty is not a DINOv2-specific position-embedding
  interpolation artifact; it reproduces on DINOv3 too.
- Net reading: the bottleneck is not "wrong backbone generation" or "patches
  too coarse" — both were independently and jointly fixed and neither moved
  the needle toward the kill bar. The likely cause is either a structural
  limitation of integer-shift argmax dense-token correlation itself, or a
  content/domain gap between GEHI and Vexcel imagery that off-the-shelf ViT
  dense features (DINOv2 or Gram-anchored/domain-matched DINOv3 alike) don't
  resolve. Fix #3 (intermediate-layer tokens) is now explicitly out of scope
  per the kill criterion, not merely deprioritized.

## 6. Carried-forward open item — confirmed still untested, not this memo's scope

Per the session-2 handoff: two cheap, zero-DINO levers ("arm 0" — widen the
phase-correlation search window itself; "arm 1" — accept + calibrate-correct
the PSR 8–12 "weak-lock" band instead of demanding a from-scratch
re-registration) were flagged as possibly higher-leverage than anything
DINO-based, since most of the dominant 2021+ population's PSR<12 failures sit
in the 8–12 weak-lock band, not total lock failure. Repo-wide search
(`scripts/`, `docs/`) for any prior implementation of either turns up
**nothing** — confirmed genuinely untested, not merely undocumented. Unrelated
to the DINO-coarse line closed by this memo; the next session picking up
install-date recall work should treat this as the open high-leverage
candidate, independent of and unaffected by the KILL verdict above.
