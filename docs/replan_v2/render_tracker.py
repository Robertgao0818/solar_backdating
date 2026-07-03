#!/usr/bin/env python3
"""Render docs/replan_v2/ISSUE-*.md into a self-contained tracker.html.

Markdown is the single source of truth: each ISSUE file carries
``Status:`` / ``Phase:`` / ``Blocked by:`` header lines and ``- [ ]`` /
``- [x]`` acceptance-criteria checkboxes. This script derives everything else
— including execution order — nothing in the HTML is hand-maintained.

Dependency Tier is NOT the ``Phase:`` label (phases group by what a slice
touches, not when it can start), and it is NOT a capacity-bounded "today's
batch" — it is the raw topological level of the ``Blocked by:`` DAG:
tier(i) = 0 if no in-tracker deps, else 1 + max(tier(dep) for dep in deps).
All zero-dependency issues land in Tier 1 together, however many there are;
deciding how many of them to actually run in parallel today is a separate,
capacity-aware judgment call (agent count, API budget, coordination
overhead) that intentionally is NOT encoded here — ask for a "today's batch"
recommendation instead. Tier stays correct as issues are added without
editing this script — only the markdown.

Usage:  python3 docs/replan_v2/render_tracker.py
Output: docs/replan_v2/tracker.html
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

# CSS custom-property names, not literal colors — actual light/dark values
# are defined once in the stylesheet so status chips stay legible in both
# themes instead of baking a single hex into every element.
STATUS_TOKENS = {
    "ready-for-agent": ("--st-agent", "ready for agent"),
    "ready-for-human": ("--st-human", "ready for human"),
    "in-progress": ("--st-progress", "in progress"),
    "done": ("--st-done", "done"),
    "wontfix": ("--st-wontfix", "wontfix"),
}
BLOCKED_TOKEN = "--st-blocked"


def parse_issue(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    title = re.search(r"^# ISSUE-(\d+):\s*(.+)$", text, re.M)
    num = int(title.group(1)) if title else 0
    status_m = re.search(r"^Status:\s*(\S+)", text, re.M)
    phase_m = re.search(r"^Phase:\s*(.+)$", text, re.M)
    blocked_m = re.search(r"^Blocked by:\s*(.+)$", text, re.M)
    blocked_raw = blocked_m.group(1).strip() if blocked_m else "none"
    deps = []
    if not blocked_raw.lower().startswith("none"):
        deps = sorted(set(int(n) for n in re.findall(r"ISSUE-(\d+)", blocked_raw)))
    external = bool(re.search(r"dinov3", blocked_raw, re.I))
    ac_open = len(re.findall(r"^\s*- \[ \]", text, re.M))
    ac_done = len(re.findall(r"^\s*- \[x\]", text, re.M))
    return {
        "num": num,
        "title": title.group(2) if title else path.stem,
        "status": status_m.group(1) if status_m else "ready-for-agent",
        "phase": phase_m.group(1).strip() if phase_m else "?",
        "deps": deps,
        "deps_raw": blocked_raw,
        "external_dep": external,
        "ac_open": ac_open,
        "ac_done": ac_done,
        "file": path.name,
    }


def compute_waves(issues: list[dict]) -> dict[int, int]:
    """Structural topological level over the in-tracker Blocked-by DAG.

    Tier is independent of current Status — it is the fixed structural
    position in the DAG, so already-done issues still show the tier they
    belonged to (progress-through-tiers), and the ordering does not jump
    around as work completes. It is a lower bound on start order, not a
    schedule: everything in one tier is *structurally* startable together,
    not *recommended* to all start together.
    """
    by_num = {i["num"]: i for i in issues}
    wave: dict[int, int] = {}
    visiting: set[int] = set()

    def level(n: int) -> int:
        if n in wave:
            return wave[n]
        if n not in by_num:
            return 0  # dangling reference; don't let it break leveling
        if n in visiting:
            return 0  # dependency cycle guard — treat as no added delay
        visiting.add(n)
        deps = by_num[n]["deps"]
        lvl = 0 if not deps else 1 + max(level(d) for d in deps)
        visiting.discard(n)
        wave[n] = lvl
        return lvl

    for i in issues:
        level(i["num"])
    return wave


def main() -> None:
    # Only files whose title is "# ISSUE-<n>: ..." are tracked slices; other
    # ISSUE-##-prefixed files (e.g. decision memos) are supporting docs.
    issues = [
        parse_issue(p)
        for p in HERE.glob("ISSUE-*.md")
        if re.match(r"^# ISSUE-\d+:", p.read_text(encoding="utf-8"))
    ]
    by_num = {i["num"]: i for i in issues}
    waves = compute_waves(issues)
    for i in issues:
        i["wave"] = waves[i["num"]]
        i["blocked"] = any(
            by_num.get(d, {}).get("status") != "done" for d in i["deps"]
        ) or (i["external_dep"] and i["status"] != "done")
    issues.sort(key=lambda d: (d["wave"], d["num"]))

    n_done = sum(1 for i in issues if i["status"] == "done")
    ac_total = sum(i["ac_open"] + i["ac_done"] for i in issues)
    ac_done = sum(i["ac_done"] for i in issues)
    pct = round(100 * ac_done / ac_total) if ac_total else 0

    rows = []
    for i in issues:
        token, label = STATUS_TOKENS.get(i["status"], ("--st-wontfix", i["status"]))
        eff = (
            f'<span class="chip" style="--c:var({BLOCKED_TOKEN})">blocked</span>'
            if i["blocked"] and i["status"] not in ("done", "wontfix")
            else ""
        )
        total = i["ac_open"] + i["ac_done"]
        bar = (
            f'<div class="bar"><div style="width:{100 * i["ac_done"] / total:.0f}%"></div></div>'
            f'<span class="acnum">{i["ac_done"]}/{total}</span>'
            if total
            else ""
        )
        rows.append(
            f'<tr><td class="wave">{i["wave"] + 1}</td><td class="num">{i["num"]}</td>'
            f'<td><a href="{html.escape(i["file"])}">{html.escape(i["title"])}</a></td>'
            f'<td>{html.escape(i["phase"].split("—")[0].strip())}</td>'
            f'<td><span class="chip" style="--c:var({token})">{label}</span> {eff}</td>'
            f'<td class="deps">{html.escape(i["deps_raw"])}</td>'
            f'<td class="ac">{bar}</td></tr>'
        )

    # layered SVG dependency graph: columns are computed Dependency Tiers —
    # structural readiness, not a capacity-bounded schedule (see module docstring).
    cols: dict[int, list[dict]] = {}
    for i in issues:
        cols.setdefault(i["wave"], []).append(i)
    col_keys = sorted(cols)
    NW, NH, XG, YG, PAD = 210, 34, 90, 14, 20
    pos = {}
    for ci, k in enumerate(col_keys):
        for ri, i in enumerate(cols[k]):
            pos[i["num"]] = (PAD + ci * (NW + XG), PAD + 28 + ri * (NH + YG))
    w = PAD * 2 + len(col_keys) * (NW + XG) - XG
    h = PAD * 2 + 28 + max(len(v) for v in cols.values()) * (NH + YG) - YG
    svg = [
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img">',
        '<defs><marker id="a" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">'
        '<path d="M0,0 L7,3.5 L0,7 z" fill="var(--edge)"/></marker></defs>',
    ]
    for ci, k in enumerate(col_keys):
        svg.append(
            f'<text x="{PAD + ci * (NW + XG) + NW / 2}" y="{PAD + 8}" text-anchor="middle" '
            f'class="phlabel">Tier {k + 1}</text>'
        )
    for i in issues:
        for d in i["deps"]:
            if d not in pos:
                continue
            x1, y1 = pos[d][0] + NW, pos[d][1] + NH / 2
            x2, y2 = pos[i["num"]][0], pos[i["num"]][1] + NH / 2
            mx = (x1 + x2) / 2
            svg.append(
                f'<path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" class="edge" marker-end="url(#a)"/>'
            )
    for i in issues:
        x, y = pos[i["num"]]
        token, _ = STATUS_TOKENS.get(i["status"], ("--st-wontfix", ""))
        stroke_var = BLOCKED_TOKEN if (i["blocked"] and i["status"] != "done") else token
        svg.append(
            f'<g><rect x="{x}" y="{y}" width="{NW}" height="{NH}" rx="7" class="node" '
            f'style="stroke:var({stroke_var})"/>'
            f'<text x="{x + 10}" y="{y + NH / 2 + 4}" class="ntext">'
            f'{i["num"]} · {html.escape(i["title"][:26])}{"…" if len(i["title"]) > 26 else ""}</text></g>'
        )
    svg.append("</svg>")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Install-Date Optimization v2 — Tracker</title>
<style>
:root {{
  --bg:#fff; --fg:#111827; --mut:#6b7280; --line:#e5e7eb; --edge:#9ca3af; --card:#f9fafb;
  --st-agent:#2563eb; --st-human:#9333ea; --st-progress:#b45309; --st-done:#15803d;
  --st-wontfix:#6b7280; --st-blocked:#dc2626;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:#0b1020; --fg:#e5e7eb; --mut:#9ca3af; --line:#273046; --edge:#7280a0; --card:#141a2e;
    --st-agent:#7fb0ff; --st-human:#d9a8ff; --st-progress:#ffb454; --st-done:#5fe08a;
    --st-wontfix:#aab2c5; --st-blocked:#ff8686;
  }}
}}
:root[data-theme="dark"] {{
  --bg:#0b1020; --fg:#e5e7eb; --mut:#9ca3af; --line:#273046; --edge:#7280a0; --card:#141a2e;
  --st-agent:#7fb0ff; --st-human:#d9a8ff; --st-progress:#ffb454; --st-done:#5fe08a;
  --st-wontfix:#aab2c5; --st-blocked:#ff8686;
}}
:root[data-theme="light"] {{
  --bg:#fff; --fg:#111827; --mut:#6b7280; --line:#e5e7eb; --edge:#9ca3af; --card:#f9fafb;
  --st-agent:#2563eb; --st-human:#9333ea; --st-progress:#b45309; --st-done:#15803d;
  --st-wontfix:#6b7280; --st-blocked:#dc2626;
}}
body {{ margin:0; padding:2rem 2.5vw; background:var(--bg); color:var(--fg);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
h1 {{ font-size:1.35rem; margin:0 0 .25rem; }}
.sub {{ color:var(--mut); font-size:.85rem; margin-bottom:1.2rem; }}
.progress {{ height:10px; background:var(--line); border-radius:5px; overflow:hidden; max-width:520px; margin:.4rem 0 1.4rem; }}
.progress div {{ height:100%; background:var(--st-done); }}
table {{ border-collapse:collapse; width:100%; }}
th,td {{ text-align:left; padding:.5rem .7rem; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ font-size:.75rem; text-transform:uppercase; letter-spacing:.05em; color:var(--mut); }}
td.num, td.wave {{ color:var(--mut); }} td.deps {{ font-size:.8rem; color:var(--mut); max-width:16rem; }}
a {{ color:inherit; }}
.chip {{ display:inline-block; padding:.1rem .55rem; border-radius:99px; font-size:.72rem; font-weight:700;
  color:var(--c); border:1.5px solid var(--c);
  background:color-mix(in srgb, var(--c) 16%, transparent); white-space:nowrap; }}
td.ac {{ min-width:7rem; }} .bar {{ display:inline-block; width:4.5rem; height:7px; background:var(--line);
  border-radius:4px; overflow:hidden; vertical-align:middle; }}
.bar div {{ height:100%; background:var(--st-done); }} .acnum {{ font-size:.75rem; color:var(--mut); margin-left:.4rem; }}
.graph {{ margin-top:2rem; overflow-x:auto; background:var(--card); border:1px solid var(--line);
  border-radius:10px; padding:1rem; }}
svg {{ min-width:1150px; width:100%; height:auto; }}
.node {{ fill:var(--card); stroke-width:2; }} .ntext {{ font-size:12px; fill:var(--fg); }}
.edge {{ fill:none; stroke:var(--edge); stroke-width:1.3; }} .phlabel {{ font-size:11px; fill:var(--mut);
  text-transform:uppercase; letter-spacing:.08em; }}
</style></head><body>
<h1>Install-Date Optimization v2 — Tracker</h1>
<div class="sub">Rendered {stamp} from <code>ISSUE-*.md</code> — do not edit this file;
edit the markdown and re-run <code>render_tracker.py</code>. <strong>Tier</strong> = structural
topological level of the <code>Blocked by:</code> graph — everything in a tier is
<em>structurally</em> unblocked together, not a recommended same-day batch (capacity
bounds that too, and is a judgment call made per-session, not stored here).
Not the <code>Phase:</code> label either (phases group by what a slice touches).
PRD: <a href="../install_date_optimization_v2_prd.md">install_date_optimization_v2_prd.md</a> ·
Phase 3 slices: <a href="../dinov3_scorer/TRACKER.md">dinov3_scorer tracker</a></div>
<div><strong>{n_done}/{len(issues)}</strong> issues done · <strong>{ac_done}/{ac_total}</strong> acceptance criteria ({pct}%)</div>
<div class="progress"><div style="width:{pct}%"></div></div>
<table><thead><tr><th>Tier</th><th>#</th><th>Slice</th><th>Phase</th><th>Status</th><th>Blocked by</th><th>AC</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<div class="graph">{''.join(svg)}</div>
</body></html>"""
    out = HERE / "tracker.html"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(issues)} issues, {ac_done}/{ac_total} AC done, {len(col_keys)} tiers)")


if __name__ == "__main__":
    main()
