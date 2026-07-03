# ISSUE-01 — PresenceScorer seam + adaptive-path injection

> **SUPERSEDED (2026-07-03)** by
> [`../replan_v2/ISSUE-05-presence-scorer-seam.md`](../replan_v2/ISSUE-05-presence-scorer-seam.md).
> The fact-check found **four** scorer call-sites (this issue covered one plus
> a partial), and the seam now serves the whole optimization-v2 program
> (verdict store, escalation compositions), not only the distillation. Do not
> implement from this file; slices 3+ should treat replan_v2 ISSUE-05 as the
> seam dependency.

> Tracer slice 1 of 8 · [TRACKER](TRACKER.md)

## Parent

PRD [`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md)
§D1 (the seam) + Testing Decisions (seam contract test). User stories 6, 7, 12, 14.

## What to build

Introduce one `PresenceScorer` callable seam that **both** scoring paths call,
and promote the production **adaptive-scan path** from a hardwired Gemini import
(its per-round batch-score call) to an **injected** scorer — mirroring the
**sequence path**, which already injects via a `scorer=` parameter. Wrap the
existing Gemini batch scorer so it satisfies the seam, so behaviour stays
byte-identical. This slice changes **structure only**, not answers — it is the
prefactor that makes every later swap a one-line config point.

The contract (from the PRD — this is the decision-bearing shape, not an impl):

```
# Input: ordered chips for ONE roof across dates (anchor-centered, same crop the pipeline renders)
picks: list[Pick]            # Pick: chip_path, capture_date, version, actual_zoom
# Output: one observation per pick, in order
class PresenceObservation:
    pv_present: bool | None   # None = abstain
    pv_score:   float         # continuous, calibrated; abstain band derived from this
    quality_flag: str         # "usable" | "ambiguous" | "unusable"
    decision_source: str      # e.g. "dinov3_frozen" | "gemini_batch"

def score(picks, *, config, ...) -> list[PresenceObservation]: ...
```

The Gemini wrapper maps its existing per-chip record (`pv_present` /
`confidence`→`pv_score` / `quality_flag` / `decision_source`) into a
`PresenceObservation`; the adaptive-scan round mapping persists it into the
existing round record unchanged, so `scan_state.json` keeps its exact schema and
dip-repair / interval inference / Vexcel clamp run on unmodified code.

## Acceptance criteria

- [ ] A `PresenceScorer` contract (`Pick` + `PresenceObservation` + `score(...)`) is defined in one place, importable by both scoring paths.
- [ ] The Gemini batch scorer is wrapped to satisfy the contract; the adaptive-scan path receives its scorer by injection — no hardwired local import inside the round loop.
- [ ] Seam contract test: a stub scorer returning canned observations drives the adaptive-scan path and produces the expected round sequence / `scan_state.json` (mirrors the sequence path's `fake_scorer` and the batch `poster=` stub patterns).
- [ ] Downstream-invariance test: identical canned observations routed through the existing mapping yield identical `scan_state.json`, proving dip-repair / interval / clamp are unperturbed.
- [ ] Running a small real anchor through the adaptive path with the Gemini scorer behind the seam reproduces the pre-refactor `scan_state.json` (no answer change).
- [ ] `scan_state.json` schema unchanged; existing temporal tests still pass.

## Blocked by

None — can start immediately.
