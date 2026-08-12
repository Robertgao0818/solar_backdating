# Cape Town CT-11 Codex-reviewed QA — 2026-08-03

Status: **initial QA execution complete; instrument remediation in progress; release gate HOLD**

Post-review instrument audit found that the 160×126 contact-sheet thumbnails
discarded critical spatial detail and that 60/325 `done_appears` strips omitted
at least one exact production boundary. Three same-input Gemini-tier pilots
also strongly disagree with the old Codex labels while agreeing with each
other substantially more often. Therefore the metrics below are retained as
diagnostic evidence, not a valid final release acceptance test. See
[`RUN-cape-town-ct11-instrument-diagnosis-2026-08-03.md`](RUN-cape-town-ct11-instrument-diagnosis-2026-08-03.md).

This report evaluates the frozen Cape Town high-density 52-grid cohort as a
`first-visible-appearance interval` product. It is Codex-reviewed QA against
frozen local imagery, not human gold truth, human inter-annotator agreement,
physical install-date accuracy, or city-wide Cape Town accuracy.

## Frozen review package

The corrected package is under
`goldset_ct_20260803/goldset_ct52_20260803_gridmin/` in the frozen production
root. It contains 500 unique anchors from all 52 grids, with at least five per
grid, 100 blind-repeat anchors, 600 assignment rows, and all 1,745 required
scan frames. The assignment manifest SHA256 is
`1c84ab3a65eb1a161dfa90338d3e02421e35ce8dbef678b84347d12c35ce8d5f`.

The independent first-pass review is frozen as
`blind_pass1_independent.csv` (SHA256
`f5e82a9ba4c3499f1e9a14af62d65bf23b71d0b96acea816f39432330a0a710d`).
The fresh-order blind repeat is frozen as `blind_repeat_independent.csv`
(SHA256
`1cbc427e3e95d40e33c727f24084ad57c97675fadf87e17468ee8df0afe29095`).
The repeat manifest SHA256 is
`06fd1830c8b7ac54520251f7995576d398325b226f5618c5c65a82ca579c876a`.

The final metrics and row-level joins are frozen in
`ct11_qa_metrics_20260803.json`, `ct11_group_metrics_20260803.csv`, and
`ct11_enriched_verdicts_20260803.csv`. Their hashes are recorded and verified
by `ct11_qa_outputs_20260803.sha256`.

## Method

The reviewer saw chronological strips without production status, confidence,
interval, or Gemini verdict. After the independent record was frozen, it was
joined to production using the following rules:

- `CONFIRM`: corrected and production intervals are exactly equal;
- `SHIFT`: a defensible dated, left-censored, or census-bound interval differs;
- `UNDATABLE`: the imagery cannot support a defensible interval.

Missing, ambiguous, or tree-obscured imagery was not treated as absence.
Post-cutoff imagery after 2025-01-31 was not used for inference. For
`ALL_ABSENT`, the corrected census-bound interval is the last visibly absent
frame through 2025-01-31. No artificial full dates were assigned to
left-censored cases.

## Headline results

| Measure | Result |
|---|---:|
| Unique reviewed anchors | 500 |
| Adjudicable anchors | 396 |
| CONFIRM | 61 |
| SHIFT | 335 |
| UNDATABLE | 104 |
| Exact-bracket agreement | 15.404% (61/396) |
| Wilson 95% CI | 12.183%–19.290% |
| Wilson half-width | 3.554 percentage points |
| Undatable rate | 20.800% (104/500) |
| Inventory-weighted bracket agreement | 15.500% |

Direct standardization over the 21,453-row deliverable's frozen
status-confidence strata gives weighted masses of 12.283% CONFIRM, 66.963%
SHIFT, and 20.754% UNDATABLE. This weighted result is a point estimate; no
pseudo-Wilson interval is assigned to it. All sampled production rows use the
Lite primary model; there is no rescue-model sample (`n=0`).

## Diagnostic strata

| Stratum | Exact agreement |
|---|---:|
| `done_appears × high` | 0.881% (2/227), Wilson 95% CI 0.242%–3.155% |
| `done_appears × medium` | 10.417% (5/48) |
| Census-bound | 38.028% (27/71) |
| Left-censored | 56.250% (27/48) |
| Dated bracket, combined | 2.545% (7/275), Wilson 95% CI 1.238%–5.160% |

Area-bin and all 52 per-grid results are retained in
`ct11_group_metrics_20260803.csv`. Many production `done_appears` cases were
reviewed as already present at the first visible frame or absent through the
last visible frame. This is evidence for a targeted interval-construction or
scoring diagnosis; this report does not assign a cause without that diagnosis.

## Blind-repeat stability

The 100-anchor repeat is exactly 20% of the unique sample and used a new
review run, randomized presentation order, freshly rendered sheets, and no
access to pass-1 verdicts.

| Codex repeat measure | Result |
|---|---:|
| Independent state agreement | 72.0% (72/100), Wilson 95% CI 62.51%–79.86% |
| Independent interval agreement | 72.0% (72/100), same CI |
| Formal verdict agreement after join | 83.0% (83/100), Wilson 95% CI 74.45%–89.11% |
| Formal interval agreement after join | 72.0% (72/100), same CI as above |

These are Codex repeat-agreement measures of review stability, not
inter-annotator agreement.

## Gates and decision

The sample-size gate (`n>=300`), 52-grid coverage gate, 20% repeat gate,
manifest/provenance checks, and the predeclared precision gate (headline
Wilson half-width no greater than 5.5 percentage points) pass.

The Cape Town product acceptance lower bound was not preregistered before the
QA results were read. It therefore cannot be selected retrospectively. In
addition, observed exact-bracket agreement is only 15.404%, including 2.545%
for combined dated brackets. The initial CT-11 review is complete as an
executed diagnostic, but CT-11 is not complete as a valid release QA task. Its
release decision is **HOLD**. CT-12 must not publish the release candidate and
the downstream full-Cape-Town imagery download must not start without a
documented diagnosis/remediation, a valid re-evaluation gate, and owner
go/no-go.
