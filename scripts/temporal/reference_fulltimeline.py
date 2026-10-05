"""Pure rules for the 2019-window full-timeline reference pilot.

This module plans grids, dates, reference roles, model-response contracts,
install intervals, and an offline replay of the existing adaptive stopper.
It does not download imagery, call Gemini, or repair labels. The runner and
the evaluator import these functions; tests call them directly.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_decision import (
    TerminateAction,
    VintageEntry,
    decide_next_action,
)
from scripts.temporal.scan_state import (
    RoundResult,
    create_scan_state,
)


WINDOW_START = "2019-01-01"
MODEL_ALIAS = "gemini-3.1-flash-lite"
DEFAULT_SEED = 20261004
PAIRED_PER_GRID = 20
REVIEW_PER_GRID = 10
TECHCHECK_ANCHORS = 12
TECHCHECK_DATES = 3
MAX_BOUNDARY_RECHECKS = 2
MAX_CONFLICT_RECHECKS = 4
MAX_SATELLITE_REFERENCES = 3
POLICY_CENSUS_DATE = "2025-01-31"
POLICY_CENSUS_PRECISION = "policy_census_bound"
# Near-white CPT2935 chips. They stay in the inventory and are not present or absent.
PIXEL_UNINFORMATIVE_DATES = frozenset(
    {
        ("ct_full_inventory_2026_06_21_merged_t00055288", "2022-12-30"),
        ("ct_full_inventory_2026_06_21_merged_t00055299", "2022-12-30"),
        ("ct_full_inventory_2026_06_21_merged_t00055301", "2022-12-30"),
        ("ct_full_inventory_2026_06_21_merged_t00055303", "2022-12-30"),
    }
)
# One recorded row is enough. These are not successes and are not retried.
TERMINAL_NONCALL = frozenset(
    {
        "pixel_uninformative",
        "not_sent",
        "no_post_census_reference",
        "image_missing",
    }
)

PROMPT = """Assess ONE candidate image for rooftop solar PV at the marked target.
Image 1 is the candidate. Image 2 is an aerial appearance reference.
Image 3, when supplied, is a satellite appearance reference.
References help identify the same roof segment and array. They do not prove
that PV existed in the candidate. Judge Image 1 from its own visible evidence.

Ignore PV on neighbouring roofs or unrelated roof segments. An older, smaller
part of the target array may count as present; the full modern array need not
already exist. Flag roof rebuilding, relocation or uncertain target identity.

Return present when candidate evidence supports PV at this target. Describe
the visible structure and distinguish it from roof texture, skylights,
water heaters, vents and shadows. Small arrays are not automatically unknown.
Return absent only when the relevant roof segment is visible and sufficiently
clear to rule out PV of the target scale. Blur, shadow, obstruction, incomplete
coverage or uncertain roof matching must be unknown, never absent.
Do not infer an installation date or force an absent-then-present pattern.
Return one JSON object for the candidate only, using the supplied schema.
"""

_JUDGMENT_RULES = """References help identify the same roof segment and array. They do not prove
that PV existed in the candidate. Judge Image 1 from its own visible evidence.

Ignore PV on neighbouring roofs or unrelated roof segments. An older, smaller
part of the target array may count as present; the full modern array need not
already exist. Flag roof rebuilding, relocation or uncertain target identity.

Return present when candidate evidence supports PV at this target. Describe
the visible structure and distinguish it from roof texture, skylights,
water heaters, vents and shadows. Small arrays are not automatically unknown.
Return absent only when the relevant roof segment is visible and sufficiently
clear to rule out PV of the target scale. Blur, shadow, obstruction, incomplete
coverage or uncertain roof matching must be unknown, never absent.
Do not infer an installation date or force an absent-then-present pattern.
Return one JSON object for the candidate only, using the supplied schema.
"""

PROMPT_T2 = (
    "Assess ONE candidate image for rooftop solar PV at the marked target.\n"
    "This request has exactly two images.\n"
    "Image 1 is the historical candidate. Image 2 is the census aerial reference.\n"
    "There is no satellite reference image.\n"
    + _JUDGMENT_RULES
)

PROMPT_T3 = (
    "Assess ONE candidate image for rooftop solar PV at the marked target.\n"
    "This request has exactly three images.\n"
    "Image 1 is the historical candidate. Image 2 is the census aerial reference.\n"
    "Image 3 is one post-census satellite reference. It is not a historical\n"
    "candidate and it is not evidence that PV was present on the candidate date.\n"
    + _JUDGMENT_RULES
)


def prompt_for_arm(arm: str) -> str:
    """Return the prompt whose image count matches the arm."""

    if arm == "t2":
        return PROMPT_T2
    if arm == "t3":
        return PROMPT_T3
    raise ValueError(f"unknown arm {arm}")


def is_pixel_uninformative(anchor_id: str, capture_date: str) -> bool:
    return (str(anchor_id), str(capture_date)[:10]) in PIXEL_UNINFORMATIVE_DATES

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "candidate_index": {"type": "INTEGER", "enum": ["1"]},
        "pv_present": {"type": "BOOLEAN", "nullable": True},
        "quality_flag": {
            "type": "STRING",
            "enum": ["usable", "ambiguous", "unusable"],
        },
        "target_match": {"type": "BOOLEAN", "nullable": True},
        "confidence": {"type": "NUMBER", "nullable": True},
        "evidence": {"type": "STRING"},
        "unknown_reason": {"type": "STRING", "nullable": True},
        "object_change_flag": {"type": "BOOLEAN", "nullable": True},
    },
    "required": [
        "candidate_index",
        "pv_present",
        "quality_flag",
        "target_match",
        "evidence",
    ],
}

ALLOWED_RESPONSE_KEYS = frozenset(RESPONSE_SCHEMA["properties"])
REFERENCE_CHECK_PROMPT = """Assess ONE reference image of a rooftop solar target.
The image is an appearance reference, not a historical candidate, and it is not
evidence that PV existed on any other date. Say whether the marked roof segment
is the inventory target and whether PV is visible there. Blur, shadow,
obstruction, or an uncertain roof match is unknown, never absent. Do not infer
an installation date. Return one JSON object with candidate_index 1.
"""

GRID_FIELDS = [
    "grid_id",
    "stratum",
    "role",
    "risk_grid",
    "n_anchors",
    "median_in_window_dates",
    "n_a48",
    "n_geometry_exception",
    "density_band",
    "coverage_band",
    "selection_reason",
]

ANCHOR_FIELDS = [
    "anchor_id",
    "grid_id",
    "source_grid",
    "source_grids",
    "region_key",
    "centroid_lon",
    "centroid_lat",
    "area_m2",
    "area_band",
    "chip_arm",
    "review_extent_m",
    "geometry_exception",
    "geometry_version",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
    "source_width_m",
    "source_height_m",
    "source_box_width_m",
    "source_box_height_m",
    "center_offset_m",
    "grid_role",
    "grid_stratum",
    "risk_grid",
    "paired",
    "paired_inclusion_probability",
    "human_review",
    "review_inclusion_probability",
    "census_date",
    "census_date_precision",
    "n_window_dates",
    "n_callable_dates",
    "history_reason",
]

CANDIDATE_FIELDS = [
    "anchor_id",
    "capture_date",
    "version",
    "provider",
    "requested_zoom",
    "bbox_complete",
    "callable",
    "terminal_state",
    "download_choice_reason",
    "n_source_rows",
    "providers",
    "census_date",
]

REFERENCE_FIELDS = [
    "anchor_id",
    "kind",
    "rank",
    "capture_date",
    "version",
    "provider",
    "requested_zoom",
    "source",
    "bbox_complete",
]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_fingerprint(payload: Mapping[str, Any]) -> str:
    """Hash the call contract. The payload must not carry a date or a prior label."""

    return sha256_text(json.dumps(dict(payload), sort_keys=True, separators=(",", ":")))


def prompt_fingerprint(prompt: str = PROMPT) -> str:
    payload = {
        "prompt": prompt,
        "schema": RESPONSE_SCHEMA,
        "reference_check_prompt": REFERENCE_CHECK_PROMPT,
    }
    return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def parse_iso(value: str) -> date:
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def in_analysis_window(capture_date: str, census_date: str) -> bool:
    """True when the capture is inside [2019-01-01, census date).

    The census date itself is a reference bound, not a historical candidate.
    Dates before 2019 are outside this pilot even if a catalog still lists them.
    """

    day = str(capture_date)[:10]
    census = str(census_date)[:10]
    return WINDOW_START <= day < census


def area_band(area: object) -> str:
    if area is None or str(area).strip() == "":
        return "unknown"
    value = float(area)
    if value < 20:
        return "<20"
    if value < 100:
        return "20-100"
    return ">=100"


def _median(values: Sequence[float]) -> float:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise ValueError("median of an empty sample")
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _as_int(value: object) -> int:
    if value is None or str(value).strip() == "":
        return 0
    return int(float(value))


def _as_float(value: object) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    return float(value)


def grid_out_of_scope_reason(row: Mapping[str, Any]) -> str:
    """Why a centroid grid cannot enter the 10-grid draw.

    Aerial location needs coordinates. GEHI coverage needs at least one
    in-window catalog date somewhere in the grid. Both failures are reported;
    the grid is not silently dropped from the denominator summary.
    """

    reasons: list[str] = []
    n_anchors = _as_int(row.get("n_anchors"))
    if n_anchors <= 0:
        return "empty_grid"
    if _as_int(row.get("n_missing_xy")) >= n_anchors:
        reasons.append("no_aerial_location")
    if _as_int(row.get("n_no_history")) >= n_anchors:
        reasons.append("no_gehi_catalog")
    return "|".join(reasons)


def is_risk_grid(row: Mapping[str, Any]) -> bool:
    """Metadata risk, decided before any new model label exists.

    A geometry exception is a localization risk. A grid whose anchors are at
    least half A48 is the large-footprint risk supplement. Clarity is not in
    the inventory tables, so it is not invented here.
    """

    if _as_int(row.get("n_geometry_exception")) > 0:
        return True
    n_anchors = _as_int(row.get("n_anchors"))
    if n_anchors <= 0:
        return False
    return _as_int(row.get("n_a48")) / n_anchors >= 0.5


def _stratum_name(density_high: bool, coverage_high: bool) -> str:
    return (
        f"density_{'high' if density_high else 'low'}"
        f"__coverage_{'high' if coverage_high else 'low'}"
    )


def select_grids(
    grids: Sequence[Mapping[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
    general_per_stratum: int = 2,
    n_development_strata: int = 2,
    n_risk: int = 2,
) -> dict[str, Any]:
    """Freeze about 10 centroid grids without reading model labels.

    Random draws use one Random(seed) stream. The function only reads inventory
    counts, so a column of new verdicts cannot change the sample.
    """

    out_of_scope: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for raw in grids:
        row = dict(raw)
        row["grid_id"] = str(row["grid_id"])
        reason = grid_out_of_scope_reason(row)
        if reason:
            out_of_scope.append({**row, "out_of_scope_reason": reason})
            continue
        row["risk_grid"] = is_risk_grid(row)
        eligible.append(row)

    general = [row for row in eligible if not row["risk_grid"]]
    risk_pool = [row for row in eligible if row["risk_grid"]]
    if not general:
        raise ValueError("no eligible non-risk grids")

    density_cut = _median([_as_int(row["n_anchors"]) for row in general])
    coverage_cut = _median(
        [_as_float(row["median_in_window_dates"]) for row in general]
    )
    strata: dict[str, list[dict[str, Any]]] = {}
    for row in general:
        density_high = _as_int(row["n_anchors"]) >= density_cut
        coverage_high = _as_float(row["median_in_window_dates"]) >= coverage_cut
        name = _stratum_name(density_high, coverage_high)
        row["density_band"] = "high" if density_high else "low"
        row["coverage_band"] = "high" if coverage_high else "low"
        row["stratum"] = name
        strata.setdefault(name, []).append(row)
    for rows in strata.values():
        rows.sort(key=lambda item: item["grid_id"])

    rng = random.Random(seed)
    stratum_names = sorted(strata)
    n_dev = min(n_development_strata, len(stratum_names))
    dev_strata = rng.sample(stratum_names, n_dev) if n_dev else []

    selected_by_stratum: dict[str, list[dict[str, Any]]] = {}
    substitutes: dict[str, list[str]] = {}
    for name in stratum_names:
        pool = strata[name]
        take = min(general_per_stratum, len(pool))
        chosen_ids = set(rng.sample([row["grid_id"] for row in pool], take))
        chosen = [row for row in pool if row["grid_id"] in chosen_ids]
        chosen.sort(key=lambda item: item["grid_id"])
        selected_by_stratum[name] = chosen
        substitutes[name] = [
            row["grid_id"] for row in pool if row["grid_id"] not in chosen_ids
        ]

    development_ids: list[str] = []
    for name in dev_strata:
        chosen = selected_by_stratum.get(name) or []
        if not chosen:
            continue
        development_ids.append(rng.choice(chosen)["grid_id"])
    development_set = set(development_ids)

    selected_general = [
        row
        for name in stratum_names
        for row in selected_by_stratum.get(name, [])
    ]
    selected_ids = {row["grid_id"] for row in selected_general}
    risk_sorted = sorted(risk_pool, key=lambda item: item["grid_id"])
    risk_take = min(n_risk, len(risk_sorted))
    risk_ids = (
        rng.sample([row["grid_id"] for row in risk_sorted], risk_take)
        if risk_take
        else []
    )
    risk_reason = "metadata_risk"
    if len(risk_ids) < n_risk:
        remaining = [
            row["grid_id"]
            for row in sorted(general, key=lambda item: item["grid_id"])
            if row["grid_id"] not in selected_ids and row["grid_id"] not in risk_ids
        ]
        need = n_risk - len(risk_ids)
        extra = rng.sample(remaining, min(need, len(remaining))) if remaining else []
        risk_ids.extend(extra)
        risk_reason = "no_risk_grid_random_fill" if risk_take == 0 else "risk_pool_shortfall_fill"

    by_id = {row["grid_id"]: row for row in eligible}
    frozen: list[dict[str, Any]] = []
    for row in selected_general:
        role = "development" if row["grid_id"] in development_set else "evaluation"
        frozen.append(
            {
                "grid_id": row["grid_id"],
                "stratum": row["stratum"],
                "role": role,
                "risk_grid": 0,
                "n_anchors": _as_int(row["n_anchors"]),
                "median_in_window_dates": _as_float(row["median_in_window_dates"]),
                "n_a48": _as_int(row.get("n_a48")),
                "n_geometry_exception": _as_int(row.get("n_geometry_exception")),
                "density_band": row["density_band"],
                "coverage_band": row["coverage_band"],
                "selection_reason": "stratum_sample",
            }
        )
    for grid_id in risk_ids:
        row = by_id[grid_id]
        frozen.append(
            {
                "grid_id": grid_id,
                "stratum": row.get("stratum") or "risk_supplement",
                "role": "evaluation",
                "risk_grid": 1 if row.get("risk_grid") else 0,
                "n_anchors": _as_int(row["n_anchors"]),
                "median_in_window_dates": _as_float(row["median_in_window_dates"]),
                "n_a48": _as_int(row.get("n_a48")),
                "n_geometry_exception": _as_int(row.get("n_geometry_exception")),
                "density_band": row.get("density_band") or "",
                "coverage_band": row.get("coverage_band") or "",
                "selection_reason": risk_reason,
            }
        )
    frozen.sort(key=lambda item: (item["role"] != "development", item["grid_id"]))
    return {
        "grids": frozen,
        "out_of_scope": out_of_scope,
        "thresholds": {
            "density_median_anchors": density_cut,
            "coverage_median_dates": coverage_cut,
            "risk_rule": "geometry_exception>0 or a48_share>=0.5",
        },
        "development_strata": list(dev_strata),
        "substitutes": substitutes,
        "seed": seed,
        "rng_order": [
            "sample_development_strata",
            "sample_two_grids_per_stratum_in_name_order",
            "choose_one_development_grid_per_sampled_stratum",
            "sample_risk_supplement",
            "sample_paired_and_review_in_grid_id_order",
        ],
    }


def allocate_band_quotas(sizes: Mapping[str, int], target: int) -> dict[str, int]:
    """Draw quotas that keep every populated area band when the grid has it.

    A band smaller than its equal share is scarce and is taken entirely. The
    leftover slots are split across the remaining bands. This does not drop
    the small-array band to make the sample look cleaner.
    """

    bands = sorted(band for band, size in sizes.items() if size > 0)
    if target <= 0 or not bands:
        return {band: 0 for band in bands}
    if sum(sizes[band] for band in bands) <= target:
        return {band: int(sizes[band]) for band in bands}
    quotas = {band: 0 for band in bands}
    active = list(bands)
    slots = target
    while slots > 0 and active:
        share = slots / len(active)
        scarce = [band for band in active if sizes[band] - quotas[band] <= share + 1e-9]
        if scarce:
            for band in scarce:
                room = sizes[band] - quotas[band]
                quotas[band] += room
                slots -= room
                active.remove(band)
            continue
        base = slots // len(active)
        extra = slots % len(active)
        order = sorted(active, key=lambda band: (-(sizes[band] - quotas[band]), band))
        for index, band in enumerate(order):
            give = base + (1 if index < extra else 0)
            room = sizes[band] - quotas[band]
            give = min(give, room)
            quotas[band] += give
            slots -= give
        break
    if slots > 0:
        order = sorted(bands, key=lambda band: (-(sizes[band] - quotas[band]), band))
        for band in order:
            room = sizes[band] - quotas[band]
            if room <= 0:
                continue
            give = min(room, slots)
            quotas[band] += give
            slots -= give
            if slots == 0:
                break
    return quotas


def _group_ids_by_band(anchors: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for row in sorted(anchors, key=lambda item: str(item["anchor_id"])):
        groups.setdefault(str(row["area_band"]), []).append(str(row["anchor_id"]))
    return groups


def sample_nested_review(
    anchors: Sequence[Mapping[str, Any]],
    rng: random.Random,
    *,
    paired_target: int = PAIRED_PER_GRID,
    review_target: int = REVIEW_PER_GRID,
) -> dict[str, dict[str, Any]]:
    """Sample the paired arm and the human-review subset nested inside it.

    A grid smaller than the target keeps every anchor. Records are not copied
    to inflate the count. Inclusion probabilities are band sampling weights.
    """

    groups = _group_ids_by_band(anchors)
    sizes = {band: len(ids) for band, ids in groups.items()}
    paired_quota = allocate_band_quotas(sizes, paired_target)
    paired_ids: set[str] = set()
    paired_prob: dict[str, float] = {}
    for band in sorted(groups):
        ids = groups[band]
        quota = paired_quota.get(band, 0)
        probability = (quota / len(ids)) if ids else 0.0
        for anchor_id in ids:
            paired_prob[anchor_id] = probability
        if quota <= 0:
            continue
        if quota >= len(ids):
            paired_ids.update(ids)
        else:
            paired_ids.update(rng.sample(ids, quota))

    paired_rows = [row for row in anchors if str(row["anchor_id"]) in paired_ids]
    review_groups = _group_ids_by_band(paired_rows)
    review_sizes = {band: len(ids) for band, ids in review_groups.items()}
    review_cap = min(review_target, len(paired_rows))
    review_quota = allocate_band_quotas(review_sizes, review_cap)
    review_ids: set[str] = set()
    review_given_paired: dict[str, float] = {}
    for band in sorted(review_groups):
        ids = review_groups[band]
        quota = review_quota.get(band, 0)
        conditional = (quota / len(ids)) if ids else 0.0
        for anchor_id in ids:
            review_given_paired[anchor_id] = conditional
        if quota <= 0:
            continue
        if quota >= len(ids):
            review_ids.update(ids)
        else:
            review_ids.update(rng.sample(ids, quota))

    out: dict[str, dict[str, Any]] = {}
    for row in anchors:
        anchor_id = str(row["anchor_id"])
        paired_p = paired_prob.get(anchor_id, 0.0)
        review_p = paired_p * review_given_paired.get(anchor_id, 0.0)
        out[anchor_id] = {
            "paired": int(anchor_id in paired_ids),
            "human_review": int(anchor_id in review_ids),
            "paired_inclusion_probability": paired_p,
            "review_inclusion_probability": review_p,
        }
    return out


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _download_rank(row: Mapping[str, Any]) -> tuple[Any, ...]:
    zoom = _as_int(row.get("requested_zoom"))
    zoom_rank = 2 if zoom == 19 else 1 if zoom == 18 else 0
    return (
        1 if _truthy(row.get("bbox_complete")) else 0,
        1 if str(row.get("provider")) == "TM" else 0,
        zoom_rank,
        -_as_int(row.get("version")) if str(row.get("version")).lstrip("-").isdigit() else 0,
        str(row.get("version")),
    )


def choose_download_row(rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], str]:
    """Pick one download for an anchor-date, keeping TM ahead of Wayback.

    Complete z19 coverage wins, then complete z18, then an incomplete row that
    must stay in the denominator. Version is only a tie break. Different dates
    are never collapsed together.
    """

    ordered = sorted(rows, key=_download_rank, reverse=True)
    best = ordered[0]
    providers = sorted({str(row.get("provider") or "") for row in rows})
    if _truthy(best.get("bbox_complete")) and str(best.get("provider")) == "TM":
        reason = "tm_complete"
    elif _truthy(best.get("bbox_complete")):
        reason = "wayback_complete_tm_unavailable"
    else:
        reason = "no_complete_coverage"
    best_view = dict(best)
    best_view["providers"] = "|".join(providers)
    best_view["n_source_rows"] = len(rows)
    return best_view, reason


def build_candidates(
    catalog_rows: Sequence[Mapping[str, Any]],
    *,
    census_by_anchor: Mapping[str, str],
) -> dict[str, Any]:
    """Collapse in-window historical rows to one download choice per date.

    Pre-2019 rows are counted and discarded. They are not candidates, so the
    runner has nothing to download or score for them. Post-census and
    reference-only rows are not historical candidates.
    """

    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    ignored_pre_2019 = 0
    ignored_post_census = 0
    ignored_reference_only = 0
    for row in catalog_rows:
        anchor_id = str(row["anchor_id"])
        capture_date = str(row["capture_date"])[:10]
        census_date = str(row.get("census_date") or census_by_anchor.get(anchor_id) or POLICY_CENSUS_DATE)[:10]
        if capture_date < WINDOW_START:
            ignored_pre_2019 += 1
            continue
        if _truthy(row.get("reference_only")):
            ignored_reference_only += 1
            continue
        if capture_date >= census_date:
            ignored_post_census += 1
            continue
        grouped.setdefault((anchor_id, capture_date), []).append(row)

    candidates: list[dict[str, Any]] = []
    for anchor_id, capture_date in sorted(grouped):
        chosen, reason = choose_download_row(grouped[(anchor_id, capture_date)])
        census_date = str(
            chosen.get("census_date")
            or census_by_anchor.get(anchor_id)
            or POLICY_CENSUS_DATE
        )[:10]
        callable_row = reason in {"tm_complete", "wayback_complete_tm_unavailable"}
        candidates.append(
            {
                "anchor_id": anchor_id,
                "capture_date": capture_date,
                "version": str(chosen.get("version") or ""),
                "provider": str(chosen.get("provider") or ""),
                "requested_zoom": str(chosen.get("requested_zoom") or ""),
                "bbox_complete": int(_truthy(chosen.get("bbox_complete"))),
                "callable": int(callable_row),
                "terminal_state": "" if callable_row else "not_downloadable",
                "download_choice_reason": reason,
                "n_source_rows": int(chosen["n_source_rows"]),
                "providers": chosen["providers"],
                "census_date": census_date,
            }
        )
    return {
        "candidates": candidates,
        "ignored_pre_2019": ignored_pre_2019,
        "ignored_post_census": ignored_post_census,
        "ignored_reference_only_historical": ignored_reference_only,
    }


def _reference_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    zoom = _as_int(row.get("requested_zoom"))
    zoom_rank = 0 if zoom == 19 else 1 if zoom == 18 else 2
    day = str(row.get("capture_date") or "0000-00-00")[:10]
    inverted_day = tuple(-int(part) for part in day.split("-"))
    return (
        0 if str(row.get("provider")) == "TM" else 1,
        zoom_rank,
        inverted_day,
        str(row.get("version") or ""),
    )


def build_reference_plan(
    anchors: Sequence[Mapping[str, Any]],
    catalog_rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rank up to three satellite references. A frame is not its own reference.

    Post-census complete frames come first. The in-window fallback stays inside
    the 2019 window and is skipped when its date is the candidate being scored;
    that omission is applied later per candidate.
    """

    census_by_anchor = {
        str(row["anchor_id"]): str(row.get("census_date") or POLICY_CENSUS_DATE)[:10]
        for row in anchors
    }
    post: dict[str, list[Mapping[str, Any]]] = {}
    for row in catalog_rows:
        if not _truthy(row.get("reference_only")):
            continue
        if not _truthy(row.get("bbox_complete")):
            continue
        anchor_id = str(row["anchor_id"])
        capture_date = str(row["capture_date"])[:10]
        census_date = census_by_anchor.get(anchor_id, POLICY_CENSUS_DATE)
        if capture_date < census_date or capture_date < WINDOW_START:
            continue
        post.setdefault(anchor_id, []).append(row)

    fallback: dict[str, list[Mapping[str, Any]]] = {}
    for row in candidates:
        if not _as_int(row.get("callable")):
            continue
        fallback.setdefault(str(row["anchor_id"]), []).append(row)

    plan: list[dict[str, Any]] = []
    for anchor in sorted(anchors, key=lambda item: str(item["anchor_id"])):
        anchor_id = str(anchor["anchor_id"])
        plan.append(
            {
                "anchor_id": anchor_id,
                "kind": "aerial",
                "rank": 1,
                "capture_date": str(anchor.get("census_date") or POLICY_CENSUS_DATE)[:10],
                "version": "",
                "provider": "census_aerial",
                "requested_zoom": "",
                "source": "local_census_aerial",
                "bbox_complete": "",
            }
        )
        post_rows = sorted(post.get(anchor_id, []), key=_reference_sort_key)[:MAX_SATELLITE_REFERENCES]
        source_rows = [(row, "post_census") for row in post_rows]
        if len(source_rows) < MAX_SATELLITE_REFERENCES:
            used_dates = {str(row["capture_date"])[:10] for row, _source in source_rows}
            for row in sorted(fallback.get(anchor_id, []), key=_reference_sort_key):
                if str(row["capture_date"])[:10] in used_dates:
                    continue
                source_rows.append((row, "pre_census_fallback"))
                if len(source_rows) >= MAX_SATELLITE_REFERENCES:
                    break
        for rank, (row, source) in enumerate(source_rows, start=1):
            plan.append(
                {
                    "anchor_id": anchor_id,
                    "kind": "satellite",
                    "rank": rank,
                    "capture_date": str(row["capture_date"])[:10],
                    "version": str(row.get("version") or ""),
                    "provider": str(row.get("provider") or ""),
                    "requested_zoom": str(row.get("requested_zoom") or ""),
                    "source": source,
                    "bbox_complete": int(_truthy(row.get("bbox_complete"))),
                }
            )
    return plan


def choose_satellite_reference(
    candidate_date: str,
    references: Sequence[Mapping[str, Any]],
    *,
    available_dates: set[str] | None = None,
) -> dict[str, Any]:
    """Pick a satellite reference that is not the candidate frame itself."""

    satellites = [row for row in references if str(row.get("kind")) == "satellite"]
    post = [row for row in satellites if str(row.get("source") or "") != "pre_census_fallback"]
    if satellites and not post:
        return {
            "reference": None,
            "degradation": "pre_census_not_reference",
            "self_reference_omitted": 0,
        }
    satellites = sorted(post, key=lambda row: _as_int(row.get("rank")))
    saw_self = False
    for row in satellites:
        ref_date = str(row.get("capture_date") or "")[:10]
        if ref_date == str(candidate_date)[:10]:
            saw_self = True
            continue
        if available_dates is not None and ref_date not in available_dates:
            continue
        return {
            "reference": dict(row),
            "degradation": "",
            "self_reference_omitted": 0,
        }
    if saw_self:
        return {"reference": None, "degradation": "self_reference_omitted", "self_reference_omitted": 1}
    return {"reference": None, "degradation": "triplet_unavailable", "self_reference_omitted": 0}


def assign_degradation(
    *,
    aerial_usable: bool,
    satellite_choice: Mapping[str, Any],
) -> str:
    """Name a missing reference. A one-image call is not a T2 or T3 success."""

    if not aerial_usable:
        return "candidate_only_reference_unavailable"
    code = str(satellite_choice.get("degradation") or "")
    if code:
        return code
    if satellite_choice.get("reference") is None:
        return "triplet_unavailable"
    return ""


def assemble_image_roles(
    *,
    arm: str,
    candidate_sha256: str,
    aerial_sha256: str | None,
    satellite_sha256: str | None,
    degradation: str,
    candidate_date: str,
    satellite_date: str | None,
) -> list[dict[str, str]]:
    """Return Image 1..N roles. Image 1 is always the candidate.

    A satellite frame with the candidate's date, or the candidate's own bytes,
    is not attached. T2 never receives the satellite frame. No request exceeds
    three images.
    """

    if not candidate_sha256:
        raise ValueError("candidate image is required")
    if arm not in {"t2", "t3"}:
        raise ValueError(f"unknown arm {arm}")
    if degradation == "candidate_only_reference_unavailable":
        raise ValueError("a missing reference is not a one-image T2 or T3 success")
    if not aerial_sha256:
        raise ValueError("aerial bytes missing for a two-frame request")
    roles = [
        {"role": "candidate", "sha256": candidate_sha256, "capture_date": candidate_date},
        {"role": "aerial_reference", "sha256": aerial_sha256, "capture_date": ""},
    ]
    if arm == "t2":
        return roles
    if not satellite_sha256:
        raise ValueError("post-census satellite reference is required for T3")
    if satellite_date and satellite_date[:10] == candidate_date[:10]:
        raise ValueError("satellite reference reuses the candidate date")
    if satellite_sha256 == candidate_sha256:
        raise ValueError("satellite reference reuses the candidate bytes")
    roles.append(
        {
            "role": "satellite_reference",
            "sha256": satellite_sha256,
            "capture_date": str(satellite_date or ""),
        }
    )
    return roles


def plan_model_requests(
    manifest_rows: Sequence[Mapping[str, Any]],
    *,
    paired_anchor_ids: set[str],
) -> list[dict[str, Any]]:
    """Plan every callable in-window candidate. Do not stop after a present label.

    Verdict columns are ignored on purpose. Adaptive early-stop belongs to the
    offline replay, not to this request list. Arms alternate on the frozen
    anchor-date order so one arm is not a single block at the front.
    """

    requests: list[dict[str, Any]] = []
    ordered = sorted(
        manifest_rows,
        key=lambda row: (str(row["anchor_id"]), str(row["capture_date"]), str(row.get("arm") or "")),
    )
    for row in ordered:
        capture_date = str(row["capture_date"])[:10]
        if capture_date < WINDOW_START:
            continue
        if not _as_int(row.get("callable")):
            continue
        # Imagery that cannot be sent is out of the model denominator.
        # A failed call stays in the request list so the 99% response
        # rate counts it instead of dropping the date.
        if str(row.get("terminal_state") or "") in {
            "not_downloadable",
            "download_failed",
            "quality_failed",
            "image_missing",
            "image_unreadable",
            "pixel_uninformative",
            "not_sent",
            "no_post_census_reference",
        }:
            continue
        anchor_id = str(row["anchor_id"])
        if is_pixel_uninformative(anchor_id, capture_date):
            continue
        candidate_sha = str(row.get("candidate_sha256") or "")
        aerial_sha = str(row.get("aerial_sha256") or "")
        satellite_sha = str(row.get("satellite_sha256") or "")
        arms = ["t3"]
        if anchor_id in paired_anchor_ids:
            arms.append("t2")
        for arm in arms:
            if arm == "t3" and (not candidate_sha or not aerial_sha or not satellite_sha):
                continue
            if arm == "t2" and (not candidate_sha or not aerial_sha):
                continue
            requests.append(
                {
                    "arm": arm,
                    "anchor_id": anchor_id,
                    "capture_date": capture_date,
                    "stage": "first_pass",
                    "degradation": "",
                    "candidate_sha256": candidate_sha,
                    "aerial_sha256": aerial_sha,
                    "satellite_sha256": satellite_sha if arm == "t3" else "",
                }
            )
    return requests


def select_techcheck(
    anchors: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    *,
    n_anchors: int = TECHCHECK_ANCHORS,
    n_dates: int = TECHCHECK_DATES,
) -> list[dict[str, str]]:
    """Pick development anchors and three metadata dates. Labels are not read."""

    development = [
        row
        for row in anchors
        if str(row.get("grid_role")) == "development" and str(row.get("history_reason")) == "ok"
    ]
    by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in development:
        by_key.setdefault((str(row.get("chip_arm") or ""), str(row.get("area_band") or "")), []).append(row)
    ordered_anchors: list[Mapping[str, Any]] = []
    bands = sorted(by_key)
    while len(ordered_anchors) < n_anchors and any(by_key.values()):
        for key in bands:
            pool = by_key[key]
            if not pool:
                continue
            ordered_anchors.append(pool.pop(0))
            if len(ordered_anchors) >= n_anchors:
                break
    chosen_ids = [str(row["anchor_id"]) for row in ordered_anchors[:n_anchors]]
    by_anchor: dict[str, list[str]] = {}
    for row in candidates:
        if not _as_int(row.get("callable")):
            continue
        by_anchor.setdefault(str(row["anchor_id"]), []).append(str(row["capture_date"])[:10])
    picks: list[dict[str, str]] = []
    for anchor_id in chosen_ids:
        dates = sorted(set(by_anchor.get(anchor_id, [])))
        if not dates:
            continue
        if len(dates) <= n_dates:
            chosen_dates = dates
        else:
            indexes = sorted({0, len(dates) // 2, len(dates) - 1})
            chosen_dates = [dates[index] for index in indexes[:n_dates]]
        for capture_date in chosen_dates:
            picks.append({"anchor_id": anchor_id, "capture_date": capture_date})
    return picks


def interpret_model_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate one candidate object. Unknown and illegal hard labels are not absent.

    present and absent both require usable quality, a matched target, a boolean
    PV label, and an evidence string. A hard label that misses any of those is
    an invalid response, not a silent absent. Transport failures never reach
    this function.
    """

    if not isinstance(payload, Mapping):
        return {"ok": False, "verdict": None, "reason": "not_object"}
    extra = set(payload) - ALLOWED_RESPONSE_KEYS
    if extra:
        return {"ok": False, "verdict": None, "reason": "extra_fields:" + ",".join(sorted(extra))}
    if payload.get("candidate_index") != 1:
        return {"ok": False, "verdict": None, "reason": "candidate_index"}
    quality = payload.get("quality_flag")
    match = payload.get("target_match")
    pv_present = payload.get("pv_present")
    if quality not in {"usable", "ambiguous", "unusable"}:
        return {"ok": False, "verdict": None, "reason": "quality_flag"}
    if match not in {True, False, None}:
        return {"ok": False, "verdict": None, "reason": "target_match"}
    if pv_present not in {True, False, None}:
        return {"ok": False, "verdict": None, "reason": "pv_present"}
    evidence = str(payload.get("evidence") or "").strip()
    unknown_reason = str(payload.get("unknown_reason") or "").strip()
    if not evidence and not unknown_reason:
        return {"ok": False, "verdict": None, "reason": "missing_evidence"}
    hard_ok = quality == "usable" and match is True and pv_present in {True, False} and bool(evidence)
    if pv_present in {True, False} and not hard_ok:
        return {"ok": False, "verdict": None, "reason": "hard_label_not_admitted"}
    if hard_ok:
        verdict = "present" if pv_present is True else "absent"
    else:
        verdict = "unknown"
    return {
        "ok": True,
        "verdict": verdict,
        "reason": "",
        "quality_flag": quality,
        "target_match": match,
        "pv_present": pv_present,
        "confidence": payload.get("confidence"),
        "evidence": evidence,
        "unknown_reason": unknown_reason,
        "object_change_flag": payload.get("object_change_flag"),
    }


def admit_model_version(returned: str | None, frozen_version: str | None) -> bool:
    """The alias and the frozen response version must both be the Lite identity.

    Before the technical check freezes a version, only the exact alias is
    admitted. A different Flash or Pro version is not rewritten into Lite.
    """

    value = str(returned or "").strip()
    if not value:
        return False
    expected = str(frozen_version or MODEL_ALIAS).strip()
    return value == expected == MODEL_ALIAS or (
        frozen_version is not None and value == frozen_version and MODEL_ALIAS in frozen_version
    )


def visual_verdict(call_status: str, interpreted: Mapping[str, Any] | None) -> str | None:
    """Separate a visual label from a failed call. Failures are not unknown or absent."""

    if call_status != "ok" or not interpreted or not interpreted.get("ok"):
        return None
    verdict = interpreted.get("verdict")
    if verdict not in {"present", "absent", "unknown"}:
        return None
    return str(verdict)


def cache_key(record: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record.get("arm") or record.get("kind") or ""),
        str(record["anchor_id"]),
        str(record["capture_date"])[:10],
        str(record.get("stage") or "first_pass"),
    )


class RawOverwriteError(RuntimeError):
    """A second successful raw verdict would replace one that is already stored."""


def commit_raw_record(
    existing: list[dict[str, Any]],
    record: Mapping[str, Any],
) -> str:
    """Append a raw record in memory, or skip a fingerprint-matched success.

    The caller writes the returned list. This function never replaces an
    earlier object. A second success for the same arm, anchor, date, and stage
    raises instead of overwriting.
    """

    key = cache_key(record)
    if any(
        cache_key(row) == key and str(row.get("call_status")) in TERMINAL_NONCALL
        for row in existing
    ):
        return "skipped"
    successes = [
        row
        for row in existing
        if cache_key(row) == key and str(row.get("call_status")) == "ok"
    ]
    if successes:
        prior = successes[0]
        same = (
            str(prior.get("fingerprint")) == str(record.get("fingerprint"))
            and str(prior.get("model_version")) == str(record.get("model_version"))
            and str(prior.get("candidate_sha256")) == str(record.get("candidate_sha256"))
        )
        if same and str(record.get("call_status")) == "ok":
            return "skipped"
        if str(record.get("call_status")) == "ok":
            raise RawOverwriteError(f"raw success already stored for {key}")
        return "skipped"
    existing.append(dict(record))
    return "appended"


def remaining_attempts(
    existing: Sequence[Mapping[str, Any]],
    key: tuple[str, str, str, str],
    *,
    limit: int = 3,
) -> int:
    """How many logical model calls may still be appended for one cache key.

    A stored success leaves zero. Failures consume the budget and are not
    rewritten; the caller stops instead of looping forever on resume.
    """

    used = 0
    for row in existing:
        if cache_key(row) != key:
            continue
        status = str(row.get("call_status"))
        if status == "ok" or status in TERMINAL_NONCALL:
            return 0
        used += 1
    return max(0, limit - used)


def pending_requests(
    planned: Sequence[Mapping[str, Any]],
    existing: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return planned calls that do not already have a matching raw success."""

    done: set[tuple[str, str, str, str]] = set()
    for row in existing:
        if str(row.get("call_status")) != "ok":
            continue
        done.add(cache_key(row))
    pending: list[dict[str, Any]] = []
    for row in planned:
        if cache_key(row) in done:
            continue
        pending.append(dict(row))
    return pending


def outstanding_requests(
    planned: Sequence[Mapping[str, Any]],
    existing: Sequence[Mapping[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return planned calls that still have an attempt left.

    One pass over the raw log. A stored success leaves zero attempts, and
    failures consume the same budget as ``remaining_attempts``.
    """

    done: set[tuple[str, str, str, str]] = set()
    used: dict[tuple[str, str, str, str], int] = {}
    for row in existing:
        key = cache_key(row)
        status = str(row.get("call_status"))
        if status == "ok" or status in TERMINAL_NONCALL:
            done.add(key)
            continue
        used[key] = used.get(key, 0) + 1
    pending: list[dict[str, Any]] = []
    for row in planned:
        key = cache_key(row)
        if key in done or used.get(key, 0) >= limit:
            continue
        pending.append(dict(row))
    return pending


def _confirmed(row: Mapping[str, Any]) -> str | None:
    if _truthy(row.get("reference_only")):
        return None
    if str(row.get("call_status") or "ok") != "ok":
        return None
    verdict = str(row.get("verdict") or "")
    if verdict in {"present", "absent"}:
        return verdict
    return None


def infer_interval(
    observations: Sequence[Mapping[str, Any]],
    *,
    expected_dates: Sequence[str],
    aerial_pv: str,
    aerial_date: str,
    aerial_precision: str,
) -> dict[str, Any]:
    """Infer one install interval from immutable first-pass labels.

    The observation list is not rewritten. An isolated absent between presents
    stays a conflict; it is not dip-repaired into a present. All-present history
    is left-censored and does not receive 2019-01-01 or the first image date as
    an install date.
    """

    expected = sorted({str(day)[:10] for day in expected_dates if str(day)[:10] >= WINDOW_START})
    by_date = {str(row["capture_date"])[:10]: row for row in observations}
    missing = [day for day in expected if day not in by_date or not _truthy(by_date[day].get("terminal", 1))]
    base = {
        "status": "undated",
        "censoring": "",
        "install_date": "",
        "interval_start": "",
        "interval_end": "",
        "last_absent_date": "",
        "earliest_present_date": "",
        "upper_bound_date": "",
        "upper_bound_precision": "",
        "conflict_frames": "",
        "undated_reason": "",
        "n_present": 0,
        "n_absent": 0,
        "n_unknown": 0,
        "n_call_failed": 0,
        "labels_modified": 0,
    }
    if any(day < WINDOW_START for day in by_date):
        base["undated_reason"] = "pre_2019_observation"
        return base
    if missing:
        base["status"] = "incomplete_timeline"
        base["undated_reason"] = "missing_terminal_state"
        return base
    present_dates: list[str] = []
    absent_dates: list[str] = []
    call_failures = {
        "call_failed",
        "invalid_response",
        "transport_failed",
        "model_version_mismatch",
        "truncated",
    }
    for day in expected:
        row = by_date[day]
        verdict = _confirmed(row)
        if verdict == "present":
            present_dates.append(day)
            continue
        if verdict == "absent":
            absent_dates.append(day)
            continue
        status = str(row.get("call_status") or "")
        if status in call_failures:
            base["n_call_failed"] += 1
        else:
            # Missing imagery and visual unknown stay unknown. They are not absent.
            base["n_unknown"] += 1
    base["n_present"] = len(present_dates)
    base["n_absent"] = len(absent_dates)
    confirmed = sorted(
        [(day, "absent") for day in absent_dates] + [(day, "present") for day in present_dates]
    )
    conflict_frames: list[str] = []
    for index in range(len(confirmed) - 1):
        if confirmed[index][1] == "present" and confirmed[index + 1][1] == "absent":
            conflict_frames.extend([confirmed[index][0], confirmed[index + 1][0]])
    if conflict_frames:
        unique_frames = sorted(set(conflict_frames))
        base.update(
            {
                "status": "conflict_pending_review",
                "conflict_frames": "|".join(unique_frames),
                "undated_reason": "present_then_absent",
            }
        )
        return base
    if present_dates and absent_dates and max(absent_dates) < min(present_dates):
        last_absent = max(absent_dates)
        earliest_present = min(present_dates)
        base.update(
            {
                "status": "bounded",
                "censoring": "none",
                "interval_start": last_absent,
                "interval_end": earliest_present,
                "last_absent_date": last_absent,
                "earliest_present_date": earliest_present,
            }
        )
        return base
    if present_dates and not absent_dates:
        base.update(
            {
                "status": "left_censored",
                "censoring": "left",
                "earliest_present_date": min(present_dates),
                "undated_reason": "no_confirmed_absent_in_window",
            }
        )
        return base
    if absent_dates and not present_dates:
        if aerial_pv == "present":
            base.update(
                {
                    "status": "aerial_upper_bound",
                    "censoring": "right",
                    "upper_bound_date": str(aerial_date)[:10],
                    "upper_bound_precision": aerial_precision,
                    "undated_reason": "no_confirmed_present_in_window",
                }
            )
            return base
        base["undated_reason"] = (
            "inventory_reference_conflict" if aerial_pv == "absent" else "aerial_reference_unknown"
        )
        return base
    if aerial_pv == "absent":
        base["undated_reason"] = "inventory_reference_conflict"
    elif aerial_pv == "unknown":
        base["undated_reason"] = "aerial_reference_unknown"
    else:
        base["undated_reason"] = "only_unknown"
    return base


def plan_rechecks(interval: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]) -> list[str]:
    """Extra single-candidate dates. Ordinary boundaries stay near two requests."""

    status = str(interval.get("status") or "")
    if status == "conflict_pending_review":
        frames = [day for day in str(interval.get("conflict_frames") or "").split("|") if day]
        ordered = sorted(set(frames))
        return ordered[:MAX_CONFLICT_RECHECKS]
    if status == "bounded":
        ends = [str(interval.get("last_absent_date") or ""), str(interval.get("earliest_present_date") or "")]
        return [day for day in ends if day][:MAX_BOUNDARY_RECHECKS]
    if status == "left_censored" and interval.get("earliest_present_date"):
        return [str(interval["earliest_present_date"])]
    return []


def repeat_support(
    first_pass: Sequence[Mapping[str, Any]],
    rechecks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Agreeing repeats are support, not human adjudication. Disagreements stay unresolved."""

    recheck_by_date: dict[str, Mapping[str, Any]] = {}
    for row in rechecks:
        if str(row.get("call_status")) != "ok":
            continue
        recheck_by_date[str(row["capture_date"])[:10]] = row
    compared = 0
    agreed = 0
    disagreements: list[str] = []
    for row in first_pass:
        day = str(row["capture_date"])[:10]
        if day not in recheck_by_date:
            continue
        compared += 1
        if str(row.get("verdict")) == str(recheck_by_date[day].get("verdict")):
            agreed += 1
        else:
            disagreements.append(day)
    if compared == 0:
        support = "not_rechecked"
    elif disagreements:
        support = "unresolved"
    else:
        support = "repeat_supported"
    return {
        "support": support,
        "n_compared": compared,
        "n_agreed": agreed,
        "disagreement_dates": "|".join(disagreements),
    }


def _label_to_round_result(pick: Any, label: Mapping[str, Any] | None) -> RoundResult:
    verdict = None if label is None else label.get("verdict")
    call_status = "ok" if label is None else str(label.get("call_status") or "ok")
    if call_status != "ok":
        pv_present = None
        quality = "unusable"
        source = "gemini_failed"
    elif verdict == "present":
        pv_present = True
        quality = "usable"
        source = "gemini_per_image"
    elif verdict == "absent":
        pv_present = False
        quality = "usable"
        source = "gemini_per_image"
    else:
        pv_present = None
        quality = "ambiguous"
        source = "gemini_per_image"
    return RoundResult(
        chip_index=int(pick.chip_index),
        capture_date=str(pick.capture_date)[:10],
        version=pick.version,
        pv_present=pv_present,
        confidence=None,
        quality_flag=quality,
        decision_source=source,
        provider=str(pick.provider),
        reference_only=bool(pick.reference_only),
    )


def replay_adaptive(
    *,
    anchor_id: str,
    region_key: str,
    grid_id: str,
    census_date: str,
    labels: Sequence[Mapping[str, Any]],
    config: AdaptiveScanConfig | None = None,
) -> dict[str, Any]:
    """Replay the current adaptive select-and-stop rule on already scored labels.

    Dates the full timeline scored, and the adaptive rule never picked, are the
    coverage loss. This function does not issue a model call and does not change
    the stored labels. Pre-2019 labels are excluded before the planner sees them.
    """

    active_config = config or AdaptiveScanConfig()
    window_labels = [
        row
        for row in labels
        if in_analysis_window(str(row["capture_date"]), census_date)
        and not _truthy(row.get("reference_only"))
    ]
    excluded_pre = [
        str(row["capture_date"])[:10]
        for row in labels
        if str(row["capture_date"])[:10] < WINDOW_START
    ]
    vintages = [
        VintageEntry(
            capture_date=str(row["capture_date"])[:10],
            version=row.get("version") or "noversion",
            provider=str(row.get("provider") or "TM"),
            reference_only=False,
        )
        for row in sorted(window_labels, key=lambda item: str(item["capture_date"]))
    ]
    by_date = {str(row["capture_date"])[:10]: row for row in window_labels}
    if not vintages:
        return {
            "anchor_id": anchor_id,
            "status": "no_history_in_window",
            "picked_dates": "",
            "skipped_dates": "",
            "skipped_boundary_dates": "",
            "n_rounds": 0,
            "excluded_pre_2019": "|".join(excluded_pre),
            "reference_frames_used_as_evidence": 0,
        }
    state = create_scan_state(
        {
            "anchor_id": anchor_id,
            "region_key": region_key,
            "grid_id": grid_id,
            "centroid_grid": grid_id,
        }
    )
    state.census_date = census_date
    picked: list[str] = []
    reference_as_evidence = 0
    for _guard in range(40):
        action = decide_next_action(
            state,
            vintages,
            active_config,
            census_date=census_date,
        )
        if isinstance(action, TerminateAction):
            state.status = action.status
            state.notes = action.notes
            break
        rnd = action.round
        results: list[RoundResult] = []
        for pick in rnd.picks:
            if pick.reference_only:
                reference_as_evidence += 1
            else:
                picked.append(str(pick.capture_date)[:10])
            results.append(_label_to_round_result(pick, by_date.get(str(pick.capture_date)[:10])))
        rnd.results = results
        rnd.completed = True
        state.rounds.append(rnd)
    else:
        state.status = "replay_guard_stopped"
    available = [item.capture_date for item in vintages]
    picked_set = set(picked)
    skipped = [day for day in available if day not in picked_set]
    return {
        "anchor_id": anchor_id,
        "status": state.status,
        "picked_dates": "|".join(sorted(picked_set)),
        "skipped_dates": "|".join(skipped),
        "n_rounds": len(state.rounds),
        "excluded_pre_2019": "|".join(excluded_pre),
        "reference_frames_used_as_evidence": reference_as_evidence,
    }


def compare_old_interval(
    new_interval: Mapping[str, Any],
    old_row: Mapping[str, Any] | None,
    *,
    raw_sequence_recovered: bool,
    raw_dates: Sequence[str] | None,
) -> dict[str, Any]:
    """Compare the published same-window interval without inventing a raw sequence."""

    if old_row is None:
        return {
            "old_interval_present": 0,
            "old_raw_sequence": "not_in_published_table",
            "invented_raw_sequence": 0,
            "narrower": "",
            "narrower_is_accuracy": 0,
        }
    recovered_dates = list(raw_dates or []) if raw_sequence_recovered else []
    old_start = str(old_row.get("latest_absent_date") or "")[:10]
    old_end = str(old_row.get("earliest_present_date") or "")[:10]
    new_start = str(new_interval.get("last_absent_date") or "")[:10]
    new_end = str(new_interval.get("earliest_present_date") or "")[:10]
    narrower = ""
    if old_start and old_end and new_start and new_end and old_start < old_end and new_start < new_end:
        old_width = (parse_iso(old_end) - parse_iso(old_start)).days
        new_width = (parse_iso(new_end) - parse_iso(new_start)).days
        if new_width < old_width:
            narrower = "narrower"
        elif new_width > old_width:
            narrower = "wider"
        else:
            narrower = "same_width"
    return {
        "old_interval_present": 1,
        "old_latest_absent_date": old_start,
        "old_earliest_present_date": old_end,
        "old_install_date": str(old_row.get("install_date") or ""),
        "old_cohort": str(old_row.get("cohort") or ""),
        "old_raw_sequence": "recovered" if raw_sequence_recovered else "not_recovered",
        "old_raw_n_dates": len(recovered_dates) if raw_sequence_recovered else "",
        "invented_raw_sequence": 0,
        "width_change": narrower,
        "narrower_is_accuracy": 0,
        "year_bound_changed": int(
            (old_start[:4] != new_start[:4]) or (old_end[:4] != new_end[:4])
        ),
    }


def build_conclusion(summary: Mapping[str, Any]) -> str:
    """State what changed, what that does to install-year bounds, and what stays undated.

    A narrower bracket is not an accuracy pass. This pilot has no independent
    human adjudication, and the old comparison changes frame choice, reading,
    and reference together.
    """

    lines = [
        "# 全日期参考对照试点结论",
        "",
        "这是工程与稳定性结果，不构成准确度通过。没有独立人工裁决，不能把模型区间写成安装日期的误差。",
        "",
        f"冻结网格 {summary.get('n_grids')} 个，其中开发 {summary.get('n_development_grids')} 个、评价 {summary.get('n_evaluation_grids')} 个。",
        f"库存目标 N={summary.get('N')}，窗口内可调用历史候选 H={summary.get('H')}，配对候选 H_pair={summary.get('H_pair')}。",
        f"有效模型响应比例 {summary.get('valid_response_rate')}，配对候选字节一致率 {summary.get('candidate_byte_agreement')}。",
        "",
        "## 变了什么",
        str(summary.get("what_changed") or ""),
        "旧流程同时改变了选帧、读图和参考，不能把全部差异归因于三帧。",
        "",
        "## 对安装年份界的影响",
        str(summary.get("install_year_effect") or ""),
        "更窄不等于更准。左删失没有把 2019-01-01 或第一张影像日期写成安装日。",
        "",
        "## 仍不能定年的目标",
        str(summary.get("undatable_targets") or ""),
        "",
        f"accuracy_pass={str(bool(summary.get('accuracy_pass'))).lower()}",
    ]
    return "\n".join(lines) + "\n"


def audit_tables(
    *,
    manifest: Sequence[Mapping[str, Any]],
    raw_records: Sequence[Mapping[str, Any]],
    intervals: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
    frozen_model_version: str = MODEL_ALIAS,
) -> dict[str, Any]:
    """Check the engineering gates. This does not award an accuracy pass."""

    failures: list[str] = []
    pending = [
        row
        for row in manifest
        if str(row.get("terminal_state") or "") in {"", "pending"}
    ]
    if pending:
        failures.append(f"pending_manifest_rows={len(pending)}")
    pre_2019_scored = [
        row
        for row in raw_records
        if str(row.get("capture_date") or "")[:10] < WINDOW_START
        and str(row.get("stage") or "") != "preflight"
    ]
    if pre_2019_scored:
        failures.append(f"pre_2019_scored={len(pre_2019_scored)}")
    coerced = [
        row
        for row in raw_records
        if str(row.get("verdict")) == "absent"
        and (
            str(row.get("call_status")) != "ok"
            or str(row.get("quality_flag")) != "usable"
            or row.get("target_match") is not True
        )
    ]
    if coerced:
        failures.append(f"absent_without_usable_match={len(coerced)}")
    foreign = [
        row
        for row in raw_records
        if str(row.get("call_status")) == "ok"
        and str(row.get("model_version") or "") != frozen_model_version
    ]
    if foreign:
        failures.append(f"foreign_model_versions={len(foreign)}")
    reference_evidence = [
        row
        for row in raw_records
        if _truthy(row.get("reference_only")) and str(row.get("stage")) == "first_pass"
    ]
    if reference_evidence:
        failures.append(f"reference_as_historical_evidence={len(reference_evidence)}")
    seen: set[tuple[str, str, str, str]] = set()
    overwrites = 0
    for row in raw_records:
        if str(row.get("call_status")) != "ok":
            continue
        key = cache_key(row)
        if key in seen:
            overwrites += 1
        seen.add(key)
    if overwrites:
        failures.append(f"raw_overwrite_successes={overwrites}")
    callable_keys = {
        (str(row["anchor_id"]), str(row["capture_date"])[:10], str(row["arm"]))
        for row in requests
    }
    # Rechecks share the anchor-date-arm key but are not extra denominator
    # successes. Counting them would hide a first pass below 99%.
    ok_keys: set[tuple[str, str, str]] = set()
    for row in raw_records:
        if str(row.get("stage") or "first_pass") != "first_pass":
            continue
        key = (str(row["anchor_id"]), str(row["capture_date"])[:10], str(row.get("arm") or ""))
        if key in callable_keys and str(row.get("call_status")) == "ok" and row.get("verdict") in {
            "present",
            "absent",
            "unknown",
        }:
            ok_keys.add(key)
    valid_rate = (len(ok_keys) / len(callable_keys)) if callable_keys else 1.0
    if callable_keys and valid_rate < 0.99:
        failures.append(f"valid_response_rate={valid_rate:.4f}")
    paired: dict[tuple[str, str], dict[str, str]] = {}
    for row in requests:
        paired.setdefault((str(row["anchor_id"]), str(row["capture_date"])[:10]), {})[
            str(row["arm"])
        ] = str(row.get("candidate_sha256") or "")
    compared = 0
    byte_mismatch = 0
    for arms in paired.values():
        if "t2" in arms and "t3" in arms:
            compared += 1
            if arms["t2"] != arms["t3"] or not arms["t2"]:
                byte_mismatch += 1
    if byte_mismatch:
        failures.append(f"candidate_byte_mismatch={byte_mismatch}")
    agreement = 1.0 if compared == 0 else (compared - byte_mismatch) / compared
    dip_repairs = [row for row in intervals if _as_int(row.get("labels_modified"))]
    if dip_repairs:
        failures.append(f"dip_repairs={len(dip_repairs)}")
    return {
        "ok": not failures,
        "failures": failures,
        "valid_response_rate": valid_rate,
        "candidate_byte_agreement": agreement,
        "n_manifest": len(manifest),
        "n_pending": len(pending),
        "foreign_model_versions": len(foreign),
        "reference_as_evidence": len(reference_evidence),
        "raw_overwrites": overwrites,
        "accuracy_pass": False,
    }


def history_reason_for_anchor(n_window_dates: int, n_callable_dates: int) -> str:
    if n_window_dates <= 0:
        return "no_history_in_window"
    if n_callable_dates <= 0:
        return "no_downloadable_history"
    return "ok"


def build_plan(
    grid_rows: Sequence[Mapping[str, Any]],
    anchor_rows: Sequence[Mapping[str, Any]],
    catalog_rows: Sequence[Mapping[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Freeze grids, every inventory anchor in those grids, candidates, and references."""

    selection = select_grids(grid_rows, seed=seed)
    selected_ids = {row["grid_id"] for row in selection["grids"]}
    role_by_grid = {row["grid_id"]: row for row in selection["grids"]}
    selected_anchors = [
        dict(row)
        for row in anchor_rows
        if str(row.get("grid_id") or row.get("centroid_grid")) in selected_ids
    ]
    for row in selected_anchors:
        row["grid_id"] = str(row.get("grid_id") or row.get("centroid_grid"))
        row["area_band"] = area_band(row.get("area_m2"))
        row["census_date"] = str(row.get("census_date") or POLICY_CENSUS_DATE)[:10]
        row["census_date_precision"] = str(
            row.get("census_date_precision") or POLICY_CENSUS_PRECISION
        )
    census_by_anchor = {str(row["anchor_id"]): row["census_date"] for row in selected_anchors}
    selected_anchor_ids = set(census_by_anchor)
    selected_catalog = [
        row for row in catalog_rows if str(row["anchor_id"]) in selected_anchor_ids
    ]
    built = build_candidates(selected_catalog, census_by_anchor=census_by_anchor)
    candidates = built["candidates"]
    references = build_reference_plan(selected_anchors, selected_catalog, candidates)
    counts: dict[str, dict[str, int]] = {}
    for row in candidates:
        bucket = counts.setdefault(str(row["anchor_id"]), {"window": 0, "callable": 0})
        bucket["window"] += 1
        bucket["callable"] += _as_int(row.get("callable"))

    # Grid draws use their own Random(seed) inside select_grids. Anchor draws
    # use a second stream that also starts at this seed, so a repeated inventory
    # does not depend on how many random calls the grid stage made.
    rng = random.Random(seed)
    anchors_by_grid: dict[str, list[dict[str, Any]]] = {}
    for row in selected_anchors:
        anchors_by_grid.setdefault(row["grid_id"], []).append(row)
    sampled_flags: dict[str, dict[str, Any]] = {}
    for grid_id in sorted(anchors_by_grid):
        sampled_flags.update(sample_nested_review(anchors_by_grid[grid_id], rng))

    anchor_table: list[dict[str, Any]] = []
    for row in sorted(selected_anchors, key=lambda item: (item["grid_id"], str(item["anchor_id"]))):
        anchor_id = str(row["anchor_id"])
        stats = counts.get(anchor_id, {"window": 0, "callable": 0})
        meta = role_by_grid[row["grid_id"]]
        flags = sampled_flags[anchor_id]
        anchor_table.append(
            {
                "anchor_id": anchor_id,
                "grid_id": row["grid_id"],
                "source_grid": row.get("source_grid", ""),
                "source_grids": row.get("source_grids", ""),
                "region_key": row.get("region_key", "cape_town"),
                "centroid_lon": row.get("centroid_lon", ""),
                "centroid_lat": row.get("centroid_lat", ""),
                "area_m2": row.get("area_m2", ""),
                "area_band": row["area_band"],
                "chip_arm": row.get("chip_arm", ""),
                "review_extent_m": row.get("review_extent_m", ""),
                "geometry_exception": row.get("geometry_exception", ""),
                "geometry_version": row.get("geometry_version", ""),
                "chip_lon_min": row.get("chip_lon_min", ""),
                "chip_lat_min": row.get("chip_lat_min", ""),
                "chip_lon_max": row.get("chip_lon_max", ""),
                "chip_lat_max": row.get("chip_lat_max", ""),
                "source_width_m": row.get("source_width_m", ""),
                "source_height_m": row.get("source_height_m", ""),
                "source_box_width_m": row.get("source_box_width_m", ""),
                "source_box_height_m": row.get("source_box_height_m", ""),
                "center_offset_m": row.get("center_offset_m", ""),
                "grid_role": meta["role"],
                "grid_stratum": meta["stratum"],
                "risk_grid": meta["risk_grid"],
                "paired": flags["paired"],
                "paired_inclusion_probability": f"{flags['paired_inclusion_probability']:.6f}",
                "human_review": flags["human_review"],
                "review_inclusion_probability": f"{flags['review_inclusion_probability']:.6f}",
                "census_date": row["census_date"],
                "census_date_precision": row["census_date_precision"],
                "n_window_dates": stats["window"],
                "n_callable_dates": stats["callable"],
                "history_reason": history_reason_for_anchor(stats["window"], stats["callable"]),
            }
        )
    n_paired = sum(_as_int(row["paired"]) for row in anchor_table)
    n_review = sum(_as_int(row["human_review"]) for row in anchor_table)
    paired_ids = {row["anchor_id"] for row in anchor_table if _as_int(row["paired"])}
    h_pair = sum(
        _as_int(row["callable"])
        for row in candidates
        if row["anchor_id"] in paired_ids
    )
    summary = {
        "seed": seed,
        "window_start": WINDOW_START,
        "window_rule": "capture_date >= 2019-01-01 and capture_date < census_date",
        "n_grids": len(selection["grids"]),
        "n_development_grids": sum(row["role"] == "development" for row in selection["grids"]),
        "n_evaluation_grids": sum(row["role"] == "evaluation" for row in selection["grids"]),
        "development_grids": "|".join(
            row["grid_id"] for row in selection["grids"] if row["role"] == "development"
        ),
        "evaluation_grids": "|".join(
            row["grid_id"] for row in selection["grids"] if row["role"] == "evaluation"
        ),
        "N": len(anchor_table),
        "H": sum(_as_int(row["callable"]) for row in candidates),
        "H_pair": h_pair,
        "n_paired_anchors": n_paired,
        "n_review_anchors": n_review,
        "n_out_of_scope_grids": len(selection["out_of_scope"]),
        "ignored_pre_2019": built["ignored_pre_2019"],
        "model_alias": MODEL_ALIAS,
        "accuracy_pass": False,
        "illustrative_example_used_as_count": 0,
    }
    return {
        "grids": selection["grids"],
        "anchors": anchor_table,
        "candidates": candidates,
        "references": references,
        "out_of_scope": selection["out_of_scope"],
        "selection": selection,
        "summary": summary,
    }


def format_inventory_summary(summary: Mapping[str, Any]) -> str:
    """Stable stdout. No clock time, so two clean runs print the same text."""

    keys = [
        "seed",
        "window_start",
        "window_rule",
        "n_grids",
        "n_development_grids",
        "n_evaluation_grids",
        "development_grids",
        "evaluation_grids",
        "N",
        "H",
        "H_pair",
        "n_paired_anchors",
        "n_review_anchors",
        "n_out_of_scope_grids",
        "ignored_pre_2019",
        "model_alias",
        "accuracy_pass",
        "illustrative_example_used_as_count",
    ]
    return "\n".join(f"{key}={summary[key]}" for key in keys) + "\n"


GAP_CALL_STATUSES = frozenset(
    {
        "not_downloadable",
        "download_failed",
        "quality_failed",
        "image_missing",
        "image_unreadable",
        "pixel_uninformative",
        "not_sent",
        "no_post_census_reference",
        "call_failed",
        "invalid_response",
        "transport_failed",
        "model_version_mismatch",
        "truncated",
    }
)


def apply_manifest_gap_states(
    candidates: Sequence[Mapping[str, Any]],
    manifest: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Copy imagery and exhausted-call terminals onto frozen candidate rows.

    The inventory table only knows ``not_downloadable``. Quality and download
    failures are known after rendering, on the manifest. A blank date there
    makes a finished anchor look incomplete and skips its boundary recheck.
    A scored date stays blank so the raw first-pass label remains the verdict.
    An existing inventory terminal is not overwritten.
    """

    gaps: dict[tuple[str, str], str] = {}
    for row in manifest:
        state = str(row.get("terminal_state") or "")
        if state in GAP_CALL_STATUSES:
            gaps[(str(row.get("anchor_id") or ""), str(row.get("capture_date") or "")[:10])] = state
    updated: list[dict[str, Any]] = []
    for row in candidates:
        item = dict(row)
        key = (str(row.get("anchor_id") or ""), str(row.get("capture_date") or "")[:10])
        if not str(item.get("terminal_state") or "") and key in gaps:
            item["terminal_state"] = gaps[key]
        updated.append(item)
    return updated


def timeline_observations(
    candidates: Sequence[Mapping[str, Any]],
    raw_records: Sequence[Mapping[str, Any]],
    *,
    anchor_id: str,
    arm: str = "t3",
) -> list[dict[str, Any]]:
    """One observation per candidate date. Imagery gaps stay non-absent.

    A first-pass success is copied from the immutable raw log. A date that
    never became a model call keeps its terminal imagery or call status.
    Dates that are still pending stay non-terminal so the interval remains
    incomplete instead of being filled in as absent.
    """

    successes: dict[str, Mapping[str, Any]] = {}
    for row in raw_records:
        if str(row.get("anchor_id")) != anchor_id:
            continue
        if str(row.get("stage")) != "first_pass":
            continue
        if str(row.get("arm") or "t3") != arm:
            continue
        if str(row.get("call_status")) != "ok":
            continue
        successes.setdefault(str(row["capture_date"])[:10], row)
    observations: list[dict[str, Any]] = []
    for row in sorted(
        (item for item in candidates if str(item["anchor_id"]) == anchor_id),
        key=lambda item: str(item["capture_date"]),
    ):
        day = str(row["capture_date"])[:10]
        success = successes.get(day)
        if success is not None:
            observations.append(
                {
                    "capture_date": day,
                    "verdict": success.get("verdict"),
                    "call_status": "ok",
                    "terminal": 1,
                    "reference_only": 0,
                    "quality_flag": success.get("quality_flag"),
                    "target_match": success.get("target_match"),
                    "version": row.get("version", ""),
                    "provider": row.get("provider", ""),
                }
            )
            continue
        status = str(row.get("terminal_state") or "")
        if status in GAP_CALL_STATUSES:
            observations.append(
                {
                    "capture_date": day,
                    "verdict": "",
                    "call_status": status,
                    "terminal": 1,
                    "reference_only": 0,
                    "quality_flag": "",
                    "target_match": None,
                    "version": row.get("version", ""),
                    "provider": row.get("provider", ""),
                }
            )
            continue
        observations.append(
            {
                "capture_date": day,
                "verdict": "",
                "call_status": "",
                "terminal": 0,
                "reference_only": 0,
                "quality_flag": "",
                "target_match": None,
                "version": row.get("version", ""),
                "provider": row.get("provider", ""),
            }
        )
    return observations


def recompute_intervals_from_raw(
    raw_records: Sequence[Mapping[str, Any]],
    anchors: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build raw intervals from first-pass labels only."""

    by_anchor: dict[str, list[Mapping[str, Any]]] = {}
    for row in raw_records:
        if str(row.get("stage")) != "first_pass":
            continue
        if str(row.get("arm") or "t3") not in {"t3", "candidate_only"}:
            continue
        by_anchor.setdefault(str(row["anchor_id"]), []).append(row)
    expected: dict[str, list[str]] = {}
    for row in candidates:
        expected.setdefault(str(row["anchor_id"]), []).append(str(row["capture_date"])[:10])
    intervals: list[dict[str, Any]] = []
    for anchor in anchors:
        anchor_id = str(anchor["anchor_id"])
        observations = []
        for row in by_anchor.get(anchor_id, []):
            observations.append(
                {
                    "capture_date": str(row["capture_date"])[:10],
                    "verdict": row.get("verdict"),
                    "call_status": row.get("call_status"),
                    "terminal": 1,
                    "reference_only": row.get("reference_only", 0),
                    "quality_flag": row.get("quality_flag"),
                    "target_match": row.get("target_match"),
                }
            )
        # Candidates that never became a model call still need a terminal row
        # supplied by the caller inside raw or as synthetic terminals. Missing
        # dates stay incomplete rather than absent.
        interval = infer_interval(
            observations,
            expected_dates=expected.get(anchor_id, []),
            aerial_pv=str(anchor.get("aerial_pv") or "unknown"),
            aerial_date=str(anchor.get("census_date") or POLICY_CENSUS_DATE),
            aerial_precision=str(anchor.get("census_date_precision") or POLICY_CENSUS_PRECISION),
        )
        interval["anchor_id"] = anchor_id
        interval["arm"] = "t3"
        intervals.append(interval)
    return intervals
