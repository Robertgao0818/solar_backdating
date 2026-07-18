#!/usr/bin/env python3
"""Render the RUN3 coverage-gain QA visual-grading artifacts from the fixed-seed
manifest. Read-only: resolves the exact marker-crop review PNGs the RUN3 scan
already materialised for Gemini (offset==0 v2 geometry) via the production
path function -- never writes into the chips dir.

Outputs under --out-dir:
  sheets/<stratum>_NN.png  : contact sheets, one row per anchor, chronological
      strip of scored frames, border green=present-usable / red=absent-usable /
      amber=unusable; header shows RUN2->RUN3 status/class/mid + v1_offset.
  pivotal/<stratum>.png    : per-anchor last-absent + 1st/last-present frames at
      native >=256px with Gemini's evidence text -- the date-accuracy check.

Reproduces the images cited in docs/replan_v2/DATA-fullscan-run3-qa-coverage-2026-07-19.md.
"""
from __future__ import annotations
import argparse, csv, json, pathlib, collections
from PIL import Image, ImageDraw, ImageFont
from scripts.temporal.gehi_common import ReviewTargetMarker, target_crop_review_png_path
try:
    from scripts.temporal.gehi_common import resolve_chip_geometry
except Exception:
    from scripts.temporal.run_adaptive_scan import resolve_chip_geometry

def load_anchors(p):
    with p.open() as f:
        return {r["anchor_id"]: r for r in csv.DictReader(f)}

def _font(sz, bold=False):
    for base in ("/usr/share/fonts/dejavu-sans-fonts", "/usr/share/fonts/truetype/dejavu"):
        try:
            return ImageFont.truetype(f"{base}/DejaVuSans{'-Bold' if bold else ''}.ttf", sz)
        except Exception:
            continue
    return ImageFont.load_default()

def png_for(v2row, tif, resolve_geom):
    g = resolve_geom(v2row["geometry_version"])
    m = ReviewTargetMarker(target_id=v2row["anchor_id"], target_label=v2row.get("target_label") or "T01",
        offset_x_m=float(v2row["target_offset_x_m"]), offset_y_m=float(v2row["target_offset_y_m"]),
        search_radius_m=float(v2row.get("search_radius_m") or 10.0))
    return target_crop_review_png_path(pathlib.Path(tif), m, chip_size_m=float(v2row["chip_size_m"]),
        crop_context_multiplier=g.crop_context_multiplier, min_crop_size_m=g.min_crop_size_m,
        min_output_px=g.min_output_px, draw_marker=True,
        bbox_width_m=float(v2row["source_width_m"]), bbox_height_m=float(v2row["source_height_m"]))

def scored_frames(state):
    seen = {}
    for rd in state.get("rounds", []):
        for res in rd.get("results", []):
            seen[res["capture_date"]] = res
    return [seen[d] for d in sorted(seen)]

def border(pv, q):
    if q and q != "usable": return (240, 180, 40)
    if pv is True: return (40, 190, 70)
    if pv is False: return (215, 60, 60)
    return (150, 150, 150)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=pathlib.Path, required=True)
    ap.add_argument("--v2-anchors", type=pathlib.Path, required=True)
    ap.add_argument("--v1-anchors", type=pathlib.Path, required=True)
    ap.add_argument("--out-dir", type=pathlib.Path, required=True)
    args = ap.parse_args()
    V2, V1 = load_anchors(args.v2_anchors), load_anchors(args.v1_anchors)
    sheets = args.out_dir / "sheets"; sheets.mkdir(parents=True, exist_ok=True)
    piv = args.out_dir / "pivotal"; piv.mkdir(parents=True, exist_ok=True)
    F, FB = _font(13), _font(14, True); FP = _font(18, True)
    TH = 150

    def thumb(p):
        try:
            im = Image.open(p).convert("RGB"); im.thumbnail((TH, TH)); return im
        except Exception:
            return None

    def strip_row(m):
        aid = m["anchor_id"]; v2 = V2.get(aid); v1 = V1.get(aid)
        st = json.load(open(m["run3_scan_state_path"]))
        frames = scored_frames(st)
        ncol = max(len(frames), 1); cellw = TH + 6; headerh = 54
        img = Image.new("RGB", (40 + ncol * cellw, headerh + TH + 36), (28, 28, 32)); dr = ImageDraw.Draw(img)
        v1off = (abs(float(v1["target_offset_x_m"])) + abs(float(v1["target_offset_y_m"]))) if v1 else 0.0
        dr.text((8, 6), f"{aid.split('_')[-1]}  [{m['stratum']}]  {m['run2_class']}->{m['run3_class']}   "
                f"R2:{m['run2_status'].replace('done_','')} mid={m['run2_mid'] or '-'}   "
                f"R3:{m['run3_status'].replace('done_','')} mid={m['run3_mid'] or '-'}   v1_offset={v1off:.1f}m "
                f"area={v2['source_width_m'] if v2 else '?'}x{v2['source_height_m'] if v2 else '?'}m", fill=(235, 235, 235), font=FB)
        dr.text((8, 26), f"R3 latest_absent={m['run3_latest_absent'] or '-'} earliest_present={m['run3_earliest_present'] or '-'} "
                f"notes={m['run3_notes'][:90]}", fill=(180, 180, 190), font=F)
        for i, res in enumerate(frames):
            cx = 40 + i * cellw; y0 = headerh; pv = res.get("pv_present"); q = res.get("quality_flag")
            p = png_for(v2, res["chip_path"], resolve_chip_geometry) if v2 else None
            th = thumb(p) if p and p.exists() else None
            if th is not None: img.paste(th, (cx + 2, y0 + 2))
            else: dr.rectangle([cx + 2, y0 + 2, cx + TH, y0 + TH], fill=(60, 60, 66))
            col = border(pv, q); dr.rectangle([cx, y0, cx + TH + 3, y0 + TH + 3], outline=col, width=3)
            dr.text((cx + 2, y0 + TH + 6), res["capture_date"], fill=(225, 225, 225), font=F)
            dr.text((cx + 2, y0 + TH + 18), f"{'PV' if pv else 'ab' if pv is False else '?'}/{(q or '?')[:2]}", fill=col, font=FB)
        return img

    def pivotal_panel(m):
        aid = m["anchor_id"]; v2 = V2[aid]; st = json.load(open(m["run3_scan_state_path"])); fr = scored_frames(st)
        absents = [r for r in fr if r.get("pv_present") is False and r.get("quality_flag") == "usable"]
        presents = [r for r in fr if r.get("pv_present") is True]
        picks = []
        if absents: picks.append(("LAST-ABSENT", absents[-1]))
        if presents:
            picks.append(("1st-PRESENT", presents[0]))
            if len(presents) > 1: picks.append(("last-PRESENT", presents[-1]))
        tiles = []
        for tag, res in picks:
            try: im = Image.open(png_for(v2, res["chip_path"], resolve_chip_geometry)).convert("RGB")
            except Exception: im = Image.new("RGB", (256, 256), (60, 60, 60))
            if im.width < 256: im = im.resize((256, 256))
            tiles.append((tag, res, im))
        tw = max((t[2].width for t in tiles), default=256); th = max((t[2].height for t in tiles), default=256)
        img = Image.new("RGB", (20 + max(len(tiles), 1) * (tw + 12), 64 + th + 80), (22, 22, 26)); dr = ImageDraw.Draw(img)
        dr.text((8, 6), f"{aid.split('_')[-1]} [{m['stratum']}] R3={m['run3_status'].replace('done_','')} mid={m['run3_mid'] or '-'} "
                f"latest_absent={m['run3_latest_absent'] or '-'} earliest_present={m['run3_earliest_present'] or '-'}", fill=(240, 240, 240), font=FP)
        x = 10
        for tag, res, im in tiles:
            img.paste(im, (x, 64)); col = (40, 190, 70) if res.get("pv_present") else (215, 60, 60)
            dr.rectangle([x, 64, x + im.width, 64 + im.height], outline=col, width=3)
            dr.text((x, 38), f"{tag} {res['capture_date']} pv={res.get('pv_present')} q={res.get('quality_flag')}", fill=col, font=FP)
            ev = res.get("evidence") or ""
            dr.text((x, 70 + im.height), ev[:64], fill=(200, 200, 205), font=FP)
            dr.text((x, 94 + im.height), ev[64:128], fill=(200, 200, 205), font=FP)
            x += im.width + 12
        return img

    rows = list(csv.DictReader(open(args.manifest)))
    by = collections.defaultdict(list)
    for m in rows: by[m["stratum"]].append(m)
    PER = 6
    for strat in sorted(by):
        grp = by[strat]
        for s in range(0, len(grp), PER):
            chunk = grp[s:s + PER]; imgs = [strip_row(m) for m in chunk]
            W = max(i.width for i in imgs); H = sum(i.height for i in imgs) + 2 * len(imgs)
            sheet = Image.new("RGB", (W, H), (15, 15, 18)); y = 0
            for im in imgs: sheet.paste(im, (0, y)); y += im.height + 2
            sheet.save(sheets / f"{strat}_{s // PER + 1:02d}.png")
        # pivotal: all sampled anchors of this stratum, PER per sheet
        for s in range(0, len(grp), PER):
            chunk = grp[s:s + PER]; imgs = [pivotal_panel(m) for m in chunk]
            W = max(i.width for i in imgs); H = sum(i.height for i in imgs) + 2 * len(imgs)
            sheet = Image.new("RGB", (W, H), (12, 12, 15)); y = 0
            for im in imgs: sheet.paste(im, (0, y)); y += im.height + 2
            sheet.save(piv / f"{strat}_{s // PER + 1:02d}.png")
        print(f"{strat}: {len(grp)} anchors")
    print(f"sheets -> {sheets}\npivotal -> {piv}")

if __name__ == "__main__":
    main()
