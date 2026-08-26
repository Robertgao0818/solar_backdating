#!/usr/bin/env python3
"""Region-batch helpers: union a same-vintage cluster, fetch it as one GEHI
region, crop the result back to per-anchor chips.

A3 (citywide disk pipeline §9) tests whether one GEHI call for a cluster union
bbox plus local window crops can replace N per-anchor invocations. Two fetch
arms exist:

- `download` — one GeoTIFF mosaic per region. GEHI 0.5.1 always JPEG-compresses
  the whole mosaic (no --co COMPRESS=NONE), so every crop pays a second
  full-image JPEG pass; this arm failed the 2026-08-18 prereg fidelity bar.
- `dump` — native Google JPEG tiles + .jgw world files, stitched losslessly
  into a canvas here, then cropped; only the per-anchor crops are JPEG q=95,
  matching production. This is the production candidate arm.

Geometry/crop/compare and the dump-tile stitcher live here. The only GEHI
contact is `dump_region_tiles`, which takes an injectable runner and paces
through `GehiRateLimiter` exactly like `gehi_download`. The live pilot driver
is `run_a3_region_batch_pilot.py`.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

import numpy as np

from scripts.temporal.gehi_common import (
    DEFAULT_GEHI_EXE,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PROVIDER,
    GehiRateLimiter,
    GehiRunResult,
    anchor_bbox_args,
    iso_to_gehi_date,
    make_throttled_runner,
    run_gehi,
)
from scripts.temporal.gehi_download import (
    FIELDS as GEHI_DOWNLOAD_FIELDS,
    JPEG_COMPRESSION_QUALITY,
    DownloadResult,
    _recompress_jpeg_in_geotiff,
    artifact_path,
    build_chip_provenance,
    sha256_file,
)

# Cape Town / UTM 34S approximations used only for waste/size reporting.
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON_AT_34S = 92_200.0
# Default mosaic pad: covers the <1 px GEHI envelope snap seen in the
# 2026-08-18 A3 pilot (3/24 edge chips). ~2 m ≈ 7 z19 pixels.
DEFAULT_MOSAIC_PAD_DEG = 2.0e-5


@dataclass(frozen=True)
class BBox:
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float

    def as_anchor_fields(self) -> dict[str, float]:
        return {
            "chip_lon_min": self.lon_min,
            "chip_lat_min": self.lat_min,
            "chip_lon_max": self.lon_max,
            "chip_lat_max": self.lat_max,
        }

    @property
    def width_deg(self) -> float:
        return self.lon_max - self.lon_min

    @property
    def height_deg(self) -> float:
        return self.lat_max - self.lat_min

    def width_m(self, m_per_deg_lon: float = _M_PER_DEG_LON_AT_34S) -> float:
        return self.width_deg * m_per_deg_lon

    def height_m(self, m_per_deg_lat: float = _M_PER_DEG_LAT) -> float:
        return self.height_deg * m_per_deg_lat

    def area_m2(
        self,
        *,
        m_per_deg_lon: float = _M_PER_DEG_LON_AT_34S,
        m_per_deg_lat: float = _M_PER_DEG_LAT,
    ) -> float:
        return self.width_m(m_per_deg_lon) * self.height_m(m_per_deg_lat)

    def padded(self, pad_deg: float) -> "BBox":
        """Expand every edge. GEHI snaps output to its pixel grid and can
        leave the requested envelope short by <1 px; envelope chips then
        fall outside the mosaic. ~2e-5 deg ≈ 2 m at Cape Town, ~7 z19 px.
        """
        if pad_deg < 0:
            raise ValueError(f"pad_deg must be >= 0, got {pad_deg}")
        return BBox(
            lon_min=self.lon_min - pad_deg,
            lat_min=self.lat_min - pad_deg,
            lon_max=self.lon_max + pad_deg,
            lat_max=self.lat_max + pad_deg,
        )


def anchor_bbox(anchor: Mapping[str, object]) -> BBox:
    return BBox(
        lon_min=float(anchor["chip_lon_min"]),
        lat_min=float(anchor["chip_lat_min"]),
        lon_max=float(anchor["chip_lon_max"]),
        lat_max=float(anchor["chip_lat_max"]),
    )


def union_bbox(anchors: Sequence[Mapping[str, object]]) -> BBox:
    if not anchors:
        raise ValueError("union_bbox requires at least one anchor")
    boxes = [anchor_bbox(a) for a in anchors]
    return BBox(
        lon_min=min(b.lon_min for b in boxes),
        lat_min=min(b.lat_min for b in boxes),
        lon_max=max(b.lon_max for b in boxes),
        lat_max=max(b.lat_max for b in boxes),
    )


def cluster_waste(anchors: Sequence[Mapping[str, object]]) -> dict[str, float]:
    """Report union-vs-sum geometry. extra_frac < 1 means chips overlap."""
    boxes = [anchor_bbox(a) for a in anchors]
    union = union_bbox(anchors)
    sum_area = sum(b.area_m2() for b in boxes)
    union_area = union.area_m2()
    return {
        "n_anchors": float(len(anchors)),
        "union_width_m": union.width_m(),
        "union_height_m": union.height_m(),
        "union_area_m2": union_area,
        "sum_chip_area_m2": sum_area,
        "extra_frac": (union_area / sum_area) if sum_area else math.nan,
    }


def select_centroid_cluster(
    anchors: Sequence[Mapping[str, object]],
    *,
    n: int = 12,
) -> list[dict[str, object]]:
    """Return the n anchors nearest the group's centroid, stable-sorted."""
    if n < 1:
        raise ValueError(f"cluster size must be >= 1, got {n}")
    rows = [dict(a) for a in anchors]
    if len(rows) <= n:
        return sorted(rows, key=lambda r: str(r["anchor_id"]))
    cx = sum(float(r["centroid_lon"]) for r in rows) / len(rows)
    cy = sum(float(r["centroid_lat"]) for r in rows) / len(rows)
    rows.sort(
        key=lambda r: (
            (float(r["centroid_lon"]) - cx) ** 2 + (float(r["centroid_lat"]) - cy) ** 2,
            str(r["anchor_id"]),
        )
    )
    return rows[:n]


def region_anchor(
    *,
    grid_id: str,
    capture_date: str,
    bbox: BBox,
    region_key: str = "cape_town",
) -> dict[str, object]:
    """Synthetic anchor whose chip bbox is the cluster union."""
    date_token = capture_date.replace("-", "")
    return {
        "anchor_id": f"a3_region_{grid_id}_{date_token}",
        "region_key": region_key,
        "grid_id": grid_id,
        "centroid_lon": (bbox.lon_min + bbox.lon_max) / 2.0,
        "centroid_lat": (bbox.lat_min + bbox.lat_max) / 2.0,
        **bbox.as_anchor_fields(),
    }


def existing_chip_path(
    chips_root: Path,
    anchor_id: str,
    capture_date: str,
    *,
    version: str = "noversion",
    zoom: int = 19,
) -> Path:
    return artifact_path(
        chips_root,
        {"anchor_id": anchor_id, "capture_date": capture_date, "version": version},
        zoom=zoom,
    )


def crop_chip_from_mosaic(
    mosaic_path: Path,
    anchor: Mapping[str, object],
    out_path: Path,
    *,
    recompress: bool = True,
    hole_cells: Sequence[tuple[int, int]] | None = None,
    hole_tile_px: tuple[int, int] | None = None,
) -> dict[str, object]:
    """Window-crop `mosaic_path` to the anchor chip bbox and write `out_path`.

    The crop uses the mosaic pixel grid (no resample). A requested bbox that
    falls outside the mosaic is a hard failure — that is the union-completeness
    signal the A3 pilot is measuring. When the mosaic was stitched with
    `allow_holes` (canary finding 2026-08-19: GEHI dump silently skips
    vintage-uncovered tiles in non-member rectangle areas), `hole_cells` +
    `hole_tile_px` are the zero-filled tile cells in mosaic pixel space; a
    crop window touching any hole cell is rejected, so hole pixels can never
    leak into a chip — affected members fall back to sequential download.
    """
    import rasterio
    from rasterio.windows import from_bounds as window_from_bounds

    bbox = anchor_bbox(anchor)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(mosaic_path) as src:
        if src.crs is None:
            raise ValueError(f"mosaic has no CRS: {mosaic_path}")
        window = window_from_bounds(
            bbox.lon_min, bbox.lat_min, bbox.lon_max, bbox.lat_max, src.transform
        )
        # Inclusive cover of the requested bbox: floor the origin, ceil the end.
        col_off = int(math.floor(window.col_off))
        row_off = int(math.floor(window.row_off))
        col_end = int(math.ceil(window.col_off + window.width))
        row_end = int(math.ceil(window.row_off + window.height))
        if col_off < 0 or row_off < 0 or col_end > src.width or row_end > src.height:
            raise ValueError(
                f"requested chip bbox falls outside mosaic "
                f"(window=[{col_off}:{col_end},{row_off}:{row_end}] "
                f"mosaic={src.width}x{src.height})"
            )
        width = col_end - col_off
        height = row_end - row_off
        if width < 1 or height < 1:
            raise ValueError("requested crop window is empty")
        if hole_cells:
            if hole_tile_px is None:
                raise ValueError("hole_tile_px is required with hole_cells")
            tile_w, tile_h = hole_tile_px
            for hole_col, hole_row in hole_cells:
                if (
                    col_off < (hole_col + 1) * tile_w
                    and col_end > hole_col * tile_w
                    and row_off < (hole_row + 1) * tile_h
                    and row_end > hole_row * tile_h
                ):
                    raise ValueError(
                        f"crop window intersects dump hole at tile "
                        f"({hole_col}, {hole_row}); refusing hole pixels"
                    )
        from rasterio.windows import Window

        read_window = Window(col_off, row_off, width, height)
        data = src.read(window=read_window)
        transform = src.window_transform(read_window)
        profile = src.profile.copy()
        profile.update(height=height, width=width, transform=transform, driver="GTiff")
        for key in ("compress", "jpeg_quality", "photometric"):
            profile.pop(key, None)
        tmp = out_path.with_name(out_path.name + ".crop.tmp")
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(data)
        tmp.replace(out_path)

    recompress_error = None
    if recompress:
        recompress_error = _recompress_jpeg_in_geotiff(out_path)
    return {
        "anchor_id": str(anchor["anchor_id"]),
        "path": str(out_path),
        "width": width,
        "height": height,
        "recompress_error": recompress_error,
        "jpeg_quality": JPEG_COMPRESSION_QUALITY if recompress else None,
    }


def _read_aligned_pair(
    path_a: Path,
    path_b: Path,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """Read two chips; if grids differ, reproject B onto A's grid."""
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(path_a) as src_a, rasterio.open(path_b) as src_b:
        a = src_a.read()
        meta = {
            "a_width": src_a.width,
            "a_height": src_a.height,
            "b_width": src_b.width,
            "b_height": src_b.height,
            "a_crs": src_a.crs.to_string() if src_a.crs else None,
            "b_crs": src_b.crs.to_string() if src_b.crs else None,
            "reprojected": False,
        }
        same_grid = (
            src_a.width == src_b.width
            and src_a.height == src_b.height
            and src_a.count == src_b.count
            and np.allclose(src_a.transform, src_b.transform, atol=1e-12, rtol=0)
            and src_a.crs == src_b.crs
        )
        if same_grid:
            return a, src_b.read(), meta
        b = np.zeros_like(a)
        reproject(
            source=rasterio.band(src_b, list(range(1, src_b.count + 1))),
            destination=b,
            src_transform=src_b.transform,
            src_crs=src_b.crs,
            dst_transform=src_a.transform,
            dst_crs=src_a.crs,
            resampling=Resampling.nearest,
        )
        meta["reprojected"] = True
        return a, b, meta


def compare_chips(path_a: Path, path_b: Path) -> dict[str, object]:
    """Pixel agreement of two georeferenced chips. A is the reference grid."""
    a, b, meta = _read_aligned_pair(path_a, path_b)
    if a.shape != b.shape:
        return {
            **meta,
            "comparable": False,
            "error": f"shape mismatch after align: {a.shape} vs {b.shape}",
        }
    a_f = a.astype(np.float32)
    b_f = b.astype(np.float32)
    abs_diff = np.abs(a_f - b_f)
    n = abs_diff.size
    return {
        **meta,
        "comparable": True,
        "error": None,
        "n_pixels": int(n),
        "mae": float(abs_diff.mean()) if n else math.nan,
        "p99_abs": float(np.percentile(abs_diff, 99)) if n else math.nan,
        "max_abs": float(abs_diff.max()) if n else math.nan,
        "exact_match_frac": float(np.mean(a == b)) if n else math.nan,
        "within_2dn_frac": float(np.mean(abs_diff <= 2.0)) if n else math.nan,
        "within_8dn_frac": float(np.mean(abs_diff <= 8.0)) if n else math.nan,
    }


def bbox_payload(bbox: BBox) -> dict[str, float]:
    payload = asdict(bbox)
    payload.update(
        {
            "width_m": bbox.width_m(),
            "height_m": bbox.height_m(),
            "area_m2": bbox.area_m2(),
        }
    )
    return payload


# ---------------------------------------------------------------------------
# GEHI `dump` arm: native tiles + world files -> lossless mosaic -> crops.
#
# Why not `download` for the mosaic (2026-08-18 A3 pilot, RUN-a3 §6): GEHI
# `download` writes the whole mosaic as JPEG-compressed GeoTIFF and 0.5.1 has
# no --co COMPRESS=NONE, so every crop pays a second full-image JPEG pass
# (median MAE 5.84 vs sequential). `dump` writes the untouched Google JPEG
# tiles plus .jgw world files; stitching them losslessly brought CPT2713 to
# median MAE 2.49, 12/12 within 5 DN. Only the per-anchor crops are then
# JPEG q=95, exactly like the production per-anchor path.
# ---------------------------------------------------------------------------

DUMP_TILE_RE = re.compile(r"^z=(?P<zoom>\d+)-Col=(?P<col>\d+)-Row=(?P<row>\d+)\.jpg$")


@dataclass(frozen=True)
class WorldFile:
    """Parsed .jgw world file (6-line ESRI form) as written by GEHI `dump`.

    Values are in CRS units (degrees for GEHI's default EPSG:4326 dump).
    WARNING, off-spec GEHI behavior (verified 2026-08-18): the ESRI world-file
    convention puts the CENTER of the upper-left pixel in C/F, but GEHI 0.5.1
    writes the upper-left CORNER there. Proof: CPT2713 dump C=18.480377197265625
    is exactly integer tile index 289058 on Google's z19 grid (360 deg / 2^19
    per tile from -180); the center reading lands at 289057.998, and crops cut
    from a center-interpreted mosaic degrade vs sequential chips (median MAE
    5.93 vs 2.49 corner-interpreted). `corner_x`/`corner_y` are therefore the
    corner coordinates, which is exactly what a GeoTIFF transform wants.
    """

    pixel_w: float  # A: x size of a pixel (> 0)
    rot_y: float    # D: rotation about the y axis (must be 0)
    rot_x: float    # B: rotation about the x axis (must be 0)
    pixel_h: float  # E: y size of a pixel (< 0 for north-up)
    corner_x: float # C: GEHI writes the upper-left CORNER x (see class docstring)
    corner_y: float # F: GEHI writes the upper-left CORNER y (see class docstring)


def parse_world_file(path: Path) -> WorldFile:
    values = [float(tok) for tok in path.read_text(encoding="utf-8").split()]
    if len(values) != 6:
        raise ValueError(f"world file must carry 6 values, got {len(values)}: {path}")
    world = WorldFile(*values)
    if world.rot_x != 0.0 or world.rot_y != 0.0:
        raise ValueError(f"rotated world file not supported: {path}")
    if world.pixel_w <= 0.0 or world.pixel_h >= 0.0:
        raise ValueError(
            f"unexpected pixel sizes in {path}: "
            f"pixel_w={world.pixel_w} pixel_h={world.pixel_h} (want w>0, h<0)"
        )
    return world


@dataclass(frozen=True)
class DumpTile:
    path: Path
    world_path: Path
    zoom: int
    file_col: int  # {c} index from the filename (rectangle-relative)
    file_row: int  # {r} index from the filename (GEHI counts rows SOUTH-up)
    world: WorldFile
    width: int
    height: int


def scan_dump_tiles(tile_dir: Path, *, zoom: int | None = None) -> list[DumpTile]:
    """Index a GEHI dump folder. Every `z=Z-Col=c-Row=r.jpg` needs its .jgw."""
    from PIL import Image

    tiles: list[DumpTile] = []
    for jpg in sorted(tile_dir.glob("*.jpg")):
        match = DUMP_TILE_RE.match(jpg.name)
        if match is None:
            continue
        tile_zoom = int(match.group("zoom"))
        if zoom is not None and tile_zoom != zoom:
            continue
        world_path = jpg.with_suffix(".jgw")
        if not world_path.is_file():
            raise ValueError(f"dump tile missing world file: {world_path}")
        with Image.open(jpg) as img:
            if img.mode != "RGB":
                raise ValueError(f"dump tile is not RGB: {jpg} mode={img.mode}")
            width, height = img.size
        tiles.append(
            DumpTile(
                path=jpg,
                world_path=world_path,
                zoom=tile_zoom,
                file_col=int(match.group("col")),
                file_row=int(match.group("row")),
                world=parse_world_file(world_path),
                width=width,
                height=height,
            )
        )
    return tiles


def stitch_dump_tiles(
    tile_dir: Path,
    out_path: Path,
    *,
    zoom: int | None = None,
    crs: str = "EPSG:4326",
    allow_holes: bool = False,
) -> dict[str, object]:
    """Losslessly stitch a GEHI dump folder into one uncompressed GeoTIFF.

    Tiles are placed by their world-file coordinates, never by filename order
    (GEHI 0.5.1 names {r} counting SOUTH-up; trusting that convention silently
    flips the mosaic if it ever changes). Pixels are copied verbatim — no
    resample, no re-encode. By default the grid must be complete: a missing
    tile means a hole in the analysis canvas, so this raises instead of
    zero-filling. With `allow_holes=True` the holes are zero-filled and
    returned as `holes` (+ `hole_tile_px`) in the info dict — callers MUST
    reject crop windows intersecting them (crop_chip_from_mosaic does, given
    the cells); hole pixels must never reach a chip silently.
    """
    import rasterio
    from affine import Affine
    from PIL import Image

    tiles = scan_dump_tiles(tile_dir, zoom=zoom)
    if not tiles:
        raise ValueError(f"no dump tiles found in {tile_dir}")
    ref = tiles[0]
    for tile in tiles[1:]:
        if tile.width != ref.width or tile.height != ref.height:
            raise ValueError(
                f"mixed tile sizes in {tile_dir}: {tile.path.name} is "
                f"{tile.width}x{tile.height}, expected {ref.width}x{ref.height}"
            )
        if not (
            math.isclose(tile.world.pixel_w, ref.world.pixel_w, rel_tol=1e-9, abs_tol=1e-15)
            and math.isclose(tile.world.pixel_h, ref.world.pixel_h, rel_tol=1e-9, abs_tol=1e-15)
        ):
            raise ValueError(f"mixed pixel sizes in {tile_dir}: {tile.path.name}")
    tile_w, tile_h = ref.width, ref.height
    px_w, px_h = ref.world.pixel_w, ref.world.pixel_h
    step_x = px_w * tile_w
    step_y = abs(px_h) * tile_h
    min_cx = min(t.world.corner_x for t in tiles)
    max_cy = max(t.world.corner_y for t in tiles)

    placed: dict[tuple[int, int], DumpTile] = {}
    for tile in tiles:
        col_f = (tile.world.corner_x - min_cx) / step_x
        row_f = (max_cy - tile.world.corner_y) / step_y
        col, row = int(round(col_f)), int(round(row_f))
        if abs(col_f - col) > 0.25 or abs(row_f - row) > 0.25:
            raise ValueError(
                f"tile {tile.path.name} does not snap to the mosaic grid "
                f"(col_f={col_f:.4f}, row_f={row_f:.4f})"
            )
        if (col, row) in placed:
            raise ValueError(f"duplicate tile placement at grid cell {(col, row)}")
        placed[(col, row)] = tile
    n_cols = max(c for c, _ in placed) + 1
    n_rows = max(r for _, r in placed) + 1
    holes = [(c, r) for c in range(n_cols) for r in range(n_rows) if (c, r) not in placed]
    if holes and not allow_holes:
        raise ValueError(
            f"dump grid in {tile_dir} has {len(holes)} hole(s), first at {holes[0]}: "
            "refusing to stitch an incomplete mosaic"
        )
    # Cross-check filename indices against coordinate placement. GEHI's {c}
    # counts eastward (matches) and {r} counts SOUTH-up (flipped vs the
    # north-up canvas). Coordinates are the truth; mismatches are reported,
    # not fatal, so a future GEHI convention change cannot silently flip chips.
    filename_grid_mismatches = sum(
        1
        for (col, row), tile in placed.items()
        if tile.file_col != col or tile.file_row != (n_rows - 1 - row)
    )

    canvas = np.zeros((3, n_rows * tile_h, n_cols * tile_w), dtype=np.uint8)
    for (col, row), tile in placed.items():
        with Image.open(tile.path) as img:
            arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
        canvas[
            :, row * tile_h : (row + 1) * tile_h, col * tile_w : (col + 1) * tile_w
        ] = arr.transpose(2, 0, 1)

    # GEHI's .jgw C/F already ARE the tile corner (see WorldFile docstring),
    # so the canvas transform takes the envelope corners directly — do NOT add
    # the ESRI half-pixel center->corner shift here.
    transform = Affine(
        px_w, 0.0, min_cx,
        0.0, px_h, max_cy,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".stitch.tmp")
    with rasterio.open(
        tmp,
        "w",
        driver="GTiff",
        width=n_cols * tile_w,
        height=n_rows * tile_h,
        count=3,
        dtype="uint8",
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(canvas)
    tmp.replace(out_path)
    return {
        "path": str(out_path),
        "n_tiles": len(tiles),
        "n_cols": n_cols,
        "n_rows": n_rows,
        "tile_w_px": tile_w,
        "tile_h_px": tile_h,
        "width_px": n_cols * tile_w,
        "height_px": n_rows * tile_h,
        "pixel_w_deg": px_w,
        "pixel_h_deg": px_h,
        "lon_min": transform.c,
        "lat_max": transform.f,
        "lon_max": transform.c + n_cols * tile_w * px_w,
        "lat_min": transform.f + n_rows * tile_h * px_h,
        "filename_grid_mismatches": filename_grid_mismatches,
        "holes": holes,
        "n_holes": len(holes),
        "hole_tile_px": (tile_w, tile_h),
    }


@dataclass
class DumpOutcome:
    region_id: str
    capture_date: str
    zoom: int
    tile_dir: Path
    n_tiles: int
    status: str  # "ok" | "reused_existing" | "failed"
    error: str | None
    gehi_command: str
    stdout_sha256: str


def dump_region_tiles(
    anchor: Mapping[str, object],
    *,
    capture_date: str,
    zoom: int,
    output_dir: Path,
    provider: str = DEFAULT_PROVIDER,
    gehi_exe: Path = DEFAULT_GEHI_EXE,
    parallel: int = 4,
    timeout: float = 600.0,
    overwrite: bool = False,
    runner: Callable[..., GehiRunResult] | None = None,
    limiter: GehiRateLimiter | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    raw_log_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> DumpOutcome:
    """Run GEHI `dump` for one region bbox into `output_dir` (tiles + .jgw).

    Idempotent: a non-empty tile folder is reused (`reused_existing`) unless
    `overwrite`. `--exact-date` is ALWAYS passed — multi-date hole filling
    would poison install-date inference. Throttling mirrors
    `download_chip_with_zoom_ladder`: no runner -> shared throttled `run_gehi`;
    runner + limiter -> the runner is wrapped in the same pacing/backoff.
    """
    if runner is None:
        def _base_runner(cmd_args, *, executable=DEFAULT_GEHI_EXE, timeout=300.0):
            return run_gehi(cmd_args, executable=executable, timeout=timeout)

        runner = make_throttled_runner(
            base_runner=_base_runner, limiter=limiter, max_attempts=max_attempts
        )
    elif limiter is not None:
        runner = make_throttled_runner(
            base_runner=runner, limiter=limiter, max_attempts=max_attempts
        )
    region_id = str(anchor["anchor_id"])

    if not overwrite:
        existing = scan_dump_tiles(output_dir, zoom=zoom) if output_dir.is_dir() else []
        if existing:
            return DumpOutcome(
                region_id=region_id,
                capture_date=capture_date,
                zoom=zoom,
                tile_dir=output_dir,
                n_tiles=len(existing),
                status="reused_existing",
                error=None,
                gehi_command="",
                stdout_sha256="",
            )

    lower_left, upper_right = anchor_bbox_args(anchor)
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd_args: list[object] = [
        "dump",
        "--lower-left", lower_left,
        "--upper-right", upper_right,
        "--zoom", int(zoom),
        "--date", iso_to_gehi_date(capture_date),
        "--exact-date",
        "--provider", provider,
        "--parallel", int(parallel),
        "--output", output_dir,
        "--world",
    ]
    try:
        result = runner(cmd_args, executable=gehi_exe, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - record and report, never crash the stage
        return DumpOutcome(
            region_id=region_id,
            capture_date=capture_date,
            zoom=zoom,
            tile_dir=output_dir,
            n_tiles=0,
            status="failed",
            error=f"runner exception: {type(exc).__name__}: {exc}",
            gehi_command="",
            stdout_sha256="",
        )
    if raw_log_callback is not None:
        raw_log_callback(
            {
                "region_id": region_id,
                "capture_date": capture_date,
                "zoom": zoom,
                "returncode": result.returncode,
                "command": result.command,
                "stdout_sha256": result.stdout_sha256,
                "stderr_sha256": result.stderr_sha256,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "tile_dir": str(output_dir),
            }
        )
    if result.returncode != 0:
        return DumpOutcome(
            region_id=region_id,
            capture_date=capture_date,
            zoom=zoom,
            tile_dir=output_dir,
            n_tiles=0,
            status="failed",
            error=f"GEHI returncode={result.returncode}: {result.stderr[:300] or result.stdout[:300]}",
            gehi_command=result.command,
            stdout_sha256=result.stdout_sha256,
        )
    tiles = scan_dump_tiles(output_dir, zoom=zoom)
    if not tiles:
        return DumpOutcome(
            region_id=region_id,
            capture_date=capture_date,
            zoom=zoom,
            tile_dir=output_dir,
            n_tiles=0,
            status="failed",
            error="GEHI dump succeeded but produced no tiles",
            gehi_command=result.command,
            stdout_sha256=result.stdout_sha256,
        )
    return DumpOutcome(
        region_id=region_id,
        capture_date=capture_date,
        zoom=zoom,
        tile_dir=output_dir,
        n_tiles=len(tiles),
        status="ok",
        error=None,
        gehi_command=result.command,
        stdout_sha256=result.stdout_sha256,
    )


# ---------------------------------------------------------------------------
# dump -> per-anchor manifest synthesis (quality_gate contract).
#
# One cluster dump produces N cropped chips. `run_ct05_chip_pipeline
# .quality_gate` consumes per-anchor manifest rows in exactly
# `gehi_download.FIELDS`; this folds the batch_dump crop records back into
# that per-anchor shape so the existing QA gate runs unchanged. Crop failures
# are emitted as `crop_failed: ...` rows, which quality_gate classifies as
# download failures — a failure class, never an absence observation. Batch
# provenance (which dump/mosaic a chip came from) does not fit the FIELDS
# contract and goes to the additive sidecar instead (ISSUE-06 philosophy:
# provenance is additive, never a schema break).
# ---------------------------------------------------------------------------

DUMP_MANIFEST_SIDECAR_FIELDS = (
    "anchor_id",
    "capture_date",
    "version",
    "provider",
    "region_id",
    "grid_id",
    "tile_dir",
    "n_tiles",
    "mosaic_path",
    "mosaic_sha256",
    "crop_path",
    "crop_sha256",
    "crop_status",
    "dump_gehi_command",
    "dump_stdout_sha256",
)


def synthesize_dump_manifest(
    anchors: Sequence[Mapping[str, object]],
    crops_by_anchor: Mapping[str, Mapping[str, object]],
    *,
    capture_date: str,
    version: str,
    provider: str,
    zoom: int,
    region_id: str,
    tile_dir: str = "",
    n_tiles: int | str = "",
    mosaic_path: str = "",
    dump_gehi_command: str = "",
    dump_stdout_sha256: str = "",
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Fold one grid's dump-arm crops into gehi_download-compatible rows.

    Returns ``(manifest_rows, sidecar_rows)``: the manifest rows carry exactly
    ``gehi_download.FIELDS`` (so `quality_gate` consumes them unchanged), the
    sidecar rows carry the batch provenance in DUMP_MANIFEST_SIDECAR_FIELDS.

    `anchors` are the cluster's per-anchor plan rows (region_key/grid_id and
    chip bbox fields are read off them). `crops_by_anchor` maps anchor_id to
    the crop record written by the pilot driver (keys: path, status, error).
    An anchor with no crop record is emitted as a `crop_failed` row — silent
    omission would make the QA denominator lie.
    """
    mosaic_sha256 = ""
    if mosaic_path and Path(mosaic_path).is_file():
        mosaic_sha256 = sha256_file(Path(mosaic_path))
    manifest_rows: list[dict[str, object]] = []
    sidecar_rows: list[dict[str, object]] = []
    for anchor in anchors:
        anchor_id = str(anchor["anchor_id"])
        crop = crops_by_anchor.get(anchor_id)
        crop_path = str(crop.get("path", "")) if crop else ""
        crop_error = str(crop.get("error", "")) if crop else "no crop record"
        crop_ok = (
            crop is not None
            and str(crop.get("status", "")) == "ok"
            and crop_path
            and Path(crop_path).is_file()
        )
        if crop_ok:
            crop_sha256 = sha256_file(Path(crop_path))
            status = "ok"
            actual_zoom: int | str = zoom
            artifact_zoom: int | str = zoom
        else:
            crop_sha256 = ""
            if crop is not None and str(crop.get("status", "")) == "ok":
                crop_error = "crop file missing on disk"
            status = f"crop_failed: {crop_error}"
            actual_zoom = ""
            artifact_zoom = ""
        outcome = DownloadResult(
            anchor_id=anchor_id,
            capture_date=capture_date,
            version=version,
            requested_zoom_ladder=(zoom,),
            actual_zoom=actual_zoom if isinstance(actual_zoom, int) else None,
            path=Path(crop_path) if crop_ok else None,
            sha256=crop_sha256,
            status=status,
            error=None if crop_ok else crop_error,
            gehi_command=dump_gehi_command,
            download_stdout_sha256=dump_stdout_sha256,
        )
        provenance = build_chip_provenance(outcome, anchor, provider)
        artifact_id = hashlib.sha1(
            f"{anchor_id}|{artifact_zoom}|{capture_date}|{version}".encode("utf-8")
        ).hexdigest()[:16]
        manifest_rows.append(
            {
                "artifact_id": artifact_id,
                "anchor_id": anchor_id,
                "region_key": anchor.get("region_key", ""),
                "grid_id": anchor.get("grid_id", ""),
                "provider": provider,
                "zoom": zoom,
                "actual_zoom": actual_zoom,
                "requested_zoom_ladder": str(zoom),
                "capture_date": capture_date,
                "version": version,
                "path": crop_path if crop_ok else "",
                "sha256": crop_sha256,
                "status": status,
                # dump_region_tiles always passes --exact-date.
                "exact_date": 1,
                "download_stdout_sha256": dump_stdout_sha256,
                "gehi_command": dump_gehi_command,
                "raster_width_px": provenance["raster_width_px"] if crop_ok else "",
                "raster_height_px": provenance["raster_height_px"] if crop_ok else "",
                "raster_crs": provenance["raster_crs"] if crop_ok else "",
                "extent_minx": provenance["extent_minx"] if crop_ok else "",
                "extent_miny": provenance["extent_miny"] if crop_ok else "",
                "extent_maxx": provenance["extent_maxx"] if crop_ok else "",
                "extent_maxy": provenance["extent_maxy"] if crop_ok else "",
                "gsd_x_m": provenance["gsd_x_m"] if crop_ok else "",
                "gsd_y_m": provenance["gsd_y_m"] if crop_ok else "",
                "raster_error": provenance["raster_error"] if crop_ok else crop_error,
            }
        )
        sidecar_rows.append(
            {
                "anchor_id": anchor_id,
                "capture_date": capture_date,
                "version": version,
                "provider": provider,
                "region_id": region_id,
                "grid_id": anchor.get("grid_id", ""),
                "tile_dir": tile_dir,
                "n_tiles": n_tiles,
                "mosaic_path": mosaic_path,
                "mosaic_sha256": mosaic_sha256,
                "crop_path": crop_path,
                "crop_sha256": crop_sha256,
                "crop_status": status,
                "dump_gehi_command": dump_gehi_command,
                "dump_stdout_sha256": dump_stdout_sha256,
            }
        )
    # Hard contract check: a drifted row shape must fail here, not inside QA.
    for row in manifest_rows:
        if list(row.keys()) != GEHI_DOWNLOAD_FIELDS:
            raise ValueError(
                "manifest row keys drifted from gehi_download.FIELDS: "
                f"{sorted(set(row) ^ set(GEHI_DOWNLOAD_FIELDS))}"
            )
    return manifest_rows, sidecar_rows


# ---------------------------------------------------------------------------
# Production cluster planning (D5, 2026-08-19): candidates -> (grid, date,
# provider, zoom) dump tasks, and the stratified canary grid selection.
# ---------------------------------------------------------------------------

# A z19 tile spans 360/2**19 degrees of longitude.
Z19_TILE_DEG = 360.0 / 2**19


def z19_tile_estimate(width_deg: float, height_deg: float) -> dict[str, int]:
    """Whole-tile cover count for a z19 dump of a (padded) union bbox."""
    n_x = max(1, math.ceil(width_deg / Z19_TILE_DEG))
    n_y = max(1, math.ceil(height_deg / Z19_TILE_DEG))
    return {"n_tiles_x": n_x, "n_tiles_y": n_y, "n_tiles": n_x * n_y}


def plan_cluster_tasks(
    candidates: Sequence[Mapping[str, object]],
    anchor_index: Mapping[str, Mapping[str, object]],
    *,
    provider: str | None = None,
    zoom: int | None = None,
) -> list[dict[str, object]]:
    """Group per-anchor download candidates into cluster dump tasks.

    Grouping key is ``(grid_id, capture_date, provider, requested_zoom)``:
    mixing vintages in one window would poison install-date inference (GEHI
    multi-date hole filling), and the grid cell is the spatial cluster unit.
    Members are deduplicated by anchor_id (AGENTS.md rule 7: dedupe by
    (anchor_id, capture_date), never by version); per-member `version` stays
    on the candidate rows, the task does not collapse it. Tasks are sorted by
    task_id so replays are byte-stable.
    """
    groups: dict[tuple[str, str, str, int], dict[str, Mapping[str, object]]] = {}
    for row in candidates:
        anchor_id = str(row.get("anchor_id", "")).strip()
        if not anchor_id:
            raise ValueError(f"candidate missing anchor_id: {row!r}")
        anchor = anchor_index.get(anchor_id)
        if anchor is None:
            raise ValueError(f"candidate anchor {anchor_id!r} not in anchor index")
        prov = str(row.get("provider", "")).strip()
        if provider is not None and prov != provider:
            continue
        req_zoom = int(str(row.get("requested_zoom", "")).strip())
        if zoom is not None and req_zoom != zoom:
            continue
        date = str(row.get("capture_date", "")).strip()[:10]
        if not date:
            raise ValueError(f"candidate missing capture_date: {row!r}")
        grid_id = str(anchor.get("grid_id", "")).strip()
        if not grid_id:
            raise ValueError(f"anchor {anchor_id!r} has no grid_id")
        key = (grid_id, date, prov, req_zoom)
        groups.setdefault(key, {}).setdefault(anchor_id, anchor)
    tasks: list[dict[str, object]] = []
    for (grid_id, date, prov, req_zoom), members in sorted(groups.items()):
        member_rows = [members[k] for k in sorted(members)]
        bbox = union_bbox(member_rows)
        padded = bbox.padded(DEFAULT_MOSAIC_PAD_DEG)
        tasks.append(
            {
                "task_id": f"{grid_id}_{date.replace('-', '')}_{prov.lower()}_z{req_zoom}",
                "grid_id": grid_id,
                "capture_date": date,
                "provider": prov,
                "zoom": req_zoom,
                "n_members": len(member_rows),
                "anchor_ids": [str(a["anchor_id"]) for a in member_rows],
                "union": {
                    "lon_min": bbox.lon_min,
                    "lat_min": bbox.lat_min,
                    "lon_max": bbox.lon_max,
                    "lat_max": bbox.lat_max,
                    "width_m": bbox.width_m(),
                    "height_m": bbox.height_m(),
                },
                "padded_tile_estimate_z19": z19_tile_estimate(
                    padded.width_deg, padded.height_deg
                ),
            }
        )
    return tasks


def select_canary_grids(
    grid_counts: Mapping[str, int],
    n: int = 10,
) -> list[str]:
    """Stratified canary selection: grids sorted by (count, id), evenly spaced
    indices including both ends — small, median, and dense cells all sampled.
    Deterministic, so the canary is replayable."""
    if n < 1:
        raise ValueError(f"canary grid count must be >= 1, got {n}")
    ordered = sorted(grid_counts, key=lambda g: (grid_counts[g], g))
    if n >= len(ordered):
        return ordered
    idxs = sorted({round(i * (len(ordered) - 1) / (n - 1)) for i in range(n)})
    return [ordered[i] for i in idxs]
