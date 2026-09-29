"""Candidate generation + per-candidate feature calculation.

Deterministic: Poisson-disc (Bridson) sampling driven by a seeded NumPy RNG,
constrained to the allowed planting area. Features are exact distances computed
with Shapely STRtree spatial indexes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep
from shapely.strtree import STRtree

from ..models.ir import UTILITY_CATEGORIES
from .geometry import GeometryIndex

FEATURE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "building": ("BUILDING",),
    "road": ("ROAD", "CURB"),
    "sidewalk": ("SIDEWALK",),
    "utility": tuple(UTILITY_CATEGORIES),
    "existing_tree": ("EXISTING_TREE",),
    "existing_shrub": ("EXISTING_SHRUB",),
    "lighting": ("LIGHTING",),
    "fence": ("FENCE",),
    "access_route": ("ACCESS_ROUTE",),
}


@dataclass
class Candidate:
    id: int
    x: float
    y: float
    features: dict[str, float | None] = field(default_factory=dict)
    valid: bool = True
    score: float = 0.0
    rejected_by: str | None = None


class DistanceOracle:
    """Exact nearest-distance queries per semantic category."""

    def __init__(self, idx: GeometryIndex, site_boundary):
        self._trees: dict[str, STRtree] = {}
        self._geoms: dict[str, list[BaseGeometry]] = {}
        for feat, cats in FEATURE_CATEGORIES.items():
            gs = [g for _, g in idx.geoms_for(list(cats))]
            if gs:
                self._geoms[feat] = gs
                self._trees[feat] = STRtree(gs)
        self.site_boundary = site_boundary
        # .boundary works for a Polygon and for a MultiPolygon site (a work
        # boundary made of several separate areas); computed once
        self._site_edge = site_boundary.boundary if site_boundary is not None else None

    def distance(self, feat: str, p: Point) -> float | None:
        tree = self._trees.get(feat)
        if tree is None:
            return None
        i = tree.nearest(p)
        return float(p.distance(self._geoms[feat][int(i)]))

    def boundary_distance(self, p: Point) -> float | None:
        if self._site_edge is None:
            return None
        return float(p.distance(self._site_edge))

    def features_for(self, p: Point) -> dict[str, float | None]:
        out: dict[str, float | None] = {f: self.distance(f, p) for f in FEATURE_CATEGORIES}
        out["boundary"] = self.boundary_distance(p)
        return out


def poisson_disc_candidates(allowed: BaseGeometry, min_r: float, seed: int,
                            max_points: int = 20000) -> list[tuple[float, float]]:
    """Bridson Poisson-disc sampling inside `allowed` (deterministic by seed)."""
    if allowed.is_empty:
        return []
    rng = np.random.RandomState(seed)
    minx, miny, maxx, maxy = allowed.bounds
    w, h = maxx - minx, maxy - miny
    if w <= 0 or h <= 0:
        return []
    cell = min_r / math.sqrt(2)
    gw, gh = int(w / cell) + 1, int(h / cell) + 1
    grid = -np.ones((gw, gh), dtype=np.int64)
    prepared = prep(allowed)

    pts: list[tuple[float, float]] = []
    active: list[int] = []

    def grid_pos(x, y):
        return int((x - minx) / cell), int((y - miny) / cell)

    def fits(x, y):
        gx, gy = grid_pos(x, y)
        if not (0 <= gx < gw and 0 <= gy < gh):
            return False
        for i in range(max(0, gx - 2), min(gw, gx + 3)):
            for j in range(max(0, gy - 2), min(gh, gy + 3)):
                k = grid[i, j]
                if k >= 0:
                    px, py = pts[k]
                    if (px - x) ** 2 + (py - y) ** 2 < min_r * min_r:
                        return False
        return True

    def add(x, y):
        pts.append((x, y))
        gx, gy = grid_pos(x, y)
        grid[gx, gy] = len(pts) - 1
        active.append(len(pts) - 1)

    # deterministic seeding: try grid-ordered starts until one lands inside
    started = False
    for _ in range(400):
        x = minx + rng.random_sample() * w
        y = miny + rng.random_sample() * h
        if prepared.contains(Point(x, y)):
            add(x, y)
            started = True
            break
    if not started:
        return []

    K = 20
    while active and len(pts) < max_points:
        a_idx = int(rng.randint(len(active)))
        base = pts[active[a_idx]]
        found = False
        for _ in range(K):
            ang = rng.random_sample() * 2 * math.pi
            rad = min_r * (1 + rng.random_sample())
            x, y = base[0] + rad * math.cos(ang), base[1] + rad * math.sin(ang)
            if fits(x, y) and prepared.contains(Point(x, y)):
                add(x, y)
                found = True
                break
        if not found:
            active.pop(a_idx)
    return pts


def grid_candidates(allowed: BaseGeometry, step: float) -> list[tuple[float, float]]:
    if allowed.is_empty:
        return []
    prepared = prep(allowed)
    minx, miny, maxx, maxy = allowed.bounds
    out = []
    y = miny + step / 2
    row = 0
    while y < maxy:
        offset = (step / 2) if row % 2 else 0.0  # hex-ish stagger
        x = minx + step / 2 + offset
        while x < maxx:
            if prepared.contains(Point(x, y)):
                out.append((round(x, 3), round(y, 3)))
            x += step
        y += step * 0.866
        row += 1
    return out


def generate_candidates(allowed: BaseGeometry, oracle: DistanceOracle,
                        spacing_m: float, seed: int, density: float = 1.0,
                        method: str = "poisson") -> list[Candidate]:
    """density scales sampling radius (higher density → more candidates)."""
    r = max(0.5, spacing_m / max(0.25, density))
    if method == "grid":
        raw = grid_candidates(allowed, r)
    else:
        raw = poisson_disc_candidates(allowed, r, seed)
    cands = []
    for i, (x, y) in enumerate(raw):
        c = Candidate(id=i, x=x, y=y)
        c.features = oracle.features_for(Point(x, y))
        cands.append(c)
    return cands
