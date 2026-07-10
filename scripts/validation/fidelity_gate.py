#!/usr/bin/env python3
"""ISSUE-06 fidelity gate — student-vs-teacher install-interval agreement (offline).

Runs the three ISSUE-06 gate numbers for one student backbone (DINOv3-L-SAT or
the DINOv2-S floor), reusing the end-to-end interval-agreement machinery rather
than re-authoring it:

* **Gate 1 (reproducibility):** student self rep-pass-vs-rep-pass interval
  agreement (``--self-repro``) — two student re-scoring passes over the same
  frames must decode to byte-identical intervals (deterministic student, "no
  wobble"). Nonzero exit on mismatch.
* **Gate 2 (no-answer-change):** student-pipeline vs Gemini-pipeline interval
  agreement, with **both pipelines decoded by the same adopted Phase-0 decoder**
  (changepoint + EB prior), inventory-weighted headline + dated-only denominator
  (D8), against the Gemini rep-i-vs-rep-j ceiling re-derived on the same banked
  panel + its pairwise spread (the R1 tie-break consumes the spread).
* **Gate 3 (distillation fidelity):** held-out chip-level agreement — ALREADY
  computed by ISSUE-04/05; lifted into the verdict, not recomputed here.

This harness supplies the two genuinely-new pieces the existing tools lack:
1. a **student re-scoring pass** (the mirror of
   ``solar_backdating.eval.scan_state_io.load_scan_observations`` — same frame
   walk / ordering / date-drop, but per-frame verdicts come from the injected
   student scorer instead of the banked Gemini verdict), and
2. a **posterior -> agree_key adapter** (``posterior_to_agree_key``) mapping an
   ``InstallDatePosterior`` onto the canonical ``UNDATED / AP_BOUND<=|{end} /
   INTERVAL|{start}|{end}`` key (production semantics —
   ``llm_endtoend_build_reference.agree_key`` :91).

Everything else is reuse: the reference->scan_state join keys + decoder wiring
(``estimator_endtoend_decode``: ``_REPLACED_PROVIDERS`` :146, ``_build_config``
:186, adopted default :119/122), the raw-delivery key (``panel_io.agree_key_raw``
== ``llm_endtoend_analyze.agree_key_raw`` :56), and the D8 inventory-weighting +
strata aggregation (``llm_endtoend_analyze.agg`` :140, replicated verbatim below
as ``agg`` because the original is a closure).

STUDENT RE-RENDER (ISSUE-06 verdict skeleton LOCKED 2026-07-06, pre-gate-2):
banked rep ``chip_path`` files are ``chip_geom_v1_banked96`` GeoTIFFs (often with
a co-located marked review PNG). Both student heads trained on
``chip_geom_v2_tight12`` **nomarker** crops. Real-GPU gate runs MUST re-render
each banked source through ``ensure_single_target_review_png`` +
``resolve_chip_geometry(chip_geom_v2_tight12)`` with ``draw_marker=False`` before
scoring. Frames that cannot be re-rendered are dropped from **both** pipelines'
observation sequences (like-for-like frame sets). Pollution stop-rule: if
re-render drops exceed ``--render-drop-stop-pct`` of frames (default 2%), the
run aborts before shipping gate-2 numbers. Teacher ceiling stays on full banked
frame sets. ``--no-student-rerender`` is an emergency escape that re-enables the
legacy identity path and stamps a hard warning into provenance (not a clean
gate). CPU unit tests inject a fake scorer with the identity resolver and are
unaffected.

The gate is an OFFLINE benchmark reported in ``docs/`` — never a CI assertion.

HARD GUARDS: refuses to run if ``--out`` resolves inside the read-only
``llm_endtoend_storebacked_20260704`` bank; fails loudly (no silent skip) on a
missing/corrupt student chip unless ``--skip-missing-chips`` (default OFF).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validation.estimator_endtoend_decode import (  # noqa: E402
    ADOPTED_DECODER_EPOCH_GAP_DAYS,
    ADOPTED_ESTIMATOR,
    DEFAULT_COHORT_PRIOR_JSON,
    DEFAULT_EMISSIONS_JSON,
    DEFAULT_VEXCEL_CSV,
    _REPLACED_PROVIDERS,
    _build_config,
    _load_emissions,
    _load_vexcel_ceiling,
    _s,
)
from solar_backdating.estimators import (  # noqa: E402
    ClampContext,
    VintageObservation,
    get_estimator,
)
from solar_backdating.eval.panel_io import (  # noqa: E402
    agree_key_raw,
    load_endtoend_delivery,
    load_endtoend_reference,
)
from solar_backdating.eval.scan_state_io import (  # noqa: E402
    _PRESENT_MAP,
    _parse_capture_date,
    load_scan_observations,
)

_STOREBACKED_BANK = "llm_endtoend_storebacked_20260704"
_MISSING_KEY = "MISSING_FROM_DELIVERY"


# --------------------------------------------------------------------------- #
# posterior -> agree_key adapter (the highest-risk new piece; unit-tested)
# --------------------------------------------------------------------------- #
def _iso(d) -> str:
    return d.isoformat() if d is not None else ""


def posterior_to_agree_key(post) -> str:
    """Map an ``InstallDatePosterior`` onto the canonical interval-agreement key.

    Canonical key (production semantics, ``llm_endtoend_build_reference.agree_key``
    :91): ``UNDATED`` / ``AP_BOUND<=|{end}`` / ``INTERVAL|{start}|{end}``.

    Discriminator = ``map_date`` (the mode-hit contract), chosen so this adapter
    is byte-consistent with ``estimator_endtoend_decode``'s own year extraction
    (``year = posterior.map_date[:4] if posterior.map_date else ""`` :294):

    * ``map_date == ""`` -> **UNDATED**. Covers all three empty-map_date states —
      ``t==0`` (no usable evidence), ``map_index==t`` beyond-window (last_absent
      set but no dated verdict), and the clamp-inverted degenerate (bounds set
      but ``map_date`` blanked). All are "no date" in production terms.
    * ``map_date != ""`` and ``map_interval_start is None`` -> **AP_BOUND<=|{end}**.
      An open-left cell (``map_index==0``, present since before the observed
      window) is exactly production's left-censored ``date_is_bound==1``; ``end``
      is the earliest-present upper bound (``map_interval_end``, never None here).
    * ``map_date != ""`` and ``map_interval_start is not None`` ->
      **INTERVAL|{start}|{end}** (a real absent->present bracket).

    ``p_undated`` is deliberately NOT consumed — the production key never reads a
    posterior mass, and the decoder folds the beyond-window decision into
    ``map_date``/``map_index`` already.
    """
    if not post.map_date:
        return "UNDATED"
    if post.map_interval_start is None:
        return f"AP_BOUND<=|{_iso(post.map_interval_end)}"
    return f"INTERVAL|{_iso(post.map_interval_start)}|{_iso(post.map_interval_end)}"


# --------------------------------------------------------------------------- #
# reuse-shims: loaders + decoder builder
# --------------------------------------------------------------------------- #
def load_reference_rows(reference_csv: Path) -> list[dict]:
    """One dict per sampled installation, carrying the join + weighting columns."""
    ref = load_endtoend_reference(reference_csv)
    rows = []
    for r in ref.to_dict("records"):
        rows.append(
            {
                "sf": int(r["source_feature_id"]),
                "group_anchor_id": _s(r.get("group_anchor_id")),
                "target_anchor_id": _s(r.get("target_anchor_id")),
                "grid_id": _s(r.get("grid_id")),
                "stratum": _s(r.get("status_stratum")),
                "w": float(r["inv_weight"]),
                "prod_agree_key": _s(r.get("prod_agree_key")),
            }
        )
    return rows


def stratum_weights(ref_rows: list[dict]) -> dict[str, float]:
    """stratum -> inv_weight. **First-wins** to be byte-identical to
    ``llm_endtoend_analyze.agg`` which takes ``ref.loc[ref.status_stratum==st,
    "inv_weight"].iloc[0]`` (the first reference row of the stratum). ``ref_rows``
    preserves reference.csv order, so first-seen == ``.iloc[0]``. Today
    ``inv_weight`` is constant within a stratum (verified), so first-vs-last is a
    no-op on current data; first-wins keeps the match if a future reference.csv
    ever carries per-row weights (R2#4)."""
    out: dict[str, float] = {}
    for r in ref_rows:
        if r["stratum"] not in out:
            out[r["stratum"]] = r["w"]
    return out


def load_delivery_by_sf(delivery_csv: Path) -> dict[int, dict]:
    d = load_endtoend_delivery(delivery_csv)
    return {int(r["source_feature_id"]): r for r in d.to_dict("records")}


def build_decoder(
    *,
    estimator: str = ADOPTED_ESTIMATOR,
    emissions=None,
    cohort_prior=None,
    decoder_epoch_gap_days: int = ADOPTED_DECODER_EPOCH_GAP_DAYS,
):
    """Return ``(est, config)`` for the adopted Phase-0 decoder (both pipelines and
    the ceiling go through this SAME object — decoder-choice-invariant by design)."""
    est = get_estimator(estimator)
    config = _build_config(
        epoch_gap_days=None,
        decoder_epoch_gap_days=decoder_epoch_gap_days,
        emissions=emissions,
        cohort_prior=cohort_prior,
    )
    return est, config


def new_stats() -> dict:
    return Counter()


# --------------------------------------------------------------------------- #
# student re-render adapter (LOCKED input contract) + re-scoring pass
# --------------------------------------------------------------------------- #
@dataclass
class _V:
    pv_present: object
    pv_score: float | None
    quality_flag: str


@dataclass
class _RawFrame:
    capture_date: object  # date
    raw_date: str
    chip_path: str
    teacher_pv: object
    teacher_confidence: object
    teacher_quality_flag: str


@dataclass
class StudentChipResolver:
    """Map a banked source chip to the PNG the student head was trained on.

    Default production path: re-render banked GeoTIFF at ``chip_geom_v2_tight12``
    with ``draw_marker=False`` (nomarker), via the same
    ``ensure_single_target_review_png`` + ``resolve_chip_geometry`` chain the
    distillation set used. Identity mode (``chip_targets_path is None``) leaves
    the banked path unchanged — used by CPU unit tests / ``--no-student-rerender``.
    """

    chip_targets_path: Path | None = None
    geometry_version: str = "chip_geom_v2_tight12"
    contain_footprint: bool = True
    # filled at init when chip_targets_path is set
    _by_target_id: dict = field(default_factory=dict, repr=False)
    _by_chip_id: dict = field(default_factory=dict, repr=False)
    _geom: object = field(default=None, repr=False)
    _resolve_fn: Callable[[str, str], tuple[str | None, str | None]] | None = field(
        default=None, repr=False
    )

    def __post_init__(self) -> None:
        if self._resolve_fn is not None:
            return
        if self.chip_targets_path is None:
            # identity: return the banked path as-is (tests / emergency escape)
            self._resolve_fn = lambda chip_path, _anchor_id: (
                (chip_path, None) if chip_path else (None, "empty_chip_path")
            )
            return
        from scripts.temporal.build_distillation_set import (  # noqa: PLC0415
            _anchor_kind,
            _marker_from_row,
            load_chip_targets_lookup,
        )
        from scripts.temporal.chip_geometry import (  # noqa: PLC0415
            contain_crop_to_footprint,
            resolve_chip_geometry,
        )
        from scripts.temporal.gehi_common import ensure_single_target_review_png  # noqa: PLC0415

        self._by_target_id, self._by_chip_id = load_chip_targets_lookup(
            Path(self.chip_targets_path)
        )
        self._geom = resolve_chip_geometry(self.geometry_version)

        def _resolve(chip_path: str, anchor_id: str) -> tuple[str | None, str | None]:
            if not chip_path:
                return None, "empty_chip_path"
            src = Path(chip_path)
            if not src.exists() or src.stat().st_size == 0:
                return None, "source_missing_or_empty"
            kind = _anchor_kind(anchor_id)
            if kind == "t":
                ct_row = self._by_target_id.get(anchor_id)
            elif kind == "c":
                ct_row = self._by_chip_id.get(anchor_id)
            else:
                ct_row = self._by_target_id.get(anchor_id) or self._by_chip_id.get(
                    anchor_id
                )
            if ct_row is None:
                return None, "unresolvable_chip_targets_join"
            try:
                geom = self._geom
                marker = _marker_from_row(ct_row)
                chip_size_m = float(
                    ct_row.get("chip_size_m", geom.download_chip_size_m)
                    or geom.download_chip_size_m
                )
                crop_mult = float(geom.crop_context_multiplier)
                if self.contain_footprint:
                    radius = marker.search_radius_m or (chip_size_m * 0.05)
                    base_crop = min(
                        chip_size_m,
                        max(geom.min_crop_size_m, 2.0 * radius * crop_mult),
                    )
                    contained = contain_crop_to_footprint(
                        base_crop,
                        source_width_m=float(ct_row.get("source_width_m", 0.0) or 0.0),
                        source_height_m=float(ct_row.get("source_height_m", 0.0) or 0.0),
                        chip_size_m=chip_size_m,
                    )
                    if radius > 0:
                        crop_mult = contained / (2.0 * radius)
                png = ensure_single_target_review_png(
                    src,
                    marker,
                    chip_size_m=chip_size_m,
                    crop_context_multiplier=crop_mult,
                    min_crop_size_m=geom.min_crop_size_m,
                    min_output_px=geom.min_output_px,
                    draw_marker=False,
                )
            except Exception as exc:  # noqa: BLE001 — recorded as a render drop
                return None, f"render_failed:{type(exc).__name__}:{exc}"
            if png is None or not Path(png).exists() or Path(png).stat().st_size == 0:
                return None, "render_output_missing_or_empty"
            return str(png), None

        self._resolve_fn = _resolve

    @property
    def active(self) -> bool:
        return self.chip_targets_path is not None

    def resolve(self, chip_path: str, anchor_id: str) -> tuple[str | None, str | None]:
        assert self._resolve_fn is not None
        return self._resolve_fn(chip_path, anchor_id)


def _iter_raw_frames(path: Path) -> tuple[str, list[_RawFrame]]:
    """Walk a scan_state into ordered raw frames + the top-level anchor_id."""
    data = json.loads(Path(path).read_text())
    anchor_id = str(data.get("anchor_id") or Path(path).stem)
    frames: list[_RawFrame] = []
    for rnd in data.get("rounds", []) or []:
        for result in rnd.get("results", []) or []:
            d = _parse_capture_date(result.get("capture_date"))
            if d is None:
                continue
            frames.append(
                _RawFrame(
                    capture_date=d,
                    raw_date=str(result.get("capture_date") or ""),
                    chip_path=str(result.get("chip_path") or ""),
                    teacher_pv=result.get("pv_present"),
                    teacher_confidence=result.get("confidence"),
                    teacher_quality_flag=str(result.get("quality_flag") or "usable")
                    or "usable",
                )
            )
    return anchor_id, frames


def _score_chips(
    chip_paths: list[str],
    raws: list[str],
    scorer,
    cache: dict | None,
    *,
    scorer_key: str,
    skip_missing: bool,
    stats: Counter,
) -> list[_V]:
    """Score already-resolved chip paths through ``scorer.score(picks)`` in order.

    Fails loudly (FileNotFoundError with the path) on a missing chip unless
    ``skip_missing`` — the scorer would otherwise silently abstain. Caches per
    ``(resolved_path, scorer_key)`` so a re-run / audit does not re-score."""
    from scripts.temporal.presence_scorer import Pick

    results: list[_V | None] = [None] * len(chip_paths)
    picks: list[Pick] = []
    pick_idx: list[int] = []
    for i, rp in enumerate(chip_paths):
        ck = (rp, scorer_key)
        if cache is not None and ck in cache:
            results[i] = cache[ck]
            stats["cache_hit"] += 1
            continue
        if not rp or not Path(rp).exists():
            stats["missing_chip"] += 1
            if not skip_missing:
                raise FileNotFoundError(f"student chip missing (no silent skip): {rp!r}")
            v = _V(None, None, "missing_chip")
            results[i] = v
            if cache is not None:
                cache[ck] = v
            continue
        picks.append(Pick(chip_path=rp, capture_date=raws[i], index=i))
        pick_idx.append(i)
    if picks:
        obs = scorer.score(picks)
        if len(obs) != len(picks):
            raise RuntimeError(f"scorer returned {len(obs)} obs for {len(picks)} picks")
        for i, ob in zip(pick_idx, obs):
            v = _V(ob.pv_present, ob.pv_score, ob.quality_flag)
            results[i] = v
            if cache is not None:
                cache[(chip_paths[i], scorer_key)] = v
    return [r for r in results]  # type: ignore[return-value]


def load_paired_observations(
    path: Path,
    scorer,
    cache: dict | None,
    *,
    scorer_key: str,
    skip_missing: bool,
    stats: Counter,
    resolver: StudentChipResolver | None = None,
) -> tuple[list[VintageObservation], list[VintageObservation], dict]:
    """Like-for-like teacher + student observation sequences for one scan_state.

    When ``resolver`` is active (tight12 nomarker re-render), a frame that
    cannot be re-rendered is dropped from **both** sequences (LOCKED render-drop
    policy). Returns ``(teacher_obs, student_obs, drop_info)`` where
    ``drop_info`` carries per-anchor frame counts for the pollution stop-rule.
    """
    resolver = resolver or StudentChipResolver()  # identity
    anchor_id, frames = _iter_raw_frames(path)
    kept: list[_RawFrame] = []
    resolved_paths: list[str] = []
    n_drop = 0
    drop_reasons: Counter = Counter()
    anchors_with_drop = False
    for fr in frames:
        rp, err = resolver.resolve(fr.chip_path, anchor_id)
        if err is not None or not rp:
            n_drop += 1
            drop_reasons[err or "unknown"] += 1
            anchors_with_drop = True
            stats["render_drop"] += 1
            stats[f"render_drop:{err or 'unknown'}"] += 1
            continue
        kept.append(fr)
        resolved_paths.append(rp)
        stats["render_ok"] += 1

    drop_info = {
        "anchor_id": anchor_id,
        "n_frames": len(frames),
        "n_kept": len(kept),
        "n_dropped": n_drop,
        "drop_reasons": dict(drop_reasons),
        "had_drop": anchors_with_drop,
    }

    teacher_obs: list[VintageObservation] = []
    for i, fr in enumerate(kept):
        teacher_obs.append(
            VintageObservation(
                capture_date=fr.capture_date,
                pv_present=_PRESENT_MAP.get(fr.teacher_pv, ""),
                confidence=fr.teacher_confidence,
                quality_flag=fr.teacher_quality_flag,
                source_row=i,
            )
        )

    student_obs: list[VintageObservation] = []
    if scorer is not None:
        verdicts = _score_chips(
            resolved_paths,
            [fr.raw_date for fr in kept],
            scorer,
            cache,
            scorer_key=scorer_key,
            skip_missing=skip_missing,
            stats=stats,
        )
        for i, (fr, v) in enumerate(zip(kept, verdicts)):
            student_obs.append(
                VintageObservation(
                    capture_date=fr.capture_date,
                    pv_present=_PRESENT_MAP.get(v.pv_present, ""),
                    confidence=v.pv_score,
                    quality_flag=v.quality_flag or "usable",
                    source_row=i,
                )
            )
    return teacher_obs, student_obs, drop_info


def load_student_observations(
    path: Path,
    scorer,
    cache: dict | None,
    *,
    scorer_key: str,
    skip_missing: bool,
    stats: Counter,
    resolver: StudentChipResolver | None = None,
) -> list[VintageObservation]:
    """Student-only convenience wrapper (tests / callers that ignore teacher side)."""
    _teacher, student, _drop = load_paired_observations(
        path,
        scorer,
        cache,
        scorer_key=scorer_key,
        skip_missing=skip_missing,
        stats=stats,
        resolver=resolver,
    )
    return student


# --------------------------------------------------------------------------- #
# per-rep walk: reference row -> decoded agree_key (teacher + student)
# --------------------------------------------------------------------------- #
def rep_keys(
    rep_dir: Path,
    ref_rows: list[dict],
    delivery_by_sf: dict[int, dict],
    *,
    est,
    config,
    vexcel_ceiling: dict,
    teacher_only: bool,
    scorer,
    cache: dict | None,
    scorer_key: str,
    skip_missing: bool,
    stats: Counter,
    resolver: StudentChipResolver | None = None,
    drop_log: list | None = None,
    apply_render_drop_to_teacher: bool = True,
) -> dict[int, dict]:
    """sf -> {teacher_key, student_key, decoded, had_unusable}.

    Walk mirrors ``estimator_endtoend_decode.decode_rep`` exactly (same provider
    -> layer/anchor-col join, same splice-through + missing-scan-state fallbacks),
    but emits the full ``agree_key`` (via the posterior adapter) instead of just
    the year, and decodes a parallel student observation sequence.

    When the student re-render resolver is active and
    ``apply_render_drop_to_teacher`` is True (gate-2 student-vs-teacher path),
    frames that fail re-render are dropped from **both** pipelines. The teacher
    ceiling path passes ``teacher_only=True`` and keeps the full banked frame
    set (LOCKED policy).
    """
    resolver = resolver or StudentChipResolver()
    out: dict[int, dict] = {}
    for row in ref_rows:
        sf = row["sf"]
        drow = delivery_by_sf.get(sf)
        if drow is None:
            out[sf] = {
                "teacher_key": _MISSING_KEY,
                "student_key": _MISSING_KEY,
                "decoded": False,
                "had_unusable": False,
            }
            stats["missing_from_delivery"] += 1
            continue
        provider = _s(drow.get("date_provider"))
        layer_col = _REPLACED_PROVIDERS.get(provider)
        if layer_col is None:
            # production splice-through (L_census / AP-bound / undated): identical
            # across pipelines by construction -> both sides take the delivery key.
            k = agree_key_raw(drow)
            out[sf] = {
                "teacher_key": k,
                "student_key": k,
                "decoded": False,
                "had_unusable": False,
            }
            continue
        layer, anchor_col = layer_col
        anchor_id = row[anchor_col]
        scan_path = rep_dir / layer / "scan_states" / f"{anchor_id}.json"
        if not scan_path.exists():
            # missing scan state: mirror decode_rep (keep the delivery baseline).
            k = agree_key_raw(drow)
            out[sf] = {
                "teacher_key": k,
                "student_key": k,
                "decoded": False,
                "had_unusable": False,
            }
            stats["scan_state_missing"] += 1
            continue

        doc = json.loads(scan_path.read_text())
        had_unusable = any(
            (r.get("quality_flag") == "unusable")
            for rnd in (doc.get("rounds") or [])
            for r in (rnd.get("results") or [])
        )
        clamp = ClampContext(ceiling_date=vexcel_ceiling.get(row["grid_id"]))
        student_key = None
        if teacher_only or not apply_render_drop_to_teacher or not resolver.active:
            # Full banked frame set for the ceiling, or identity-resolver tests.
            teacher_obs = load_scan_observations(scan_path)
            teacher_key = posterior_to_agree_key(est(teacher_obs, clamp, config))
            if not teacher_only:
                student_obs = load_student_observations(
                    scan_path,
                    scorer,
                    cache,
                    scorer_key=scorer_key,
                    skip_missing=skip_missing,
                    stats=stats,
                    resolver=resolver,
                )
                student_key = posterior_to_agree_key(est(student_obs, clamp, config))
        else:
            # LOCKED: drop re-render failures from both pipelines.
            teacher_obs, student_obs, drop_info = load_paired_observations(
                scan_path,
                scorer,
                cache,
                scorer_key=scorer_key,
                skip_missing=skip_missing,
                stats=stats,
                resolver=resolver,
            )
            if drop_log is not None and drop_info.get("had_drop"):
                drop_log.append(drop_info)
            teacher_key = posterior_to_agree_key(est(teacher_obs, clamp, config))
            student_key = posterior_to_agree_key(est(student_obs, clamp, config))
        out[sf] = {
            "teacher_key": teacher_key,
            "student_key": student_key,
            "decoded": True,
            "had_unusable": had_unusable,
        }
    return out


# --------------------------------------------------------------------------- #
# D8 aggregation — replicated verbatim from llm_endtoend_analyze.agg :140
# (the original is a closure over ``rows``/``ref``; copied per recon guidance)
# --------------------------------------------------------------------------- #
def agg(rows: list[dict], selector, strat_weight: dict[str, float]):
    """(unweighted_rate, inv_weighted_rate, per_stratum). ``rows`` carry
    ``stratum``; inventory weight is the stratum-level constant from reference.csv."""
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


# --------------------------------------------------------------------------- #
# gate-2 metrics: student-vs-teacher + rep-vs-rep ceiling
# --------------------------------------------------------------------------- #
def student_teacher_rows(rep_keys_map: dict[int, dict], ref_by_sf: dict[int, dict]) -> list[dict]:
    rows = []
    for sf, info in rep_keys_map.items():
        if info["student_key"] is None or info["teacher_key"] == _MISSING_KEY:
            continue
        rows.append(
            {
                "sf": sf,
                "stratum": ref_by_sf[sf]["stratum"],
                "w": ref_by_sf[sf]["w"],
                "agree": int(info["student_key"] == info["teacher_key"]),
                "decoded": info["decoded"],
                "teacher_dated": info["teacher_key"] != "UNDATED",
                "had_unusable": info["had_unusable"],
            }
        )
    return rows


def student_vs_teacher(rows: list[dict], strat_weight: dict[str, float]) -> dict:
    decoded = [r for r in rows if r["decoded"]]
    dated = [r for r in decoded if r["teacher_dated"]]
    u_all, w_all, by_all = agg(rows, lambda r: r["agree"], strat_weight)
    u_dec, w_dec, by_dec = agg(decoded, lambda r: r["agree"], strat_weight)
    u_dat, w_dat, _ = (None, None, {}) if not dated else agg(
        dated, lambda r: r["agree"], strat_weight
    )
    # R3 split: decoded rows split by whether the teacher flagged any unusable frame.
    u_flag = [r for r in decoded if r["had_unusable"]]
    u_clean = [r for r in decoded if not r["had_unusable"]]
    r3 = {
        "with_unusable_frame": None
        if not u_flag
        else {"n": len(u_flag), "agreement_inv_weighted": agg(u_flag, lambda r: r["agree"], strat_weight)[1]},
        "no_unusable_frame": None
        if not u_clean
        else {"n": len(u_clean), "agreement_inv_weighted": agg(u_clean, lambda r: r["agree"], strat_weight)[1]},
    }
    n_dec = len(decoded)
    return {
        "n_all_units": len(rows),
        "n_decoded": n_dec,
        "n_dated": len(dated),
        "dated_fraction": round(len(dated) / n_dec, 4) if n_dec else None,
        "all_units": {"agreement_unweighted": u_all, "agreement_inv_weighted": w_all, "by_stratum": by_all},
        "decoded_only": {"agreement_unweighted": u_dec, "agreement_inv_weighted": w_dec, "by_stratum": by_dec},
        "dated_only_denominator": {"agreement_unweighted": u_dat, "agreement_inv_weighted": w_dat},
        "r3_unusable_split": r3,
    }


def pairwise_ceiling(
    rep_keys_maps: list[dict[int, dict]],
    ref_by_sf: dict[int, dict],
    strat_weight: dict[str, float],
) -> dict:
    """Gemini rep-i-vs-rep-j teacher-key agreement for every pair, on the decoded
    intersection (the known 230/236/239-of-244 quirk: pairwise uses the anchors
    both reps decoded; report per-pair denominators + what was missing)."""
    pairs = []
    for (i, mi), (j, mj) in combinations(enumerate(rep_keys_maps), 2):
        dec_i = {sf for sf, v in mi.items() if v["decoded"]}
        dec_j = {sf for sf, v in mj.items() if v["decoded"]}
        inter = sorted(dec_i & dec_j)
        rows = [
            {
                "sf": sf,
                "stratum": ref_by_sf[sf]["stratum"],
                "w": ref_by_sf[sf]["w"],
                "agree": int(mi[sf]["teacher_key"] == mj[sf]["teacher_key"]),
                "both_dated": mi[sf]["teacher_key"] != "UNDATED"
                and mj[sf]["teacher_key"] != "UNDATED",
            }
            for sf in inter
        ]
        u, w, _by = agg(rows, lambda r: r["agree"], strat_weight) if rows else (None, None, {})
        dated_rows = [r for r in rows if r["both_dated"]]
        _ud, wd, _ = agg(dated_rows, lambda r: r["agree"], strat_weight) if dated_rows else (
            None,
            None,
            {},
        )
        pairs.append(
            {
                "reps": [i + 1, j + 1],
                "n_intersection": len(inter),
                "agreement_unweighted": u,
                "agreement_inv_weighted": w,
                "dated_only_inv_weighted": wd,
                "n_dated": len(dated_rows),
                "missing_in_rep_b": sorted(dec_i - dec_j),
                "missing_in_rep_a": sorted(dec_j - dec_i),
            }
        )
    spread_vals = [p["agreement_inv_weighted"] for p in pairs if p["agreement_inv_weighted"] is not None]
    spread = (
        {
            "min": round(min(spread_vals), 4),
            "max": round(max(spread_vals), 4),
            "mean": round(sum(spread_vals) / len(spread_vals), 4),
        }
        if spread_vals
        else {"min": None, "max": None, "mean": None}
    )
    return {"pairs": pairs, "spread": spread}


# --------------------------------------------------------------------------- #
# gate 1: student self-reproducibility
# --------------------------------------------------------------------------- #
def self_repro_rep(
    rep_dir: Path,
    ref_rows: list[dict],
    delivery_by_sf: dict[int, dict],
    *,
    est,
    config,
    vexcel_ceiling: dict,
    scorer,
    skip_missing: bool,
    resolver: StudentChipResolver | None = None,
) -> tuple[bool, list[dict], int]:
    """Run the student pass twice (cache OFF both passes -> real re-scoring) and
    byte-compare the decoded student keys. Returns ``(ok, mismatches, n_decoded)``
    where ``n_decoded`` is the count of decoded anchors compared (denominator for
    the reported self-agreement rate)."""
    def _pass() -> dict[int, str]:
        km = rep_keys(
            rep_dir,
            ref_rows,
            delivery_by_sf,
            est=est,
            config=config,
            vexcel_ceiling=vexcel_ceiling,
            teacher_only=False,
            scorer=scorer,
            cache=None,
            scorer_key="self_repro",
            skip_missing=skip_missing,
            stats=new_stats(),
            resolver=resolver,
        )
        return {sf: v["student_key"] for sf, v in km.items() if v["decoded"]}

    a = _pass()
    b = _pass()
    mismatches = [
        {"sf": sf, "pass_a": a[sf], "pass_b": b.get(sf)} for sf in a if a[sf] != b.get(sf)
    ]
    return (len(mismatches) == 0, mismatches, len(a))


# --------------------------------------------------------------------------- #
# leakage drop + guards + provenance
# --------------------------------------------------------------------------- #
def drop_overlapping_anchors(ref_rows: list[dict], anchors_json: Path) -> tuple[list[dict], dict]:
    """Drop reference rows whose group/target anchor overlaps the head's training
    subset (computed dynamically by intersection, NOT a hardcoded count)."""
    overlap = set(json.loads(Path(anchors_json).read_text()))
    kept, dropped = [], []
    for r in ref_rows:
        if r["group_anchor_id"] in overlap or r["target_anchor_id"] in overlap:
            dropped.append(r)
        else:
            kept.append(r)
    # Only the anchor that actually hit ``overlap`` is a training-overlap anchor.
    # A row dropped solely because its *target* anchor overlaps still carries a
    # NON-overlapping group anchor (and vice-versa); those must not be counted as
    # overlapping anchors (R2#3). Filter each side against ``overlap`` directly.
    overlap_group = sorted({r["group_anchor_id"] for r in dropped if r["group_anchor_id"] in overlap})
    overlap_target = sorted({r["target_anchor_id"] for r in dropped if r["target_anchor_id"] in overlap})
    record = {
        "anchors_json": str(anchors_json),
        "n_overlap_anchor_ids": len(overlap),
        "n_dropped_rows": len(dropped),
        "dropped_source_feature_ids": sorted(r["sf"] for r in dropped),
        "dropped_group_anchor_ids": overlap_group,
        "dropped_target_anchor_ids": overlap_target,
        "n_dropped_group_anchors": len(overlap_group),
        "n_dropped_target_anchors": len(overlap_target),
    }
    return kept, record


def guard_out_dir(out: Path) -> Path:
    """Refuse to write inside the read-only ISSUE-21-owned bank."""
    resolved = Path(out).resolve()
    if _STOREBACKED_BANK in resolved.parts or _STOREBACKED_BANK in str(resolved):
        raise SystemExit(
            f"REFUSING to write into the read-only bank {_STOREBACKED_BANK!r}: {resolved}"
        )
    return resolved


def _sha256_file(path: Path | None) -> str | None:
    if path is None or not Path(path).exists():
        return None
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception as exc:  # noqa: BLE001
        return f"unknown:{type(exc).__name__}"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--reference", type=Path, required=True, help="642-row reference.csv")
    ap.add_argument(
        "--reps", type=Path, nargs="+", required=True, help="banked rep dirs (rep1 rep2 rep3 ...)"
    )
    ap.add_argument(
        "--scorer",
        choices=["dinov3_frozen", "dinov2_floor"],
        default="dinov3_frozen",
        help="student backbone (ignored under --teacher-only)",
    )
    ap.add_argument("--head", type=Path, default=None, help="student head bundle .pt")
    ap.add_argument("--device", default="cuda", help="torch device for the student scorer")
    ap.add_argument("--estimator", default=ADOPTED_ESTIMATOR)
    ap.add_argument("--cohort-prior", type=Path, default=DEFAULT_COHORT_PRIOR_JSON)
    ap.add_argument("--no-cohort-prior", action="store_true")
    ap.add_argument("--emissions", type=Path, default=DEFAULT_EMISSIONS_JSON)
    ap.add_argument("--no-emissions", action="store_true")
    ap.add_argument("--decoder-epoch-gap-days", type=int, default=ADOPTED_DECODER_EPOCH_GAP_DAYS)
    ap.add_argument("--vexcel-capture-csv", type=Path, default=DEFAULT_VEXCEL_CSV)
    ap.add_argument("--no-clamp", action="store_true")
    ap.add_argument("--drop-anchors-overlapping", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None, help="smoke: first N group anchors only")
    ap.add_argument(
        "--teacher-only", action="store_true", help="ceiling only (no GPU/scorer needed)"
    )
    ap.add_argument(
        "--self-repro",
        action="store_true",
        help="run the student pass twice, byte-compare decoded intervals; nonzero exit on mismatch",
    )
    ap.add_argument(
        "--skip-missing-chips",
        action="store_true",
        help="escape hatch: abstain on a missing chip instead of failing loudly (counts always reported)",
    )
    _default_chip_targets = (
        Path.home()
        / "zasolar_data"
        / "geid_temporal"
        / "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
        / "chip_targets.csv"
    )
    ap.add_argument(
        "--chip-targets",
        type=Path,
        default=_default_chip_targets,
        help="chip_targets.csv for tight12 nomarker re-render (LOCKED student input contract)",
    )
    ap.add_argument(
        "--geometry-version",
        default="chip_geom_v2_tight12",
        help="named chip geometry for the student re-render (default: chip_geom_v2_tight12)",
    )
    ap.add_argument(
        "--no-student-rerender",
        action="store_true",
        help="EMERGENCY: score banked chip_path as-is (OOD). Not a clean gate-2 number.",
    )
    ap.add_argument(
        "--render-drop-stop-pct",
        type=float,
        default=2.0,
        help="pollution stop-rule: abort if re-render drops exceed this %% of frames (default 2.0)",
    )
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    out_dir = guard_out_dir(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if a.estimator not in {"changepoint"}:
        # non-changepoint baselines ignore prior/emissions (mirror decode script).
        a.cohort_prior = None
        a.emissions = None
    if a.no_cohort_prior:
        a.cohort_prior = None
    if a.no_emissions:
        a.emissions = None

    ref_rows = load_reference_rows(a.reference)
    leakage = None
    if a.drop_anchors_overlapping:
        ref_rows, leakage = drop_overlapping_anchors(ref_rows, a.drop_anchors_overlapping)
    else:
        # Omitting the drop silently contaminates BOTH the student number AND the
        # ceiling with head-training-overlapping anchors — fail loud, not silent
        # (R1#2). Recorded in provenance + summary as well.
        print(
            "WARNING: --drop-anchors-overlapping NOT passed — head-training-overlapping "
            "anchors are INCLUDED in BOTH the student-vs-teacher number AND the rep-vs-rep "
            "ceiling; the gate-2 number may be LEAKAGE-INFLATED. Pass "
            "--drop-anchors-overlapping <chip_subset_anchors.json> for a clean run.",
            file=sys.stderr,
        )
    if a.limit:
        seen: list[str] = []
        for r in ref_rows:
            if r["group_anchor_id"] not in seen:
                seen.append(r["group_anchor_id"])
        keep = set(seen[: a.limit])
        ref_rows = [r for r in ref_rows if r["group_anchor_id"] in keep]

    ref_by_sf = {r["sf"]: r for r in ref_rows}
    strat_w = stratum_weights(ref_rows)

    vexcel_ceiling = {} if a.no_clamp else _load_vexcel_ceiling(a.vexcel_capture_csv)
    emissions = _load_emissions(a.emissions) if a.emissions else None
    cohort_prior = None
    if a.cohort_prior:
        from solar_backdating.estimators.survival import cohort_prior_from_json  # noqa: PLC0415

        cohort_prior = cohort_prior_from_json(json.loads(Path(a.cohort_prior).read_text()))
    est, config = build_decoder(
        estimator=a.estimator,
        emissions=emissions,
        cohort_prior=cohort_prior,
        decoder_epoch_gap_days=a.decoder_epoch_gap_days,
    )

    # Student chip resolver (LOCKED tight12 nomarker re-render, unless escaped).
    if a.teacher_only or a.no_student_rerender:
        resolver = StudentChipResolver()  # identity
        if a.no_student_rerender and not a.teacher_only:
            print(
                "WARNING: --no-student-rerender active — scoring banked geometry-v1 "
                "pixels; gate-2 is NOT a clean fidelity number.",
                file=sys.stderr,
            )
    else:
        if not Path(a.chip_targets).exists():
            raise SystemExit(
                f"chip_targets.csv required for student re-render (LOCKED contract): "
                f"{a.chip_targets}"
            )
        resolver = StudentChipResolver(
            chip_targets_path=Path(a.chip_targets),
            geometry_version=a.geometry_version,
            contain_footprint=True,
        )

    head_sha = _sha256_file(a.head)
    scorer = None
    scorer_key = "teacher_only"
    if not a.teacher_only:
        from scripts.temporal.presence_scorer import get_scorer  # noqa: PLC0415

        kwargs = {"device": a.device}
        if a.head is not None:
            kwargs["head_checkpoint"] = str(a.head)
        scorer = get_scorer(a.scorer, **kwargs)
        scorer_key = f"{a.scorer}|{head_sha}"

    # ---- teacher ceiling (full banked frame sets; LOCKED) ----
    ceiling_stats = new_stats()
    ceiling_maps = []
    for rep_dir in a.reps:
        delivery = load_delivery_by_sf(rep_dir / "delivery.csv")
        ceiling_maps.append(
            rep_keys(
                rep_dir,
                ref_rows,
                delivery,
                est=est,
                config=config,
                vexcel_ceiling=vexcel_ceiling,
                teacher_only=True,
                scorer=None,
                cache=None,
                scorer_key="teacher_only",
                skip_missing=True,
                stats=ceiling_stats,
                resolver=StudentChipResolver(),  # unused under teacher_only
            )
        )
    ceiling = pairwise_ceiling(ceiling_maps, ref_by_sf, strat_w)

    # ---- student-vs-teacher (render-drop applied to both pipelines) ----
    cache: dict = {}
    stats = new_stats()
    drop_log: list[dict] = []
    svt_maps = []
    if not a.teacher_only:
        for rep_dir in a.reps:
            delivery = load_delivery_by_sf(rep_dir / "delivery.csv")
            svt_maps.append(
                rep_keys(
                    rep_dir,
                    ref_rows,
                    delivery,
                    est=est,
                    config=config,
                    vexcel_ceiling=vexcel_ceiling,
                    teacher_only=False,
                    scorer=scorer,
                    cache=cache,
                    scorer_key=scorer_key,
                    skip_missing=a.skip_missing_chips,
                    stats=stats,
                    resolver=resolver,
                    drop_log=drop_log,
                    apply_render_drop_to_teacher=resolver.active,
                )
            )

    # Pollution stop-rule (LOCKED): abort if re-render drops exceed threshold.
    n_render_ok = int(stats.get("render_ok", 0))
    n_render_drop = int(stats.get("render_drop", 0))
    n_render_total = n_render_ok + n_render_drop
    drop_pct = (100.0 * n_render_drop / n_render_total) if n_render_total else 0.0
    render_drop_report = {
        "n_frames_attempted": n_render_total,
        "n_render_ok": n_render_ok,
        "n_render_drop": n_render_drop,
        "drop_pct": round(drop_pct, 4),
        "stop_pct": a.render_drop_stop_pct,
        "n_anchors_with_drop": len(drop_log),
        "drop_reasons": {
            k.split("render_drop:", 1)[-1]: int(v)
            for k, v in stats.items()
            if str(k).startswith("render_drop:")
        },
        "anchors_with_drop_sample": drop_log[:50],
    }
    if (
        resolver.active
        and not a.teacher_only
        and n_render_total > 0
        and drop_pct > a.render_drop_stop_pct
    ):
        (out_dir / "render_drop_report.json").write_text(
            json.dumps(render_drop_report, indent=2)
        )
        raise SystemExit(
            f"POLLUTION STOP-RULE: re-render drops {drop_pct:.3f}% "
            f"({n_render_drop}/{n_render_total} frames) exceed "
            f"--render-drop-stop-pct={a.render_drop_stop_pct}. "
            f"Refusing to ship gate-2 numbers. See {out_dir}/render_drop_report.json"
        )

    # ---- gate 2 ----
    svt_per_rep = []
    pooled_rows: list[dict] = []
    if not a.teacher_only:
        for idx, km in enumerate(svt_maps):
            rows = student_teacher_rows(km, ref_by_sf)
            pooled_rows.extend(rows)
            svt_per_rep.append({"rep": idx + 1, **student_vs_teacher(rows, strat_w)})
    gate2 = {
        "scorer": a.scorer if not a.teacher_only else None,
        "n_reps": len(a.reps),
        "teacher_rep_vs_rep_ceiling": ceiling,
        "student_vs_teacher_per_rep": svt_per_rep,
        "student_vs_teacher_pooled": student_vs_teacher(pooled_rows, strat_w)
        if pooled_rows
        else None,
        "render_drop": render_drop_report,
        "known_quirk_note": (
            "L1 scan-state counts differ per rep (230/236/239 of 244 groups); "
            "pairwise ceiling uses the decoded intersection, per-pair denominators "
            "+ missing sfs reported. Teacher ceiling uses full banked frame sets; "
            "student-vs-teacher drops re-render failures from both pipelines."
        ),
    }
    (out_dir / "gate2_agreement.json").write_text(json.dumps(gate2, indent=2))
    (out_dir / "render_drop_report.json").write_text(
        json.dumps(render_drop_report, indent=2)
    )

    # ---- gate 1 (self-repro) ----
    gate1 = None
    self_repro_ok = True
    if a.self_repro and not a.teacher_only:
        rep0 = a.reps[0]
        delivery0 = load_delivery_by_sf(rep0 / "delivery.csv")
        ok, mism, n_decoded = self_repro_rep(
            rep0,
            ref_rows,
            delivery0,
            est=est,
            config=config,
            vexcel_ceiling=vexcel_ceiling,
            scorer=scorer,
            skip_missing=a.skip_missing_chips,
            resolver=resolver,
        )
        self_repro_ok = ok
        # Report the ACTUAL fraction, not 1.0-or-None, so a near-miss keeps its
        # magnitude (R2#5). 1.0 when nothing decoded (vacuously reproducible).
        self_agreement = 1.0 if n_decoded == 0 else round(1 - len(mism) / n_decoded, 4)
        gate1 = {
            "rep": str(rep0),
            "scorer": a.scorer,
            "self_agreement": self_agreement,
            "n_decoded": n_decoded,
            "n_mismatches": len(mism),
            "mismatches": mism[:20],
            "pass": ok,
        }
        (out_dir / "gate1_self_repro.json").write_text(json.dumps(gate1, indent=2))

    # ---- provenance manifest ----
    def _jsonable(v):
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, (list, tuple)):
            return [_jsonable(x) for x in v]
        return v

    if resolver.active:
        chip_geometry_note = (
            f"student chips re-rendered at {a.geometry_version} draw_marker=False "
            f"(nomarker) from banked GeoTIFF sources via ensure_single_target_review_png; "
            f"chip_targets={a.chip_targets}; render drops applied to both pipelines "
            f"({n_render_drop}/{n_render_total} frames = {drop_pct:.3f}%). "
            "Teacher ceiling uses full banked frame sets."
        )
    else:
        chip_geometry_note = (
            "NO student re-render (identity path / --no-student-rerender / "
            "--teacher-only). Banked chip_path files are chip_geom_v1_banked96; "
            "student heads were trained on chip_geom_v2_tight12 nomarker — any "
            "student-vs-teacher number under this path is OOD and not a clean gate."
        )

    provenance = {
        "git_rev": _git_rev(),
        "args": {k: _jsonable(v) for k, v in vars(a).items()},
        "reference": str(a.reference),
        "reps": [str(r) for r in a.reps],
        "scorer": a.scorer,
        "head": str(a.head) if a.head else None,
        "head_sha256": head_sha,
        "estimator": a.estimator,
        "decoder_epoch_gap_days": a.decoder_epoch_gap_days,
        "cohort_prior": str(a.cohort_prior) if a.cohort_prior else None,
        "cohort_prior_sha256": _sha256_file(a.cohort_prior),
        "emissions": str(a.emissions) if a.emissions else None,
        "emissions_sha256": _sha256_file(a.emissions),
        "clamp_used": bool(vexcel_ceiling),
        "n_reference_rows_after_filters": len(ref_rows),
        "leakage_drop_applied": leakage is not None,
        "leakage_drop": leakage,
        "walk_stats": dict(stats),
        "ceiling_walk_stats": dict(ceiling_stats),
        "student_rerender_active": resolver.active,
        "geometry_version": a.geometry_version if resolver.active else None,
        "chip_targets": str(a.chip_targets) if resolver.active else None,
        "render_drop": render_drop_report,
        "chip_geometry_note": chip_geometry_note,
        # keep the old key name so prior consumers still see a string
        "chip_geometry_warning": chip_geometry_note
        if not resolver.active
        else None,
    }
    (out_dir / "provenance.json").write_text(json.dumps(provenance, indent=2))

    # ---- human summary ----
    _write_summary(out_dir, gate1, gate2, provenance, teacher_only=a.teacher_only)

    print(f"wrote {out_dir}/gate2_agreement.json + provenance.json + summary.md")
    if gate1 is not None:
        print(f"gate1 self-repro pass={self_repro_ok} (mismatches={gate1['n_mismatches']})")
    print(f"ceiling spread: {ceiling['spread']}")
    print(
        f"render drops: {n_render_drop}/{n_render_total} ({drop_pct:.3f}%) "
        f"rerender_active={resolver.active}"
    )
    # gate 1 is the only hard exit signal (reproducibility must hold by construction).
    return 0 if self_repro_ok else 3


def _write_summary(out_dir: Path, gate1, gate2, provenance, *, teacher_only: bool) -> None:
    L = ["# ISSUE-06 fidelity gate — machine summary", ""]
    L.append(f"- git_rev: `{provenance['git_rev']}`")
    L.append(f"- scorer: `{provenance['scorer']}`  head_sha256: `{provenance['head_sha256']}`")
    L.append(f"- estimator: `{provenance['estimator']}` (gap={provenance['decoder_epoch_gap_days']})")
    L.append(f"- reference rows after filters: {provenance['n_reference_rows_after_filters']}")
    if provenance["leakage_drop"]:
        ld = provenance["leakage_drop"]
        L.append(
            f"- leakage drop: {ld['n_dropped_rows']} rows / "
            f"{ld['n_dropped_group_anchors']} group / {ld['n_dropped_target_anchors']} target anchors"
        )
    else:
        L.append(
            "- **LEAKAGE DROP NOT APPLIED** (`--drop-anchors-overlapping` omitted): "
            "head-training-overlapping anchors are INCLUDED in both the student number "
            "and the ceiling — this gate-2 number may be leakage-inflated. Re-run with "
            "the flag for a clean fidelity number."
        )
    L += ["", "## Gate 1 — student self-reproducibility"]
    if gate1 is None:
        L.append("- not run (use --self-repro; skipped under --teacher-only)")
    else:
        L.append(f"- pass: **{gate1['pass']}**  self-agreement: {gate1['self_agreement']}  "
                 f"mismatches: {gate1['n_mismatches']}")
    L += ["", "## Gate 2 — interval agreement"]
    ceil = gate2["teacher_rep_vs_rep_ceiling"]
    L.append(f"- teacher rep-vs-rep ceiling spread (inv-weighted): {ceil['spread']}")
    for p in ceil["pairs"]:
        L.append(
            f"  - reps {p['reps']}: agree={p['agreement_inv_weighted']} "
            f"(n={p['n_intersection']}, dated-only={p['dated_only_inv_weighted']})"
        )
    if not teacher_only:
        pooled = gate2["student_vs_teacher_pooled"]
        if pooled:
            L.append(
                f"- student-vs-teacher pooled (decoded-only, inv-weighted): "
                f"{pooled['decoded_only']['agreement_inv_weighted']} "
                f"(dated-only={pooled['dated_only_denominator']['agreement_inv_weighted']}, "
                f"n_decoded={pooled['n_decoded']})"
            )
            L.append(f"- R3 unusable split: {pooled['r3_unusable_split']}")
        rd = gate2.get("render_drop") or {}
        if rd:
            L.append(
                f"- render drops: {rd.get('n_render_drop')}/{rd.get('n_frames_attempted')} "
                f"({rd.get('drop_pct')}%), anchors_with_drop={rd.get('n_anchors_with_drop')}"
            )
    L += [
        "",
        "## Chip geometry (student input contract)",
        "",
        provenance.get("chip_geometry_note")
        or provenance.get("chip_geometry_warning")
        or "(none)",
        "",
    ]
    L += [
        "## Gate 3 — held-out chip-level agreement (lifted, not recomputed)",
        "",
        "Lift the ISSUE-04/05 report-half numbers into the verdict doc "
        "(0.797 @ 0.885 cov DINOv3 / 0.7968 @ 0.8776 cov DINOv2, n=858). "
        "This harness does not recompute them.",
        "",
    ]
    (out_dir / "summary.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
