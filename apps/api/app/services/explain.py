"""Explainability engine.

All facts (distances, rule results, sources) are computed deterministically.
An optional local-LLM pass may rephrase facts as natural language but can
never add documents, clauses or numbers — the narrative is generated from the
already-verified check list only, and is clearly labeled.
"""
from __future__ import annotations

from shapely.geometry import Point

from .rules import RuleRegistry
from .validator import Placement, RuleDistanceOracle


def build_explanation(p: Placement, iteration: int, registry: RuleRegistry,
                      oracle: RuleDistanceOracle, site_boundary) -> dict:
    pt = Point(p.x, p.y)
    checks = []
    all_passed = True
    has_assumed = False

    for rule in registry.rules_for(p.kind, {"min_distance"}):
        d = rule.effective_distance_m
        src = {"document": rule.source.document, "clause": rule.source.clause,
               "title": rule.source.title, "status": rule.status}
        if d is None:
            continue
        res = oracle.nearest(rule.appliesTo.get("obstacle_type", []), pt)
        if res is None:
            checks.append({"ruleId": rule.id, "result": "n/a", "actualM": None,
                           "requiredM": d, "comparator": ">=",
                           "obstacleCategory": ",".join(rule.appliesTo.get("obstacle_type", [])),
                           "effectiveSource": rule.effective_source, "source": src,
                           "note": "no obstacles of this type in the drawing"})
            continue
        dist, _ = res
        passed = dist >= d - 1e-9
        all_passed = all_passed and passed
        if rule.effective_source == "assumption":
            has_assumed = True
        checks.append({"ruleId": rule.id, "result": "passed" if passed else "failed",
                       "actualM": round(dist, 2), "requiredM": d, "comparator": ">=",
                       "obstacleCategory": ",".join(rule.appliesTo.get("obstacle_type", [])),
                       "effectiveSource": rule.effective_source, "source": src})

    if site_boundary is not None:
        inside = site_boundary.covers(pt)
        all_passed = all_passed and inside
        checks.append({"ruleId": "PLANTING_INSIDE_SITE_BOUNDARY",
                       "result": "passed" if inside else "failed",
                       "actualM": None, "requiredM": None, "comparator": ">=",
                       "obstacleCategory": "SITE_BOUNDARY",
                       "effectiveSource": "verified",
                       "source": {"document": "Границы участка (геометрия чертежа)",
                                  "clause": None, "title": "Посадка в границах участка",
                                  "status": "verified"}})

    n_checked = sum(1 for c in checks if c["result"] in ("passed", "failed"))
    summary = (
        f"Position ({p.x:.1f}, {p.y:.1f}) m: {n_checked} enforced constraint(s) checked, "
        + ("all passed." if all_passed else "VIOLATIONS PRESENT.")
    )
    if has_assumed:
        summary += (" Some checks use ASSUMED demo distances (source clause not verified) — "
                    "see per-check effectiveSource.")

    return {
        "plantingId": p.id,
        "position": [round(p.x, 3), round(p.y, 3)],
        "speciesId": p.speciesId,
        "iteration": iteration,
        "summary": summary,
        "llmNarrative": None,
        "checks": checks,
    }
