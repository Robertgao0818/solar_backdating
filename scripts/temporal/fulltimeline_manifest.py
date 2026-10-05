"""Build the scored-manifest rows for one frozen full-timeline pilot.

The manifest is a pure function of the frozen plan, the rendered file index,
and the immutable raw log. It does not download imagery or call a model.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from scripts.temporal.reference_fulltimeline import (
    assign_degradation,
    cache_key,
    choose_satellite_reference,
    plan_model_requests,
    remaining_attempts,
)


ATTEMPT_LIMIT = 3


def _index_raw(
    raw_records: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str, str], list[Mapping[str, Any]]]:
    index: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    for row in raw_records:
        index.setdefault(cache_key(row), []).append(row)
    return index


def _index_render(render_rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in render_rows:
        key = (
            str(row.get("anchor_id") or ""),
            str(row.get("kind") or ""),
            str(row.get("capture_date") or "")[:10],
        )
        index[key] = dict(row)
    return index


def _ok_raw(
    raw_records: Sequence[Mapping[str, Any]],
    *,
    arm: str,
    anchor_id: str,
    capture_date: str,
    stage: str,
) -> Mapping[str, Any] | None:
    for row in raw_records:
        if cache_key(row) != (arm, anchor_id, capture_date[:10], stage):
            continue
        if str(row.get("call_status")) == "ok":
            return row
    return None


def _latest_failure(
    raw_records: Sequence[Mapping[str, Any]],
    *,
    arm: str,
    anchor_id: str,
    capture_date: str,
    stage: str,
) -> str:
    status = ""
    for row in raw_records:
        if cache_key(row) != (arm, anchor_id, capture_date[:10], stage):
            continue
        if str(row.get("call_status")) == "ok":
            return ""
        status = str(row.get("call_status") or "call_failed")
    return status


def _image_usable(
    image: Mapping[str, Any] | None,
    check: Mapping[str, Any] | None,
) -> bool:
    """A readable frame stays usable unless the reference read rejects it.

    A missing file is not usable. A transport failure on the reference read
    does not erase a frame that already passed the file check.
    """

    if image is None:
        return False
    if str(image.get("qa_pass") or "") not in {"1", "true", "True"} and image.get("qa_pass") is not True:
        return False
    if not str(image.get("sha256") or ""):
        return False
    if check is None or str(check.get("call_status")) != "ok":
        return True
    if str(check.get("quality_flag") or "") == "unusable":
        return False
    if check.get("target_match") is False:
        return False
    return True


def _candidate_terminal(
    candidate: Mapping[str, Any],
    image: Mapping[str, Any] | None,
    raw_index: Mapping[tuple[str, str, str, str], Sequence[Mapping[str, Any]]],
    *,
    paired: bool,
    attempt_limit: int,
) -> str:
    if not int(str(candidate.get("callable") or "0") or 0):
        return str(candidate.get("terminal_state") or "not_downloadable")
    if image is None or not str(image.get("source_tif") or ""):
        return "download_failed"
    if not _image_usable(image, None):
        if str(image.get("failure") or "") in {"image_missing", "image_unreadable"}:
            return str(image["failure"])
        return "quality_failed"
    anchor_id = str(candidate["anchor_id"])
    day = str(candidate["capture_date"])[:10]
    arms = ["t3", "t2"] if paired else ["t3"]
    pending = False
    t3_ok = False
    t3_failure = ""
    for arm in arms:
        bucket = raw_index.get((arm, anchor_id, day, "first_pass"), ())
        if _ok_raw(bucket, arm=arm, anchor_id=anchor_id, capture_date=day, stage="first_pass"):
            if arm == "t3":
                t3_ok = True
            continue
        key = (arm, anchor_id, day, "first_pass")
        if remaining_attempts(bucket, key, limit=attempt_limit) > 0:
            pending = True
            continue
        if arm == "t3":
            t3_failure = _latest_failure(
                bucket, arm=arm, anchor_id=anchor_id, capture_date=day, stage="first_pass"
            ) or "call_failed"
    if pending:
        return ""
    if t3_ok:
        return "scored"
    return t3_failure or "call_failed"


def assemble_manifest(
    anchors: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
    render_rows: Sequence[Mapping[str, Any]],
    raw_records: Sequence[Mapping[str, Any]],
    *,
    attempt_limit: int = ATTEMPT_LIMIT,
) -> list[dict[str, Any]]:
    """Return one row per frozen candidate, including dates that will not be called.

    T2 and T3 share the candidate PNG hash. A satellite frame is omitted when
    it is the candidate date or the candidate bytes. Pending rows keep an empty
    terminal state; exhausted model failures stay in the request denominator.
    """

    rendered = _index_render(render_rows)
    raw_index = _index_raw(raw_records)
    aerial_fallback: dict[str, Mapping[str, Any]] = {}
    for (anchor_id, kind, _day), row in rendered.items():
        if kind == "aerial" and anchor_id not in aerial_fallback:
            aerial_fallback[anchor_id] = row
    refs_by_anchor: dict[str, list[Mapping[str, Any]]] = {}
    for row in references:
        refs_by_anchor.setdefault(str(row["anchor_id"]), []).append(row)
    anchors_by_id = {str(row["anchor_id"]): row for row in anchors}
    manifest: list[dict[str, Any]] = []
    for candidate in candidates:
        anchor_id = str(candidate["anchor_id"])
        day = str(candidate["capture_date"])[:10]
        anchor = anchors_by_id.get(anchor_id, {})
        image = rendered.get((anchor_id, "candidate", day))
        aerial = rendered.get((anchor_id, "aerial", str(anchor.get("census_date") or "")[:10]))
        if aerial is None:
            aerial = aerial_fallback.get(anchor_id)
        aerial_day = str((aerial or {}).get("capture_date") or anchor.get("census_date") or "")[:10]
        aerial_check = _ok_raw(
            raw_index.get(("reference_aerial", anchor_id, aerial_day, "reference_check"), ()),
            arm="reference_aerial",
            anchor_id=anchor_id,
            capture_date=aerial_day,
            stage="reference_check",
        )
        aerial_ok = _image_usable(aerial, aerial_check)
        satellite_rows = [row for row in refs_by_anchor.get(anchor_id, []) if str(row.get("kind")) == "satellite"]
        usable_dates: set[str] = set()
        sat_by_date: dict[str, Mapping[str, Any]] = {}
        saw_same_bytes = False
        for ref in satellite_rows:
            ref_day = str(ref.get("capture_date") or "")[:10]
            sat_image = rendered.get((anchor_id, "satellite", ref_day))
            if sat_image is None:
                continue
            sat_check = _ok_raw(
                raw_index.get(("reference_satellite", anchor_id, ref_day, "reference_check"), ()),
                arm="reference_satellite",
                anchor_id=anchor_id,
                capture_date=ref_day,
                stage="reference_check",
            )
            if not _image_usable(sat_image, sat_check):
                continue
            if image is not None and str(sat_image.get("sha256") or "") == str(image.get("sha256") or ""):
                saw_same_bytes = True
                continue
            if ref_day == day:
                continue
            usable_dates.add(ref_day)
            sat_by_date[ref_day] = sat_image
        choice = choose_satellite_reference(day, satellite_rows, available_dates=usable_dates)
        chosen = choice.get("reference")
        chosen_image = sat_by_date.get(str((chosen or {}).get("capture_date") or "")[:10]) if chosen else None
        if chosen_image is None and not choice.get("degradation"):
            code = "self_reference_omitted" if saw_same_bytes or choice.get("self_reference_omitted") else "triplet_unavailable"
            choice = {"reference": None, "degradation": code, "self_reference_omitted": int(code == "self_reference_omitted")}
        degradation = assign_degradation(aerial_usable=aerial_ok, satellite_choice=choice)
        paired = str(anchor.get("paired") or "") in {"1", "true", "True"}
        t2_degradation = "" if aerial_ok else "candidate_only_reference_unavailable"
        terminal = _candidate_terminal(
            candidate,
            image,
            raw_index,
            paired=paired,
            attempt_limit=attempt_limit,
        )
        candidate_sha = str((image or {}).get("sha256") or "") if terminal not in {
            "not_downloadable",
            "download_failed",
            "quality_failed",
            "image_missing",
            "image_unreadable",
        } else ""
        manifest.append(
            {
                "anchor_id": anchor_id,
                "grid_id": str(anchor.get("grid_id") or ""),
                "capture_date": day,
                "version": str(candidate.get("version") or ""),
                "provider": str(candidate.get("provider") or ""),
                "requested_zoom": str(candidate.get("requested_zoom") or ""),
                "callable": int(str(candidate.get("callable") or "0") or 0),
                "terminal_state": terminal,
                "download_choice_reason": str(candidate.get("download_choice_reason") or ""),
                "candidate_sha256": candidate_sha,
                "candidate_png": str((image or {}).get("png_path") or ""),
                "aerial_sha256": str((aerial or {}).get("sha256") or "") if aerial_ok else "",
                "aerial_png": str((aerial or {}).get("png_path") or "") if aerial_ok else "",
                "satellite_sha256": str((chosen_image or {}).get("sha256") or "") if degradation == "" else "",
                "satellite_png": str((chosen_image or {}).get("png_path") or "") if degradation == "" else "",
                "satellite_date": str((chosen or {}).get("capture_date") or "")[:10] if degradation == "" else "",
                "t2_degradation": t2_degradation,
                "t3_degradation": degradation,
                "paired": int(paired),
                "grid_role": str(anchor.get("grid_role") or ""),
                "census_date": str(anchor.get("census_date") or "")[:10],
            }
        )
    return manifest


def scoring_requests(
    manifest: Sequence[Mapping[str, Any]],
    anchors: Sequence[Mapping[str, Any]],
    *,
    extra_paired_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Plan T3 for every model-callable date and T2 for paired anchors.

    ``extra_paired_ids`` adds the technical-check anchors so both arms run
    before the parameter freeze. Image bytes still come from one candidate PNG.
    """

    paired = {
        str(row["anchor_id"])
        for row in anchors
        if str(row.get("paired") or "") in {"1", "true", "True"}
    }
    if extra_paired_ids:
        paired.update(extra_paired_ids)
    requests = plan_model_requests(manifest, paired_anchor_ids=paired)
    by_key = {(str(row["anchor_id"]), str(row["capture_date"])[:10]): row for row in manifest}
    for request in requests:
        source = by_key[(request["anchor_id"], request["capture_date"])]
        request["satellite_date"] = str(source.get("satellite_date") or "")
        request["candidate_png"] = str(source.get("candidate_png") or "")
        request["aerial_png"] = str(source.get("aerial_png") or "")
        request["satellite_png"] = str(source.get("satellite_png") or "")
        request["grid_id"] = str(source.get("grid_id") or "")
        if request["arm"] == "t2":
            request["satellite_sha256"] = ""
            request["satellite_png"] = ""
            request["satellite_date"] = ""
        if request.get("degradation") in {
            "triplet_unavailable",
            "self_reference_omitted",
            "candidate_only_reference_unavailable",
        }:
            request["satellite_sha256"] = ""
            request["satellite_png"] = ""
            request["satellite_date"] = ""
        if request.get("degradation") == "candidate_only_reference_unavailable":
            request["aerial_sha256"] = ""
            request["aerial_png"] = ""
    return requests
