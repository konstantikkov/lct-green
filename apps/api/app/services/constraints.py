"""Regulatory constraint engine: rules × CAD geometry → exclusion zones and
allowed planting area. Pure deterministic Shapely math.

Every exclusion zone records exactly which rule and which source object
generated it (provenance), and whether the distance is verified or an
explicitly flagged assumption.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

from ..models.ir import CadDocument, UTILITY_CATEGORIES
from .geometry import GeometryIndex, as_polygon_list, detect_site_boundary, polygon_to_rings
from .rules import RuleRegistry

# Object categories whose own footprint is unplantable regardless of buffers.
FOOTPRINT_CATEGORIES = {"BUILDING", "ROAD", "SIDEWALK", "PARKING", "CURB"}

# Max number of per-entity zones per rule before merging (keeps API payload sane).
_MERGE_THRESHOLD = 150

# --- visibility triangles at road-road crossings (demo dimensions) ----------
VISIBILITY_RULE_ID = "VISIBILITY_TRIANGLE_NO_TALL_PLANTS"
_VIS_MIN_ROAD_LEN_M = 10.0    # skip exploded dash segments of linetypes
_VIS_CLUSTER_R_M = 15.0       # intersection points closer than this = one crossing
_VIS_MAX_CROSSINGS = 50       # runtime cap for 12k-entity topo maps
_VIS_LONG_M = 40.0            # sight leg along the priority direction (40 km/h)
_VIS_SHORT_M = 8.0            # leg across the secondary direction


@dataclass
class ExclusionZone:
    id: str
    ruleId: str
    generatedFrom: str
    sourceLayer: str
    obstacleCategory: str
    distanceM: float
    effectiveSource: str  # verified | assumption
    geometry: BaseGeometry
    source: dict = field(default_factory=dict)

    def to_api(self) -> dict:
        rings = polygon_to_rings(self.geometry)
        return {
            "id": self.id, "ruleId": self.ruleId, "generatedFrom": self.generatedFrom,
            "sourceLayer": self.sourceLayer, "obstacleCategory": self.obstacleCategory,
            "distanceM": self.distanceM, "effectiveSource": self.effectiveSource,
            "polygon": rings[0] if rings else {"shell": [], "holes": []},
            "polygons": rings,
            "source": self.source,
        }


@dataclass
class ConstraintResult:
    site_boundary: Polygon | None
    site_boundary_source: str
    exclusion_zones: list[ExclusionZone]
    allowed_area: BaseGeometry
    inactive_rules: list[dict]
    repair_log: list[str]
    planting_domain: BaseGeometry | None = None
    allowed_basis: str = "site_minus_exclusions"
    # provenance of the boundary the validator's inside-site check runs
    # against: "detected" | "inferred_drawing_extent" | "vegetation_evidence"
    site_boundary_kind: str = "detected"

    def visibility_zones(self) -> list["ExclusionZone"]:
        return [z for z in self.exclusion_zones if z.ruleId == VISIBILITY_RULE_ID]

    def to_api(self) -> dict:
        site = polygon_to_rings(self.site_boundary) if self.site_boundary else []
        allowed = polygon_to_rings(self.allowed_area)
        area = sum(p.area for p in as_polygon_list(self.allowed_area))
        return {
            "siteBoundary": [r["shell"] for r in site] or None,
            "siteBoundarySource": self.site_boundary_source,
            "siteBoundaryKind": self.site_boundary_kind,
            "allowedBasis": self.allowed_basis,
            "exclusionZones": [z.to_api() for z in self.exclusion_zones],
            "allowedArea": {"polygons": allowed, "areaM2": round(area, 1)},
            "repairLog": self.repair_log,
            "inactiveRules": self.inactive_rules,
        }


def detect_planting_domain(doc: CadDocument, idx: GeometryIndex, site: Polygon | None,
                           site_src: str, repair_log: list[str]):
    """Planting domain and its provenance.

    On drawings with a real (closed) site boundary the domain is that site.
    Topographic maps often have NO closed areas at all (boundaries drawn as
    exploded dash linetypes) — planting across the full extents is nonsense
    (trees end up on roads). There we derive vegetation zones from evidence:
    the union of buffers around existing-vegetation symbols, morphologically
    tightened to drop lone outliers. Basis is always reported to the UI.
    """
    import re as _re
    if site_src == "detected":
        return site, "site_boundary"

    evidence = []
    for cat in ("EXISTING_TREE", "EXISTING_SHRUB", "GREEN_AREA"):
        for eid in idx.by_category.get(cat, []):
            e = idx.entity_meta[eid]
            # boundary linetypes ('граница растительности') are lines, not vegetation
            if _re.search(r"границ", e.layer, _re.IGNORECASE):
                continue
            evidence.append(idx.by_entity[eid].centroid)

    if len(evidence) < 30:
        repair_log.append(
            "planting domain: no site boundary and little vegetation evidence — "
            "using drawing extents (review layer classification!)")
        return site, "extents_fallback"

    r = 12.0
    mask = unary_union([p.buffer(r, resolution=6) for p in evidence])
    mask = mask.buffer(-r * 0.45).buffer(r * 0.35).simplify(0.5)
    if site is not None:
        mask = mask.intersection(site)
    if not mask.is_valid:
        mask = mask.buffer(0)
        repair_log.append("repaired vegetation-evidence mask (buffer(0))")
    repair_log.append(
        f"planting domain derived from {len(evidence)} vegetation-evidence symbols "
        f"(existing trees/shrubs/green glyphs), r={r} m")
    return mask, "vegetation_evidence"


# ---------------------------------------------------------------------------
# Visibility triangles at road-road crossings (СП/местные правила — размеры
# ДЕМО, treated as an explicit assumption; see VISIBILITY_TRIANGLE_NO_TALL_PLANTS)
# ---------------------------------------------------------------------------

def _intersection_points(geom) -> list[Point]:
    if geom.is_empty:
        return []
    t = geom.geom_type
    if t == "Point":
        return [geom]
    if t in ("MultiPoint", "GeometryCollection"):
        out = []
        for g in geom.geoms:
            out.extend(_intersection_points(g))
        return out
    if t in ("LineString", "MultiLineString"):
        # overlapping collinear roads: use a representative point
        return [geom.representative_point()]
    return []


def _direction_at(line: LineString, p: Point) -> tuple[float, float] | None:
    """Unit direction of the line segment nearest to p (dominant local road
    direction at the crossing)."""
    coords = list(line.coords)
    best = None
    best_d = None
    for a, b in zip(coords[:-1], coords[1:]):
        seg = LineString([a, b])
        d = seg.distance(p)
        if best_d is None or d < best_d:
            best_d = d
            best = (a, b)
    if best is None:
        return None
    dx = best[1][0] - best[0][0]
    dy = best[1][1] - best[0][1]
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return None
    return dx / n, dy / n


def detect_road_crossings(idx: GeometryIndex,
                          min_len_m: float = _VIS_MIN_ROAD_LEN_M,
                          cluster_r_m: float = _VIS_CLUSTER_R_M,
                          cap: int = _VIS_MAX_CROSSINGS
                          ) -> list[tuple[Point, tuple, tuple]]:
    """Road-road crossing points with the two dominant road directions.

    Only ROAD-category LineStrings longer than `min_len_m` participate (skips
    exploded dash linetypes on topo maps); pairwise intersections via STRtree;
    points within `cluster_r_m` are one crossing; at most `cap` crossings."""
    lines: list[LineString] = []
    for eid in idx.by_category.get("ROAD", []):
        g = idx.by_entity[eid]
        if g.geom_type == "LineString" and g.length > min_len_m:
            lines.append(g)
    if len(lines) < 2:
        return []

    tree = STRtree(lines)
    raw: list[tuple[float, float, int, int]] = []
    for i, a in enumerate(lines):
        for j in tree.query(a):
            j = int(j)
            if j <= i:
                continue
            inter = a.intersection(lines[j])
            for p in _intersection_points(inter):
                raw.append((p.x, p.y, i, j))

    clusters: list[tuple[float, float, int, int]] = []
    r2 = cluster_r_m * cluster_r_m
    for x, y, i, j in raw:
        if any((cx - x) ** 2 + (cy - y) ** 2 < r2 for cx, cy, _, _ in clusters):
            continue
        clusters.append((x, y, i, j))
        if len(clusters) >= cap:
            break

    out = []
    for x, y, i, j in clusters:
        p = Point(x, y)
        da = _direction_at(lines[i], p)
        db = _direction_at(lines[j], p)
        if da is None or db is None:
            continue
        out.append((p, da, db))
    return out


def visibility_triangle_polygons(p: Point, da: tuple, db: tuple,
                                 long_m: float = _VIS_LONG_M,
                                 short_m: float = _VIS_SHORT_M) -> list[Polygon]:
    """The 4 sight triangles at a crossing: (P, P±dA·40, P±dB·8) and
    (P, P±dA·8, P±dB·40). Kept as individual triangles (their union is the
    exclusion shape — no convex hulls)."""
    def tri(sign: int, la: float, lb: float) -> Polygon:
        return Polygon([
            (p.x, p.y),
            (p.x + sign * da[0] * la, p.y + sign * da[1] * la),
            (p.x + sign * db[0] * lb, p.y + sign * db[1] * lb),
        ])

    tris = [tri(+1, long_m, short_m), tri(-1, long_m, short_m),
            tri(+1, short_m, long_m), tri(-1, short_m, long_m)]
    return [t for t in tris if t.is_valid and t.area > 1e-6]


def build_visibility_zones(idx: GeometryIndex, registry: RuleRegistry,
                           zone_seq_start: int) -> list[ExclusionZone]:
    """Visibility-triangle exclusion zones (one merged zone per crossing).

    Active only while the rule's assumption is enabled; every zone is labeled
    effectiveSource="assumption" — demo dimensions, never a verified norm."""
    rule = registry.get(VISIBILITY_RULE_ID)
    if rule is None or not rule.enabled:
        return []
    if rule.status != "verified" and not (rule.assumption and rule.assumption.enabled):
        return []
    zones: list[ExclusionZone] = []
    src = {"document": rule.source.document, "clause": rule.source.clause,
           "title": rule.source.title, "status": rule.status}
    seq = zone_seq_start
    for p, da, db in detect_road_crossings(idx):
        tris = visibility_triangle_polygons(p, da, db)
        if not tris:
            continue
        geom = unary_union(tris)
        if not geom.is_valid:
            geom = geom.buffer(0)
        seq += 1
        zones.append(ExclusionZone(
            id=f"excl_vis_{seq}", ruleId=VISIBILITY_RULE_ID,
            generatedFrom=f"road crossing at ({p.x:.1f}, {p.y:.1f})",
            sourceLayer="ROAD", obstacleCategory="ROAD", distanceM=0.0,
            effectiveSource="assumption", geometry=geom, source=src))
    return zones


def _obstacle_categories_present(rule_obstacles: list[str], idx: GeometryIndex) -> list[str]:
    cats = []
    for c in rule_obstacles:
        if c in ("PLANTING_TREE", "PLANTING_SHRUB", "SITE_BOUNDARY"):
            continue  # handled at validation/candidate stage, not as buffers
        if c == "UTILITY":
            # generic-utility rule applies only to *unclassified* utility layers;
            # specific utility rules cover the typed ones.
            if idx.by_category.get("UTILITY"):
                cats.append("UTILITY")
            continue
        if idx.by_category.get(c):
            cats.append(c)
    return cats


def build_constraints(doc: CadDocument, idx: GeometryIndex, registry: RuleRegistry,
                      planting_types: list[str] | None = None) -> ConstraintResult:
    planting_types = planting_types or ["TREE", "SHRUB"]
    repair_log = list(idx.repair_log)
    site, site_src = detect_site_boundary(doc, idx)

    zones: list[ExclusionZone] = []
    inactive: list[dict] = []
    zone_seq = 0

    seen_rules: set[str] = set()
    for ptype in planting_types:
        for rule in registry.rules_for(ptype, {"min_distance"}):
            if rule.id in seen_rules:
                continue
            seen_rules.add(rule.id)
            dist = rule.effective_distance_m
            if dist is None:
                reason = ("rule disabled" if not rule.enabled else
                          "missing_source_data — no verified value and assumption disabled")
                inactive.append({"ruleId": rule.id, "reason": reason})
                continue

            obstacle_cats = _obstacle_categories_present(rule.appliesTo.get("obstacle_type", []), idx)
            src = {
                "document": rule.source.document, "clause": rule.source.clause,
                "title": rule.source.title, "status": rule.status,
            }
            for cat in obstacle_cats:
                eids = idx.by_category.get(cat, [])
                if not eids:
                    continue
                if len(eids) > _MERGE_THRESHOLD:
                    # buffer per-entity at low arc resolution, then union:
                    # buffering an already-unioned complex geometry explodes
                    # memory on drawings with thousands of vegetation symbols
                    merged = unary_union([idx.by_entity[eid].buffer(dist, resolution=4)
                                          for eid in eids]).simplify(0.05)
                    zone_seq += 1
                    zones.append(ExclusionZone(
                        id=f"excl_{zone_seq}", ruleId=rule.id,
                        generatedFrom=f"{len(eids)} entities on {cat}",
                        sourceLayer=cat, obstacleCategory=cat, distanceM=dist,
                        effectiveSource=rule.effective_source, geometry=merged, source=src))
                else:
                    for eid in eids:
                        g = idx.by_entity[eid]
                        zone_seq += 1
                        zones.append(ExclusionZone(
                            id=f"excl_{zone_seq}", ruleId=rule.id, generatedFrom=eid,
                            sourceLayer=idx.entity_meta[eid].layer, obstacleCategory=cat,
                            distanceM=dist, effectiveSource=rule.effective_source,
                            geometry=g.buffer(dist, resolution=6), source=src))

    # visibility triangles at road-road crossings (assumption-labeled): join
    # exclusion zones so generation avoids them; the validator re-checks them
    vis_zones = build_visibility_zones(idx, registry, zone_seq)
    if vis_zones:
        zones.extend(vis_zones)
        repair_log.append(f"visibility triangles: {len(vis_zones)} road crossing(s), "
                          f"8×40 m demo dimensions (assumption)")

    # rules that never produced zones because they are inactive for other reasons
    for rule in registry.rules:
        if rule.id in seen_rules:
            continue
        ctype = rule.constraint.get("type")
        if ctype == "species_blacklist" and not rule.constraint.get("species"):
            inactive.append({"ruleId": rule.id,
                             "reason": "missing_source_data — invasive species list not provided; "
                                       "will auto-enforce once the verified list is added"})
        elif ctype in ("min_distance", "min_spacing") and rule.effective_distance_m is None:
            inactive.append({"ruleId": rule.id,
                             "reason": "rule disabled" if not rule.enabled else
                                       "missing_source_data — assumption disabled"})

    # allowed area = planting domain − object footprints − exclusion buffers
    domain, basis = detect_planting_domain(doc, idx, site, site_src, repair_log)
    subtract: list[BaseGeometry] = []
    for cat in FOOTPRINT_CATEGORIES:
        for eid in idx.by_category.get(cat, []):
            g = idx.by_entity[eid]
            if g.geom_type in ("Polygon", "MultiPolygon"):
                subtract.append(g)
            else:
                subtract.append(g.buffer(0.3))  # linear footprint gets nominal width
    subtract.extend(z.geometry for z in zones)

    allowed: BaseGeometry = domain if domain is not None else Polygon()
    if subtract and not allowed.is_empty:
        union = unary_union(subtract)
        if not union.is_valid:
            union = union.buffer(0)
            repair_log.append("repaired exclusion union (buffer(0))")
        allowed = allowed.difference(union)
        if not allowed.is_valid:
            allowed = allowed.buffer(0)
            repair_log.append("repaired allowed area (buffer(0))")
    # drop slivers narrower than ~1 m (low-res buffers + simplify keep memory sane)
    allowed = allowed.simplify(0.05).buffer(-0.5, resolution=4).buffer(0.5, resolution=4)
    if not allowed.is_valid:
        allowed = allowed.buffer(0)
        repair_log.append("repaired allowed area after sliver removal")

    if site_src == "detected":
        boundary_kind = "detected"
    elif basis == "vegetation_evidence":
        boundary_kind = "vegetation_evidence"
    else:
        boundary_kind = "inferred_drawing_extent"

    return ConstraintResult(
        site_boundary=site, site_boundary_source=site_src,
        exclusion_zones=zones, allowed_area=allowed,
        inactive_rules=inactive, repair_log=repair_log,
        planting_domain=domain, allowed_basis=basis,
        site_boundary_kind=boundary_kind,
    )
