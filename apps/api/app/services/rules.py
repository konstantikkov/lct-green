"""Regulatory rule registry.

Rules are DATA (rules/regulations.yaml), never hard-coded logic. The engine
resolves each rule to an *effective* distance:

  verified value (constraint.distance_m, status=verified)   → effectiveSource=verified
  else assumption.assumed_distance_m when assumption.enabled → effectiveSource=assumption
  else                                                       → inactive (never enforced)

User overrides (enable/disable, assumption tweaks) are persisted separately in
data/rule_overrides.json so the YAML registry stays authoritative source data.

A rule may carry `also_cited` — further acts that set the same requirement
(e.g. 743-ПП табл. 3.6.1 repeats СП 42 табл. 9.1) — and may be restricted to
plant genera via `applies_to.species_genus` (e.g. 623-ПП п. 4.2.8 names
species that must stay farther from heat mains).
"""
from __future__ import annotations

import json
import threading
from functools import lru_cache
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from .. import config

_lock = threading.Lock()

RULE_ROLES = ("regulatory", "safe_buffer", "design_heuristic")


def _snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


class RuleSource(BaseModel):
    document: str
    documentNumber: str = ""
    documentDate: Optional[str] = None
    clause: Optional[str] = None
    title: str = ""
    url: Optional[str] = None
    excerpt: Optional[str] = None
    # only on also_cited entries: "same_value" — the act repeats the value;
    # "reference" — the act requires the norm by reference to another table
    relation: Optional[str] = None


class RuleAssumption(BaseModel):
    assumedDistanceM: Optional[float] = None
    enabled: bool = False
    note: str = ""


class RegulatoryRule(BaseModel):
    id: str
    enabled: bool = True
    appliesTo: dict[str, list[str]] = Field(default_factory=dict)  # plantingType, obstacleType
    constraint: dict[str, Any] = Field(default_factory=dict)
    source: RuleSource
    alsoCited: list[RuleSource] = Field(default_factory=list)
    status: str = "missing_source_data"
    severity: str = "hard"
    # role separates true regulatory constraints from engineering artifacts:
    #   regulatory       — a legal norm (checked for PASS/FAIL/UNVERIFIED)
    #   safe_buffer      — conservative technological offset, NOT a norm check
    #   design_heuristic — project heuristic; violations are warnings only
    role: str = "regulatory"
    # applicability conditions, e.g. {crown_diameter_max_m: 5.0} for СП 42
    # табл. 9.1 (rows apply to trees with crown ≤ 5 m only)
    condition: dict[str, Any] = Field(default_factory=dict)
    assumption: Optional[RuleAssumption] = None
    note: str = ""

    @property
    def effective_distance_m(self) -> Optional[float]:
        if not self.enabled:
            return None
        if self.status == "verified" and self.constraint.get("distance_m") is not None:
            return float(self.constraint["distance_m"])
        if self.assumption and self.assumption.enabled and self.assumption.assumedDistanceM is not None:
            return float(self.assumption.assumedDistanceM)
        return None

    @property
    def _has_operative_value(self) -> bool:
        """Whether the rule carries the value it needs to be enforceable.

        Distance rules need constraint.distance_m; a species_blacklist is
        driven by constraint.species instead and would otherwise be reported
        as inactive even when verified and actively rejecting species.
        """
        if self.constraint.get("distance_m") is not None:
            return True
        ctype = self.constraint.get("type")
        if ctype == "species_blacklist":
            return bool(self.constraint.get("species"))
        if ctype == "custom_geometry":
            return bool(self.constraint.get("legs_m"))
        if ctype == "lawn_area":
            return self.constraint.get("min_width_m") is not None
        return False

    @property
    def species_genera(self) -> list[str]:
        return self.appliesTo.get("species_genus") or []

    def applies_to_species(self, species_id: Optional[str]) -> bool:
        """Genus filter for species-specific rules. A species missing from the
        catalog cannot be ruled out, so the rule applies (safe direction)."""
        genera = self.species_genera
        if not genera:
            return True
        genus = species_genus(species_id) if species_id else None
        if genus is None:
            return True
        return genus.lower() in {g.lower() for g in genera}

    @property
    def effective_source(self) -> str:
        if not self.enabled:
            return "inactive"
        if self.status == "verified" and self._has_operative_value:
            return "verified"
        if self.assumption and self.assumption.enabled and self.assumption.assumedDistanceM is not None:
            return "assumption"
        return "inactive"

    def to_api(self) -> dict:
        return {
            "id": self.id,
            "enabled": self.enabled,
            "appliesTo": {"plantingType": self.appliesTo.get("planting_type", []),
                          "obstacleType": self.appliesTo.get("obstacle_type", []),
                          "speciesGenus": self.species_genera or None},
            "constraint": {"type": self.constraint.get("type"),
                           "distanceM": self.constraint.get("distance_m"),
                           "species": self.constraint.get("species"),
                           "legsM": self.constraint.get("legs_m"),
                           "units": self.constraint.get("units", "m")},
            "source": self.source.model_dump(),
            "alsoCited": [c.model_dump() for c in self.alsoCited],
            "status": self.status,
            "severity": self.severity,
            "role": self.role,
            "condition": {_snake_to_camel(k): v for k, v in self.condition.items()},
            "assumption": self.assumption.model_dump() if self.assumption else None,
            "effectiveDistanceM": self.effective_distance_m,
            "effectiveSource": self.effective_source,
            "note": self.note,
        }


class RuleRegistry(BaseModel):
    version: str = "0.0.0"
    documents: list[dict] = Field(default_factory=list)
    rules: list[RegulatoryRule] = Field(default_factory=list)

    def get(self, rule_id: str) -> Optional[RegulatoryRule]:
        return next((r for r in self.rules if r.id == rule_id), None)

    def rules_for(self, planting_type: str, constraint_types: set[str] | None = None,
                  species_id: Optional[str] = None) -> list[RegulatoryRule]:
        """Rules for a planting type. Species-specific rules (applies_to.
        species_genus) are returned only when `species_id` is given and
        matches: kind-level callers such as exclusion-zone building must not
        impose one genus's setback on every species of that kind."""
        out = []
        for r in self.rules:
            if planting_type not in r.appliesTo.get("planting_type", []):
                continue
            if constraint_types and r.constraint.get("type") not in constraint_types:
                continue
            if r.species_genera and (species_id is None or not r.applies_to_species(species_id)):
                continue
            out.append(r)
        return out


def _overrides_path():
    return config.DATA_DIR / "rule_overrides.json"


def _load_overrides() -> dict:
    p = _overrides_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def save_override(rule_id: str, patch: dict) -> None:
    with _lock:
        ov = _load_overrides()
        cur = ov.get(rule_id, {})
        cur.update({k: v for k, v in patch.items() if v is not None})
        ov[rule_id] = cur
        _overrides_path().parent.mkdir(parents=True, exist_ok=True)
        _overrides_path().write_text(json.dumps(ov, ensure_ascii=False, indent=2))


def _resolve_source(src: dict, docs: dict) -> RuleSource:
    doc = docs.get(src.get("document"), {})
    return RuleSource(
        document=doc.get("number", src.get("document", "unknown")),
        documentNumber=doc.get("number", ""),
        documentDate=doc.get("date"),
        clause=src.get("clause"),
        title=src.get("title", ""),
        url=src.get("url"),
        excerpt=src.get("excerpt"),
        relation=src.get("relation"),
    )


def load_registry() -> RuleRegistry:
    raw = yaml.safe_load(config.RULES_FILE.read_text(encoding="utf-8"))
    docs = {d["key"]: d for d in raw.get("documents", [])}
    rules: list[RegulatoryRule] = []
    for r in raw.get("rules", []):
        source = _resolve_source(r.get("source", {}), docs)
        also_cited = [_resolve_source(c, docs) for c in r.get("also_cited") or []]
        assumption = None
        if r.get("assumption"):
            a = r["assumption"]
            assumption = RuleAssumption(
                assumedDistanceM=a.get("assumed_distance_m"),
                enabled=bool(a.get("enabled", False)),
                note=a.get("note", ""),
            )
        rules.append(RegulatoryRule(
            id=r["id"], enabled=bool(r.get("enabled", True)),
            appliesTo=r.get("applies_to", {}),
            constraint=r.get("constraint", {}),
            source=source, alsoCited=also_cited,
            status=r.get("status", "missing_source_data"),
            severity=r.get("severity", "hard"),
            role=r.get("role", "regulatory"),
            condition=r.get("condition") or {},
            assumption=assumption, note=r.get("note", ""),
        ))

    registry = RuleRegistry(version=str(raw.get("version", "0.0.0")),
                            documents=list(docs.values()), rules=rules)

    # apply persisted user overrides
    for rule_id, patch in _load_overrides().items():
        rule = registry.get(rule_id)
        if not rule:
            continue
        if "enabled" in patch:
            rule.enabled = bool(patch["enabled"])
        if "assumption" in patch and rule.assumption:
            ap = patch["assumption"] or {}
            if ap.get("assumedDistanceM") is not None:
                rule.assumption.assumedDistanceM = float(ap["assumedDistanceM"])
            if ap.get("enabled") is not None:
                rule.assumption.enabled = bool(ap["enabled"])

    # merge ACTIVE regulation-ingestion candidates (WS4): the integration layer
    # receives the whole registry so it can PATCH canonical YAML rules in place
    # (same subject/obstacle/type => merged provenance) instead of appending a
    # duplicate constraint. A broken regulations store must never break the
    # registry.
    try:
        from ..regulations.integration import apply_active_candidates
        apply_active_candidates(registry)
    except Exception:
        pass
    return registry


def load_species() -> list[dict]:
    raw = yaml.safe_load(config.SPECIES_FILE.read_text(encoding="utf-8"))
    out = []
    for s in raw.get("species", []):
        p = s.get("parameters", {})
        stock = s.get("planting_stock") or {}
        growth = s.get("growth") or {}
        out.append({
            "id": s["id"], "kind": s["kind"],
            "displayName": s.get("display_name", s["id"]),
            "displayNameRu": s.get("display_name_ru", ""),
            "latin": s.get("latin", ""),
            "parameters": {
                "crownRadiusM": float(p.get("crown_radius_m", 2.0)),
                "matureHeightM": float(p.get("mature_height_m", 10.0)),
                "rootZoneRadiusM": float(p.get("root_zone_radius_m", 1.5)),
            },
            # illustrative nursery-stock and growth defaults (see species.yaml)
            "plantingStock": {_snake_to_camel(k): v for k, v in stock.items()} or None,
            "growth": {_snake_to_camel(k): v for k, v in growth.items()} or None,
            "regulatoryStatus": "unknown",  # until ППМ 369-ПП list is verified
        })
    return out


@lru_cache(maxsize=1)
def _species_latin() -> dict[str, str]:
    return {s["id"]: s["latin"] for s in load_species() if s.get("latin")}


def species_genus(species_id: str) -> Optional[str]:
    """Genus of a catalog species ("Tilia cordata" → "Tilia"); None if unknown."""
    latin = _species_latin().get(species_id)
    return latin.split()[0] if latin else None


def blacklist_check(registry: RuleRegistry, species_id: str, latin: str) -> tuple[bool, Optional[str]]:
    """Returns (allowed, blocking_rule_id). Blacklist enforced only when a
    verified species list is present — never invented."""
    for r in registry.rules:
        if r.constraint.get("type") != "species_blacklist" or not r.enabled:
            continue
        species = r.constraint.get("species")
        if not species:
            continue  # missing_source_data: list absent → cannot enforce
        needles = {s.lower() for s in species}
        if species_id.lower() in needles or (latin and latin.lower() in needles):
            return False, r.id
    return True, None
