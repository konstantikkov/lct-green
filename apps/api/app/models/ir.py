"""CAD Intermediate Representation (IR) models.

Two representations exist:
  * CadDocument — high-precision IR used by geometry algorithms and DXF export.
    Coordinates are stored in METERS (normalized at ingestion); the original
    unit scale is retained so exports can map back to source units.
  * Chunk / semantic summaries (see chunking.py) — the compact LLM-facing IR.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Category = Literal[
    "SITE_BOUNDARY", "BUILDING", "ROAD", "CURB", "SIDEWALK",
    "UTILITY", "UTILITY_WATER", "UTILITY_GAS", "UTILITY_ELECTRICITY",
    "UTILITY_SEWER", "UTILITY_HEAT", "UTILITY_DRAINAGE", "UTILITY_COMMUNICATION",
    "POWER_LINE_OVERHEAD",
    "EXISTING_TREE", "EXISTING_SHRUB", "GREEN_AREA", "PARKING",
    "ACCESS_ROUTE", "FENCE", "LIGHTING", "ANNOTATION", "UNKNOWN",
]

CATEGORIES: list[str] = list(Category.__args__)  # type: ignore[attr-defined]

UTILITY_CATEGORIES = {
    "UTILITY", "UTILITY_WATER", "UTILITY_GAS", "UTILITY_ELECTRICITY",
    "UTILITY_SEWER", "UTILITY_HEAT", "UTILITY_DRAINAGE", "UTILITY_COMMUNICATION",
    "POWER_LINE_OVERHEAD",
}


class LayerClassification(BaseModel):
    category: str = "UNKNOWN"
    confidence: float = 0.0
    # package: set by the xref-package source (e.g. the work-boundary file)
    source: Literal["rule", "geometry", "llm", "user", "package"] = "rule"
    requiresReview: bool = True
    matchedKeyword: Optional[str] = None


class LayerInfo(BaseModel):
    name: str
    entityCount: int = 0
    color: str = "#8899aa"
    classification: LayerClassification = Field(default_factory=LayerClassification)


class IREntity(BaseModel):
    """One CAD entity, geometry normalized to meters.

    geometry kinds:
      polyline: {"points": [[x,y],...], "closed": bool}
      point:    {"position": [x,y]}
      text:     {"position": [x,y], "text": str, "height": float}
      circle:   {"center": [x,y], "radius": float, "points": [...] } (points = tessellation)
    """
    id: str
    sourceHandle: Optional[str] = None
    type: str
    layer: str
    kind: Literal["polyline", "polygon", "point", "text"]
    geometry: dict[str, Any]
    attributes: dict[str, Any] = Field(default_factory=dict)
    blockName: Optional[str] = None


class Extents(BaseModel):
    min: tuple[float, float]
    max: tuple[float, float]


class CadDocument(BaseModel):
    documentId: str
    fileName: str
    format: Literal["DXF", "DWG", "PACKAGE"]  # PACKAGE: eTransmit zip merged from xrefs
    cadVersion: str = "unknown"
    units: str = "unknown"
    unitsScaleToMeters: float = 1.0
    extents: Extents
    layers: list[LayerInfo]
    entities: list[IREntity]
    entityCount: int = 0
    conversionLog: list[str] = Field(default_factory=list)

    def layer_map(self) -> dict[str, LayerInfo]:
        return {l.name: l for l in self.layers}

    def category_of(self, entity: IREntity) -> str:
        lm = self.layer_map()
        li = lm.get(entity.layer)
        return li.classification.category if li else "UNKNOWN"
