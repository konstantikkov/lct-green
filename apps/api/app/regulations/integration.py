"""Bridge from ACTIVE rule candidates to the constraint engine's registry.

`apply_active_candidates(registry)` merges every ACTIVE candidate INTO the
registry (app/services/rules.py). Canonical-rule merge semantics (audit item 4):

  canonical key = (frozenset(plantingType), mapped obstacle category,
                   constraint type)

  * key already covered by a registry rule (e.g. the YAML
    TREE_BUILDING_MIN_DISTANCE) → the existing rule is PATCHED in place:
    constraint.distance_m takes the candidate value (meters), status becomes
    "verified", source is replaced with the document provenance (document
    number, clause=hierarchyPath, excerpt=sourceText[:400], url=None); the
    rule id and its assumption block are kept (the assumption becomes
    irrelevant — effective_source switches to "verified" automatically) and a
    merge note is appended to rule.note.
  * key not covered → a new REG_<candidateId> rule is appended.

  Either way, exactly ONE constraint per canonical key: never a duplicate.

Units are normalized to meters; unmappable reference objects are skipped with
a log entry.
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

EXCERPT_LIMIT = 400


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
    from ..services.rules import RegulatoryRule

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

    return RegulatoryRule(
        id=f"REG_{cid}",
        enabled=True,
        appliesTo={"planting_type": _plant_types(cand),
                   "obstacle_type": [obstacle]},
        constraint={"type": ctype, "distance_m": float(value) * factor, "units": "m"},
        source=_candidate_source(cand, doc),
        status="verified",
        severity="hard",
        assumption=None,
        note=f"из документа (кандидат {cid}, v{cand.get('ruleVersion', 1)}, "
             f"извлечение {cand.get('extraction', {}).get('promptVersion', '?')})",
    )


def _candidate_source(cand: dict, doc: dict | None):
    """Document provenance for a candidate: number, clause=hierarchyPath,
    excerpt=sourceText[:EXCERPT_LIMIT], url=None."""
    from ..services.rules import RuleSource

    src = cand.get("source", {})
    doc = doc or {}
    excerpt = (src.get("sourceText") or "")[:EXCERPT_LIMIT]
    return RuleSource(
        document=doc.get("documentNumber") or doc.get("fileName") or "regulations upload",
        documentNumber=doc.get("documentNumber") or "",
        documentDate=doc.get("publicationDate"),
        clause=src.get("hierarchyPath") or None,
        title=doc.get("title") or "",
        url=None,
        excerpt=excerpt or None,
    )


# ---------------------------------------------------------------------------
# canonical merge
# ---------------------------------------------------------------------------

def _rule_role(rule) -> str:
    # defensive: `role` lands on RegulatoryRule in a parallel change
    return getattr(rule, "role", None) or "regulatory"


def _rule_keys(rule):
    """Every canonical key a registry rule covers (one per obstacle type)."""
    applies = getattr(rule, "appliesTo", None) or {}
    plants = frozenset(applies.get("planting_type", []))
    ctype = (getattr(rule, "constraint", None) or {}).get("type")
    for obstacle in applies.get("obstacle_type", []):
        yield (plants, obstacle, ctype)


def _patch_rule(rule, mapped, cand: dict, doc: dict | None) -> None:
    """PATCH a canonical registry rule in place from an ACTIVE candidate.
    Keeps the rule id and assumption block (effective_source becomes
    "verified" automatically once status/distance are set)."""
    rule.constraint["distance_m"] = mapped.constraint["distance_m"]
    rule.constraint.setdefault("units", "m")
    rule.status = "verified"
    rule.source = mapped.source
    doc_name = (mapped.source.documentNumber or mapped.source.document
                or (doc or {}).get("fileName") or "документа")
    reviewer = cand.get("reviewer") or "не указан"
    merge_note = (f"значение верифицировано из {doc_name} "
                  f"(кандидат {cand.get('id', '?')}, reviewer {reviewer})")
    rule.note = f"{rule.note}; {merge_note}" if rule.note else merge_note


def apply_active_candidates(registry, candidates=None, documents=None) -> None:
    """Merge ACTIVE candidates into `registry` (RuleRegistry) IN PLACE.

    `candidates`/`documents` default to the regulations store; the benchmark
    (evals/regulations/score.py) passes scripted ones to exercise the real
    merge logic without any storage.
    """
    if candidates is None:
        from . import storage
        candidates = storage.list_candidates(status="ACTIVE")
    doc_cache: dict[str, dict | None] = dict(documents or {})

    def _doc_for(did: str) -> dict | None:
        if did not in doc_cache:
            try:
                from . import storage
                doc_cache[did] = storage.get_document(did)
            except Exception:
                doc_cache[did] = None
        return doc_cache[did]

    # index existing rules by canonical key; regulatory-role rules take
    # precedence over safe_buffer/design_heuristic duplicates of the same key
    index: dict[tuple, object] = {}
    for rule in registry.rules:
        for key in _rule_keys(rule):
            cur = index.get(key)
            if cur is None or (_rule_role(cur) != "regulatory"
                               and _rule_role(rule) == "regulatory"):
                index[key] = rule

    for cand in candidates:
        doc = _doc_for(cand.get("documentId", ""))
        mapped = candidate_to_rule(cand, doc)
        if mapped is None:
            continue  # unmappable → logged inside candidate_to_rule
        key = (frozenset(mapped.appliesTo["planting_type"]),
               mapped.appliesTo["obstacle_type"][0],
               mapped.constraint["type"])
        existing = index.get(key)
        if existing is not None:
            # canonical rule exists → PATCH in place, never duplicate
            _patch_rule(existing, mapped, cand, doc)
            log.info("regulations: candidate %s merged into canonical rule %s",
                     cand.get("id", "?"), existing.id)
        else:
            registry.rules.append(mapped)
            for k in _rule_keys(mapped):
                index.setdefault(k, mapped)
