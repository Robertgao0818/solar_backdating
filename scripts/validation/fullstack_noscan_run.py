#!/usr/bin/env python3
"""Score the fixed full-stack vintage grid K times -- NO adaptive search (L2 removed).

For every (chip, target) we take the chip's COMPLETE drivable vintage stack
(rendered by ``fullstack_noscan_prep.py``), tile it into fixed consecutive
windows of ``--window`` frames, and score each window with the SAME Gemini
SEQUENCE instrument Arm A used. Per-frame ``pv_present`` verdicts are emitted to
``long_all.csv``; ``fullstack_noscan_analyze.py`` stitches them into a per-rep
install-date and measures rep-to-rep reproducibility vs Arm A (0.875) / Arm B
(0.771).

The grid is fixed and exhaustive, so there is no data-dependent search path to
diverge: any rep-to-rep wobble here is pure L1 (single-call) noise, no longer
amplified by L2.
"""
from __future__ import annotations

import argparse
import csv
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.presence_scorer import get_scorer, validate_emission  # noqa: E402
from scripts.temporal.score_target_sequence import (  # noqa: E402
    RateLimiter,
    TargetKey,
    _default_env_file,
    load_review_png_manifest,
)
from scripts.validation.gemini_solar_image_review import (  # noqa: E402
    API_FORMATS,
    GeminiClientConfig,
    SequenceDatePick,
    env_value,
    load_env_file,
)

LONG_FIELDS = [
    "rep", "chip_id", "anchor_id", "target_label", "window_idx",
    "date_index", "capture_date", "pv_present", "pv_score",
    "quality_flag", "sequence_pattern", "sequence_confidence",
    "consistency_flag", "decision_source", "error",
]
DONE_FIELDS = ["rep", "anchor_id", "target_label", "window_idx"]


def _bool_csv(v) -> str:
    return "1" if v is True else ("0" if v is False else "")


def _f(v) -> str:
    return "" if v is None else f"{v:.4f}"


def build_config(env_file: Path, model: str | None) -> GeminiClientConfig:
    env = load_env_file(env_file)
    base_url = env_value(env, "GOOGLE_GEMINI_BASE_URL")
    api_key = env_value(env, "GEMINI_API_KEY")
    if not base_url or not api_key:
        raise SystemExit(f"Missing GOOGLE_GEMINI_BASE_URL / GEMINI_API_KEY in {env_file}")
    api_format = env_value(env, "GEMINI_API_FORMAT", "native")
    if api_format not in API_FORMATS:
        raise SystemExit(f"Unsupported API format {api_format!r}")
    return GeminiClientConfig(
        base_url=base_url,
        api_key=api_key,
        model=model or env_value(env, "GEMINI_MODEL", "gemini-3-flash-preview"),
        api_format=api_format,
        native_path=env_value(env, "GEMINI_NATIVE_PATH", "/v1beta"),
        timeout=int(env_value(env, "GEMINI_TIMEOUT", "120")),
    )


def load_done(path: Path) -> set[tuple[str, str, str, str]]:
    if not path.exists():
        return set()
    done = set()
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            done.add((r["rep"], r["anchor_id"], r["target_label"], r["window_idx"]))
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--window", type=int, default=8)
    ap.add_argument("--workers", type=int, default=35)
    ap.add_argument("--qps", type=float, default=8.0)
    ap.add_argument("--model", default=None)
    ap.add_argument(
        "--scorer",
        default="gemini",
        help="Registered PresenceScorer name to score sequence windows with (default: gemini).",
    )
    ap.add_argument("--env-file", type=Path, default=_default_env_file())
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--limit-chips", type=int, default=None, help="smoke: only first N chips")
    ap.add_argument("--limit-windows", type=int, default=None, help="smoke: only first N windows/target")
    ap.add_argument("--rep-start", type=int, default=1)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    config = build_config(a.env_file, a.model)
    # Route sequence scoring through the PresenceScorer seam instead of a
    # hardwired import. `.sequence` lazily resolves the concrete Gemini callable
    # only when the gemini scorer is selected, preserving the native
    # GeminiSequenceResult shape run_job flattens below.
    score_sequence = get_scorer(a.scorer).sequence

    review_pngs = load_review_png_manifest(a.manifest)
    by_target: dict[TargetKey, dict[str, object]] = defaultdict(dict)
    for rp in review_pngs:
        if rp.render_status in {"ok", "skipped_existing"} and rp.review_png_path and rp.review_png_path.exists():
            by_target[rp.key][rp.capture_date] = rp

    target_keys = sorted(by_target, key=lambda k: (k.chip_id, k.anchor_id, k.target_label))
    if a.limit_chips is not None:
        keep_chips = sorted({k.chip_id for k in target_keys})[: a.limit_chips]
        target_keys = [k for k in target_keys if k.chip_id in set(keep_chips)]

    # build (target -> sorted dates -> windows)
    jobs = []  # (rep, key, window_idx, window_dates)
    for key in target_keys:
        dates = sorted(by_target[key])
        windows = [dates[i:i + a.window] for i in range(0, len(dates), a.window)]
        if a.limit_windows is not None:
            windows = windows[: a.limit_windows]
        for rep in range(a.rep_start, a.rep_start + a.reps):
            for wi, wdates in enumerate(windows):
                jobs.append((rep, key, wi, wdates))

    long_path = a.out_dir / "long_all.csv"
    done_path = a.out_dir / "done.csv"
    done = load_done(done_path)
    jobs = [j for j in jobs if (str(j[0]), j[1].anchor_id, j[1].target_label, str(j[2])) not in done]

    n_targets = len(target_keys)
    print(f"targets={n_targets}  reps={a.reps} (start {a.rep_start})  window={a.window}  "
          f"workers={a.workers} qps={a.qps}  pending_jobs={len(jobs)} (skipped {len(done)} done)",
          flush=True)
    if not jobs:
        print("nothing to do.")
        return 0

    limiter = RateLimiter(a.qps)
    write_lock = threading.Lock()
    new_long = not long_path.exists()
    new_done = not done_path.exists()
    lf = long_path.open("a", newline="")
    df = done_path.open("a", newline="")
    lw = csv.DictWriter(lf, fieldnames=LONG_FIELDS)
    dw = csv.DictWriter(df, fieldnames=DONE_FIELDS)
    if new_long:
        lw.writeheader()
    if new_done:
        dw.writeheader()

    counter = {"done": 0, "err": 0}
    t0 = time.monotonic()

    def run_job(job):
        rep, key, wi, wdates = job
        pngs = by_target[key]
        picks = [
            SequenceDatePick(
                date_index=i,
                chip_path=pngs[d].review_png_path,
                capture_date=d,
                actual_zoom=pngs[d].actual_zoom,
            )
            for i, d in enumerate(wdates, start=1)
        ]
        limiter.wait()
        rows = []
        err = ""
        try:
            res = score_sequence(picks, config=config, audit_writer=None, max_tokens=a.max_tokens)
            # Vocab enforcement at ingest: an unregistered value degrades this
            # window to an explicit error row instead of flowing into the CSV.
            validate_emission(res.quality_flag, res.decision_source)
            obs_by_date = {o.capture_date: o for o in res.observations}
            for d in wdates:
                o = obs_by_date.get(d)
                rows.append({
                    "rep": rep, "chip_id": key.chip_id, "anchor_id": key.anchor_id,
                    "target_label": key.target_label, "window_idx": wi,
                    "date_index": "", "capture_date": d,
                    "pv_present": _bool_csv(o.pv_present if o else None),
                    "pv_score": _f(o.pv_score if o else None),
                    "quality_flag": res.quality_flag,
                    "sequence_pattern": res.sequence_pattern,
                    "sequence_confidence": _f(res.confidence),
                    "consistency_flag": res.consistency_flag,
                    "decision_source": res.decision_source,
                    "error": "",
                })
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            for d in wdates:
                rows.append({
                    "rep": rep, "chip_id": key.chip_id, "anchor_id": key.anchor_id,
                    "target_label": key.target_label, "window_idx": wi,
                    "date_index": "", "capture_date": d, "pv_present": "", "pv_score": "",
                    "quality_flag": "error", "sequence_pattern": "", "sequence_confidence": "",
                    "consistency_flag": "", "decision_source": "", "error": err,
                })
        return job, rows, err

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = [pool.submit(run_job, j) for j in jobs]
        for fut in as_completed(futures):
            (rep, key, wi, _wd), rows, err = fut.result()
            with write_lock:
                lw.writerows(rows)
                dw.writerow({"rep": rep, "anchor_id": key.anchor_id,
                             "target_label": key.target_label, "window_idx": wi})
                lf.flush()
                df.flush()
                counter["done"] += 1
                if err:
                    counter["err"] += 1
                n = counter["done"]
                if n % 50 == 0 or n == len(jobs):
                    rate = n / (time.monotonic() - t0)
                    print(f"  {n}/{len(jobs)} jobs  err={counter['err']}  "
                          f"{rate:.1f} job/s  eta {(len(jobs)-n)/rate/60:.1f}min", flush=True)

    lf.close()
    df.close()
    print(f"\nDONE: {counter['done']} jobs, {counter['err']} errors -> {long_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
