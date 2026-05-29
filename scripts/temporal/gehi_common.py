from __future__ import annotations

import hashlib
import json
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


@dataclass(frozen=True)
class ReviewTargetMarker:
    """One labeled target marker for a multi-target chip review PNG."""

    target_id: str
    target_label: str
    offset_x_m: float
    offset_y_m: float
    search_radius_m: float | None = None


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


def _target_marker_cache_token(
    target_markers: Sequence[ReviewTargetMarker],
    *,
    chip_size_m: float,
) -> str:
    payload = {
        "chip_size_m": round(float(chip_size_m), 6),
        "targets": [
            {
                "target_id": marker.target_id,
                "target_label": marker.target_label,
                "offset_x_m": round(float(marker.offset_x_m), 6),
                "offset_y_m": round(float(marker.offset_y_m), 6),
                "search_radius_m": (
                    None
                    if marker.search_radius_m is None
                    else round(float(marker.search_radius_m), 6)
                ),
            }
            for marker in target_markers
        ],
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def target_review_png_path(
    image_path: Path,
    target_markers: Sequence[ReviewTargetMarker],
    *,
    chip_size_m: float,
) -> Path:
    """Return the deterministic multi-target review PNG cache path."""
    token = _target_marker_cache_token(target_markers, chip_size_m=chip_size_m)
    return image_path.with_name(f"{image_path.stem}.targets-{token}.png")


def target_crop_review_png_path(
    image_path: Path,
    target_marker: ReviewTargetMarker,
    *,
    chip_size_m: float,
    crop_context_multiplier: float,
    min_crop_size_m: float,
    min_output_px: int,
) -> Path:
    """Return the deterministic single-target crop review PNG cache path."""
    payload = {
        "chip_size_m": round(float(chip_size_m), 6),
        "crop_context_multiplier": round(float(crop_context_multiplier), 6),
        "min_crop_size_m": round(float(min_crop_size_m), 6),
        "min_output_px": int(min_output_px),
        "target": {
            "target_id": target_marker.target_id,
            "target_label": target_marker.target_label,
            "offset_x_m": round(float(target_marker.offset_x_m), 6),
            "offset_y_m": round(float(target_marker.offset_y_m), 6),
            "search_radius_m": (
                None
                if target_marker.search_radius_m is None
                else round(float(target_marker.search_radius_m), 6)
            ),
        },
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    token = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", target_marker.target_label).strip("_") or "target"
    return image_path.with_name(f"{image_path.stem}.target-{safe_label}-{token}.png")


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _draw_single_target_marker_at(
    img,
    *,
    x: float,
    y: float,
    target_label: str,
    search_radius_px: float | None,
) -> None:
    from PIL import ImageDraw, ImageFont

    w, h = img.size
    short = min(w, h)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    color = (0, 220, 255)
    px = _clamp_int(int(round(x)), 0, max(0, w - 1))
    py = _clamp_int(int(round(y)), 0, max(0, h - 1))
    ring_r = max(6, int(round(search_radius_px))) if search_radius_px else max(6, int(round(short * 0.12)))
    ring_r = min(ring_r, max(6, short // 2 - 2))
    cross_r = max(5, int(round(short * 0.045)))

    for offset in range(3, 0, -1):
        r = ring_r + offset
        draw.ellipse([px - r, py - r, px + r, py + r], outline=(0, 0, 0))
    for offset in range(2):
        r = ring_r - offset
        draw.ellipse([px - r, py - r, px + r, py + r], outline=color)
    draw.line([px - cross_r, py, px + cross_r, py], fill=(0, 0, 0), width=5)
    draw.line([px, py - cross_r, px, py + cross_r], fill=(0, 0, 0), width=5)
    draw.line([px - cross_r, py, px + cross_r, py], fill=color, width=3)
    draw.line([px, py - cross_r, px, py + cross_r], fill=color, width=3)

    label = target_label
    bbox = draw.textbbox((0, 0), label, font=font)
    label_w = bbox[2] - bbox[0] + 8
    label_h = bbox[3] - bbox[1] + 6
    label_x = px + ring_r + 4
    if label_x + label_w >= w:
        label_x = px - ring_r - label_w - 4
    label_y = py - ring_r - label_h - 3
    if label_y < 0:
        label_y = py + ring_r + 3
    label_x = _clamp_int(label_x, 0, max(0, w - label_w - 1))
    label_y = _clamp_int(label_y, 0, max(0, h - label_h - 1))
    draw.rectangle(
        [label_x, label_y, label_x + label_w, label_y + label_h],
        fill=(0, 0, 0),
        outline=color,
    )
    draw.text((label_x + 4, label_y + 3), label, fill=(255, 255, 255), font=font)


def _draw_labeled_target_markers(
    img,
    target_markers: Sequence[ReviewTargetMarker],
    *,
    chip_size_m: float,
) -> None:
    """Draw visible Txx labels at metric offsets from the chip center."""
    from PIL import ImageDraw, ImageFont

    w, h = img.size
    short = min(w, h)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    palette = (
        (255, 215, 0),
        (0, 180, 255),
        (255, 92, 92),
        (76, 217, 100),
        (255, 128, 0),
        (210, 120, 255),
    )

    for idx, marker in enumerate(target_markers):
        color = palette[idx % len(palette)]
        px = int(round(w * (0.5 + float(marker.offset_x_m) / float(chip_size_m))))
        py = int(round(h * (0.5 - float(marker.offset_y_m) / float(chip_size_m))))
        px = _clamp_int(px, 0, max(0, w - 1))
        py = _clamp_int(py, 0, max(0, h - 1))
        if marker.search_radius_m is not None and marker.search_radius_m > 0:
            ring_r = max(5, int(round(short * float(marker.search_radius_m) / float(chip_size_m))))
        else:
            ring_r = max(5, int(round(short * 0.05)))
        cross_r = max(4, int(round(short * 0.035)))

        # Black under-stroke keeps the marker readable on bright roofs.
        for offset in range(3, 0, -1):
            r = ring_r + offset
            draw.ellipse([px - r, py - r, px + r, py + r], outline=(0, 0, 0))
        for offset in range(2):
            r = ring_r - offset
            draw.ellipse([px - r, py - r, px + r, py + r], outline=color)
        draw.line([px - cross_r, py, px + cross_r, py], fill=(0, 0, 0), width=5)
        draw.line([px, py - cross_r, px, py + cross_r], fill=(0, 0, 0), width=5)
        draw.line([px - cross_r, py, px + cross_r, py], fill=color, width=3)
        draw.line([px, py - cross_r, px, py + cross_r], fill=color, width=3)

        label = marker.target_label
        bbox = draw.textbbox((0, 0), label, font=font)
        label_w = bbox[2] - bbox[0] + 8
        label_h = bbox[3] - bbox[1] + 6
        label_x = px + ring_r + 4
        if label_x + label_w >= w:
            label_x = px - ring_r - label_w - 4
        label_y = py - ring_r - label_h - 3
        if label_y < 0:
            label_y = py + ring_r + 3
        label_x = _clamp_int(label_x, 0, max(0, w - label_w - 1))
        label_y = _clamp_int(label_y, 0, max(0, h - label_h - 1))
        draw.rectangle(
            [label_x, label_y, label_x + label_w, label_y + label_h],
            fill=(0, 0, 0),
            outline=color,
        )
        draw.text((label_x + 4, label_y + 3), label, fill=color, font=font)


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


def ensure_target_review_png(
    image_path: Path,
    target_markers: Sequence[ReviewTargetMarker],
    *,
    chip_size_m: float,
) -> Path:
    """Create a PNG annotated with T01/T02/... markers for matrix review.

    The target offsets are metres east/north from the chip center. Pixel mapping
    assumes north-up chips: positive x moves right and positive y moves up.
    The cache filename includes a hash of the marker contract so different
    target subsets do not reuse the wrong annotated PNG.
    """
    if not target_markers:
        return ensure_review_png(image_path, anchor_marker=False)
    if chip_size_m <= 0:
        raise ValueError("chip_size_m must be positive")
    png_path = target_review_png_path(image_path, target_markers, chip_size_m=chip_size_m)
    try:
        if (
            png_path.exists()
            and png_path.stat().st_size > 0
            and png_path.stat().st_mtime >= image_path.stat().st_mtime
        ):
            return png_path
    except OSError:
        pass

    from PIL import Image

    with Image.open(image_path) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        else:
            img = img.copy()
        _draw_labeled_target_markers(img, target_markers, chip_size_m=chip_size_m)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(png_path, format="PNG")
    return png_path


def ensure_single_target_review_png(
    image_path: Path,
    target_marker: ReviewTargetMarker,
    *,
    chip_size_m: float,
    crop_context_multiplier: float = 3.0,
    min_crop_size_m: float = 24.0,
    min_output_px: int = 128,
) -> Path:
    """Create a target-centered review PNG for single-target sequence scoring.

    The source chip remains the provenance artifact. This PNG crops around the
    target offset, draws only that target's ring/cross/label, and upscales very
    small crops to keep the marker and roof texture legible for vision review.
    """
    if chip_size_m <= 0:
        raise ValueError("chip_size_m must be positive")
    if crop_context_multiplier <= 0:
        raise ValueError("crop_context_multiplier must be positive")
    if min_crop_size_m <= 0:
        raise ValueError("min_crop_size_m must be positive")
    if min_output_px <= 0:
        raise ValueError("min_output_px must be positive")

    png_path = target_crop_review_png_path(
        image_path,
        target_marker,
        chip_size_m=chip_size_m,
        crop_context_multiplier=crop_context_multiplier,
        min_crop_size_m=min_crop_size_m,
        min_output_px=min_output_px,
    )
    try:
        if (
            png_path.exists()
            and png_path.stat().st_size > 0
            and png_path.stat().st_mtime >= image_path.stat().st_mtime
        ):
            return png_path
    except OSError:
        pass

    from PIL import Image

    with Image.open(image_path) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        else:
            img = img.copy()
        w, h = img.size
        short = min(w, h)
        target_x = w * (0.5 + float(target_marker.offset_x_m) / float(chip_size_m))
        target_y = h * (0.5 - float(target_marker.offset_y_m) / float(chip_size_m))
        radius_m = (
            float(target_marker.search_radius_m)
            if target_marker.search_radius_m is not None and target_marker.search_radius_m > 0
            else max(float(chip_size_m) * 0.05, 1.0)
        )
        crop_size_m = min(
            float(chip_size_m),
            max(float(min_crop_size_m), 2.0 * radius_m * float(crop_context_multiplier)),
        )
        crop_px = max(1, int(round(short * crop_size_m / float(chip_size_m))))
        crop_px = min(crop_px, w, h)
        left = _clamp_int(int(round(target_x - crop_px / 2)), 0, max(0, w - crop_px))
        top = _clamp_int(int(round(target_y - crop_px / 2)), 0, max(0, h - crop_px))
        right = left + crop_px
        bottom = top + crop_px
        crop = img.crop((left, top, right, bottom))

        scale = 1.0
        if min(crop.size) < min_output_px:
            scale = float(min_output_px) / float(min(crop.size))
            new_size = (max(1, int(round(crop.size[0] * scale))), max(1, int(round(crop.size[1] * scale))))
            resampling = getattr(Image, "Resampling", Image).BICUBIC
            crop = crop.resize(new_size, resampling)

        local_x = (target_x - left) * scale
        local_y = (target_y - top) * scale
        search_radius_px = short * radius_m / float(chip_size_m) * scale
        _draw_single_target_marker_at(
            crop,
            x=local_x,
            y=local_y,
            target_label=target_marker.target_label,
            search_radius_px=search_radius_px,
        )
        png_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(png_path, format="PNG")
    return png_path


def assert_gehi_success(result: GehiRunResult, *, allow_availability_chooser_exit: bool = False) -> None:
    if result.returncode == 0:
        return
    if allow_availability_chooser_exit and (
        "Cannot read keys" in result.stdout or "Cannot read keys" in result.stderr
    ):
        return
    raise RuntimeError(
        f"GEHistoricalImagery failed with return code {result.returncode}: "
        f"{result.stderr[:1000] or result.stdout[:1000]}"
    )
