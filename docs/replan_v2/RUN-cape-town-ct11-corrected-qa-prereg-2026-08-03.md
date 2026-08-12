# Cape Town CT-11 corrected QA preregistration — 2026-08-03

Status: **package and acceptance thresholds frozen; owner-confirmed;
corrected images not yet reviewed**

This preregistration applies only to the corrected native-resolution,
boundary-complete CT-11 review. No corrected Pass-1 or repeat verdict has been
read at the time of writing.

## Frozen inputs

- 500 unique anchors; 52/52 grids; minimum five per grid.
- 1,498 pre-cutoff frames; exact claimed-boundary failures=0;
  post-2025-01-31 frames=0.
- Corrected Pass-1: 167 native PNG sheets, seed `20260804`, package SHA256
  `bb662d65571518891d41a389545003d79ed6a52f7670a40d5607b5dcac628627`.
- Fresh repeat: 100 unique anchors (20%), 34 native PNG sheets, selection seed
  `20260805`, fresh-order seed `20260806`, package SHA256
  `28b1eeffe3af30fa3923dea324cc07ba5d0ee53e3376bb14a9bfa0ef9832d6d5`.
- Assignment SHA256
  `1c84ab3a65eb1a161dfa90338d3e02421e35ce8dbef678b84347d12c35ce8d5f`.

Public sheet manifests contain no anchor IDs, production status, interval,
confidence, previous verdict, or model outcome. Private identity maps and all
package artifacts are hash-locked and read-only.

## Locked metrics

Primary product metric:

- exact production-vs-corrected first-visible-appearance bracket agreement on
  adjudicable records (`CONFIRM / (CONFIRM + SHIFT)`), with Wilson 95% CI.

Secondary metrics:

- inventory-standardized exact-bracket agreement over the frozen 21,453-row
  status-confidence population;
- SHIFT and UNDATABLE proportions;
- overall and class-specific Codex repeat state/interval agreement with Wilson
  95% intervals;
- status-confidence, coverage-class, area-bin, and per-grid diagnostics.

No pseudo-Wilson interval will be assigned to a directly standardized weighted
point estimate. The QA remains Codex-reviewed external-AI evidence, not human
gold truth or physical install-date accuracy.

## Owner-confirmed release gates

The minimum release gates are:

1. primary exact-bracket Wilson 95% lower bound **at least 70%**;
2. inventory-standardized exact-bracket point estimate **at least 75%**;
3. corrected-review UNDATABLE rate **no greater than 25%**;
4. repeat state-agreement point estimate **at least 80%** and Wilson 95% lower
   bound **at least 70%**;
5. zero boundary omissions, zero post-cutoff inference frames, complete
   provenance, and all structural CT-12 gates PASS.

The owner confirmed these gates with the message `确认门槛` at
`2026-08-02T21:01:04Z`, before any corrected sheet was viewed. They must not
be weakened after corrected verdicts are viewed. If any confirmed gate fails,
CT-12 remains HOLD; remediation must be evaluated on a new frozen holdout
rather than reusing this package as both tuning and final acceptance data.

The machine-readable lock is
`configs/ct11_corrected_acceptance_v1.json`. The review evaluator fails closed
unless `owner_confirmation.confirmed=true`. After owner confirmation, the
fail-closed validator returned PASS against the frozen corrected package. The
confirmed config SHA256 is
`770db9c49bc3a85e62a0b776ee596e66c1f443e32d08aaf4ea7e3f0c40ed5f74`.
