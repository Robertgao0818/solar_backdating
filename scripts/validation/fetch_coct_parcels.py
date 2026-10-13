#!/usr/bin/env python3
"""Download City of Cape Town land parcels, resumably.

Source: ODP_SPLIT_4/MapServer/0 (Land Parcels). Pages are OBJECTID ranges of
at most 2,000 features. A finished page is a gzipped GeoJSON file; a rerun
skips pages that already contain the expected OBJECTIDs. The GeoPackage is
written only after the page count matches the server count.

Run inside tmux for the full download:

    tmux new -d -s cvapp_parcels \\
        'python -u scripts/validation/fetch_coct_parcels.py'

This script only reads the municipal service and writes under the local
work directory. It does not touch Dropbox.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
from shapely.geometry import shape as shapely_shape
from shapely.validation import make_valid

from scripts.validation.cv_appendix_constants import (
    PARCEL_FIELDS,
    PARCEL_PAGE_SIZE,
    PARCEL_QUERY_URL,
    PARCEL_SERVER_COUNT,
    WORK_ROOT,
)

USER_AGENT = "solar-backdating-cv-appendix/1.0 (research; local cache)"
LON_LAT_BOX = (17.5, 20.5, -35.5, -32.5)
_PRINT = threading.Lock()


def page_ranges(n_features: int, page_size: int) -> list[tuple[int, int]]:
    """OBJECTID ranges covering 1..n_features. The live id space is 1..count."""
    if n_features < 1:
        raise ValueError("n_features must be positive")
    if page_size < 1:
        raise ValueError("page_size must be positive")
    ranges = []
    lo = 1
    while lo <= n_features:
        hi = min(lo + page_size - 1, n_features)
        ranges.append((lo, hi))
        lo = hi + 1
    return ranges


def _log(message: str) -> None:
    with _PRINT:
        print(message, flush=True)


def server_count(url: str, timeout: int) -> int:
    query = urllib.parse.urlencode({"where": "1=1", "returnCountOnly": "true", "f": "json"})
    payload = _request(url, query.encode(), timeout)
    body = json.loads(payload)
    if "count" not in body:
        raise RuntimeError(f"parcel count response has no count: {body}")
    return int(body["count"])


def _request(url: str, data: bytes, timeout: int, attempts: int = 6) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
            if raw[:1] in (b"<", b"\n") or raw.lstrip()[:1] == b"<":
                raise RuntimeError("parcel service returned HTML, not JSON")
            return raw
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            delay = min(60, 2 ** attempt)
            _log(f"RETRY attempt={attempt + 1} error={exc} sleep={delay}")
            time.sleep(delay)
    raise RuntimeError(f"parcel request failed after {attempts} tries: {last_error}")


def fetch_page(lo: int, hi: int, timeout: int) -> dict:
    form = {
        "where": f"OBJECTID>={lo} AND OBJECTID<={hi}",
        "outFields": ",".join(PARCEL_FIELDS),
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
        "resultRecordCount": str(PARCEL_PAGE_SIZE),
    }
    raw = _request(PARCEL_QUERY_URL, urllib.parse.urlencode(form).encode(), timeout)
    body = json.loads(raw)
    if body.get("error"):
        raise RuntimeError(f"page {lo}-{hi} error: {body['error']}")
    features = body.get("features") or []
    got = sorted(int(feature["properties"]["OBJECTID"]) for feature in features)
    expected = list(range(lo, hi + 1))
    if got != expected:
        raise RuntimeError(
            f"page {lo}-{hi} returned {len(got)} ids, expected {len(expected)}"
        )
    for feature in features:
        coords = _first_coord(feature.get("geometry"))
        if coords is None:
            raise RuntimeError(f"page {lo}-{hi} has a feature without coordinates")
        lon, lat = coords
        if not (LON_LAT_BOX[0] <= lon <= LON_LAT_BOX[1] and LON_LAT_BOX[2] <= lat <= LON_LAT_BOX[3]):
            raise RuntimeError(f"page {lo}-{hi} coordinate outside Cape Town bbox: {coords}")
        if "ZONING" not in feature["properties"]:
            raise RuntimeError(f"page {lo}-{hi} is missing ZONING")
    return body


def _first_coord(geometry: dict | None) -> tuple[float, float] | None:
    if not geometry:
        return None
    coords = geometry.get("coordinates")
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if not isinstance(coords, list) or len(coords) < 2:
        return None
    return float(coords[0]), float(coords[1])


def page_path(page_dir: Path, lo: int) -> Path:
    return page_dir / f"page_{lo:07d}.geojson.gz"


def page_is_valid(path: Path, lo: int, hi: int) -> bool:
    if not path.exists():
        return False
    try:
        with gzip.open(path, "rt") as handle:
            body = json.load(handle)
        got = sorted(int(feature["properties"]["OBJECTID"]) for feature in body["features"])
        return got == list(range(lo, hi + 1))
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return False


def write_page(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt") as handle:
        json.dump(body, handle)
    temporary.replace(path)


def _to_polygons(geom):
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in {"Polygon", "MultiPolygon"}:
        return geom
    if geom.geom_type == "GeometryCollection":
        parts = [part for part in geom.geoms if part.geom_type in {"Polygon", "MultiPolygon"}]
        if not parts:
            return None
        out = parts[0]
        for part in parts[1:]:
            out = out.union(part)
        return out
    return None


def build_gpkg(page_dir: Path, ranges: list[tuple[int, int]], gpkg: Path) -> dict:
    if gpkg.exists():
        gpkg.unlink()
    repaired = 0
    dropped = 0
    written = 0
    for lo, hi in ranges:
        path = page_path(page_dir, lo)
        with gzip.open(path, "rt") as handle:
            body = json.load(handle)
        records = []
        for feature in body["features"]:
            props = feature["properties"]
            geom = shapely_shape(feature["geometry"])
            original_valid = geom.is_valid
            if not original_valid:
                geom = make_valid(geom)
                repaired += 1
            geom = _to_polygons(geom)
            if geom is None or geom.is_empty or not geom.is_valid:
                dropped += 1
                continue
            records.append(
                {
                    "objectid": int(props["OBJECTID"]),
                    "sg26_code": props.get("SG26_CODE"),
                    "zoning": props.get("ZONING"),
                    "shape_area": props.get("SHAPE_Area"),
                    "geometry": geom,
                }
            )
        frame = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
        if written == 0:
            frame.to_file(gpkg, layer="parcels", driver="GPKG")
        else:
            frame.to_file(gpkg, layer="parcels", driver="GPKG", mode="a")
        written += len(frame)
        _log(f"GPKG pages_through={lo} rows={written}")
    qa = {
        "rows": written,
        "repaired_invalid": repaired,
        "dropped_invalid": dropped,
        "gpkg": str(gpkg),
    }
    return qa


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=WORK_ROOT / "parcels")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--skip-gpkg", action="store_true")
    args = parser.parse_args()

    out_dir = args.out_dir.expanduser()
    page_dir = out_dir / "pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "fetch.log"

    def mirror(message: str) -> None:
        _log(message)
        with log_path.open("a") as handle:
            handle.write(message + "\n")

    count = server_count(PARCEL_QUERY_URL, args.timeout)
    mirror(f"SERVER_COUNT {count}")
    if count != PARCEL_SERVER_COUNT:
        mirror(f"FAILED server count {count} != expected {PARCEL_SERVER_COUNT}")
        return 2
    ranges = page_ranges(count, PARCEL_PAGE_SIZE)
    pending = [(lo, hi) for lo, hi in ranges if not page_is_valid(page_path(page_dir, lo), lo, hi)]
    mirror(f"PAGES total={len(ranges)} pending={len(pending)} workers={args.workers}")

    failures: list[str] = []
    done = len(ranges) - len(pending)
    lock = threading.Lock()

    def one(bounds: tuple[int, int]) -> None:
        nonlocal done
        lo, hi = bounds
        started = time.time()
        body = fetch_page(lo, hi, args.timeout)
        write_page(page_path(page_dir, lo), body)
        with lock:
            done += 1
            current = done
        mirror(
            f"PROGRESS pages={current}/{len(ranges)} lo={lo} hi={hi} "
            f"seconds={time.time() - started:.1f}"
        )

    if pending:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(one, bounds): bounds for bounds in pending}
            for future in as_completed(futures):
                bounds = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 — one bad page must not kill the rest
                    failures.append(f"{bounds}: {exc}")
                    mirror(f"PAGE_FAIL {bounds} {exc}")

    if failures:
        mirror(f"FAILED {len(failures)} pages")
        return 2
    missing = [lo for lo, hi in ranges if not page_is_valid(page_path(page_dir, lo), lo, hi)]
    if missing:
        mirror(f"FAILED missing pages {missing[:8]}")
        return 2

    qa = {"server_count": count, "pages": len(ranges), "page_size": PARCEL_PAGE_SIZE}
    if not args.skip_gpkg:
        gpkg = out_dir / "parcels.gpkg"
        built = build_gpkg(page_dir, ranges, gpkg)
        qa.update(built)
        if built["rows"] != count or built["dropped_invalid"] != 0:
            mirror(f"FAILED gpkg qa {built}")
            return 2
        # Zoning column is required by the plan. Empty strings are allowed;
        # a missing column is not.
        probe = gpd.read_file(gpkg, rows=1)
        if "zoning" not in probe.columns:
            mirror("FAILED gpkg has no zoning column")
            return 2
        sentinel = out_dir / "parcels.gpkg.done"
        sentinel.write_text(json.dumps(qa, indent=2) + "\n")
    (out_dir / "parcels_qa.json").write_text(json.dumps(qa, indent=2) + "\n")
    mirror(f"DONE {json.dumps(qa)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
