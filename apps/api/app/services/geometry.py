"""Deterministic geometry layer: IR → Shapely, category collections, repairs.

All regulatory math happens here or in modules built on top of this one.
The LLM never receives or produces coordinates for compliance purposes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import explain_validity

from ..models.ir import CadDocument, IREntity


@dataclass
class GeometryIndex:
    """Shapely geometries per entity + per category, with a repair log."""
    by_entity: dict[str, BaseGeometry] = field(default_factory=dict)
    entity_meta: dict[str, IREntity] = field(default_factory=dict)
    by_category: dict[str, list[str]] = field(default_factory=dict)  # category -> entity ids
    repair_log: list[str] = field(default_factory=list)

    def geoms_for(self, categories: set[str] | list[str]) -> list[tuple[str, BaseGeometry]]:
        out = []
        for c in categories:
            for eid in self.by_category.get(c, []):
                out.append((eid, self.by_entity[eid]))
        return out

    def union_for(self, categories: set[str] | list[str]) -> BaseGeometry | None:
        gs = [g for _, g in self.geoms_for(categories)]
        if not gs:
            return None
        return unary_union(gs)


def entity_to_geometry(e: IREntity, repair_log: list[str] | None = None) -> BaseGeometry | None:
    g = e.geometry
    try:
        if e.kind in ("polyline", "polygon"):
            pts = g.get("points") or []
            if len(pts) < 2:
                return None
            rings = g.get("rings") or []
            if e.kind == "polygon" and len(rings) > 1:
                return _hatch_area(rings, e, repair_log)
            if e.kind == "polygon" and len(pts) >= 3:
                poly = Polygon(pts)
                if not poly.is_valid:
                    reason = explain_validity(poly)
                    poly = poly.buffer(0)
                    if repair_log is not None:
                        repair_log.append(f"repaired polygon {e.id} ({e.layer}): {reason}")
                if poly.is_empty:
                    return LineString(pts)
                return poly
            return LineString(pts)
        if e.kind == "point":
            pos = g.get("position")
            return Point(pos) if pos else None
        if e.kind == "text":
            return None  # annotations carry no regulatory geometry
    except Exception as exc:  # degenerate coordinates etc.
        if repair_log is not None:
            repair_log.append(f"dropped entity {e.id} ({e.layer}): {exc}")
    return None


def _hatch_area(rings: list, e: IREntity, repair_log: list[str] | None) -> BaseGeometry | None:
    """A hatch with several boundary loops, filled by parity as AutoCAD does:
    an island inside the outer loop is a hole (a lawn or a tree pit inside
    the asphalt of a cover plan), an island inside that is filled again."""
    polys = []
    for r in rings:
        if len(r) < 3:
            continue
        p = Polygon(r)
        if not p.is_valid:
            p = p.buffer(0)
        if not p.is_empty and p.area > 0:
            polys.append(p)
    if not polys:
        return None
    polys.sort(key=lambda p: -p.area)
    area = polys[0]
    if len(polys) <= 200:
        for p in polys[1:]:
            area = area.symmetric_difference(p)
    else:  # many loops: holes inside the outer loop only (two-level parity)
        inner = unary_union([p for p in polys[1:] if polys[0].contains(p.representative_point())])
        area = polys[0].difference(inner)
    if not area.is_valid:
        area = area.buffer(0)
        if repair_log is not None:
            repair_log.append(f"repaired hatch {e.id} ({e.layer})")
    return None if area.is_empty else area


def _linear_categories() -> set[str]:
    from ..models.ir import UTILITY_CATEGORIES
    return {"CURB", "FENCE"} | UTILITY_CATEGORIES


def build_geometry_index(doc: CadDocument) -> GeometryIndex:
    idx = GeometryIndex()
    lm = doc.layer_map()
    linear = _linear_categories()
    outlines = 0
    for e in doc.entities:
        geom = entity_to_geometry(e, idx.repair_log)
        if geom is None or geom.is_empty:
            continue
        li = lm.get(e.layer)
        cat = li.classification.category if li else "UNKNOWN"
        # a curb, fence or network drawn as a CLOSED outline (a curb ring
        # around a lawn, a fence around a yard, a cable loop) is still a line:
        # the ground inside the ring is not the object — measure to the line
        if cat in linear and geom.geom_type in ("Polygon", "MultiPolygon"):
            geom = geom.boundary
            outlines += 1
        idx.by_entity[e.id] = geom
        idx.entity_meta[e.id] = e
        idx.by_category.setdefault(cat, []).append(e.id)
    if outlines:
        idx.repair_log.append(f"{outlines} closed outline(s) of curbs, fences or networks "
                              "taken as lines, not areas")
    link_power_line_symbols(idx)
    return idx


POWER_SYMBOL_MAX_M = 6.0     # a support symbol: 2.5 m stroke + 1 m arrow
POWER_SPAN_MAX_M = 60.0      # longest span between two supports joined as one wire
POWER_SPAN_MAX_ANGLE = 20.0  # degrees between a symbol's axis and the span


def link_power_line_symbols(idx: GeometryIndex) -> int:
    """Mosgeotrest draws an overhead line (layer «ЛЭП») as a symbol at every
    support — a 2.5 m stroke and a 1 m arrow along the line — not as the wire
    between supports. The protection zone runs along the wire (ПП РФ № 160,
    приложение п. «а»), so each symbol is joined to the nearest symbol ahead
    and behind along its own axis (within POWER_SPAN_MAX_M, off-axis by less
    than POWER_SPAN_MAX_ANGLE). Symbols across the street lie perpendicular
    to the axis and are not joined. The spans are added to the index as
    derived POWER_LINE_OVERHEAD entities; the drawing is not changed.
    Returns the number of spans."""
    import math

    import numpy as np
    from shapely import STRtree, get_coordinates

    ids = [i for i in idx.by_category.get("POWER_LINE_OVERHEAD", [])
           if not isinstance(idx.by_entity[i], Polygon)]
    if len(ids) < 2:
        return 0
    geoms = [idx.by_entity[i] for i in ids]
    blobs = unary_union([g.buffer(1.0) for g in geoms])
    tree = STRtree(geoms)
    symbols = []  # (centre, unit axis, a member entity id)
    for blob in getattr(blobs, "geoms", [blobs]):
        members = [ids[k] for k in tree.query(blob, predicate="intersects")]
        pts = np.vstack([get_coordinates(idx.by_entity[m]) for m in members])
        if len(pts) < 2 or np.ptp(pts, axis=0).max() > POWER_SYMBOL_MAX_M:
            continue  # a drawn wire, not a support symbol
        centre = pts.mean(axis=0)
        w, v = np.linalg.eigh(np.cov((pts - centre).T))
        symbols.append((centre, v[:, int(np.argmax(w))], members[0]))
    if len(symbols) < 2:
        return 0
    centres = np.array([c for c, _, _ in symbols])
    cos_max = math.cos(math.radians(POWER_SPAN_MAX_ANGLE))
    edges: set[tuple[int, int]] = set()
    for i, (c, axis, _) in enumerate(symbols):
        d = centres - c
        dist = np.hypot(d[:, 0], d[:, 1])
        ahead = behind = None
        for j in np.argsort(dist):
            if dist[j] > POWER_SPAN_MAX_M:
                break
            if j == i or dist[j] < 1e-6:
                continue
            cos = float(d[j] @ axis) / float(dist[j])
            if cos >= cos_max and ahead is None:
                ahead = int(j)
            elif cos <= -cos_max and behind is None:
                behind = int(j)
            if ahead is not None and behind is not None:
                break
        for j in (ahead, behind):
            if j is not None:
                edges.add((min(i, j), max(i, j)))
    for n, (i, j) in enumerate(sorted(edges)):
        a, b = (tuple(float(v) for v in symbols[k][0]) for k in (i, j))
        src = idx.entity_meta[symbols[i][2]]
        eid = f"{src.id}~span{n}"
        idx.by_entity[eid] = LineString([a, b])
        idx.entity_meta[eid] = IREntity(id=eid, type="SPAN", layer=src.layer, kind="polyline",
                                        geometry={"points": [list(a), list(b)], "closed": False},
                                        attributes={"derived": "power_line_span"})
        idx.by_category["POWER_LINE_OVERHEAD"].append(eid)
    idx.repair_log.append(f"power lines: {len(symbols)} support symbol(s) joined into "
                          f"{len(edges)} span(s) along the wire")
    return len(edges)


def _as_polygon(coords: list) -> Polygon | None:
    ring = Polygon(coords)
    if ring.is_valid and ring.area > 0:
        return ring
    repaired = ring.buffer(0)
    if isinstance(repaired, Polygon) and repaired.area > 0:
        return repaired
    if isinstance(repaired, MultiPolygon) and repaired.area > 0:
        return max(repaired.geoms, key=lambda p: p.area)
    return None


def stitch_rings(lines: list[LineString], tol: float = 2.0) -> list[Polygon]:
    """Join open polylines whose ends meet (within `tol` m) into closed rings.

    A chain that cannot be closed but has ≥ 4 points is closed by its own
    first-to-last segment — the long-standing behaviour for a single open
    boundary polyline."""
    import math
    pool = [list(ln.coords) for ln in lines if len(ln.coords) >= 2]
    out: list[Polygon] = []

    def near(a, b) -> bool:
        return math.dist(a[:2], b[:2]) <= tol

    while pool:
        chain = pool.pop(0)
        grown = True
        while grown and not (len(chain) >= 4 and near(chain[0], chain[-1])):
            grown = False
            for i, c in enumerate(pool):
                if near(chain[-1], c[0]):
                    chain = chain + c
                elif near(chain[-1], c[-1]):
                    chain = chain + c[::-1]
                elif near(chain[0], c[-1]):
                    chain = c + chain
                elif near(chain[0], c[0]):
                    chain = c[::-1] + chain
                else:
                    continue
                pool.pop(i)
                grown = True
                break
        if len(chain) >= 4:
            poly = _as_polygon(chain)
            if poly is not None:
                out.append(poly)
    return out


def detect_site_boundary(doc: CadDocument, idx: GeometryIndex) -> tuple[Polygon | None, str]:
    """Largest closed polygon on a SITE_BOUNDARY layer; fallback to drawing extents.

    A work boundary delivered in an xref package (layer classified with
    source="package") is often several separate areas — there the site is
    the union of all of them, not just the largest."""
    candidates: list[Polygon] = []
    from_package: list[Polygon] = []
    open_lines: dict[bool, list[LineString]] = {True: [], False: []}
    lm = doc.layer_map()
    for eid in idx.by_category.get("SITE_BOUNDARY", []):
        g = idx.by_entity[eid]
        li = lm.get(idx.entity_meta[eid].layer)
        pkg = li is not None and li.classification.source == "package"
        if isinstance(g, Polygon) and g.area > 0:
            candidates.append(g)
            if pkg:
                from_package.append(g)
        elif isinstance(g, LineString) and len(g.coords) >= 2:
            open_lines[pkg].append(g)
    # boundaries drawn as several open pieces (the Moscow work boundary is two
    # halves meeting end to end): stitch them into rings first
    for pkg, lines in open_lines.items():
        for ring in stitch_rings(lines):
            candidates.append(ring)
            if pkg:
                from_package.append(ring)
    if from_package:
        return parts_with_content(unary_union(from_package), idx), "detected"
    if candidates:
        return max(candidates, key=lambda p: p.area), "detected"

    (minx, miny), (maxx, maxy) = doc.extents.min, doc.extents.max
    return Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]), "extents_fallback"


def parts_with_content(site: BaseGeometry, idx: GeometryIndex) -> BaseGeometry:
    """Drop work-boundary parts with no drawing content inside. A base drawing
    may keep a stray copy of the contour far from everything else (Харьковский
    проезд: the same outline 5 km to the side) — that copy is not a site part.
    A part is kept when it holds at least 1 % of the entities of the richest
    part; the drawing itself is not changed."""
    import numpy as np
    from shapely import contains_xy
    parts = as_polygon_list(site)
    if len(parts) < 2:
        return site
    boundary_ids = set(idx.by_category.get("SITE_BOUNDARY", []))
    centres = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
               for eid, g in idx.by_entity.items()
               if eid not in boundary_ids and g is not None and not g.is_empty
               for b in (g.bounds,)]
    if not centres:
        return site
    xs, ys = np.array(centres).T
    counts = [int(contains_xy(p, xs, ys).sum()) for p in parts]
    keep = [p for p, n in zip(parts, counts) if n >= max(1, 0.01 * max(counts))]
    if not keep or len(keep) == len(parts):
        return site
    idx.repair_log.append(f"site: {len(parts) - len(keep)} work-boundary part(s) with no drawing "
                          f"content inside ignored (a copy of the contour beside the drawing)")
    return unary_union(keep)


def polygon_to_rings(geom: BaseGeometry) -> list[dict]:
    """→ [{shell: flat [x,y,...], holes: [flat,...]}, ...] for the API/UI."""
    polys: list[Polygon] = []
    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    elif hasattr(geom, "geoms"):
        polys = [g for g in geom.geoms if isinstance(g, Polygon)]
    out = []
    for p in polys:
        if p.is_empty or p.area <= 0:
            continue
        shell = [round(c, 3) for xy in p.exterior.coords for c in xy]
        holes = [[round(c, 3) for xy in ring.coords for c in xy] for ring in p.interiors]
        out.append({"shell": shell, "holes": holes})
    return out


def as_polygon_list(geom: BaseGeometry | None) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty]
    if hasattr(geom, "geoms"):
        return [g for g in geom.geoms if isinstance(g, Polygon) and not g.is_empty]
    return []
