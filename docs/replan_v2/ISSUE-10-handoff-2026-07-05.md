# ISSUE-10 handoff — gold-set tooling, post-ISSUE-09 (2026-07-05)

Status: ready-to-start. ISSUE-09 is **done**; all inputs the sampler and strip
UI need are on disk. This doc lets a fresh session start ISSUE-10 without
re-deriving the cohort artifacts or the gate_a closure.

Read alongside:
- Spec: [`ISSUE-10-goldset-tooling.md`](ISSUE-10-goldset-tooling.md) (what to build + ACs)
- Engineering design (architect-signed, WP ownership, test plan, dry-run runbook):
  [`ISSUE-10-design-2026-07-05.md`](ISSUE-10-design-2026-07-05.md) — **do not re-derive it; execute it.**
- Cohort schema (tables the sampler consumes): [`ISSUE-09-cohort-schema.md`](ISSUE-09-cohort-schema.md)
- ISSUE-09 closure: [`ISSUE-09-coj-audit-cohort.md`](ISSUE-09-coj-audit-cohort.md)

---

## 1. State of the world — ISSUE-09 complete

Full 16,166-unit CoJ true-date cohort run **complete 2026-07-05 11:52 NZST**
(idempotent resume after a Windows reboot killed the chain mid-score at 02:26).
Coverage **12,190 / 12,190 dated anchors = 100%**, zero fetch failures. gate_nc
PASS (1/300 false-present, `nc_0210`). gate_a FAIL in exactly one cell
(`c_cal_present_pre2019@2015`, 49/55 = 0.891 vs 0.95 bar) — all 6 disagreements
human-adjudicated and closed (§3). Headline layer: probe_2023@2023 = 0/2863
contradictions (2023 is the clean primary layer).

### File inventory — `~/zasolar_data/geid_temporal/coj_audit_cohort_20260704/`

| file | purpose |
|---|---|
| `cohort_units.csv` | planned fetch units (16,166 rows, one per (anchor, year)), with `unit_purpose` + chip bbox; WP-A planning output. |
| `cohort_anchors.csv` | authoritative anchor set (12,490), one row per anchor with coords, `grid_id`, `region_key`, `in_fals_2019`, chip bbox + `chip_half_m`. |
| `cohort_audit_anchors.csv` | **the ISSUE-10 pivot** — WIDE, one row per anchor: `bit_{2015,2019,2023}`, `detector_S_*`, `contradicts_*`, `any_contradiction`, `n_contradictions`, `first_present_year`, `stratum`. The sampler stratifies on this. |
| `cohort_audit_bits.csv` | LONG source-of-truth, one row per planned unit (`bit`, `expected`, `agrees`, `contradicts_interval`, `routed_to_queue`). |
| `human_queue.csv` | **8,407 low-margin bits** routed to human review (same schema as the bits table). ISSUE-10/11 adjudication universe. |
| `contradiction_rate_by_stratum.csv` | headline contradiction rates (stratum / stratum×year / `in_fals_2019` flag) with Wilson CI. |
| `gates_report.json` | `gate_a` / `gate_nc` / `gate_b` / `clamp_findings` + coverage block. |
| `cohort_report.md` | human-readable roll-up of the above. |
| `gate_a_2015_human_adjudication.{csv,md}` | closure record for the 6 gate_a@2015 disagreements (§3 below). |
| `chips/{2015,2019,2023}/<anchor_id>.tif` | CoJ municipal true-date aerial chips (~0.15 m), **~27 G** (2015 1.7 G / 2019 4.5 G / 2023 21 G). **Reusable READ-ONLY** as the overlay layer in the strip UI — do not re-fetch these. |
| (aux) `scored.csv`, `classifier_manifest.csv`, `classifier_scores*.csv`, `negative_controls.csv`, `fetch_stats.jsonl`, `fetch_failures.csv` (empty), `dropped_units_summary.json`, `launch_*.log` | detector/classifier scoring intermediates + provenance; not primary ISSUE-10 inputs. |

---

## 2. What ISSUE-10 builds + which ACs now have live inputs

Three disjoint work packages (full plan + file ownership in the design doc):

- **WP-A — strip UI builder** (`build_goldset_strip_html.py`): per-anchor
  jump-window HTML (claimed latest-absent / earliest-present ±1–2 flanks),
  overlaid with the CoJ true-date chips + Vexcel + any in-window Wayback,
  CONFIRM / SHIFT / UNDATABLE buttons → offline JSON verdict manifest.
- **WP-B — stratified sampler** (`build_goldset_sample.py`): seeded draw of
  n=300–500 anchors over `status × confidence × any_contradiction`, 20%
  double-annotation overlap, 15 disputes force-included.
- **WP-C — window chip re-download** (`rerender_goldset_windows.py`):
  idempotent, LLM-free re-render of scan-window frames from retained
  `scan_states/*.json` + chipgroups bbox (the GEHI window chips were deleted —
  see §4).

### ACs that now have live inputs

- **"Stratification uses contradiction flags when present"** — now the *live*
  branch, not just the degrade path: `cohort_audit_anchors.csv` exists with
  `any_contradiction` + `contradicts_{2015,2019,2023}`. Exercise the
  present-flags path against the real file; keep the degrade path tested for
  the ~3,669 production anchors outside the cohort.
- **"10-anchor dry run + time-per-anchor"** — end-to-end runnable now: intervals
  CSV + retained `scan_states/` + `cohort_audit_anchors.csv` + the CoJ overlay
  chips are all on disk. Only WP-C's fresh window frames need GEHI network (§4).
- Strip-render / verdict-round-trip / overlap-emit ACs are tooling-only and were
  never data-blocked.

---

## 3. NEW requirements from the gate_a@2015 adjudication (codebook MUST absorb)

Closure: 6/6 disagreements adjudicated 2026-07-05, split ~50/50 —
3× `backdating_early` (2× `heater_swap`, 1× plain early date) and
3× `audit_miss_2015` (detector FN on real 2015 PV). Neither error source alone
breaches 95%; the 2015 cell fails because it is the only cell exposed to both.

The ISSUE-10 sampler/codebook must carry these forward:

1. **`heater_swap` tag.** New failure mode: a roof carries a pool heater years
   before the PV; GEHI change-detection anchors on the *heater's* appearance and
   pulls the install date early by up to 5–9 years (`c0008826`, `c0010056`).
   Contaminates backdating dates only — the 2023 census detection is genuine PV.
   DO add `heater_swap` to the verdict codebook as a distinct SHIFT reason.
2. **Per-case `dwelling_context` field** ∈ {`detached_villa`, `townhouse`,
   `other`}, recorded per adjudicated anchor. Needed to test the villa
   hypothesis below.
3. **Villa-concentration hypothesis (untested).** Heater_swap *may* concentrate
   in detached-villa suburbs (pools ⇒ affluent standalone housing), which would
   make the pre-2015 early-dating bias spatially clustered rather than uniform.
   **Caveat:** flat-plate SWH geysers (large, subsidized, non-villa SA base) can
   produce the same anchor-too-early error on ordinary roofs — so **keep the
   label unrestricted; verify per case, do not pre-filter to villa areas.**
   How the sampler can test it: `dwelling_context` is unknown pre-adjudication,
   so it is a *recorded* field, not a sampling axis. To enable the test the
   sampler MUST (a) preserve `grid_id` (present in `cohort_audit_anchors.csv`)
   through `sample_assignments.csv` so `heater_swap`-tagged verdicts can be
   joined post-hoc to a suburb/dwelling layer, and (b) optionally support an
   oversample knob on villa-suspect strata so the 8,407-bit-queue prevalence
   estimate isn't swamped by a uniform draw.
4. **2015-layer absent downweight.** 2015 absent evidence is downweighted
   (~5% detector-FN false-absent rate on truly-present roofs) but **not voided**.
   2023 stays the clean primary evidence layer (probe 0/2863). The codebook /
   ISSUE-11 hit-rate logic must not treat a 2015 absent as hard ground truth.
5. **2019 present-bit FP risk — queue-only, never headline.** Pilot spotcheck
   found 4/4 high-margin present@2019 s3 bits were audit false-positives (tile
   texture / roof clutter / bright metal). Treat 2019-based contradictions as
   human-queue candidates only; never promote a 2019 present bit to a headline
   claim.
6. **s3 = findings layer, not calibration.** `c_findings_s3like` (52.6%
   contradiction, n=19) is the expected falsification signal (the pilot
   falsified s3 boundary dates); it is a findings stratum, not a known-sign
   calibration cell. Do not fold it into calibration gates or headline accuracy.

---

## 4. Gotchas / do-not-touch

- **Margin caliber 0.30 / 0.95 is VALIDATED — do not touch.** The frozen ISSUE-08
  present/absent margin rule + classifier demotion held at cohort scale (gate_nc
  PASS, 2023 probe 0-contradiction). It is imported, never re-derived.
- **GEHI window chips were deleted in the disk cleanup — WP-C is on the critical
  path.** The scan-window frames (`scan_states/*.json` `chip_path`) are dead
  links; the CoJ overlay chips in this cohort dir survive but the GEHI/Wayback
  jump-window frames must be re-rendered from retained scan metadata + chipgroups
  bbox. Build/verify WP-C before the strip UI can show real jump windows.
  Re-render is idempotent (skip-if-exists), so a partial GEHI outage resumes.
- **DINOv2 classifier has FNs on the CoJ aerial domain — corroboration is
  advisory, the margin rule is the backstop.** `c0010056@2023` scored `pv=0.0025`
  on real PV. Do not gate a verdict on classifier probability; the detector
  margin rule is authoritative and the classifier is a secondary signal only.
- **Cross-source scale mismatch is UNDIAGNOSED — check it before overlaying
  CoJ / GEHI / Vexcel in the strip UI.** Owner observation (2026-07-05): CoJ
  audit chips are visibly higher-resolution than GEHI satellite chips AND the
  same house renders larger in the CoJ chip — cause not yet isolated
  (sampling / GSD / projection / chip-window geometry). Until diagnosed, any
  apparent-scale-dependent read (area impressions, panel discernibility)
  is NOT comparable across sources; the strip UI must not imply it is.
  Echoes the dinov3 ISSUE-02 teacher/student geometry-mismatch caveat.

---

## Regenerate tracker after any Status flip

```bash
cd /home/gaosh/projects/solar_backdating && python3 docs/replan_v2/render_tracker.py
```
