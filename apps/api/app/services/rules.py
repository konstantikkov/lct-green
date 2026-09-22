"""Regulatory rule registry.

Rules are DATA (rules/regulations.yaml), never hard-coded logic. The engine
resolves each rule to an *effective* distance:

  verified value (constraint.distance_m, status=verified)   → effectiveSource=verified
  else assumption.assumed_distance_m when assumption.enabled → effectiveSource=assumption
  else                                                       → inactive (never enforced)

User overrides (enable/disable, assumption tweaks) are persisted separately in
data/rule_overrides.json so the YAML registry stays authoritative source data.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from .. import config

_lock = threading.Lock()


class RuleSource(BaseModel):
    document: str
    documentNumber: str = ""
    documentDate: Optional[str] = None
    clause: Optional[str] = None
    title: str = ""
    url: Optional[str] = None
    excerpt: Optional[str] = None


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
    status: str = "missing_source_data"
    severity: str = "hard"
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
    def effective_source(self) -> str:
        if not self.enabled:
            return "inactive"
        if self.status == "verified" and self.constraint.get("distance_m") is not None:
            return "verified"
        if self.assumption and self.assumption.enabled and self.assumption.assumedDistanceM is not None:
            return "assumption"
        return "inactive"

    def to_api(self) -> dict:
        return {
            "id": self.id,
            "enabled": self.enabled,
            "appliesTo": {"plantingType": self.appliesTo.get("planting_type", []),
                          "obstacleType": self.appliesTo.get("obstacle_type", [])},
            "constraint": {"type": self.constraint.get("type"),
                           "distanceM": self.constraint.get("distance_m"),
                           "species": self.constraint.get("species"),
                           "units": self.constraint.get("units", "m")},
            "source": self.source.model_dump(),
            "status": self.status,
            "severity": self.severity,
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

    def rules_for(self, planting_type: str, constraint_types: set[str] | None = None) -> list[RegulatoryRule]:
        out = []
        for r in self.rules:
            if planting_type not in r.appliesTo.get("planting_type", []):
                continue
            if constraint_types and r.constraint.get("type") not in constraint_types:
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


def load_registry() -> RuleRegistry:
    raw = yaml.safe_load(config.RULES_FILE.read_text(encoding="utf-8"))
    docs = {d["key"]: d for d in raw.get("documents", [])}
    rules: list[RegulatoryRule] = []
    for r in raw.get("rules", []):
        src = r.get("source", {})
        doc = docs.get(src.get("document"), {})
        source = RuleSource(
            document=doc.get("number", src.get("document", "unknown")),
            documentNumber=doc.get("number", ""),
            documentDate=doc.get("date"),
            clause=src.get("clause"),
            title=src.get("title", ""),
            url=src.get("url"),
            excerpt=src.get("excerpt"),
        )
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
            source=source, status=r.get("status", "missing_source_data"),
            severity=r.get("severity", "hard"),
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

    # merge ACTIVE regulation-ingestion candidates (WS4) as verified rules;
    # a broken regulations store must never break the registry
    try:
        from ..regulations.integration import active_candidate_rules
        registry.rules.extend(active_candidate_rules())
    except Exception:
        pass
    return registry


def load_species() -> list[dict]:
    raw = yaml.safe_load(config.SPECIES_FILE.read_text(encoding="utf-8"))
    out = []
    for s in raw.get("species", []):
        p = s.get("parameters", {})
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
            "regulatoryStatus": "unknown",  # until ППМ 369-ПП list is verified
        })
    return out


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
