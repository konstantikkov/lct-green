"""Deterministic plan validator — the FINAL authority on compliance.

Recomputes every hard constraint from precise geometry (never from simplified
render geometry, never from LLM output). The LLM judge can never override a
hard violation reported here, and it can never *grant* regulatory validity:
the `regulatory` verdict computed here is deterministic and separate from the
judge's ACCEPT/RERUN decision (which only means "further optimization
iteration useful?").

Per-check statuses (frozen contract, docs/API_CONTRACT.md):
  PASS            geometry satisfied against a VERIFIED regulatory-role rule
                  whose applicability conditions are confirmed
  FAIL            geometry violates an enforced non-heuristic rule (verified
                  OR assumed — safe direction; still repairs via hardViolations)
  UNVERIFIED      geometry satisfied but the value is an ASSUMED demo value,
                  the rule is a safe_buffer / design heuristic, an
                  applicability condition is unconfirmed (e.g. species crown
                  diameter exceeds condition.crown_diameter_max_m of СП 42
                  табл. 9.1), or the inside-site check runs against a
                  non-detected (inferred) boundary
  NOT_APPLICABLE  no obstacles of the rule's type in the drawing

Counting convention (documented choice): checkCounts and regulatoryCoverage
are counted per rule × placement pair — every enforced rule contributes one
check per placement it applies to. regulatoryCoverage = (applicable hard
checks that are verified-passable, i.e. value verified + role regulatory +
conditions confirmed) / (all applicable hard checks). design_heuristic rules
never enter hardViolations or the hard-coverage math; their geometric
violations are surfaced as structured `warnings`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import Point
from shapely.prepared import prep
from shapely.strtree import STRtree

from ..models.ir import UTILITY_CATEGORIES
from .geometry import GeometryIndex, as_polygon_list
from .rules import RuleRegistry

VISIBILITY_RULE_ID = "VISIBILITY_TRIANGLE_NO_TALL_PLANTS"

# regulatory verdict values (deterministic; the judge can never change them)
COMPLIANT = "COMPLIANT"
NON_COMPLIANT = "NON_COMPLIANT"
NEEDS_REVIEW = "NEEDS_REVIEW"


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


def condition_confirmed(rule, placement: Placement) -> bool:
    """True when every applicability condition of the rule is confirmed for
    this placement's species. Unknown condition keys are treated as
    UNCONFIRMED (safe direction)."""
    for key, value in (rule.condition or {}).items():
        if key == "crown_diameter_max_m":
            if 2.0 * float(placement.crownRadiusM) > float(value) + 1e-9:
                return False
        else:
            return False  # unknown condition — cannot confirm applicability
    return True


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


class _CheckTally:
    """Per rule×placement check accounting for the regulatory verdict."""

    def __init__(self):
        self.pass_ = 0
        self.fail = 0
        self.unverified = 0
        self.not_applicable = 0
        # hard (non-heuristic) checks only:
        self.hard_applicable = 0
        self.hard_verified_passable = 0
        self.unverified_hard_rule_ids: set[str] = set()

    def record(self, status: str, *, hard: bool, rule_id: str,
               verified_passable: bool) -> None:
        if status == "PASS":
            self.pass_ += 1
        elif status == "FAIL":
            self.fail += 1
        elif status == "UNVERIFIED":
            self.unverified += 1
            if hard:
                self.unverified_hard_rule_ids.add(rule_id)
        else:
            self.not_applicable += 1
        if hard and status != "NOT_APPLICABLE":
            self.hard_applicable += 1
            if verified_passable:
                self.hard_verified_passable += 1


def _parser_coverage(idx: GeometryIndex, site_boundary_source: str,
                     crossings_detected: int) -> dict:
    typed = sum(len(idx.by_category.get(c, []))
                for c in UTILITY_CATEGORIES if c != "UTILITY")
    untyped = len(idx.by_category.get("UTILITY", []))
    total_util = typed + untyped
    return {
        "siteBoundaryDetected": site_boundary_source == "detected",
        "utilitiesTypedShare": (round(typed / total_util, 3) if total_util else None),
        "roadsClassified": bool(idx.by_category.get("ROAD")),
        "existingVegetationDetected": bool(idx.by_category.get("EXISTING_TREE")
                                           or idx.by_category.get("EXISTING_SHRUB")
                                           or idx.by_category.get("GREEN_AREA")),
        "crossingsDetected": int(crossings_detected),
    }


def validate_plan(placements: list[Placement], idx: GeometryIndex,
                  registry: RuleRegistry, site_boundary, allowed_area,
                  site_boundary_source: str = "detected",
                  visibility_zones: list | None = None) -> dict:
    """Validates a plan and computes the deterministic regulatory verdict.

    `site_boundary_source` — "detected" when a real closed boundary was found
    on a boundary layer; anything else ("inferred_drawing_extent",
    "vegetation_evidence", legacy "extents_fallback") means the inside-site
    check can only be UNVERIFIED and the SITE_BOUNDARY_UNKNOWN blocker is set.

    `visibility_zones` — visibility-triangle exclusion zones from the
    constraint engine (objects/dicts with a shapely `geometry`); a placement
    inside one is a FAIL-as-assumed hard violation (catalog plants are taller
    than 0.5 m by definition).

    See the module docstring for the per rule×placement counting convention.
    """
    oracle = RuleDistanceOracle(idx)
    hard: list[dict] = []
    warnings: list[dict] = []
    tally = _CheckTally()
    prepared_site = prep(site_boundary) if site_boundary is not None else None
    boundary_detected = site_boundary_source == "detected"
    visibility_zones = visibility_zones or []
    vis_prepared = []
    for z in visibility_zones:
        g = getattr(z, "geometry", None) or (z.get("geometry") if isinstance(z, dict) else None)
        if g is not None:
            vis_prepared.append((getattr(z, "id", None) or "visibility_zone", prep(g), g))

    inside_rule = next((r for r in registry.rules
                        if r.constraint.get("type") == "inside" and r.enabled), None)
    vis_rule = registry.get(VISIBILITY_RULE_ID)
    vis_enforced = bool(vis_rule and vis_rule.enabled and
                        (vis_rule.status == "verified" or
                         (vis_rule.assumption and vis_rule.assumption.enabled)))

    for p in placements:
        pt = Point(p.x, p.y)

        # --- min_distance rules (regulatory + safe_buffer roles) ------------
        for rule in registry.rules_for(p.kind, {"min_distance"}):
            if rule.role == "design_heuristic":
                continue  # handled with the heuristics below
            d = rule.effective_distance_m
            if d is None:
                continue
            hard_rule = rule.severity == "hard"
            res = oracle.nearest(rule.appliesTo.get("obstacle_type", []), pt)
            if res is None:
                tally.record("NOT_APPLICABLE", hard=hard_rule, rule_id=rule.id,
                             verified_passable=False)
                continue
            cond_ok = condition_confirmed(rule, p)
            verified_passable = (rule.effective_source == "verified"
                                 and rule.role == "regulatory" and cond_ok)
            dist, obstacle_id = res
            if dist < d - 1e-9:
                # safe direction: assumed and safe-buffer values still repair
                status = "FAIL" if hard_rule else "UNVERIFIED"
                if hard_rule:
                    hard.append({"plantingId": p.id, "ruleId": rule.id,
                                 "actual": round(dist, 3), "requiredM": d,
                                 "obstacle": obstacle_id})
                else:
                    warnings.append({"plantingId": p.id, "ruleId": rule.id,
                                     "actual": round(dist, 3), "requiredM": d,
                                     "kind": "recommended"})
            else:
                status = "PASS" if verified_passable else "UNVERIFIED"
            tally.record(status, hard=hard_rule, rule_id=rule.id,
                         verified_passable=verified_passable)

        # --- inside-site check ----------------------------------------------
        if inside_rule is not None:
            if prepared_site is None:
                tally.record("NOT_APPLICABLE", hard=True, rule_id=inside_rule.id,
                             verified_passable=False)
            elif not prepared_site.covers(pt):
                hard.append({"plantingId": p.id, "ruleId": inside_rule.id,
                             "actual": 0.0, "requiredM": 0.0,
                             "obstacle": "site_boundary"})
                tally.record("FAIL", hard=True, rule_id=inside_rule.id,
                             verified_passable=boundary_detected)
            else:
                status = "PASS" if boundary_detected else "UNVERIFIED"
                tally.record(status, hard=True, rule_id=inside_rule.id,
                             verified_passable=boundary_detected)

        # --- visibility triangles (assumed demo geometry — never PASS) ------
        if vis_enforced and p.kind in vis_rule.appliesTo.get("planting_type", []):
            if not vis_prepared:
                tally.record("NOT_APPLICABLE", hard=True, rule_id=VISIBILITY_RULE_ID,
                             verified_passable=False)
            else:
                inside_zone = next((zid for zid, pz, _ in vis_prepared
                                    if pz.covers(pt)), None)
                if inside_zone is not None:
                    hard.append({"plantingId": p.id, "ruleId": VISIBILITY_RULE_ID,
                                 "actual": 0.0, "requiredM": 0.0,
                                 "obstacle": f"visibility_triangle {inside_zone}"})
                    tally.record("FAIL", hard=True, rule_id=VISIBILITY_RULE_ID,
                                 verified_passable=False)
                else:
                    tally.record("UNVERIFIED", hard=True, rule_id=VISIBILITY_RULE_ID,
                                 verified_passable=False)

    # --- spacing rules ---------------------------------------------------
    for kind in ("TREE", "SHRUB"):
        kind_count = sum(1 for p in placements if p.kind == kind)
        for rule in registry.rules_for(kind, {"min_spacing"}):
            d = rule.effective_distance_m
            if d is None:
                continue
            if rule.role == "design_heuristic":
                # heuristics: violations become structured warnings, NEVER
                # hardViolations; checks count as UNVERIFIED (value/source
                # not established), outside the hard-coverage math
                for v in _spacing_violations(placements, kind, d, rule.id):
                    warnings.append({**v, "kind": "design_heuristic"})
                    warnings[-1].pop("obstacle", None)
                for _ in range(kind_count):
                    tally.record("UNVERIFIED" if kind_count > 1 else "NOT_APPLICABLE",
                                 hard=False, rule_id=rule.id, verified_passable=False)
            else:
                viols = _spacing_violations(placements, kind, d, rule.id)
                if rule.severity == "hard":
                    hard.extend(viols)
                else:
                    warnings.extend({**v, "kind": "recommended"} for v in viols)
                bad_ids = {v["plantingId"] for v in viols}
                for p in placements:
                    if p.kind != kind:
                        continue
                    if kind_count <= 1:
                        status = "NOT_APPLICABLE"
                    elif p.id in bad_ids:
                        status = "FAIL" if rule.severity == "hard" else "UNVERIFIED"
                    else:
                        status = ("PASS" if rule.effective_source == "verified"
                                  and rule.role == "regulatory" else "UNVERIFIED")
                    tally.record(status, hard=rule.severity == "hard", rule_id=rule.id,
                                 verified_passable=rule.effective_source == "verified"
                                 and rule.role == "regulatory")

    violating_ids = {v["plantingId"] for v in hard}
    valid_count = len(placements) - len(violating_ids)
    metrics = compute_metrics(placements, allowed_area, valid_count)
    metrics["hardViolations"] = len(hard)

    # --- deterministic regulatory verdict ---------------------------------
    blockers: list[str] = []
    if not boundary_detected:
        blockers.append("SITE_BOUNDARY_UNKNOWN")
    if tally.unverified_hard_rule_ids:
        blockers.append(f"UNVERIFIED_HARD_RULES:{len(tally.unverified_hard_rule_ids)}")

    if tally.fail:
        status = NON_COMPLIANT
    elif tally.unverified_hard_rule_ids or blockers:
        status = NEEDS_REVIEW
    else:
        status = COMPLIANT

    coverage = (tally.hard_verified_passable / tally.hard_applicable
                if tally.hard_applicable else 1.0)
    regulatory = {
        "status": status,
        "blockers": blockers,
        "coverage": {
            "regulatoryCoverage": round(coverage, 4),
            "parser": _parser_coverage(idx, site_boundary_source,
                                       len(visibility_zones)),
        },
        "checkCounts": {"pass": tally.pass_, "fail": tally.fail,
                        "unverified": tally.unverified,
                        "notApplicable": tally.not_applicable},
    }

    return {
        "valid": not hard,
        "hardViolations": hard,
        "warnings": warnings,
        "metrics": metrics,
        "regulatory": regulatory,
    }
