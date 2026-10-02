#!/usr/bin/env python3
"""H3-7 spatial coverage + satellite availability maps.

Universe = urban-domain cells ∪ Vexcel cells (the research-object grid,
not cadastral hinterland). Satellite dates join only where we probed:
Vexcel-covered unique H3-7 centroids.

Three map modes share the same hexes:
  coverage   Vexcel vs urban-centre gap vs peri-urban gap
  tm_dates   TM date-count on probed cells; unprobed urban as outline
  tm_2023    whether that cell has a 2023 TM date (Joburg 2023 hole)
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

import h3

from scripts.temporal.satellite_year_availability import (
    BRIEF_DIR,
    DOCS_CATALOG,
    MUNI_ORDER,
    MUNI_SHORT,
    MUNI_ZH,
    utc_today,
)

VEXCEL_DERIVED = (
    Path.home() / "zasolar_data/geid_temporal/vexcel_muni_h3r7_2026-09-06/derived"
)
DEFAULT_PROBE_ROOT = (
    Path.home() / "zasolar_data/geid_temporal/satellite_vexcel_h3r7_gehi_info_2026-09-13"
)

SPATIAL_FIELDS = [
    "municipality",
    "municipality_name",
    "h3_index",
    "lat",
    "lng",
    "in_urban_centre",
    "in_urban_domain",
    "in_vexcel",
    "vexcel_coverage_frac",
    "coverage_class",
    "probed",
    "tm_n_dates",
    "tm_n_years",
    "tm_has_2023",
    "tm_cell_class",
    "wayback_n_dates",
    "union_n_dates",
]
CITY_SPATIAL_FIELDS = [
    "municipality",
    "municipality_name",
    "n_urban_centre",
    "n_urban_domain",
    "n_vexcel",
    "n_urban_centre_gap",
    "frac_uc_with_vexcel",
    "n_probed",
    "n_tm_2023",
    "frac_vexcel_with_tm_2023",
    "median_tm_dates",
    "n_tm_dense",
    "n_tm_usable",
    "n_tm_thin",
]


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "t", "yes"}


def coverage_class(*, in_vexcel: bool, in_urban_centre: bool, in_urban_domain: bool) -> str:
    if in_vexcel:
        return "vexcel"
    if in_urban_centre:
        return "urban_centre_gap"
    if in_urban_domain:
        return "urban_domain_gap"
    return "other"


def tm_cell_class(n_dates: int | None) -> str:
    if n_dates is None:
        return "unprobed"
    if n_dates >= 90:
        return "dense"
    if n_dates >= 50:
        return "usable"
    if n_dates > 0:
        return "thin"
    return "empty"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    return list(csv.DictReader(path.open(encoding="utf-8")))


def hex_ring(h3_index: str) -> list[tuple[float, float]]:
    """Return closed (lng, lat) ring."""
    boundary = h3.cell_to_boundary(h3_index)
    ring = [(float(lng), float(lat)) for lat, lng in boundary]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def build_spatial_rows(
    *,
    urban_rows: Sequence[Mapping[str, str]],
    vexcel_rows: Sequence[Mapping[str, str]],
    muni_cells: Sequence[Mapping[str, str]],
    sat_cells: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    xy: dict[str, tuple[float, float]] = {}
    for row in muni_cells:
        xy[row["h3_index"]] = (float(row["lat"]), float(row["lng"]))
    for row in vexcel_rows:
        xy.setdefault(row["h3_index"], (float(row["lat"]), float(row["lng"])))

    vexcel_frac: dict[tuple[str, str], float] = {}
    vexcel_keys: set[tuple[str, str]] = set()
    for row in vexcel_rows:
        key = (row["municipality"], row["h3_index"])
        vexcel_keys.add(key)
        prev = vexcel_frac.get(key, 0.0)
        vexcel_frac[key] = max(prev, float(row.get("vexcel_coverage_frac") or 0.0))

    sat: dict[str, Mapping[str, str]] = {r["h3_index"]: r for r in sat_cells}

    flags: dict[tuple[str, str], dict[str, bool]] = {}
    for row in urban_rows:
        key = (row["municipality"], row["h3_index"])
        flags[key] = {
            "in_urban_centre": _as_bool(row["in_urban_centre"]),
            "in_urban_domain": _as_bool(row["in_urban_domain"]),
            "in_vexcel": _as_bool(row["in_vexcel"]),
        }
    for muni, h3_index in vexcel_keys:
        key = (muni, h3_index)
        if key not in flags:
            flags[key] = {
                "in_urban_centre": False,
                "in_urban_domain": False,
                "in_vexcel": True,
            }
        else:
            flags[key]["in_vexcel"] = True

    out: list[dict[str, Any]] = []
    for (muni, h3_index), fl in sorted(flags.items(), key=lambda kv: (MUNI_ORDER.index(kv[0][0]) if kv[0][0] in MUNI_ORDER else 99, kv[0][1])):
        lat, lng = xy.get(h3_index, (None, None))
        if lat is None:
            lat, lng = h3.cell_to_latlng(h3_index)
        sat_row = sat.get(h3_index)
        probed = sat_row is not None
        tm_n = int(sat_row["tm_n_dates"]) if probed and sat_row.get("tm_n_dates") != "" else None
        tm_years = str(sat_row.get("tm_years") or "") if probed else ""
        tm_has_2023 = bool(probed and "2023" in {y.strip() for y in tm_years.split(",") if y.strip()})
        cov = coverage_class(
            in_vexcel=fl["in_vexcel"],
            in_urban_centre=fl["in_urban_centre"],
            in_urban_domain=fl["in_urban_domain"],
        )
        out.append(
            {
                "municipality": muni,
                "municipality_name": MUNI_SHORT.get(muni, muni),
                "h3_index": h3_index,
                "lat": round(float(lat), 7),
                "lng": round(float(lng), 7),
                "in_urban_centre": "yes" if fl["in_urban_centre"] else "no",
                "in_urban_domain": "yes" if fl["in_urban_domain"] else "no",
                "in_vexcel": "yes" if fl["in_vexcel"] else "no",
                "vexcel_coverage_frac": round(vexcel_frac.get((muni, h3_index), 0.0), 4) if fl["in_vexcel"] else "",
                "coverage_class": cov,
                "probed": "yes" if probed else "no",
                "tm_n_dates": tm_n if tm_n is not None else "",
                "tm_n_years": sat_row.get("tm_n_years", "") if probed else "",
                "tm_has_2023": "yes" if tm_has_2023 else ("no" if probed else ""),
                "tm_cell_class": tm_cell_class(tm_n),
                "wayback_n_dates": sat_row.get("wayback_n_dates", "") if probed else "",
                "union_n_dates": sat_row.get("union_n_dates", "") if probed else "",
            }
        )
    return out


def city_spatial_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["municipality"]].append(row)
    out = []
    for muni in MUNI_ORDER:
        cells = grouped.get(muni, [])
        uc = [c for c in cells if c["in_urban_centre"] == "yes"]
        ud = [c for c in cells if c["in_urban_domain"] == "yes"]
        vx = [c for c in cells if c["in_vexcel"] == "yes"]
        uc_gap = [c for c in uc if c["in_vexcel"] != "yes"]
        probed = [c for c in vx if c["probed"] == "yes"]
        tm2023 = [c for c in probed if c["tm_has_2023"] == "yes"]
        tm_dates = [int(c["tm_n_dates"]) for c in probed if c["tm_n_dates"] != ""]
        median = round(float(statistics.median(tm_dates)), 1) if tm_dates else ""
        out.append(
            {
                "municipality": muni,
                "municipality_name": MUNI_SHORT[muni],
                "n_urban_centre": len(uc),
                "n_urban_domain": len(ud),
                "n_vexcel": len(vx),
                "n_urban_centre_gap": len(uc_gap),
                "frac_uc_with_vexcel": round((len(uc) - len(uc_gap)) / len(uc), 4) if uc else "",
                "n_probed": len(probed),
                "n_tm_2023": len(tm2023),
                "frac_vexcel_with_tm_2023": round(len(tm2023) / len(probed), 4) if probed else "",
                "median_tm_dates": median,
                "n_tm_dense": sum(1 for c in probed if c["tm_cell_class"] == "dense"),
                "n_tm_usable": sum(1 for c in probed if c["tm_cell_class"] == "usable"),
                "n_tm_thin": sum(1 for c in probed if c["tm_cell_class"] == "thin"),
            }
        )
    return out


def _svg_for_city(cells: Sequence[Mapping[str, Any]], *, width: int = 340, height: int = 300) -> str:
    rings: list[tuple[Mapping[str, Any], list[tuple[float, float]]]] = []
    xs: list[float] = []
    ys: list[float] = []
    for cell in cells:
        ring = hex_ring(cell["h3_index"])
        rings.append((cell, ring))
        for x, y in ring:
            xs.append(x)
            ys.append(-y)
    if not xs:
        return f'<svg width="{width}" height="{height}"></svg>'
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    pad = 0.04
    dx = max(maxx - minx, 1e-6)
    dy = max(maxy - miny, 1e-6)
    minx -= dx * pad
    maxx += dx * pad
    miny -= dy * pad
    maxy += dy * pad
    dx = maxx - minx
    dy = maxy - miny
    scale = min((width - 8) / dx, (height - 8) / dy)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" data-n="{len(cells)}">'
    ]
    for cell, ring in rings:
        pts = []
        for lng, lat in ring[:-1]:
            px = 4 + (lng - minx) * scale
            py = 4 + ((-lat) - miny) * scale
            pts.append(f"{px:.1f},{py:.1f}")
        tm = cell["tm_n_dates"] if cell["tm_n_dates"] != "" else ""
        title = (
            f"{cell['h3_index']} {cell['coverage_class']} "
            f"TM={tm or '—'} 2023={cell['tm_has_2023'] or '—'}"
        )
        parts.append(
            "<polygon "
            f'points="{" ".join(pts)}" '
            f'data-cover="{escape(cell["coverage_class"])}" '
            f'data-tmclass="{escape(cell["tm_cell_class"])}" '
            f'data-tm="{escape(str(tm))}" '
            f'data-y2023="{escape(str(cell["tm_has_2023"]))}" '
            f'data-uc="{escape(cell["in_urban_centre"])}" '
            f'class="hex" '
            f'title="{escape(title)}">'
            f"<title>{escape(title)}</title>"
            "</polygon>"
        )
    parts.append("</svg>")
    return "".join(parts)


CSS = """
:root{
  --bg:#0f1419; --card:#1e2834; --bg3:#243040; --text:#e8eef4; --muted:#9aabbc;
  --accent:#f0a202; --teal:#3dd6c6; --border:rgba(255,255,255,.08);
  --red:#ff8686; --font:"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
}
*{box-sizing:border-box}
body{margin:0;font-family:var(--font);background:var(--bg);color:var(--text);line-height:1.6;font-size:16px}
a{color:var(--teal)}
.wrap{max-width:1180px;margin:0 auto;padding:28px 18px 72px}
.hero{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;margin-bottom:20px}
h1{margin:0 0 8px;font-size:clamp(1.3rem,2.6vw,1.85rem)}
.sub{color:var(--muted);max-width:84ch}
.pill{display:inline-block;background:#1a222c;border:1px solid var(--border);border-radius:999px;padding:4px 10px;font-size:.82rem;color:var(--muted);margin:3px 4px 3px 0}
.pill b{color:var(--text)}
h2{font-size:1.2rem;margin:26px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border)}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 14px;margin-bottom:10px}
.callout{border-left:4px solid var(--accent);background:#2a2418;padding:12px 14px;border-radius:0 10px 10px 0;margin:12px 0}
.callout.red{border-left-color:#ff8686;background:#2a1a1a}
.why{margin:.35rem 0 0;color:#d5dee8;font-size:.92rem}
.muted{color:var(--muted);font-size:.9rem}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 14px}
.sw{display:inline-flex;align-items:center;gap:6px;font-size:.8rem;color:var(--muted)}
.sw i{width:14px;height:14px;border-radius:3px;display:inline-block;border:1px solid var(--border)}
.modes{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.modes label{background:#1a222c;border:1px solid var(--border);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:.88rem}
.modes input{margin-right:6px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
svg{background:#141a22;border-radius:10px;border:1px solid var(--border)}
.hex{stroke:#0f1419;stroke-width:.6;fill:#243040}
body[data-mode="coverage"] .hex[data-cover="vexcel"]{fill:#147a6a}
body[data-mode="coverage"] .hex[data-cover="urban_centre_gap"]{fill:#7a2430}
body[data-mode="coverage"] .hex[data-cover="urban_domain_gap"]{fill:#3a4450}
body[data-mode="tm_dates"] .hex[data-tmclass="unprobed"]{fill:#1a222c;stroke:#3a4450}
body[data-mode="tm_dates"] .hex[data-tmclass="thin"]{fill:#7a5a14}
body[data-mode="tm_dates"] .hex[data-tmclass="usable"]{fill:#1e5c4a}
body[data-mode="tm_dates"] .hex[data-tmclass="dense"]{fill:#147a6a}
body[data-mode="tm_dates"] .hex[data-tmclass="empty"]{fill:#7a2430}
body[data-mode="tm_2023"] .hex[data-y2023=""]{fill:#1a222c;stroke:#3a4450}
body[data-mode="tm_2023"] .hex[data-y2023="yes"]{fill:#147a6a}
body[data-mode="tm_2023"] .hex[data-y2023="no"]{fill:#7a2430}
table.plain{width:100%;border-collapse:collapse;font-size:.86rem;background:var(--card)}
table.plain th,table.plain td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left}
table.plain th{background:var(--bg3);color:var(--accent);font-size:.72rem;letter-spacing:.04em;text-transform:uppercase}
td.num{font-variant-numeric:tabular-nums}
footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--border);color:var(--muted);font-size:.84rem}
.scroll{overflow-x:auto}
"""


def render_html(
    rows: Sequence[Mapping[str, Any]],
    city_rows: Sequence[Mapping[str, Any]],
    date: str,
) -> str:
    by_muni: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_muni[row["municipality"]].append(row)
    cards = []
    for muni in MUNI_ORDER:
        svg = _svg_for_city(by_muni.get(muni, []))
        summary = next(c for c in city_rows if c["municipality"] == muni)
        cards.append(
            f'<div class="card"><h3 style="margin:0 0 6px">{escape(MUNI_ZH[muni])}</h3>'
            f'<p class="muted" style="margin:0 0 8px">城区无 Vexcel {summary["n_urban_centre_gap"]}/{summary["n_urban_centre"]}'
            f' · 已探 {summary["n_probed"]} · TM2023 {summary["n_tm_2023"]}/{summary["n_probed"]}</p>'
            f"{svg}</div>"
        )
    gauteng = by_muni["johannesburg"] + by_muni["ekurhuleni"] + by_muni["tshwane"]
    gauteng_svg = _svg_for_city(gauteng, width=720, height=420)
    table_body = []
    for r in city_rows:
        table_body.append(
            "<tr>"
            f"<td>{escape(MUNI_ZH[r['municipality']])}</td>"
            f"<td class='num'>{r['n_urban_centre']}</td>"
            f"<td class='num'>{r['n_urban_centre_gap']}</td>"
            f"<td class='num'>{r['frac_uc_with_vexcel']}</td>"
            f"<td class='num'>{r['n_vexcel']}</td>"
            f"<td class='num'>{r['n_probed']}</td>"
            f"<td class='num'>{r['n_tm_2023']}</td>"
            f"<td class='num'>{r['frac_vexcel_with_tm_2023']}</td>"
            f"<td class='num'>{r['median_tm_dates']}</td>"
            "</tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>H3-7 空间覆盖 / 卫星可用性</title>
<style>{CSS}</style>
</head>
<body data-mode="coverage">
<div class="wrap">
<header class="hero">
  <h1>H3-7 对齐：空间覆盖和卫星可用性</h1>
  <p class="sub">同一套 Uber H3 第 7 级格子（约 4.9 km²）。底图是
  <b>城区 + Vexcel 足迹</b>，不是全市腹地。卫星日期只接在 Vexcel 格子上
  （这次质心 info 的样本）。红格在「覆盖」模式下是城区没有 Vexcel 的洞；
  在「2023」模式下是有 Vexcel 但这一年没有 TM。</p>
  <span class="pill"><b>日期</b> {escape(date)}</span>
  <span class="pill"><b>格子</b> {len(rows)} 条城×H3</span>
  <span class="pill"><b>对齐</b> 与年密度图同一套 h3_index</span>
</header>
<div class="modes">
  <label><input type="radio" name="mode" value="coverage" checked> 覆盖：Vexcel vs 城区空洞</label>
  <label><input type="radio" name="mode" value="tm_dates"> 可用性：TM 日期数</label>
  <label><input type="radio" name="mode" value="tm_2023"> 2023：这一格有没有 TM</label>
</div>
<div class="legend" id="legend-coverage">
  <span class="sw"><i style="background:#147a6a"></i>Vexcel（已探卫星）</span>
  <span class="sw"><i style="background:#7a2430"></i>城区无 Vexcel（空间空洞，优先）</span>
  <span class="sw"><i style="background:#3a4450"></i>城郊无 Vexcel</span>
</div>
<div class="legend" id="legend-tm" style="display:none">
  <span class="sw"><i style="background:#147a6a"></i>密 ≥90 日</span>
  <span class="sw"><i style="background:#1e5c4a"></i>可用 50–89 日</span>
  <span class="sw"><i style="background:#7a5a14"></i>薄 &lt;50 日</span>
  <span class="sw"><i style="background:#1a222c"></i>未探（无 Vexcel）</span>
</div>
<div class="legend" id="legend-2023" style="display:none">
  <span class="sw"><i style="background:#147a6a"></i>该格 2023 有 TM</span>
  <span class="sw"><i style="background:#7a2430"></i>有 Vexcel、2023 无 TM</span>
  <span class="sw"><i style="background:#1a222c"></i>未探</span>
</div>
<div class="callout red">
<b>空间上约翰内斯堡 2023 是两层洞。</b> 城区 264 格里约 192 格根本没有 Vexcel，
所以不在卫星样本里。剩下的 Vexcel 内城格，TM 2023 又只盖一部分（热图里的 32/74）。
<p class="why">不管会怎样：会把「约翰内斯堡有 2023 卫星年」读成全市城区都有图。</p>
</div>
<h2>分城 H3-7</h2>
<div class="grid">{''.join(cards)}</div>
<h2>Gauteng 三城拼图（JHB + Ekurhuleni + 茨瓦内）</h2>
<p class="muted">同一投影。可以看到 Vexcel 是内城岛屿，城区空洞在约翰内斯堡西南和 Ekurhuleni 连成一片。</p>
<div class="card">{gauteng_svg}</div>
<h2>对齐后的计数</h2>
<div class="scroll"><table class="plain">
<thead><tr><th>城</th><th>城区格</th><th>城区无 Vexcel</th><th>Vexcel / 城区</th><th>Vexcel 格</th><th>已探</th><th>TM 2023</th><th>Vexcel 中有 2023</th><th>TM 中位日</th></tr></thead>
<tbody>{''.join(table_body)}</tbody>
</table></div>
<p class="muted">CSV：<a href="_muni_catalog/satellite_vexcel_h3r7_spatial.csv">satellite_vexcel_h3r7_spatial.csv</a>
 · <a href="_muni_catalog/satellite_vexcel_h3r7_city_spatial.csv">satellite_vexcel_h3r7_city_spatial.csv</a>
 · 年维图 <a href="FIG-satellite-year-heatmap-2026-09-14.html">FIG-satellite-year-heatmap</a></p>
<footer>H3-7 边界由 h3.cell_to_boundary 生成。不画全市腹地。卫星仍是质心 info，不是整格完整 availability。</footer>
</div>
<script>
const legends={{coverage:'legend-coverage', tm_dates:'legend-tm', tm_2023:'legend-2023'}};
function setMode(m){{
  document.body.dataset.mode=m;
  for (const [k,id] of Object.entries(legends)) {{
    document.getElementById(id).style.display = (k===m) ? 'flex' : 'none';
  }}
}}
document.querySelectorAll('input[name=mode]').forEach(el=>el.addEventListener('change',()=>setMode(el.value)));
</script>
</body>
</html>
"""


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields))
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def write_geojson(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """One feature per unique h3 (first municipality wins; spill listed in properties)."""
    by_h3: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_h3[row["h3_index"]].append(row)
    features = []
    for h3_index, group in by_h3.items():
        primary = group[0]
        ring = [[round(x, 6), round(y, 6)] for x, y in hex_ring(h3_index)]
        props = {
            "h3_index": h3_index,
            "municipalities": "|".join(r["municipality"] for r in group),
            "coverage_class": primary["coverage_class"],
            "in_urban_centre": primary["in_urban_centre"],
            "in_vexcel": primary["in_vexcel"],
            "probed": primary["probed"],
            "tm_n_dates": primary["tm_n_dates"],
            "tm_cell_class": primary["tm_cell_class"],
            "tm_has_2023": primary["tm_has_2023"],
        }
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vexcel-derived", type=Path, default=VEXCEL_DERIVED)
    parser.add_argument("--probe-root", type=Path, default=DEFAULT_PROBE_ROOT)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    urban = _read_csv(args.vexcel_derived / "vexcel_urban_h3_r7.csv")
    vexcel = _read_csv(DOCS_CATALOG / "vexcel_muni_h3_r7.csv")
    muni_cells = _read_csv(args.vexcel_derived / "municipality_h3_r7_cells.csv")
    sat = _read_csv(DOCS_CATALOG / "satellite_vexcel_h3r7_cell.csv")
    rows = build_spatial_rows(urban_rows=urban, vexcel_rows=vexcel, muni_cells=muni_cells, sat_cells=sat)
    city_rows = city_spatial_summary(rows)
    date = utc_today()
    html = render_html(rows, city_rows, date)
    derived = args.probe_root / "derived"
    write_csv(derived / "h3_spatial.csv", rows, SPATIAL_FIELDS)
    write_csv(derived / "h3_city_spatial.csv", city_rows, CITY_SPATIAL_FIELDS)
    write_geojson(derived / "h3_spatial.geojson", rows)
    (derived / "FIG-h3-spatial-coverage.html").write_text(html, encoding="utf-8")
    if args.publish:
        write_csv(DOCS_CATALOG / "satellite_vexcel_h3r7_spatial.csv", rows, SPATIAL_FIELDS)
        write_csv(DOCS_CATALOG / "satellite_vexcel_h3r7_city_spatial.csv", city_rows, CITY_SPATIAL_FIELDS)
        (BRIEF_DIR / "FIG-h3-spatial-coverage-2026-09-14.html").write_text(html, encoding="utf-8")
        print(f"published {BRIEF_DIR / 'FIG-h3-spatial-coverage-2026-09-14.html'}")
    print(f"spatial rows={len(rows)} cities={len(city_rows)} geojson -> {derived / 'h3_spatial.geojson'}")


if __name__ == "__main__":
    main()
