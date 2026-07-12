#!/usr/bin/env python3
"""GEHI cross-vintage displacement + contamination audit (round-1).

Question (handoff ``/tmp/handoff_gehi_displacement_audit_2026-07-06.md``): every
GEHI vintage chip for an anchor shares the *same nominal* tile-snapped bbox, so
real cross-vintage misregistration is invisible to the georeferencing. How large
is it in metres, and does it push the installation footprint out of the Phase-3
``chip_geom_v2_tight12`` (12 m) scoring crop that is centred on the Vexcel-derived
census anchor? The banked-96 m chip tolerates several metres; tight12 may not.

Three reference signals per anchor (see ``chip_displacement`` for the estimator):

  S1  GEHI vintage -> GEHI latest/highest-zoom frame   (same-sensor intra-stack
      wobble; the variance term, zero-download).
  S3  GEHI vintage -> Vexcel 2024 crop over the SAME bbox (PRIMARY absolute
      offset vs the exact frame the tight12 crop is centred on; the census ran on
      vexcel_2024, so the anchor centre IS the Vexcel position). Fetched per
      anchor via the Vexcel /ortho/extract endpoint — cached + resumable.
  S2  GEHI vintage -> CoJ 0.15 m municipal ortho of the nearest true-date year
      (independent, license-clean, contemporaneous cross-check for old vintages).

Everything is put on a shared per-anchor UTM grid (metric CRS looked up, never
hardcoded) so a pixel shift converts straight to true ground metres. Low-texture
/ low-zoom / scene-changed pairs are *flagged* (alignment_score gate) and counted,
never silently dropped or misread as large real shifts.

Outputs to ``$AUDIT_DIR`` (default ~/zasolar_data/geid_temporal/
gehi_displacement_audit_2026-07-06/):
  per_chipdate_offsets.csv      one row per (chip, capture_date, ref_kind) with
                                dx_m/dy_m/best_offset_m/alignment_score — this IS
                                the best_offset_m/alignment_score write-back table
                                (join to any manifest on chip_id+capture_date),
                                leaving the frozen builder untouched (PRD D18).
  offset_quantiles_by_stratum.csv   dx/dy/offset quantiles by year x zoom x area.
  contamination_summary.csv     banked96 vs tight12 partial/full-loss rates.
  gehi_vs_vexcel_bias.csv        per-anchor constant GEHI-vs-Vexcel bias (S3 on
                                present-day vintages) = the term S1 alone misses.

Only derived statistics/vectors are written; no imagery chip is redistributed
(handoff provider-legality constraint).
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import numpy as np

ZASOLAR_ROOT = Path(os.environ.get("ZASOLAR_ROOT", "/home/gao/projects/ZAsolar"))
GT_ROOT = Path(os.path.expanduser("~/zasolar_data/geid_temporal"))

DISTILL_CHIPS = GT_ROOT / "dinov3_distill_20260705" / "chips"
PANEL_REPAIR_CHIPS = GT_ROOT / "panel_repair_20260703" / "chips_frozen"
CHIP_TARGETS = (
    GT_ROOT
    / "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
    / "chip_targets.csv"
)
COJ_COHORT = GT_ROOT / "coj_audit_cohort_20260704"
COJ_YEARS = (2015, 2019, 2023)
VEXCEL_COLLECTION = "za-gp-johannesburg-2024"
VEXCEL_LAYER = "urban"
TIFF_SIGS = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")

CHIP_RE = re.compile(r"_(\d{8})_v(\d+)\.tif$")

from scripts.temporal import chip_displacement as cd  # noqa: E402


# --------------------------------------------------------------------------- #
# Corpus enumeration                                                          #
# --------------------------------------------------------------------------- #

def load_targets(path: Path) -> dict[str, dict]:
    """chip_id (== distill dir / GEHI stack name) -> representative T01 target."""
    out: dict[str, dict] = {}
    with path.open() as f:
        for r in csv.DictReader(f):
            if r.get("target_label") != "T01":
                continue
            out[r["chip_id"]] = r
    return out


def enumerate_stack(anchor_dir: Path) -> list[dict]:
    """All GEHI vintage tifs under an anchor dir -> [{date, zoom, version, path}]."""
    picks = []
    for tif in anchor_dir.rglob("*.tif"):
        m = CHIP_RE.search(tif.name)
        if not m:
            continue
        ymd, ver = m.group(1), int(m.group(2))
        zoom = None
        if tif.parent.name.startswith("z") and tif.parent.name[1:].isdigit():
            zoom = int(tif.parent.name[1:])
        picks.append(
            {
                "date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
                "ymd": ymd,
                "zoom": zoom,
                "version": ver,
                "path": tif,
            }
        )
    picks.sort(key=lambda p: (p["ymd"], p["zoom"] or 0))
    return picks


def pick_reference(stack: list[dict]) -> dict | None:
    """Latest capture at the highest zoom available — the present-day proxy for
    the crop-centering frame within the GEHI stack."""
    if not stack:
        return None
    max_zoom = max((p["zoom"] or 0) for p in stack)
    cands = [p for p in stack if (p["zoom"] or 0) == max_zoom]
    return max(cands, key=lambda p: p["ymd"])


# --------------------------------------------------------------------------- #
# Vexcel per-anchor crop fetch (cached, resumable, threaded)                   #
# --------------------------------------------------------------------------- #

def fetch_vexcel_crops(targets: dict[str, dict], out_dir: Path, *, workers: int, token: str, base_url: str) -> dict[str, Path]:
    import requests

    out_dir.mkdir(parents=True, exist_ok=True)
    from shapely.geometry import box

    def _one(chip_id: str, r: dict) -> tuple[str, Path | None, str]:
        dst = out_dir / f"{chip_id}.tif"
        if dst.exists() and dst.stat().st_size > 8 and dst.read_bytes()[:4] in TIFF_SIGS:
            return chip_id, dst, "cached"
        bbox = (float(r["chip_lon_min"]), float(r["chip_lat_min"]), float(r["chip_lon_max"]), float(r["chip_lat_max"]))
        params = {
            "layer": VEXCEL_LAYER, "collection": VEXCEL_COLLECTION, "wkt": box(*bbox).wkt,
            "srid": "4326", "image-format": "tiff", "token": token,
        }
        try:
            resp = requests.get(f"{base_url.rstrip('/')}/ortho/extract", params=params, timeout=180)
            if resp.status_code == 200 and resp.content[:4] in TIFF_SIGS:
                tmp = dst.with_suffix(".part")
                tmp.write_bytes(resp.content)
                tmp.replace(dst)
                return chip_id, dst, "downloaded"
            return chip_id, None, f"http{resp.status_code}:{resp.text[:80]}"
        except Exception as exc:  # noqa: BLE001
            return chip_id, None, f"err:{exc}"

    result: dict[str, Path] = {}
    n_dl = n_cache = n_fail = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, cid, r) for cid, r in targets.items()]
        for i, fut in enumerate(as_completed(futs), 1):
            cid, path, status = fut.result()
            if path is not None:
                result[cid] = path
                if status == "cached":
                    n_cache += 1
                else:
                    n_dl += 1
            else:
                n_fail += 1
                if n_fail <= 5:
                    print(f"  vexcel miss {cid}: {status}", flush=True)
            if i % 100 == 0:
                print(f"  vexcel {i}/{len(futs)} dl={n_dl} cache={n_cache} fail={n_fail}", flush=True)
    print(f"vexcel crops: dl={n_dl} cache={n_cache} fail={n_fail} available={len(result)}", flush=True)
    return result


# --------------------------------------------------------------------------- #
# Registration on a shared UTM grid                                           #
# --------------------------------------------------------------------------- #

def register(
    ref_path: Path, mov_path: Path, *, bbox4326: tuple, metric_crs: str, gsd_m: float,
    min_psr: float,
) -> cd.ShiftResult | None:
    transform, shape, (gx, gy) = cd.utm_grid_for_bounds(*bbox4326, metric_crs=metric_crs, gsd_m=gsd_m)
    if shape[0] < 24 or shape[1] < 24:
        return None
    try:
        ref = cd.reproject_to_grid(ref_path, dst_crs=metric_crs, dst_transform=transform, dst_shape=shape)
        mov = cd.reproject_to_grid(mov_path, dst_crs=metric_crs, dst_transform=transform, dst_shape=shape)
    except Exception:
        return None
    return cd.estimate_shift(ref, mov, min_psr=min_psr)


def year_bucket(area_m2: float) -> str:
    if area_m2 < 15:
        return "a_xs(<15)"
    if area_m2 < 40:
        return "b_sm(15-40)"
    if area_m2 < 100:
        return "c_md(40-100)"
    return "d_lg(>=100)"


# --------------------------------------------------------------------------- #
# Main audit                                                                   #
# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", type=Path, default=GT_ROOT / "gehi_displacement_audit_2026-07-06")
    ap.add_argument("--limit", type=int, default=None, help="cap #anchors (smoke run)")
    ap.add_argument("--gsd-m", type=float, default=0.15, help="shared audit grid resolution")
    ap.add_argument("--min-psr", type=float, default=cd.DEFAULT_MIN_PSR)
    ap.add_argument("--vexcel-workers", type=int, default=8)
    ap.add_argument("--no-vexcel", action="store_true", help="skip S3 (Vexcel)")
    ap.add_argument("--no-coj", action="store_true", help="skip S2 (CoJ)")
    args = ap.parse_args()

    from core.grid_utils import get_metric_crs

    args.audit_dir.mkdir(parents=True, exist_ok=True)
    targets = load_targets(CHIP_TARGETS)

    # Corpus = distill (decision set) + panel_repair dense stacks.
    anchor_dirs: dict[str, Path] = {}
    for d in sorted(DISTILL_CHIPS.iterdir()):
        if d.is_dir() and d.name in targets:
            anchor_dirs[d.name] = d
    for d in sorted(PANEL_REPAIR_CHIPS.iterdir()) if PANEL_REPAIR_CHIPS.exists() else []:
        if d.is_dir() and d.name in targets:
            anchor_dirs.setdefault(d.name, d)
    chip_ids = list(anchor_dirs)
    if args.limit:
        chip_ids = chip_ids[: args.limit]
    print(f"corpus: {len(chip_ids)} anchors (distill+panel_repair) w/ T01 targets", flush=True)

    # S3 imagery: fetch Vexcel crops for the corpus.
    vexcel: dict[str, Path] = {}
    if not args.no_vexcel:
        from core.vexcel_auth import load_env, resolve_token
        env = load_env(ZASOLAR_ROOT / ".env")
        base_url = env.get("VEXCEL_API_BASE", "https://api.vexcelgroup.com/v2")
        token = resolve_token(env, base_url)
        vexcel = fetch_vexcel_crops(
            {cid: targets[cid] for cid in chip_ids}, args.audit_dir / "vexcel_crops",
            workers=args.vexcel_workers, token=token, base_url=base_url,
        )

    # S2 imagery: CoJ ortho per (chip, year) if present.
    coj: dict[tuple[str, int], Path] = {}
    if not args.no_coj:
        for y in COJ_YEARS:
            ydir = COJ_COHORT / "chips" / str(y)
            if not ydir.exists():
                continue
            for cid in chip_ids:
                p = ydir / f"{cid}.tif"
                if p.exists():
                    coj[(cid, y)] = p
        print(f"coj crops available: {len(coj)} (chip,year) pairs", flush=True)

    rows: list[dict] = []
    bias_rows: list[dict] = []
    drops = defaultdict(int)
    n_no_ref = n_no_stack = 0

    for k, cid in enumerate(chip_ids, 1):
        tgt = targets[cid]
        bbox = (float(tgt["chip_lon_min"]), float(tgt["chip_lat_min"]),
                float(tgt["chip_lon_max"]), float(tgt["chip_lat_max"]))
        grid_id = tgt.get("grid_id") or "unknown"
        try:
            metric_crs = get_metric_crs(grid_id, region="johannesburg")
        except Exception:
            metric_crs = "EPSG:32735"
        area = float(tgt.get("source_area_m2") or 0.0)
        fp_w = float(tgt.get("source_width_m") or 0.0)
        fp_h = float(tgt.get("source_height_m") or 0.0)
        search_r = float(tgt.get("search_radius_m") or 10.0)
        chip_m = float(tgt.get("chip_size_m") or 96.0)
        abucket = year_bucket(area)

        crop_tight = cd.crop_size_for_geometry(
            "chip_geom_v2_tight12", footprint_w_m=fp_w, footprint_h_m=fp_h,
            search_radius_m=search_r, chip_size_m=chip_m)
        crop_banked = cd.crop_size_for_geometry(
            "chip_geom_v1_banked96", footprint_w_m=fp_w, footprint_h_m=fp_h,
            search_radius_m=search_r, chip_size_m=chip_m)
        footprint_m = max(fp_w, fp_h)

        stack = enumerate_stack(anchor_dirs[cid])
        if not stack:
            n_no_stack += 1
            continue
        ref = pick_reference(stack)
        if ref is None:
            n_no_ref += 1
            continue

        def record(vint, ref_kind, res):
            if res is None:
                drops[f"{ref_kind}:reproject"] += 1
                return
            if not res.ok:
                drops[f"{ref_kind}:{res.reason or 'gate'}"] += 1
                return
            dx_m, dy_m, off_m = cd.shift_px_to_m(res.dy_px, res.dx_px, gsd_y_m=args.gsd_m, gsd_x_m=args.gsd_m)
            rows.append({
                "chip_id": cid, "grid_id": grid_id, "capture_date": vint["date"],
                "year": int(vint["ymd"][:4]), "zoom": vint["zoom"], "version": vint["version"],
                "ref_kind": ref_kind, "ref_date": ref["date"],
                "dx_m": round(dx_m, 3), "dy_m": round(dy_m, 3), "best_offset_m": round(off_m, 3),
                "psr": round(res.psr, 2), "alignment_score": round(res.alignment_score, 3), "texture_std": round(res.texture_std, 4),
                "source_area_m2": round(area, 2), "footprint_m": round(footprint_m, 2), "area_bucket": abucket,
                "crop_tight12_m": round(crop_tight, 2), "crop_banked96_m": round(crop_banked, 2),
                "retain_tight12": round(cd.footprint_retained_fraction(dx_m, dy_m, footprint_w_m=fp_w, footprint_h_m=fp_h, crop_size_m=crop_tight), 4),
                "retain_banked96": round(cd.footprint_retained_fraction(dx_m, dy_m, footprint_w_m=fp_w, footprint_h_m=fp_h, crop_size_m=crop_banked), 4),
            })

        # S1: intra-stack vs latest/highest-zoom reference.
        for vint in stack:
            if vint["path"] == ref["path"]:
                continue
            res = register(ref["path"], vint["path"], bbox4326=bbox, metric_crs=metric_crs, gsd_m=args.gsd_m, min_psr=args.min_psr)
            record(vint, "S1_gehi_latest", res)

        # S3: absolute vs Vexcel census frame.
        vx = vexcel.get(cid)
        if vx is not None:
            for vint in stack:
                res = register(vx, vint["path"], bbox4326=bbox, metric_crs=metric_crs, gsd_m=args.gsd_m, min_psr=args.min_psr)
                record(vint, "S3_vexcel", res)
            # constant bias = present-day GEHI (the ref) vs Vexcel.
            bres = register(vx, ref["path"], bbox4326=bbox, metric_crs=metric_crs, gsd_m=args.gsd_m, min_psr=args.min_psr)
            if bres is not None and bres.ok:
                bdx, bdy, boff = cd.shift_px_to_m(bres.dy_px, bres.dx_px, gsd_y_m=args.gsd_m, gsd_x_m=args.gsd_m)
                bias_rows.append({"chip_id": cid, "grid_id": grid_id, "ref_date": ref["date"],
                                  "bias_dx_m": round(bdx, 3), "bias_dy_m": round(bdy, 3),
                                  "bias_offset_m": round(boff, 3), "alignment_score": round(bres.alignment_score, 3)})

        # S2: contemporaneous CoJ true-date cross-check.
        for y in COJ_YEARS:
            cp = coj.get((cid, y))
            if cp is None:
                continue
            nearest = min(stack, key=lambda p: abs(int(p["ymd"][:4]) - y))
            res = register(cp, nearest["path"], bbox4326=bbox, metric_crs=metric_crs, gsd_m=args.gsd_m, min_psr=args.min_psr)
            record(nearest, f"S2_coj_{y}", res)

        if k % 50 == 0:
            print(f"  measured {k}/{len(chip_ids)} anchors, {len(rows)} rows so far", flush=True)

    # ---- write per-chipdate table ----
    _write_csv(args.audit_dir / "per_chipdate_offsets.csv", rows)
    _write_csv(args.audit_dir / "gehi_vs_vexcel_bias.csv", bias_rows)
    _write_quantiles(args.audit_dir / "offset_quantiles_by_stratum.csv", rows)
    _write_contamination(args.audit_dir / "contamination_summary.csv", rows)

    with (args.audit_dir / "drops.txt").open("w") as f:
        f.write(f"anchors: {len(chip_ids)}  no_stack={n_no_stack} no_ref={n_no_ref}\n")
        f.write(f"kept rows: {len(rows)}\n")
        for kk, vv in sorted(drops.items()):
            f.write(f"drop {kk}: {vv}\n")
    print(f"DONE: {len(rows)} rows, {len(bias_rows)} bias rows -> {args.audit_dir}", flush=True)
    print("drops:", dict(drops), flush=True)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _q(vals, p):
    return round(float(np.percentile(vals, p)), 3) if vals else None


def _write_quantiles(path: Path, rows: list[dict]) -> None:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["ref_kind"], r["year"], r["zoom"], r["area_bucket"])].append(r)
    # also overall per ref_kind
    for r in rows:
        groups[(r["ref_kind"], "ALL", "ALL", "ALL")].append(r)
    out = []
    for (rk, yr, zm, ab), rs in sorted(groups.items(), key=lambda x: str(x[0])):
        offs = [r["best_offset_m"] for r in rs]
        out.append({
            "ref_kind": rk, "year": yr, "zoom": zm, "area_bucket": ab, "n": len(rs),
            "offset_p50": _q(offs, 50), "offset_p90": _q(offs, 90), "offset_p95": _q(offs, 95),
            "offset_max": round(max(offs), 3) if offs else None,
            "dx_p50": _q([r["dx_m"] for r in rs], 50), "dy_p50": _q([r["dy_m"] for r in rs], 50),
            "align_p50": _q([r["alignment_score"] for r in rs], 50),
        })
    _write_csv(path, out)


def _write_contamination(path: Path, rows: list[dict]) -> None:
    """Retention-based contamination by ref_kind x area_bucket, at PSR cuts {8,10,12}
    (robustness across the thin noise-floor-to-lock margin — see estimator notes)."""
    out = []
    for psr_cut in (8.0, 10.0, 12.0):
        groups = defaultdict(list)
        for r in rows:
            if r["psr"] < psr_cut:
                continue
            groups[(r["ref_kind"], r["area_bucket"])].append(r)
            groups[(r["ref_kind"], "ALL")].append(r)
        for (rk, ab), rs in sorted(groups.items(), key=lambda x: str(x[0])):
            n = len(rs)
            t = [r["retain_tight12"] for r in rs]
            b = [r["retain_banked96"] for r in rs]
            out.append({
                "psr_cut": psr_cut, "ref_kind": rk, "area_bucket": ab, "n": n,
                "tight12_retain_mean": round(sum(t) / n, 4),
                "tight12_clip>25%_rate": round(sum(x < 0.75 for x in t) / n, 4),
                "tight12_lose>50%_rate": round(sum(x < 0.50 for x in t) / n, 4),
                "tight12_full_loss_rate": round(sum(x <= 0.0 for x in t) / n, 4),
                "banked96_retain_mean": round(sum(b) / n, 4),
                "banked96_lose>50%_rate": round(sum(x < 0.50 for x in b) / n, 4),
            })
    _write_csv(path, out)


if __name__ == "__main__":
    main()
