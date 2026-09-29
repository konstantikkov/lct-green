"""Plan pictures of end-to-end runs: one SVG per object (site boundary, CAD
context, exclusion zones, allowed area, lawn, plantings) and an HTML gallery
with the metrics — geometry errors that numbers hide (zones as beads instead
of a corridor, a stray copy of the boundary, a lawn cut out of its curb ring)
are seen at a glance.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

CONTEXT = {"BUILDING": "#64748b", "ROAD": "#94a3b8", "CURB": "#94a3b8", "SIDEWALK": "#cbd5e1"}
UTILITY_COLOR = "#f9a8d4"  # networks: the document endpoint sends them first on big drawings


def _context_color(category: str | None) -> str | None:
    if not category:
        return None
    if category.startswith("UTILITY") or category == "POWER_LINE_OVERHEAD":
        return UTILITY_COLOR
    return CONTEXT.get(category)
MAX_CONTEXT = 60000  # polylines of CAD context in one picture


def _rings_to_polys(rings: list) -> list:
    from shapely.geometry import Polygon
    out = []
    for r in rings or []:
        shell = r["shell"] if isinstance(r, dict) else r
        holes = r.get("holes", []) if isinstance(r, dict) else []
        pts = list(zip(shell[0::2], shell[1::2]))
        if len(pts) >= 3:
            p = Polygon(pts, [list(zip(h[0::2], h[1::2])) for h in holes])
            out.append(p if p.is_valid else p.buffer(0))
    return out


def _path(geom, tol: float) -> str:
    """SVG path data of a (multi)polygon, holes included (fill-rule evenodd)."""
    from shapely.geometry import MultiPolygon, Polygon
    polys = [geom] if isinstance(geom, Polygon) else list(getattr(geom, "geoms", []))
    parts = []
    for p in polys:
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        p = p.simplify(tol)
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)
            if len(pts) >= 3:
                parts.append("M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z")
    return "".join(parts)


def render_plan(constraints: dict, iteration: dict, document: dict | None, out: Path,
                title: str = "") -> Path:
    from shapely.ops import unary_union
    site = unary_union(_rings_to_polys(constraints.get("siteBoundary") or []))
    if site.is_empty:
        return out
    minx, miny, maxx, maxy = site.bounds
    pad = 0.03 * max(maxx - minx, maxy - miny)
    minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad
    w, h = maxx - minx, maxy - miny
    tol = max(w, h) / 3000  # ~1 px at 3000 px
    px_w = 1600
    stroke = max(w, h) / px_w  # 1 px in metres

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx:.1f} {-maxy:.1f} {w:.1f} {h:.1f}" '
             f'width="{px_w}" height="{px_w * h / w:.0f}" style="background:#fff">',
             f"<title>{html.escape(title)}</title>", '<g transform="scale(1,-1)">']

    # CAD context: buildings, roads, curbs, sidewalks
    if document:
        cats = {l["name"]: l["classification"]["category"] for l in document.get("layers", [])}
        n = 0
        for e in document.get("entities", []):
            color = _context_color(cats.get(e.get("layer")))
            flat = e.get("points") or []  # the document endpoint sends [x, y, x, y, …]
            pts = list(zip(flat[0::2], flat[1::2]))
            if not color or len(pts) < 2:
                continue
            if not any(minx <= p[0] <= maxx and miny <= p[1] <= maxy for p in pts[:: max(1, len(pts) // 8)]):
                continue
            d = "M" + " L".join(f"{p[0]:.1f} {p[1]:.1f}" for p in pts)
            parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{stroke:.2f}"/>')
            n += 1
            if n >= MAX_CONTEXT:
                break

    zones = unary_union([p for z in constraints.get("exclusionZones") or []
                         for p in _rings_to_polys(z.get("polygons") or [z.get("polygon")])])
    lawn = unary_union(_rings_to_polys((iteration.get("lawn") or {}).get("polygons") or []))
    allowed = unary_union(_rings_to_polys((constraints.get("allowedArea") or {}).get("polygons") or []))
    for geom, style in ((zones, 'fill="#ef4444" fill-opacity="0.12" stroke="none"'),
                        (lawn, 'fill="#84cc16" fill-opacity="0.35" stroke="none"'),
                        (allowed, f'fill="none" stroke="#15803d" stroke-width="{stroke * 1.2:.2f}"'),
                        (site, f'fill="none" stroke="#f59e0b" stroke-width="{stroke * 2.5:.2f}"')):
        if not geom.is_empty:
            parts.append(f'<path d="{_path(geom, tol)}" fill-rule="evenodd" {style}/>')

    for p in iteration.get("placements") or []:
        x, y = p["position"]
        tree = p["kind"] == "TREE"
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{max(p["crownRadiusM"], stroke * 2):.2f}" '
                     f'fill="{"#166534" if tree else "#4ade80"}" fill-opacity="{0.85 if tree else 0.7}"/>')
    parts.append("</g></svg>")
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def write_gallery(rows: dict, out_dir: Path) -> Path:
    """index.html: per object the plan picture, the metrics, problems, drift."""
    cards = []
    for name, r in rows.items():
        status = "OK" if r.get("ok") else "FAIL"
        issues = "".join(f"<li>{html.escape(p)}</li>" for p in (r.get("problems") or []) + (r.get("drift") or []))
        ref = r.get("reference") or {}
        brk = ref.get("referenceBreakingNorms") or {}
        ref_line = (f"<br>эталон: {ref.get('reference')}, ближе нормы (наша трактовка) — деревья "
                    f"{(brk.get('trees') or {}).get('percent')} %, кусты {(brk.get('shrubs') or {}).get('percent')} %"
                    if brk else "")
        img = f'<img src="{html.escape(name)}/plan.svg" loading="lazy">' if (out_dir / name / "plan.svg").exists() else ""
        cards.append(
            f'<section class="{status.lower()}"><h2>{html.escape(name)} — {status}</h2>'
            f"<p>загрузка {r.get('uploadS', '–')} с · генерация {r.get('generateS', '–')} с · "
            f"деревья {r.get('trees', '–')} · кусты {r.get('shrubs', '–')} · газон {r.get('lawnM2', '–')} м² · "
            f"допустимая область {r.get('allowedAreaM2', '–')} м² · статус {r.get('status', '–')}"
            f"<br>файлы: {html.escape(json.dumps(r.get('sources'), ensure_ascii=False))}{ref_line}</p>"
            f"{'<ul>' + issues + '</ul>' if issues else ''}{img}</section>")
    page = ("<!doctype html><meta charset='utf-8'><title>GreenPlan e2e</title>"
            "<style>body{font:14px system-ui,sans-serif;margin:16px;background:#f8fafc}"
            "section{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin:0 0 16px}"
            "section.fail{border-color:#ef4444}h2{margin:0 0 6px;font-size:16px}"
            "img{max-width:100%;border:1px solid #e2e8f0}li{color:#b91c1c}</style>"
            "<h1>GreenPlan — сквозные прогоны</h1>"
            "<p>Оранжевый — граница участка, зелёный контур — допустимая область, салатовый — газон, "
            "красный — зоны запрета, тёмно-зелёные круги — деревья, светлые — кусты; серые линии — дороги, "
            "борта и здания, розовые — сети (из выборки для просмотра, до 20 000 объектов).</p>" + "".join(cards))
    path = out_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path
