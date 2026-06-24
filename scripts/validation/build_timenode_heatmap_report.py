#!/usr/bin/env python3
"""Build a self-contained HTML report for the per-grid time-node heatmaps (Task 3).

Embeds the two heatmap PNGs (base64) and computes census-anchored stats from the
probe CSVs + valid matrices. Output is a single portable .html in the existing
ZAsolar dark-theme deliverable style.
"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path

import pandas as pd

RESULTS = Path(__file__).resolve().parents[2] / "results" / "timenode_heatmaps"
DATA = Path(__file__).resolve().parents[2] / "data" / "timenode_heatmaps"


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def city_stats(probe_csv: Path, census: str) -> dict:
    df = pd.read_csv(probe_csv, dtype={"grid_id": str})
    df["d"] = pd.to_datetime(df["capture_date"], errors="coerce")
    df = df[df["d"].notna()].drop_duplicates(["grid_id", "capture_date"])
    ng = df["grid_id"].nunique()
    C = pd.Timestamp(census)
    valid = df[df["d"] <= C]
    post = df[df["d"] > C]
    vc = valid.groupby("grid_id")["capture_date"].nunique()
    # yearly valid coverage
    valid_yr = valid.assign(y=valid["d"].dt.year)
    cov = valid_yr.groupby("y")["grid_id"].nunique() / ng
    # most recent year with >=90% valid coverage (the recent solid backdating anchor)
    full_years = [int(y) for y, c in cov.items() if c >= 0.90]
    last_full = max(full_years) if full_years else None
    cov2023 = float(cov.get(2023, 0.0))
    return {
        "grids": ng,
        "census": census,
        "valid_total": int(len(valid)),
        "post_total": int(len(post)),
        "med": int(vc.median()),
        "mn": int(vc.min()),
        "mx": int(vc.max()),
        "last_full": last_full,
        "cov2023": cov2023,
        "cov2024": float(cov.get(2024, 0.0)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=RESULTS / "timenode_heatmaps_report.html")
    args = ap.parse_args()

    ct = city_stats(DATA / "gehi_info_cpt.csv", "2025-01-31")
    jhb = city_stats(DATA / "gehi_info_jnb.csv", "2024-04-30")
    ct_png = b64(RESULTS / "ct_timenodes_heatmap.png")
    jhb_png = b64(RESULTS / "jhb_timenodes_heatmap.png")
    ct_spatial = b64(RESULTS / "ct_timenodes_spatial.png")
    jhb_spatial = b64(RESULTS / "jhb_timenodes_spatial.png")

    def cards(s, census_label):
        return f"""
        <div class="cards">
          <div class="card"><div class="card-val">{s['grids']:,}</div><div class="card-lab">grid cells probed</div></div>
          <div class="card"><div class="card-val">{s['med']}</div><div class="card-lab">median valid time-nodes / grid</div></div>
          <div class="card"><div class="card-val">{s['last_full']}</div><div class="card-lab">recent year ≥90% valid coverage</div></div>
          <div class="card"><div class="card-val">{s['cov2023']*100:.0f}%</div><div class="card-lab">grids with 2023 imagery</div></div>
        </div>
        <p class="hint">Census node = <code>{census_label}</code>. Valid backdating anchors (≤ census):
        <b>{s['valid_total']:,}</b> grid×date · post-census imagery (shown grey): {s['post_total']:,} ·
        valid time-nodes/grid range {s['mn']}–{s['mx']}.</p>
        """

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Per-grid satellite time-node heatmaps — JHB &amp; CT</title>
<style>
  :root {{ --bg:#0f1117; --panel:#171a21; --line:#262b36; --txt:#e6e9ef; --mut:#9aa3b2; --acc:#2a9d6f; --warn:#c2554d; }}
  body {{ margin:0; background:var(--bg); color:var(--txt);
    font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width:1040px; margin:0 auto; padding:34px 22px 90px; }}
  h1 {{ font-size:25px; margin:0 0 4px; letter-spacing:-.2px; }}
  .meta {{ color:var(--mut); font-size:13px; margin-bottom:20px; }}
  code {{ background:var(--panel); padding:1px 6px; border-radius:5px; color:#bcd; font-size:.92em; }}
  h2 {{ font-size:17px; margin:38px 0 6px; }}
  h2 .tag {{ font-size:11px; font-weight:600; color:var(--acc); border:1px solid #2a9d6f55;
    border-radius:20px; padding:1px 9px; margin-left:8px; vertical-align:middle; }}
  .hint {{ color:var(--mut); font-size:13px; margin:0 0 14px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; margin:16px 0 6px; }}
  .card {{ flex:1 1 170px; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
  .card-val {{ font-size:26px; font-weight:700; letter-spacing:-.5px; }}
  .card-lab {{ color:var(--mut); font-size:12.5px; margin-top:3px; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin:10px 0; }}
  figure {{ margin:14px 0 4px; }}
  figure img {{ width:100%; height:auto; border:1px solid var(--line); border-radius:10px; background:#fff; }}
  figcaption {{ color:var(--mut); font-size:12px; margin-top:6px; }}
  .note {{ background:#14171e; border-left:3px solid #d98a3d; border-radius:0 8px 8px 0;
    padding:12px 16px; color:var(--mut); font-size:13px; margin:14px 0; }}
  .note.ok {{ border-left-color:var(--acc); }}
  .note b {{ color:var(--txt); }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px; margin:10px 0; }}
  th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--mut); font-weight:600; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  ul {{ margin:8px 0; padding-left:20px; }} li {{ margin:4px 0; }}
  footer {{ color:var(--mut); font-size:12px; margin-top:48px; border-top:1px solid var(--line); padding-top:16px; }}
</style></head>
<body><div class="wrap">

<h1>Per-grid satellite time-node heatmaps — Johannesburg &amp; Cape Town</h1>
<div class="meta">ZAsolar / <code>solar_backdating</code> · weekly deliverable ③ · 2026-06-22 ·
source: GEHistoricalImagery (Google Time Machine, provider <code>TM</code>, zoom 19, centroid probe)</div>

<div class="note">
<b>What this is.</b> For every task-grid cell, the set of <b>satellite imagery capture dates</b> Google Time
Machine holds at the cell centroid — the raw material backdating uses to date each detected installation.
Counts are <b>census-anchored</b>: only captures <b>at or before the census base-map date</b> are valid
backdating anchors (blue); imagery captured after the census still exists but cannot date a census-time
detection, so it is shown grey and excluded from the count. The count is taken <b>backward from the census node</b>.
</div>

<h2>Cape Town <span class="tag">aerial census 2025-01</span></h2>
{cards(ct, "aerial census 2025-01")}
<figure><img alt="Cape Town time-node heatmap" src="data:image/png;base64,{ct_png}">
<figcaption>2083 CPT cells × 2009–2026. Red dashed line = census node (Jan 2025). Blue = valid anchors (≤ census);
grey = post-census imagery (mostly the Feb-2025 Google vintage + 2026).</figcaption></figure>
<figure><img alt="Cape Town spatial distribution" src="data:image/png;base64,{ct_spatial}">
<figcaption>Spatial view — left: # valid time-nodes per cell (backdating depth, deeper in the central metro);
right: year of the most-recent valid capture (mostly 2024, dropping to 2023 in the south).</figcaption></figure>
<div class="note">
<b>Why 2024 matters here.</b> The census base map is January 2025, but Google Time Machine has a Jan-2025
vintage for only <b>3.4% of CT grids</b>; the large "2025" Google vintage is <b>February 2025</b> — <i>after</i>
the census, hence invalid for dating a Jan-2025 detection. So <b>2024 is the most recent near-fully-covered
valid year ({ct['cov2024']*100:.0f}% of grids)</b> and the workhorse recent anchor for CT backdating.
</div>

<h2>Johannesburg <span class="tag">Vexcel census 2024 (Feb–Apr)</span></h2>
{cards(jhb, "Vexcel census 2024 (Feb–Apr)")}
<figure><img alt="Johannesburg time-node heatmap" src="data:image/png;base64,{jhb_png}">
<figcaption>382 JNB Vexcel cells × 2009–2026. Red dashed line = census node (end of the Vexcel 2024 Feb–Apr
collection). Blue = valid anchors (≤ census); grey = post-census imagery (the 2024-10 Google vintage + all
of 2025–2026).</figcaption></figure>
<figure><img alt="Johannesburg spatial distribution" src="data:image/png;base64,{jhb_spatial}">
<figcaption>Spatial view — left: # valid time-nodes per cell (backdating depth, deeper east/central, thinner west);
right: year of the most-recent valid capture (near-uniformly 2024).</figcaption></figure>
<div class="note">
<b>The 2023 hole sits right before the census.</b> JHB's detection base map is the Feb–Apr-2024 Vexcel ortho, so
the most recent pre-census year is 2023 — and Google Time Machine covers <b>only {jhb['cov2023']*100:.0f}% of
JHB grids in 2023</b> (vs ~100% every other year 2009–2022). Backdating therefore leans on ≤2022 anchors and
the Wayback patch for the recent edge. Cape Town does not share this hole ({ct['cov2023']*100:.0f}% 2023 coverage).
</div>

<h2>Side by side</h2>
<table>
<tr><th>Metric</th><th class="num">Cape Town</th><th class="num">Johannesburg</th></tr>
<tr><td>Grid cells probed</td><td class="num">{ct['grids']:,}</td><td class="num">{jhb['grids']:,}</td></tr>
<tr><td>Census node (base map)</td><td class="num">aerial 2025-01</td><td class="num">Vexcel 2024 (Feb–Apr)</td></tr>
<tr><td>Valid (≤census) grid×date anchors</td><td class="num">{ct['valid_total']:,}</td><td class="num">{jhb['valid_total']:,}</td></tr>
<tr><td>Valid time-nodes / grid (median)</td><td class="num">{ct['med']}</td><td class="num">{jhb['med']}</td></tr>
<tr><td>Valid time-nodes / grid (min–max)</td><td class="num">{ct['mn']}–{ct['mx']}</td><td class="num">{jhb['mn']}–{jhb['mx']}</td></tr>
<tr><td>Recent year ≥90% valid coverage</td><td class="num">{ct['last_full']}</td><td class="num">{jhb['last_full']}</td></tr>
<tr><td>Grids with 2023 imagery</td><td class="num">{ct['cov2023']*100:.0f}%</td><td class="num">{jhb['cov2023']*100:.0f}%</td></tr>
<tr><td>Post-census grid×date (shown grey)</td><td class="num">{ct['post_total']:,}</td><td class="num">{jhb['post_total']:,}</td></tr>
</table>

<h2>Method</h2>
<ul>
<li><b>Probe.</b> GEHistoricalImagery <code>info</code> at each grid-cell centroid (TM, z19) — the lightweight,
    network-only availability probe; no Gemini/gateway auth involved.</li>
<li><b>Pipeline</b> (<code>solar_backdating/scripts/validation/</code>): build anchors from the task grids →
    <code>probe_gehi_timenodes.py</code> (parallel, resumable) → <code>plot_timenode_heatmap.py</code> →
    <code>build_timenode_heatmap_report.py</code>.</li>
<li><b>Coverage.</b> CT 2083/2083 and JHB 382/382 cells probed, 0 empty; ~250k (grid×date) availability records.</li>
<li><b>Counting.</b> distinct capture dates per (grid, year); census-anchored so only dates ≤ the base-map date
    are counted as valid backdating anchors.</li>
</ul>

<h2>Verification <span class="tag">adversarial, read-only</span></h2>
<div class="note ok">
Two independent skeptic agents re-derived the available-date set for 5 random grids per city straight from
<b>fresh</b> GEHI calls (not the project parser) and cross-checked the matrices:
fresh date sets == probe CSV for all 10 grids (exact); matrix == recomputed per-year counts (every cell);
grid/row counts, empties and 2023 fractions all confirmed.
</div>

<h2>Caveats</h2>
<ul>
<li><b>Centroid probe, not full-cell coverage.</b> Each value is imagery available at the cell centroid. A 1 km
    cell spans many z19 tiles whose TM date availability varies; cross-checking the JHB multi-anchor install-date
    scan shows ~3% of its observed dates occur at sub-grid points the centroid misses — a mild under-count. Both
    cities use the same method, so the JHB↔CT comparison is unaffected.</li>
<li><b>TM "date" labels</b> are Google vintage timestamps, not guaranteed acquisition instants — treat as time-nodes.</li>
<li><b>Census node is the base-map collection window-end per city.</b> CT aerial census window is 2025-01
    (node 2025-01-31); JHB Vexcel flights span 2024-02-17 to 2024-04-19 (node 2024-04-30, so the contemporaneous
    Google 2024-02-29/03-30 vintages count as valid).</li>
</ul>

<footer>Artifacts in <code>solar_backdating/results/timenode_heatmaps/</code>:
heatmap PNGs, valid-count matrices (<code>*_timenode_matrix.csv</code>), per-grid valid-node summaries
(<code>*_valid_timenodes_per_grid.csv</code>), and this report. Underlying probe data in
<code>data/timenode_heatmaps/</code>.</footer>

</div></body></html>"""

    args.out.write_text(html, encoding="utf-8")
    print(f"[report] -> {args.out}  ({len(html)//1024} KB)")


if __name__ == "__main__":
    main()
