# ISSUE-19: Chip-geometry policy at the Phase-3 re-render

Status: done
Phase: 3 — Student
Blocked by: ISSUE-04, ISSUE-12

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D18. User story 38.
Evidence: 2026-07-03 chip-geometry & resolution audit (amendment block in the PRD).

## What to build

Production chips are a fixed 96 m square regardless of installation size
(footprints span 3.7–3,376 m²); the adaptive-size formula never shipped
beyond an orphaned pilot script. Per owner decision (D18), this is **not**
hotfixed in the legacy builder — chip pixels are verdict-store keys and the
banked reps' comparability depends on the frozen geometry. Instead, chip
geometry becomes an explicit, versioned, provenance-recorded parameter of the
Phase-3 chip re-render path (chip-target manifest → renderer).

Consume the tight-crop experiment's result (slice 4) to decide the render
geometry policy — frozen 96 m, a global tighter crop, or per-installation
adaptive — wire the chosen policy into the re-render path with a recorded
geometry version, and document the cache-migration consequence: a geometry
change re-keys the verdict store deliberately, never silently.

## Acceptance criteria

- [x] Written geometry decision referencing the tight-crop experiment's numbers (accuracy delta vs crop size) — [`ISSUE-19-geometry-decision-2026-07-04.md`](ISSUE-19-geometry-decision-2026-07-04.md)
- [x] Re-render path takes geometry as an explicit versioned parameter recorded in provenance — `--chip-geometry <version>` on `score_target_sequence.py` resolves the param triple via `scripts/temporal/chip_geometry.py` and records `geometry_version` in the scoring-provenance `context` blob
- [x] Verdict-store key implications of a geometry change documented (deliberate migration, not silent re-keying) — decision memo "Cache / verdict-store consequence chain" + `chip_geometry.py` module docstring; regression-pinned (`geometry_version` absent from `build_verdict_key`, re-keys only via `chip_sha256`)
- [x] Legacy builder untouched; banked-rep comparability preserved (frozen 96 m remains the default until the decision says otherwise) — `build_inventory_chip_groups.py` unchanged; `chip_geom_v1_banked96` stays the legacy default, `chip_geom_v2_tight12` is the Phase-3 re-render default

## Blocked by

- [ISSUE-04](ISSUE-04-reliability-panel-repair.md) (tight-crop experiment supplies the evidence)
- [ISSUE-12](ISSUE-12-student-prd-amendments.md) (chip-manifest re-render dependency made explicit)
