"""Deterministic plan validator — the FINAL authority on compliance.

Recomputes every hard constraint from precise geometry (never from simplified
render geometry, never from LLM output). The LLM judge can never override a
hard violation reported here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import Point
from shapely.prepared import prep
from shapely.strtree import STRtree

from .geometry import GeometryIndex, as_polygon_list
from .rules import RuleRegistry


@dataclass
class Placement:
    id: str
    kind: str            # TREE | SHRUB
    speciesId: str
    x: float
    y: float
    crownRadiusM: float
    score: float = 0.0
    distances: dict = field(default_factory=dict)

    def to_api(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "speciesId": self.speciesId,
            "position": [round(self.x, 3), round(self.y, 3)],
            "crownRadiusM": self.crownRadiusM, "score": round(self.score, 4),
            "distances": {k: (round(v, 2) if isinstance(v, (int, float)) else None)
                          for k, v in self.distances.items()},
        }


class RuleDistanceOracle:
    """Per-rule exact nearest-distance queries over obstacle categories."""

    def __init__(self, idx: GeometryIndex):
        self.idx = idx
        self._cache: dict[frozenset, tuple[STRtree, list, list[str]] | None] = {}

    def _tree_for(self, cats: frozenset):
        if cats not in self._cache:
            pairs = self.idx.geoms_for(list(cats))
            if not pairs:
                self._cache[cats] = None
            else:
                ids = [eid for eid, _ in pairs]
                geoms = [g for _, g in pairs]
                self._cache[cats] = (STRtree(geoms), geoms, ids)
        return self._cache[cats]

    def nearest(self, cats: list[str], p: Point) -> tuple[float, str] | None:
        entry = self._tree_for(frozenset(c for c in cats
                                         if c not in ("PLANTING_TREE", "PLANTING_SHRUB", "SITE_BOUNDARY")))
        if entry is None:
            return None
        tree, geoms, ids = entry
        i = int(tree.nearest(p))
        return float(p.distance(geoms[i])), ids[i]


def _spacing_violations(placements: list[Placement], kind: str, min_d: float,
                        rule_id: str) -> list[dict]:
    pts = [(p.x, p.y, p.id) for p in placements if p.kind == kind]
    out = []
    # O(n²) is fine at MVP scale (≤ a few hundred); switch to cKDTree if needed
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.dist(pts[i][:2], pts[j][:2])
            if d < min_d - 1e-9:
                out.append({"plantingId": pts[j][2], "ruleId": rule_id,
                            "actual": round(d, 3), "requiredM": min_d,
                            "obstacle": f"planting {pts[i][2]}"})
    return out


def compute_metrics(placements: list[Placement], allowed_area, valid_count: int) -> dict:
    polys = as_polygon_list(allowed_area)
    area = sum(p.area for p in polys) or 1.0
    canopy = sum(math.pi * p.crownRadiusM ** 2 for p in placements)
    coverage = min(1.0, canopy / area)

    # distribution: fraction of occupied 20 m cells among cells intersecting allowed area
    distribution = 0.0
    if polys and placements:
        minx = min(p.bounds[0] for p in polys); miny = min(p.bounds[1] for p in polys)
        maxx = max(p.bounds[2] for p in polys); maxy = max(p.bounds[3] for p in polys)
        cell = max(10.0, min(30.0, math.sqrt(area) / 6))
        nx = max(1, int((maxx - minx) / cell) + 1)
        ny = max(1, int((maxy - miny) / cell) + 1)
        from shapely.geometry import box
        total = occupied = 0
        occupied_cells = set()
        for p in placements:
            occupied_cells.add((int((p.x - minx) / cell), int((p.y - miny) / cell)))
        for i in range(nx):
            for j in range(ny):
                b = box(minx + i * cell, miny + j * cell,
                        minx + (i + 1) * cell, miny + (j + 1) * cell)
                if any(pl.intersects(b) for pl in polys):
                    total += 1
                    if (i, j) in occupied_cells:
                        occupied += 1
        distribution = occupied / total if total else 0.0

    return {
        "plantingCount": len(placements),
        "treeCount": sum(1 for p in placements if p.kind == "TREE"),
        "shrubCount": sum(1 for p in placements if p.kind == "SHRUB"),
        "validCount": valid_count,
        "coverageScore": round(coverage, 4),
        "distributionScore": round(distribution, 4),
        "canopyAreaM2": round(canopy, 1),
    }


def validate_plan(placements: list[Placement], idx: GeometryIndex,
                  registry: RuleRegistry, site_boundary, allowed_area) -> dict:
    oracle = RuleDistanceOracle(idx)
    hard: list[dict] = []
    warnings: list[str] = []
    prepared_site = prep(site_boundary) if site_boundary is not None else None

    for p in placements:
        pt = Point(p.x, p.y)
        for rule in registry.rules_for(p.kind, {"min_distance"}):
            d = rule.effective_distance_m
            if d is None:
                continue
            res = oracle.nearest(rule.appliesTo.get("obstacle_type", []), pt)
            if res is None:
                continue
            dist, obstacle_id = res
            if dist < d - 1e-9:
                hard.append({"plantingId": p.id, "ruleId": rule.id,
                             "actual": round(dist, 3), "requiredM": d,
                             "obstacle": obstacle_id})
        for rule in registry.rules_for(p.kind, {"inside"}):
            if rule.effective_distance_m is None and rule.status != "verified":
                continue
            if prepared_site is not None and not prepared_site.covers(pt):
                hard.append({"plantingId": p.id, "ruleId": rule.id,
                             "actual": 0.0, "requiredM": 0.0,
                             "obstacle": "site_boundary"})

    for kind in ("TREE", "SHRUB"):
        for rule in registry.rules_for(kind, {"min_spacing"}):
            d = rule.effective_distance_m
            if d is not None:
                hard.extend(_spacing_violations(placements, kind, d, rule.id))

    violating_ids = {v["plantingId"] for v in hard}
    valid_count = len(placements) - len(violating_ids)
    metrics = compute_metrics(placements, allowed_area, valid_count)
    metrics["hardViolations"] = len(hard)

    return {
        "valid": not hard,
        "hardViolations": hard,
        "warnings": warnings,
        "metrics": metrics,
    }
