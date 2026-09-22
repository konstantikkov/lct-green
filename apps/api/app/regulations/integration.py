"""Bridge from ACTIVE rule candidates to the constraint engine's registry.

`active_candidate_rules()` maps every ACTIVE candidate onto the existing
RegulatoryRule model (app/services/rules.py) so the engine consumes verified,
document-sourced rules with no code change per rule. Units are normalized to
meters; unmappable reference objects are skipped with a log entry.
"""
from __future__ import annotations

import logging

log = logging.getLogger("greenplan.regulations")

# unit → factor to meters
UNIT_TO_M = {
    "м": 1.0, "m": 1.0, "метр": 1.0, "метра": 1.0, "метров": 1.0,
    "см": 0.01, "cm": 0.01, "мм": 0.001, "mm": 0.001,
}

# candidate referenceObject → engine obstacle Category
OBSTACLE_MAP = {
    "BUILDING_WALL": "BUILDING", "BUILDING": "BUILDING", "WALL": "BUILDING",
    "FACADE": "BUILDING", "STRUCTURE": "BUILDING",
    "ROAD_EDGE": "ROAD", "ROAD": "ROAD", "ROADWAY": "ROAD", "CARRIAGEWAY": "ROAD",
    "CURB": "CURB", "SIDEWALK": "SIDEWALK", "FOOTPATH": "SIDEWALK", "PATH": "SIDEWALK",
    "WATER_PIPE": "UTILITY_WATER", "WATER_SUPPLY": "UTILITY_WATER",
    "WATER_MAIN": "UTILITY_WATER",
    "GAS_PIPE": "UTILITY_GAS", "GAS_PIPELINE": "UTILITY_GAS",
    "SEWER": "UTILITY_SEWER", "SEWER_PIPE": "UTILITY_SEWER",
    "SEWERAGE": "UTILITY_SEWER",
    "POWER_CABLE": "UTILITY_ELECTRICITY", "ELECTRIC_CABLE": "UTILITY_ELECTRICITY",
    "POWER_LINE": "UTILITY_ELECTRICITY",
    "HEAT_PIPE": "UTILITY_HEAT", "HEATING_NETWORK": "UTILITY_HEAT",
    "DRAINAGE": "UTILITY_DRAINAGE", "DRAIN": "UTILITY_DRAINAGE",
    "COMMUNICATION_CABLE": "UTILITY_COMMUNICATION",
    "UTILITY": "UTILITY", "UNDERGROUND_UTILITY": "UTILITY",
    "FENCE": "FENCE",
    "LIGHTING": "LIGHTING", "LIGHT_POLE": "LIGHTING", "LAMP_POST": "LIGHTING",
    "LIGHTING_SUPPORT": "LIGHTING", "POLE": "LIGHTING",
    "TREE": "EXISTING_TREE", "EXISTING_TREE": "EXISTING_TREE",
    "SHRUB": "EXISTING_SHRUB", "EXISTING_SHRUB": "EXISTING_SHRUB",
    "PARKING": "PARKING",
}

PLANT_MAP = {
    "TREE": "TREE", "TREES": "TREE", "ДЕРЕВО": "TREE", "ДЕРЕВЬЯ": "TREE",
    "SHRUB": "SHRUB", "SHRUBS": "SHRUB", "BUSH": "SHRUB",
    "КУСТАРНИК": "SHRUB", "КУСТАРНИКИ": "SHRUB",
}

CONSTRAINT_MAP = {"MIN_DISTANCE": "min_distance", "MIN_SPACING": "min_spacing"}


def _norm_key(s: str) -> str:
    return (s or "").strip().upper().replace(" ", "_").replace("-", "_")


def _plant_types(cand: dict) -> list[str]:
    kinds = []
    for p in cand.get("applicability", {}).get("plantType", []):
        k = PLANT_MAP.get(_norm_key(p))
        if k and k not in kinds:
            kinds.append(k)
    return kinds or ["TREE", "SHRUB"]  # unspecified → applies to both


def candidate_to_rule(cand: dict, doc: dict | None):
    """Map one ACTIVE candidate to a RegulatoryRule; None when unmappable."""
    from ..services.rules import RegulatoryRule, RuleSource

    cid = cand.get("id", "?")
    con = cand.get("constraint", {})
    ctype = CONSTRAINT_MAP.get(con.get("type"))
    if ctype is None:
        log.info("regulations: candidate %s skipped (constraint type %s not executable)",
                 cid, con.get("type"))
        return None
    value, unit = con.get("value"), (con.get("unit") or "").strip().lower().rstrip(".")
    factor = UNIT_TO_M.get(unit)
    if value is None or factor is None:
        log.info("regulations: candidate %s skipped (value=%s unit=%r not convertible to m)",
                 cid, value, con.get("unit"))
        return None
    obstacle = OBSTACLE_MAP.get(_norm_key(con.get("referenceObject", "")))
    if obstacle is None:
        log.info("regulations: candidate %s skipped (unmappable referenceObject %r)",
                 cid, con.get("referenceObject"))
        return None

    src = cand.get("source", {})
    doc = doc or {}
    excerpt = (src.get("sourceText") or "")[:280]
    source = RuleSource(
        document=doc.get("documentNumber") or doc.get("fileName") or "regulations upload",
        documentNumber=doc.get("documentNumber") or "",
        documentDate=doc.get("publicationDate"),
        clause=src.get("hierarchyPath") or None,
        title=doc.get("title") or "",
        url=None,
        excerpt=excerpt or None,
    )
    return RegulatoryRule(
        id=f"REG_{cid}",
        enabled=True,
        appliesTo={"planting_type": _plant_types(cand),
                   "obstacle_type": [obstacle]},
        constraint={"type": ctype, "distance_m": float(value) * factor, "units": "m"},
        source=source,
        status="verified",
        severity="hard",
        assumption=None,
        note=f"из документа (кандидат {cid}, v{cand.get('ruleVersion', 1)}, "
             f"извлечение {cand.get('extraction', {}).get('promptVersion', '?')})",
    )


def active_candidate_rules() -> list:
    """All ACTIVE candidates as RegulatoryRule entries (engine-ready)."""
    from . import storage
    rules = []
    doc_cache: dict[str, dict | None] = {}
    for cand in storage.list_candidates(status="ACTIVE"):
        did = cand.get("documentId", "")
        if did not in doc_cache:
            doc_cache[did] = storage.get_document(did)
        rule = candidate_to_rule(cand, doc_cache[did])
        if rule is not None:
            rules.append(rule)
    return rules
