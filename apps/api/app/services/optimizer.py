"""Placement optimizer.

Hard constraints filter candidates (a violation makes a candidate INVALID —
never a score penalty). Soft criteria shape the score. Greedy selection with
spacing enforcement + a light local-search improvement pass. Fully
deterministic for a given (seed, parameters).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point

from .candidates import Candidate
from .rules import RuleRegistry
from .validator import RuleDistanceOracle

STRATEGIES = ("balanced", "maximum_trees", "maximum_canopy", "street_greening")


@dataclass
class OptimizerParams:
    strategy: str = "balanced"
    candidateDensity: float = 1.0     # scales candidate sampling radius
    spacingWeight: float = 0.35       # prefer breathing room between plantings
    coverageWeight: float = 0.45      # prefer larger canopy contribution
    distributionWeight: float = 0.40  # prefer under-populated regions
    streetWeight: float = 0.15        # prefer road/sidewalk adjacency (street_greening)
    targetCount: int | None = None

    def clamped(self) -> "OptimizerParams":
        c = OptimizerParams(**self.__dict__)
        c.candidateDensity = min(3.0, max(0.3, c.candidateDensity))
        for f in ("spacingWeight", "coverageWeight", "distributionWeight", "streetWeight"):
            setattr(c, f, min(1.0, max(0.0, float(getattr(c, f)))))
        return c

    @staticmethod
    def for_strategy(strategy: str) -> "OptimizerParams":
        base = OptimizerParams(strategy=strategy)
        if strategy == "maximum_trees":
            base.candidateDensity = 1.6
            base.spacingWeight = 0.1
            base.coverageWeight = 0.3
        elif strategy == "maximum_canopy":
            base.coverageWeight = 0.9
            base.candidateDensity = 1.2
        elif strategy == "street_greening":
            base.streetWeight = 0.8
            base.distributionWeight = 0.25
        return base

    def to_api(self) -> dict:
        d = dict(self.__dict__)
        return d


@dataclass
class PlantSpec:
    speciesId: str
    kind: str
    crownRadiusM: float
    spacingM: float  # effective same-kind spacing from rules


def hard_filter_candidates(cands: list[Candidate], kind: str,
                           registry: RuleRegistry, oracle: RuleDistanceOracle) -> None:
    """Marks candidates invalid when any effective hard min_distance rule fails."""
    rules = [(r, r.effective_distance_m) for r in registry.rules_for(kind, {"min_distance"})
             if r.severity == "hard"]
    rules = [(r, d) for r, d in rules if d is not None]
    for c in cands:
        if not c.valid:
            continue
        pt = Point(c.x, c.y)
        for rule, d in rules:
            res = oracle.nearest(rule.appliesTo.get("obstacle_type", []), pt)
            if res is not None and res[0] < d - 1e-9:
                c.valid = False
                c.rejected_by = rule.id
                break


def _local_density_scorer(cands: list[Candidate], radius: float):
    """Returns f(x, y, chosen_points) → density penalty in [0,1]."""
    r2 = radius * radius

    def density(x: float, y: float, chosen: list[tuple[float, float]]) -> float:
        n = 0
        for cx, cy in chosen:
            if (cx - x) ** 2 + (cy - y) ** 2 < r2:
                n += 1
        return min(1.0, n / 6.0)
    return density


def score_candidate(c: Candidate, params: OptimizerParams, crown_r: float,
                    region_fill: dict, region_size: float) -> float:
    f = c.features
    s = 1.0
    # spacing quality: room to existing vegetation and boundary
    room = min(x for x in [f.get("existing_tree") or 50.0, f.get("boundary") or 50.0, 50.0])
    s += params.spacingWeight * min(1.0, room / 15.0)
    # canopy contribution
    s += params.coverageWeight * (crown_r / 5.0)
    # distribution: prefer under-filled regions
    key = (int(c.x // region_size), int(c.y // region_size))
    s += params.distributionWeight * (1.0 / (1.0 + region_fill.get(key, 0)))
    # street greening: reward proximity to roads/sidewalks (beyond legal setback)
    road = f.get("road")
    sidewalk = f.get("sidewalk")
    near_street = min(x for x in [road if road is not None else 1e9,
                                  sidewalk if sidewalk is not None else 1e9])
    if near_street < 1e8:
        s += params.streetWeight * max(0.0, 1.0 - near_street / 20.0)
    return s


def auto_target(allowed_area_m2: float, spacing_m: float, valid_count: int) -> int:
    """Default planting count when the user picks 'Auto': one plant per
    ~(2.5·spacing)² of allowed area, capped to keep the MVP interactive."""
    cell = max(25.0, (2.5 * spacing_m) ** 2)
    return max(1, min(valid_count, min(400, int(allowed_area_m2 / cell))))


def optimize(cands: list[Candidate], plant: PlantSpec, params: OptimizerParams,
             existing_points: list[tuple[float, float]], seed: int,
             region_size: float = 25.0, progress=None) -> list[Candidate]:
    """Greedy pick with spacing enforcement + local-search swap improvement."""
    params = params.clamped()
    valid = [c for c in cands if c.valid]
    if not valid:
        return []

    rng = np.random.RandomState(seed)
    target = params.targetCount if params.targetCount else len(valid)

    chosen: list[Candidate] = []
    chosen_pts: list[tuple[float, float]] = list(existing_points)
    region_fill: dict[tuple[int, int], int] = {}
    for x, y in existing_points:
        k = (int(x // region_size), int(y // region_size))
        region_fill[k] = region_fill.get(k, 0) + 1

    spacing2 = plant.spacingM ** 2

    # candidates violating spacing to pre-existing same-kind points (existing
    # vegetation and already-placed plantings of other species) are invalid picks
    if existing_points and valid:
        ex = np.asarray(existing_points, dtype=np.float64)
        pts_arr = np.asarray([(c.x, c.y) for c in valid], dtype=np.float64)
        keep = np.ones(len(valid), dtype=bool)
        for i in range(0, len(valid), 2000):
            chunk = pts_arr[i:i + 2000]
            d2 = ((chunk[:, None, :] - ex[None, :, :]) ** 2).sum(axis=2)
            keep[i:i + 2000] = (d2 >= spacing2).all(axis=1)
        valid = [c for c, k in zip(valid, keep) if k]
        if not valid:
            return []

    remaining = list(valid)

    while remaining and len(chosen) < target:
        # (re)score remaining against current fill state
        best, best_score = None, -1e18
        for c in remaining:
            sc = score_candidate(c, params, plant.crownRadiusM, region_fill, region_size)
            if sc > best_score:
                best, best_score = c, sc
        if best is None:
            break
        best.score = best_score
        chosen.append(best)
        chosen_pts.append((best.x, best.y))
        if progress and len(chosen) % 25 == 0:
            progress(len(chosen))
        k = (int(best.x // region_size), int(best.y // region_size))
        region_fill[k] = region_fill.get(k, 0) + 1
        # prune candidates violating same-kind spacing to the new pick
        bx, by = best.x, best.y
        remaining = [c for c in remaining
                     if c is not best and (c.x - bx) ** 2 + (c.y - by) ** 2 >= spacing2]

    # local search: try replacing weakest picks with unused candidates
    unused = [c for c in valid if c not in chosen]
    if unused and chosen and params.targetCount:
        chosen.sort(key=lambda c: c.score)
        for _ in range(min(30, len(chosen))):
            weak = chosen[0]
            alt = unused[int(rng.randint(len(unused)))]
            ok = all((alt.x - c.x) ** 2 + (alt.y - c.y) ** 2 >= spacing2
                     for c in chosen if c is not weak)
            if ok:
                alt_score = score_candidate(alt, params, plant.crownRadiusM, {}, region_size)
                if alt_score > weak.score:
                    chosen[0] = alt
                    alt.score = alt_score
            chosen.sort(key=lambda c: c.score)

    return chosen
