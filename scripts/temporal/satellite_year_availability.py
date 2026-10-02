#!/usr/bin/env python3
"""Classify GEHI centroid years and emit availability CSVs + two HTML figures.

A year is not just "present in the year union". Johannesburg 2023 is the
template: TM is on 32/74 Vexcel cells with exactly one date each. That is
not a citywide satellite year even though 2023 appears in `years_union`.

Classes (applied separately to TM, Wayback, and their cell-union):
  absent      no cells
  noise       1–2 cells only (do not advertise as a city year)
  sparse_1_2  ≤2 dates/covered cell AND <50% of Vexcel cells  → priority flag
  one_look    ≤2 dates/covered cell but ≥50% spatial (year panel only)
  thin_spatial >2 dates/cell but <50% spatial
  usable      >2 dates/cell and ≥50% spatial
  dense       ≥5 dates/cell and ≥90% spatial

Year-panel OK: one_look / usable / dense.
Day-interval OK: usable / dense (almost always TM; Wayback is one_look).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROBE_ROOT = (
    Path.home() / "zasolar_data/geid_temporal/satellite_vexcel_h3r7_gehi_info_2026-09-13"
)
DOCS_CATALOG = PROJECT_ROOT / "docs/briefs/_muni_catalog"
BRIEF_DIR = PROJECT_ROOT / "docs/briefs"

MUNI_ORDER = [
    "cape_town",
    "johannesburg",
    "ethekwini",
    "ekurhuleni",
    "tshwane",
    "nelson_mandela_bay",
    "buffalo_city",
    "mangaung",
    "msunduzi",
]
MUNI_SHORT = {
    "cape_town": "Cape Town",
    "johannesburg": "Johannesburg",
    "ethekwini": "eThekwini",
    "ekurhuleni": "Ekurhuleni",
    "tshwane": "Tshwane",
    "nelson_mandela_bay": "NMB",
    "buffalo_city": "Buffalo",
    "mangaung": "Mangaung",
    "msunduzi": "Msunduzi",
}
MUNI_ZH = {
    "cape_town": "开普敦",
    "johannesburg": "约翰内斯堡",
    "ethekwini": "eThekwini",
    "ekurhuleni": "Ekurhuleni",
    "tshwane": "茨瓦内",
    "nelson_mandela_bay": "NMB",
    "buffalo_city": "Buffalo City",
    "mangaung": "Mangaung",
    "msunduzi": "Msunduzi",
}
YEARS = list(range(2009, 2027))
NOISE_CELLS = 2
SPARSE_FRAC = 0.5
ONE_LOOK_DPC = 2.0
DENSE_DPC = 5.0
DENSE_FRAC = 0.9
YEAR_PANEL_OK = frozenset({"one_look", "usable", "dense"})
DAY_OK = frozenset({"usable", "dense"})

CLASS_LABEL_ZH = {
    "absent": "无",
    "noise": "噪声",
    "sparse_1_2": "一两帧且空间残",
    "one_look": "全市一两帧",
    "thin_spatial": "空间残",
    "usable": "可用",
    "dense": "密",
}

YEAR_AVAIL_FIELDS = [
    "municipality",
    "municipality_name",
    "year",
    "source",
    "n_h3_vexcel",
    "n_cells_with_year",
    "n_date_rows",
    "frac_cells",
    "dates_per_covered_cell",
    "class",
    "priority_flag",
    "year_panel_ok",
    "day_interval_ok",
    "note",
]
CITY_USE_FIELDS = [
    "municipality",
    "municipality_name",
    "n_h3_vexcel",
    "tm_median_dates",
    "aerial_usable_years",
    "n_aerial_years",
    "vexcel_years",
    "sat_year_panel_years",
    "n_sat_year_panel",
    "sat_day_interval_years",
    "n_sat_day_interval",
    "priority_sparse_years",
    "aerial_fills_sparse",
    "wayback_fills_sparse",
    "year_panel_verdict",
    "day_interval_verdict",
    "notes",
]


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def classify_year(*, n_cells_with: int, n_h3: int, n_dates: int) -> str:
    """Return one of the seven year-classes. n_h3 is the Vexcel denominator."""
    if n_h3 <= 0:
        raise ValueError("n_h3 must be positive")
    if n_cells_with <= 0 or n_dates <= 0:
        return "absent"
    if n_cells_with <= NOISE_CELLS:
        return "noise"
    frac = n_cells_with / n_h3
    dpc = n_dates / n_cells_with
    if dpc <= ONE_LOOK_DPC and frac < SPARSE_FRAC:
        return "sparse_1_2"
    if dpc <= ONE_LOOK_DPC:
        return "one_look"
    if frac < SPARSE_FRAC:
        return "thin_spatial"
    if dpc >= DENSE_DPC and frac >= DENSE_FRAC:
        return "dense"
    return "usable"


def parse_year_list(text: str) -> list[int]:
    if not text or not str(text).strip():
        return []
    out: list[int] = []
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def parse_vexcel_years(capture: str) -> list[int]:
    years: set[int] = set()
    for token in capture.replace("…", " ").replace("...", " ").replace("–", " ").split():
        if len(token) >= 4 and token[:4].isdigit():
            years.add(int(token[:4]))
    return sorted(years)


def load_plan_membership(probe_root: Path) -> dict[str, list[str]]:
    path = probe_root / "plan" / "h3_centroids.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return {r["h3_index"]: [m for m in r["municipalities"].split("|") if m] for r in rows}


def load_probe_counts(
    probe_root: Path, membership: Mapping[str, Sequence[str]]
) -> tuple[dict[str, set[str]], dict[tuple[str, str, int], set[str]], dict[tuple[str, str, int], int]]:
    """Return n_h3 sets, cell sets keyed (muni, provider, year), date counts."""
    n_h3: dict[str, set[str]] = defaultdict(set)
    for h3_index, munis in membership.items():
        for muni in munis:
            n_h3[muni].add(h3_index)
    cells: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    dates: dict[tuple[str, str, int], int] = defaultdict(int)
    for path in (probe_root / "probes").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        h3_index = str(payload["h3_index"])
        provider = str(payload["provider"])
        munis = membership.get(h3_index, [])
        for capture in payload.get("dates") or []:
            year = int(str(capture)[:4])
            for muni in munis:
                cells[(muni, provider, year)].add(h3_index)
                dates[(muni, provider, year)] += 1
    return n_h3, cells, dates


def _row(
    muni: str,
    year: int,
    source: str,
    n_h3: int,
    n_cells: int,
    n_dates: int,
    note: str = "",
) -> dict[str, Any]:
    klass = classify_year(n_cells_with=n_cells, n_h3=n_h3, n_dates=n_dates)
    frac = (n_cells / n_h3) if n_h3 else 0.0
    dpc = (n_dates / n_cells) if n_cells else 0.0
    priority = klass == "sparse_1_2"
    return {
        "municipality": muni,
        "municipality_name": MUNI_SHORT[muni],
        "year": year,
        "source": source,
        "n_h3_vexcel": n_h3,
        "n_cells_with_year": n_cells,
        "n_date_rows": n_dates,
        "frac_cells": round(frac, 4),
        "dates_per_covered_cell": round(dpc, 4) if n_cells else "",
        "class": klass,
        "priority_flag": "sparse_1_2" if priority else "",
        "year_panel_ok": "yes" if klass in YEAR_PANEL_OK else "no",
        "day_interval_ok": "yes" if klass in DAY_OK else "no",
        "note": note,
    }


def build_year_rows(
    n_h3: Mapping[str, set[str]],
    cells: Mapping[tuple[str, str, int], set[str]],
    dates: Mapping[tuple[str, str, int], int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for muni in MUNI_ORDER:
        n = len(n_h3[muni])
        for year in YEARS:
            tm_cells = cells.get((muni, "TM", year), set())
            wb_cells = cells.get((muni, "Wayback", year), set())
            union_cells = tm_cells | wb_cells
            tm_n = dates.get((muni, "TM", year), 0)
            wb_n = dates.get((muni, "Wayback", year), 0)
            note = ""
            tm_class = classify_year(n_cells_with=len(tm_cells), n_h3=n, n_dates=tm_n)
            if tm_class == "sparse_1_2":
                wb_class = classify_year(n_cells_with=len(wb_cells), n_h3=n, n_dates=wb_n)
                un_class = classify_year(
                    n_cells_with=len(union_cells), n_h3=n, n_dates=tm_n + wb_n
                )
                bits = []
                if wb_class in YEAR_PANEL_OK:
                    bits.append(f"Wayback fills ({wb_class})")
                if un_class in YEAR_PANEL_OK:
                    bits.append(f"union year-panel {un_class}")
                note = "; ".join(bits) if bits else "no Wayback fill"
            rows.append(_row(muni, year, "TM", n, len(tm_cells), tm_n, note))
            rows.append(_row(muni, year, "Wayback", n, len(wb_cells), wb_n))
            rows.append(
                _row(muni, year, "union", n, len(union_cells), tm_n + wb_n)
            )
    return rows


def load_aerial() -> dict[str, dict[str, Any]]:
    path = DOCS_CATALOG / "aerial_city_summary.csv"
    out: dict[str, dict[str, Any]] = {}
    for row in csv.DictReader(path.open(encoding="utf-8")):
        key = row["municipality"]
        if key == "national":
            continue
        out[key] = {
            "years": parse_year_list(row.get("usable_years", "")),
            "vexcel_years": parse_vexcel_years(row.get("vexcel_capture", "")),
            "panel_verdict": row.get("panel_verdict", ""),
            "traps": row.get("traps", ""),
        }
    return out


def load_tm_medians() -> dict[str, str]:
    path = DOCS_CATALOG / "satellite_vexcel_h3r7_city_provider.csv"
    out: dict[str, str] = {}
    for row in csv.DictReader(path.open(encoding="utf-8")):
        if row["provider"] == "TM":
            out[row["municipality"]] = row["median_dates"]
    return out


def build_city_rows(
    year_rows: Sequence[Mapping[str, Any]],
    aerial: Mapping[str, Mapping[str, Any]],
    tm_medians: Mapping[str, str],
    n_h3: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    by: dict[tuple[str, int, str], Mapping[str, Any]] = {
        (r["municipality"], int(r["year"]), r["source"]): r for r in year_rows
    }
    out: list[dict[str, Any]] = []
    for muni in MUNI_ORDER:
        aerial_years = aerial.get(muni, {}).get("years", [])
        vexcel_years = aerial.get(muni, {}).get("vexcel_years", [])
        sat_year = []
        sat_day = []
        sparse = []
        aerial_fills = []
        wb_fills = []
        for year in YEARS:
            tm = by[(muni, year, "TM")]
            un = by[(muni, year, "union")]
            if un["class"] in YEAR_PANEL_OK:
                sat_year.append(year)
            if tm["class"] in DAY_OK:
                sat_day.append(year)
            if tm["priority_flag"] == "sparse_1_2":
                sparse.append(year)
                if year in aerial_years:
                    aerial_fills.append(year)
                if "Wayback fills" in str(tm.get("note", "")):
                    wb_fills.append(year)
        n_aerial = len(aerial_years)
        if n_aerial >= 8:
            aerial_v = "strong_aerial_year_panel"
        elif n_aerial >= 3:
            aerial_v = "sparse_aerial_year_panel"
        elif n_aerial > 0:
            aerial_v = "two_epoch_aerial"
        else:
            aerial_v = "no_municipal_aerial"
        if len(sat_day) >= 8:
            day_v = "dense_tm_day_axis"
        elif len(sat_day) >= 4:
            day_v = "partial_tm_day_axis"
        else:
            day_v = "year_panel_only"
        if sparse and not (aerial_fills or wb_fills):
            year_v = "sat_year_panel_with_unfilled_holes"
        elif sparse:
            year_v = "sat_year_panel_holes_filled"
        else:
            year_v = "sat_year_panel_contiguous"
        notes = aerial.get(muni, {}).get("traps", "")
        out.append(
            {
                "municipality": muni,
                "municipality_name": MUNI_SHORT[muni],
                "n_h3_vexcel": len(n_h3[muni]),
                "tm_median_dates": tm_medians.get(muni, ""),
                "aerial_usable_years": ",".join(str(y) for y in aerial_years),
                "n_aerial_years": n_aerial,
                "vexcel_years": ",".join(str(y) for y in vexcel_years),
                "sat_year_panel_years": ",".join(str(y) for y in sat_year),
                "n_sat_year_panel": len(sat_year),
                "sat_day_interval_years": ",".join(str(y) for y in sat_day),
                "n_sat_day_interval": len(sat_day),
                "priority_sparse_years": ",".join(str(y) for y in sparse),
                "aerial_fills_sparse": ",".join(str(y) for y in aerial_fills),
                "wayback_fills_sparse": ",".join(str(y) for y in wb_fills),
                "year_panel_verdict": f"{aerial_v}|{year_v}",
                "day_interval_verdict": day_v,
                "notes": notes,
            }
        )
    return out


CSS = """
:root{
  --bg:#0f1419; --card:#1e2834; --bg3:#243040; --text:#e8eef4; --muted:#9aabbc;
  --accent:#f0a202; --teal:#3dd6c6; --border:rgba(255,255,255,.08);
  --red:#ff8686; --green:#5fe08a; --gold:#ffd27a; --blue:#5b9cff;
  --font:"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
  --c-absent:#1a222c; --c-noise:#3a4450; --c-sparse:#7a2430; --c-one:#7a5a14;
  --c-thin:#4a3a18; --c-usable:#1e5c4a; --c-dense:#147a6a; --c-air:#1a4a5c; --c-vx:#4a2a6a;
}
*{box-sizing:border-box}
body{margin:0;font-family:var(--font);background:var(--bg);color:var(--text);line-height:1.6;font-size:16px}
a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1180px;margin:0 auto;padding:28px 18px 72px}
.hero{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;margin-bottom:20px}
h1{margin:0 0 8px;font-size:clamp(1.3rem,2.6vw,1.85rem)}
.sub{color:var(--muted);margin:0 0 12px;max-width:82ch}
.pill{display:inline-block;background:#1a222c;border:1px solid var(--border);border-radius:999px;padding:4px 10px;font-size:.82rem;color:var(--muted);margin:3px 4px 3px 0}
.pill b{color:var(--text)}
h2{font-size:1.2rem;margin:26px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border)}
h3{font-size:1.02rem;margin:18px 0 8px;color:#d5dee8}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.callout{border-left:4px solid var(--accent);background:#2a2418;padding:12px 14px;border-radius:0 10px 10px 0;margin:12px 0}
.callout.red{border-left-color:var(--red);background:#2a1a1a}
.callout.teal{border-left-color:var(--teal);background:#142428}
.why{margin:.35rem 0 0;color:#d5dee8;font-size:.92rem}
.muted{color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 12px}
.sw{display:inline-flex;align-items:center;gap:6px;font-size:.8rem;color:var(--muted)}
.sw i{width:14px;height:14px;border-radius:3px;display:inline-block;border:1px solid var(--border)}
.scroll{overflow-x:auto;border:1px solid var(--border);border-radius:10px;margin:0 0 14px}
table.heat{border-collapse:collapse;font-size:11px;min-width:720px;width:100%}
table.heat th,table.heat td{padding:0;text-align:center;border:1px solid #0f1419}
table.heat th{background:var(--bg3);color:var(--accent);font-weight:600;padding:6px 4px;font-size:10px}
table.heat th.lab{text-align:left;padding-left:8px;min-width:92px;color:var(--text);font-size:12px}
table.heat td{width:36px;height:28px;font-variant-numeric:tabular-nums;color:#f4f0e8;font-size:10px}
td.absent{background:var(--c-absent);color:#667} td.noise{background:var(--c-noise)}
td.sparse_1_2{background:var(--c-sparse);box-shadow:inset 0 0 0 2px #ff8686;font-weight:700}
td.one_look{background:var(--c-one)} td.thin_spatial{background:var(--c-thin)}
td.usable{background:var(--c-usable)} td.dense{background:var(--c-dense)}
td.air{background:var(--c-air)} td.vx{background:var(--c-vx)}
td.empty{background:var(--c-absent)}
table.heat td .mark{font-size:11px;line-height:28px}
.caption{color:var(--muted);font-size:.85rem;margin:4px 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
table.plain{width:100%;border-collapse:collapse;font-size:.86rem;background:var(--card);min-width:640px}
table.plain th,table.plain td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
table.plain th{background:var(--bg3);color:var(--accent);font-size:.72rem;letter-spacing:.04em;text-transform:uppercase}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--border);color:var(--muted);font-size:.84rem}
code{font-size:.86em;background:#1a222c;padding:1px 5px;border-radius:4px}
"""


def _heat_cell(row: Mapping[str, Any] | None, *, mark_priority: bool = True) -> str:
    if row is None or row["class"] == "absent":
        return '<td class="absent" title="无日期"></td>'
    klass = str(row["class"])
    title = (
        f"{row['municipality_name']} {row['source']} {row['year']}: "
        f"{row['n_cells_with_year']}/{row['n_h3_vexcel']} 格, "
        f"{row['n_date_rows']} 日期, "
        f"{row['dates_per_covered_cell']} 帧/格, {CLASS_LABEL_ZH[klass]}"
    )
    mark = "!" if klass == "sparse_1_2" and mark_priority else (
        "1" if klass == "one_look" else ("·" if klass == "noise" else "")
    )
    return f'<td class="{escape(klass)}" title="{escape(title)}"><span class="mark">{mark}</span></td>'


def heatmap_table(
    year_rows: Sequence[Mapping[str, Any]], *, source: str, caption: str
) -> str:
    by = {(r["municipality"], int(r["year"]), r["source"]): r for r in year_rows}
    head = "".join(f"<th>{y}</th>" for y in YEARS)
    body = []
    for muni in MUNI_ORDER:
        cells = "".join(_heat_cell(by.get((muni, y, source))) for y in YEARS)
        body.append(f'<tr><th class="lab">{escape(MUNI_ZH[muni])}</th>{cells}</tr>')
    return f"""
<p class="caption">{escape(caption)}</p>
<div class="legend">
  <span class="sw"><i style="background:var(--c-dense)"></i>密（≥5帧/格且≥90%格子）</span>
  <span class="sw"><i style="background:var(--c-usable)"></i>可用（&gt;2帧/格）</span>
  <span class="sw"><i style="background:var(--c-one)"></i>全市一两帧（年 panel 可以，日精度不行）</span>
  <span class="sw"><i style="background:var(--c-sparse)"></i><b style="color:var(--red)">优先</b>：一两帧且不到一半格子</span>
  <span class="sw"><i style="background:var(--c-noise)"></i>1–2 个格子，噪声</span>
  <span class="sw"><i style="background:var(--c-absent)"></i>无</span>
</div>
<div class="scroll"><table class="heat">
<tr><th class="lab">城 \\ 年</th>{head}</tr>
{''.join(body)}
</table></div>
"""


def availability_table(
    year_rows: Sequence[Mapping[str, Any]],
    aerial: Mapping[str, Mapping[str, Any]],
) -> str:
    by = {(r["municipality"], int(r["year"]), r["source"]): r for r in year_rows}
    head = "".join(f"<th>{y}</th>" for y in YEARS)
    blocks = []
    for muni in MUNI_ORDER:
        air = set(aerial.get(muni, {}).get("years", []))
        vx = set(aerial.get(muni, {}).get("vexcel_years", []))
        air_cells = []
        vx_cells = []
        sat_cells = []
        for y in YEARS:
            air_cells.append(
                '<td class="air" title="市政航测年"></td>' if y in air else '<td class="empty"></td>'
            )
            vx_cells.append(
                '<td class="vx" title="Vexcel 一季"></td>' if y in vx else '<td class="empty"></td>'
            )
            sat_cells.append(_heat_cell(by.get((muni, y, "union")), mark_priority=False))
        blocks.append(
            f"<h3>{escape(MUNI_ZH[muni])} · {escape(MUNI_SHORT[muni])}</h3>"
            f'<div class="scroll"><table class="heat">'
            f'<tr><th class="lab">层 \\ 年</th>{head}</tr>'
            f'<tr><th class="lab">市政航测</th>{"".join(air_cells)}</tr>'
            f'<tr><th class="lab">Vexcel</th>{"".join(vx_cells)}</tr>'
            f'<tr><th class="lab">卫星 union</th>{"".join(sat_cells)}</tr>'
            f"</table></div>"
        )
    return (
        '<div class="legend">'
        '<span class="sw"><i style="background:var(--c-air)"></i>市政真日期航测可用年</span>'
        '<span class="sw"><i style="background:var(--c-vx)"></i>Vexcel 单季快照</span>'
        '<span class="sw"><i style="background:var(--c-dense)"></i>卫星密</span>'
        '<span class="sw"><i style="background:var(--c-usable)"></i>卫星可用</span>'
        '<span class="sw"><i style="background:var(--c-one)"></i>卫星全市一两帧</span>'
        '<span class="sw"><i style="background:var(--c-sparse)"></i>卫星一两帧且空间残</span>'
        "</div>\n" + "\n".join(blocks)
    )


def priority_table(year_rows: Sequence[Mapping[str, Any]], aerial: Mapping[str, Mapping[str, Any]]) -> str:
    rows = [r for r in year_rows if r["source"] == "TM" and r["priority_flag"] == "sparse_1_2"]
    body = []
    for r in rows:
        air = "是" if int(r["year"]) in set(aerial.get(r["municipality"], {}).get("years", [])) else "否"
        fill = r.get("note") or "无"
        body.append(
            "<tr>"
            f"<td>{escape(MUNI_ZH[r['municipality']])}</td>"
            f"<td class='num'>{r['year']}</td>"
            f"<td class='num'>{r['n_cells_with_year']}/{r['n_h3_vexcel']}</td>"
            f"<td class='num'>{r['dates_per_covered_cell']}</td>"
            f"<td class='num'>{r['n_date_rows']}</td>"
            f"<td>{air}</td>"
            f"<td>{escape(str(fill))}</td>"
            "</tr>"
        )
    return f"""
<div class="scroll"><table class="plain">
<thead><tr><th>城</th><th>年</th><th>TM 格子</th><th>帧/格</th><th>日期行</th><th>市政航测有这年？</th><th>Wayback / union 能否补</th></tr></thead>
<tbody>{''.join(body)}</tbody>
</table></div>
"""


def city_cards(city_rows: Sequence[Mapping[str, Any]]) -> str:
    parts = []
    for r in city_rows:
        parts.append(
            f"""<div class="card">
<b>{escape(MUNI_ZH[r['municipality']])}</b>
 · TM 中位 {escape(str(r['tm_median_dates']))} 日
 · 航测 {r['n_aerial_years']} 年 · 卫星年 panel {r['n_sat_year_panel']} 年 · 日精度年 {r['n_sat_day_interval']}
<p class="muted" style="margin:6px 0 0">优先残年：{escape(r['priority_sparse_years'] or '无')}
 · 航测能补：{escape(r['aerial_fills_sparse'] or '无')}
 · Wayback 能补：{escape(r['wayback_fills_sparse'] or '无')}</p>
</div>"""
        )
    return '<div class="grid">' + "".join(parts) + "</div>"


def wrap_page(title: str, body: str, *, date: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
{body}
<footer>探元数据 {escape(date)}。卫星 = Vexcel 覆盖 H3-7 质心 GEHI info，z19，TM + Wayback。
不是芯片框完整 availability，不是屋顶 catalog。数字以 CSV 为准。</footer>
</div>
</body>
</html>
"""


def render_fig1(year_rows: Sequence[Mapping[str, Any]], date: str) -> str:
    body = f"""
<header class="hero">
  <h1>图 1 · 卫星年 × 城：TM 有几帧</h1>
  <p class="sub">每个格子是该城 Vexcel 覆盖区里、这一年 TM 质心日期的密度。
  红色描边带「!」的是约翰内斯堡 2023 那一类：<b>有这一年的名字，但不到一半格子、且每格只有一两帧</b>。
  金黄色带「1」是全市都有、可是一年只看得到一两次——能做年 panel，不能做日区间。</p>
  <span class="pill"><b>样本</b> 1,148 个 H3-7 质心</span>
  <span class="pill"><b>查询</b> TM z19 info</span>
  <span class="pill"><b>日期</b> {escape(date)}</span>
</header>
<h2>Google Earth TM</h2>
{heatmap_table(year_rows, source="TM", caption="颜色 = 这一年每个有图格子上的 TM 日期数，以及覆盖了多少 Vexcel 格子。分母是 Vexcel 足迹，不是全市腹地。")}
<div class="callout red">
<b>优先标注。</b> 约翰内斯堡 2023：32/74 格、每格 1 帧。Ekurhuleni 2023 同型（4/23）。
这两年 <b>不能</b> 当「全市有一张 TM」。Wayback 2023 在这两城是全市覆盖，市政航测约翰内斯堡 2023 也在。
<p class="why">不管会怎样：会把 years_union 里的 2023 当成和 2019 一样能用的卫星年，日精度和年 panel 都会在内城缺一块。</p>
</div>
<h2>对照：TM ∪ Wayback</h2>
{heatmap_table(year_rows, source="union", caption="同一套格子，两个传感器并集。约翰内斯堡 / Ekurhuleni 2023 的红框在并集上消失——Wayback 补上了空间。")}
"""
    return wrap_page("图 1 · 卫星年密度热图（TM）", body, date=date)


def render_fig2(
    year_rows: Sequence[Mapping[str, Any]],
    aerial: Mapping[str, Mapping[str, Any]],
    city_rows: Sequence[Mapping[str, Any]],
    date: str,
) -> str:
    body = f"""
<header class="hero">
  <h1>图 2 · 三层能不能叠成 panel</h1>
  <p class="sub">每一城三行：市政真日期航测可用年、Vexcel 单季快照、卫星 union 年类。
  问的是「这一年这个城有没有一张能用的观测」，不是「有多少日」。</p>
  <span class="pill"><b>航测</b> 2026-09-06 目录</span>
  <span class="pill"><b>卫星</b> 2026-09-13 质心 info</span>
</header>
<div class="callout teal">
<b>怎么读可用性。</b> 年 panel：卫星 union 是「全市一两帧 / 可用 / 密」即可；日区间：只有 TM「可用 / 密」。
市政航测更清（5–15 cm），但年更稀。Vexcel 每个城只有一季，当「现在」不是时间维。
<p class="why">忽略会怎样：会用 6.2 cm 的 2024 当五年，或把 TM 2023 残年当成和航测 2023 等价。</p>
</div>
{availability_table(year_rows, aerial)}
<h2>九城一句可用性</h2>
{city_cards(city_rows)}
"""
    return wrap_page("图 2 · 航测 / Vexcel / 卫星年可用性", body, date=date)


def render_brief(
    year_rows: Sequence[Mapping[str, Any]],
    aerial: Mapping[str, Mapping[str, Any]],
    city_rows: Sequence[Mapping[str, Any]],
    date: str,
) -> str:
    n_sparse = sum(1 for r in year_rows if r["source"] == "TM" and r["priority_flag"] == "sparse_1_2")
    n_year = {r["municipality"]: r["n_sat_year_panel"] for r in city_rows}
    n_day = {r["municipality"]: r["n_sat_day_interval"] for r in city_rows}
    body = f"""
<header class="hero">
  <h1>卫星覆盖 metadata 交付：密度热图 + 三层可用性</h1>
  <p class="sub">合作者要的图层目录之外，这一包回答两件事：Vexcel 覆盖区里卫星
  <b>哪一年真的有图</b>，以及像约翰内斯堡 2023 那种「名字在、图不够」的年要先标出来。
  样本是 1,148 个 H3-7 质心的 live GEHI info（TM + Wayback，z19），不是开普敦全城屋顶 catalog。</p>
  <span class="pill"><b>日期</b> {escape(date)}</span>
  <span class="pill"><b>优先残年</b> TM {n_sparse} 个城×年</span>
  <span class="pill"><b>图 1</b> TM 年密度</span>
  <span class="pill"><b>图 2</b> 航测 / Vexcel / 卫星叠年</span>
</header>
<nav class="legend" style="position:sticky;top:8px;background:rgba(26,34,44,.94);padding:8px 10px;border-radius:10px;border:1px solid var(--border)">
  <a href="#fig1">图 1 热图</a>
  <a href="#priority">优先残年</a>
  <a href="#fig2">图 2 三层</a>
  <a href="#verdict">可用性结论</a>
  <a href="#files">文件</a>
</nav>
<section id="fig1">
<h2>图 1 · TM 年密度（优先看红框）</h2>
{heatmap_table(year_rows, source="TM", caption="红框「!」= 一两帧且不到一半 Vexcel 格子。金「1」= 全市都有但一年只看一两次。")}
</section>
<section id="priority">
<h2>优先标注：TM 一两帧且空间残</h2>
<div class="callout red">
<b>这张表说什么。</b> years_union 里出现的年份不等于能用的卫星年。约翰内斯堡 2023 TM 只有 32/74 格、每格 1 帧。
<b>为什么要紧。</b> 合作者若按「有 2023」去建年 panel，内城一半格子没有 TM。
<b>忽略会怎样。</b> 2023 的有/无会在约翰内斯堡和 Ekurhuleni 系统性缺测，却看起来像全市观测。
</div>
{priority_table(year_rows, aerial)}
<p class="caption">补洞：约翰内斯堡 / Ekurhuleni 2023 的 Wayback 是全市的（1.6–2.0 帧/格）；约翰内斯堡还有市政 15 cm 2023。
eThekwini 2009、NMB 2012/2026、Buffalo 2009/2011/2026、Mangaung 2011 没有同等补丁的，要从年 panel 剔除或降为局部样本。</p>
</section>
<section id="fig2">
<h2>图 2 · 航测 / Vexcel / 卫星能不能叠年</h2>
{availability_table(year_rows, aerial)}
</section>
<section id="verdict">
<h2>整体可用性</h2>
<div class="grid">
  <div class="card"><b>年精度 panel</b>
    <p>九城 Vexcel 足迹上，卫星 union 都能排出 {min(n_year.values())}–{max(n_year.values())} 个年。
    开普敦、约翰内斯堡、eThekwini、NMB 可以和市政航测叠年；Buffalo / Mangaung / Msunduzi 没有市政年栈，卫星年 panel 是主时间维，Vexcel 只提供「现在」。</p>
  </div>
  <div class="card"><b>日精度</b>
    <p>只有 TM「可用 / 密」的年（开普敦大约 {n_day['cape_town']} 年，约翰内斯堡 {n_day['johannesburg']} 年，茨瓦内 {n_day['tshwane']} 年）。
    2023–2025 在多数城是全市一两帧，能判有/无，不能收紧到几十天。</p>
  </div>
  <div class="card"><b>2023 这一刀</b>
    <p>几乎所有城的 TM 2023 都掉到一两帧。约翰内斯堡和 Ekurhuleni 还加上空间残。
    大都市用 Wayback 或市政航测过 2023；没有航测的城把 2023 当弱观测。</p>
  </div>
</div>
{city_cards(city_rows)}
<div class="callout">
<b>交付怎么用。</b> 年 panel 的年份清单用 union 的 year_panel_ok=yes，再减去 TM sparse_1_2 且没有 Wayback/航测补丁的年。
日区间清单只用 TM day_interval_ok=yes。不要把 Vexcel 五个开普敦 collection 名当成五年。
</div>
</section>
<section id="files">
<h2>文件</h2>
<div class="card">
<p>图：<a href="FIG-satellite-year-heatmap-2026-09-14.html">FIG-satellite-year-heatmap-2026-09-14.html</a>
 · <a href="FIG-layer-availability-2026-09-14.html">FIG-layer-availability-2026-09-14.html</a>
 · <a href="FIG-h3-spatial-coverage-2026-09-14.html">FIG-h3-spatial-coverage-2026-09-14.html</a>（H3-7 空间覆盖）</p>
<p>CSV：<a href="_muni_catalog/satellite_vexcel_h3r7_year_availability.csv">year_availability.csv</a>
 · <a href="_muni_catalog/satellite_vexcel_h3r7_city_usability.csv">city_usability.csv</a>
 · <a href="_muni_catalog/satellite_vexcel_h3r7_spatial.csv">spatial.csv</a>
 · <a href="_muni_catalog/satellite_vexcel_h3r7_city_spatial.csv">city_spatial.csv</a></p>
<p>上游 SRC：<a href="SRC-satellite-vexcel-h3r7-gehi-info-2026-09-13.md">质心 info 目录</a>
 · <a href="SRC-municipal-layers-2026-09-06.md">市政航测</a>
 · <a href="SRC-vexcel-layers-h3r7-2026-09-06.md">Vexcel</a></p>
</div>
</section>
"""
    return wrap_page("卫星覆盖 metadata：密度 + 可用性", body, date=date)


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-root", type=Path, default=DEFAULT_PROBE_ROOT)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    membership = load_plan_membership(args.probe_root)
    n_h3, cells, dates = load_probe_counts(args.probe_root, membership)
    year_rows = build_year_rows(n_h3, cells, dates)
    aerial = load_aerial()
    city_rows = build_city_rows(year_rows, aerial, load_tm_medians(), n_h3)
    date = utc_today()
    derived = args.probe_root / "derived"
    write_csv(derived / "year_availability.csv", year_rows, YEAR_AVAIL_FIELDS)
    write_csv(derived / "city_usability.csv", city_rows, CITY_USE_FIELDS)
    fig1 = render_fig1(year_rows, date)
    fig2 = render_fig2(year_rows, aerial, city_rows, date)
    brief = render_brief(year_rows, aerial, city_rows, date)
    (derived / "FIG-satellite-year-heatmap.html").write_text(fig1, encoding="utf-8")
    (derived / "FIG-layer-availability.html").write_text(fig2, encoding="utf-8")
    (derived / "BRIEF-satellite-availability.html").write_text(brief, encoding="utf-8")
    if args.publish:
        write_csv(DOCS_CATALOG / "satellite_vexcel_h3r7_year_availability.csv", year_rows, YEAR_AVAIL_FIELDS)
        write_csv(DOCS_CATALOG / "satellite_vexcel_h3r7_city_usability.csv", city_rows, CITY_USE_FIELDS)
        (BRIEF_DIR / "FIG-satellite-year-heatmap-2026-09-14.html").write_text(fig1, encoding="utf-8")
        (BRIEF_DIR / "FIG-layer-availability-2026-09-14.html").write_text(fig2, encoding="utf-8")
        (BRIEF_DIR / "BRIEF-satellite-availability-2026-09-14.html").write_text(brief, encoding="utf-8")
        print(f"published figures + brief -> {BRIEF_DIR}")
    n_pri = sum(1 for r in year_rows if r["source"] == "TM" and r["priority_flag"] == "sparse_1_2")
    print(f"year rows={len(year_rows)} city rows={len(city_rows)} TM sparse_1_2={n_pri}")


if __name__ == "__main__":
    main()
