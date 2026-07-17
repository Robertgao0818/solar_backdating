#!/usr/bin/env python3
"""Versioned chip-geometry registry for the Phase-3 chip re-render (ISSUE-19).

See ``docs/replan_v2/ISSUE-19-chip-geometry-policy.md`` and the decision memo
``docs/replan_v2/ISSUE-19-geometry-decision-2026-07-04.md``. Parent: PRD D18 /
user story 38 (``docs/install_date_optimization_v2_prd.md``).

Chip geometry is an explicit, versioned, provenance-recorded parameter of the
scoring-crop render (``ensure_single_target_review_png`` in ``gehi_common.py``),
NOT a hotfix in the frozen 96 m builder. A named ``geometry_version`` string maps
to the three render-crop params (``crop_context_multiplier``, ``min_crop_size_m``,
``min_output_px``); the version name — not three loose flags — is what flows into
provenance and into a re-render invocation.

Two named versions ship (decision memo geometry table):

- ``chip_geom_v1_banked96`` — the banked render (3.0 / 24.0 / 128, ~60 m context
  at >=128 px on a small install). Legacy default; never re-run; its verdict-store
  rows stay authoritative for banked-rep comparability.
- ``chip_geom_v2_tight12`` — the ISSUE-04 tight-crop arm (0.5 / 12.0 / 256,
  ~12 m context at 256 px). The Phase-3 re-render default consumed by the dinov3
  distillation-set build (``docs/dinov3_scorer/ISSUE-02-distillation-training-set.md``).
- ``basemap96_z19_v1`` — Path C0 (ISSUE-09) basemap_rebuild_2026-07-13 stack
  adapter geometry. The per-target ``chips/<target_id>/z<zoom>/`` tif IS the
  scoring crop (no render-crop call at all -- the pilot's ``--stack-format
  basemap96`` embed path transcodes tif->png unmodified); the three
  ``ensure_single_target_review_png`` fields below are therefore
  INFORMATIONAL ONLY for this version (nominal 96 m download extent, no
  min-crop floor, no upscale) and are never read by that embed path. This is
  a DISTINCT named version (not a silent tight12 reuse) so its config hash
  keeps basemap96 artifacts from colliding with tight12 ones in provenance.
  See ``docs/dinov3_scorer/DATA-c0-reverse-template-prereg-2026-07-12.md``
  amendment 2026-07-13.

Verdict-store migration rule (D18; ``verdict_store.py`` docstring). A geometry
change re-renders different crop bytes -> a different chip content hash
(``chip_sha256`` hashes the rendered PNG, per ``scoring_provenance.py``) -> new
verdict-store keys, so old rows survive unreferenced (no silent reuse). The
re-key flows through the content hash and ONLY the content hash — which is
exactly how the store already works ("a re-rendered chip is simply a cache
miss"). ``geometry_version`` is therefore RECORDED PROVENANCE (it lands in the
scoring-provenance ``context`` blob), NEVER a component of ``build_verdict_key``,
its ``extras`` dict, or ``_key_fields``. Adding it to the key would double-count
geometry and break the store's own correctness contract.

The GEHI 96 m download and the frozen 96 m builder
(``build_inventory_chip_groups.py``) are untouched — this module only versions
the scoring-crop render geometry.
"""

from __future__ import annotations

from dataclasses import dataclass

# Informational only: the GEHI download / builder chip extent, unchanged across
# every geometry version (the crop render never enlarges beyond the source chip).
# The authoritative per-target ``chip_size_m`` is read from ``chip_targets.csv``
# (96 m for the production manifest); recorded here purely to document that
# geometry versioning never touches the download extent.
GEHI_DOWNLOAD_CHIP_SIZE_M = 96.0


@dataclass(frozen=True)
class ChipGeometry:
    """An immutable, named chip-render geometry (ISSUE-19).

    ``crop_context_multiplier`` / ``min_crop_size_m`` / ``min_output_px`` are the
    three render-crop params of ``ensure_single_target_review_png``; the render
    extent is ``crop_size_m = min(chip_size_m, max(min_crop_size_m,
    2 * radius * crop_context_multiplier))`` and ``min_output_px`` only ever
    upsamples the crop's short side. ``download_chip_size_m`` is informational
    (see ``GEHI_DOWNLOAD_CHIP_SIZE_M``) — the same across versions.
    """

    geometry_version: str
    crop_context_multiplier: float
    min_crop_size_m: float
    min_output_px: int
    download_chip_size_m: float = GEHI_DOWNLOAD_CHIP_SIZE_M


# The named registry. Extend ONLY by adding a new named entry (never by loosening
# a flag) — a future size-stratified follow-up is a second named fixed version
# (e.g. ``chip_geom_v3_tight_scaled``), never a silent per-anchor formula.
_REGISTRY: dict[str, ChipGeometry] = {
    "chip_geom_v1_banked96": ChipGeometry(
        geometry_version="chip_geom_v1_banked96",
        crop_context_multiplier=3.0,
        min_crop_size_m=24.0,
        min_output_px=128,
    ),
    "chip_geom_v2_tight12": ChipGeometry(
        geometry_version="chip_geom_v2_tight12",
        crop_context_multiplier=0.5,
        min_crop_size_m=12.0,
        min_output_px=256,
    ),
    "basemap96_z19_v1": ChipGeometry(
        geometry_version="basemap96_z19_v1",
        crop_context_multiplier=1.0,   # informational only -- see module docstring
        min_crop_size_m=96.0,          # informational only -- nominal download extent
        min_output_px=256,             # informational only -- unused (no render-crop call)
    ),
}

# Legacy/banked default (never re-run; rows stay authoritative).
LEGACY_GEOMETRY_VERSION = "chip_geom_v1_banked96"
# Phase-3 re-render default (the ISSUE-04 tight-crop arm).
RERENDER_GEOMETRY_VERSION = "chip_geom_v2_tight12"

LEGACY_GEOMETRY = _REGISTRY[LEGACY_GEOMETRY_VERSION]
RERENDER_GEOMETRY = _REGISTRY[RERENDER_GEOMETRY_VERSION]


def resolve_chip_geometry(geometry_version: str) -> ChipGeometry:
    """Return the ``ChipGeometry`` for ``geometry_version`` or fail loudly.

    Raises ``KeyError`` with the known versions listed if ``geometry_version`` is
    not registered — a geometry change must always be a deliberate, named event.
    """
    try:
        return _REGISTRY[geometry_version]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(
            f"unknown chip geometry_version {geometry_version!r}; "
            f"known versions: {known}"
        ) from None


def available_geometry_versions() -> tuple[str, ...]:
    """Return the registered geometry-version ids, sorted."""
    return tuple(sorted(_REGISTRY))


def contain_crop_to_footprint(
    crop_size_m: float,
    *,
    source_width_m: float,
    source_height_m: float,
    chip_size_m: float,
) -> float:
    """Enlarge a crop window so it contains the full installation footprint.

    ISSUE-19 guard (decision memo, Consequences): the tight-crop default keys the
    crop window off the target ``search_radius`` and ``min_crop_size_m``, so a
    small radius on a large install could clip the footprint. Floor the crop at
    the larger footprint dimension (``source_width_m`` / ``source_height_m`` from
    the chip-target manifest), still capped at the source chip extent
    ``chip_size_m`` — the render can never enlarge beyond the downloaded chip.

    Pure and side-effect-free. The invocation site is the ISSUE-02 Phase-3
    re-render wiring, where the manifest footprint columns flow into the renderer;
    it is exposed here so that consumer builds on the exact, tested guard formula.
    """
    footprint_m = max(float(source_width_m), float(source_height_m))
    return min(float(chip_size_m), max(float(crop_size_m), footprint_m))
