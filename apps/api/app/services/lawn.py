"""Lawn (herbaceous cover) recommendation — ТЗ §2 п. 3 «травянистые покрытия».

A lawn goes wherever the planting domain is unpaved and not taken by a shrub
bed. The distance norms do not limit it: СП 42.13330.2016 табл. 9.1 sets
distances for trees and shrubs only, and ПП РФ № 160 п. 11 «з» restricts
planting trees and shrubs in a protection zone — so utility buffers and
protection zones are NOT subtracted. Its construction follows 743-ПП п. 3.8:
a 20 cm fertile layer, shallower than the 0.3 m earthwork limit in a cable
protection zone (ПП РФ № 160 п. 11 «д»).
"""
from __future__ import annotations

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .constraints import FOOTPRINT_CATEGORIES, ConstraintResult
from .geometry import GeometryIndex, as_polygon_list, polygon_to_rings

LAWN_RULE_ID = "LAWN_RECOMMENDATION"
# paved or kept clear besides the footprints: a fire lane stays a hard surface
_HARD_SURFACES = FOOTPRINT_CATEGORIES | {"ACCESS_ROUTE"}


def lawn_geometry(summary: dict | None) -> BaseGeometry | None:
    """The lawn polygons stored in an iteration record, back as geometry.
    The rings are rounded to a millimetre when stored, which can make a ring
    touch itself (3-я Парковая: GEOS "side location conflict" on a union) —
    each part is made valid, and the parts are not unioned: they came out of
    a union and do not overlap."""
    from shapely.geometry import MultiPolygon, Polygon
    polys = []
    for r in (summary or {}).get("polygons", []):
        shell = list(zip(r["shell"][0::2], r["shell"][1::2]))
        holes = [list(zip(h[0::2], h[1::2])) for h in r.get("holes", [])]
        if len(shell) < 3:
            continue
        p = Polygon(shell, holes)
        if not p.is_valid:
            p = p.buffer(0)
        polys.extend(as_polygon_list(p))
    return MultiPolygon(polys) if polys else None


def recommend_lawn(constraints: ConstraintResult, idx: GeometryIndex, placements: list,
                   min_width_m: float = 1.0) -> tuple[BaseGeometry | None, dict]:
    """(lawn geometry or None, summary for the iteration record)."""
    domain = constraints.planting_domain
    if domain is None or domain.is_empty:
        domain = constraints.allowed_area
    if domain is None or domain.is_empty:
        return None, {"areaM2": 0.0, "polygonCount": 0, "polygons": [], "ruleId": LAWN_RULE_ID,
                      "basis": constraints.allowed_basis}
    subtract: list[BaseGeometry] = []
    for cat in _HARD_SURFACES:
        for eid in idx.by_category.get(cat, []):
            g = idx.by_entity[eid]
            # linear footprints get a nominal width, as in the allowed area
            subtract.append(g if g.geom_type in ("Polygon", "MultiPolygon") else g.buffer(0.3))
    # a shrub bed is not lawn; the lawn runs on under a tree crown
    subtract.extend(Point(p.x, p.y).buffer(p.crownRadiusM, 8)
                    for p in placements if p.kind == "SHRUB")
    lawn = domain
    if subtract:
        cut = unary_union(subtract)
        if not cut.is_valid:
            cut = cut.buffer(0)
        lawn = lawn.difference(cut)
    half = min_width_m / 2.0
    lawn = lawn.simplify(0.1).buffer(-half, resolution=4).buffer(half, resolution=4)
    if not lawn.is_valid:
        lawn = lawn.buffer(0)
    polys = [p for p in as_polygon_list(lawn) if p.area >= 1.0]
    lawn = unary_union(polys) if polys else None
    return lawn, {
        "areaM2": round(sum(p.area for p in polys), 1),
        "explanation": (
            f"Газон {format(round(sum(p.area for p in polys)), ',').replace(',', ' ')} м² "
            f"({len(polys)} уч.): область озеленения без твёрдых покрытий и кустарниковых групп, "
            f"полосы уже {min_width_m:g} м отброшены. "
            "Устройство — 743-ПП п. 3.8.1–3.8.2: спланированное основание с уклоном 2–3°, "
            "плодородный слой 20 см. Отступы СП 42 п. 9.6 (табл. 9.1) и охранные зоны "
            "ПП РФ № 160 (п. 11 «з») относятся к деревьям и кустарникам и газон не ограничивают."
        ),
        "polygonCount": len(polys),
        "polygons": polygon_to_rings(lawn) if lawn is not None else [],
        "ruleId": LAWN_RULE_ID,
        "basis": constraints.allowed_basis,
        "minWidthM": min_width_m,
    }
