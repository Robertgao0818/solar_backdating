# Verdict store — content-addressed scorer memoization (ISSUE-07, PRD D6)

`scripts/temporal/verdict_store.py`. Memoizes presence verdicts at the
`PresenceScorer` seam: every scoring call consults a content-addressed KV
store first, so an identical re-run replays byte-identical results and pays
only for never-seen chips. A cached verdict can never drift — this is the
program's version-drift kill, delivered before any student model exists, and
it makes multi-rep protocols nearly free after rep 1.

## Key

```
key = sha256(canonical_json({
  chip_sha256,          # pixel identity (see below)
  scorer_name,          # seam registry identity ("gemini", "dry_run", student, ...)
  model_id, api_format, # resolved from the config passed AT CALL TIME (never env)
  prompt_config_hash,   # scorer.prompt_config_fingerprint(mode, config) digest —
                        #   same payload as the ISSUE-06 sidecar's hash, so
                        #   sidecar rows and store keys line up in drift audits
  scoring_mode,         # "batch" | "sequence" | "matrix"
  extras,               # call-time instruction inputs outside the config object
}))
```

**Correctness condition (PRD D6): key by pixel hash, never by
capture-date/version metadata.** The imagery provider re-renders pixels under
stable metadata; a metadata key would serve a stale verdict for changed
pixels. Under pixel keying a re-render is simply a cache miss (re-scored),
and the churn monitor below makes that visible instead of silent.

`extras` closes the gap between the config-level prompt hash and the
instruction actually executed:

| mode | extras |
|------|--------|
| batch / `score()` | `census_mid_date_iso` (renders the census-calibration clause) |
| sequence | ordered window `capture_dates` (rendered into the prompt), `max_tokens` |
| matrix | ordered window `capture_dates`, ordered `[target_id, target_label]` list |

## Memoization grain

- **batch / `score()` — one record per chip**, in one normalized shape
  (`chip_verdict`) shared by both entrypoints, so a verdict stored via
  `.batch` replays through `.score()` and vice versa. `chip_index` / seam
  `index` / `capture_date` are restamped from the requesting pick at replay.
  Batch-composition effects (sibling chips in the same call, the
  census-reference clause pointing at one of them) are deliberately flattened
  to the per-chip grain: identical re-runs have identical composition, and
  across compositions the first-seen verdict is the pinned one.
- **sequence / matrix — one record per scored window** (`sequence_verdict` /
  `matrix_verdicts`): the verdict is target-level over the whole ordered chip
  set, so the pixel identity is the canonical hash of the ordered per-chip
  hash list. All-or-nothing: one changed chip misses the whole window.

Never cached:

- verdicts whose `decision_source` is in the scorer's declared
  `failure_decision_sources`, or that carry an `error` — a transient API
  failure must be re-scored, not replayed forever (matrix windows are cached
  only when every cell succeeded);
- chips whose bytes cannot be hashed (missing/unreadable path). Dry-run picks
  carry `chip_path=""`, so a store never changes dry-run output.
- `raw_response` (transport debugging payload) is not memoized — nothing
  persisted reads it back (it lives in per-run audit JSONLs at scoring time);
  replayed results carry `raw_response=""`.

## Wrap order with the ISSUE-06 provenance sidecar

```python
scorer = with_scoring_provenance(with_verdict_store(scorer, store), writer)
```

Provenance outermost, verdict cache inner. Cache hits then still emit one
sidecar row per chip, so every verdict in every run stays attributable; the
sidecar's `prompt_config_hash` equals the store key's component by
construction. (The reverse order would silently drop sidecar rows on hits,
violating ISSUE-06's "every call emits rows".)

## Call-sites

| script | default | store path default |
|--------|---------|--------------------|
| `run_adaptive_scan.py` | **on** (`--no-verdict-store` to disable) | `<scan-states parent>/verdict_store.jsonl` |
| `run_census2023_scan.py` | **on** (`--no-verdict-store`) | `<output parent>/verdict_store.jsonl` |
| `score_target_sequence.py` | **on** (`--no-verdict-store`; default gemini branch only, like the sidecar) | `<output parent>/verdict_store.jsonl` |
| `score_chip_group_matrix.py` | **on** (`--no-verdict-store`; default branch only) | `<output parent>/verdict_store.jsonl` |
| `fullstack_noscan_run.py` | **OPT-IN** (`--verdict-store PATH`) | — |

`fullstack_noscan_run` is deliberately opt-in: that harness measures
rep-to-rep scorer noise, and a store would replay rep 1's verdicts for every
later rep, collapsing the variance being measured. The same reasoning applies
to any deliberate independent-replication protocol elsewhere: point each rep
at a fresh store path or pass `--no-verdict-store`.

Every run prints cache accounting at exit
(`hits / misses / stored / inner_calls / uncacheable / failures_not_cached`);
"second identical run issues zero scorer calls" is `inner_calls == 0` on the
re-run (also asserted by `tests/temporal/test_verdict_store.py`).

## Concurrency

Safe under the production parallelism pattern: one process, many anchor
worker threads sharing one `VerdictStore` (index/append/counters all mutate
under a `threading.Lock`). Cross-process access is a **single-writer
constraint, enforced** — opening for write takes a non-blocking `fcntl`
exclusive lock on `<store>.lock`, and a second writer process raises
immediately with a clear message. `read_only=True` opens are allowed
alongside a writer (they see the file as of open time and reject `put`).

Caveat: `flock` is advisory and unreliable on network filesystems — on a
RunPod `/workspace` (MFS) volume the enforcement is a no-op. Keep store files
on local disk (the CLI defaults do), or treat single-writer as a convention
there.

## Store size & compaction

Append-only JSONL, one row per verdict
(`{record_version, ts_utc, key, key_fields, record_type, record}`;
`key_fields` is kept verbatim for drift audits), fully indexed in memory on
open, last-write-wins per key.

Record-per-frame at cohort scale: the JHB cohort is ~11.5k anchors at ~15–25
scored frames each → **~200–300k `chip_verdict` rows**. Rows are ~0.5 KB
(verdict fields + key_fields; no raw_response) → **~100–150 MB** on disk and
a similar order in RAM while open. Census-narrowing sequence windows add one
window row per anchor (~10k rows). Repeated re-runs only append when a key is
new, so steady-state growth is churn-driven; superseded duplicate-key rows
accumulate only if records are overwritten (same key re-put) — reclaim with:

```bash
python scripts/temporal/verdict_store.py compact --store <store.jsonl>
```

If the cohort ever grows to the point where the in-memory index hurts
(~10× current scale), move the index to sqlite; the row format is already
key-addressed so the migration is mechanical.

## Churn monitor (sentinel chip set)

Fixed sentinel set → baseline manifest → periodic sweep:

```bash
# once: pin a stable, spread sample of scored chips
python scripts/temporal/verdict_store.py sentinel-init \
  --manifest sentinels.json --chips-file sentinel_chips.txt

# each sweep: re-download the same frames, then
python scripts/temporal/verdict_store.py sentinel-check --manifest sentinels.json
# exit 2 + alert=true when churn_rate > --alert-churn-rate (default 0.0: any churn alerts)
```

Churned frames are reported with `escalation="rescore_deliberate"`: they will
miss the store anyway (new pixel hash), but the escalation class turns a
silent re-score under a possibly-newer model into an operator-visible event.
Missing sentinel files are a separate class (local eviction, not provider
churn) and are excluded from the churn-rate denominator.

## Replay gate

Byte-identity of a re-run is checked two ways:

- `tests/temporal/test_verdict_store.py::test_replay_gate_byte_identical_scan_states_zero_calls`
  runs a full adaptive-scan anchor twice through one store (timestamps
  frozen): scan states are byte-identical and the second run makes zero inner
  scorer calls.
- operationally:
  `python scripts/temporal/verdict_store.py replay-diff <scan_states_A> <scan_states_B>`
  compares two scan-state dirs ignoring only the volatile `started_at` /
  `updated_at` fields; exit 0 == replay-identical.

## Backfill limitation

Inherited from ISSUE-06 (see `docs/resolution_provenance.md`): scans predating
the scoring-provenance sidecar recorded no scorer identity, so their verdicts
can never be seeded into the store. Replay coverage begins at sidecar
deployment.
