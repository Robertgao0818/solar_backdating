# Cape Town CT-11 corrected Codex-reviewed QA — 2026-08-03

Status: **corrected holdout complete; acceptance failed; CT-12 and full-Cape-Town GEHI HOLD**

This is the result report for the immutable gates preregistered in
[`RUN-cape-town-ct11-corrected-qa-prereg-2026-08-03.md`](RUN-cape-town-ct11-corrected-qa-prereg-2026-08-03.md).
The review remained blind to production outcomes and private identity maps.
Pass-1 was frozen before the separately ordered repeat sheets were opened;
both blind outputs were frozen before any production join was performed.

## Frozen review outputs

- Pass-1: 500/500 records, SHA256
  `822c79fc85ae90877864dc413262c3112ac508c8b31135a9e5081a802c59e4a0`,
  mode `0444`.
- Repeat: 100/100 records, SHA256
  `36be288d8587b6245c717728a27da91ac5bd5e1c189019b2287f526781445350`,
  mode `0444`.
- Acceptance config: SHA256
  `770db9c49bc3a85e62a0b776ee596e66c1f443e32d08aaf4ea7e3f0c40ed5f74`.
- Corrected Pass-1/repeat package hashes: `bb662d65…` / `28b1eeff…`.
- Assignment hash: `1c84ab3…`.

Pass-1 classes were 222 `ALREADY_PRESENT`, 171 `ALL_ABSENT`, 60
`TRANSITION`, and 47 `UNDATABLE`. Repeat classes were 46
`ALREADY_PRESENT`, 29 `ALL_ABSENT`, 12 `TRANSITION`, and 13 `UNDATABLE`.
No reviewed date is after 2025-01-31.

## Locked result

| Gate | Actual | Required | Result |
|---|---:|---:|---|
| Exact-bracket Wilson 95% lower bound | 17.270% | at least 70% | FAIL |
| Inventory-standardized exact bracket | 20.827% | at least 75% | FAIL |
| Corrected-review `UNDATABLE` | 9.400% | no greater than 25% | PASS |
| Repeat state agreement | 68.000% | at least 80% | FAIL |
| Repeat state Wilson 95% lower bound | 58.337% | at least 70% | FAIL |
| Exact claimed-boundary failures | 0 | 0 | PASS |
| Post-cutoff frames | 0 | 0 | PASS |

The primary exact-bracket result is 94 `CONFIRM`, 359 `SHIFT`, and 47
`UNDATABLE`: 94/453 = 20.751%, with Wilson 95% CI 17.270%–24.723%.
The inventory-standardized point estimate is 20.827%; no pseudo-Wilson
interval is assigned to this directly standardized estimate. Repeat interval
agreement is 65.000%, with Wilson 95% CI 55.254%–73.636%.

## Decision and policy consequence

The corrected CT-11 holdout fails four immutable gates. CT-12 remains HOLD,
the Top-52 release candidate must not be published, and the downstream full
Cape Town GEHI date-acquisition/download run must not start. The failed
holdout must not be tuned or reinterpreted into a pass. Any remediation must
be evaluated against a newly frozen holdout and must receive a new owner
go/no-go before release or full-Cape-Town download.

This is `Codex-reviewed QA` over first-visible-appearance intervals. It is not
human gold truth, physical install-date accuracy, human inter-annotator
agreement, or city-wide Cape Town accuracy.

## Evaluation artifacts

The evaluation root is
`production_lite_v2_20260731/goldset_ct_20260803/ct11_corrected_codex_review_20260803/evaluation/`.

- `ct11_corrected_metrics.json`: `ccee6364d5465e9d30a07ae6f6ffe0fc6205087c30735b124ee8087da2cf4da3`
- `ct11_corrected_joined.csv`: `9124c6b7016f4e0f022f78f6174c00b810d7c035706f25689c1223da91955d74`
- `ct11_corrected_outputs.sha256` verifies both outputs; all three files are
  frozen mode `0444`.

The reproducible evaluator is
`scripts/temporal/evaluate_ct11_corrected_review.py`.
