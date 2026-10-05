"""Build T3 and paired T2 requests from the v2 ten-grid image pack.

The historical candidate, the census aerial, and one post-census satellite
frame are read from the pack. A declared image that is missing or empty is
not turned into a model call. The four near-white chips are a non-call status.
"""

from __future__ import annotations

import csv
import hashlib
import os
import shutil
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.temporal.reference_fulltimeline import (
    MODEL_ALIAS,
    PIXEL_UNINFORMATIVE_DATES,
    POLICY_CENSUS_DATE,
    POLICY_CENSUS_PRECISION,
    TERMINAL_NONCALL,
    WINDOW_START,
    assemble_image_roles,
    choose_satellite_reference,
    infer_interval,
    is_pixel_uninformative,
    plan_model_requests,
    prompt_for_arm,
    timeline_observations,
)


V2_PACK = Path.home() / "zasolar_data/geid_temporal/reference_fulltimeline_v2_10grid_20261005"
LITE_PLAN = Path.home() / "zasolar_data/geid_temporal/reference_fulltimeline_lite_10grid_20261004/plan"
V2_RUN_ID = "reference_fulltimeline_v2_10grid_20261005"
EXPECTED_ROOFS = 1114
EXPECTED_CALLABLE = 51212
EXPECTED_PAIRED_ROOFS = 149
EXPECTED_PAIRED_DATES = 7102
EXPECTED_INCOMPLETE = 4555
ATTEMPT_LIMIT = 3


def pack_image_roots(pack: Path) -> dict[str, Path]:
    """Return the three imagery directories the v2 scorer is allowed to read."""

    return {
        "aerial": pack / "imagery" / "aerial",
        "z19": pack / "imagery" / "satellite_a3" / "chips",
        "z18": pack / "imagery" / "satellite_z18",
    }


def assemble_send_request(
    *,
    arm: str,
    anchor_id: str,
    capture_date: str,
    candidate_sha256: str,
    aerial_sha256: str,
    satellite_sha256: str | None,
    candidate_path: str,
    aerial_path: str,
    satellite_path: str,
    satellite_date: str | None,
    grid_id: str,
) -> dict[str, Any]:
    """Attach the image paths whose roles assemble_image_roles just accepted."""

    roles = assemble_image_roles(
        arm=arm,
        candidate_sha256=candidate_sha256,
        aerial_sha256=aerial_sha256,
        satellite_sha256=satellite_sha256,
        degradation="",
        candidate_date=capture_date,
        satellite_date=satellite_date,
    )
    by_role = {
        "candidate": candidate_path,
        "aerial_reference": aerial_path,
        "satellite_reference": satellite_path,
    }
    image_paths = [by_role[role["role"]] for role in roles]
    return {
        "arm": arm,
        "anchor_id": anchor_id,
        "capture_date": capture_date[:10],
        "stage": "first_pass",
        "degradation": "",
        "candidate_sha256": candidate_sha256,
        "aerial_sha256": aerial_sha256,
        "satellite_sha256": satellite_sha256 if arm == "t3" else "",
        "satellite_date": (satellite_date or "")[:10] if arm == "t3" else "",
        "image_paths": image_paths,
        "image_roles": [role["role"] for role in roles],
        "n_images": len(image_paths),
        "prompt": prompt_for_arm(arm),
        "grid_id": grid_id,
    }


def t3_status_for_rows(rows: Sequence[Mapping[str, Any]], *, attempt_limit: int = ATTEMPT_LIMIT) -> str:
    """Return the terminal T3 status for one roof-date, or '' while it is still open.

    present, absent, and unknown come only from a successful model call.
    pixel_uninformative and other non-call statuses are returned unchanged.
    """

    relevant = [
        row
        for row in rows
        if str(row.get("arm") or "") == "t3" and str(row.get("stage") or "") == "first_pass"
    ]
    for row in relevant:
        if str(row.get("call_status") or "") == "ok" and row.get("verdict") in {"present", "absent", "unknown"}:
            return str(row["verdict"])
    for row in relevant:
        status = str(row.get("call_status") or "")
        if status in TERMINAL_NONCALL:
            return status
    failures = [row for row in relevant if str(row.get("call_status") or "") != "ok"]
    if len(failures) >= attempt_limit:
        return str(failures[-1].get("call_status") or "call_failed")
    return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _in_window(day: str) -> bool:
    return WINDOW_START <= day[:10] < POLICY_CENSUS_DATE


def _truthy_flag(value: object) -> bool:
    return str(value or "") in {"1", "true", "True"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    temporary.replace(path)


def validate_inventory(anchors: Sequence[Mapping[str, str]], candidates: Sequence[Mapping[str, str]]) -> None:
    """Stop before any request if this is not the frozen ten-grid list."""

    if len(anchors) != EXPECTED_ROOFS:
        raise SystemExit(f"anchor count {len(anchors)} != {EXPECTED_ROOFS}")
    callable_rows = [
        row for row in candidates if _truthy_flag(row.get("callable")) and _in_window(str(row["capture_date"]))
    ]
    incomplete = [row for row in candidates if not _truthy_flag(row.get("callable"))]
    paired_ids = {row["anchor_id"] for row in anchors if _truthy_flag(row.get("paired"))}
    paired_dates = [row for row in callable_rows if row["anchor_id"] in paired_ids]
    if len(callable_rows) != EXPECTED_CALLABLE:
        raise SystemExit(f"callable dates {len(callable_rows)} != {EXPECTED_CALLABLE}")
    if len(incomplete) != EXPECTED_INCOMPLETE:
        raise SystemExit(f"incomplete-coverage dates {len(incomplete)} != {EXPECTED_INCOMPLETE}")
    if len(paired_ids) != EXPECTED_PAIRED_ROOFS:
        raise SystemExit(f"paired roofs {len(paired_ids)} != {EXPECTED_PAIRED_ROOFS}")
    if len(paired_dates) != EXPECTED_PAIRED_DATES:
        raise SystemExit(f"paired dates {len(paired_dates)} != {EXPECTED_PAIRED_DATES}")


def smoke_selection(
    anchors: Sequence[Mapping[str, str]],
    candidates: Sequence[Mapping[str, str]],
) -> set[tuple[str, str]]:
    """Two paired development roofs, earliest and latest callable date on each."""

    chosen = sorted(
        (
            row["anchor_id"]
            for row in anchors
            if str(row.get("grid_role") or "") == "development" and _truthy_flag(row.get("paired"))
        )
    )[:2]
    if len(chosen) != 2:
        raise SystemExit("smoke needs two paired development roofs")
    by_anchor: dict[str, list[str]] = defaultdict(list)
    for row in candidates:
        if not _truthy_flag(row.get("callable")) or not _in_window(str(row["capture_date"])):
            continue
        by_anchor[row["anchor_id"]].append(str(row["capture_date"])[:10])
    keys: set[tuple[str, str]] = set()
    for anchor_id in chosen:
        dates = sorted(set(by_anchor[anchor_id]))
        if len(dates) < 2:
            raise SystemExit(f"smoke roof {anchor_id} has fewer than two callable dates")
        keys.add((anchor_id, dates[0]))
        keys.add((anchor_id, dates[-1]))
    return keys


def _load_cached_sha(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    return {row["key"]: row for row in _read_csv(path)}


def _store_cached_sha(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    _write_csv(path, rows, ["key", "sha256", "nbytes", "path"])


def _sha_for(path: Path, cache: dict[str, dict[str, str]]) -> str:
    size = path.stat().st_size
    prior = cache.get(str(path))
    if prior and int(prior.get("nbytes") or 0) == size and prior.get("sha256"):
        return str(prior["sha256"])
    return _sha256(path)


def load_image_index(pack: Path) -> dict[str, dict[Any, dict[str, Any]]]:
    """Index aerials and satellite chips. z19 wins when both zooms exist."""

    roots = pack_image_roots(pack)
    chips: dict[tuple[str, str], dict[str, Any]] = {}
    qa_path = pack / "imagery" / "satellite_a3" / "qa" / "chip_qa.csv"
    with qa_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            path = Path(row.get("path") or "")
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            sha = str(row.get("actual_sha256") or "")
            if row.get("hash_status") != "pass" or not sha:
                sha = _sha256(path)
            chips[(row["anchor_id"], str(row["capture_date"])[:10])] = {
                "path": path,
                "sha256": sha,
                "nbytes": path.stat().st_size,
            }
    z18_cache_path = pack / "imagery" / "z18_sha256.csv"
    z18_cache = _load_cached_sha(z18_cache_path)
    z18_rows: list[dict[str, str]] = []
    z18_changed = False
    # Layout is {anchor}/z18/{anchor}_{YYYYMMDD}_vnoversion.tif, plus a .tfw sidecar.
    for path in sorted(roots["z18"].glob("*/*/*.tif")):
        parts = path.stem.rsplit("_", 2)
        if len(parts) != 3 or len(parts[1]) != 8 or not parts[1].isdigit():
            continue
        day = f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:8]}"
        key = (parts[0], day)
        if key in chips:
            continue
        size = path.stat().st_size
        if size <= 0:
            continue
        prior = z18_cache.get(str(path))
        if prior and int(prior.get("nbytes") or 0) == size and prior.get("sha256"):
            sha = str(prior["sha256"])
        else:
            sha = _sha256(path)
            z18_changed = True
        chips[key] = {"path": path, "sha256": sha, "nbytes": size}
        z18_rows.append({"key": str(path), "sha256": sha, "nbytes": str(size), "path": str(path)})
    if z18_changed or not z18_cache_path.is_file():
        _store_cached_sha(z18_cache_path, z18_rows)
    aerial_cache_path = pack / "imagery" / "aerial_sha256.csv"
    aerial_cache = _load_cached_sha(aerial_cache_path)
    aerial: dict[str, dict[str, Any]] = {}
    aerial_rows: list[dict[str, str]] = []
    aerial_changed = False
    for path in sorted(roots["aerial"].glob("*.tif")):
        size = path.stat().st_size
        if size <= 0:
            continue
        prior = aerial_cache.get(str(path))
        if prior and int(prior.get("nbytes") or 0) == size and prior.get("sha256"):
            sha = str(prior["sha256"])
        else:
            sha = _sha256(path)
            aerial_changed = True
        aerial[path.stem] = {"path": path, "sha256": sha, "nbytes": size}
        aerial_rows.append({"key": str(path), "sha256": sha, "nbytes": str(size), "path": str(path)})
    if aerial_changed or not aerial_cache_path.is_file():
        _store_cached_sha(aerial_cache_path, aerial_rows)
    return {"chips": chips, "aerial": aerial}


def _write_png(source: Path, dest: Path) -> None:
    import numpy as np
    import rasterio
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + f".{os.getpid()}.tmp")
    with rasterio.open(source) as dataset:
        array = dataset.read()
    if array.shape[0] == 1:
        array = np.repeat(array, 3, axis=0)
    rgb = np.transpose(array[:3], (1, 2, 0))
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    Image.fromarray(rgb, mode="RGB").save(temporary, format="PNG")
    temporary.replace(dest)


def ensure_png(source: Path, source_sha: str, cache: Path) -> tuple[Path, str, int]:
    """Return a PNG with the same pixel grid as the GeoTIFF, plus its sha256."""

    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"{source_sha}.png"
    sha_path = cache / f"{source_sha}.png.sha"
    if not dest.is_file() or dest.stat().st_size <= 0:
        _write_png(source, dest)
        if sha_path.exists():
            sha_path.unlink()
    if sha_path.is_file():
        png_sha = sha_path.read_text(encoding="utf-8").strip()
    else:
        png_sha = _sha256(dest)
        sha_path.write_text(png_sha + "\n", encoding="utf-8")
    return dest, png_sha, dest.stat().st_size


def _materialize(images: Sequence[tuple[Path, str]], cache: Path) -> None:
    unique: dict[str, Path] = {}
    for path, sha in images:
        unique.setdefault(sha, path)

    def _one(item: tuple[str, Path]) -> None:
        sha, path = item
        ensure_png(path, sha, cache)

    items = list(unique.items())
    if not items:
        return
    cache.mkdir(parents=True, exist_ok=True)
    floor = 3 * 1024**3
    for start in range(0, len(items), 200):
        free = shutil.disk_usage(cache).free
        if free < floor:
            raise SystemExit(f"free disk {free} bytes is below 3 GiB; PNG cache left intact")
        chunk = items[start : start + 200]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(_one, chunk, chunksize=8))
        if start and start % 2000 == 0:
            print(f"[v2] png {start}/{len(items)}", file=sys.stderr, flush=True)


def _noncall(anchor: Mapping[str, str], day: str, status: str) -> dict[str, Any]:
    return {
        "arm": "t3",
        "anchor_id": anchor["anchor_id"],
        "capture_date": day,
        "stage": "first_pass",
        "call_status": status,
        "verdict": None,
        "model_version": "",
        "fingerprint": "",
        "candidate_sha256": "",
        "aerial_sha256": "",
        "satellite_sha256": "",
        "degradation": "",
        "reference_only": 0,
        "n_images": 0,
        "image_bytes": [],
        "image_roles": [],
        "image_paths": [],
        "reason": status,
    }


def build_job(
    pack: Path,
    plan: Mapping[str, Sequence[Mapping[str, str]]],
    *,
    only: set[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return non-call records and model requests. Model requests already have paths."""

    validate_inventory(plan["anchors"], plan["candidates"])
    index = load_image_index(pack)
    chips: dict[tuple[str, str], dict[str, Any]] = index["chips"]
    aerials: dict[str, dict[str, Any]] = index["aerial"]
    dates_for: dict[str, set[str]] = defaultdict(set)
    for anchor_id, day in chips:
        dates_for[anchor_id].add(day)
    references: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in plan["references"]:
        if str(row.get("kind") or "") == "satellite":
            references[row["anchor_id"]].append(row)
    anchors = {row["anchor_id"]: row for row in plan["anchors"]}
    paired_ids = {row["anchor_id"] for row in plan["anchors"] if _truthy_flag(row.get("paired"))}
    noncalls: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []
    to_png: list[tuple[Path, str]] = []
    for candidate in plan["candidates"]:
        anchor_id = candidate["anchor_id"]
        day = str(candidate["capture_date"])[:10]
        if not _truthy_flag(candidate.get("callable")) or not _in_window(day):
            continue
        if only is not None and (anchor_id, day) not in only:
            continue
        anchor = anchors[anchor_id]
        if is_pixel_uninformative(anchor_id, day):
            noncalls.append(_noncall(anchor, day, "pixel_uninformative"))
            continue
        chip = chips.get((anchor_id, day))
        aerial = aerials.get(anchor_id)
        choice = choose_satellite_reference(day, references.get(anchor_id, []), available_dates=dates_for[anchor_id])
        reference = choice.get("reference")
        ref_chip = None
        if reference is not None:
            ref_day = str(reference["capture_date"])[:10]
            ref_chip = chips.get((anchor_id, ref_day))
            if ref_chip is not None and chip is not None and ref_chip["sha256"] == chip["sha256"]:
                reduced = set(dates_for[anchor_id]) - {ref_day}
                choice = choose_satellite_reference(
                    day, references.get(anchor_id, []), available_dates=reduced
                )
                reference = choice.get("reference")
                ref_chip = chips.get((anchor_id, str(reference["capture_date"])[:10])) if reference else None
        if chip is None or aerial is None or ref_chip is None or reference is None:
            status = "no_post_census_reference" if chip is not None and aerial is not None else "image_missing"
            noncalls.append(_noncall(anchor, day, status))
            continue
        to_png.append((chip["path"], chip["sha256"]))
        to_png.append((aerial["path"], aerial["sha256"]))
        to_png.append((ref_chip["path"], ref_chip["sha256"]))
        prepared.append(
            {
                "anchor": anchor,
                "day": day,
                "chip": chip,
                "aerial": aerial,
                "reference": reference,
                "ref_chip": ref_chip,
            }
        )
    cache = pack / "imagery" / "request_png"
    _materialize(to_png, cache)
    plan_rows: list[dict[str, Any]] = []
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    for item in prepared:
        anchor = item["anchor"]
        day = item["day"]
        chip_png, chip_sha, _chip_n = ensure_png(item["chip"]["path"], item["chip"]["sha256"], cache)
        air_png, air_sha, _air_n = ensure_png(item["aerial"]["path"], item["aerial"]["sha256"], cache)
        ref_png, ref_sha, _ref_n = ensure_png(item["ref_chip"]["path"], item["ref_chip"]["sha256"], cache)
        ref_day = str(item["reference"]["capture_date"])[:10]
        if chip_sha == ref_sha or ref_day == day:
            noncalls.append(_noncall(anchor, day, "no_post_census_reference"))
            continue
        plan_rows.append(
            {
                "anchor_id": anchor["anchor_id"],
                "capture_date": day,
                "callable": 1,
                "terminal_state": "",
                "candidate_sha256": chip_sha,
                "aerial_sha256": air_sha,
                "satellite_sha256": ref_sha,
            }
        )
        meta[(anchor["anchor_id"], day)] = {
            "anchor": anchor,
            "chip_png": chip_png,
            "air_png": air_png,
            "ref_png": ref_png,
            "chip_sha": chip_sha,
            "air_sha": air_sha,
            "ref_sha": ref_sha,
            "ref_day": ref_day,
        }
    planned = plan_model_requests(plan_rows, paired_anchor_ids=paired_ids)
    requests: list[dict[str, Any]] = []
    for row in planned:
        info = meta[(row["anchor_id"], row["capture_date"])]
        anchor = info["anchor"]
        arm = str(row["arm"])
        try:
            request = assemble_send_request(
                arm=arm,
                anchor_id=anchor["anchor_id"],
                capture_date=row["capture_date"],
                candidate_sha256=info["chip_sha"],
                aerial_sha256=info["air_sha"],
                satellite_sha256=info["ref_sha"] if arm == "t3" else None,
                candidate_path=str(info["chip_png"]),
                aerial_path=str(info["air_png"]),
                satellite_path=str(info["ref_png"]) if arm == "t3" else "",
                satellite_date=info["ref_day"] if arm == "t3" else None,
                grid_id=str(anchor.get("grid_id") or ""),
            )
        except ValueError:
            noncalls.append(_noncall(anchor, row["capture_date"], "not_sent"))
            continue
        requests.append(request)
    sent_t3 = {(row["anchor_id"], row["capture_date"]) for row in requests if row["arm"] == "t3"}
    for item in prepared:
        key = (item["anchor"]["anchor_id"], item["day"])
        if key in sent_t3 or key in {(row["anchor_id"], row["capture_date"]) for row in noncalls}:
            continue
        noncalls.append(_noncall(item["anchor"], item["day"], "not_sent"))
    return noncalls, requests


def _raw_by_key(raw_rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], list[Mapping[str, Any]]]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        grouped[(str(row.get("arm") or ""), str(row.get("anchor_id") or ""), str(row.get("capture_date") or "")[:10])].append(row)
    return grouped


def _ok_row(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for row in rows:
        if str(row.get("stage") or "") == "first_pass" and str(row.get("call_status") or "") == "ok":
            return row
    return None


def write_products(
    pack: Path,
    plan: Mapping[str, Sequence[Mapping[str, str]]],
    raw_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Write the status table, the paired comparison, and T3 intervals."""

    grouped = _raw_by_key(raw_rows)
    anchors = {row["anchor_id"]: row for row in plan["anchors"]}
    paired_ids = {row["anchor_id"] for row in plan["anchors"] if _truthy_flag(row.get("paired"))}
    status_rows: list[dict[str, Any]] = []
    paired_rows: list[dict[str, Any]] = []
    for candidate in plan["candidates"]:
        anchor_id = candidate["anchor_id"]
        day = str(candidate["capture_date"])[:10]
        if not _in_window(day):
            continue
        anchor = anchors[anchor_id]
        if not _truthy_flag(candidate.get("callable")):
            continue
        t3_rows = grouped.get(("t3", anchor_id, day), [])
        status = t3_status_for_rows(t3_rows)
        ok = _ok_row(t3_rows)
        status_rows.append(
            {
                "anchor_id": anchor_id,
                "grid_id": anchor.get("grid_id", ""),
                "grid_role": anchor.get("grid_role", ""),
                "capture_date": day,
                "t3_status": status,
                "t3_verdict": (ok or {}).get("verdict") or "",
                "n_images": (ok or {}).get("n_images", ""),
                "model_version": (ok or {}).get("model_version") or "",
                "candidate_sha256": (ok or {}).get("candidate_sha256") or "",
                "aerial_sha256": (ok or {}).get("aerial_sha256") or "",
                "satellite_sha256": (ok or {}).get("satellite_sha256") or "",
                "satellite_date": (ok or {}).get("satellite_date") or "",
            }
        )
        if anchor_id not in paired_ids:
            continue
        t2_rows = grouped.get(("t2", anchor_id, day), [])
        t2_ok = _ok_row(t2_rows)
        both = (
            ok is not None
            and t2_ok is not None
            and ok.get("verdict") in {"present", "absent", "unknown"}
            and t2_ok.get("verdict") in {"present", "absent", "unknown"}
        )
        candidate_agree = int(both and ok.get("candidate_sha256") == t2_ok.get("candidate_sha256"))
        aerial_agree = int(both and ok.get("aerial_sha256") == t2_ok.get("aerial_sha256"))
        paired_rows.append(
            {
                "anchor_id": anchor_id,
                "grid_id": anchor.get("grid_id", ""),
                "grid_role": anchor.get("grid_role", ""),
                "capture_date": day,
                "t3_status": status,
                "t3_verdict": (ok or {}).get("verdict") or "",
                "t2_verdict": (t2_ok or {}).get("verdict") or "",
                "in_effect_comparison": int(bool(both)),
                "candidate_bytes_agree": candidate_agree if both else "",
                "aerial_bytes_agree": aerial_agree if both else "",
                "candidate_sha256_t3": (ok or {}).get("candidate_sha256") or "",
                "candidate_sha256_t2": (t2_ok or {}).get("candidate_sha256") or "",
                "aerial_sha256_t3": (ok or {}).get("aerial_sha256") or "",
                "aerial_sha256_t2": (t2_ok or {}).get("aerial_sha256") or "",
            }
        )
    by_anchor_raw: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        by_anchor_raw[str(row.get("anchor_id") or "")].append(row)
    by_anchor_candidates: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for candidate in plan["candidates"]:
        day = str(candidate["capture_date"])[:10]
        if _truthy_flag(candidate.get("callable")) and _in_window(day):
            by_anchor_candidates[candidate["anchor_id"]].append(candidate)
    intervals: list[dict[str, Any]] = []
    for anchor in plan["anchors"]:
        anchor_id = anchor["anchor_id"]
        prepared_candidates = []
        for candidate in by_anchor_candidates[anchor_id]:
            item = dict(candidate)
            day = str(candidate["capture_date"])[:10]
            status = t3_status_for_rows(grouped.get(("t3", anchor_id, day), []))
            item["terminal_state"] = "" if status in {"present", "absent", "unknown"} else status
            prepared_candidates.append(item)
        observations = timeline_observations(
            prepared_candidates,
            by_anchor_raw.get(anchor_id, []),
            anchor_id=anchor_id,
            arm="t3",
        )
        interval = infer_interval(
            observations,
            expected_dates=[row["capture_date"] for row in prepared_candidates],
            aerial_pv="unknown",
            aerial_date=str(anchor.get("census_date") or POLICY_CENSUS_DATE),
            aerial_precision=str(anchor.get("census_date_precision") or POLICY_CENSUS_PRECISION),
        )
        interval["anchor_id"] = anchor_id
        interval["grid_id"] = anchor.get("grid_id", "")
        interval["grid_role"] = anchor.get("grid_role", "")
        if interval["status"] == "bounded":
            interval["interval_start_precision"] = "catalog_day"
            interval["interval_end_precision"] = "catalog_day"
            interval["install_relation"] = "between_last_absent_and_first_present"
        elif interval["status"] == "left_censored":
            interval["interval_start_precision"] = ""
            interval["interval_end_precision"] = "catalog_day"
            interval["install_relation"] = "earlier_than_first_visible_date"
        elif interval["status"] == "conflict_pending_review":
            interval["interval_start_precision"] = ""
            interval["interval_end_precision"] = ""
            interval["install_relation"] = "conflict_not_repaired"
        else:
            interval["interval_start_precision"] = ""
            interval["interval_end_precision"] = ""
            interval["install_relation"] = ""
        intervals.append(interval)
    summary: dict[str, Counter[str]] = defaultdict(Counter)
    for row in intervals:
        role = str(row.get("grid_role") or "")
        summary[role]["n"] += 1
        summary[role][str(row.get("status") or "")] += 1
    summary_rows = []
    for role in ("development", "evaluation"):
        counts = summary[role]
        summary_rows.append({"grid_role": role, "n_roofs": counts["n"], **{key: counts[key] for key in sorted(counts) if key != "n"}})
    results = pack / "results"
    _write_csv(
        results / "t3_status.csv",
        status_rows,
        [
            "anchor_id",
            "grid_id",
            "grid_role",
            "capture_date",
            "t3_status",
            "t3_verdict",
            "n_images",
            "model_version",
            "candidate_sha256",
            "aerial_sha256",
            "satellite_sha256",
            "satellite_date",
        ],
    )
    _write_csv(
        results / "paired_comparison.csv",
        paired_rows,
        [
            "anchor_id",
            "grid_id",
            "grid_role",
            "capture_date",
            "t3_status",
            "t3_verdict",
            "t2_verdict",
            "in_effect_comparison",
            "candidate_bytes_agree",
            "aerial_bytes_agree",
            "candidate_sha256_t3",
            "candidate_sha256_t2",
            "aerial_sha256_t3",
            "aerial_sha256_t2",
        ],
    )
    interval_fields = [
        "anchor_id",
        "grid_id",
        "grid_role",
        "status",
        "censoring",
        "install_date",
        "install_relation",
        "interval_start",
        "interval_start_precision",
        "interval_end",
        "interval_end_precision",
        "last_absent_date",
        "earliest_present_date",
        "conflict_frames",
        "undated_reason",
        "n_present",
        "n_absent",
        "n_unknown",
        "n_call_failed",
        "labels_modified",
    ]
    _write_csv(results / "intervals_t3.csv", intervals, interval_fields)
    role_fields = ["grid_role", "n_roofs"] + sorted({key for row in summary_rows for key in row if key not in {"grid_role", "n_roofs"}})
    _write_csv(results / "interval_summary_by_role.csv", summary_rows, role_fields)
    effect = [row for row in paired_rows if str(row["in_effect_comparison"]) == "1"]
    candidate_agreement = (
        sum(str(row["candidate_bytes_agree"]) == "1" for row in effect) / len(effect) if effect else 0
    )
    aerial_agreement = sum(str(row["aerial_bytes_agree"]) == "1" for row in effect) / len(effect) if effect else 0
    return {
        "n_status": len(status_rows),
        "n_terminal": sum(1 for row in status_rows if row["t3_status"]),
        "n_paired": len(paired_rows),
        "effect_denominator": len(effect),
        "candidate_byte_agreement": candidate_agreement,
        "aerial_byte_agreement": aerial_agreement,
        "summary": {role: dict(summary[role]) for role in summary},
    }


def audit_text(pack: Path, plan: Mapping[str, Sequence[Mapping[str, str]]], raw_rows: Sequence[Mapping[str, Any]]) -> str:
    """Read-only check of the raw ledger and the written tables."""

    status_rows = _read_csv(pack / "results" / "t3_status.csv")
    paired_rows = _read_csv(pack / "results" / "paired_comparison.csv")
    intervals = _read_csv(pack / "results" / "intervals_t3.csv")
    role_rows = _read_csv(pack / "results" / "interval_summary_by_role.csv")
    terminal = [row for row in status_rows if row.get("t3_status")]
    one_image = 0
    versions: set[str] = set()
    sample: Mapping[str, Any] | None = None
    post_census_violations = 0
    for row in raw_rows:
        if str(row.get("arm") or "") != "t3" or str(row.get("stage") or "") != "first_pass":
            continue
        if str(row.get("call_status") or "") != "ok":
            continue
        if row.get("verdict") not in {"present", "absent", "unknown"}:
            continue
        versions.add(str(row.get("model_version") or ""))
        n_images = int(row.get("n_images") or 0)
        if n_images == 1:
            one_image += 1
        satellite_date = str(row.get("satellite_date") or "")[:10]
        if satellite_date and satellite_date <= POLICY_CENSUS_DATE:
            post_census_violations += 1
        if sample is None and n_images == 3:
            sample = row
    if sample is None:
        raise SystemExit("audit found no successful three-image T3 request")
    sizes = [int(value) for value in sample.get("image_bytes") or []]
    paths = [Path(value) for value in sample.get("image_paths") or []]
    reopened = [path.stat().st_size if path.is_file() else 0 for path in paths]
    effect = [row for row in paired_rows if row.get("in_effect_comparison") == "1"]
    candidate_agreement = (
        1 if effect and all(row.get("candidate_bytes_agree") == "1" for row in effect) else 0
    )
    aerial_agreement = 1 if effect and all(row.get("aerial_bytes_agree") == "1" for row in effect) else 0
    white_lines = []
    status_by = {(row["anchor_id"], row["capture_date"]): row["t3_status"] for row in status_rows}
    for anchor_id, day in sorted(PIXEL_UNINFORMATIVE_DATES):
        white_lines.append(f"white_{anchor_id[-8:]}_{day}={status_by.get((anchor_id, day), 'missing')}")
    incomplete = [row for row in plan["candidates"] if not _truthy_flag(row.get("callable"))]
    coded_absent = 0
    grouped = _raw_by_key(raw_rows)
    for row in incomplete:
        status = t3_status_for_rows(grouped.get(("t3", row["anchor_id"], str(row["capture_date"])[:10]), []))
        if status == "absent":
            coded_absent += 1
    foreign = sorted(version for version in versions if version != MODEL_ALIAS)
    role_text = " ".join(
        f"{row['grid_role']}_n={row['n_roofs']}_" + "_".join(f"{key}={row[key]}" for key in row if key not in {"grid_role", "n_roofs"})
        for row in role_rows
    )
    later_absent = sum(1 for row in intervals if row.get("status") == "conflict_pending_review")
    left_censored = sum(1 for row in intervals if row.get("install_relation") == "earlier_than_first_visible_date")
    repaired = sum(1 for row in intervals if str(row.get("labels_modified") or "0") not in {"0", ""})
    lines = [
        f"terminal_t3_count={len(terminal)}",
        f"one_image_successful_t3_count={one_image}",
        f"sampled_t3_anchor={sample.get('anchor_id')}",
        f"sampled_t3_date={str(sample.get('capture_date') or '')[:10]}",
        f"sampled_t3_n_images={sample.get('n_images')}",
        f"sampled_t3_image_roles={','.join(sample.get('image_roles') or [])}",
        f"sampled_t3_image_bytes={','.join(str(value) for value in sizes)}",
        f"sampled_t3_reopened_bytes={','.join(str(value) for value in reopened)}",
        f"sampled_t3_bytes_nonempty={int(bool(sizes) and all(value > 0 for value in sizes) and sizes == reopened)}",
        f"paired_rows={len(paired_rows)}",
        f"paired_effect_denominator={len(effect)}",
        f"candidate_byte_agreement={candidate_agreement}",
        f"aerial_byte_agreement={aerial_agreement}",
        *white_lines,
        f"model_identity={MODEL_ALIAS if not foreign and versions == {MODEL_ALIAS} else 'MISMATCH'}",
        f"model_versions={','.join(sorted(versions)) or 'none'}",
        f"foreign_model_versions={len(foreign)}",
        f"incomplete_coverage_unscored={len(incomplete)}",
        f"incomplete_coverage_coded_absent={coded_absent}",
        f"post_census_reference_violations={post_census_violations}",
        f"left_censored_earlier_than_first_visible={left_censored}",
        f"conflict_not_repaired={later_absent}",
        f"labels_modified_rows={repaired}",
        f"interval_rows={len(intervals)}",
        f"role_summary={role_text}",
        "accuracy_claim=none",
    ]
    return "\n".join(lines) + "\n"
