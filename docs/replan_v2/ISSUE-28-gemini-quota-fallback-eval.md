# ISSUE-28: Gemini per-model quota exhaustion — gemini-3-flash fallback evaluation

Status: open — owner evaluation pending, not started
Phase: 4 — Scan / GEHI provider
Blocked by: —
Opened: 2026-07-18 — recurring RUN 3 quota outage, diagnosed live in-session
(sub2api `ops_error_logs` + `accounts.extra` forensics, agy dashboard
cross-check, live gateway probes).

## Problem statement

`gemini-3.1-flash-lite` — the only model RUN 3 uses for both
`--round1-model` and `--round2-model` — sits behind an **independent,
per-model 5-hour rolling quota** that is invisible on the Antigravity CLI's
own "GEMINI MODELS" dashboard panel (that panel is explicitly scoped to
`Models within this group: Gemini Flash, Gemini Pro` — Flash-Lite is not a
member). Confirmed two ways:

- Live probe 2026-07-18: immediately after `docker restart sub2api`,
  `gemini-3-flash` returned a real `200` (`modelVersion=gemini-3-flash`);
  `gemini-3.1-flash-lite` returned a real, slow (30.7s round-trip) upstream
  `429 RESOURCE_EXHAUSTED` from `cloudcode-pa.googleapis.com` — i.e. Google
  itself still refused Flash-Lite while Flash was fully healthy.
- Owner's own agy dashboard check on two of the pooled accounts (`gao`,
  `botao`) showed Five-Hour Limit at 100% ("Quota available") during the
  same outage window — consistent with Flash/Pro being untouched while
  Flash-Lite (not shown on that panel) was fully exhausted underneath.

Sustained heavy scanning (`WORKERS_TOTAL=80`, `QPS_TOTAL=8`,
`--provider Merged --scorer gemini`) exhausts this hidden per-model quota
across all 11 pooled sub2api accounts **almost simultaneously** — observed
per-account `quotaResetTimeStamp` values cluster within ~40s of each other
(`2026-07-18T08:54:29Z`–`08:55:08Z`). This is not a per-account-independent
window; it appears to be a single 5h window that starts ticking from the
first sustained burst of Flash-Lite calls against the pool. Empirically:

**reset time ≈ scan launch time + 5h.** RUN 3 launched
`2026-07-18T03:54:34Z`; observed resets clustered at `08:54:29Z`–
`08:55:08Z` — a match within ~30-90s. This has now recurred on back-to-back
days at the same rough time-of-day:

- **2026-07-17 11:37-14:43 NZST** (documented in
  `scripts/validation/gemini_solar_image_review.py:263-280` — predates the
  pool-exhausted retry policy; fossilized **1,786 anchors** into
  `done_ambiguous_gemini_failed` before anyone intervened).
  `POOL_EXHAUSTED_MAX_ATTEMPTS`/`POOL_EXHAUSTED_RETRY_INTERVAL_SEC` were
  added afterward specifically to narrow this blast radius.
- **2026-07-18 (RUN 3, a24 lane)** — same pattern, ~3h05m dead window,
  caught within ~10min of onset and the run killed manually; only **38
  anchors** fossilized this time (retry policy + fast manual response
  limited the damage vs. 2026-07-17's 1,786).

### A second, compounding problem: sub2api's own coarse rate-limit key

sub2api caches cooldown state in Postgres
`accounts.extra.model_rate_limits`, keyed **both** per-model
(`"gemini-3.1-flash-lite"`) **and** by a coarse group key
(`"antigravity:gemini"`) that appears to mean "any Gemini model on this
account." Once Flash-Lite trips real `QUOTA_EXHAUSTED`, the coarse key gets
set too, and the gateway's routing layer (backed by Redis, not read live
from Postgres — clearing the Postgres `extra` field did **not** change
routing behavior) then rejects **every** Gemini model on that account with
an instant `503 "No available Gemini accounts: no available accounts"` —
including models (Flash, Pro) that Google itself has not throttled. Only a
full `docker restart sub2api` was confirmed to clear this; the admin API
(`/api/v1/admin/accounts/{id}/clear-error`,
`/api/v1/admin/accounts/batch-clear-error`) may do it more surgically but
was untested (our `SUB2API_ADMIN_TOKEN` session had expired).

## Impact

- Any fullscan run at `WORKERS_TOTAL=80`/`QPS_TOTAL=8` using Flash-Lite
  should budget for **one ~3h dead window landing ~2h after launch, then
  clearing at launch+5h** — recurring if the run spans multiple local
  day-cycles at this intensity. RUN 3's own wall-clock estimate (≈10h for
  41,393 anchors) is consistent with needing close to **two** 5h windows
  end-to-end from a cold start.
- Anchors scored during an unattended dead window fossilize into
  `done_ambiguous_gemini_failed` at whatever rate workers keep retrying
  before someone (human or agent) notices and kills the run — 1,786 anchors
  in the 2026-07-17 incident, versus 38 in 2026-07-18 (killed fast).
- `done_ambiguous_gemini_failed` anchors require the same identify + topup
  + rescan remediation as any other artifact-driven failure cohort (cf.
  `build_wayback_unresolved_subset.py`, `build_distillation_set.py`'s
  treatment of this stratum) — real, but bounded, rework.

## Proposed fallback: gemini-3-flash substitution

Confirmed live and reachable during today's Flash-Lite outage. Not yet
usable as a drop-in production substitute — three gaps:

1. **Thinking-level control isn't wired up.** `GeminiClientConfig`
   (`gemini_solar_image_review.py:851-865`) already supports
   `thinking_level`/`thinking_budget`, and `post_native_generate_content`
   threads them into the request — but `run_adaptive_scan.py::
   _load_gemini_config` never sets either field, so Flash would run with
   whatever its server-side default thinking behavior is (observed: a
   trivial 20-token ping burned 16 tokens on thinking alone).
   `GEMINI_MAX_TOKENS_PER_CHIP=4000` for real batched scoring calls is
   probably generous enough that this isn't a truncation risk, but adds
   unmeasured latency/cost at 41k-anchor scale. Needs: a
   `--thinking-level`/env knob threaded through `_load_gemini_config` into
   `GeminiClientConfig`.
2. **Unvalidated on this task.** Flash-Lite is the only model this
   pipeline's Q5.6 batch/matrix prompt and JSON schema have been tuned and
   accuracy-checked against. Flash's accuracy, JSON-schema compliance rate,
   and agreement with Flash-Lite on this specific vision-scoring prompt are
   unknown. Needs a small pilot (~30-50 anchors, same chips scored by both
   models) before trusting it at production scale.
3. **Switching mid-run breaks comparability.** RUN 3 exists to be
   apples-to-apples with RUN 2 (both Flash-Lite) per the RUN 3 doc's §7
   "Reconcile vs RUN 2" step. A mid-run swap to Flash for the unscored tail
   would produce a hybrid-model dataset unless deliberately scoped as a
   separate arm.

## Open questions for owner evaluation

1. Is a recurring ~3h/day Flash-Lite dead window acceptable as steady
   state — i.e., always launch expecting to auto-resume at launch+5h — or
   is a same-day fallback worth building?
2. If a fallback is wanted: full switch to `gemini-3-flash` for *future*
   runs (after the accuracy pilot), or an *automatic degrade-on-429* inside
   a single run (escalate from Flash-Lite to Flash only on the
   pool-exhaustion signature, mid-run)?
3. Scope for the accuracy pilot + `--thinking-level` plumbing, if approved.
4. Separately (lower priority, not blocking): is sub2api's coarse
   `"antigravity:gemini"` per-account block intended behavior, or worth
   reporting upstream / patching locally to be per-model instead of
   per-account? A per-model-scoped block would stop Flash/Pro from being
   collaterally knocked out by a Flash-Lite exhaustion.

## Evidence

- sub2api `ops_error_logs` + `accounts.extra.model_rate_limits`,
  2026-07-18 05:48–08:55 UTC window (11 accounts, synced reset timestamps).
- `scripts/validation/gemini_solar_image_review.py:248-280` (transport +
  pool-exhausted retry policy, documents the 2026-07-17 incident inline).
- Live probes, 2026-07-18 ~07:2x UTC: `gemini-3-flash` 200 OK
  post-`docker restart sub2api`; `gemini-3.1-flash-lite` 429
  `RESOURCE_EXHAUSTED` (real upstream, 30.7s) same window.
- `docs/replan_v2/RUN-fullscan-gemini-backdating-run3-2026-07-18.md` §5-6
  (RUN 3 wall-clock estimate, known-expected-outcomes).
