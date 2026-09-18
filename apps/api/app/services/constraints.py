"""Regulatory constraint engine: rules × CAD geometry → exclusion zones and
allowed planting area. Pure deterministic Shapely math.

Every exclusion zone records exactly which rule and which source object
generated it (provenance), and whether the distance is verified or an
explicitly flagged assumption.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..models.ir import CadDocument, UTILITY_CATEGORIES
from .geometry import GeometryIndex, as_polygon_list, detect_site_boundary, polygon_to_rings
from .rules import RuleRegistry

# Object categories whose own footprint is unplantable regardless of buffers.
FOOTPRINT_CATEGORIES = {"BUILDING", "ROAD", "SIDEWALK", "PARKING", "CURB"}

# Max number of per-entity zones per rule before merging (keeps API payload sane).
_MERGE_THRESHOLD = 150


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

    def to_api(self) -> dict:
        site = polygon_to_rings(self.site_boundary) if self.site_boundary else []
        allowed = polygon_to_rings(self.allowed_area)
        area = sum(p.area for p in as_polygon_list(self.allowed_area))
        return {
            "siteBoundary": [r["shell"] for r in site] or None,
            "siteBoundarySource": self.site_boundary_source,
            "exclusionZones": [z.to_api() for z in self.exclusion_zones],
            "allowedArea": {"polygons": allowed, "areaM2": round(area, 1)},
            "repairLog": self.repair_log,
            "inactiveRules": self.inactive_rules,
        }


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
                    merged = unary_union([idx.by_entity[eid] for eid in eids]).buffer(dist)
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
                            geometry=g.buffer(dist), source=src))

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

    # allowed area = site − object footprints − exclusion buffers
    subtract: list[BaseGeometry] = []
    for cat in FOOTPRINT_CATEGORIES:
        for eid in idx.by_category.get(cat, []):
            g = idx.by_entity[eid]
            if g.geom_type in ("Polygon", "MultiPolygon"):
                subtract.append(g)
            else:
                subtract.append(g.buffer(0.3))  # linear footprint gets nominal width
    subtract.extend(z.geometry for z in zones)

    allowed: BaseGeometry = site if site else Polygon()
    if subtract and site:
        union = unary_union(subtract)
        if not union.is_valid:
            union = union.buffer(0)
            repair_log.append("repaired exclusion union (buffer(0))")
        allowed = site.difference(union)
        if not allowed.is_valid:
            allowed = allowed.buffer(0)
            repair_log.append("repaired allowed area (buffer(0))")
    # drop slivers narrower than ~1 m
    allowed = allowed.buffer(-0.5).buffer(0.5)
    if not allowed.is_valid:
        allowed = allowed.buffer(0)
        repair_log.append("repaired allowed area after sliver removal")

    return ConstraintResult(
        site_boundary=site, site_boundary_source=site_src,
        exclusion_zones=zones, allowed_area=allowed,
        inactive_rules=inactive, repair_log=repair_log,
    )
