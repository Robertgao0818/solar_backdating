#!/usr/bin/env python3
"""RUN3-native repeat-ceiling teammate task (2026-07-19).

Re-derives the DINOv3 fidelity-gate "teacher ceiling" statistic (the
0.7724/S=0.0116 number in
``docs/dinov3_scorer/TRACKER.md`` slice 6 / ``ISSUE-06-gate-verdict-2026-07-06.md``)
on the RUN3-native R0 manifest instead of the old (pre-geometry-fix)
distillation corpus. Same-named statistic, reused decoder + reused Gemini
scoring callable — see ``docs/dinov3_scorer/DATA-run3native-repeat-ceiling-2026-07-19.md``
for the full calibre-alignment writeup.

Subcommands
-----------
* ``build-panel``     -- OFFLINE. Freeze a stratified anchor panel from the R0
                          test split (no Gemini calls, no quota spent).
* ``run-rep``          -- REAL Gemini quota. Independently re-score every frame
                          in the frozen panel for one rep (1/2/3).
* ``reconcile``         -- OFFLINE. actual-vs-expected accounting for one rep.
* ``compute-ceiling``   -- OFFLINE. Decode all 3 reps through the adopted
                          Phase-0 decoder and compute the pairwise ceiling.

Reuse discipline (owner red line: do not touch shared scoring scripts):
this file only *imports* from ``scripts.temporal.run_adaptive_scan``,
``scripts.validation.gemini_solar_image_review``,
``scripts.validation.fidelity_gate`` and ``scripts.validation.estimator_endtoend_decode``
-- it never edits them. All new code (panel construction, rep-scoring loop,
checkpointing, ceiling aggregation) lives here.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from itertools import combinations
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

R0_ROOT = Path.home() / "zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1"
OUT_ROOT = Path.home() / "zasolar_data/geid_temporal/run3_native_line_2026-07/repeat_ceiling_v1"
PANEL_LOCK_PATH = OUT_ROOT / "PANEL_LOCK.json"
PANEL_OBS_PATH = OUT_ROOT / "panel_obs.parquet"
PANEL_ANCHORS_PATH = OUT_ROOT / "panel_anchors.csv"

STRATA = ("A24|[0,15)", "A24|[15,40)", "A48|[40,100)", "A48|[100,inf)")
UNDER_40M2_STRATA = ("A24|[0,15)", "A24|[15,40)")

# Panel design (locked here; see DATA memo for the search that produced these
# numbers). Target: obs(single rep) x 3 <= 14,000 hard cap with an >=5% retry
# reserve, panel size within the 550-620 anchor guidance, <40 m^2 co-headline
# layer >= majority share matching its 87.7% corpus share.
TARGET_ANCHORS_PER_STRATUM = {
    "A24|[0,15)": 278,
    "A24|[15,40)": 183,
    "A48|[40,100)": 62,
    "A48|[100,inf)": 53,
}
PANEL_SEED = 2026071901
QUOTA_HARD_CAP_OBS = 14000


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# build-panel (offline)
# --------------------------------------------------------------------------- #
def cmd_build_panel(args: argparse.Namespace) -> None:
    import numpy as np

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if PANEL_LOCK_PATH.exists() and not args.force:
        raise SystemExit(
            f"{PANEL_LOCK_PATH} already exists — panel is frozen once built. "
            "Pass --force only if team-lead has authorized a re-open (red line: "
            "do not silently re-sample)."
        )

    manifest_lock = json.loads((R0_ROOT / "MANIFEST_LOCK.json").read_text())
    corpus_share = manifest_lock["split"]["stratum_balance"]["corpus_share"]

    splits = pd.read_parquet(R0_ROOT / "splits.parquet")
    manifest = pd.read_parquet(R0_ROOT / "manifest.parquet")

    test_anchor_ids = set(splits.loc[splits.split == "test", "anchor_id"])
    m = manifest[manifest.anchor_id.isin(test_anchor_ids)].copy()
    if len(m) == 0:
        raise SystemExit("no test-split observations found in manifest.parquet")

    # anchor-level frame availability + chip existence (must be 100% present —
    # a frozen panel cannot include an anchor with an unresolvable chip)
    exists = m["chip_png_path"].apply(lambda p: Path(p).exists())
    m["chip_exists"] = exists
    bad_anchor_ids = set(m.loc[~m.chip_exists, "anchor_id"])

    per_anchor = (
        m.groupby("anchor_id")
        .agg(
            n_frames=("capture_date", "size"),
            chip_arm=("chip_arm", "first"),
            area_bin=("area_bin", "first"),
            all_chips_exist=("chip_exists", "all"),
        )
        .reset_index()
    )
    per_anchor["stratum"] = per_anchor["chip_arm"] + "|" + per_anchor["area_bin"]
    eligible = per_anchor[per_anchor.all_chips_exist & per_anchor.stratum.isin(STRATA)]

    missing_stratum = set(per_anchor.stratum) - set(STRATA)
    if missing_stratum:
        raise SystemExit(f"unexpected strata in test split not in MANIFEST_LOCK corpus_share: {missing_stratum}")

    rng = np.random.default_rng(PANEL_SEED)
    picked_anchor_ids: list[str] = []
    stratum_pool_sizes: dict[str, int] = {}
    for st, n_target in TARGET_ANCHORS_PER_STRATUM.items():
        pool = np.sort(eligible.loc[eligible.stratum == st, "anchor_id"].to_numpy())
        stratum_pool_sizes[st] = int(len(pool))
        if len(pool) < n_target:
            raise SystemExit(f"stratum {st} pool={len(pool)} < target {n_target}")
        idx = rng.permutation(len(pool))[:n_target]
        picked_anchor_ids.extend(pool[idx].tolist())

    if len(set(picked_anchor_ids)) != len(picked_anchor_ids):
        raise SystemExit("duplicate anchor picked across strata — sampling bug, aborting")

    panel_obs = m[m.anchor_id.isin(set(picked_anchor_ids))].copy()
    panel_obs = panel_obs.sort_values(["anchor_id", "capture_date", "chip_index"]).reset_index(drop=True)
    keep_cols = [
        "anchor_id", "capture_date", "version", "chip_arm", "scan_arm",
        "round_id", "round_type", "decision_source", "chip_index",
        "pv_present", "confidence", "quality_flag", "label_v1",
        "chip_png_path", "chip_png_sha256", "achieved_zoom",
        "grid_id", "region_key", "source_area_m2", "area_bin",
        "geometry_version", "census_date", "prompt_config_hash", "model_id",
    ]
    panel_obs = panel_obs[keep_cols]
    panel_obs["stratum"] = panel_obs["chip_arm"] + "|" + panel_obs["area_bin"]

    n_obs_single_rep = len(panel_obs)
    n_anchors = panel_obs.anchor_id.nunique()
    if n_obs_single_rep * 3 > QUOTA_HARD_CAP_OBS:
        raise SystemExit(
            f"panel design exceeds quota hard cap: {n_obs_single_rep} obs x 3 reps "
            f"= {n_obs_single_rep*3} > {QUOTA_HARD_CAP_OBS}"
        )

    obs_by_stratum = panel_obs.groupby("stratum").size().to_dict()
    anchors_by_stratum = panel_obs.groupby("stratum")["anchor_id"].nunique().to_dict()

    PANEL_ANCHORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    anchors_df = (
        panel_obs[["anchor_id", "stratum", "chip_arm", "area_bin"]]
        .drop_duplicates()
        .sort_values("anchor_id")
    )
    anchors_df.to_csv(PANEL_ANCHORS_PATH, index=False)
    panel_obs.to_parquet(PANEL_OBS_PATH, index=False)

    obs_key_list = [f"{r.anchor_id}|{r.capture_date}|{r.chip_index}" for r in panel_obs.itertuples()]
    obs_list_sha256 = hashlib.sha256("\n".join(obs_key_list).encode("utf-8")).hexdigest()

    lock = {
        "generated_at_utc": _now_utc(),
        "generator": "scripts/temporal/run3_repeat_ceiling.py build-panel",
        "prd": "docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md (SS7 repeat ceiling)",
        "source": {
            "r0_manifest_lock": str(R0_ROOT / "MANIFEST_LOCK.json"),
            "manifest_sha256": manifest_lock["outputs"]["manifest.parquet"]["sha256"],
            "splits_sha256": manifest_lock["outputs"]["splits.parquet"]["sha256"],
            "test_split_anchors_total": len(test_anchor_ids),
            "test_split_obs_total": int(len(m)),
        },
        "chip_availability": {
            "checked_obs": int(len(m)),
            "missing_chip_obs": int((~exists).sum()),
            "anchors_with_missing_chip": len(bad_anchor_ids),
        },
        "sampling": {
            "method": "stratified simple random sample of anchors (numpy Generator.permutation), "
                      "full anchor taken whole (all scored frames included) -- ceiling is "
                      "interval-level, dropping frames would corrupt decode",
            "seed": PANEL_SEED,
            "stratum_definition": "chip_arm|area_bin (identical to R0 MANIFEST_LOCK stratum_balance keys)",
            "stratum_pool_sizes_eligible_anchors": stratum_pool_sizes,
            "target_anchors_per_stratum": TARGET_ANCHORS_PER_STRATUM,
        },
        "panel": {
            "n_anchors": int(n_anchors),
            "n_obs_single_rep": int(n_obs_single_rep),
            "n_obs_three_reps_planned": int(n_obs_single_rep * 3),
            "quota_hard_cap_obs": QUOTA_HARD_CAP_OBS,
            "retry_reserve_obs": QUOTA_HARD_CAP_OBS - n_obs_single_rep * 3,
            "retry_reserve_pct": round((QUOTA_HARD_CAP_OBS - n_obs_single_rep * 3) / QUOTA_HARD_CAP_OBS, 4),
            "obs_by_stratum": {k: int(v) for k, v in obs_by_stratum.items()},
            "anchors_by_stratum": {k: int(v) for k, v in anchors_by_stratum.items()},
            "under_40m2_obs_share": round(
                sum(obs_by_stratum.get(s, 0) for s in UNDER_40M2_STRATA) / n_obs_single_rep, 4
            ),
            "corpus_share_reference": corpus_share,
        },
        "reps_planned": [1, 2, 3],
        "t0_dual_use_reservation": (
            "Every rep's scoring_provenance_rep{N}.jsonl retains the full raw per-frame "
            "verdict + confidence + quality_flag (T0 paired-ablation is NOT approved and "
            "NOT executed here -- see DESIGN-phase0-emission-extension-2026-07-19 SS5.3; "
            "this field is retained only so a future approved T0 run can reuse these 3 reps "
            "at zero new quota)."
        ),
        "outputs": {
            "panel_obs.parquet": {
                "path": str(PANEL_OBS_PATH),
                "rows": int(len(panel_obs)),
                "sha256": _sha256_file(PANEL_OBS_PATH),
            },
            "panel_anchors.csv": {
                "path": str(PANEL_ANCHORS_PATH),
                "rows": int(len(anchors_df)),
                "sha256": _sha256_file(PANEL_ANCHORS_PATH),
            },
        },
        "obs_list_sha256": obs_list_sha256,
        "frozen": True,
    }
    PANEL_LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=False))
    print(f"[build-panel] wrote {PANEL_LOCK_PATH}")
    print(f"[build-panel] anchors={n_anchors} obs/rep={n_obs_single_rep} x3={n_obs_single_rep*3} "
          f"(cap={QUOTA_HARD_CAP_OBS}, reserve={lock['panel']['retry_reserve_obs']})")
    print(f"[build-panel] obs_by_stratum={obs_by_stratum}")


# --------------------------------------------------------------------------- #
# run-rep (REAL Gemini quota)
# --------------------------------------------------------------------------- #
def _load_panel() -> tuple[dict, "pd.DataFrame"]:
    if not PANEL_LOCK_PATH.exists():
        raise SystemExit(f"{PANEL_LOCK_PATH} missing -- run build-panel first")
    lock = json.loads(PANEL_LOCK_PATH.read_text())
    panel_obs = pd.read_parquet(PANEL_OBS_PATH)
    actual_sha = _sha256_file(PANEL_OBS_PATH)
    expected_sha = lock["outputs"]["panel_obs.parquet"]["sha256"]
    if actual_sha != expected_sha:
        raise SystemExit(
            f"panel_obs.parquet sha256 mismatch (lock={expected_sha[:12]}.. actual={actual_sha[:12]}..) "
            "-- panel file changed after freeze, refusing to score against a mutated panel"
        )
    return lock, panel_obs


def _provenance_path(rep: int) -> Path:
    return OUT_ROOT / f"scoring_provenance_rep{rep}.jsonl"


def _checkpoint_path(rep: int) -> Path:
    return OUT_ROOT / f"rep{rep}_checkpoint.json"


def _load_checkpoint(rep: int) -> set[str]:
    p = _checkpoint_path(rep)
    if not p.exists():
        return set()
    return set(json.loads(p.read_text()).get("done_anchor_ids", []))


def cmd_run_rep(args: argparse.Namespace) -> None:
    from scripts.temporal.presence_scorer import PresenceObservation  # noqa: F401 (type doc only)
    from scripts.temporal.run_adaptive_scan import _default_gemini_env, _load_gemini_config, _routing_salt
    from scripts.validation.gemini_solar_image_review import BatchPick, RateLimiter, score_batch_with_fallback

    rep = args.rep
    if rep not in (1, 2, 3):
        raise SystemExit("--rep must be 1, 2, or 3")

    lock, panel_obs = _load_panel()
    gemini_config = _load_gemini_config(_default_gemini_env())
    panel_models = panel_obs["model_id"].unique().tolist()
    if len(panel_models) != 1:
        raise SystemExit(f"panel has mixed model_id values, expected 1: {panel_models}")
    model = panel_models[0]
    if gemini_config.model != model:
        # Pin to the RUN3 model the panel was originally scored with (env-file
        # default is a different flag, e.g. gemini-3-flash) -- a repeat ceiling
        # must replay the SAME instrument, not whatever the env default is.
        gemini_config = dataclasses.replace(gemini_config, model=model)
    max_dates = 5  # matches scan_config.gemini_max_dates_per_call default; RUN3 used the default

    done_anchors = _load_checkpoint(rep)
    all_anchor_ids = sorted(panel_obs.anchor_id.unique().tolist())
    todo_anchor_ids = [a for a in all_anchor_ids if a not in done_anchors]
    print(f"[run-rep {rep}] panel anchors={len(all_anchor_ids)} already_done={len(done_anchors)} "
          f"todo={len(todo_anchor_ids)}")
    if args.limit_anchors:
        todo_anchor_ids = todo_anchor_ids[: args.limit_anchors]
        print(f"[run-rep {rep}] --limit-anchors -> {len(todo_anchor_ids)}")

    by_anchor = {aid: g.sort_values(["capture_date", "chip_index"]) for aid, g in panel_obs.groupby("anchor_id")}

    limiter = RateLimiter(args.qps)
    prov_lock = threading.Lock()
    ckpt_lock = threading.Lock()
    prov_fh = _provenance_path(rep).open("a", encoding="utf-8")
    stats = {"obs_scored": 0, "obs_failed": 0, "anchors_done": 0, "anchors_errored": 0}
    stats_lock = threading.Lock()
    t_start = time.monotonic()

    def _write_provenance_row(row: dict) -> None:
        with prov_lock:
            prov_fh.write(json.dumps(row) + "\n")
            prov_fh.flush()

    def _mark_anchor_done(anchor_id: str) -> None:
        with ckpt_lock:
            cur = _load_checkpoint(rep)
            cur.add(anchor_id)
            _checkpoint_path(rep).write_text(
                json.dumps({"rep": rep, "done_anchor_ids": sorted(cur), "updated_at_utc": _now_utc()})
            )

    def _score_one_anchor(anchor_id: str) -> tuple[str, int, int, str | None]:
        g = by_anchor[anchor_id]
        rows = list(g.itertuples())
        census_mid_date_iso = str(rows[0].census_date) if rows[0].census_date else None
        n_ok = 0
        n_failed = 0
        try:
            for start in range(0, len(rows), max_dates):
                chunk = rows[start : start + max_dates]
                picks = [
                    BatchPick(
                        chip_index=i + 1,
                        chip_path=Path(r.chip_png_path),
                        capture_date=str(r.capture_date),
                        version=str(r.version),
                        actual_zoom=int(r.achieved_zoom) if r.achieved_zoom is not None else None,
                    )
                    for i, r in enumerate(chunk)
                ]
                salt = _routing_salt("target", model, anchor_id, f"repeatceiling_rep{rep}_c{start}")
                observations = score_batch_with_fallback(
                    picks,
                    config=gemini_config,
                    census_mid_date_iso=census_mid_date_iso,
                    routing_salt=salt,
                    limiter=limiter,
                )
                obs_by_idx = {o.chip_index: o for o in observations}
                for i, r in enumerate(chunk):
                    obs = obs_by_idx.get(i + 1)
                    ts = _now_utc()
                    if obs is None:
                        n_failed += 1
                        row = {
                            "record_version": 1, "ts_utc": ts, "rep_id": rep,
                            "scorer_name": "gemini", "model_id": model,
                            "scoring_mode": "repeat_ceiling_batch",
                            "anchor_id": anchor_id, "capture_date": str(r.capture_date),
                            "chip_index": i + 1, "pv_present": None, "confidence": None,
                            "quality_flag": "unusable", "decision_source": "gemini_failed",
                            "error": "no_observation_returned",
                        }
                    else:
                        is_fail = obs.decision_source == "gemini_failed"
                        n_failed += int(is_fail)
                        n_ok += int(not is_fail)
                        row = {
                            "record_version": 1, "ts_utc": ts, "rep_id": rep,
                            "scorer_name": "gemini", "model_id": model,
                            "scoring_mode": "repeat_ceiling_batch",
                            "anchor_id": anchor_id, "capture_date": str(r.capture_date),
                            "chip_index": i + 1, "pv_present": obs.pv_present,
                            "confidence": obs.confidence, "quality_flag": obs.quality_flag,
                            "decision_source": obs.decision_source,
                            "evidence": getattr(obs, "evidence", ""), "notes": getattr(obs, "notes", ""),
                            "error": getattr(obs, "error", None),
                        }
                    _write_provenance_row(row)
            _mark_anchor_done(anchor_id)
            return anchor_id, n_ok, n_failed, None
        except Exception as exc:  # noqa: BLE001 - anchor-level isolation, keep the pool alive
            return anchor_id, n_ok, n_failed, f"{type(exc).__name__}: {exc}"

    log_path = OUT_ROOT / f"rep{rep}_run.log"
    log_fh = log_path.open("a", encoding="utf-8")

    def _log(msg: str) -> None:
        line = f"[{_now_utc()}] {msg}"
        print(line)
        log_fh.write(line + "\n")
        log_fh.flush()

    _log(f"run-rep {rep} start: todo_anchors={len(todo_anchor_ids)} workers={args.anchor_workers} qps={args.qps} model={model}")

    with ThreadPoolExecutor(max_workers=args.anchor_workers) as pool:
        futures = {pool.submit(_score_one_anchor, aid): aid for aid in todo_anchor_ids}
        completed = 0
        for fut in as_completed(futures):
            anchor_id, n_ok, n_failed, err = fut.result()
            completed += 1
            with stats_lock:
                stats["obs_scored"] += n_ok
                stats["obs_failed"] += n_failed
                if err is None:
                    stats["anchors_done"] += 1
                else:
                    stats["anchors_errored"] += 1
            if err is not None:
                _log(f"ANCHOR ERROR {anchor_id}: {err}")
            if completed % 25 == 0 or completed == len(todo_anchor_ids):
                elapsed = time.monotonic() - t_start
                _log(f"progress {completed}/{len(todo_anchor_ids)} anchors "
                     f"(obs_ok={stats['obs_scored']} obs_failed={stats['obs_failed']} "
                     f"anchor_errors={stats['anchors_errored']}) elapsed={elapsed:.0f}s")

    prov_fh.close()
    _log(f"run-rep {rep} DONE: {stats}")
    log_fh.close()


# --------------------------------------------------------------------------- #
# reconcile (offline)
# --------------------------------------------------------------------------- #
def cmd_reconcile(args: argparse.Namespace) -> None:
    rep = args.rep
    lock, panel_obs = _load_panel()
    expected_obs = lock["panel"]["n_obs_single_rep"]
    expected_anchors = lock["panel"]["n_anchors"]

    prov_path = _provenance_path(rep)
    if not prov_path.exists():
        raise SystemExit(f"{prov_path} missing -- rep {rep} has not been run")
    rows = [json.loads(line) for line in prov_path.read_text().splitlines() if line.strip()]
    df = pd.DataFrame(rows)

    dupe_keys = df.duplicated(subset=["anchor_id", "capture_date", "chip_index"], keep=False)
    n_dupe = int(dupe_keys.sum())

    actual_obs = df.drop_duplicates(subset=["anchor_id", "capture_date", "chip_index"], keep="last")
    n_actual_obs = len(actual_obs)
    n_actual_anchors = actual_obs.anchor_id.nunique()
    n_gemini_failed = int((actual_obs.decision_source == "gemini_failed").sum())

    checkpoint = _load_checkpoint(rep)
    panel_anchor_ids = set(panel_obs.anchor_id.unique())
    missing_anchors = sorted(panel_anchor_ids - checkpoint)

    result = {
        "rep": rep,
        "reconciled_at_utc": _now_utc(),
        "expected": {"anchors": expected_anchors, "obs": expected_obs},
        "actual": {
            "provenance_rows_raw": len(rows),
            "provenance_rows_deduped": n_actual_obs,
            "duplicate_rows": n_dupe,
            "anchors_scored": int(n_actual_anchors),
            "anchors_checkpointed_done": len(checkpoint),
            "gemini_failed_count": n_gemini_failed,
        },
        "missing_anchors": missing_anchors,
        "passed": (n_actual_obs == expected_obs and len(missing_anchors) == 0 and n_dupe == 0),
    }
    out_path = OUT_ROOT / f"rep{rep}_reconcile.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        print(f"[reconcile] rep {rep} DID NOT reconcile cleanly -- see {out_path}", file=sys.stderr)
        sys.exit(1)
    print(f"[reconcile] rep {rep} OK -> {out_path}")


# --------------------------------------------------------------------------- #
# compute-ceiling (offline)
# --------------------------------------------------------------------------- #
def _obs_to_vintage_observations(rows: "pd.DataFrame"):
    from solar_backdating.estimators import VintageObservation

    present_map = {True: "1", False: "0", None: ""}
    out = []
    for i, r in enumerate(rows.itertuples()):
        pv = r.pv_present
        # provenance stores python bool/None already (json round-trip keeps them)
        out.append(
            VintageObservation(
                capture_date=date.fromisoformat(str(r.capture_date)[:10]),
                pv_present=present_map.get(pv if isinstance(pv, bool) else (None if pd.isna(pv) else pv), ""),
                confidence=None if (r.confidence is None or (isinstance(r.confidence, float) and pd.isna(r.confidence))) else float(r.confidence),
                quality_flag=r.quality_flag or "usable",
                source_row=i,
            )
        )
    return out


def cmd_compute_ceiling(args: argparse.Namespace) -> None:
    from scripts.validation.estimator_endtoend_decode import DEFAULT_VEXCEL_CSV, _load_vexcel_ceiling
    from scripts.validation.fidelity_gate import build_decoder, posterior_to_agree_key
    from solar_backdating.estimators import ClampContext

    lock, panel_obs = _load_panel()
    manifest_lock = json.loads((R0_ROOT / "MANIFEST_LOCK.json").read_text())
    corpus_share = manifest_lock["split"]["stratum_balance"]["corpus_share"]

    reps = {}
    for rep in (1, 2, 3):
        prov_path = _provenance_path(rep)
        if not prov_path.exists():
            raise SystemExit(f"rep {rep} provenance missing at {prov_path} -- run all 3 reps first")
        rows = [json.loads(line) for line in prov_path.read_text().splitlines() if line.strip()]
        df = pd.DataFrame(rows).drop_duplicates(subset=["anchor_id", "capture_date", "chip_index"], keep="last")
        reps[rep] = df

    vexcel_ceiling = _load_vexcel_ceiling(DEFAULT_VEXCEL_CSV)
    est, config = build_decoder()

    anchor_stratum = panel_obs[["anchor_id", "stratum", "grid_id"]].drop_duplicates(subset=["anchor_id"]).set_index("anchor_id")

    decoded_keys: dict[int, dict[str, str]] = {}
    for rep, df in reps.items():
        decoded_keys[rep] = {}
        for anchor_id, g in df.groupby("anchor_id"):
            if anchor_id not in anchor_stratum.index:
                continue
            grid_id = anchor_stratum.loc[anchor_id, "grid_id"]
            clamp = ClampContext(ceiling_date=vexcel_ceiling.get(grid_id))
            obs = _obs_to_vintage_observations(g)
            posterior = est(obs, clamp, config)
            decoded_keys[rep][anchor_id] = posterior_to_agree_key(posterior)

    def agg(rows, selector, strat_weight):
        per: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        tot = [0, 0]
        for r in rows:
            v = selector(r)
            per[r["stratum"]][0] += v
            per[r["stratum"]][1] += 1
            tot[0] += v
            tot[1] += 1
        strat_w = {st: strat_weight.get(st, 0.0) for st in per}
        wsum = sum(strat_w.values()) or 1.0
        wrate = sum(strat_w[st] * (h / n if n else 0) for st, (h, n) in per.items()) / wsum
        return (
            round(tot[0] / tot[1], 4) if tot[1] else None,
            round(wrate, 4),
            {st: {"n": n, "rate": round(h / n, 4) if n else None} for st, (h, n) in per.items()},
        )

    def pair_report(rep_i: int, rep_j: int, strat_weight: dict[str, float]):
        ki, kj = decoded_keys[rep_i], decoded_keys[rep_j]
        common = sorted(set(ki) & set(kj))
        rows = []
        for aid in common:
            rows.append({
                "anchor_id": aid,
                "stratum": anchor_stratum.loc[aid, "stratum"],
                "agree": int(ki[aid] == kj[aid]),
                "both_dated": ki[aid] != "UNDATED" and kj[aid] != "UNDATED",
            })
        u, w, by = agg(rows, lambda r: r["agree"], strat_weight) if rows else (None, None, {})
        dated_rows = [r for r in rows if r["both_dated"]]
        _ud, wd, _ = agg(dated_rows, lambda r: r["agree"], strat_weight) if dated_rows else (None, None, {})
        return {
            "reps": [rep_i, rep_j],
            "n_intersection": len(common),
            "agreement_unweighted": u,
            "agreement_inv_weighted": w,
            "dated_only_inv_weighted": wd,
            "n_dated": len(dated_rows),
            "by_stratum": by,
        }

    pairs_overall = [pair_report(i, j, corpus_share) for i, j in combinations((1, 2, 3), 2)]
    spread_vals = [p["agreement_inv_weighted"] for p in pairs_overall if p["agreement_inv_weighted"] is not None]
    import statistics
    spread = {
        "min": round(min(spread_vals), 4) if spread_vals else None,
        "max": round(max(spread_vals), 4) if spread_vals else None,
        "mean": round(sum(spread_vals) / len(spread_vals), 4) if spread_vals else None,
        "std": round(statistics.pstdev(spread_vals), 4) if len(spread_vals) > 1 else 0.0,
    }

    under40_weight = {s: corpus_share[s] for s in UNDER_40M2_STRATA}
    pairs_under40 = [pair_report(i, j, under40_weight) for i, j in combinations((1, 2, 3), 2)]
    spread_vals_u40 = [p["agreement_inv_weighted"] for p in pairs_under40 if p["agreement_inv_weighted"] is not None]

    result = {
        "computed_at_utc": _now_utc(),
        "statistic_definition": (
            "Same-named statistic as TRACKER slice 6 (0.7724): per-anchor exact "
            "install-interval agree_key (UNDATED / AP_BOUND<=|end / INTERVAL|start|end) "
            "decoded via the ADOPTED Phase-0 changepoint decoder (posterior_to_agree_key, "
            "scripts/validation/fidelity_gate.py), rep-i-vs-rep-j exact-match rate on the "
            "decoded intersection, inventory-weighted by stratum, averaged over the 3 pairs "
            "C(3,2). DEVIATION from the old statistic: weight source is R0 MANIFEST_LOCK "
            "corpus_share (chip_arm|area_bin, 4 strata) instead of the old reference.csv "
            "status_stratum inv_weight -- the old reference.csv/status_stratum concept does "
            "not exist for the RUN3-native manifest; corpus_share is the population-share "
            "analog on this corpus. Spread reported as min/max/mean/std across the 3 pairs "
            "(old code only reported min/max/mean; std added per team-lead's request)."
        ),
        "ceiling_overall": {
            "pairs": pairs_overall,
            "ceiling_mean_inv_weighted": spread["mean"],
            "spread": spread,
        },
        "ceiling_under_40m2": {
            "pairs": pairs_under40,
            "ceiling_mean_inv_weighted": (
                round(sum(spread_vals_u40) / len(spread_vals_u40), 4) if spread_vals_u40 else None
            ),
            "spread": {
                "min": round(min(spread_vals_u40), 4) if spread_vals_u40 else None,
                "max": round(max(spread_vals_u40), 4) if spread_vals_u40 else None,
            },
        },
        "old_ceiling_reference": {
            "value": 0.7724, "spread_S": 0.0116,
            "source": "docs/dinov3_scorer/TRACKER.md slice 6 (2026-07-10), pre-RUN3-geometry-fix corpus",
        },
        "decoded_anchor_counts": {rep: len(v) for rep, v in decoded_keys.items()},
    }
    out_path = OUT_ROOT / "CEILING_RESULT.json"
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, indent=2, default=str))
    print(f"[compute-ceiling] wrote {out_path}")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("build-panel")
    p1.add_argument("--force", action="store_true")
    p1.set_defaults(func=cmd_build_panel)

    p2 = sub.add_parser("run-rep")
    p2.add_argument("--rep", type=int, required=True)
    p2.add_argument("--anchor-workers", type=int, default=20)
    p2.add_argument("--qps", type=float, default=6.0)
    p2.add_argument("--limit-anchors", type=int, default=0)
    p2.set_defaults(func=cmd_run_rep)

    p3 = sub.add_parser("reconcile")
    p3.add_argument("--rep", type=int, required=True)
    p3.set_defaults(func=cmd_reconcile)

    p4 = sub.add_parser("compute-ceiling")
    p4.set_defaults(func=cmd_compute_ceiling)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
