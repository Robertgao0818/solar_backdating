#!/usr/bin/env python3
"""Freeze the Run3-native training manifest (PRD Phase R0, data contract).

Aggregates the RUN 3 (anchors_v2, geometry-repaired 41,393-anchor clean-label)
fullscan into one versioned, hash-locked manifest of unique
``(anchor_id, capture_date)`` scored observations, plus a spatially-isolated
train/calibration/test split. This is the R0 base component of
``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md`` (§3, §9.1).

Three banked provenance streams are merged (all keyed on
``(anchor_id, capture_date)`` after dedup):

- ``run3_v2/{a24,a48}/scan_states/<anchor_id>.json`` -- authoritative for the
  Gemini verdict (``pv_present``/``confidence``/``quality_flag``), the
  round/decision provenance (``round_id``/``round_type``/``decision_source``)
  and per-anchor scan metadata (``census_date``/``catalog_max_date``/
  ``status``). A ``(anchor_id, capture_date)`` may appear in several rounds;
  **later round wins** (max ``round_id``).
- ``run3_v2/{a24,a48}/scoring_provenance.jsonl`` -- authoritative for the
  scored-chip PNG crop (``chip_path``/``chip_sha256``) and scorer metadata
  (``prompt_config_hash``/``model_id``/``scoring_mode``/``ts_utc``). Retries and
  the gemini_failed rescan produce duplicate keys; **latest ``ts_utc`` wins**.
- ``run3_v2/{a24,a48}/chip_provenance.jsonl`` -- authoritative for the source
  raster (TIFF path + SHA, CRS, extent, GSD, zoom, provider). Duplicate keys
  (re-runs, identical geometry) are collapsed keeping the last row.

The dedup rules, reconciliation results and split statistics are all frozen
into ``MANIFEST_LOCK.json``.

Label semantics (PRD §3.3, three-state ``label_v1``): ``present`` / ``absent``
/ ``uninformative``. The legacy ``unusable`` quality flag and any missing/failed
verdict map to ``uninformative``. ``absent`` is provisional in R0 -- PRD §3.3
only *permits* ``absent`` once ``target_localized=true`` (the localization layer
in R2 must re-gate placement-failure ``absent`` -> ``uninformative``); the
nullable ``target_localized`` column is emitted all-null for R2 to backfill.

Spatial split (PRD §3.2): whole leakage-components are atomically assigned to a
single split so no same-roof / adjacent-target / overlapping-pixel pair is torn
across splits. See ``--leakage-graph`` for the two adjacency definitions and why
``chip_overlap`` (not the coarse ``grid`` tile) is the default.

Run from the shared venv (``source scripts/activate_env.sh``); needs pandas +
pyarrow. No real data is written into the repo -- outputs go to
``~/zasolar_data/geid_temporal/run3_native_line_2026-07/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from solar_backdating.localization.observation import verdict_token

# ---------------------------------------------------------------------------
# Versioned rule identifiers (frozen into the lock so downstream can pin them).
# ---------------------------------------------------------------------------
LABEL_MAP_VERSION = "label_v1@2026-07-19"
DEDUP_RULES_VERSION = "dedup@2026-07-19"
SPLIT_ALGO_VERSION = "leakage_component_stratum_greedy@2026-07-19"
SPLIT_ALGO_SUPERSEDED = "leakage_component_balanced_greedy@2026-07-19"
TFW_RECON_VERSION = "tfw_from_extent_pxcount@2026-07-19"

# RUN 3 acceptance ledger (PRD §3.1 / task hand-off). Reconciliation is a hard
# gate: any mismatch aborts with a non-zero exit and a diff report.
EXPECTED_RUN3 = {
    "observations": 311195,
    "anchors": 41393,
    "round_type": {
        "initial": 206965,
        "bisection": 73118,
        "anchor_recovery": 31087,
        "walk_back": 25,
    },
    "decision_source": {"gemini_batch": 311093, "gemini_failed": 102},
    "later_round_wins_removed": 0,
}

DEFAULT_DATA_ROOT = (
    Path.home()
    / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/run3_v2"
)
DEFAULT_ANCHORS_CSV = (
    Path.home()
    / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
    / "anchors_v2/anchors_all.csv"
)
DEFAULT_OUT_DIR = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1"
)
DEFAULT_SEED = 20260719
AREA_BINS = [(0, 15), (15, 40), (40, 100), (100, float("inf"))]


def area_bin_label(area_m2: float | None) -> str:
    if area_m2 is None or (isinstance(area_m2, float) and area_m2 != area_m2):
        return "unknown"
    for lo, hi in AREA_BINS:
        if lo <= area_m2 < hi:
            return f"[{lo},{hi if hi != float('inf') else 'inf'})"
    return "unknown"


# ---------------------------------------------------------------------------
# Pure-function rules (imported by the unit tests).
# ---------------------------------------------------------------------------
def classify_label(
    pv_present: Any, quality_flag: Any, decision_source: Any
) -> str:
    """Three-state ``label_v1`` (PRD §3.3), order-sensitive.

    ``unusable`` / a failed scoring / a missing verdict -> ``uninformative``;
    otherwise the boolean Gemini verdict maps to ``present`` / ``absent`` via
    the single canonical ``verdict_token`` (localization layer's schema) so the
    manifest and the R2 replay script share one bool->token map.
    """
    if quality_flag == "unusable" or decision_source == "gemini_failed":
        return "uninformative"
    return verdict_token(pv_present)


def tfw_six_params(
    extent_minx: float,
    extent_miny: float,
    extent_maxx: float,
    extent_maxy: float,
    width_px: int,
    height_px: int,
) -> tuple[float, float, float, float, float, float]:
    """Reconstruct the world-file six params from extent + pixel counts.

    Pixel size is derived in the raster's own CRS units (metres for EPSG:3857
    Wayback frames, degrees for EPSG:4326 TM frames), so this matches the
    on-disk ``.tfw`` exactly for both providers. Convention (verified against
    disk): corner-referenced upper-left, C=minx, F=maxy, no rotation.
    Returns ``(A, D, B, E, C, F)`` in world-file line order.
    """
    a = (extent_maxx - extent_minx) / width_px  # x pixel size (line 1)
    d = 0.0  # rotation about y (line 2)
    b = 0.0  # rotation about x (line 3)
    e = -(extent_maxy - extent_miny) / height_px  # y pixel size, N-up (line 4)
    c = extent_minx  # x of UL pixel corner (line 5)
    f = extent_maxy  # y of UL pixel corner (line 6)
    return (a, d, b, e, c, f)


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def build_leakage_components(
    anchor_rows: list[dict], mode: str
) -> dict[str, str]:
    """Return ``anchor_id -> component_id`` under the chosen leakage graph.

    ``chip_overlap`` (default, PRD §3.2 intent): edge iff two anchors' chip
    bounding boxes overlap (the actual overlapping-pixel / adjacent-target
    relation) OR share a ``legacy_group_anchor_id`` (same roof group). The
    coarse ``grid`` alternative (edge iff two anchors share any tile in
    ``source_grids``) is retained for audit but is degenerate on this corpus:
    spanning anchors chain the whole Johannesburg tile mesh into one 98.9%
    component, making a 70/15/15 hold-out impossible.

    ``buffer`` is a reserved interface value (owner request, 2026-07-19): a
    future half-box bbox dilation before the overlap test, to also cut the
    "spatially near but outside the 96m box" residual that the cross-split
    nearest-neighbour metric quantifies. It is intentionally **not** implemented
    in R0 and is not the default; wiring it up is a separate change.
    """
    uf = UnionFind()
    for r in anchor_rows:
        uf.add(r["anchor_id"])
    # Same-roof group ties apply to both modes.
    group_members: dict[str, list[str]] = defaultdict(list)
    for r in anchor_rows:
        group_members[r["legacy_group_anchor_id"]].append(r["anchor_id"])
    for members in group_members.values():
        for m in members[1:]:
            uf.union(members[0], m)

    if mode == "grid":
        grid_members: dict[str, list[str]] = defaultdict(list)
        for r in anchor_rows:
            grids = [g for g in str(r["source_grids"]).split(";") if g] or [
                r["grid_id"]
            ]
            for g in grids:
                grid_members[g].append(r["anchor_id"])
        for members in grid_members.values():
            for m in members[1:]:
                uf.union(members[0], m)
    elif mode == "chip_overlap":
        boxes = {
            r["anchor_id"]: (
                float(r["chip_lon_min"]),
                float(r["chip_lat_min"]),
                float(r["chip_lon_max"]),
                float(r["chip_lat_max"]),
            )
            for r in anchor_rows
        }
        cell = max((b[2] - b[0]) for b in boxes.values()) * 1.01
        buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
        for aid, b in boxes.items():
            x0, y0, x1, y1 = b
            for cx in range(int(x0 // cell), int(x1 // cell) + 1):
                for cy in range(int(y0 // cell), int(y1 // cell) + 1):
                    buckets[(cx, cy)].append(aid)

        def overlap(a: str, b: str) -> bool:
            ax0, ay0, ax1, ay1 = boxes[a]
            bx0, by0, bx1, by1 = boxes[b]
            return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)

        checked: set[tuple[str, str]] = set()
        for (cx, cy), members in buckets.items():
            neigh: list[str] = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neigh.extend(buckets.get((cx + dx, cy + dy), ()))
            for a in members:
                for b in neigh:
                    if a >= b:
                        continue
                    key = (a, b)
                    if key in checked:
                        continue
                    checked.add(key)
                    if overlap(a, b):
                        uf.union(a, b)
    elif mode == "buffer":
        raise NotImplementedError(
            "--leakage-graph buffer: reserved interface (half-box bbox dilation "
            "before overlap test); not implemented in R0"
        )
    else:  # pragma: no cover - guarded by argparse choices
        raise ValueError(f"unknown leakage-graph mode: {mode}")

    # Canonical component id = min anchor_id in the component (deterministic).
    members_by_root: dict[str, list[str]] = defaultdict(list)
    for r in anchor_rows:
        members_by_root[uf.find(r["anchor_id"])].append(r["anchor_id"])
    comp_of: dict[str, str] = {}
    for members in members_by_root.values():
        cid = min(members)
        for m in members:
            comp_of[m] = cid
    return comp_of


SPLITS = ("train", "calibration", "test")


def _split_fracs(train_frac: float, cal_frac: float) -> dict[str, float]:
    return {
        "train": train_frac,
        "calibration": cal_frac,
        "test": 1.0 - train_frac - cal_frac,
    }


def _component_members(comp_of_anchor: dict[str, str]) -> dict[str, list[str]]:
    members: dict[str, list[str]] = defaultdict(list)
    for aid, cid in comp_of_anchor.items():
        members[cid].append(aid)
    return members


def _order_key(cid: str, members: dict[str, list[str]], seed: int):
    h = hashlib.blake2b(f"{seed}:{cid}".encode(), digest_size=8).hexdigest()
    return (-len(members[cid]), h)


def assign_splits(
    comp_of_anchor: dict[str, str],
    train_frac: float,
    cal_frac: float,
    seed: int,
) -> tuple[dict[str, str], dict[str, int]]:
    """Anchor-count-only balanced greedy (legacy; kept for the superseded
    comparison and as the test baseline).

    Components are ordered largest-first (ties broken by a seeded hash) and each
    is placed into the split with the current largest head-count deficit versus
    its target. Balances anchor counts but ignores per-stratum composition.
    """
    members = _component_members(comp_of_anchor)
    total = len(comp_of_anchor)
    fr = _split_fracs(train_frac, cal_frac)
    targets = {k: fr[k] * total for k in SPLITS}
    counts = {k: 0 for k in SPLITS}
    assign: dict[str, str] = {}
    for cid in sorted(members, key=lambda c: _order_key(c, members, seed)):
        split = max(SPLITS, key=lambda s: targets[s] - counts[s])
        for aid in members[cid]:
            assign[aid] = split
        counts[split] += len(members[cid])
    return assign, counts


def assign_splits_stratified(
    comp_of_anchor: dict[str, str],
    anchor_obs: dict[str, int],
    anchor_stratum: dict[str, str],
    train_frac: float,
    cal_frac: float,
    seed: int,
) -> tuple[dict[str, str], dict[str, int]]:
    """Stratum-aware balanced greedy (owner request, 2026-07-19).

    Same atomic-component, largest-first, seeded ordering as ``assign_splits``,
    but each component is placed into the split that **minimises the joint
    weighted L1 deviation** from target fractions across (a) anchor count and
    (b) every ``chip_arm x area_bin`` observation-count stratum. The anchor
    block and the stratum block are weighted equally (each stratum carries
    ``1/n_strata``), so a component that would skew one split's stratum mix is
    steered elsewhere. Components are never split, so stratum balance is bounded
    by component granularity.
    """
    members = _component_members(comp_of_anchor)
    strata = sorted(set(anchor_stratum.values()))
    fr = _split_fracs(train_frac, cal_frac)
    total_anchor = len(comp_of_anchor)
    total_obs_s = {s: 0.0 for s in strata}
    for aid in comp_of_anchor:
        total_obs_s[anchor_stratum[aid]] += anchor_obs[aid]

    comp_obs_s: dict[str, dict[str, float]] = {}
    for cid, mem in members.items():
        acc = {s: 0.0 for s in strata}
        for aid in mem:
            acc[anchor_stratum[aid]] += anchor_obs[aid]
        comp_obs_s[cid] = acc

    w_anchor = 1.0
    w_s = 1.0 / len(strata) if strata else 0.0
    counts_anchor = {k: 0 for k in SPLITS}
    counts_obs = {k: {s: 0.0 for s in strata} for k in SPLITS}

    def dev(k: str) -> float:
        d = w_anchor * abs(counts_anchor[k] / total_anchor - fr[k])
        for s in strata:
            if total_obs_s[s] > 0:
                d += w_s * abs(counts_obs[k][s] / total_obs_s[s] - fr[k])
        return d

    assign: dict[str, str] = {}
    for cid in sorted(members, key=lambda c: _order_key(c, members, seed)):
        n_anchor = len(members[cid])
        obs_s = comp_obs_s[cid]
        best_k, best_delta = None, None
        for k in SPLITS:
            before = dev(k)
            counts_anchor[k] += n_anchor
            for s in strata:
                counts_obs[k][s] += obs_s[s]
            after = dev(k)
            counts_anchor[k] -= n_anchor
            for s in strata:
                counts_obs[k][s] -= obs_s[s]
            delta = after - before
            if best_delta is None or delta < best_delta - 1e-12:
                best_delta, best_k = delta, k
        for aid in members[cid]:
            assign[aid] = best_k
        counts_anchor[best_k] += n_anchor
        for s in strata:
            counts_obs[best_k][s] += obs_s[s]
    return assign, {k: counts_anchor[k] for k in SPLITS}


def stratum_shares(
    assign: dict[str, str],
    anchor_obs: dict[str, int],
    anchor_stratum: dict[str, str],
) -> tuple[dict[str, dict[str, float]], dict[str, float], float]:
    """Return (per-split stratum obs share, corpus stratum share, max abs dev).

    ``share`` = fraction of a split's observations in each stratum; the balance
    target is that each split's shares equal the corpus shares. ``max abs dev``
    is the worst |split_share - corpus_share| over all splits/strata (in the
    0..1 fraction scale; x100 for pp).
    """
    strata = sorted(set(anchor_stratum.values()))
    obs_ks: dict[str, dict[str, float]] = {
        k: {s: 0.0 for s in strata} for k in SPLITS
    }
    obs_k: dict[str, float] = {k: 0.0 for k in SPLITS}
    obs_s: dict[str, float] = {s: 0.0 for s in strata}
    total = 0.0
    for aid, k in assign.items():
        s = anchor_stratum[aid]
        n = anchor_obs[aid]
        obs_ks[k][s] += n
        obs_k[k] += n
        obs_s[s] += n
        total += n
    corpus = {s: (obs_s[s] / total if total else 0.0) for s in strata}
    shares = {
        k: {
            s: (obs_ks[k][s] / obs_k[k] if obs_k[k] else 0.0) for s in strata
        }
        for k in SPLITS
    }
    max_dev = max(
        (abs(shares[k][s] - corpus[s]) for k in SPLITS for s in strata),
        default=0.0,
    )
    return shares, corpus, max_dev


# ---------------------------------------------------------------------------
# Data loaders.
# ---------------------------------------------------------------------------
@dataclass
class ScanStatsResult:
    total_result_rows: int
    round_type_raw: Counter
    later_round_wins_removed: int
    # (anchor_id, capture_date) -> verdict/provenance dict
    obs: dict[tuple[str, str], dict]
    # anchor_id -> per-anchor scan metadata
    anchor_meta: dict[str, dict]


def load_scan_states(data_root: Path, arms: Iterable[str]) -> ScanStatsResult:
    best: dict[tuple[str, str], tuple[int, dict]] = {}
    anchor_meta: dict[str, dict] = {}
    total = 0
    removed = 0
    round_type_raw: Counter = Counter()
    for arm in arms:
        sdir = data_root / arm / "scan_states"
        for entry in os.scandir(sdir):
            if not entry.name.endswith(".json"):
                continue
            with open(entry.path) as fh:
                d = json.load(fh)
            aid = d["anchor_id"]
            anchor_meta[aid] = {
                "region_key": d.get("region_key"),
                "grid_id": d.get("grid_id"),
                "scan_status": d.get("status"),
                "census_date": d.get("census_date"),
                "catalog_max_date": d.get("catalog_max_date"),
                "post_census_reference_frames": d.get(
                    "post_census_reference_frames"
                ),
                "geometry_version": d.get("geometry_version"),
                "scan_arm": arm,
            }
            for rnd in d.get("rounds", []):
                rid = rnd.get("round_id")
                rtype = rnd.get("round_type")
                for res in rnd.get("results", []):
                    total += 1
                    round_type_raw[rtype] += 1
                    key = (aid, res["capture_date"])
                    row = {
                        "anchor_id": aid,
                        "capture_date": res["capture_date"],
                        "version": str(res.get("version")),
                        "round_id": rid,
                        "round_type": rtype,
                        "chip_index": res.get("chip_index"),
                        "pv_present": res.get("pv_present"),
                        "confidence": res.get("confidence"),
                        "quality_flag": res.get("quality_flag"),
                        "decision_source": res.get("decision_source"),
                    }
                    if key in best:
                        removed += 1
                        if rid <= best[key][0]:
                            continue
                    best[key] = (rid, row)
    return ScanStatsResult(
        total_result_rows=total,
        round_type_raw=round_type_raw,
        later_round_wins_removed=removed,
        obs={k: v[1] for k, v in best.items()},
        anchor_meta=anchor_meta,
    )


def load_scoring_provenance(
    data_root: Path, arms: Iterable[str]
) -> tuple[dict[tuple[str, str], dict], int, int]:
    """Latest-``ts_utc``-wins dedup. Returns (map, raw_rows, removed)."""
    best: dict[tuple[str, str], dict] = {}
    raw = 0
    removed = 0
    for arm in arms:
        path = data_root / arm / "scoring_provenance.jsonl"
        with open(path) as fh:
            for line in fh:
                if not line.strip():
                    continue
                d = json.loads(line)
                raw += 1
                key = (d["anchor_id"], d["capture_date"])
                ts = d.get("ts_utc") or ""
                if key in best:
                    removed += 1
                    if ts <= best[key]["ts_utc"]:
                        continue
                best[key] = {
                    "chip_png_path": d.get("chip_path"),
                    "chip_png_sha256": d.get("chip_sha256"),
                    "chip_sha256_error": d.get("chip_sha256_error"),
                    "prompt_config_hash": d.get("prompt_config_hash"),
                    "model_id": d.get("model_id"),
                    "scorer_name": d.get("scorer_name"),
                    "scoring_mode": d.get("scoring_mode"),
                    "ts_utc": ts,
                }
    return best, raw, removed


def load_chip_provenance(
    data_root: Path, arms: Iterable[str]
) -> tuple[dict[tuple[str, str], dict], int, int, int]:
    """Dedup by key (single version/key), keep last. Returns
    (map, raw_rows, removed, sha_conflicts)."""
    best: dict[tuple[str, str], dict] = {}
    raw = 0
    removed = 0
    sha_conflicts = 0
    for arm in arms:
        path = data_root / arm / "chip_provenance.jsonl"
        with open(path) as fh:
            for line in fh:
                if not line.strip():
                    continue
                d = json.loads(line)
                raw += 1
                key = (d["anchor_id"], d["capture_date"])
                row = {
                    "src_tiff_path": d.get("chip_path"),
                    "src_tiff_sha256": d.get("chip_sha256"),
                    "raster_error": d.get("raster_error"),
                    "provider": d.get("provider"),
                    "raster_crs": d.get("raster_crs"),
                    "raster_width_px": d.get("raster_width_px"),
                    "raster_height_px": d.get("raster_height_px"),
                    "extent_minx": d.get("extent_minx"),
                    "extent_miny": d.get("extent_miny"),
                    "extent_maxx": d.get("extent_maxx"),
                    "extent_maxy": d.get("extent_maxy"),
                    "gsd_x_m": d.get("gsd_x_m"),
                    "gsd_y_m": d.get("gsd_y_m"),
                    "achieved_zoom": d.get("achieved_zoom"),
                }
                if key in best:
                    removed += 1
                    if best[key]["src_tiff_sha256"] != row["src_tiff_sha256"]:
                        sha_conflicts += 1
                best[key] = row
    return best, raw, removed, sha_conflicts


# ---------------------------------------------------------------------------
# Reconciliation.
# ---------------------------------------------------------------------------
def reconcile(actual: dict, expected: dict) -> list[str]:
    """Return a list of human-readable mismatch strings (empty == pass)."""
    diffs: list[str] = []
    for key in ("observations", "anchors", "later_round_wins_removed"):
        if actual.get(key) != expected.get(key):
            diffs.append(
                f"{key}: actual={actual.get(key)} expected={expected.get(key)}"
            )
    for sub in ("round_type", "decision_source"):
        a = dict(actual.get(sub, {}))
        e = dict(expected.get(sub, {}))
        if a != e:
            diffs.append(f"{sub}: actual={a} expected={e}")
    return diffs


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_tfw_sample(df: pd.DataFrame, sample_n: int, seed: int) -> dict:
    """Cross-check reconstructed TFW params against on-disk ``.tfw`` files."""
    if sample_n <= 0:
        return {"checked": 0, "note": "skipped (--verify-tfw-sample 0)"}
    sample = df.sample(min(sample_n, len(df)), random_state=seed)
    checked = 0
    mism = 0
    missing = 0
    max_abs = 0.0
    for _, r in sample.iterrows():
        tif = r["src_tiff_path"]
        if not isinstance(tif, str) or not tif.endswith(".tif"):
            continue
        tfw = tif[:-4] + ".tfw"
        if not os.path.exists(tfw):
            missing += 1
            continue
        try:
            disk = [float(x) for x in open(tfw).read().split()]
        except Exception:
            missing += 1
            continue
        if len(disk) != 6:
            missing += 1
            continue
        recon = [r["tfw_a"], r["tfw_d"], r["tfw_b"], r["tfw_e"], r["tfw_c"], r["tfw_f"]]
        checked += 1
        for dv, rv in zip(disk, recon):
            scale = max(1.0, abs(dv))
            if abs(dv - rv) / scale > 1e-9:
                mism += 1
                max_abs = max(max_abs, abs(dv - rv))
                break
    return {
        "checked": checked,
        "mismatches": mism,
        "missing_tfw": missing,
        "max_abs_err": max_abs,
        "tolerance_rel": 1e-9,
    }


def cross_split_nn_distances(
    anchor_split: dict[str, str], centroids: dict[str, tuple[float, float]]
) -> dict:
    """Transparency metric (owner request, 2026-07-19): for each anchor, the
    centroid distance (metres, EPSG:32735) to the nearest anchor in a *different*
    split. Quantifies the spatial-proximity residual the chip-overlap leakage
    graph does not cover (near neighbours whose 96m boxes just miss). Reported,
    not gated. Returns min / p1 / p5 / p50 in metres.
    """
    from pyproj import Transformer
    from scipy.spatial import cKDTree

    aids = [a for a in anchor_split if a in centroids]
    if len(set(anchor_split[a] for a in aids)) < 2:
        return {"n": len(aids), "note": "fewer than 2 splits present"}
    tf = Transformer.from_crs("EPSG:4326", "EPSG:32735", always_xy=True)
    lons = [centroids[a][0] for a in aids]
    lats = [centroids[a][1] for a in aids]
    xs, ys = tf.transform(lons, lats)
    pts = {a: (float(xs[i]), float(ys[i])) for i, a in enumerate(aids)}
    by_split: dict[str, list[str]] = defaultdict(list)
    for a in aids:
        by_split[anchor_split[a]].append(a)
    trees = {
        s: cKDTree(np.array([pts[a] for a in members]))
        for s, members in by_split.items()
    }
    dmins = np.empty(len(aids))
    for i, a in enumerate(aids):
        s0 = anchor_split[a]
        p = pts[a]
        best = float("inf")
        for s, tree in trees.items():
            if s == s0:
                continue
            d, _ = tree.query(p)
            best = min(best, float(d))
        dmins[i] = best
    return {
        "n": int(len(dmins)),
        "crs": "EPSG:32735",
        "min_m": float(dmins.min()),
        "p1_m": float(np.percentile(dmins, 1)),
        "p5_m": float(np.percentile(dmins, 5)),
        "p50_m": float(np.percentile(dmins, 50)),
    }


# ---------------------------------------------------------------------------
# Build.
# ---------------------------------------------------------------------------
def build_manifest(args: argparse.Namespace) -> int:
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    data_root = Path(args.data_root)
    print(f"[R0] loading scan_states from {data_root} arms={arms} ...", flush=True)
    ss = load_scan_states(data_root, arms)
    print(
        f"[R0]   scan_states: rows={ss.total_result_rows} "
        f"unique={len(ss.obs)} later_round_removed={ss.later_round_wins_removed}",
        flush=True,
    )
    sp, sp_raw, sp_removed = load_scoring_provenance(data_root, arms)
    print(
        f"[R0]   scoring_provenance: rows={sp_raw} unique={len(sp)} "
        f"latest_ts_removed={sp_removed}",
        flush=True,
    )
    cp, cp_raw, cp_removed, cp_sha_conflict = load_chip_provenance(data_root, arms)
    print(
        f"[R0]   chip_provenance: rows={cp_raw} unique={len(cp)} "
        f"removed={cp_removed} sha_conflicts={cp_sha_conflict}",
        flush=True,
    )

    # Key-set alignment across the three streams.
    ks_ss, ks_sp, ks_cp = set(ss.obs), set(sp), set(cp)
    if not (ks_ss == ks_sp == ks_cp):
        print("[R0][FATAL] observation key sets disagree across streams:", flush=True)
        print(f"   scan_states\\scoring_prov: {len(ks_ss - ks_sp)}", flush=True)
        print(f"   scoring_prov\\scan_states: {len(ks_sp - ks_ss)}", flush=True)
        print(f"   scan_states\\chip_prov:    {len(ks_ss - ks_cp)}", flush=True)
        print(f"   chip_prov\\scan_states:    {len(ks_cp - ks_ss)}", flush=True)
        return 3

    # Anchor metadata from the frozen anchors CSV.
    anchors_df = pd.read_csv(args.anchors_csv, dtype=str)
    anchor_cols = [
        "anchor_id",
        "legacy_group_anchor_id",
        "region_key",
        "grid_id",
        "source_grids",
        "centroid_lon",
        "centroid_lat",
        "source_area_m2",
        "chip_arm",
        "review_extent_m",
        "geometry_version",
        "chip_lon_min",
        "chip_lat_min",
        "chip_lon_max",
        "chip_lat_max",
    ]
    anchors_meta = {
        row["anchor_id"]: {c: row[c] for c in anchor_cols}
        for _, row in anchors_df[anchor_cols].iterrows()
    }

    # Assemble one row per observation.
    records = []
    arm_mismatch = 0
    for key in ks_ss:
        aid, cdate = key
        v = ss.obs[key]
        s = sp[key]
        c = cp[key]
        am = anchors_meta.get(aid, {})
        sm = ss.anchor_meta.get(aid, {})
        label = classify_label(
            v["pv_present"], v["quality_flag"], v["decision_source"]
        )
        corrupt = bool(s.get("chip_sha256_error")) or bool(c.get("raster_error"))
        # TFW from extent + pixel counts (matches disk for both CRS).
        try:
            tfw = tfw_six_params(
                float(c["extent_minx"]),
                float(c["extent_miny"]),
                float(c["extent_maxx"]),
                float(c["extent_maxy"]),
                int(c["raster_width_px"]),
                int(c["raster_height_px"]),
            )
        except (TypeError, ValueError, ZeroDivisionError):
            tfw = (None, None, None, None, None, None)
        scan_arm = sm.get("scan_arm")
        if am.get("chip_arm") and scan_arm:
            if am["chip_arm"].lower() != scan_arm:
                arm_mismatch += 1
        area = am.get("source_area_m2")
        area_f = float(area) if area not in (None, "") else None
        rec = {
            "anchor_id": aid,
            "capture_date": cdate,
            "version": v["version"],
            "chip_arm": am.get("chip_arm"),
            "scan_arm": scan_arm,
            # round / decision provenance
            "round_id": v["round_id"],
            "round_type": v["round_type"],
            "decision_source": v["decision_source"],
            "chip_index": v["chip_index"],
            # Gemini verdict
            "pv_present": v["pv_present"],
            "confidence": v["confidence"],
            "quality_flag": v["quality_flag"],
            "label_v1": label,
            "target_localized": None,  # R2 backfills
            "corrupt": corrupt,
            # scored chip (PNG crop)
            "chip_png_path": s["chip_png_path"],
            "chip_png_sha256": s["chip_png_sha256"],
            "prompt_config_hash": s["prompt_config_hash"],
            "model_id": s["model_id"],
            "scorer_name": s["scorer_name"],
            "scoring_mode": s["scoring_mode"],
            "ts_utc": s["ts_utc"],
            # source raster (TIFF) + geometry
            "src_tiff_path": c["src_tiff_path"],
            "src_tiff_sha256": c["src_tiff_sha256"],
            "provider": c["provider"],
            "raster_crs": c["raster_crs"],
            "raster_width_px": c["raster_width_px"],
            "raster_height_px": c["raster_height_px"],
            "extent_minx": c["extent_minx"],
            "extent_miny": c["extent_miny"],
            "extent_maxx": c["extent_maxx"],
            "extent_maxy": c["extent_maxy"],
            "gsd_x_m": c["gsd_x_m"],
            "gsd_y_m": c["gsd_y_m"],
            "achieved_zoom": c["achieved_zoom"],
            "tfw_a": tfw[0],
            "tfw_d": tfw[1],
            "tfw_b": tfw[2],
            "tfw_e": tfw[3],
            "tfw_c": tfw[4],
            "tfw_f": tfw[5],
            # anchor metadata
            "legacy_group_anchor_id": am.get("legacy_group_anchor_id"),
            "region_key": am.get("region_key"),
            "grid_id": am.get("grid_id"),
            "source_grids": am.get("source_grids"),
            "centroid_lon": am.get("centroid_lon"),
            "centroid_lat": am.get("centroid_lat"),
            "source_area_m2": area_f,
            "area_bin": area_bin_label(area_f),
            "review_extent_m": am.get("review_extent_m"),
            "geometry_version": am.get("geometry_version") or sm.get("geometry_version"),
            "chip_lon_min": am.get("chip_lon_min"),
            "chip_lat_min": am.get("chip_lat_min"),
            "chip_lon_max": am.get("chip_lon_max"),
            "chip_lat_max": am.get("chip_lat_max"),
            # per-anchor scan metadata
            "census_date": sm.get("census_date"),
            "catalog_max_date": sm.get("catalog_max_date"),
            "scan_status": sm.get("scan_status"),
        }
        records.append(rec)

    df = pd.DataFrame.from_records(records)

    # --- Reconciliation gate ---
    actual = {
        "observations": len(df),
        "anchors": df["anchor_id"].nunique(),
        "round_type": dict(Counter(df["round_type"])),
        "decision_source": dict(Counter(df["decision_source"])),
        "later_round_wins_removed": ss.later_round_wins_removed,
    }
    diffs = [] if args.skip_reconcile else reconcile(actual, EXPECTED_RUN3)
    if diffs:
        print("[R0][RECONCILE-FAIL] actual vs expected mismatch:", flush=True)
        for d in diffs:
            print(f"   - {d}", flush=True)
        print(f"   full actual = {json.dumps(actual, sort_keys=True)}", flush=True)
        return 2
    if args.skip_reconcile:
        print("[R0] reconciliation skipped (--skip-reconcile)", flush=True)
    else:
        print("[R0] reconciliation PASS (all acceptance ledger numbers match)", flush=True)

    # --- Spatial split ---
    anchor_rows = [
        {
            "anchor_id": aid,
            "legacy_group_anchor_id": m.get("legacy_group_anchor_id"),
            "grid_id": m.get("grid_id"),
            "source_grids": m.get("source_grids"),
            "chip_lon_min": m.get("chip_lon_min"),
            "chip_lat_min": m.get("chip_lat_min"),
            "chip_lon_max": m.get("chip_lon_max"),
            "chip_lat_max": m.get("chip_lat_max"),
        }
        for aid, m in anchors_meta.items()
        if aid in set(df["anchor_id"])
    ]
    comp_of = build_leakage_components(anchor_rows, args.leakage_graph)
    n_components = len(set(comp_of.values()))

    # Per-anchor observation count + stratum (chip_arm x area_bin) for the
    # stratum-aware split. arm/area are constant within an anchor.
    per_anchor = df.groupby("anchor_id")
    anchor_obs = per_anchor.size().to_dict()
    anchor_stratum = {
        aid: f"{g['chip_arm'].iloc[0]}|{g['area_bin'].iloc[0]}"
        for aid, g in per_anchor
    }

    assign, counts = assign_splits_stratified(
        comp_of, anchor_obs, anchor_stratum, args.train_frac, args.cal_frac, args.seed
    )
    strat_shares, corpus_shares, strat_max_dev = stratum_shares(
        assign, anchor_obs, anchor_stratum
    )
    # Superseded anchor-only greedy, for the pre/post comparison in the lock.
    old_assign, old_counts = assign_splits(
        comp_of, args.train_frac, args.cal_frac, args.seed
    )
    _, _, old_max_dev = stratum_shares(old_assign, anchor_obs, anchor_stratum)
    df["split"] = df["anchor_id"].map(assign)
    print(
        f"[R0] stratum-aware split: max abs stratum obs-share dev = "
        f"{strat_max_dev * 100:.2f}pp (superseded balanced_greedy: "
        f"{old_max_dev * 100:.2f}pp)",
        flush=True,
    )

    splits_df = pd.DataFrame(
        {
            "anchor_id": list(assign.keys()),
            "split": list(assign.values()),
            "leakage_component_id": [comp_of[a] for a in assign.keys()],
        }
    ).sort_values("anchor_id").reset_index(drop=True)

    # Leakage assertions: no legacy group / component spans splits.
    grp_split = defaultdict(set)
    for _, r in df.iterrows():
        grp_split[r["legacy_group_anchor_id"]].add(r["split"])
    leaky_groups = [g for g, s in grp_split.items() if len(s) > 1]
    comp_split = defaultdict(set)
    for a, s in assign.items():
        comp_split[comp_of[a]].add(s)
    leaky_comps = [c for c, s in comp_split.items() if len(s) > 1]
    if leaky_groups or leaky_comps:
        print(
            f"[R0][FATAL] split leakage: {len(leaky_groups)} legacy groups / "
            f"{len(leaky_comps)} components span >1 split",
            flush=True,
        )
        return 4

    # Cross-split proximity transparency metric (reported, not a gate).
    centroids = {}
    for aid, meta in anchors_meta.items():
        if aid not in assign:
            continue
        try:
            centroids[aid] = (float(meta["centroid_lon"]), float(meta["centroid_lat"]))
        except (TypeError, ValueError):
            pass
    cross_split_nn = cross_split_nn_distances(assign, centroids)
    print(f"[R0] cross-split nearest-neighbour (m) = {cross_split_nn}", flush=True)

    # Split statistics + stratification balance.
    split_stats = {}
    for sp_name in ("train", "calibration", "test"):
        sub = df[df["split"] == sp_name]
        strat = (
            sub.groupby(["chip_arm", "area_bin"]).size().to_dict()
            if len(sub)
            else {}
        )
        split_stats[sp_name] = {
            "anchors": int(sub["anchor_id"].nunique()),
            "observations": int(len(sub)),
            "anchor_frac": round(counts[sp_name] / len(assign), 4),
            "label_v1": {k: int(v) for k, v in Counter(sub["label_v1"]).items()},
            "stratum_chip_arm_x_area": {
                f"{a}|{b}": int(n) for (a, b), n in strat.items()
            },
        }

    # --- Write outputs ---
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.parquet"
    splits_path = out_dir / "splits.parquet"
    df = df.sort_values(["anchor_id", "capture_date"]).reset_index(drop=True)
    df.to_parquet(manifest_path, index=False)
    splits_df.to_parquet(splits_path, index=False)

    tfw_check = verify_tfw_sample(df, args.verify_tfw_sample, args.seed)

    lock = {
        "generator": "scripts/temporal/build_run3_native_manifest.py",
        "prd": "docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md (Phase R0)",
        "generated_at_utc": pd.Timestamp.utcnow().isoformat(),
        "versions": {
            "label_map": LABEL_MAP_VERSION,
            "dedup_rules": DEDUP_RULES_VERSION,
            "split_algo": SPLIT_ALGO_VERSION,
            "tfw_recon": TFW_RECON_VERSION,
        },
        "params": {
            "data_root": str(data_root),
            "anchors_csv": str(args.anchors_csv),
            "arms": arms,
            "leakage_graph": args.leakage_graph,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "cal_frac": args.cal_frac,
            "test_frac": round(1.0 - args.train_frac - args.cal_frac, 6),
        },
        "dedup_rules": {
            "scan_states": "key=(anchor_id,capture_date); later round wins (max round_id)",
            "scoring_provenance": "key=(anchor_id,capture_date); latest ts_utc wins",
            "chip_provenance": "key=(anchor_id,capture_date); single version/key, keep last",
        },
        "label_map": {
            "version": LABEL_MAP_VERSION,
            "rule": "unusable|gemini_failed->uninformative; else pv_present True->present / False->absent / null->uninformative",
            "note": "absent provisional in R0; PRD §3.3 permits absent only when target_localized=true (R2 re-gates placement-failure absent->uninformative)",
            "corrupt": "chip_sha256_error not null OR raster_error not null",
            "target_localized": "all-null in R0; backfilled by R2 localization layer",
        },
        "dedup_stats": {
            "scan_states": {
                "raw_result_rows": ss.total_result_rows,
                "unique": len(ss.obs),
                "later_round_wins_removed": ss.later_round_wins_removed,
                "round_type_raw": dict(ss.round_type_raw),
            },
            "scoring_provenance": {
                "raw_rows": sp_raw,
                "unique": len(sp),
                "latest_ts_removed": sp_removed,
            },
            "chip_provenance": {
                "raw_rows": cp_raw,
                "unique": len(cp),
                "removed": cp_removed,
                "sha_conflicts": cp_sha_conflict,
            },
        },
        "reconciliation": {
            "skipped": bool(args.skip_reconcile),
            "actual": actual,
            "expected": EXPECTED_RUN3,
            "passed": not diffs,
        },
        "label_v1_totals": {k: int(v) for k, v in Counter(df["label_v1"]).items()},
        "corrupt_total": int(df["corrupt"].sum()),
        "chip_arm_mismatch_vs_scan_arm": arm_mismatch,
        "tfw_verification": tfw_check,
        "split": {
            "algo": SPLIT_ALGO_VERSION,
            "leakage_graph": args.leakage_graph,
            "n_components": n_components,
            "counts_anchors": counts,
            "leaky_groups": len(leaky_groups),
            "leaky_components": len(leaky_comps),
            "cross_split_nn_distance": cross_split_nn,
            "stratum_balance": {
                "metric": "per-split chip_arm x area_bin obs share vs corpus share",
                "acceptance_max_abs_dev_pp": 3.0,
                "achieved_max_abs_dev_pp": round(strat_max_dev * 100, 3),
                "passed": bool(strat_max_dev * 100 <= 3.0),
                "corpus_share": {s: round(v, 5) for s, v in corpus_shares.items()},
                "split_share": {
                    k: {s: round(v, 5) for s, v in strat_shares[k].items()}
                    for k in strat_shares
                },
            },
            "superseded_algo": {
                "name": SPLIT_ALGO_SUPERSEDED,
                "reason": "anchor-count-only; balanced anchors but skewed A48 obs share (train 8.9% / cal 18.6% / test 25.8% vs corpus 12.9%)",
                "counts_anchors": old_counts,
                "max_abs_stratum_dev_pp": round(old_max_dev * 100, 3),
            },
            "stats": split_stats,
        },
        "outputs": {},
    }
    for p in (manifest_path, splits_path):
        n_rows = len(df) if p == manifest_path else len(splits_df)
        lock["outputs"][p.name] = {
            "path": str(p),
            "sha256": sha256_file(p),
            "rows": int(n_rows),
            "bytes": p.stat().st_size,
        }
    lock_path = out_dir / "MANIFEST_LOCK.json"
    with open(lock_path, "w") as fh:
        json.dump(lock, fh, indent=2, sort_keys=True, default=str)

    print(f"[R0] wrote {manifest_path} ({len(df)} rows)", flush=True)
    print(f"[R0] wrote {splits_path} ({len(splits_df)} rows)", flush=True)
    print(f"[R0] wrote {lock_path}", flush=True)
    print(f"[R0] split anchor counts = {counts}", flush=True)
    print(f"[R0] label_v1 totals = {lock['label_v1_totals']}", flush=True)
    print(f"[R0] tfw check = {tfw_check}", flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    p.add_argument("--anchors-csv", default=str(DEFAULT_ANCHORS_CSV))
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--arms", default="a24,a48")
    p.add_argument(
        "--leakage-graph",
        choices=["chip_overlap", "grid", "buffer"],
        default="chip_overlap",
        help="anchor adjacency for split isolation (default chip_overlap; "
        "grid is degenerate on this corpus; buffer is a reserved, "
        "not-implemented interface -- see build_leakage_components)",
    )
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--train-frac", type=float, default=0.70)
    p.add_argument("--cal-frac", type=float, default=0.15)
    p.add_argument(
        "--verify-tfw-sample",
        type=int,
        default=1000,
        help="cross-check N reconstructed TFWs against on-disk .tfw (0=skip)",
    )
    p.add_argument(
        "--skip-reconcile",
        action="store_true",
        help="skip the RUN 3 acceptance-ledger gate (for synthetic runs)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return build_manifest(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
