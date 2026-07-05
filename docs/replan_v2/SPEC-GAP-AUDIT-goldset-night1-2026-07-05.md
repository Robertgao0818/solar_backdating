# Gold-set night-1 feedback → v2 PRD gap audit (2026-07-05)

Status: **draft-for-owner-review** (owner directive: stop salvaging old outputs;
make the new SPEC carry a complete production scheme)
Author: agent session, night of first gold-set adjudication
Inputs: (i) owner's three observed failure patterns after adjudicating ~150
anchors (batch `real_20260705`, annotator A, pages p01/p02/p05); (ii) same-night
quantitative checks on the production chip-group manifest and census gpkg
(commands + numbers below, reproducible).

## 1. Owner-observed patterns (night 1)

- **P1 — marker/circle off the PV.** Merged nearby installations put the anchor
  marker on bare ground; the 10 m circle contains no PV; the scorer judges the
  marked location and fails. Measured in night-1 verdicts: `marker_off_target:`
  notes on 21/145 exported records (~14%; quota-only 18/139 ≈ 13%).
- **P2 — latest-absent frame looks wrong**, and many anchors lack a 2023
  context frame (wayback/CoJ row missing).
- **P3 — no imagery before ~2018 at some locations** → the true interval has no
  observable left endpoint.

## 2. Quantitative anchors (verified this session)

Production manifest `jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups`
(15,859 chip groups / 41,393 targets) and the fpcut census gpkg (41,393 polygons):

| check | result |
|---|---|
| multi-target chip groups | **11,335 / 15,859 = 71.5%** |
| `max_target_offset_m` from group centroid | p50 = 9.9 m, **45.2% > 12 m**, 17.9% > 24 m, max 55.4 m |
| targets outside the 96 m chip half-window (48 m) | 22 groups |
| polygon centroid **not inside** its own polygon | 98 / 41,393 = **0.2%** |
| MultiPolygon features in fpcut gpkg | 0 |
| footprint exceeds a bare 12 m v2 crop window | 12.8% (matches ISSUE-19's ~12% guard band) |

**Reading.** P1 is a **group-level** phenomenon, not a polygon pathology: merged
census polygons are single parts and their own centroids are on-panel in 99.8%
of cases. The off-target marker comes from the **chip-group centroid** (the
production scan/dating unit) sitting between member installations that are
routinely 10–55 m away, with a constant 10 m marker radius (ISSUE-19 audit F1).
The gold-set strip inherits that marker, so the human sees exactly what the
scorer was asked to judge — and ~13% of the time it marks bare ground.

## 3. Pattern → SPEC mapping

### P1 — partially covered; one structural gap

Covered by the PRD: D16–D18 + the ISSUE-19 decision fix the **per-target render
geometry** (`chip_geom_v2_tight12`, footprint-containment guard, provenance).

**GAP-1 (structural, unowned by any D-decision): the production dating unit.**
Production scan states / `install_intervals.csv` are keyed by **chip group**;
71.5% of groups hold ≥2 distinct installations. Consequences the SPEC never
addresses:
1. marker/circle at group centroid → the P1 failure class (~13% measured);
2. D2's single monotone changepoint is assumed **per anchor**, but a
   multi-install group can have several true install dates — the model is
   mis-specified on 71.5% of units by construction;
3. the deliverable assigns one date to up to N installations.

Note the tight-crop v2 render (target-centered) does *not* inherit the group
centroid — but Phase-3/4 labels harvested from group-level scan states do
(cross-unit supervision; see §5 dinov3 check).

### P2 — mostly covered; two bounded gaps

Covered: D5/D6 (verdict store + churn monitor), D17 (resolution provenance +
effective-resolution sentinel), D9 (municipal true-flight-date brackets — the
arbiter for wrong-looking GEHI dates), decoder posterior absorbs per-frame
verdict noise.

**GAP-2: per-frame vintage integrity.** A GEHI frame's nominal capture date can
misrepresent the pixels (mosaic patchwork / phantom vintages — known GEHI
behaviour). D17's sentinel detects resolution substitution, not **date**
substitution. Nothing in the SPEC checks that a frame dated `t` shows imagery
from epoch `t`.

**GAP-3: context-frame coverage.** D9 says "for every dated anchor in the JHB
inventory"; in practice CoJ/Wayback context chips exist only for the ISSUE-09
audit cohort — the night-1 package hit 1,026 `source_missing` context frames
because the gold sample legitimately draws beyond that cohort. Coverage is an
execution rule the SPEC should state, not assume.

### P3 — covered by design; one reporting gap

The decoder already represents left-censoring (τ at/before the first observed
epoch is an enumerable cell; Turnbull prior consumes censored intervals; D19
fractional channel propagates the mass). D11's claim phrasing absorbs cadence
censoring.

**GAP-4: the deliverable does not split the boundary masses.** P(τ before
window start) ("present in earliest frame" — an old install) and P(τ beyond
window end) ("never appeared") have opposite economic meanings but both read
as "undated" today. Per-anchor `window_start`/`window_end` (earliest/latest
scored vintage) should ride the output table. (Verify whether ISSUE-22's
fractional table already carries these; add if absent.)

## 4. Proposed amendment candidates (owner sign-off required)

- **A (GAP-1) — dating unit becomes the installation (target), not the chip
  group.** Group demoted to a chip-download batching construct. Marker =
  target polygon centroid (on-panel 99.8%; fall back to point-on-surface for
  the 0.2%). Requires per-target scan states/intervals; composes with D12.ii
  (chip-target manifest is already per-target), D18 (versioned, cache-aware
  migration — new unit ⇒ new crop bytes ⇒ new verdict keys, exactly the
  ISSUE-19 chain), and Phase-4's exhaustive-grid shape (score per target
  natively). This is the single highest-leverage change the new production
  scheme should carry.
- **B (GAP-2) — vintage-integrity sentinel.** Extend the D17 sentinel set with
  cross-source date-consistency checks: at epochs where a municipal / Wayback
  true-dated capture exists, a GEHI frame of matching nominal date must agree
  on gross scene state; disagreement rate is monitored like hash churn.
- **C (GAP-3) — coverage rule for the accuracy channel.** Any anchor sampled
  into a gold set or audited under D9 gets its context frames fetched on
  demand (bounded, idempotent); "context row silently absent" is a package
  defect, not an acceptable state.
- **D (GAP-4) — deliverable schema.** Split boundary masses
  (`p_pre_window` / `p_post_window`) and carry `window_start`/`window_end`
  per anchor in the fractional table.

## 5. Impact on in-flight work

- **ISSUE-11 gold set: continue unchanged.** The `marker_off_target:` /
  `left_censored:` notes conventions (adopted night 1) turn P1/P3 into
  measured evidence channels for GAP-1/GAP-4. The ~13% P1 class measures the
  shipped group-level production — it strengthens the run's value as the
  baseline; it does not invalidate it.
- **dinov3 Phase-3 (unit-alignment check):** distillation labels are harvested
  from **group-level** scan states while student chips are rendered
  **per-target** — verify how dinov3 ISSUE-02 aligned units; if unaddressed,
  add to the ISSUE-12 amendment checklist alongside the existing
  label-geometry caveat.
- **No rescue of `jhb_full382` fpcut outputs** (owner directive 2026-07-05):
  fixes land in the new production scheme; old outputs remain as the baseline
  the gold set measures.

## 6. Reproduce the numbers

```bash
# group/offset stats: chip_groups_as_anchors.csv (n_targets, max_target_offset_m)
# centroid + 12 m containment: fpcut gpkg → to_crs(32735), centroid.within(geometry)
# night-1 verdict tallies: load_verdict_manifest_export over goldset_real_20260705/verdicts/
```
