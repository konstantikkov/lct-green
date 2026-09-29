"""Why each rejected candidate point was rejected — with the norm behind it.

ТЗ п. 8: "если посадка запрещена или отклонена алгоритмом вблизи
коммуникаций / охранных зон — также фиксируется причина со ссылкой на норму".

A candidate is rejected when it lies outside the allowed area or breaks a
hard rule in the candidate filter. Here every rejected point gets ALL the
reasons that apply to it, in this order:

  OUTSIDE_SITE          outside the site boundary (PLANTING_INSIDE_SITE_BOUNDARY)
  NOT_PLANTING_AREA     inside the site, outside the planting domain (e.g. not
                        a project lawn of the cover plan)
  ON_OBJECT:<category>  on a building / road / sidewalk / parking / curb
  <ruleId>              inside that rule's exclusion zone (setback), or
                        rejected by it in the candidate filter (species rules)
  NARROW_STRIP          none of the above: a sliver narrower than ~1 m that
                        the allowed area drops

The first reason is the primary one (used for the per-reason counts); rules
are ordered verified regulatory norms first, then by required distance.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.strtree import STRtree

from .constraints import FOOTPRINT_CATEGORIES, ConstraintResult
from .geometry import GeometryIndex
from .rules import RuleRegistry
from .validator import RuleDistanceOracle

OUTSIDE_SITE = "OUTSIDE_SITE"
NOT_PLANTING_AREA = "NOT_PLANTING_AREA"
ON_OBJECT = "ON_OBJECT"
NARROW_STRIP = "NARROW_STRIP"
FOOTPRINT_TOL_M = 0.3   # linear footprints (curbs, road edges) get this half-width
MAX_SAMPLES = 1500


@dataclass
class Rejected:
    x: float
    y: float
    species_id: str
    kind: str
    rejected_by: str | None


def _rule_order(registry: RuleRegistry):
    def key(rule_id: str):
        r = registry.get(rule_id)
        if r is None:
            return (3, 0.0, rule_id)
        verified = r.effective_source == "verified" and r.role == "regulatory"
        return (0 if verified else 1 if r.role == "regulatory" else 2,
                -(r.effective_distance_m or 0.0), rule_id)
    return key


def explain_rejections(rejected: list[Rejected], constraints: ConstraintResult,
                       registry: RuleRegistry, idx: GeometryIndex,
                       max_samples: int = MAX_SAMPLES) -> dict:
    n = len(rejected)
    if n == 0:
        return {"total": 0, "byReason": [], "samples": []}
    xs = np.fromiter((r.x for r in rejected), float, n)
    ys = np.fromiter((r.y for r in rejected), float, n)
    pts = shapely.points(xs, ys)

    site = constraints.site_boundary
    domain = constraints.planting_domain if constraints.planting_domain is not None else site
    in_site = shapely.contains_xy(site, xs, ys) if site is not None else np.ones(n, bool)
    in_domain = shapely.contains_xy(domain, xs, ys) if domain is not None else np.ones(n, bool)

    # object footprints
    on_object: dict[int, str] = {}
    fp_geoms, fp_cats = [], []
    for cat in sorted(FOOTPRINT_CATEGORIES):
        for _eid, g in idx.geoms_for([cat]):
            fp_geoms.append(g)
            fp_cats.append(cat)
    if fp_geoms:
        pi, gi = STRtree(fp_geoms).query(pts, predicate="dwithin", distance=FOOTPRINT_TOL_M)
        for p, g in zip(pi, gi):
            on_object.setdefault(int(p), fp_cats[int(g)])

    # exclusion zones of rules that apply to the point's planting kind
    zones = constraints.exclusion_zones
    rules_at: dict[int, set[str]] = defaultdict(set)
    if zones:
        pi, zi = STRtree([z.geometry for z in zones]).query(pts, predicate="within")
        kinds_of = {}
        for p, z in zip(pi, zi):
            rid = zones[int(z)].ruleId
            if rid not in kinds_of:
                r = registry.get(rid)
                kinds_of[rid] = set(r.appliesTo.get("planting_type", [])) if r else set()
            if rejected[int(p)].kind in kinds_of[rid]:
                rules_at[int(p)].add(rid)
    for i, r in enumerate(rejected):
        if r.rejected_by and r.rejected_by != "OUTSIDE_ALLOWED_AREA":
            rules_at[i].add(r.rejected_by)

    order = _rule_order(registry)
    reasons: list[list[str]] = []
    for i in range(n):
        if not in_site[i]:
            rs = [OUTSIDE_SITE]
        elif not in_domain[i]:
            rs = [NOT_PLANTING_AREA]
        else:
            rs = []
            if i in on_object:
                rs.append(f"{ON_OBJECT}:{on_object[i]}")
            rs.extend(sorted(rules_at.get(i, ()), key=order))
            if not rs:
                rs = [NARROW_STRIP]
        reasons.append(rs)

    primary = Counter()
    primary_kind: dict[str, Counter] = defaultdict(Counter)
    involved = Counter()
    for i, rs in enumerate(reasons):
        primary[rs[0]] += 1
        primary_kind[rs[0]][rejected[i].kind] += 1
        for key in rs:
            involved[key] += 1
    by_reason = [{"key": k, "count": c, "byKind": dict(primary_kind[k]), "involved": involved[k]}
                 for k, c in primary.most_common()]
    # rules that block points only as a secondary reason still get listed
    for k, c in involved.most_common():
        if k not in primary:
            by_reason.append({"key": k, "count": 0, "byKind": {}, "involved": c})

    # evenly spaced samples, with the measured distance for the primary rule
    oracle = RuleDistanceOracle(idx)
    step = max(1, n // max_samples)
    samples = []
    for i in range(0, n, step):
        r, rs = rejected[i], reasons[i]
        item = {"position": [round(r.x, 2), round(r.y, 2)], "speciesId": r.species_id,
                "kind": r.kind, "primary": rs[0], "reasons": rs}
        rule = registry.get(rs[0])
        if rule is not None and rule.effective_distance_m is not None:
            hit = oracle.nearest(rule.appliesTo.get("obstacle_type", []), shapely.Point(r.x, r.y))
            if hit is not None:
                item["actualM"] = round(hit[0], 2)
                item["requiredM"] = rule.effective_distance_m
        samples.append(item)
        if len(samples) >= max_samples:
            break
    return {"total": n, "byReason": by_reason, "samples": samples}


def cite(registry: RuleRegistry, key: str) -> dict | None:
    """Norm citation for a reason key (None for geometric reasons)."""
    rule = registry.get(key)
    if rule is None:
        return None
    return {"document": rule.source.document, "clause": rule.source.clause,
            "title": rule.source.title, "status": rule.status, "role": rule.role,
            "effectiveSource": rule.effective_source,
            "requiredM": rule.effective_distance_m,
            "alsoCited": [{"document": c.document, "clause": c.clause} for c in rule.alsoCited]}
