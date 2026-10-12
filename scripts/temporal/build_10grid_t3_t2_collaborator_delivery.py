#!/usr/bin/env python3
"""Build the collaborator package for the ten-grid full-timeline T3/T2 runs.

Reads the finished gemini-3.1-flash-lite run (T3 + paired T2) and the
gemini-3.5-flash-lite T3 rerun, plus the local 2026-09-02 production table,
and writes:

  ct_10grid_t3_t2_overview_2026-10-07.html   self-contained overview
  ct_10grid_t3_t2_overview_2026-10-07_zh.html  the same overview in Chinese
  ct_10grid_date_labels_20261007.csv         one row per roof x catalog date
  ct_10grid_intervals_20261007.csv           one row per roof
  gates.json                                 recomputed counts checked against the RUN notes
  README.md, SHA256SUMS.txt

Every count in the HTML is recomputed from the source tables here; the gate
block fails the build if a count drifts from the RUN notes.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "zasolar_data/geid_temporal"
RUN31 = ROOT / "reference_fulltimeline_v2_10grid_20261005/results"
RUN35 = ROOT / "reference_fulltimeline_v2_10grid_20261005_gemini35_flash_lite/results"
PROD = ROOT / "cape_town_citywide_backdating_v1_20260812/deliverable_2026-09-02/ct_citywide_install_dated_20260902.csv"
OUT = ROOT / "delivery_10grid_t3_t2_20261007"

HTML_NAME = "ct_10grid_t3_t2_overview_2026-10-07.html"
HTML_ZH_NAME = "ct_10grid_t3_t2_overview_2026-10-07_zh.html"
LABELS_NAME = "ct_10grid_date_labels_20261007.csv"
INTERVALS_NAME = "ct_10grid_intervals_20261007.csv"

ADMITTED = ["absent", "present", "unknown"]
LABEL_ORDER = ["absent", "present", "unknown", "no_label"]
LABEL_NAME = {"absent": "Absent", "present": "Present", "unknown": "Unclear", "no_label": "No admitted label"}
STATUS_ORDER = ["bounded", "conflict_pending_review", "left_censored", "undated"]
STATUS_NAME = {"bounded": "Bounded", "conflict_pending_review": "Conflict", "left_censored": "Left-censored", "undated": "Undated"}
PROD_ORDER = ["done_appears", "done_installed_during_census", "done_already_present_before_geid_history",
              "done_ambiguous_nonmonotonic", "done_ambiguous_no_recent_anchor"]
PROD_NAME = {"done_appears": "Dated bracket", "done_installed_during_census": "Census upper bound only",
             "done_already_present_before_geid_history": "Present before 2019 (left-censored)",
             "done_ambiguous_nonmonotonic": "Non-monotonic, undated",
             "done_ambiguous_no_recent_anchor": "No recent anchor, undated"}
MODELS = [("g31", "gemini-3.1-flash-lite"), ("g35", "gemini-3.5-flash-lite")]
SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"]
WIDTH_BINS = [(0, 35, "≤35 d"), (36, 100, "36–100 d"), (101, 200, "101–200 d"), (201, 366, "201–366 d"), (367, 400, "367–395 d")]

# Counts stated in the RUN notes (2026-10-06 and 2026-10-07). The build stops if any drifts.
EXPECTED = {
    "dates": 51212, "roofs": 1114,
    "g31_present": 12338, "g31_absent": 38356, "g31_unknown": 509, "g31_invalid": 5, "pixel_uninformative": 4,
    "g35_present": 8825, "g35_absent": 39583, "g35_unknown": 2339, "g35_invalid": 461,
    "both_admitted_same_images": 50742, "both_admitted_same_label": 41668, "present31_absent35": 5224,
    "t2_dates": 7102, "t2_roofs": 149, "t2_same": 6331,
    "g31_bounded": 250, "g31_conflict": 781, "g31_left": 6, "g31_undated": 77,
    "g35_bounded": 119, "g35_conflict": 866, "g35_left": 1, "g35_undated": 128,
    "interval_same_status": 831, "both_bounded": 71, "both_bounded_same_ends": 50,
    "prod_bracket": 761, "prod_bracket_t31_conflict": 539, "prod_t31_both_bounded": 209,
}


def esc(x) -> str:
    return html.escape(str(x))


def fmt(n) -> str:
    return f"{int(n):,}"


def pct(a, b, d=1) -> str:
    return f"{100 * a / b:.{d}f}%" if b else "–"


def load():
    s31 = pd.read_csv(RUN31 / "t3_status.csv", dtype=str)
    s35 = pd.read_csv(RUN35 / "t3_status.csv", dtype=str)
    pair = pd.read_csv(RUN31 / "paired_comparison.csv", dtype=str)
    i31 = pd.read_csv(RUN31 / "intervals_t3.csv", dtype=str)
    i35 = pd.read_csv(RUN35 / "intervals_t3.csv", dtype=str)
    prod = pd.read_csv(PROD, dtype=str)
    return s31, s35, pair, i31, i35, prod


def label_bucket(s: pd.Series) -> pd.Series:
    return s.where(s.isin(ADMITTED), "no_label")


def build_labels(s31, s35, pair):
    key = ["anchor_id", "capture_date"]
    m = s31.merge(s35, on=key, suffixes=("_31", "_35"), validate="one_to_one")
    assert len(m) == len(s31) == len(s35)
    assert (m.grid_id_31 == m.grid_id_35).all() and (m.grid_role_31 == m.grid_role_35).all()
    sat = pd.concat([s31[["anchor_id", "satellite_date"]], s35[["anchor_id", "satellite_date"]]]).dropna()
    sat = sat.drop_duplicates()
    assert not sat.anchor_id.duplicated().any(), "one post-census satellite reference per roof expected"
    sat = sat.set_index("anchor_id").satellite_date
    both_ok = m.t3_status_31.isin(ADMITTED) & m.t3_status_35.isin(ADMITTED)
    same = ((m.candidate_sha256_31 == m.candidate_sha256_35) & (m.aerial_sha256_31 == m.aerial_sha256_35)
            & (m.satellite_sha256_31 == m.satellite_sha256_35))
    assert same[both_ok].all(), "admitted dates on both sides must share the three image hashes"
    p = pair[key + ["t2_verdict"]]
    assert pair.in_effect_comparison.eq("1").all() and pair.candidate_bytes_agree.eq("1").all() and pair.aerial_bytes_agree.eq("1").all()
    m = m.merge(p, on=key, how="left", validate="one_to_one")
    paired_roofs = set(pair.anchor_id)
    out = pd.DataFrame({
        "anchor_id": m.anchor_id,
        "grid_id": m.grid_id_31,
        "grid_role": m.grid_role_31,
        "capture_date": m.capture_date,
        "t3_label_gemini31": m.t3_status_31,
        "t3_label_gemini35": m.t3_status_35,
        "t2_label_gemini31": m.t2_verdict,
        "t2_paired_roof": m.anchor_id.isin(paired_roofs).astype(int),
        "satellite_reference_date": m.anchor_id.map(sat),
    }).sort_values(key).reset_index(drop=True)
    return out


def build_intervals(i31, i35, prod):
    keep = ["status", "install_relation", "interval_start", "interval_end", "last_absent_date",
            "earliest_present_date", "conflict_frames", "n_present", "n_absent", "n_unknown", "n_call_failed"]
    for i in (i31, i35):
        assert i.labels_modified.eq("0").all(), "labels must be unmodified"
    base = i31[["anchor_id", "grid_id", "grid_role"]].copy()
    for tag, i in (("g31", i31), ("g35", i35)):
        part = i[["anchor_id"] + keep].copy()
        part["width_days"] = (pd.to_datetime(part.interval_end) - pd.to_datetime(part.interval_start)).dt.days.astype("Int64")
        part = part.rename(columns={c: f"{tag}_{c}" for c in keep + ["width_days"]})
        part = part.rename(columns={f"{tag}_conflict_frames": f"{tag}_conflict_dates"})
        base = base.merge(part, on="anchor_id", how="left", validate="one_to_one")
    pc = prod[["source_anchor_id", "centroid_lon", "centroid_lat", "area_m2", "scan_status", "install_date",
               "install_interval_start", "install_interval_end", "n_observations"]].rename(columns={
        "source_anchor_id": "anchor_id", "scan_status": "prod_status", "install_date": "prod_install_date",
        "install_interval_start": "prod_interval_start", "install_interval_end": "prod_interval_end",
        "n_observations": "prod_n_observations"})
    base = base.merge(pc, on="anchor_id", how="left", validate="one_to_one")
    assert base.prod_status.notna().all(), "every roof must match the production table"
    base["prod_width_days"] = (pd.to_datetime(base.prod_interval_end) - pd.to_datetime(base.prod_interval_start)).dt.days.astype("Int64")
    lead = ["anchor_id", "grid_id", "grid_role", "centroid_lon", "centroid_lat", "area_m2"]
    rest = [c for c in base.columns if c not in lead]
    return base[lead + rest].sort_values("anchor_id").reset_index(drop=True)


def compute(labels, iv, pair):
    g = {}
    g["dates"] = len(labels)
    g["roofs"] = labels.anchor_id.nunique()
    for tag, col in (("g31", "t3_label_gemini31"), ("g35", "t3_label_gemini35")):
        vc = labels[col].value_counts()
        for k in ["present", "absent", "unknown"]:
            g[f"{tag}_{k}"] = int(vc.get(k, 0))
        g[f"{tag}_invalid"] = int(vc.get("invalid_response", 0))
        g[f"pixel_uninformative_{tag}"] = int(vc.get("pixel_uninformative", 0))
    g["pixel_uninformative"] = g["pixel_uninformative_g31"]
    both = labels[labels.t3_label_gemini31.isin(ADMITTED) & labels.t3_label_gemini35.isin(ADMITTED)]
    g["both_admitted_same_images"] = len(both)
    g["both_admitted_same_label"] = int((both.t3_label_gemini31 == both.t3_label_gemini35).sum())
    g["present31_absent35"] = int(((both.t3_label_gemini31 == "present") & (both.t3_label_gemini35 == "absent")).sum())
    t2 = labels[labels.t2_label_gemini31.notna()]
    g["t2_dates"] = len(t2)
    g["t2_roofs"] = t2.anchor_id.nunique()
    g["t2_same"] = int((t2.t2_label_gemini31 == t2.t3_label_gemini31).sum())
    for tag in ("g31", "g35"):
        vc = iv[f"{tag}_status"].value_counts()
        g[f"{tag}_bounded"] = int(vc.get("bounded", 0))
        g[f"{tag}_conflict"] = int(vc.get("conflict_pending_review", 0))
        g[f"{tag}_left"] = int(vc.get("left_censored", 0))
        g[f"{tag}_undated"] = int(vc.get("undated", 0))
    g["interval_same_status"] = int((iv.g31_status == iv.g35_status).sum())
    bb = iv[(iv.g31_status == "bounded") & (iv.g35_status == "bounded")]
    g["both_bounded"] = len(bb)
    g["both_bounded_same_ends"] = int(((bb.g31_interval_start == bb.g35_interval_start) & (bb.g31_interval_end == bb.g35_interval_end)).sum())
    g["prod_bracket"] = int((iv.prod_status == "done_appears").sum())
    g["prod_bracket_t31_conflict"] = int(((iv.prod_status == "done_appears") & (iv.g31_status == "conflict_pending_review")).sum())
    g["prod_t31_both_bounded"] = int(((iv.prod_status == "done_appears") & (iv.g31_status == "bounded")).sum())
    return g


def check_gates(g):
    bad = {k: (g.get(k), v) for k, v in EXPECTED.items() if g.get(k) != v}
    if bad:
        raise SystemExit(f"gate drift (got, expected): {bad}")


LABEL_NAME_ZH = {"absent": "无板", "present": "有板", "unknown": "看不清", "no_label": "无可采纳标签"}
STATUS_NAME_ZH = {"bounded": "有界", "conflict_pending_review": "冲突", "left_censored": "左删失", "undated": "未定"}
PROD_NAME_ZH = {"done_appears": "有括号（已定区间）", "done_installed_during_census": "只有普查日上界",
                "done_already_present_before_geid_history": "2019 年前已有板（左删失）",
                "done_ambiguous_nonmonotonic": "序列不单调，未定", "done_ambiguous_no_recent_anchor": "无近期锚点，未定"}
ZH = {
    "development": "开发格", "evaluation": "评估格", "all": "合计", "Development": "开发格", "Evaluation": "评估格",
    "dates": "个日期", "roofs": "座屋顶",
    "T3 label share per catalog date, by model and grid role": "各模型、各格组的 T3 日期标签占比",
    "Model": "模型", "Absent": "无板", "Present": "有板", "Unclear": "看不清", "Not admitted": "不能采纳",
    "Near-white chip": "近白芯片", "Total": "合计",
    "rows: 3.1 · columns: 3.5": "行：3.1 · 列：3.5", "All ten grids": "十格合计",
    "Grid role": "格组", "Dates, both admitted": "两边都有标签的日期", "Same label": "标签相同", "Agreement": "一致率",
    "3.1 present → 3.5 absent": "3.1 有板 → 3.5 无板", "3.1 absent → 3.5 present": "3.1 无板 → 3.5 有板",
    "rows: T2 (2 images) · columns: T3 (3 images)": "行：T2（两张图）· 列：T3（三张图）",
    "Roofs": "屋顶数", "Dates": "日期数", "T2 present": "T2 有板", "T3 present": "T3 有板",
    "T2 unclear": "T2 看不清", "T3 unclear": "T3 看不清",
    "Install-interval status per roof, by model and grid role": "各模型、各格组的屋顶安装区间状态占比",
    "Model · role": "模型 · 格组", "Bounded": "有界", "Conflict": "冲突", "Left-censored": "左删失", "Undated": "未定",
    "Median bounded width (days)": "有界区间宽度中位数（天）",
    "Width of bounded intervals (last absent → first present), roofs per bin": "有界区间宽度（最后一次无板 → 第一次有板），每档屋顶数",
    "rows: 2026-09-02 production · columns: T3 ": "行：2026-09-02 生产表 · 列：T3 ",
    "T3 model": "T3 模型", "Roofs bracketed in both": "两边都有区间的屋顶", "Median width, production (days)": "生产宽度中位数（天）",
    "Median width, T3 (days)": "T3 宽度中位数（天）", "T3 wider": "T3 更宽", "Same width": "同宽", "T3 narrower": "T3 更窄",
    "Identical endpoints": "端点完全相同", "Production bracket inside T3 (not identical)": "生产区间落在 T3 区间内（不全同）",
    "Partial overlap": "部分重叠", "T3 entirely earlier": "T3 整段更早", "T3 entirely later": "T3 整段更晚",
    "At least one endpoint changes year": "至少一个端点换了年份",
    "Grid": "格", "Role": "格组", "Present 3.1": "有板 3.1", "Present 3.5": "有板 3.5", "Not admitted 3.5": "不能采纳 3.5",
    "Bounded 3.1": "有界 3.1", "Bounded 3.5": "有界 3.5", "Conflict 3.1": "冲突 3.1", "Conflict 3.5": "冲突 3.5",
}


# ----------------------------------------------------------------- charts

def stacked_bars(rows, cats, names, title, unit="dates"):
    """rows: list of (row label, {cat: n}). 100% horizontal stacked bars, inline SVG."""
    W, left, right, bar, gapy = 760, 200, 16, 22, 18
    plot = W - left - right
    H = 28 + len(rows) * (bar + gapy) + 10
    parts = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}">']
    for t in (0, 25, 50, 75, 100):
        x = left + plot * t / 100
        parts.append(f'<line x1="{x:.1f}" y1="18" x2="{x:.1f}" y2="{H - 6}" class="grid"/>')
        parts.append(f'<text x="{x:.1f}" y="12" class="tick" text-anchor="middle">{t}%</text>')
    for r, (lab, counts) in enumerate(rows):
        y = 28 + r * (bar + gapy)
        tot = sum(counts.get(c, 0) for c in cats)
        parts.append(f'<text x="{left - 10}" y="{y + bar / 2 + 4}" class="rowlab" text-anchor="end">{esc(lab)}</text>')
        x = left
        segs = [(c, counts.get(c, 0)) for c in cats if counts.get(c, 0) > 0]
        for k, (c, n) in enumerate(segs):
            w = plot * n / tot
            wd = max(w - (2 if k < len(segs) - 1 else 0), 0.8)
            last = k == len(segs) - 1
            color = SERIES[cats.index(c)]
            tip = f"{lab} · {names[c]}: {fmt(n)} {unit} ({pct(n, tot)})"
            if last and wd > 4:
                d = (f"M{x:.1f},{y} h{wd - 4:.1f} a4,4 0 0 1 4,4 v{bar - 8} a4,4 0 0 1 -4,4 h{-(wd - 4):.1f} z")
                parts.append(f'<path d="{d}" fill="{color}" data-tip="{esc(tip)}"/>')
            else:
                parts.append(f'<rect x="{x:.1f}" y="{y}" width="{wd:.1f}" height="{bar}" fill="{color}" data-tip="{esc(tip)}"/>')
            if w > 54:
                parts.append(f'<text x="{x + 6:.1f}" y="{y + bar / 2 + 4}" class="seglab">{pct(n, tot, 0)}</text>')
            x += w
    parts.append("</svg>")
    legend = "".join(f'<span class="key"><i style="background:{SERIES[k]}"></i>{esc(names[c])}</span>' for k, c in enumerate(cats))
    return f'<figure><figcaption>{esc(title)}</figcaption><div class="legend">{legend}</div>{"".join(parts)}</figure>'


def grouped_columns(bins, series, title, unit="roofs"):
    """bins: list of labels; series: list of (name, [counts]). Vertical grouped columns."""
    W, H, left, bottom, top = 760, 260, 48, 34, 16
    plot_w, plot_h = W - left - 16, H - top - bottom
    vmax = max(max(v) for _, v in series)
    step = 10 if vmax <= 60 else 20 if vmax <= 120 else 50
    ymax = ((vmax // step) + 1) * step
    parts = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}">']
    for t in range(0, ymax + 1, step):
        y = top + plot_h * (1 - t / ymax)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{W - 16}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">{t}</text>')
    band = plot_w / len(bins)
    bw = min(24, (band - 24) / len(series))
    for b, lab in enumerate(bins):
        cx = left + band * (b + 0.5)
        parts.append(f'<text x="{cx:.1f}" y="{H - 12}" class="tick" text-anchor="middle">{esc(lab)}</text>')
        x0 = cx - (bw * len(series) + 2 * (len(series) - 1)) / 2
        for k, (name, vals) in enumerate(series):
            v = vals[b]
            h = plot_h * v / ymax
            x = x0 + k * (bw + 2)
            y = top + plot_h - h
            tip = f"{name} · {lab}: {v} {unit}"
            if h > 4:
                d = f"M{x:.1f},{top + plot_h} v{-(h - 4):.1f} a4,4 0 0 1 4,-4 h{bw - 8:.1f} a4,4 0 0 1 4,4 v{h - 4:.1f} z"
                parts.append(f'<path d="{d}" fill="{SERIES[k]}" data-tip="{esc(tip)}"/>')
            elif v:
                parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{SERIES[k]}" data-tip="{esc(tip)}"/>')
            parts.append(f'<rect x="{x - 3:.1f}" y="{top}" width="{bw + 6:.1f}" height="{plot_h}" fill="transparent" data-tip="{esc(tip)}"/>')
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{y - 5:.1f}" class="vallab" text-anchor="middle">{v}</text>')
    parts.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{W - 16}" y2="{top + plot_h}" class="axis"/>')
    parts.append("</svg>")
    legend = "".join(f'<span class="key"><i style="background:{SERIES[k]}"></i>{esc(n)}</span>' for k, (n, _) in enumerate(series))
    return f'<figure><figcaption>{esc(title)}</figcaption><div class="legend">{legend}</div>{"".join(parts)}</figure>'


def crosstab_table(df, rcol, ccol, rorder, corder, rname, cname, corner, highlight=None):
    ct = pd.crosstab(df[rcol], df[ccol])
    ct = ct.reindex(index=[r for r in rorder if r in ct.index], columns=[c for c in corder if c in ct.columns], fill_value=0)
    vmax = ct.values.max()
    head = "".join(f"<th>{esc(cname.get(c, c))}</th>" for c in ct.columns)
    body = []
    for r in ct.index:
        cells = []
        for c in ct.columns:
            v = int(ct.loc[r, c])
            alpha = 0.08 + 0.55 * (v / vmax) ** 0.5 if v else 0
            cls = ' class="hl"' if highlight and (r, c) in highlight else ""
            cells.append(f'<td{cls} style="--a:{alpha:.2f}">{fmt(v)}</td>')
        body.append(f"<tr><th>{esc(rname.get(r, r))}<span class='tot'>{fmt(ct.loc[r].sum())}</span></th>{''.join(cells)}</tr>")
    foot = "".join(f"<td class='coltot'>{fmt(ct[c].sum())}</td>" for c in ct.columns)
    return (f'<div class="tablewrap"><table class="xt"><thead><tr><th class="corner">{esc(corner)}</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}<tr><th>Column total</th>{foot}</tr></tbody></table></div>')


def simple_table(header, rows, num_from=1):
    h = "".join(f"<th>{esc(x)}</th>" for x in header)
    b = []
    for r in rows:
        tds = "".join(f'<td class="{"num" if k >= num_from else ""}">{x}</td>' for k, x in enumerate(r))
        b.append(f"<tr>{tds}</tr>")
    return f'<div class="tablewrap"><table class="st"><thead><tr>{h}</tr></thead><tbody>{"".join(b)}</tbody></table></div>'


# ----------------------------------------------------------------- page

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;
--heat:42,120,214;--note:#fff8e6;--noteb:#eda100}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--heat:57,135,229;--note:#2a2410;--noteb:#c98500}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;
--heat:57,135,229;--note:#2a2410;--noteb:#c98500}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:32px 16px 80px}
h1{font-size:26px;line-height:1.25;margin:0 0 6px}
h2{font-size:19px;margin:44px 0 10px;padding-top:12px;border-top:1px solid var(--grid)}
h3{font-size:15px;margin:22px 0 6px}
p,li{color:var(--ink)} .sub{color:var(--ink2);margin:0 0 18px}
code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:0 4px}
.note{background:var(--note);border-left:3px solid var(--noteb);padding:10px 14px;border-radius:4px;margin:14px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:18px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.tile b{display:block;font-size:24px;line-height:1.2} .tile span{color:var(--ink2);font-size:13px}
figure{margin:16px 0;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 14px 8px}
figcaption{font-weight:600;margin-bottom:4px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;color:var(--ink2);margin-bottom:6px}
.key i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
svg.chart{width:100%;min-width:620px;height:auto;display:block}
figure{overflow-x:auto} td{white-space:nowrap}
.grid{stroke:var(--grid);stroke-width:1} .axis{stroke:var(--axis);stroke-width:1}
.tick{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.rowlab{fill:var(--ink2);font-size:12.5px} .seglab{fill:#fff;font-size:11.5px;font-weight:600;pointer-events:none}
.vallab{fill:var(--ink2);font-size:11px;font-variant-numeric:tabular-nums;pointer-events:none}
[data-tip]{cursor:default} path[data-tip]:hover,rect[data-tip]:hover{opacity:.85}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--page);font-size:12.5px;padding:5px 8px;border-radius:4px;opacity:0;transition:opacity .08s;max-width:320px;z-index:9}
.tablewrap{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums;background:var(--surface)}
th,td{padding:6px 10px;border:1px solid var(--grid);text-align:right}
th{font-weight:600;background:var(--page)} thead th{text-align:center} tbody th{text-align:left}
.st td{text-align:left} .st td.num{text-align:right}
.xt td{background:rgba(var(--heat),var(--a))} .xt td.hl{outline:2px solid var(--s2);outline-offset:-2px;font-weight:700}
.xt .corner{text-align:left;color:var(--ink2);font-weight:500} .tot{display:block;color:var(--muted);font-weight:400;font-size:12px}
.coltot{color:var(--ink2)}
dl.gloss dt{font-weight:600;margin-top:8px} dl.gloss dd{margin:0 0 0 16px;color:var(--ink2)}
.toc{columns:2;font-size:14px} .toc a{color:var(--ink2)}
@media (max-width:600px){.toc{columns:1} h1{font-size:22px}}
"""

JS = """
const tip=document.getElementById('tip');
document.addEventListener('mousemove',e=>{const t=e.target.closest('[data-tip]');
if(!t){tip.style.opacity=0;return}tip.textContent=t.dataset.tip;tip.style.opacity=1;
const x=Math.min(e.clientX+14,window.innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(e.clientY+14)+'px'});
"""


def body_zh(v):
    """Chinese report body; every number comes from build_html's locals."""
    g, n, nroof, both, t2, iv = v["g"], v["n"], v["nroof"], v["both"], v["t2"], v["iv"]
    s_all, pa_all, ap_all, role_agree = v["s_all"], v["pa_all"], v["ap_all"], v["role_agree"]
    c31, big, lost, gained = v["c31"], v["big"], v["lost"], v["gained"]
    bb_same, bb_one, t2_diff, p31, p31_keep = v["bb_same"], v["bb_one"], v["t2_diff"], v["p31"], v["p31_keep"]
    prod_stats, prod_obs_med, t3_obs_med, wmax, agree_pct = v["prod_stats"], v["prod_obs_med"], v["t3_obs_med"], v["wmax"], v["agree_pct"]
    a2u = int(((both.t3_label_gemini31 == "absent") & (both.t3_label_gemini35 == "unknown")).sum())
    u2a = int(((t2.t2_label_gemini31 == "unknown") & (t2.t3_label_gemini31 == "absent")).sum())
    a2p = int(((t2.t2_label_gemini31 == "absent") & (t2.t3_label_gemini31 == "present")).sum())
    b2c = int(((iv.g31_status == "bounded") & (iv.g35_status == "conflict_pending_review")).sum())
    c35_prod = int(((iv.prod_status == "done_appears") & (iv.g35_status == "conflict_pending_review")).sum())
    run_tab = simple_table(["日期", "模型", "请求", "屋顶数", "日期数", "成功调用", "无可采纳标签的日期"], [
        ["2026-10-06", "gemini-3.1-flash-lite", "T3", fmt(g['roofs']), fmt(n), fmt(n - g['g31_invalid'] - 4), fmt(g['g31_invalid'])],
        ["2026-10-06", "gemini-3.1-flash-lite", "T2", fmt(g['t2_roofs']), fmt(g['t2_dates']), fmt(g['t2_dates']), "0"],
        ["2026-10-07", "gemini-3.5-flash-lite", "T3", fmt(g['roofs']), fmt(n), fmt(n - g['g35_invalid'] - 4), fmt(g['g35_invalid'])],
    ], num_from=3)
    return f"""
<h1>开普敦十格全时间线评分：T3 与 T2 结果</h1>
<p class="sub">报告日期 2026-10-07 · 开普敦 10 个冻结网格里的 1,114 座普查屋顶光伏 ·
2019-01-01 到普查截止日 2025-01-31 之间每一个可下载的卫星日期 · Gemini 3.1 flash-lite（T3 和 T2）与 Gemini 3.5 flash-lite（T3）</p>

<div class="note"><b>阅读前提。</b>本报告的所有标签都是模型对历史卫星图的判读，没有任何一条经过人工核对，所以下面没有一项比较能说明准确率。
两轮结果一致，只说明两个模型看法一致，它们也可能一起看错。区间更窄、看不清更少，也都不等于日期更准。</div>

<div class="tiles">
<div class="tile"><b>{fmt(g['roofs'])}</b><span>座屋顶（开发格 {fmt(nroof['development'])}，评估格 {fmt(nroof['evaluation'])}）</span></div>
<div class="tile"><b>{fmt(n)}</b><span>每个模型评分的「屋顶 × 日期」图像数</span></div>
<div class="tile"><b>{agree_pct}</b><span>同一批图上 3.1 与 3.5 标签一致的比例</span></div>
<div class="tile"><b>{fmt(g['g31_bounded'])} → {fmt(g['g35_bounded'])}</b><span>有界安装区间的屋顶数，3.1 → 3.5</span></div>
</div>

<nav class="toc">
<a href="#takeaways">1. 主要发现</a><br><a href="#design">2. 跑了什么</a><br><a href="#labels">3. 日期标签</a><br>
<a href="#agree">4. 同一批图上 Gemini 3.1 与 3.5 的比较</a><br><a href="#t2">5. T2 与 T3：卫星参考图有没有影响</a><br>
<a href="#intervals">6. 安装区间</a><br><a href="#prod">7. 与 2026-09-02 生产表对照</a><br>
<a href="#grids">8. 分格明细</a><br><a href="#next">9. 能说明什么、还不能说明什么</a><br><a href="#files">10. 文件与定义</a>
</nav>

<h2 id="takeaways">1. 主要发现</h2>
<ol>
<li><b>把每个日期都读一遍，大多数安装区间就站不住了。</b>模型不再是「定出区间就停」，而是把窗口内每张图都判一遍之后，3.1 下
{fmt(g['roofs'])} 座屋顶里有 {fmt(c31)} 座（{pct(c31, g['roofs'], 0)}）出现「先判有板、后面又判无板」。这些屋顶记为<i>冲突</i>，不给区间，因为标签一条都没改。
这件事重要，是因为生产表里已有括号的 {fmt(g['prod_bracket'])} 座屋顶中，有 {fmt(g['prod_bracket_t31_conflict'])} 座在这里成了冲突：生产扫描提前停了，
没有读到后面那些与它矛盾的日期。如果忽略这一点、把生产表日期当成定论，就等于保留了一批完整标签序列并不支持的区间。</li>
<li><b>两个模型版本大约每五个日期就有一个判得不一样。</b>在同一批图、两边都给出可采纳标签的 {fmt(len(both))} 个日期上，标签一致的是 {fmt(s_all)} 个（{agree_pct}）。
分歧有方向：3.1 判有板的日期里，3.5 把 {fmt(pa_all)} 个改判成无板，反方向只有 {fmt(ap_all)} 个。也就是说，光是换模型版本就会挪动安装日期。
忽略这一点，任何依赖具体模型版本的结果都会显得比实际更稳。</li>
<li><b>换成 3.5 后，有界区间从 {fmt(g['g31_bounded'])} 座降到 {fmt(g['g35_bounded'])} 座。</b>两个模型下都有界的只有 {fmt(g['both_bounded'])} 座，其中 {fmt(g['both_bounded'] - bb_same)} 座端点不同。
如果用 3.5 换掉 3.1，会丢掉 {fmt(lost)} 座 3.1 有界的屋顶，新纳入 {fmt(gained)} 座，两组都没有人工核对过。所以不能凭这些计数选模型，需要有人工标注的真值。</li>
<li><b>加上普查后卫星参考图（T3 对 T2），{fmt(g['t2_dates'])} 个日期里有 {fmt(t2_diff)} 个标签变了（{pct(t2_diff, g['t2_dates'])}）。</b>
变化主要是模型把「看不清」判成「无板」，以及更常判「有板」。第三张图确实改变了模型的回答，但它是把回答往真值推，还是往「有板」推，目前没有检验。</li>
<li><b>开发格和评估格的变化方向相同。</b>3.1 → 3.5 的「有板改无板」在两组格里都出现（一致率：开发格 {role_agree[0][3]}，评估格 {role_agree[1][3]}），
说明这是模型层面的整体变化，不是评估格特有的问题。不过评估格主要由 CPT1956 构成（849 座里占 {fmt(big['CPT1956'])} 座），更像一个大格加几个小格，而不是八个均等的格。</li>
</ol>

<h2 id="design">2. 跑了什么</h2>
<p>样本是 2026-10-04 冻结的开普敦 10 个网格（随机种子 20261004）。其中 CPT2304 和 CPT3359 两格是<b>开发格</b>（265 座屋顶），即设计提示词时看过的格；
其余八格是<b>评估格</b>（849 座屋顶），设计提示词时没有看过。每座屋顶都是普查里已知的光伏多边形。对每座屋顶，我们把 2019-01-01 到普查截止日 2025-01-31
之间每一张可下载的历史卫星图都评了一遍，总共 {fmt(n)} 个「屋顶 × 日期」图像。</p>
<p>用了两种请求：</p>
<ul>
<li><b>T3（一次请求三张图）</b>：历史卫星候选图、同一屋顶的普查航拍、一张普查后的卫星图（拍摄于 2025 年 2 月到 12 月）。后两张图让模型知道这套光伏长什么样，
只有第一张图是这个日期的证据。</li>
<li><b>T2（一次请求两张图）</b>：同一张候选图和同一张普查航拍，不附普查后卫星图。T2 只在 149 座配对屋顶（{fmt(g['t2_dates'])} 个日期）上跑，用来衡量第三张图起了什么作用。</li>
</ul>
{run_tab}
<p>两轮 T3 用的是逐字节相同的图和相同的提示词，只换了模型名。每一次成功调用返回的模型版本都与请求一致，且都是三张图。四张近白芯片（CPT2935 格，2022-12-30）
没有发给任何一个模型，记为 <code>pixel_uninformative</code>。另有 4,555 个目录日期覆盖不完整，完全没有评分，不在文件里，不能当成无板。</p>
<p><b>已知的简化。</b>普查航拍是 768×768 的裁片，不是图层原始分辨率。普查后卫星参考取的是盘上排第一的那张，不是整个日期池里最清楚的一张。
航拍身份（航拍里是不是同一套光伏）没有逐屋顶人工裁定。2026-10-04 那轮单图试跑已经作废，本报告完全没有用它。</p>

<h2 id="labels">3. 日期标签</h2>
<p>每个「屋顶 × 日期」图像得到以下之一：<b>无板</b>（看不到光伏板）、<b>有板</b>（看得到）、<b>看不清</b>（模型无法判断），或者无可采纳标签。
「无可采纳标签」是指模型回答了三次，每次都违反标签规则（例如一边说图不能用、一边又给出有板或无板）。这些日期留空，不记成无板。</p>
{v['label_chart']}
{v['lab_table']}
<p>换成 3.5 后，有板减少约四分之一（{fmt(g['g31_present'])} → {fmt(g['g35_present'])}），看不清增加约四倍（{fmt(g['g31_unknown'])} → {fmt(g['g35_unknown'])}），
无可采纳标签的日期从 {g['g31_invalid']} 个增加到 {g['g35_invalid']} 个。这些是合计数，下一节看单个日期是怎么流动的。
如果把这 {g['g35_invalid']} 个日期填成无板，增加的无板恰好落在模型没有给出可用答案的地方。</p>

<h2 id="agree">4. 同一批图上 Gemini 3.1 与 3.5 的比较</h2>
<p>这里只比较两个模型都给出可采纳标签的 {fmt(len(both))} 个日期。这些日期上三张图的哈希值（文件指纹）完全相同，所以任何差别都来自模型，不来自输入。
加框的格子是最大的分歧。</p>
{v['agree_tab']}
{v['role_agree_tab']}
<p>在这些日期上，3.1 判有板的 {fmt(len(p31))} 个里，3.5 保留了 {fmt(p31_keep)} 个（{pct(p31_keep, len(p31), 0)}），把 {fmt(pa_all)} 个判成无板。
反方向上，3.5 在 3.1 判无板的地方新判出 {fmt(ap_all)} 个有板，还把 {fmt(a2u)} 个无板改成看不清。总体看，3.5 更不愿意判「有板」。
开发格一致率 {role_agree[0][3]}，评估格 {role_agree[1][3]}，两组都有「有板改无板」，所以这个变化不是某一组格特有的。</p>

<h2 id="t2">5. T2 与 T3：卫星参考图有没有影响</h2>
<p>在 149 座配对屋顶上，同一个模型（3.1）把同一张候选图和同一张航拍读了两次：一次附普查后卫星图（T3），一次不附（T2）。
全部 {fmt(g['t2_dates'])} 个日期上，候选图和航拍的字节都完全一致。</p>
{v['t2_tab']}
{v['t2_role_tab']}
<p>标签不同的日期有 {fmt(t2_diff)} 个（{pct(t2_diff, g['t2_dates'])}）。最大的两类变化是 T2 看不清 → T3 无板（{fmt(u2a)}）和 T2 无板 → T3 有板（{fmt(a2p)}）。
有了参考图，模型判得更果断，也更常看到板。T2 只是同一个模型少看一张图，不是人工基准，所以这只能说明参考图影响很大，不能说明 T3 更准。</p>

<h2 id="intervals">6. 安装区间</h2>
<p>每座屋顶的日期标签按时间排好，归成一个区间状态。标签从不修改（每座屋顶的 <code>labels_modified</code> 都是 0）。</p>
<dl class="gloss">
<dt>有界</dt><dd>先是一段无板，后面是一段有板，之后再没有无板。安装日落在最后一次无板和第一次有板之间。</dd>
<dt>冲突</dt><dd>有板之后又出现无板。原样保留、不给区间，因为修补就等于假定其中某个标签是错的。</dd>
<dt>左删失</dt><dd>「左删失」是指安装发生在观测窗口开始之前，只知道早于某个日期。这里是从第一张图起每个可用日期都有板；2019 不是安装年份。</dd>
<dt>未定</dt><dd>窗口内没有确认的有板。没有用普查航拍把区间封在 2025-01-31，因为航拍身份没有裁定。</dd>
</dl>
{v['iv_chart']}
{v['iv_tab']}
<h3>从 3.1 到 3.5 的状态变化</h3>
{v['iv_x']}
<p>{fmt(g['roofs'])} 座屋顶里有 {fmt(g['interval_same_status'])} 座（{pct(g['interval_same_status'], g['roofs'], 0)}）状态不变。两个模型下都有界的 {fmt(g['both_bounded'])} 座里，
{fmt(bb_same)} 座两个端点都没变，{fmt(g['both_bounded'] - bb_same - bb_one)} 座两个端点都变了，只变一个端点的是 {fmt(bb_one)} 座。
最大的流向是有界 → 冲突（{fmt(b2c)} 座）：3.5 多出来的无板标签落在某个有板日期之后，把序列打断了。</p>
<h3>有界区间有多宽？</h3>
{v['width_chart']}
<p>宽度集中在一个月左右和一年左右两处（最长 {wmax} 天）。每个宽度都是两张相邻可用图之间的间隔，所以这个形状由这座屋顶什么时候有卫星图决定，
而不是由模型有多确定决定。某一格区间更短，不代表那一格的日期更准。</p>

<h2 id="prod">7. 与 2026-09-02 生产表对照</h2>
<p>生产表（2026-09-02 研究预览中共享过）用<i>自适应扫描</i>给这 1,114 座屋顶定过日期：一直读到能定出区间就停，并且把夹在两次有板中间的单个无板改成有板
（「凹陷修补」）。T3 读每一个日期，什么都不修补。两者用的图也不完全一样，所以差异不能全部归到参考图上。这里的生产值取本地版本，其中屋顶
t00010449 和 t00010452 在 2026-10-05 重算过；Dropbox 上的研究预览仍是这两座的旧值。</p>
<h3>生产表状态 × T3 3.1</h3>
{v['prod_tabs']['g31']}
<h3>生产表状态 × T3 3.5</h3>
{v['prod_tabs']['g35']}
<p>生产表有括号的 {fmt(g['prod_bracket'])} 座屋顶里，3.1 下有 {fmt(g['prod_bracket_t31_conflict'])} 座是冲突，3.5 下有 {fmt(c35_prod)} 座。生产扫描每座屋顶用的观测次数中位数是
{prod_obs_med}，完整 T3 时间线每座屋顶可评分日期的中位数是 {t3_obs_med}。后来出现的无板标签，就来自这些多读的日期。</p>
<h3>两边都给出区间时</h3>
{v['pr_tab']}
<p>在 3.1 下，{fmt(prod_stats['g31']['n'])} 座里有 {fmt(prod_stats['g31']['mid_in'])} 座的生产中点严格落在 T3 区间之内，{fmt(prod_stats['g31']['year'])} 座至少有一个端点换了年份。
如果用 T3 替换生产日期，主要改的是区间宽度，同时会把 {fmt(g['prod_bracket_t31_conflict'])} 座原本有日期的屋顶变成没有日期的冲突；如果保留生产日期不动，
就保留了完整标签序列不支持的区间。没有人工标注，两种做法都没有依据。</p>

<h2 id="grids">8. 分格明细</h2>
{v['grid_tab']}
<p>849 座评估格屋顶里，CPT1956 一格就占 {fmt(big['CPT1956'])} 座，另有四个评估格各只有 14 座或更少。所以评估格的合计数主要反映 CPT1956、CPT3234、CPT2935 和 CPT2947。</p>

<h2 id="next">9. 能说明什么、还不能说明什么</h2>
<p><b>能说明：</b>全时间线 T3 流程在 1,114 座屋顶上完整跑通，两个模型的输入完全相同；标签结果明显取决于模型版本和参考图；
把每个日期都读一遍后，大多数屋顶至少出现一次「有板后又无板」。</p>
<p><b>不能说明：</b>T3、3.5、3.1 或生产表中哪一个更准。一致率、区间宽度和看不清的比例都不是准确率。</p>
<p><b>需要人工标注日期才能回答的问题：</b></p>
<ul>
<li>3.1 判有板、3.5 判无板的那 {fmt(pa_all)} 个日期，是真的有板，还是误报？</li>
<li>3.1 下 {fmt(c31)} 座冲突背后的反转，是真的拆除、图像配准偏差，还是标签噪声？这决定凹陷修补是否站得住。</li>
<li>普查后参考图是把标签往真值推，还是往「有板」推？</li>
</ul>

<h2 id="files">10. 文件与定义</h2>
<ul>
<li><code>{LABELS_NAME}</code>：每座屋顶的每个目录日期一行（{fmt(n)} 行）。包括两个模型的 T3 标签、配对屋顶上的 T2 标签（其他屋顶留空，表示没跑），
以及普查后卫星参考图的日期。Excel 可以打开。</li>
<li><code>{INTERVALS_NAME}</code>：每座屋顶一行（{fmt(g['roofs'])} 行）。包括两个模型各自的区间状态、端点、宽度和标签计数，以及生产表的状态和区间。
与 2026-09-02 预览用 <code>anchor_id</code> = <code>source_anchor_id</code> 连接。</li>
<li><code>gates.json</code>：本报告的每个计数，都在构建时从源表重新算出，并与运行记录逐项核对。</li>
<li><code>README.md</code>：逐列说明（英文）。</li>
</ul>
<p>CSV 里的标签取值：<code>absent</code>（无板）、<code>present</code>（有板）、<code>unknown</code>（看不清）、<code>invalid_response</code>（三次都没有可采纳标签）、
<code>pixel_uninformative</code>（近白芯片，未发送）。没有行的日期就是没有评分，不要填成无板。</p>
"""


def build_html(labels, iv, pair, g, lang="en"):
    zh = lang == "zh"
    tr = ZH if zh else {}

    def t(x):
        return tr.get(x, x)
    LN = LABEL_NAME_ZH if zh else LABEL_NAME
    SN = STATUS_NAME_ZH if zh else STATUS_NAME
    PN = PROD_NAME_ZH if zh else PROD_NAME
    L = labels
    n = len(L)
    roles = ["development", "evaluation"]
    nroof = iv.groupby("grid_role").size()

    # --- date label chart rows
    rows = []
    for tag, col, mname in (("3.1", "t3_label_gemini31", "3.1"), ("3.5", "t3_label_gemini35", "3.5")):
        for r in roles:
            vc = label_bucket(L.loc[L.grid_role == r, col]).value_counts().to_dict()
            rows.append((f"Gemini {mname} · {t(r)}", vc))
    label_chart = stacked_bars(rows, LABEL_ORDER, LN, t("T3 label share per catalog date, by model and grid role"), unit=t("dates"))
    lab_rows = []
    for col, mname in (("t3_label_gemini31", "Gemini 3.1 flash-lite"), ("t3_label_gemini35", "Gemini 3.5 flash-lite")):
        vc = L[col].value_counts()
        lab_rows.append([mname] + [fmt(vc.get(k, 0)) for k in ["absent", "present", "unknown", "invalid_response", "pixel_uninformative"]] + [fmt(n)])
    lab_table = simple_table([t(x) for x in ["Model", "Absent", "Present", "Unclear", "Not admitted", "Near-white chip", "Total"]], lab_rows)

    # --- model agreement
    both = L[L.t3_label_gemini31.isin(ADMITTED) & L.t3_label_gemini35.isin(ADMITTED)]
    agree_tab = crosstab_table(both, "t3_label_gemini31", "t3_label_gemini35", ADMITTED, ADMITTED, LN, LN,
                               t("rows: 3.1 · columns: 3.5"), highlight={("present", "absent")})
    role_agree = []
    for r in roles:
        b = both[both.grid_role == r]
        s = int((b.t3_label_gemini31 == b.t3_label_gemini35).sum())
        pa = int(((b.t3_label_gemini31 == "present") & (b.t3_label_gemini35 == "absent")).sum())
        ap = int(((b.t3_label_gemini31 == "absent") & (b.t3_label_gemini35 == "present")).sum())
        role_agree.append([t(r.capitalize()), fmt(len(b)), fmt(s), pct(s, len(b)), fmt(pa), fmt(ap)])
    s_all = g["both_admitted_same_label"]
    pa_all = g["present31_absent35"]
    ap_all = int(((both.t3_label_gemini31 == "absent") & (both.t3_label_gemini35 == "present")).sum())
    role_agree.append([t("All ten grids"), fmt(len(both)), fmt(s_all), pct(s_all, len(both)), fmt(pa_all), fmt(ap_all)])
    role_agree_tab = simple_table([t(x) for x in ["Grid role", "Dates, both admitted", "Same label", "Agreement", "3.1 present → 3.5 absent", "3.1 absent → 3.5 present"]], role_agree)
    p31 = both[both.t3_label_gemini31 == "present"]
    p31_keep = int((p31.t3_label_gemini35 == "present").sum())

    # --- T2 vs T3
    t2 = L[L.t2_label_gemini31.notna()]
    t2_tab = crosstab_table(t2, "t2_label_gemini31", "t3_label_gemini31", ADMITTED, ADMITTED, LN, LN,
                            t("rows: T2 (2 images) · columns: T3 (3 images)"), highlight={("unknown", "absent"), ("absent", "present")})
    t2_role = []
    for r in roles:
        b = t2[t2.grid_role == r]
        s = int((b.t2_label_gemini31 == b.t3_label_gemini31).sum())
        t2_role.append([t(r.capitalize()), fmt(b.anchor_id.nunique()), fmt(len(b)), fmt(s), pct(s, len(b)),
                        fmt((b.t2_label_gemini31 == "present").sum()), fmt((b.t3_label_gemini31 == "present").sum()),
                        fmt((b.t2_label_gemini31 == "unknown").sum()), fmt((b.t3_label_gemini31 == "unknown").sum())])
    t2_role_tab = simple_table([t(x) for x in ["Grid role", "Roofs", "Dates", "Same label", "Agreement", "T2 present", "T3 present", "T2 unclear", "T3 unclear"]], t2_role)
    t2_diff = g["t2_dates"] - g["t2_same"]

    # --- intervals
    irows = []
    for tag, mname in (("g31", "3.1"), ("g35", "3.5")):
        for r in roles:
            irows.append((f"Gemini {mname} · {t(r)}", iv.loc[iv.grid_role == r, f"{tag}_status"].value_counts().to_dict()))
    iv_chart = stacked_bars(irows, STATUS_ORDER, SN, t("Install-interval status per roof, by model and grid role"), unit=t("roofs"))
    iv_rows = []
    for tag, mname in (("g31", "Gemini 3.1"), ("g35", "Gemini 3.5")):
        for r in roles + ["all"]:
            sub = iv if r == "all" else iv[iv.grid_role == r]
            vc = sub[f"{tag}_status"].value_counts()
            w = sub.loc[sub[f"{tag}_status"] == "bounded", f"{tag}_width_days"].astype(float)
            iv_rows.append([f"{mname} · {t(r)}", fmt(len(sub))] + [fmt(vc.get(s, 0)) for s in STATUS_ORDER]
                           + [f"{w.median():.0f}" if len(w) else "–"])
    iv_tab = simple_table([t(x) for x in ["Model · role", "Roofs", "Bounded", "Conflict", "Left-censored", "Undated", "Median bounded width (days)"]], iv_rows)
    iv_x = crosstab_table(iv, "g31_status", "g35_status", STATUS_ORDER, STATUS_ORDER, SN, SN, t("rows: 3.1 · columns: 3.5"))
    bb = iv[(iv.g31_status == "bounded") & (iv.g35_status == "bounded")]
    bb_same = g["both_bounded_same_ends"]
    bb_one = int(((bb.g31_interval_start == bb.g35_interval_start) ^ (bb.g31_interval_end == bb.g35_interval_end)).sum())
    lost = int(((iv.g31_status == "bounded") & (iv.g35_status != "bounded")).sum())
    gained = int(((iv.g31_status != "bounded") & (iv.g35_status == "bounded")).sum())

    def wbins(tag):
        w = iv.loc[iv[f"{tag}_status"] == "bounded", f"{tag}_width_days"].astype(int)
        return [int(((w >= lo) & (w <= hi)).sum()) for lo, hi, _ in WIDTH_BINS]
    width_chart = grouped_columns([b[2] for b in WIDTH_BINS], [("Gemini 3.1", wbins("g31")), ("Gemini 3.5", wbins("g35"))],
                                  t("Width of bounded intervals (last absent → first present), roofs per bin"), unit=t("roofs"))
    wmax = int(max(iv.g31_width_days.max(), iv.g35_width_days.max()))

    # --- production
    prod_tabs = {}
    prod_stats = {}
    for tag in ("g31", "g35"):
        prod_tabs[tag] = crosstab_table(iv, "prod_status", f"{tag}_status", PROD_ORDER, STATUS_ORDER, PN, SN,
                                        t("rows: 2026-09-02 production · columns: T3 ") + f"{tag[1]}.{tag[2]}",
                                        highlight={("done_appears", "conflict_pending_review")})
        b = iv[(iv.prod_status == "done_appears") & (iv[f"{tag}_status"] == "bounded")]
        ps, pe = pd.to_datetime(b.prod_interval_start), pd.to_datetime(b.prod_interval_end)
        ts, te = pd.to_datetime(b[f"{tag}_interval_start"]), pd.to_datetime(b[f"{tag}_interval_end"])
        wp, wt = (pe - ps).dt.days, (te - ts).dt.days
        exact = (ps == ts) & (pe == te)
        mid = pd.to_datetime(b.prod_install_date)
        prod_stats[tag] = dict(
            n=len(b), wider=int((wt > wp).sum()), same=int((wt == wp).sum()), narrower=int((wt < wp).sum()),
            med_p=float(wp.median()), med_t=float(wt.median()), exact=int(exact.sum()),
            inside=int(((ps >= ts) & (pe <= te) & ~exact).sum()), earlier=int((te < ps).sum()), later=int((ts > pe).sum()),
            year=int(((ps.dt.year != ts.dt.year) | (pe.dt.year != te.dt.year)).sum()),
            mid_in=int(((mid > ts) & (mid < te)).sum()))
        st = prod_stats[tag]
        st["overlap"] = st["n"] - st["exact"] - st["inside"] - st["earlier"] - st["later"]
    pr_w, pr_p = [], []
    for tag, mname in (("g31", "Gemini 3.1"), ("g35", "Gemini 3.5")):
        s = prod_stats[tag]
        pr_w.append([mname, fmt(s["n"]), f'{s["med_p"]:.0f}', f'{s["med_t"]:.0f}', fmt(s["wider"]), fmt(s["same"]), fmt(s["narrower"])])
        pr_p.append([mname, fmt(s["exact"]), fmt(s["inside"]), fmt(s["overlap"]), fmt(s["earlier"]), fmt(s["later"]), fmt(s["year"])])
    pr_tab = (simple_table([t(x) for x in ["T3 model", "Roofs bracketed in both", "Median width, production (days)", "Median width, T3 (days)",
                            "T3 wider", "Same width", "T3 narrower"]], pr_w)
              + simple_table([t(x) for x in ["T3 model", "Identical endpoints", "Production bracket inside T3 (not identical)", "Partial overlap",
                              "T3 entirely earlier", "T3 entirely later", "At least one endpoint changes year"]], pr_p))
    prod_obs_med = int(iv.prod_n_observations.astype(int).median())
    t3_obs_med = int((iv.g31_n_present.astype(int) + iv.g31_n_absent.astype(int) + iv.g31_n_unknown.astype(int)).median())

    # --- per grid
    grid_rows = []
    for (gid, role), sub in iv.groupby(["grid_id", "grid_role"]):
        ld = L[L.grid_id == gid]
        grid_rows.append([f"{gid}", t(role), fmt(len(sub)), fmt(len(ld)),
                          fmt((ld.t3_label_gemini31 == "present").sum()), fmt((ld.t3_label_gemini35 == "present").sum()),
                          fmt((ld.t3_label_gemini35 == "invalid_response").sum()),
                          fmt((sub.g31_status == "bounded").sum()), fmt((sub.g35_status == "bounded").sum()),
                          fmt((sub.g31_status == "conflict_pending_review").sum()), fmt((sub.g35_status == "conflict_pending_review").sum())])
    grid_rows.sort(key=lambda r: (iv.loc[iv.grid_id == r[0], "grid_role"].iat[0], r[0]))
    grid_tab = simple_table([t(x) for x in ["Grid", "Role", "Roofs", "Dates", "Present 3.1", "Present 3.5", "Not admitted 3.5",
                             "Bounded 3.1", "Bounded 3.5", "Conflict 3.1", "Conflict 3.5"]], grid_rows, num_from=2)
    big = iv.grid_id.value_counts()

    agree_pct = pct(s_all, len(both))
    c31, c35 = g["g31_conflict"], g["g35_conflict"]

    if zh:
        body = body_zh(locals())
    else:
        body = f"""
<h1>Cape Town ten-grid full-timeline scoring: T3 and T2 results</h1>
<p class="sub">Report date 2026-10-07 · 1,114 census rooftop PV installations in 10 frozen Cape Town grids ·
every downloadable satellite date from 2019-01-01 to the 2025-01-31 census cutoff · Gemini 3.1 flash-lite (T3 and T2) and Gemini 3.5 flash-lite (T3)</p>

<div class="note"><b>How to read this report.</b> Everything here is a model reading of historical satellite images. No human has checked
any of these labels, so none of the comparisons below measure accuracy. When two runs agree, the models agree; they can be wrong together.
A narrower interval or fewer unclear dates is not evidence of a better date.</div>

<div class="tiles">
<div class="tile"><b>{fmt(g['roofs'])}</b><span>roofs ({fmt(nroof['development'])} development, {fmt(nroof['evaluation'])} evaluation)</span></div>
<div class="tile"><b>{fmt(n)}</b><span>roof × date images scored per model</span></div>
<div class="tile"><b>{agree_pct}</b><span>date labels on which 3.1 and 3.5 agree (same images)</span></div>
<div class="tile"><b>{fmt(g['g31_bounded'])} → {fmt(g['g35_bounded'])}</b><span>roofs with a bounded install interval, 3.1 → 3.5</span></div>
</div>

<nav class="toc">
<a href="#takeaways">1. Main findings</a><br><a href="#design">2. What was run</a><br><a href="#labels">3. Date-level labels</a><br>
<a href="#agree">4. Gemini 3.1 vs 3.5 on the same images</a><br><a href="#t2">5. T2 vs T3: does the satellite reference matter?</a><br>
<a href="#intervals">6. Install intervals</a><br><a href="#prod">7. Against the 2026-09-02 production table</a><br>
<a href="#grids">8. Per-grid breakdown</a><br><a href="#next">9. What this supports and what is still open</a><br><a href="#files">10. Files and definitions</a>
</nav>

<h2 id="takeaways">1. Main findings</h2>
<ol>
<li><b>Reading every date breaks most install intervals.</b> When the model labels every image in the window instead of stopping once
an interval is found, {fmt(c31)} of {fmt(g['roofs'])} roofs under 3.1 ({pct(c31, g['roofs'], 0)}) show a present label followed later by an absent label.
These roofs are kept as <i>conflict</i>, with no interval, because nothing was repaired. This matters because {fmt(g['prod_bracket_t31_conflict'])} of the
{fmt(g['prod_bracket'])} roofs with a dated production bracket become conflicts: the production scan stopped early and never read the later dates that contradict it.
If we ignore this and treat production dates as settled, we keep intervals that the full sequence of labels does not support.</li>
<li><b>The two model versions disagree on about one date in five.</b> On {fmt(len(both))} dates where both models gave a usable label on identical images,
they agree on {fmt(s_all)} ({agree_pct}). The disagreement is directional: 3.5 calls {fmt(pa_all)} of 3.1's present labels absent, and only {fmt(ap_all)}
moves the other way. A model upgrade alone therefore shifts install dates. If we ignore this, any result that depends on the exact model version will look
more stable than it is.</li>
<li><b>Under 3.5, bounded intervals fall from {fmt(g['g31_bounded'])} to {fmt(g['g35_bounded'])}.</b> Only {fmt(g['both_bounded'])} roofs are bounded under both models, and
{fmt(g['both_bounded'] - bb_same)} of those {fmt(g['both_bounded'])} have different endpoints. Choosing 3.5 over 3.1 would drop {fmt(lost)} roofs that 3.1 had bounded and add {fmt(gained)} new ones,
without any human check of either set. The model choice cannot be made on these counts; it needs labelled ground truth.</li>
<li><b>Adding the post-census satellite reference (T3 vs T2) changes {fmt(t2_diff)} of {fmt(g['t2_dates'])} date labels ({pct(t2_diff, g['t2_dates'])}).</b>
Most of the change is the model resolving "unclear" into "absent" and seeing more "present". The third image changes what the model says. Whether it changes it
toward the truth is untested.</li>
<li><b>The development and evaluation grids move in the same direction.</b> The 3.1 → 3.5 present → absent shift appears in both grid roles
(agreement {role_agree[0][3]} development, {role_agree[1][3]} evaluation). That points to a model-wide effect rather than one peculiar to the evaluation grids. The evaluation side
is dominated by CPT1956 ({fmt(big['CPT1956'])} of 849 roofs), so it is closer to one large grid than to eight equal ones.</li>
</ol>

<h2 id="design">2. What was run</h2>
<p>The sample is 10 Cape Town grids frozen on 2026-10-04 (seed 20261004). Two grids, CPT2304 and CPT3359, are <b>development</b> grids
(265 roofs), meaning they were used while designing the prompt. The other eight are <b>evaluation</b> grids (849 roofs), which were not looked at while
designing it. Each roof is a known census PV polygon. For each roof we scored every downloadable historical satellite image dated
from 2019-01-01 up to the census cutoff of 2025-01-31: {fmt(n)} roof-date images in total.</p>
<p>Two request types were used:</p>
<ul>
<li><b>T3 (three images)</b>: the historical satellite candidate, the census aerial photo of the same roof, and one post-census satellite image of the roof
(dated 2025-02 to 2025-12). The second and third images show the model what the installation looks like. Only the first image is the evidence for that date.</li>
<li><b>T2 (two images)</b>: the same candidate and the same census aerial, without the post-census satellite image. T2 was run only on 149 paired roofs
({fmt(g['t2_dates'])} dates) to measure what the third image does.</li>
</ul>
{simple_table(["Run", "Model", "Request", "Roofs", "Dates", "Successful calls", "Dates with no admitted label"], [
    ["2026-10-06", "gemini-3.1-flash-lite", "T3", fmt(g['roofs']), fmt(n), fmt(n - g['g31_invalid'] - 4), fmt(g['g31_invalid'])],
    ["2026-10-06", "gemini-3.1-flash-lite", "T2", fmt(g['t2_roofs']), fmt(g['t2_dates']), fmt(g['t2_dates']), "0"],
    ["2026-10-07", "gemini-3.5-flash-lite", "T3", fmt(g['roofs']), fmt(n), fmt(n - g['g35_invalid'] - 4), fmt(g['g35_invalid'])],
], num_from=3)}
<p>Both T3 runs used byte-identical images and the same prompt; only the model name differs. Every successful call returned the requested model version and
contained exactly three images. Four near-white chips (grid CPT2935, 2022-12-30) were not sent to either model and are labelled
<code>pixel_uninformative</code>. A further 4,555 catalog dates had incomplete coverage and were not scored at all; they are not in the files and must not be read as absent.</p>
<p><b>Known simplifications.</b> Census aerials are 768×768 crops, not the layer's native resolution. The post-census satellite reference is the first-ranked
frame already on disk, not the clearest frame from the full date pool. Aerial identity (that the aerial shows the same installation) was not adjudicated
roof by roof. An earlier single-image run from 2026-10-04 is void and is not used anywhere here.</p>

<h2 id="labels">3. Date-level labels</h2>
<p>Each roof-date image receives one of: <b>absent</b> (no panels), <b>present</b> (panels visible), <b>unclear</b> (the model could not decide), or no admitted label.
"No admitted label" means the model replied three times but each reply broke the labelling rules (for example, it marked the image unusable and still
gave present or absent). Those dates are left blank; they are not coded absent.</p>
{label_chart}
{lab_table}
<p>Under 3.5 the present share falls by about a quarter ({fmt(g['g31_present'])} → {fmt(g['g35_present'])}), unclear rises about fourfold
({fmt(g['g31_unknown'])} → {fmt(g['g35_unknown'])}), and dates with no admitted label rise from {g['g31_invalid']} to {g['g35_invalid']}. These are totals; the next section shows where individual dates move.
If the {g['g35_invalid']} unadmitted dates were filled in as absent, absent would grow precisely where the model gave no usable answer.</p>

<h2 id="agree">4. Gemini 3.1 vs 3.5 on the same images</h2>
<p>This compares the two models on the {fmt(len(both))} dates where both returned an admitted label. On all of them the three image hashes are identical, so any
difference comes from the model, not the input. The outlined cell is the largest disagreement.</p>
{agree_tab}
{role_agree_tab}
<p>Of 3.1's {fmt(len(p31))} present labels on these dates, 3.5 keeps {fmt(p31_keep)} ({pct(p31_keep, len(p31), 0)}) and calls {fmt(pa_all)} absent. In the other
direction, 3.5 adds {fmt(ap_all)} present labels where 3.1 said absent, and turns {fmt(int(((both.t3_label_gemini31 == 'absent') & (both.t3_label_gemini35 == 'unknown')).sum()))} absent labels into unclear.
So 3.5 is more conservative about calling panels present. Agreement is {role_agree[0][3]} on development grids and {role_agree[1][3]} on evaluation grids,
and the present → absent flow appears on both, so the change is not specific to one grid role.</p>

<h2 id="t2">5. T2 vs T3: does the satellite reference matter?</h2>
<p>On the 149 paired roofs, the same model (3.1) read the same candidate and the same aerial twice: once with the post-census satellite image (T3) and once
without (T2). The candidate and aerial bytes match on all {fmt(g['t2_dates'])} dates.</p>
{t2_tab}
{t2_role_tab}
<p>Labels differ on {fmt(t2_diff)} dates ({pct(t2_diff, g['t2_dates'])}). The two largest moves are T2-unclear → T3-absent
({fmt(int(((t2.t2_label_gemini31 == 'unknown') & (t2.t3_label_gemini31 == 'absent')).sum()))}) and T2-absent → T3-present
({fmt(int(((t2.t2_label_gemini31 == 'absent') & (t2.t3_label_gemini31 == 'present')).sum()))}). With the reference image the model is more decisive and sees
panels more often. T2 is the same model with less information, not a human benchmark, so this shows that the reference image is influential. It does not
show that T3 is more accurate.</p>

<h2 id="intervals">6. Install intervals</h2>
<p>Each roof's date labels, in time order, are turned into one interval status. Labels are never edited (<code>labels_modified</code> is 0 on every roof).</p>
<dl class="gloss">
<dt>Bounded</dt><dd>An absent run followed by a present run with no later absent. The install date lies between the last absent date and the first present date.</dd>
<dt>Conflict</dt><dd>A present label followed later by an absent label. This is kept as is and no interval is given, because repairing it would assume which label is wrong.</dd>
<dt>Left-censored</dt><dd>Present on every usable date from the first image onward. The installation predates the first usable image; 2019 is not an install year.</dd>
<dt>Undated</dt><dd>No confirmed present in the window. The census aerial was not used to close the interval at 2025-01-31, because aerial identity was not adjudicated.</dd>
</dl>
{iv_chart}
{iv_tab}
<h3>Status change from 3.1 to 3.5</h3>
{iv_x}
<p>{fmt(g['interval_same_status'])} of {fmt(g['roofs'])} roofs ({pct(g['interval_same_status'], g['roofs'], 0)}) keep the same status. Of the {fmt(g['both_bounded'])} roofs bounded under both models,
{fmt(bb_same)} keep both endpoints, {fmt(g['both_bounded'] - bb_same - bb_one)} change both, and {fmt(bb_one)} change exactly one. The largest flow is bounded → conflict
({fmt(int(((iv.g31_status == 'bounded') & (iv.g35_status == 'conflict_pending_review')).sum()))} roofs): the extra absent labels from 3.5 land after a present date and break the sequence.</p>
<h3>How wide are the bounded intervals?</h3>
{width_chart}
<p>Widths cluster near one month and near one year (up to {wmax} days). Each width is the gap between two consecutive usable images, so the
pattern is set by when satellite images exist for a roof, not by how sure the model is. A shorter interval on one grid does not mean that grid's dates are more correct.</p>

<h2 id="prod">7. Against the 2026-09-02 production table</h2>
<p>The production table (shared in the 2026-09-02 research preview) dated these same 1,114 roofs with an <i>adaptive</i> scan: it read dates until it
could bracket the install and then stopped, and it repaired isolated absent dips between presents. The T3 runs read every date and repair nothing. The two
also use different images, so differences cannot all be attributed to the reference images. The production values used here are the local copy, in which
roofs t00010449 and t00010452 were re-scored on 2026-10-05; the Dropbox research preview still carries their earlier values.</p>
<h3>Production status vs T3 3.1</h3>
{prod_tabs['g31']}
<h3>Production status vs T3 3.5</h3>
{prod_tabs['g35']}
<p>Of {fmt(g['prod_bracket'])} roofs with a dated production bracket, {fmt(g['prod_bracket_t31_conflict'])} are conflicts under 3.1 and
{fmt(int(((iv.prod_status == 'done_appears') & (iv.g35_status == 'conflict_pending_review')).sum()))} under 3.5. The production scan used a median of
{prod_obs_med} observations per roof, while the full T3 timeline has a median of {t3_obs_med} scorable dates. The extra dates are where the later absent labels come from.</p>
<h3>Where both give a bracket</h3>
{pr_tab}
<p>Under 3.1, the production midpoint falls strictly inside the T3 bracket for {fmt(prod_stats['g31']['mid_in'])} of {fmt(prod_stats['g31']['n'])} roofs, and {fmt(prod_stats['g31']['year'])} roofs
have at least one endpoint in a different year. Replacing production dates with T3 dates would mostly change bracket widths and would turn {fmt(g['prod_bracket_t31_conflict'])} dated roofs into undated
conflicts. Keeping production dates unchanged keeps intervals that the full label sequence does not support. Neither choice can be justified without human labels.</p>

<h2 id="grids">8. Per-grid breakdown</h2>
{grid_tab}
<p>CPT1956 alone holds {fmt(big['CPT1956'])} of the 849 evaluation roofs, and four evaluation grids have 14 roofs or fewer. Evaluation-side totals therefore mostly describe
CPT1956, CPT3234, CPT2935 and CPT2947.</p>

<h2 id="next">9. What this supports and what is still open</h2>
<p><b>Supported:</b> the full-timeline T3 pipeline runs end to end on 1,114 roofs with identical inputs across models; label outcomes depend materially on the model
version and on the reference image; most roofs show at least one present-then-absent reversal when every date is read.</p>
<p><b>Not supported:</b> any statement that T3, 3.5, 3.1, or the production table is more accurate than another. Agreement rates, interval widths and unclear rates
are not accuracy measures.</p>
<p><b>Open questions, which need human-labelled dates to answer:</b></p>
<ul>
<li>Are the 3.1 present labels that 3.5 calls absent (n = {fmt(pa_all)}) real panels or false positives?</li>
<li>Are the reversals behind the {fmt(c31)} conflicts (3.1) real removals, image misregistration, or label noise? The answer decides whether dip repair is
legitimate.</li>
<li>Does the post-census reference image push labels toward the truth or toward "present"?</li>
</ul>

<h2 id="files">10. Files and definitions</h2>
<ul>
<li><code>{LABELS_NAME}</code>: one row per roof × catalog date ({fmt(n)} rows). It holds the T3 label from each model, the T2 label on paired roofs
(blank elsewhere, meaning not run), and the date of the post-census satellite reference. Excel opens it.</li>
<li><code>{INTERVALS_NAME}</code>: one row per roof ({fmt(g['roofs'])} rows). It holds interval status, endpoints, widths and label counts for each model,
and the production status and interval. Join to the 2026-09-02 preview on <code>anchor_id</code> = <code>source_anchor_id</code>.</li>
<li><code>gates.json</code>: every count in this report, recomputed from the source tables at build time and checked against the run notes.</li>
<li><code>README.md</code>: column dictionary.</li>
</ul>
<p>Label values: <code>absent</code>, <code>present</code>, <code>unknown</code> (shown as "unclear" here), <code>invalid_response</code> (no admitted label after
three attempts), <code>pixel_uninformative</code> (near-white chip, not sent). A date with no row was not scored. Do not fill such dates in as absent.</p>
"""
    if zh:
        body = body.replace("<th>Column total</th>", "<th>列合计</th>")
        # A source line break next to a CJK character renders as a stray space.
        body = re.sub(r"(?<=[^\x00-\x7f])\n|\n(?=[^\x00-\x7f])", "", body)
    title = "十格 T3/T2 结果" if zh else "Ten-grid T3/T2 results"
    return (f'<!doctype html><html lang="{'zh-CN' if zh else 'en'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><style>{CSS}</style></head><body><main>{body}</main><div id="tip"></div>'
            f'<script>{JS}</script></body></html>')


README = """# Cape Town ten-grid full-timeline scoring: T3 and T2 results

Delivery date: 2026-10-07
Sample: 1,114 census rooftop PV installations in 10 frozen Cape Town grids
(development: CPT2304, CPT3359; evaluation: the other eight).
Window: every downloadable satellite date with 2019-01-01 <= capture_date < 2025-01-31.

These are model readings of satellite images. No labels have been checked by
a person. Agreement between runs is model agreement, not accuracy.

Start with `ct_10grid_t3_t2_overview_2026-10-07.html` (open in any browser;
it is self-contained).

## Files

- `ct_10grid_t3_t2_overview_2026-10-07.html` — the overview report.
- `ct_10grid_t3_t2_overview_2026-10-07_zh.html` — the same report in Chinese (same numbers).
- `ct_10grid_date_labels_20261007.csv` — one row per roof x catalog date (51,212 rows).
- `ct_10grid_intervals_20261007.csv` — one row per roof (1,114 rows).
- `gates.json` — recomputed counts, checked against the run notes at build time.
- `SHA256SUMS.txt` — checksums.

## Date-label file

| column | meaning |
|---|---|
| `anchor_id` | installation id; same as `source_anchor_id` in the 2026-09-02 preview |
| `grid_id`, `grid_role` | grid and role (`development` / `evaluation`) |
| `capture_date` | historical satellite image date (catalog day) |
| `t3_label_gemini31` | three-image label from gemini-3.1-flash-lite |
| `t3_label_gemini35` | three-image label from gemini-3.5-flash-lite, same images |
| `t2_label_gemini31` | two-image label (no satellite reference), 149 paired roofs only; blank = not run |
| `t2_paired_roof` | 1 if the roof is one of the 149 paired roofs |
| `satellite_reference_date` | date of the post-census satellite image shown as the third image |

Label values: `absent`, `present`, `unknown` (model could not decide),
`invalid_response` (three replies, none admissible under the label rules;
not absent), `pixel_uninformative` (near-white chip, not sent to the model).
4,555 catalog dates with incomplete coverage were not scored and have no row.
Do not fill missing dates in as absent.

## Interval file

Prefix `g31_` = gemini-3.1-flash-lite T3, `g35_` = gemini-3.5-flash-lite T3,
`prod_` = the 2026-09-02 production table (local copy; t00010449 and
t00010452 re-scored 2026-10-05 and differ from the Dropbox preview).

| column | meaning |
|---|---|
| `centroid_lon`, `centroid_lat`, `area_m2` | from the production table |
| `*_status` | `bounded`, `conflict_pending_review`, `left_censored`, `undated` |
| `*_install_relation` | how the install date relates to the interval |
| `*_interval_start`, `*_interval_end` | last absent date and first present date (bounded only) |
| `*_width_days` | interval_end - interval_start |
| `*_last_absent_date`, `*_earliest_present_date` | sequence anchors |
| `*_conflict_dates` | present dates followed later by an absent label (pipe-separated) |
| `*_n_present`, `*_n_absent`, `*_n_unknown`, `*_n_call_failed` | label counts |
| `prod_status` | production `scan_status` |
| `prod_install_date` | production midpoint |
| `prod_interval_start`, `prod_interval_end`, `prod_width_days` | production bracket |
| `prod_n_observations` | dates the adaptive production scan used |

Labels were never repaired in the T3 runs (`labels_modified` = 0 on every
roof). A conflict has no interval by design. Left-censored means present from
the first usable image; 2019 is not the install year. Undated means no
confirmed present in the window; the census aerial was not used to close the
interval at 2025-01-31.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    s31, s35, pair, i31, i35, prod = load()
    labels = build_labels(s31, s35, pair)
    iv = build_intervals(i31, i35, prod)
    g = compute(labels, iv, pair)
    check_gates(g)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    labels.to_csv(out / LABELS_NAME, index=False)
    iv.to_csv(out / INTERVALS_NAME, index=False)
    (out / HTML_NAME).write_text(build_html(labels, iv, pair, g), encoding="utf-8")
    (out / HTML_ZH_NAME).write_text(build_html(labels, iv, pair, g, lang="zh"), encoding="utf-8")
    (out / "README.md").write_text(README, encoding="utf-8")
    gates = {"agreement_is_accuracy": 0, "sources": {"gemini31": str(RUN31), "gemini35": str(RUN35), "production": str(PROD)},
             "counts": g, "expected_from_run_notes": EXPECTED, "all_expected_match": True}
    (out / "gates.json").write_text(json.dumps(gates, indent=2) + "\n", encoding="utf-8")
    files = [HTML_NAME, HTML_ZH_NAME, LABELS_NAME, INTERVALS_NAME, "gates.json", "README.md"]
    sums = [f"{hashlib.sha256((out / f).read_bytes()).hexdigest()}  {f}" for f in files]
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    for line in sums:
        print(line)


if __name__ == "__main__":
    main()
