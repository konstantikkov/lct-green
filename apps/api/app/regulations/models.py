"""Pydantic models for the regulations feature — mirrors the FROZEN contract
in docs/API_CONTRACT.md (§ Regulations feature WS4) exactly."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

DocStatus = Literal["UPLOADED", "PARSED", "EXTRACTED", "FAILED"]
RuleCandidateStatus = Literal["EXTRACTED", "NEEDS_REVIEW", "VERIFIED",
                              "ACTIVE", "DEPRECATED", "REJECTED"]
CANDIDATE_STATUSES = ["EXTRACTED", "NEEDS_REVIEW", "VERIFIED",
                      "ACTIVE", "DEPRECATED", "REJECTED"]


class RegDocument(BaseModel):
    id: str
    fileName: str
    title: Optional[str] = None
    documentNumber: Optional[str] = None
    jurisdiction: Optional[str] = None
    publicationDate: Optional[str] = None
    effectiveDate: Optional[str] = None
    version: str = "1"
    fileHash: str
    status: str = "UPLOADED"
    uploadedAt: float
    pageCount: Optional[int] = None
    fragmentCount: int = 0
    candidateCounts: dict[str, int] = Field(default_factory=dict)
    parseLog: list[str] = Field(default_factory=list)


class Fragment(BaseModel):
    id: str
    page: Optional[int] = None
    hierarchyPath: str
    kind: Literal["clause", "table", "note", "heading"]
    text: str
    order: int


class CandidateSource(BaseModel):
    documentId: str
    documentVersion: str
    page: Optional[int] = None
    hierarchyPath: str = ""
    fragmentIds: list[str] = Field(default_factory=list)
    sourceText: str = ""


class Applicability(BaseModel):
    jurisdiction: Optional[str] = None
    plantType: list[str] = Field(default_factory=list)
    objectTypes: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class Constraint(BaseModel):
    type: Literal["MIN_DISTANCE", "MIN_SPACING", "OTHER"] = "OTHER"
    subject: str = ""
    referenceObject: str = ""
    operator: str = ">="
    value: Optional[float] = None
    unit: Optional[str] = None


class Verification(BaseModel):
    verdict: Optional[Literal["SUPPORTED", "PARTIALLY_SUPPORTED",
                              "UNSUPPORTED", "AMBIGUOUS"]] = None
    numericLiteralFound: bool = False
    notes: Optional[str] = None
    model: Optional[str] = None
    promptVersion: Optional[str] = None


class ExtractionMeta(BaseModel):
    model: str = ""
    promptVersion: str = ""


class RuleCandidate(BaseModel):
    id: str
    documentId: str
    ruleVersion: int = 1
    source: CandidateSource
    applicability: Applicability = Field(default_factory=Applicability)
    constraint: Constraint = Field(default_factory=Constraint)
    exceptions: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    machineExecutable: bool = False
    confidence: float = 0.0
    status: str = "EXTRACTED"
    verification: Optional[Verification] = None
    reviewer: Optional[str] = None
    reviewDate: Optional[float] = None
    extraction: ExtractionMeta = Field(default_factory=ExtractionMeta)


# ---------------------------------------------------------------------------
# LLM I/O schemas (never exposed via the API directly)
# ---------------------------------------------------------------------------

class LLMCandidate(BaseModel):
    """What the extraction model is allowed to emit. Provenance text is NOT
    taken from the model — sourceText is copied server-side from fragments."""
    fragmentIds: list[str] = Field(default_factory=list)
    constraintType: Literal["MIN_DISTANCE", "MIN_SPACING", "OTHER"] = "OTHER"
    subject: str = ""
    referenceObject: str = ""
    operator: str = ">="
    value: Optional[float] = None
    unit: Optional[str] = None
    plantType: list[str] = Field(default_factory=list)
    objectTypes: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    jurisdiction: Optional[str] = None
    confidence: float = 0.5


class ExtractionBatch(BaseModel):
    candidates: list[LLMCandidate] = Field(default_factory=list)


class VerifyLLMResult(BaseModel):
    verdict: Literal["SUPPORTED", "PARTIALLY_SUPPORTED",
                     "UNSUPPORTED", "AMBIGUOUS"] = "AMBIGUOUS"
    notes: str = ""
    valueSupported: bool = False
    unitSupported: bool = False
    applicabilitySupported: bool = False
    exceptionsComplete: bool = False
    isObligation: bool = False
