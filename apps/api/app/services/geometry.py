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


def build_geometry_index(doc: CadDocument) -> GeometryIndex:
    idx = GeometryIndex()
    lm = doc.layer_map()
    for e in doc.entities:
        geom = entity_to_geometry(e, idx.repair_log)
        if geom is None or geom.is_empty:
            continue
        idx.by_entity[e.id] = geom
        idx.entity_meta[e.id] = e
        li = lm.get(e.layer)
        cat = li.classification.category if li else "UNKNOWN"
        idx.by_category.setdefault(cat, []).append(e.id)
    return idx


def detect_site_boundary(doc: CadDocument, idx: GeometryIndex) -> tuple[Polygon | None, str]:
    """Largest closed polygon on a SITE_BOUNDARY layer; fallback to drawing extents."""
    candidates: list[Polygon] = []
    for eid in idx.by_category.get("SITE_BOUNDARY", []):
        g = idx.by_entity[eid]
        if isinstance(g, Polygon) and g.area > 0:
            candidates.append(g)
        elif isinstance(g, LineString) and len(g.coords) >= 4:
            ring = Polygon(list(g.coords))
            if ring.is_valid and ring.area > 0:
                candidates.append(ring)
            else:
                repaired = ring.buffer(0)
                if isinstance(repaired, Polygon) and repaired.area > 0:
                    candidates.append(repaired)
    if candidates:
        return max(candidates, key=lambda p: p.area), "detected"

    (minx, miny), (maxx, maxy) = doc.extents.min, doc.extents.max
    return Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]), "extents_fallback"


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
