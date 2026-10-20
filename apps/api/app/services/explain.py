"""Explainability engine.

All facts (distances, rule results, sources) are computed deterministically.
An optional local-LLM pass may rephrase facts as natural language but can
never add documents, clauses or numbers — the narrative is generated from the
already-verified check list only, and is clearly labeled.

Per-check `result` uses the frozen regulatory statuses:
PASS | FAIL | UNVERIFIED | NOT_APPLICABLE (see docs/API_CONTRACT.md).
PASS is reserved for verified regulatory-role rules with confirmed
applicability conditions — an ASSUMED demo value can never read as PASS.
"""
from __future__ import annotations

from shapely.geometry import Point

from .rules import RuleRegistry
from .validator import Placement, RuleDistanceOracle, condition_confirmed


def _check_status_and_reason(rule, p: Placement, passed_geom: bool,
                             ) -> tuple[str, str | None]:
    """Status + human reason for a geometrically evaluated min_distance check."""
    if not passed_geom:
        return "FAIL", None
    if rule.role == "safe_buffer":
        if "UTILITY" in rule.appliesTo.get("obstacle_type", []):
            return "UNVERIFIED", ("технологический буфер (НЕ нормативная проверка) — "
                                  "тип сети не установлен, норма зависит от типа")
        return "UNVERIFIED", ("технологический запас проекта (НЕ нормативная проверка) — "
                              "нормой не установлен, на итоговый статус не влияет")
    if not condition_confirmed(rule, p) and "crown_diameter_max_m" in rule.condition:
        crown = 2.0 * float(p.crownRadiusM)
        limit = rule.condition.get("crown_diameter_max_m")
        return "UNVERIFIED", (f"крона {crown:.1f} м > {limit:g} м — табл. 9.1 "
                              "недостаточна, требуется увеличенное расстояние")
    if "voltage_class_kv" in rule.condition:
        return "UNVERIFIED", (f"класс напряжения в чертеже не указан — по расположению знаков у опор "
                              f"наружного освещения принята линия {rule.condition['voltage_class_kv']} кВ; "
                              f"для линий выше 1 кВ охранная зона шире (10–55 м)")
    if rule.effective_source != "verified":
        return "UNVERIFIED", "значение не верифицировано (ДЕМО-допущение)"
    if not condition_confirmed(rule, p):
        return "UNVERIFIED", "условие применимости правила не подтверждено"
    if rule.role != "regulatory":
        return "UNVERIFIED", "правило не является правовой нормой"
    return "PASS", None


def build_explanation(p: Placement, iteration: int, registry: RuleRegistry,
                      oracle: RuleDistanceOracle, site_boundary,
                      site_boundary_source: str = "detected") -> dict:
    pt = Point(p.x, p.y)
    checks = []
    any_failed = False
    has_assumed = False
    has_unverified = False

    for rule in registry.rules_for(p.kind, {"min_distance"}, species_id=p.speciesId):
        if rule.role == "design_heuristic":
            continue  # heuristics are warnings, not regulatory checks
        d = rule.effective_distance_m
        src = {"document": rule.source.document, "clause": rule.source.clause,
               "title": rule.source.title, "status": rule.status,
               "alsoCited": [{"document": c.document, "clause": c.clause,
                              "relation": c.relation} for c in rule.alsoCited]}
        if d is None:
            continue
        res = oracle.nearest(rule.appliesTo.get("obstacle_type", []), pt)
        if res is None:
            checks.append({"ruleId": rule.id, "result": "NOT_APPLICABLE",
                           "actualM": None, "requiredM": d, "comparator": ">=",
                           "obstacleCategory": ",".join(rule.appliesTo.get("obstacle_type", [])),
                           "effectiveSource": rule.effective_source,
                           "role": rule.role, "source": src,
                           "note": "no obstacles of this type in the drawing",
                           "reason": "объектов этого типа нет в чертеже"})
            continue
        dist, _ = res
        passed_geom = dist >= d - 1e-9
        status, reason = _check_status_and_reason(rule, p, passed_geom)
        any_failed = any_failed or status == "FAIL"
        if rule.effective_source == "assumption":
            has_assumed = True
        if status == "UNVERIFIED":
            has_unverified = True
        checks.append({"ruleId": rule.id, "result": status,
                       "actualM": round(dist, 2), "requiredM": d, "comparator": ">=",
                       "obstacleCategory": ",".join(rule.appliesTo.get("obstacle_type", [])),
                       "effectiveSource": rule.effective_source,
                       "role": rule.role, "source": src,
                       "reason": reason})

    for rule in registry.rules_for(p.kind, {"outside_area"}, species_id=p.speciesId):
        if rule.effective_source == "inactive":
            continue
        cats = rule.appliesTo.get("obstacle_type", [])
        present, inside_id = oracle.covering_area(cats, pt)
        src = {"document": rule.source.document, "clause": rule.source.clause,
               "title": rule.source.title, "status": rule.status, "alsoCited": []}
        row = {"ruleId": rule.id, "actualM": None, "requiredM": None, "comparator": "outside",
               "obstacleCategory": ",".join(cats), "effectiveSource": rule.effective_source,
               "role": rule.role, "source": src}
        if not present:
            checks.append({**row, "result": "NOT_APPLICABLE",
                           "note": "no areas of this type in the drawing",
                           "reason": "объектов этого типа нет в чертеже"})
            continue
        status, reason = _check_status_and_reason(rule, p, inside_id is None)
        if inside_id is not None:
            reason = "посадка внутри контура (" + rule.source.title + ")"
        any_failed = any_failed or status == "FAIL"
        has_unverified = has_unverified or status == "UNVERIFIED"
        checks.append({**row, "result": status, "reason": reason})

    if site_boundary is not None:
        inside = site_boundary.covers(pt)
        boundary_detected = site_boundary_source == "detected"
        if not inside:
            status, reason = "FAIL", None
            any_failed = True
        elif boundary_detected:
            status, reason = "PASS", None
        else:
            has_unverified = True
            status = "UNVERIFIED"
            reason = ("граница участка не определена — использованы границы "
                      f"чертежа ({site_boundary_source.upper()})")
        checks.append({"ruleId": "PLANTING_INSIDE_SITE_BOUNDARY",
                       "result": status,
                       "actualM": None, "requiredM": None, "comparator": ">=",
                       "obstacleCategory": "SITE_BOUNDARY",
                       "effectiveSource": "verified" if boundary_detected else "assumption",
                       "role": "regulatory",
                       "source": {"document": "Границы участка (геометрия чертежа)",
                                  "clause": None, "title": "Посадка в границах участка",
                                  "status": "verified" if boundary_detected
                                            else "missing_source_data"},
                       "reason": reason})

    n_checked = sum(1 for c in checks if c["result"] != "NOT_APPLICABLE")
    summary = (
        f"Position ({p.x:.1f}, {p.y:.1f}) m: {n_checked} enforced constraint(s) checked, "
        + ("VIOLATIONS PRESENT." if any_failed else
           ("no violations, but some checks are UNVERIFIED — "
            "regulatory compliance NOT established." if has_unverified
            else "all passed against verified values."))
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
