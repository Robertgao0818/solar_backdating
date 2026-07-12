"""CLI orchestrator for the CoJ true-date audit at cohort scale (ISSUE-09).

Wires the four scientific work packages (A: cohort builder + layer plan +
negative controls, B: concurrent fetch + 2015 layer, C: resumable/chunked
scoring, D: cohort join / gates / report) into one stage pipeline, mirroring
`coj_audit_pilot.py`'s stage pattern at cohort scale (~12,190 dated anchors +
~300 negative controls, ~16k planned fetch/score units instead of the
pilot's 244). All the scientific logic lives in `coj_cohort_build.py` /
`coj_arcgis_fetch.py` / `score_coj_chips.py` / `coj_cohort_join.py` /
`coj_cohort_report.py` (each independently unit-tested); this module is
deliberately I/O-heavy (real network, real GPU, real CSVs/JSON) and has no
dedicated test of its own, per the design's WP-E note (pilot precedent: the
orchestrator has none).

Stages (each idempotent / independently re-runnable; each reads its
predecessor's file straight from ``--output-root``):

  build  -- plan cohort strata + per-anchor CoJ layer-years + the seeded 2019
            falsification subsample + negative-control geometry ->
            cohort_anchors.csv / cohort_units.csv / negative_controls.csv /
            dropped_units_summary.json (`coj_cohort_build.build_cohort`).
  fetch  -- load cohort_units.csv -> FetchUnit list -> pre-filter already-
            fetched non-empty chips -> fetch concurrently (politeness intact,
            see `_production_fetch_one`) -> chips/{year}/<id>.tif +
            fetch_stats.jsonl (append-only) + fetch_failures.csv.
  score  -- detector (+ optional solar_cls classifier corroboration) over
            every scorable fetched chip, resumable/chunked -> scored.csv +
            classifier_scores.csv.
  join   -- merge cohort_units.csv + fetch outcomes + scored.csv +
            classifier_scores.csv into the long/wide contradiction dataset
            -> cohort_audit_bits.csv / cohort_audit_anchors.csv /
            human_queue.csv (`coj_cohort_join.build_cohort_bit` /
            `pivot_anchors`).
  gates  -- the four cohort gates + coverage accounting -> gates_report.json
            (+ refreshes fetch_failures.csv from the authoritative coverage
            computation).
  report -- headline per-stratum contradiction-rate table + human-readable
            roll-up -> contradiction_rate_by_stratum.csv / cohort_report.md.
  all    -- run every stage in order.

Runbook (tmux, per repo convention -- design §8):

  tmux new -s coj_cohort
  source scripts/activate_env.sh
  ROOT=~/zasolar_data/geid_temporal/coj_audit_cohort_20260704
  python -u -m scripts.audit.coj_audit_cohort build  --output-root $ROOT --seed 20260704
  stdbuf -oL python -u -m scripts.audit.coj_audit_cohort fetch --output-root $ROOT --workers 5
  python -u -m scripts.audit.coj_audit_cohort score  --output-root $ROOT
  python -u -m scripts.audit.coj_audit_cohort join   --output-root $ROOT
  python -u -m scripts.audit.coj_audit_cohort gates  --output-root $ROOT
  python -u -m scripts.audit.coj_audit_cohort report --output-root $ROOT

See `scripts/audit/coj_audit_cohort_launch.sh` for the scripted chain.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import signal
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.audit.coj_arcgis_fetch import (  # noqa: E402
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_POLITENESS_SLEEP_S,
    DEFAULT_USER_AGENT,
    FetchOutcome,
    FetchUnit,
    fetch_and_save_chip,
    fetch_units_concurrent,
)
from scripts.audit.coj_audit_join import _parse_date  # noqa: E402
from scripts.audit.coj_cohort_join import (  # noqa: E402
    COVERED_FETCH_OUTCOMES,
    LONG_COLUMNS,
    WIDE_COLUMNS,
    build_cohort_bit,
    compute_cohort_gates,
    coverage_report,
    pivot_anchors,
)

# ---------------------------------------------------------------------------
# Default input/output paths (this is a one-shot JHB accuracy-channel audit,
# not the multi-city census production path -- mirrors coj_audit_pilot.py's
# own module-level DEFAULT_* convention). grid_gpkg is the one path that IS
# multi-city-registry-resolvable and is looked up via core.region_registry,
# never hardcoded (.claude/rules/06-multi-city.md).
# ---------------------------------------------------------------------------

DATA_ROOT = Path("~/zasolar_data/geid_temporal").expanduser()
ZASOLAR_ROOT = Path(os.environ.get("ZASOLAR_ROOT", "/home/gao/projects/ZAsolar"))

DEFAULT_OUTPUT_ROOT = DATA_ROOT / "coj_audit_cohort_20260704"
DEFAULT_SEED = 20260704

DEFAULT_INTERVALS_CSV = (
    DATA_ROOT / "jhb_full382_fpcut_scan_2026-06-02" / "install_intervals.csv"
)
DEFAULT_CHIPGROUPS_CSV = (
    DATA_ROOT
    / "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
    / "chip_groups_as_anchors.csv"
)
DEFAULT_CENSUS_GPKG = (
    DATA_ROOT
    / "jhb_full382_fpcut_scan_2026-06-02"
    / "jhb_full382_fpcut_install_dated_2026-06-04.gpkg"
)
DEFAULT_PREFPCUT_GPKG = (
    ZASOLAR_ROOT
    / "results" / "analysis" / "full382_merge01_2026-05-15"
    / "jhb_full382_unified_A_merge01_c0925.gpkg"
)

BBOX_COLS = ("chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max")

# Mirrors coj_cohort_join._SCORABLE_OUTCOMES (private there) -- a planned unit
# yields a scorable chip iff its fetch reached one of these terminal outcomes.
# Keep in sync with that module if its fetch-outcome vocabulary ever changes.
SCORABLE_FETCH_OUTCOMES = frozenset({"ok", "skipped_existing"})

_D_2019_12_31 = date(2019, 12, 31)
_D_2023_12_31 = date(2023, 12, 31)


def _default_grid_gpkg() -> Path:
    """JNB task-grid geometry, looked up via the registry (never hardcoded)."""
    from core.region_registry import get_task_grid_path

    return Path(get_task_grid_path("johannesburg"))


def _chip_path(output_root: Path, anchor_id: str, year: int) -> Path:
    return output_root / "chips" / str(year) / f"{anchor_id}.tif"


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _read_csv_rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Stage: build
# ---------------------------------------------------------------------------


def _select_smoke_anchor_ids(
    intervals_csv: Path, *, seed: int, smoke_n: int
) -> set[str]:
    """Design §7 branch-coverage smoke selection over the full dated frame.

    Forces (at least, as hard floors -- ``smoke_n`` only tops up beyond them):
    2 ``c_cal_present_pre2019``; 2 ``c_cal_present_2019_2022`` incl. 1 genuine
    2019 bisector; 2 ``c_probe_2023`` incl. 1 falsification-subsample-eligible
    (a real ``install_interval_start`` in the ``(2019-12-31, 2023-12-31]``
    frame `select_falsification_subsample` draws from); all
    ``c_findings_s3like``, capped at 3. Negative controls are handled
    separately via ``nc_n`` (not anchor_ids-filtered) -- see `stage_build`.

    Deterministic for a fixed ``seed``. Pure function over the intervals CSV
    (reuses `coj_cohort_build`'s pure stratum/layer-plan functions); no gpkg
    I/O here.
    """
    from scripts.audit.coj_cohort_build import (
        C_CAL_PRESENT_PRE2019,
        C_CAL_PRESENT_2019_2022,
        C_FINDINGS_S3LIKE,
        C_PROBE_2023,
        assign_cohort_stratum,
        plan_units_for_anchor,
    )

    rows = _read_csv_rows(intervals_csv)

    by_stratum: dict[str, list[dict]] = {}
    for row in rows:
        stratum = assign_cohort_stratum(row)
        if stratum is not None:
            by_stratum.setdefault(stratum, []).append(row)
    for pool in by_stratum.values():
        pool.sort(key=lambda r: r["anchor_id"])

    rng = random.Random(seed)
    chosen: set[str] = set()

    def _draw(pool: list[dict], k: int) -> list[dict]:
        k = min(k, len(pool))
        return rng.sample(pool, k) if k else []

    # 1. pre2019: >= 2
    chosen.update(r["anchor_id"] for r in _draw(by_stratum.get(C_CAL_PRESENT_PRE2019, []), 2))

    # 2. 2019_2022: >= 2, at least one a genuine 2019 bisector.
    pool = by_stratum.get(C_CAL_PRESENT_2019_2022, [])
    bisector_pool = [
        r for r in pool
        if any(
            u.year == 2019 and u.unit_purpose == "bisector"
            for u in plan_units_for_anchor(r, in_fals_2019=False)
        )
    ]
    picked: list[str] = []
    if bisector_pool:
        picked.append(rng.choice(bisector_pool)["anchor_id"])
    else:
        print("[build] smoke: no genuine 2019 bisector found in c_cal_present_2019_2022 (skipping that forced branch)")
    remaining = [r for r in pool if r["anchor_id"] not in picked]
    while len(picked) < 2 and remaining:
        r = rng.choice(remaining)
        remaining.remove(r)
        picked.append(r["anchor_id"])
    chosen.update(picked)

    # 3. probe_2023: >= 2, at least one falsification-subsample-eligible.
    pool = by_stratum.get(C_PROBE_2023, [])

    def _fals_eligible(row: dict) -> bool:
        start = _parse_date(row.get("install_interval_start"))
        return start is not None and _D_2019_12_31 < start <= _D_2023_12_31

    fals_pool = [r for r in pool if _fals_eligible(r)]
    picked = []
    if fals_pool:
        picked.append(rng.choice(fals_pool)["anchor_id"])
    else:
        print("[build] smoke: no falsification-subsample-eligible anchor found in c_probe_2023 (skipping that forced branch)")
    remaining = [r for r in pool if r["anchor_id"] not in picked]
    while len(picked) < 2 and remaining:
        r = rng.choice(remaining)
        remaining.remove(r)
        picked.append(r["anchor_id"])
    chosen.update(picked)

    # 4. s3-like: all, capped at 3.
    chosen.update(r["anchor_id"] for r in _draw(by_stratum.get(C_FINDINGS_S3LIKE, []), 3))

    # Top up toward smoke_n (soft target on top of the hard floors above).
    if smoke_n and smoke_n > len(chosen):
        all_rows = [r for pool in by_stratum.values() for r in pool]
        remaining = [r for r in all_rows if r["anchor_id"] not in chosen]
        extra_n = min(smoke_n - len(chosen), len(remaining))
        if extra_n > 0:
            chosen.update(r["anchor_id"] for r in rng.sample(remaining, extra_n))

    return chosen


def stage_build(
    output_root: Path,
    *,
    intervals_csv: Path = DEFAULT_INTERVALS_CSV,
    chipgroups_csv: Path = DEFAULT_CHIPGROUPS_CSV,
    census_gpkg: Path = DEFAULT_CENSUS_GPKG,
    prefpcut_gpkg: Path = DEFAULT_PREFPCUT_GPKG,
    grid_gpkg: Path | None = None,
    seed: int = DEFAULT_SEED,
    n_fals: int = 1200,
    nc_n: int | None = None,
    nc_buffer_m: float = 100.0,
    nc_spacing_m: float = 150.0,
    smoke: int | None = None,
) -> dict:
    """Plan the cohort (strata + layer units + negative controls).

    ``smoke`` (optional): a seeded branch-coverage anchor subset (design §7)
    is computed from ``intervals_csv`` and passed to `build_cohort` as
    ``anchor_ids``; ``nc_n`` also defaults down to 2 in smoke mode unless the
    caller passes an explicit ``nc_n``.
    """
    from scripts.audit.coj_cohort_build import build_cohort

    anchor_ids: set[str] | None = None
    if smoke is not None:
        anchor_ids = _select_smoke_anchor_ids(intervals_csv, seed=seed, smoke_n=smoke)
        print(f"[build] smoke mode: {len(anchor_ids)} anchors selected: {sorted(anchor_ids)}")

    resolved_grid_gpkg = grid_gpkg if grid_gpkg is not None else _default_grid_gpkg()
    resolved_nc_n = nc_n if nc_n is not None else (2 if smoke is not None else 300)

    result = build_cohort(
        intervals_csv=intervals_csv,
        chipgroups_csv=chipgroups_csv,
        census_gpkg=census_gpkg,
        prefpcut_gpkg=prefpcut_gpkg,
        grid_gpkg=resolved_grid_gpkg,
        output_root=output_root,
        seed=seed,
        n_fals=n_fals,
        nc_n=resolved_nc_n,
        nc_buffer_m=nc_buffer_m,
        nc_spacing_m=nc_spacing_m,
        anchor_ids=anchor_ids,
    )
    return result


# ---------------------------------------------------------------------------
# Shared fetch-outcome bookkeeping (fetch_stats.jsonl is the single source of
# truth for "last known outcome per planned unit", read by both fetch (for
# the pre-filter + fetch_failures.csv) and join (to merge fetch_outcome onto
# each planned unit).
# ---------------------------------------------------------------------------


def _load_fetch_outcomes(output_root: Path) -> dict[tuple[str, int], str]:
    """{(anchor_id, year): last recorded fetch outcome} from fetch_stats.jsonl.

    Append-only log: later lines override earlier ones for the same unit
    (e.g. a retried unit that failed then later succeeded on a resumed run).
    """
    path = output_root / "fetch_stats.jsonl"
    outcomes: dict[tuple[str, int], str] = {}
    if not path.exists():
        return outcomes
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            anchor_id = rec.get("anchor_id")
            year = rec.get("year")
            if year is None:
                try:
                    year = int(rec.get("layer"))
                except (TypeError, ValueError):
                    continue
            if anchor_id is None:
                continue
            outcomes[(str(anchor_id), int(year))] = rec.get("outcome", "") or ""
    return outcomes


def _write_fetch_failures_csv(
    output_root: Path, unit_rows: list[dict], outcomes: dict[tuple[str, int], str]
) -> Path:
    rows = []
    for row in unit_rows:
        anchor_id = row["anchor_id"]
        year = int(row["year"])
        outcome = outcomes.get((anchor_id, year)) or "not_fetched"
        if outcome not in COVERED_FETCH_OUTCOMES:
            rows.append({"anchor_id": anchor_id, "year": year, "outcome": outcome})
    rows.sort(key=lambda r: (r["anchor_id"], r["year"]))
    out_path = output_root / "fetch_failures.csv"
    _write_csv(out_path, rows, ["anchor_id", "year", "outcome"])
    return out_path


# ---------------------------------------------------------------------------
# Stage: fetch
# ---------------------------------------------------------------------------


def _production_fetch_one(
    *, politeness_sleep_s: float, max_attempts: int, timeout_s: float = 30.0
):
    """Build the production ``fetch_one`` callable for `fetch_units_concurrent`.

    Wraps `fetch_and_save_chip` with the real ``requests`` transport. Keeps
    the pilot's per-request politeness *sequential within this call*: an
    extra ``politeness_sleep_s`` sleep after a real (non-skipped) fetch --
    this is what the design's wall-clock math (15.0s latency + 0.4s
    politeness = 15.4s/unit) assumes happens per worker thread, exactly
    mirroring `coj_audit_pilot.stage_fetch`'s own inter-request sleep, just
    relocated inside the per-unit callable so it applies per-worker under
    concurrency rather than globally-sequential.
    """
    import requests

    def _http_get(url: str):
        return requests.get(url, timeout=timeout_s, headers={"User-Agent": DEFAULT_USER_AGENT})

    def _fetch_one(unit: FetchUnit) -> FetchOutcome:
        outcome = fetch_and_save_chip(
            unit.anchor_id, unit.year, unit.bbox_4326, unit.out_path,
            http_get=_http_get, sleep_fn=time.sleep, max_attempts=max_attempts,
        )
        if outcome.outcome != "skipped_existing":
            time.sleep(politeness_sleep_s)
        return outcome

    return _fetch_one


def stage_fetch(
    output_root: Path,
    *,
    workers: int = 5,
    max_units: int | None = None,
    politeness_sleep_s: float = DEFAULT_POLITENESS_SLEEP_S,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Path:
    """Fetch every planned unit's CoJ chip, concurrently, resumably.

    Idempotent: units already terminally covered (per `fetch_stats.jsonl`, or
    an existing non-empty chip file not yet logged) are pre-filtered out
    before submission -- a re-run only touches the remaining gap. Cooperative
    on KeyboardInterrupt: a first Ctrl-C stops further submission and lets
    already-submitted units drain; `fetch_failures.csv` is always (re)written
    from whatever ran before returning, whether interrupted or not. A second
    Ctrl-C force-stops.
    """
    units_csv = output_root / "cohort_units.csv"
    unit_rows = _read_csv_rows(units_csv)

    outcomes = _load_fetch_outcomes(output_root)
    fetch_stats_path = output_root / "fetch_stats.jsonl"

    all_units: list[FetchUnit] = []
    for row in unit_rows:
        year = int(row["year"])
        anchor_id = row["anchor_id"]
        bbox = tuple(float(row[c]) for c in BBOX_COLS)
        out_path = _chip_path(output_root, anchor_id, year)
        all_units.append(FetchUnit(anchor_id=anchor_id, year=year, bbox_4326=bbox, out_path=out_path))

    to_submit: list[FetchUnit] = []
    n_prefiltered = 0
    fetch_stats_path.parent.mkdir(parents=True, exist_ok=True)
    with open(fetch_stats_path, "a") as stats_f:
        for u in all_units:
            key = (u.anchor_id, u.year)
            if outcomes.get(key) in COVERED_FETCH_OUTCOMES:
                continue  # already terminally covered in a prior run
            if u.out_path.exists() and u.out_path.stat().st_size > 0:
                # chip present on disk but not yet logged (e.g. a crashed
                # prior run) -- log it now so future invocations don't retry.
                rec = {
                    "anchor_id": u.anchor_id, "year": u.year, "layer": str(u.year),
                    "outcome": "skipped_existing", "n_bytes": u.out_path.stat().st_size,
                    "attempts": 0,
                }
                stats_f.write(json.dumps(rec) + "\n")
                stats_f.flush()
                outcomes[key] = "skipped_existing"
                n_prefiltered += 1
                continue
            to_submit.append(u)

    print(
        f"[fetch] {len(all_units)} planned units, {n_prefiltered} pre-filtered "
        f"(already fetched), {len(to_submit)} to submit"
    )
    if max_units is not None:
        print(f"[fetch] --max-units {max_units} (smoke cap)")

    n_done = {"n": 0}
    result_lock_note = threading.Lock()  # unused directly (driver already locks on_result)

    def _on_result(unit: FetchUnit, outcome: FetchOutcome) -> None:
        rec = {"anchor_id": unit.anchor_id, "year": unit.year, **outcome.to_dict()}
        with open(fetch_stats_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
        outcomes[(unit.anchor_id, unit.year)] = outcome.outcome
        n_done["n"] += 1
        if n_done["n"] % 50 == 0:
            print(f"[fetch] {n_done['n']}/{len(to_submit)} submitted units done")

    fetch_one = _production_fetch_one(
        politeness_sleep_s=politeness_sleep_s, max_attempts=max_attempts
    )

    stop_event = threading.Event()

    def _sigint_handler(signum, frame):
        if stop_event.is_set():
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise KeyboardInterrupt
        print(
            "[fetch] Ctrl-C received: draining already-submitted units, no new "
            "ones will be queued. Press Ctrl-C again to force-stop."
        )
        stop_event.set()

    old_handler = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except ValueError:
        old_handler = None  # not the main thread -- signal handling unavailable

    try:
        summary = fetch_units_concurrent(
            to_submit, fetch_one=fetch_one, on_result=_on_result,
            pool_size=workers, max_units=max_units, should_stop=stop_event.is_set,
        )
        print(f"[fetch] outcomes this pass: {summary}")
    except KeyboardInterrupt:
        print("[fetch] interrupted -- re-run `fetch` to resume (idempotent).")
    finally:
        if old_handler is not None:
            signal.signal(signal.SIGINT, old_handler)
        failures_csv = _write_fetch_failures_csv(output_root, unit_rows, outcomes)
        print(f"[fetch] -> {failures_csv}")

    return fetch_stats_path


# ---------------------------------------------------------------------------
# Stage: score
# ---------------------------------------------------------------------------


def stage_score(
    output_root: Path,
    *,
    checkpoint: Path | None = None,
    device: str = "cuda",
    skip_classifier: bool = False,
    max_units: int | None = None,
    flush_every: int = 200,
) -> Path:
    """Detector (+ optional classifier corroboration) scoring, resumable/chunked.

    Reads cohort_units.csv + fetch_stats.jsonl to build the manifest of
    scorable chips (fetch outcome ok/skipped_existing); scores it via
    `score_manifest_resumable` (skip-already-scored + flush-every-N chunking).
    Classifier corroboration reuses the pilot's subprocess seam
    (`run_classifier_manifest`) over just the chips still pending a
    probability (`pending_classifier_chips`), appended into
    classifier_scores.csv; a subprocess failure degrades to leaving those
    chips' probability blank -- never fatal, never crashes the stage.
    """
    from scripts.audit.score_coj_chips import (
        DEFAULT_CHECKPOINT,
        append_scored_rows,
        load_classifier_probs,
        load_detector,
        pending_classifier_chips,
        run_classifier_manifest,
        score_manifest_resumable,
    )
    import torch

    units_csv = output_root / "cohort_units.csv"
    unit_rows = _read_csv_rows(units_csv)
    outcomes = _load_fetch_outcomes(output_root)

    manifest_rows: list[dict[str, Any]] = []
    for row in unit_rows:
        anchor_id = row["anchor_id"]
        year = int(row["year"])
        outcome = outcomes.get((anchor_id, year), "")
        if outcome in SCORABLE_FETCH_OUTCOMES:
            manifest_rows.append(
                {
                    "anchor_id": anchor_id,
                    "year": year,
                    "chip_path": str(_chip_path(output_root, anchor_id, year)),
                }
            )
    if max_units is not None:
        manifest_rows = manifest_rows[:max_units]
        print(f"[score] --max-units {max_units} (smoke cap)")

    print(f"[score] {len(manifest_rows)} scorable chips planned this invocation")

    dev = torch.device(device if (device != "cuda" or torch.cuda.is_available()) else "cpu")
    model = load_detector(checkpoint or DEFAULT_CHECKPOINT, device=str(dev))
    scored_csv = output_root / "scored.csv"
    score_manifest_resumable(manifest_rows, model, dev, scored_csv=scored_csv, flush_every=flush_every)
    print(f"[score] -> {scored_csv}")

    if skip_classifier:
        print("[score] --skip-classifier: classifier corroboration skipped")
        return scored_csv

    scored_rows = _read_csv_rows(scored_csv) if scored_csv.exists() else []
    classifier_scores_csv = output_root / "classifier_scores.csv"
    pending_chips = pending_classifier_chips(scored_rows, classifier_scores_csv)
    print(f"[score] {len(pending_chips)} chips pending classifier corroboration")
    if not pending_chips:
        return scored_csv

    manifest_csv = output_root / "classifier_manifest.csv"
    _write_csv(manifest_csv, [{"chip_path": cp} for cp in pending_chips], ["chip_path"])
    batch_out_csv = output_root / "classifier_scores_batch.csv"
    result = run_classifier_manifest(manifest_csv, batch_out_csv)
    print(f"[score] classifier subprocess ok={result.get('ok')}")
    if result.get("ok"):
        probs = load_classifier_probs(batch_out_csv)
        new_rows = [{"chip_path": cp, "pv_prob": probs[cp]} for cp in pending_chips if cp in probs]
        append_scored_rows(new_rows, classifier_scores_csv, fieldnames=["chip_path", "pv_prob"])
        print(f"[score] classifier corroborated {len(new_rows)}/{len(pending_chips)} chips -> {classifier_scores_csv}")
    else:
        print(
            "[score] classifier subprocess FAILED, degrading to blank probs "
            f"(non-fatal): {result.get('error') or result.get('stderr', '')[:500]}"
        )

    return scored_csv


# ---------------------------------------------------------------------------
# Stage: join
# ---------------------------------------------------------------------------


def _load_scored_map(scored_csv: Path) -> dict[str, dict]:
    """{chip_path: scored.csv row} (last-wins; ordinarily each chip_path
    appears once thanks to the resume/skip logic in `score_manifest_resumable`)."""
    if not scored_csv.exists():
        return {}
    return {row["chip_path"]: row for row in _read_csv_rows(scored_csv) if row.get("chip_path")}


def stage_join(output_root: Path) -> Path:
    """Merge cohort_units.csv + fetch outcomes + scored.csv + classifier_scores.csv
    into the long bit table + wide anchor table (design §4a/§4b)."""
    from scripts.audit.score_coj_chips import load_classifier_probs

    unit_rows = _read_csv_rows(output_root / "cohort_units.csv")
    anchor_rows = _read_csv_rows(output_root / "cohort_anchors.csv")

    outcomes = _load_fetch_outcomes(output_root)
    detector_by_chip = _load_scored_map(output_root / "scored.csv")
    classifier_probs = load_classifier_probs(output_root / "classifier_scores.csv")

    bit_rows: list[dict] = []
    for row in unit_rows:
        anchor_id = row["anchor_id"]
        year = int(row["year"])
        outcome = outcomes.get((anchor_id, year)) or "not_fetched"
        chip_path = str(_chip_path(output_root, anchor_id, year)) if outcome in SCORABLE_FETCH_OUTCOMES else ""
        det = detector_by_chip.get(chip_path, {}) if chip_path else {}

        merged = dict(row)
        merged["fetch_outcome"] = outcome
        merged["chip_path"] = chip_path
        merged["detector_S"] = det.get("score", "")
        merged["classifier_pv_prob"] = classifier_probs.get(chip_path, "") if chip_path else ""
        bit_rows.append(build_cohort_bit(merged))

    bits_csv = output_root / "cohort_audit_bits.csv"
    _write_csv(bits_csv, bit_rows, list(LONG_COLUMNS))

    human_queue_csv = output_root / "human_queue.csv"
    _write_csv(human_queue_csv, [b for b in bit_rows if b["routed_to_queue"]], list(LONG_COLUMNS))

    wide_rows = pivot_anchors(bit_rows, anchor_rows)
    anchors_out_csv = output_root / "cohort_audit_anchors.csv"
    _write_csv(anchors_out_csv, wide_rows, list(WIDE_COLUMNS))

    n_queue = sum(1 for b in bit_rows if b["routed_to_queue"])
    print(
        f"[join] {len(bit_rows)} bits -> {bits_csv} ({n_queue} routed to {human_queue_csv}); "
        f"{len(wide_rows)} anchors -> {anchors_out_csv}"
    )
    return bits_csv


# ---------------------------------------------------------------------------
# Stage: gates
# ---------------------------------------------------------------------------


def stage_gates(output_root: Path) -> Path:
    bit_rows = _read_csv_rows(output_root / "cohort_audit_bits.csv")
    planned_units = _read_csv_rows(output_root / "cohort_units.csv")

    nc_csv = output_root / "negative_controls.csv"
    nc_anchor_ids: set[str] = set()
    if nc_csv.exists():
        nc_anchor_ids = {r["anchor_id"] for r in _read_csv_rows(nc_csv)}

    gates = compute_cohort_gates(bit_rows, nc_anchor_ids=nc_anchor_ids)
    coverage = coverage_report(planned_units, bit_rows)

    gates_report_path = output_root / "gates_report.json"
    with open(gates_report_path, "w") as f:
        json.dump({"gates": gates, "coverage": coverage}, f, indent=2, default=str)

    # Refresh fetch_failures.csv from the authoritative coverage computation
    # (fetch stage already wrote a best-effort version from the same data).
    _write_csv(
        output_root / "fetch_failures.csv",
        [
            {"anchor_id": u["anchor_id"], "year": u["year"], "outcome": u["outcome"]}
            for u in coverage["failed_units"]
        ],
        ["anchor_id", "year", "outcome"],
    )

    print(f"[gates] -> {gates_report_path}")
    print(
        f"[gates] coverage {coverage['covered_anchors']}/{coverage['dated_anchors']} "
        f"= {coverage['coverage']} ({'PASS' if coverage['passes_95pct'] else 'FAIL'} at >=0.95)"
    )
    print(
        f"[gates] gate_a passes={gates['gate_a']['passes']}  "
        f"gate_nc passes={gates['gate_nc']['passes']} "
        f"({gates['gate_nc']['n_false_present']}/{gates['gate_nc']['n_controls']})  "
        f"gate_b violations={gates['gate_b']['violations']}  "
        f"clamp_findings={gates['clamp_findings']['count']}"
    )
    return gates_report_path


# ---------------------------------------------------------------------------
# Stage: report
# ---------------------------------------------------------------------------


def stage_report(output_root: Path) -> dict:
    from scripts.audit.coj_cohort_report import build_cohort_report

    result = build_cohort_report(output_root=output_root)
    print(f"[report] -> {result['cohort_report_md']}")
    print(f"[report] -> {result['contradiction_rate_csv']}")
    return result


# ---------------------------------------------------------------------------
# CLI
#
# One flat parser (mirrors coj_audit_pilot.py's own style, and the design's
# CLI spec, which lists a single shared flag block under all seven stage
# names rather than per-stage schemas): every flag is always available
# regardless of `stage`, unused by whichever stages don't need it. This is
# deliberate, not an oversight -- it is what lets the launcher script's
# EXTRA_FLAGS be appended verbatim to every stage invocation (e.g. `--smoke
# 30` only matters to `build`, but doesn't error out on `fetch`/`score`/...).
# `python ... <stage> --help` and `python ... --help` both exit 0 and show
# the same full flag set; per-stage docs are in each `stage_*` docstring.
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("stage", choices=["build", "fetch", "score", "join", "gates", "report", "all"])
    ap.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)

    # build
    ap.add_argument("--n-fals", type=int, default=1200)
    ap.add_argument("--nc-n", type=int, default=None, help="default 300 (2 in --smoke mode unless set)")
    ap.add_argument("--nc-buffer-m", type=float, default=100.0)
    ap.add_argument("--nc-spacing-m", type=float, default=150.0)
    ap.add_argument(
        "--smoke", type=int, default=None, metavar="N",
        help="build-only: seeded branch-coverage anchor subset (design §7) instead of the full cohort",
    )
    ap.add_argument("--intervals-csv", type=Path, default=DEFAULT_INTERVALS_CSV)
    ap.add_argument("--chipgroups-csv", type=Path, default=DEFAULT_CHIPGROUPS_CSV)
    ap.add_argument("--census-gpkg", type=Path, default=DEFAULT_CENSUS_GPKG)
    ap.add_argument("--prefpcut-gpkg", type=Path, default=DEFAULT_PREFPCUT_GPKG)
    ap.add_argument(
        "--grid-gpkg", type=Path, default=None,
        help="build-only; default: core.region_registry.get_task_grid_path('johannesburg')",
    )

    # fetch / score
    ap.add_argument("--workers", type=int, default=5, help="fetch-only: concurrent fetch workers")
    ap.add_argument("--max-units", type=int, default=None, help="fetch/score-only: smoke cap on units processed")
    ap.add_argument("--checkpoint", type=Path, default=None, help="score-only: detector checkpoint override")
    ap.add_argument("--device", default="cuda", help="score-only")
    ap.add_argument("--skip-classifier", action="store_true", help="score-only")
    ap.add_argument("--flush-every", type=int, default=200, help="score-only: resume chunk size")

    return ap


def main() -> None:
    ap = build_arg_parser()
    args = ap.parse_args()

    output_root = Path(args.output_root).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)

    if args.stage in ("build", "all"):
        stage_build(
            output_root,
            intervals_csv=args.intervals_csv,
            chipgroups_csv=args.chipgroups_csv,
            census_gpkg=args.census_gpkg,
            prefpcut_gpkg=args.prefpcut_gpkg,
            grid_gpkg=args.grid_gpkg,
            seed=args.seed,
            n_fals=args.n_fals,
            nc_n=args.nc_n,
            nc_buffer_m=args.nc_buffer_m,
            nc_spacing_m=args.nc_spacing_m,
            smoke=args.smoke,
        )

    if args.stage in ("fetch", "all"):
        stage_fetch(output_root, workers=args.workers, max_units=args.max_units)

    if args.stage in ("score", "all"):
        stage_score(
            output_root,
            checkpoint=args.checkpoint,
            device=args.device,
            skip_classifier=args.skip_classifier,
            max_units=args.max_units,
            flush_every=args.flush_every,
        )

    if args.stage in ("join", "all"):
        stage_join(output_root)

    if args.stage in ("gates", "all"):
        stage_gates(output_root)

    if args.stage in ("report", "all"):
        stage_report(output_root)


if __name__ == "__main__":
    main()
