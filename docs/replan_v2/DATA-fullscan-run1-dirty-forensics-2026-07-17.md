# DATA — Fullscan Gemini backdating run 1: dirty-run forensics + rerun preflight (2026-07-17)

Parent: [`RUN-fullscan-gemini-backdating-2026-07-15.md`](RUN-fullscan-gemini-backdating-2026-07-15.md) ·
Archive: `~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/dirty_run1_archive_2026-07-17/`
(lanes `a24_v4`, `a48`, `a24_v6` + `monitor_status.md`) ·
Launcher (read-only, current): `scripts/temporal/run_fullscan_backdating.sh`

**Question:** was run 1 (the full 41,393-anchor Gemini backdating pass launched
2026-07-15 21:55 UTC) contaminated badly enough to discard, and what does a
clean rerun need?

**Answer: yes, discard run 1 entirely.** Two independent faults compounded:
(a) a genuine sub2api gateway incident (2026-07-16 22:46–23:30 NZST) plus a
much larger volume of **all-day, pre-incident** transient failures that a
no-backoff retry policy fossilized into terminal `done_ambiguous_gemini_failed`
states — only **7.2%** of terminal-failed states (34/473) actually fall
inside the incident window; and (b) lane `a24_v6` hard-stalled in a GEHI
403-ban backoff from 23:36 NZST with only 177/15,626 anchors processed
(1.1%). Owner decision: discard, patch (transport retry backoff + strict
no-live-GEHI mode), clean rerun at **60 workers / 8 qps total**.

---

## 1. Terminal status histogram (archived ground truth, full pass — not sampled)

| status | a24_v4 (of 20,696) | a48 (of 5,071) | a24_v6 (of 15,626) |
|---|--:|--:|--:|
| `done_installed_during_census` | 7,547 | 2,337 | 136 |
| `done_appears` | 3,594 | 1,796 | 10 |
| `done_ambiguous_nonmonotonic` | 580 | 427 | 4 |
| `done_ambiguous_no_recent_anchor` | 430 | 55 | 5 |
| `done_ambiguous_gemini_failed` | 240 | 233 | 0 |
| `done_already_present_before_geid_history` | 224 | 223 | 2 |
| `scanning` (non-terminal, in-flight at kill) | 20 | 0 | 20 |
| **total scan_state files** | **12,635 (61.1%)** | **5,071 (100%, clean complete)** | **177 (1.1%)** |

The 20 `scanning` states in `a24_v4` and `a24_v6` exactly match
`--anchor-workers 20` — one frozen in-flight anchor per worker thread at kill
time, consistent with a clean-ish kill (no corrupted/partial JSON found).
`a48` has zero `scanning` states because it ran to completion before being
archived (confirmed by `monitor_status.md` check #9: "a48 finished at
23:34:53 NZST").

**Correction to the preliminary headline given for this task:** `a24_v4`'s
archived state count is **12,635**, not the 10,928 quoted — the quoted number
was evidently a stale snapshot (the monitor's own check #10 at 00:10 NZST
already showed 11,849, and the archive was taken 8 minutes later at 00:18
during a measured ~5,150/hr burst, which accounts for the gap). `a48`'s
quoted numbers (5,071 / 233 / 3,188) match the archive exactly. `a24_v6`'s
quoted 176 vs. archived 177 is within one state of timing noise.

## 2. Terminal `done_ambiguous_gemini_failed` count

**473 total** across all lanes: a24_v4 = 240, a48 = 233, a24_v6 = 0.

## 3. Partial contamination (normal `done_*` status, but ≥1 chip-round result carries `decision_source: gemini_failed`)

Parsed JSON (`rounds[].results[].decision_source`), not grepped — the raw
string `"gemini_failed"` also appears as a full match inside the fully-failed
terminal status name, so the two must be subtracted to isolate partial
contamination:

| lane | fully-failed (`done_ambiguous_gemini_failed`) | partial (other `done_*`, ≥1 tainted chip) | string-match total | states processed |
|---|--:|--:|--:|--:|
| a24_v4 | 240 | **4,071** (32.2%) | 4,311 | 12,635 |
| a48 | 233 | **2,955** (58.3%) | 3,188 | 5,071 |
| a24_v6 | 0 | 14 (7.9%) | 14 | 177 |

`a48`'s partial-contamination rate (58.3% of all its processed anchors have
at least one tainted chip) is markedly worse than `a24_v4`'s (32.2%) despite
a48 being the lane that ran to clean completion — consistent with a48 sharing
the IPv6/GEHI family that also produced the a24_v6 stall, i.e. a48's
underlying egress path was under more sustained stress all day, it just
never fully wedged the way a24_v6 did.

## 4. Failure etiology — two distinct layers, easy to conflate

**This is the single most important forensic finding.** The scan_state
field `decision_source == "gemini_failed"` is a catch-all label covering two
structurally unrelated failure modes, and the majority of occurrences are
**not** a Gemini/gateway problem at all.

### 4a. Chip-round result level (10,287 `decision_source: gemini_failed` rows, all lanes, full pass)

| root cause | n | % | detail |
|---|--:|--:|---|
| **GEHI-side, pre-Gemini** (`notes` starts `download_failed:`) | **8,139** | **79.1%** | 8,051 × "GEHI succeeded but output file empty/missing at z=18" (a systematic z18 chip-coverage gap in the pre-downloaded basemap — the scan has no live fallback to actually fetch it); 76 × GEHI tool subprocess `TimeoutExpired`; 12 × `vintage_check_failed` (capture date not in offline catalog) |
| **Genuine post-chip Gemini failure** (`notes` starts `gemini_failed: per_image_call_error:`) | **2,148** | **20.9%** | 1,183 × `ReadTimeout`; 618 × `429 Too Many Requests`; 347 × `502 Bad Gateway` |

I.e. **4 out of 5** "gemini_failed" chip results never reached Gemini at
all — the chip simply wasn't servable from the pre-downloaded basemap at
z=18. This is orthogonal to the gateway incident and to the retry-backoff
fix; it's a chip-coverage gap that strict no-live-GEHI mode should either
route around (fall back to an available zoom) or at minimum should stop
being folded into the same status label as real Gemini failures, since it
will otherwise keep polluting future contamination stats the same way.

### 4b. Audit-sidecar attempt level (40,364 LLM-call attempts logged, full pass — not sampled, ran in ~2s so no sampling was needed)

| category | n | % | `error` signature |
|---|--:|--:|---|
| clean_attempt | 38,429 | 95.21% | — |
| transport | 1,932 | 4.79% | 1,273 `ReadTimeout`, 454 `429`, 205 `502/Bad Gateway` |
| schema/parse | 3 | 0.01% | `JSONDecodeError` (2) + non-null `missing_indices` (1) |
| other | 0 | 0% | — |

By attempt stage (this is where the no-backoff signature shows up):

| stage | n attempts | error rate |
|---|--:|--:|
| `batch_attempt_1` (first try) | 35,922 | 1,081 failed (3.0%) |
| `batch_attempt_2` (immediate retry, no backoff) | 1,084 | **851 failed (78.5%)** |
| `per_image_fallback` (per-chip recovery path) | 3,358 | 0 failed (100% clean) |

`batch_attempt_2`'s 78.5% failure rate is the retry-storm signature the
owner's "no-backoff retry policy fossilized transient failures" diagnosis
predicted: retrying instantly into a gateway that's still down just
reproduces the same failure. `per_image_fallback` — the eventual recovery
path, invoked only after a batch attempt already failed — never itself
fails, which is why most `gemini_failed` chip results (§4a) trace to
*download*-stage failures rather than this recovery path exhausting.

## 5. Hourly distribution of terminal-failed states (confirms all-day fossilization, not just the incident window)

mtime of each `done_ambiguous_gemini_failed` scan_state file, NZST, all
lanes combined (n = 473):

```
07-16 11:00   2  #
07-16 13:00   9  #####
07-16 14:00   6  ###
07-16 15:00  15  ########
07-16 16:00  16  ########
07-16 17:00  24  ############
07-16 18:00 126  ###############################################################
07-16 19:00 110  #######################################################
07-16 20:00  23  ############
07-16 21:00  88  ############################################
07-16 22:00  37  ###################
07-16 23:00  17  #########
```

**Only 34/473 (7.2%) terminal-failed states have an mtime inside the
22:46–23:30 NZST gateway-incident window; 439/473 (92.8%) fall outside it**,
spread from 11:00 through 23:00. The two biggest spikes (18:00, 19:00 —
combined 236/473 = 49.9%) land right after the 16:23/16:55 NZST offline-TM
/offline-Wayback mid-run restarts, well before the incident. A parallel
attempt-level check (bucketing audit round-file mtime, file-level
granularity so treat as approximate) gives the same shape: 277/1,934 (14.3%)
raw transport-error attempts inside the incident window vs. 1,657 (85.7%)
outside it. **Conclusion confirmed: the 22:46–23:30 incident was real and
locally dense, but a minority contributor to the day's total failure
volume** — the no-backoff fossilization of ordinary all-day transient
errors is the dominant driver, exactly as the owner's discard rationale
assumed.

---

## 6. Clean-rerun preflight

### Run root state

- `anchors_all.csv` (41,393), `anchors_A24.csv` (36,322), `anchors_A48.csv`
  (5,071), `anchors_A24_v4.csv` (20,696), `anchors_A24_v6.csv` (15,626) —
  row counts all match `anchors_summary.json` / the 20,696+15,626=36,322
  arithmetic. sha256 of `anchors_all`/`anchors_A24`/`anchors_A48` verified
  byte-identical to the hashes recorded in `anchors_summary.json` — untouched
  since the 09:28 build.
- **Stale gate markers found outside the archive, still live in the run
  root — reported, not deleted:**
  - `preflight/.ok` (2026-07-16 16:23:24) — from the mid-run offline-TM-
    catalog restart, i.e. it attests to a config that predates the coming
    no-live-GEHI flag. The launcher's `[[ ! -f "$PREFLIGHT/.ok" ]]` guard
    means **a fresh invocation will silently skip gate 4** (the authenticated
    1-anchor preflight) unless this marker is cleared first.
  - `.storage_checked` + `storage_check.txt` (both 2026-07-16 09:55:27) —
    from the very first launch, projecting 23.7 GB growth under the old
    assumption of live chip downloads during the scan. **A fresh invocation
    will silently skip gate 5** (storage projection) for the same reason,
    and even if it didn't, the stored projection no longer reflects the new
    code path's actual growth profile (§ Storage below).
  - `anchors_A24_v4.csv` / `anchors_A24_v6.csv` are not stale/wrong, just
    superseded: they implement the old IPv4/IPv6 GEHI-family split, which
    has no purpose once the rerun makes zero live GEHI calls.
  - No other stray `.done`/`.complete`/`.ok` markers exist outside
    `dirty_run1_archive_2026-07-17/` — confirmed by a targeted `find`.

### What a fresh launcher invocation does today, and what breaks under the new plan

Gates 1–3 (download sentinel, anchors build, gateway health/slots) re-check
live state every invocation and will pass cleanly. Gates 4–5 will **skip**
due to the stale markers above — this must be fixed (delete the two
markers, or key the gates off a config fingerprint) before trusting a fresh
run under new code. The main-run section hardcodes a **3-lane / 2-family**
topology (`a24_v4` @ 20w/10qps IPv4-pinned, running concurrently with a
`a48 → a24_v6` chain @ 20w/10qps IPv6, aggregate 40 workers / 20 qps) whose
entire reason for existing was spreading GEHI catalog/download load across
two address families to avoid the 403-ban threshold — moot once
no-live-GEHI mode makes that traffic zero. The check #9 gateway-concurrency
measurement (Postgres `usage_logs`, Little's-Law) already showed the
existing 40-worker ceiling running at only **15–35% utilization** — gateway
capacity was never the bottleneck, so 60 workers / 8 qps has ample headroom.

### Recommended minimal relaunch shape

Collapse the 3-lane/2-family structure to **two sequential lanes over the
original routed arms**, since the routing that actually matters
(`source_area_m2 >= 40 → A48 else A24`, i.e. `--review-extent-m`) is
independent of the now-moot GEHI-family split:

```
1) anchors_A24.csv   --anchor-workers 60 --qps 8 --review-extent-m 24
2) anchors_A48.csv   --anchor-workers 60 --qps 8 --review-extent-m 48
```

run one after the other, not concurrently — "60 workers / 8 qps total" maps
directly onto whichever single process is active, with no implicit ×2.

### Launcher edits this implies (not implemented — for the patch author)

1. Drop `run_lane`'s `family`/`DOTNET_SYSTEM_NET_DISABLEIPV6` env wrapper
   (moot under no-live-GEHI).
2. Drop the gate-2 inline-python block that builds `anchors_A24_v4.csv`/
   `anchors_A24_v6.csv`; drive the main run off `anchors_A24.csv` /
   `anchors_A48.csv` directly.
3. Reinterpret `WORKERS_PER_LANE`/`QPS_PER_LANE` as **totals**, not
   per-of-two-concurrent-lanes values (60/8 rather than today's 20/10 ×2).
4. Replace the `pids=(); ...; wait` concurrent-lane machinery with a plain
   sequential `run_lane a24 ...; run_lane a48 ...` (or keep two `run_lane`
   calls back to back with no backgrounding).
5. Gate 3's `NEEDED_SLOTS=$((2 * WORKERS_PER_LANE))` should become just
   `$WORKERS_PER_LANE` (60) under sequential single-process execution.
6. Clear (or re-key) `preflight/.ok` and `.storage_checked`/
   `storage_check.txt` at the top of a fresh run so gates 4–5 actually
   re-validate the new no-live-GEHI code path and the new growth profile
   instead of trusting run-1 artifacts.
7. Whatever flag lands for strict no-live-GEHI mode needs to be threaded
   through both the gate-4 preflight invocation and the main `run_lane`
   calls, so the preflight is actually exercising the code path the main
   run will use.
8. Given items 1/2/4/5 touch most of the main-run control flow, a new thin
   sequential launcher (reusing gates 1–3 verbatim) is likely cleaner than
   surgically patching the existing 3-lane script — a judgment call for
   whoever implements it.

### Storage

Archive (`dirty_run1_archive_2026-07-17/`, 17,883 anchors processed across
all three lanes before the stop): **703 M total** (a24_v4 466 M / a48 232 M /
a24_v6 5.0 M). Breakdown for a24_v4: audit 151 M, scan_states 109 M,
catalog_cache 66 M, **chips (`unused_merged_chips/`) 0 bytes in all three
lanes** — confirms chip reuse worked exactly as designed; zero fresh
imagery was downloaded in run 1 despite the large audit-directory size
(that's rendered LLM prompts + raw responses, not TIFFs). `catalog_cache`
is already near-saturated and does not scale with anchor count (~64 MB for
both a24_v4 @ 12,635 anchors and a48 @ 5,071 anchors — it caches per-family
catalog state, not per-anchor).

Per-anchor growth that *does* scale (scan_states + audit): a24_v4 ≈15.2
KB/anchor, a48 ≈16.1 KB/anchor, a24_v6 ≈12.9 KB/anchor (early/partial).
Projected for a full 41,393-anchor rerun: ≈41,393 × 15.5 KB ≈ **640 MB**,
plus ≈130 MB catalog_cache (two families) ≈ **well under 1 GB total** — far
below run 1's own gate-5 projection of 23.7 GB, because that number assumed
live chip downloads during the scan; strict no-live-GEHI mode draws 100% of
chips from the already-downloaded, untouched, read-only
`basemap_rebuild_2026-07-13/chips` (51 G) and writes zero new imagery.

Current free space: **101 G free on `/home`** (`df -h`). Projected rerun
growth (~1 GB) leaves >100× headroom — no storage risk for the clean rerun.

---

## 7. Bottom line for the rerun decision

- Discard run 1 entirely — confirmed correct call. Confirmed contamination
  is deep on the lanes that ran furthest (32–58% of processed anchors carry
  ≥1 tainted chip; 473 anchors are fully unrecoverable without a rescan) and
  concentrated in causes a clean rerun's two patches actually fix: the
  no-backoff retry storm (transport 4.79% attempt-level failure rate, with
  `batch_attempt_2` retries failing 78.5% of the time) and the GEHI
  403-ban stall (a24_v6 dead at 1.1% progress).
- One finding that isn't fixed by either patch: 79.1% of `gemini_failed`
  chip results are GEHI z=18 chip-coverage gaps, not Gemini/gateway
  failures — worth a decision on how the rerun (or a post-hoc audit pass)
  should handle those rather than assuming the two planned patches resolve
  all of it.
- Rerun shape: sequential `anchors_A24.csv` (60w/8qps/extent 24) then
  `anchors_A48.csv` (same, extent 48), replacing the now-moot 3-lane/
  2-GEHI-family topology. Storage is a non-issue (~1 GB projected vs. 101 G
  free). The two stale gate markers (`preflight/.ok`,
  `.storage_checked`/`storage_check.txt`) must be cleared or re-keyed before
  a fresh launch, or gates 4–5 will silently validate nothing.

## 8. Addendum 2026-07-17 (post-memo round 2): z18 gap root-caused, resolved by catalog filtering

Follow-up analysis (gehi-zero-mode teammate + orchestrator disk checks) after
§7's open finding. All numbers full-population unless noted.

**Root cause of the 79.1% GEHI-side rows.** Provider attribution: 8,102/8,139
download-failed rows are **Wayback**, 37 TM. Mechanism: a picked (date,
version) chip missing from the pre-downloaded basemap → `download_chip_with_
zoom_ladder` falls through to a live GEHI download (offline-catalog flags only
govern metadata, never chip downloads) → Wayback z20 skipped (offline builder
marks it unavailable), z19/z18 live attempts → "rc=0 but output empty/missing
at z=18" (8,051 rows). The identical trigger produced two symptoms by address
family: rc=0-empty on v4 (all-day silent taint) and 403/429-blocks on v6 (the
23:36 `GehiRateLimiter` 1800s-hard-backoff stall — 20 workers asleep, zero
sockets).

**Failures cluster on ~14 "phantom" dates, not uniformly.** Top:
`2018-06-27` ×5,326 — **not present in the offline candidates CSV at all**
(live-catalog-era pick, pre-16:23 restart; run 2 is immune by construction).
`2020-04-25` ×1,532 and `2021-08-30` (the pair shared by all 20 stuck a24_v6
anchors) ARE in the CSV for ~40k anchors each, but the tile store largely
lacks them: disk holds 1,084 / ~39k expected chips for 2020-04-25, 2 for
2021-07-23, 0 for 2018-06-27. ESRI Wayback metadata declares coverage the
tile store doesn't serve; the basemap download phase already failed on these
rows, so **a Wayback backfill would mostly re-fail — rejected** in favor of
catalog-level filtering.

**Fixes landed (uncommitted, on top of §7's two patches):**
- `--no-live-gehi` (run_adaptive_scan.py, gehi_download.py): scan can never
  spawn a GEHI subprocess; missing chip → existing `download_failed` notes
  path with greppable `no_live_gehi` marker; CSV-absent anchor → loud
  `done_ambiguous_orchestrator_error`. Tests: tests/temporal/test_no_live_gehi.py.
- `--offline-require-chip-on-disk` (requires the above): offline catalogs
  filtered at load time to entries whose chip exists on disk, so the adaptive
  picker never selects an unobtainable date — zero download_failed rows and
  zero wasted picks by construction. Startup logs document dropped entries.
  Measured against production CSV+disk: TM drops 3,925/908,785 (0.43%),
  Wayback drops 81,708/289,085 (28.26%); top dropped Wayback dates
  2020-04-25 ×40,313 and 2021-08-30 ×39,293 (the phantom pair). ISSUE-26
  cutoff walk operates on the filtered (obtainable) dates, so post-census
  reference slots are never spent on undownloadable frames. Tests:
  tests/temporal/test_offline_disk_filter.py.
- Transport retry (gemini_solar_image_review.py): {429,502,503,504} + conn/
  timeout errors retried ≤5 attempts, full-jitter exponential backoff 2s→60s
  cap; schema two-attempt policy byte-identical; `transport_retries` recorded
  in audit sidecars. Tests: tests/temporal/test_gemini_transport_retry.py.

**Residual risk cluster: 51 anchors** (4 A48 + 47 A24_v6) lose >50% of their
offline candidates to the disk filter — a spatially contiguous JNB0070–
JNB0078 grid band (JNB0076/0077 = 11 anchors each) missing most of their TM
month-end history 2019–2024: a grid-level TM download-batch failure in the
2026-07-13 rebuild, NOT phantom dates, so a targeted TM backfill (861 rows,
~25–50 min at the 1 req/s throttle) is expected to succeed. Backfill CSV
prepared (scratchpad `tm_backfill_51anchor_cluster.csv`); owner decision
pending on running it pre-launch. Zero anchors are 100% dark either way.

**Run 2 launcher**: `scripts/temporal/run_fullscan_backdating_v2.sh`
(sequential a24→a48, 60 workers / 8 qps total, all three new flags, `.done`
written only on clean lane exit, storage gate from run 1's measured
~16 KB/anchor). Stale gate markers from §7 were archived with run 1.
