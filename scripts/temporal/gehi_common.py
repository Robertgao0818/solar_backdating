from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_GEHI_EXE = Path("/home/gaosh/zasolar_data/tools/GEHistoricalImagery/GEHistoricalImagery")
DEFAULT_PROVIDER = "TM"
DEFAULT_PROBE_ZOOM = 19

INFO_LEVEL_RE = re.compile(r"Level\s*=\s*(?P<level>\d+),\s*Path\s*=\s*(?P<path>[0-3]+)")
INFO_DATE_RE = re.compile(r"date\s*=\s*(?P<date>\d{4}/\d{2}/\d{2}),\s*version\s*=\s*(?P<version>\d+)")
AVAIL_DATE_RE = re.compile(r"\[\d+\]\s*(?P<date>\d{4}/\d{2}/\d{2})")
TILE_AVAIL_DATE_RE = re.compile(r"Tile availability on\s+(?P<date>\d{4}/\d{2}/\d{2})")


@dataclass(frozen=True)
class GehiRunResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def command(self) -> str:
        return " ".join(shlex.quote(part) for part in self.args)

    @property
    def stdout_sha256(self) -> str:
        return hashlib.sha256(self.stdout.encode("utf-8")).hexdigest()

    @property
    def stderr_sha256(self) -> str:
        return hashlib.sha256(self.stderr.encode("utf-8")).hexdigest()


def decode_gehi_output(raw: bytes) -> str:
    """Decode GEHistoricalImagery output captured from subprocess pipes.

    The `availability` command switches Console.OutputEncoding to UTF-16LE and
    then enters an interactive chooser. `info` normally emits UTF-8. Decode both
    forms and normalize NUL-padded output for stable parsing.
    """
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-16le", errors="replace")
    if text.count("\x00") > max(1, len(text) // 20):
        text = raw.decode("utf-16le", errors="replace")
    return text.replace("\x00", "")


def run_gehi(args: Sequence[object], *, executable: Path = DEFAULT_GEHI_EXE, timeout: float = 300.0) -> GehiRunResult:
    cmd = [str(executable), *[str(arg) for arg in args]]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    return GehiRunResult(
        args=tuple(cmd),
        returncode=proc.returncode,
        stdout=decode_gehi_output(proc.stdout),
        stderr=decode_gehi_output(proc.stderr),
    )


def parse_gehi_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y/%m/%d").date()


def iso_to_gehi_date(value: object) -> str:
    text = str(value).strip()[:10]
    parsed = datetime.strptime(text, "%Y-%m-%d").date()
    return parsed.strftime("%Y/%m/%d")


def anchor_location_arg(anchor: Mapping[str, object]) -> str:
    lat = float(anchor["centroid_lat"])
    lon = float(anchor["centroid_lon"])
    return f"{lat:.10f},{lon:.10f}"


def anchor_bbox_args(anchor: Mapping[str, object]) -> tuple[str, str]:
    """Return GEHI lower-left and upper-right args in LAT,LONG order."""
    lon_min = min(float(anchor["chip_lon_min"]), float(anchor["chip_lon_max"]))
    lon_max = max(float(anchor["chip_lon_min"]), float(anchor["chip_lon_max"]))
    lat_min = min(float(anchor["chip_lat_min"]), float(anchor["chip_lat_max"]))
    lat_max = max(float(anchor["chip_lat_min"]), float(anchor["chip_lat_max"]))
    lower_left = f"{lat_min:.10f},{lon_min:.10f}"
    upper_right = f"{lat_max:.10f},{lon_max:.10f}"
    return lower_left, upper_right


def parse_info_output(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    current_level: int | None = None
    current_path = ""
    for line in text.splitlines():
        level_match = INFO_LEVEL_RE.search(line)
        if level_match:
            current_level = int(level_match.group("level"))
            current_path = level_match.group("path")
            continue
        date_match = INFO_DATE_RE.search(line)
        if date_match and current_level is not None:
            rows.append(
                {
                    "zoom": current_level,
                    "path": current_path,
                    "capture_date": parse_gehi_date(date_match.group("date")).isoformat(),
                    "version": int(date_match.group("version")),
                }
            )
    return rows


def parse_availability_output(text: str) -> list[str]:
    dates = {parse_gehi_date(m.group("date")).isoformat() for m in AVAIL_DATE_RE.finditer(text)}
    dates.update(parse_gehi_date(m.group("date")).isoformat() for m in TILE_AVAIL_DATE_RE.finditer(text))
    return sorted(dates)


def dedupe_info_rows_by_date(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Deduplicate GEHI info rows by (anchor_id, capture_date).

    Each distinct capture_date is preserved as one vintage row regardless of
    Google's internal `version` label. GEHI returns multiple capture_dates
    sharing a single version (e.g. v296 spans 2010-06 through 2021-04 in JHB
    CBD); each labeled date is a distinct timestamp the user cares about,
    so collapsing them by version drops most of the vintage list and breaks
    bisection / progressive walk_back. If multiple rows share an exact
    (anchor_id, capture_date), keep the lowest version number for stable
    ordering.
    """
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in rows:
        key = (str(row.get("anchor_id", "")), str(row["capture_date"]))
        grouped.setdefault(key, []).append(row)

    out: list[dict[str, object]] = []
    for (_anchor_id, _capture_date), items in sorted(
        grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])
    ):
        sorted_items = sorted(items, key=lambda item: int(item["version"]))
        first = dict(sorted_items[0])
        versions = sorted({int(item["version"]) for item in sorted_items})
        first["capture_date_min"] = first["capture_date"]
        first["capture_date_max"] = first["capture_date"]
        first["all_capture_dates"] = first["capture_date"]
        first["n_date_labels"] = 1
        first["version_dedupe_key"] = f"{first.get('anchor_id', '')}:{first['capture_date']}"
        first["all_versions"] = ";".join(str(v) for v in versions)
        first["n_versions_at_date"] = len(versions)
        out.append(first)
    return out


# Backward-compat alias for any callers still importing the old name.
dedupe_info_rows_by_version = dedupe_info_rows_by_date


def _draw_anchor_marker(
    img,
    *,
    color: tuple[int, int, int] = (255, 215, 0),
    ring_radius_pct: float = 0.18,
    ring_stroke: int = 3,
    inner_arm_pct: float = 0.04,
    inner_thickness: int = 2,
) -> None:
    """Draw a yellow ring + tiny + at the chip center marking the anchor extent.

    The ring covers ~36% of the chip diameter (radius = 18% of short side) to
    approximate the anchor's source_area_m2 footprint (~6-7m radius for a typical
    100-150 m² installation in a 36m × 36m chip). This widens the judgment area
    from a single pixel to the actual install extent, so cases where the centroid
    falls on a roof shadow / aisle between PV arrays no longer get misjudged as
    'absent'.

    Visual contract for the prompt:
      - yellow ring outline = "search this region for PV"
      - small + inside = "this is the centroid (anchor location anchor)"

    Sizes are image-relative so z=19 (~67px) and z=20 (~135px) chips both get
    a legible marker without occluding the roof.

    Rationale (P1 fix, 2026-05-05): single-pixel + marker placed Gemini's
    attention too narrowly. a000003 (PV in 4 distinct arrays around but not at
    centroid) and a000007 (centroid fell on a dark roof shadow with PV around)
    were misjudged as absent. The ring widens to anchor extent without losing
    the precise centroid cue.
    """
    from PIL import ImageDraw

    w, h = img.size
    cx, cy = w // 2, h // 2
    short = min(w, h)
    ring_r = max(6, int(short * ring_radius_pct))
    inner_arm = max(3, int(short * inner_arm_pct))
    inner_half_t = max(1, inner_thickness // 2) if inner_thickness >= 2 else 0
    draw = ImageDraw.Draw(img)
    # Ring outline (anchor extent)
    for offset in range(ring_stroke):
        r = ring_r - offset
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color)
    # Inner + at centroid
    draw.rectangle(
        [cx - inner_arm, cy - inner_half_t - 0, cx + inner_arm, cy + inner_half_t + (1 if inner_half_t == 0 else 0)],
        fill=color,
    )
    draw.rectangle(
        [cx - inner_half_t - 0, cy - inner_arm, cx + inner_half_t + (1 if inner_half_t == 0 else 0), cy + inner_arm],
        fill=color,
    )


def ensure_review_png(tif_path: Path, *, anchor_marker: bool = True) -> Path:
    """Convert a GEHI GeoTIFF chip to a sibling PNG suitable for Gemini vision review.

    Gemini's image input accepts PNG/JPEG/WEBP/HEIC/HEIF — not TIFF — so chips
    must be transcoded before being sent. The PNG lives next to the TIFF
    (same stem, .png extension); the TIFF is left untouched as the canonical
    artifact for provenance.

    `anchor_marker=True` (default) draws a small yellow + at the chip center
    so the prompt can explicitly point Gemini at the anchor location. Pass
    False for raw transcoding (e.g., debugging chip alignment).

    Idempotent: if a non-empty PNG already exists with mtime >= the TIFF's
    mtime, returns it without re-encoding. Non-TIFF inputs (e.g. .jpg) are
    returned unchanged.
    """
    if tif_path.suffix.lower() not in (".tif", ".tiff"):
        return tif_path
    png_path = tif_path.with_suffix(".png")
    try:
        if (
            png_path.exists()
            and png_path.stat().st_size > 0
            and png_path.stat().st_mtime >= tif_path.stat().st_mtime
        ):
            return png_path
    except OSError:
        pass
    from PIL import Image

    with Image.open(tif_path) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        if anchor_marker:
            _draw_anchor_marker(img)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(png_path, format="PNG")
    return png_path


def assert_gehi_success(result: GehiRunResult, *, allow_availability_chooser_exit: bool = False) -> None:
    if result.returncode == 0:
        return
    if allow_availability_chooser_exit and parse_availability_output(result.stdout):
        if "Cannot read keys" in result.stdout or "Cannot read keys" in result.stderr:
            return
    raise RuntimeError(
        f"GEHistoricalImagery failed with return code {result.returncode}: "
        f"{result.stderr[:1000] or result.stdout[:1000]}"
    )

