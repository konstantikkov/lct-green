"""EvalPacket builder: everything the GPT evaluator sees, nothing it doesn't.

Normalized, compact, NO raw DXF / raw CAD entities — only derived facts the
deterministic engine already computed, plus the fixture expectations.
"""
from __future__ import annotations

from evals.pipeline_runner import PipelineResult

PACKET_SCHEMA_VERSION = "eval-packet/v1"
MAX_SAMPLE_EXPLANATIONS = 3


def _verified_rule_set(result: PipelineResult) -> list[dict]:
    out = []
    for rule in result.registry.rules:
        eff = rule.effective_distance_m
        if eff is None and rule.constraint.get("type") not in ("inside",):
            continue
        out.append({"id": rule.id,
                    "effectiveDistanceM": eff,
                    "effectiveSource": rule.effective_source,
                    "sourceStatus": rule.status,
                    "severity": rule.severity})
    return out


def _sample_explanations(result: PipelineResult, limit: int = MAX_SAMPLE_EXPLANATIONS) -> list[dict]:
    if not result.placements:
        return []
    from app.services.explain import build_explanation
    from app.services.validator import RuleDistanceOracle
    oracle = RuleDistanceOracle(result.idx)
    out = []
    for i, p in enumerate(result.placements[:limit]):
        exp = build_explanation(p, i, result.registry, oracle, result.cons.site_boundary)
        out.append({"plantingId": p.id,
                    "checks": [{"ruleId": c["ruleId"],
                                "effectiveSource": c["effectiveSource"],
                                "requiredM": c.get("requiredM"),
                                "actualM": c.get("actualM")}
                               for c in exp.get("checks", [])[:6]]})
    return out


def build_eval_packet(result: PipelineResult, expected: dict | None = None) -> dict:
    m = result.metrics
    from app.services.geometry import as_polygon_list
    allowed_area = sum(p.area for p in as_polygon_list(result.cons.allowed_area))
    return {
        "schemaVersion": PACKET_SCHEMA_VERSION,
        "caseId": result.case_id,
        "planSummary": {
            "seed": result.seed,
            "species": result.species,
            "placementCount": len(result.placements),
            "treeCount": m["treeCount"],
            "shrubCount": m["shrubCount"],
        },
        "verifiedRuleSet": _verified_rule_set(result),
        "deterministicValidation": {
            "valid": result.validation["valid"],
            "hardViolationCount": len(result.validation["hardViolations"]),
            "metrics": {k: m[k] for k in ("coverageScore", "distributionScore",
                                          "canopyAreaM2", "validCount")},
        },
        "expectedFixtureRequirements": {
            k: (expected or {}).get(k)
            for k in ("mustPass", "expectedSemanticLayers", "expectedForbiddenZoneCount",
                      "allowedMetricRanges", "knownEdgeCases")
        },
        "placements": [[round(p.x, 2), round(p.y, 2), p.kind] for p in result.placements],
        "spatialMetrics": {
            "allowedAreaM2": round(allowed_area, 1),
            "exclusionZoneCount": len(result.cons.exclusion_zones),
            "siteBoundarySource": result.cons.site_boundary_source,
            "allowedBasis": result.cons.allowed_basis,
            "repairLogSize": len(result.cons.repair_log),
        },
        "sampleExplanations": _sample_explanations(result),
        "semanticClassifications": {
            li.name: {"category": li.classification.category,
                      "confidence": li.classification.confidence,
                      "requiresReview": li.classification.requiresReview}
            for li in result.doc.layers
        },
    }
