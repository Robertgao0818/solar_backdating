# RUN plan — left-censored cohort back-extension to 2015 (ext2015)

- **Status: DRAFT — pending owner approval** (2026-07-19)
- Upstream: RUN 3 complete (41,393/41,393, `deliverable_run3_2026-07-18`),
  coverage-gain QA closed (`DATA-fullscan-run3-qa-coverage-2026-07-19.md`).
- Goal: for the RUN-3 **left-censored cohort** (present in the earliest ≥2019
  frame, install date only known as "≤2019"), extend the scan window back to
  2015 (optionally 2012) and recover year-level install brackets.

## 1. Cohort (frozen from RUN-3 deliverable)

From `deliverable_run3_2026-07-18/jhb_full382_fpcut_install_dated_2026-07-18-run3.csv`,
`left_censored == 1`:

- **4,058 anchors**, all `scan_status = done_already_present_before_geid_history`.
- `earliest_present_date`: 4,057 × `2019-01-15` (Wayback floor frame), 1 × `2019-07-30`.
- Lane split: **a24 = 2,960 / a48 = 1,098** (keep the same chip-arm routing).

Cohort CSVs are built by filtering `anchors_A24.csv` / `anchors_A48.csv` on this
anchor_id set (same filtered-CSV mechanism as `rescan_gemini_failed_run3.sh`).

## 2. Evidence base — pre-2019 vintage availability (measured, not assumed)

From `availability_rebuild_2026-07-12/wayback_info_full.csv` (full 15,859-group
corpus, GEHI `info` captured dates — the correct source per the Wayback
release-vs-capture-date lesson):

- **2015–2018 window: every group has exactly 4–5 Wayback capture dates**
  (distribution: 4 dates × 15,608 groups, 5 × 251):
  `2015-01-15` (all 15,859), `2015-03-18`/`2015-08-25` (subsets),
  `2016-05-28` (14,146), `2017-05-04/09/20/31` (strip mosaic, one per group),
  `2018-06-03`/`2018-06-27` (15,762+97).
- **Pre-2015: exactly one date, `2012-03-03` (all groups)** — a single cheap
  frame that converts "≤2015" into "2012–2015" vs "≤2012".
- Wayback cadence is ~annual → Wayback-only precision is a **year-level
  bracket**, consistent with the Turnbull/survival-prior handling of coarse
  intervals. TM densification (below) can tighten this where TM has old
  vintages.
- **TM pre-2019 is unmeasured**: the 2026-07-12 TM availability probe was run
  with `--min-date 2019/01/01`, so `tm_full_reparsed.csv` contains nothing
  before 2019. The scoped probe is a **mainline P0 step** (see D2), not an
  optional top-up.

**Provider topology — same as RUN 3 (TM 主干 + Wayback floor).** RUN 3's
offline catalog is TM-primary: 909,056 TM rows vs 289,190 Wayback rows
(~76% TM); Wayback contributes the guaranteed annual floor frames (2019–2022:
exactly 41,393 rows/year = one per anchor), TM supplies the dense vintages.
The ext window keeps the identical topology: **Wayback's measured 4–5 frames
per group are the guaranteed floor; TM (probed in P0) is the primary
densifier wherever it has pre-2019 vintages.** If the probe shows TM pre-2019
is sparse for these groups, the run degrades gracefully to the Wayback floor
— feasibility does not depend on TM.

## 3. Design decisions (owner input wanted on D1/D3/D4; D2 resolved)

| # | Decision | Recommendation |
|---|----------|----------------|
| D1 | Window floor: `2015-01-01` vs `2012-01-01` | **2012-01-01** — costs exactly one extra frame per anchor (2012-03-03) and upgrades the residual left-censor bound from ≤2015 to ≤2012 |
| D2 | TM availability probe for the cohort's groups (pre-2019) | **Resolved 2026-07-19: mainline, P0 prerequisite** — keeps the RUN-3 provider topology (TM primary, Wayback floor; see §2). Cohort's unique chip groups only (≪15,859); at the proven-safe ~60 calls/min ≈ well under an hour. Re-apply the playbook: `DOTNET_SYSTEM_NET_DISABLEIPV6=1` exported in the tmux pane; treat rc=0 + empty stdout as 403 and purge empty catalog-cache rows |
| D3 | CoJ 2015 aerial (0.15 m) as reference layer | Optional, off by default — the layer was dropped from detection for FN rate (gate_a 0.891), but as a *presence-confirmation reference* at the 2015 boundary it is usable in QA sheets, not in changepoint evidence |
| D4 | Window ceiling for this run | Cap `catalog_max_date` ≈ `2019-06-30`: the 2019-01-15 presence is already established by RUN 3; the ext scan only needs the pre-floor segment plus one overlap frame. Pilot must verify the ISSUE-26 census+3 cutoff logic doesn't misbehave with a ceiling far before census date |

## 4. Execution phases

**P0 — cohort + catalog prep**
Filter cohort CSVs (a24/a48). **Run the scoped TM pre-2019 probe first**
(D2, mainline — the only networked P0 step), then build offline provider
catalog rows for the cohort from **probe output + `wayback_info_full.csv`**,
both restricted to `< 2019-07-31`, merged via `merge_provider_catalogs`
semantics (same catalog-row format the RUN-3 mini-topup used, consumed by
`load_offline_provider_catalogs`). Note the known
`load_offline_provider_catalogs` quirk from RUN 3 §6: an anchor with zero TM
rows is indistinguishable from one absent from the catalog and errors before
Wayback is consulted — if the probe returns TM-empty groups, verify the
Wayback-only path (or carry a sentinel row) before P2, since pre-2019
TM-empty groups may be common here, unlike in RUN 3.

**P1 — config**
Run-local YAML (copy of `configs/geid_anchor_presence.yaml`) with
`adaptive_scan.catalog_min_date: "2012-01-01"` (per D1) and the D4 ceiling;
passed via `run_adaptive_scan.py --config`. **Main config keeps the 2019
floor** — the ext window is a per-run override, not a repo-wide change.
Keep `--vexcel-capture-csv` wiring identical to RUN 3.

**P2 — pilot (~40 anchors, stratified a24/a48 + across grids)**
- **Fresh run dir** `ext2015_v1/` with its **own scan_states** — never point
  `--force-restart` at `run3_v2/` scan_states (force-restart deletes and
  recreates state for every anchor it iterates; the cohort's RUN-3 states
  must survive for the merge step).
- `--force-restart` is **required** (all 4,058 are in terminal state; the
  RUN-3 lesson: a plain rerun silently no-ops on terminal anchors).
- Frames download on demand through the zoom ladder into the shared GEHI tile
  cache; no separate bulk download phase.
- Pilot QA (visual, per RUN-3 QA tooling): (a) 2015–2018-era GE frame quality
  and registration — older vintages are the misregistration-risk zone, and
  the offset=0 policy from ISSUE-27 stays in force; (b) whether the zoom
  ladder tops out at z19 on old dates — the a24 arm (2,960 small panels) is
  the resolution-sensitive one; (c) D4 cutoff behavior.

**P3 — full cohort run (tmux, resumable)**
Same worker/qps envelope as the RUN-3 rescan lane (10 workers / 2 qps).
Volume: ~4,058 anchors × ~5–6 Wayback-floor frames, plus whatever TM
densification the P0 probe surfaces (unknown until measured — recompute this
estimate after P0); adaptive search ⇒ roughly 3–6 Gemini calls/anchor ≈
**15–25k calls** — a fraction of RUN 3, so the
ISSUE-28 per-model 5-h quota is a low risk here, but the fallback decision
stays open and applies if pace is raised. Any terminal-error stragglers get
the filtered-CSV + `--force-restart` treatment directly (never a bare rerun).

**P4 — merge + deliverable v2**
`infer_install_dates.py` on `ext2015_v1/scan_states` → ext intervals. Merge
policy: for the 4,058 cohort, the ext result **replaces** the RUN-3 row
(expected outcomes: `done_appears` with a 2015–2019 bracket; or still
`already_present_before_geid_history`, now meaning ≤2015 / ≤2012); all other
37,335 rows carry over from RUN 3 unchanged. Rebuild
`build_install_dated_deliverable.py` with all 6 gates; expected effect:
left-censored class shrinks from 4,058, dated/bounded coverage rises,
residual left-censor bound moves 2019→2015 (or 2012).

**P5 — QA + docs**
Stratified visual grade (reuse `qa_run3_*` scripts) over transition strata
(left_censored→dated, left_censored→left_censored-deeper). DATA memo +
TRACKER row + this doc updated to RUN status.

## 5. Risks / open items

1. **Old-vintage imagery quality** (clouds, pan-sharpening artifacts, worse
   registration) — pilot gate P2(a) is the go/no-go.
2. **z19-only ceiling on old dates** hurting the small-panel a24 arm — pilot
   gate P2(b); TM densification is already mainline (D2), so the remaining
   mitigation if bad is the CoJ-2015 QA reference (D3).
3. **TM pre-2019 coverage may be sparse or absent** for these groups — this
   bounds bracket tightness, not feasibility (Wayback floor guarantees 4–5
   frames/group). Measured by the P0 probe; also triggers the
   `load_offline_provider_catalogs` TM-empty quirk noted in P0.
4. **Census-cutoff interaction with the early ceiling** (D4) — logic check in
   pilot before full run.
5. **1 anchor** with `earliest_present_date = 2019-07-30` and the known
   TM-403 holdout group (`...c0012722`, zero TM dates as of the 07-12 probe;
   RUN 3 §6 later recovered 21 TM dates for it via a fresh live probe, so
   include it in the P0 probe rather than pre-excluding) — Wayback-only
   evidence for it is acceptable; not a bug.
6. Interval-inference `done_appears` inverted-interval defect stays unpatched
   (standing owner decision) — unchanged by this run, noted for consistency.
