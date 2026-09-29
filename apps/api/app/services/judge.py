"""LLM judge v2 — soft-quality review of an already-compliant plan.

The judge runs ONLY after the deterministic validator reports valid==true
(the pipeline repairs or skips otherwise), so it never argues about hard
compliance. It receives a compact, deterministic JudgePacket (judge_packet.py)
whose diagnostics carry evidence IDs; every claim in the response must cite
those IDs — unknown IDs are stripped in code, parameter changes are clamped to
a whitelist with per-iteration max deltas. Decisions: ACCEPT, RERUN, or
MANUAL_REVIEW (abstention — terminal for the run, routed to a human).

When the local LLM is disabled or unreachable, a clearly-labeled deterministic
heuristic emits the same v2 schema (mode="heuristic_fallback") so the pipeline
stays functional. No cloud fallback exists by design.

Prompts are versioned files: prompts/judge/{version}.md, selected via the
JUDGE_PROMPT_VERSION env var (default "v2"). Every stored judgment records
promptVersion, model, temperature, inputSchemaVersion and judgeInputHash.

Regulatory separation (iteration 5): ACCEPT never means regulatory validity —
that verdict is computed deterministically by the validator. In code, judge
confidence is capped at 0.3 + 0.7*regulatoryCoverage, robustness is bounded
by parser coverage, no-op parameter changes are dropped, and a deterministic
issue is added when a non-GOOD dimension comes with an empty issue list.
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Literal

from pydantic import BaseModel, Field

from .. import config
from .judge_packet import (PACKET_SCHEMA_VERSION, PARAMETER_WHITELIST,
                           collect_evidence_ids, packet_hash, packet_json)
from .llm import LocalLLM

REPORT_SCHEMA_VERSION = "judge-report-2"
LLM_TEMPERATURE = 0.1  # fixed in LocalLLM._chat; recorded for observability

ASSESSMENT_DIMENSIONS = ("coverage", "distribution", "clustering",
                         "utilization", "robustnessToUncertainty")

# selection/retention policy for judge-triggered reruns
RERUN_MIN_OBJECTIVE_GAIN = 0.02
RERUN_MAX_COMPONENT_DROP = 0.05

# deterministic confidence cap: the judge can never be more confident than
# what the verified share of hard regulatory checks supports
CONFIDENCE_CAP_BASE = 0.3
CONFIDENCE_CAP_SLOPE = 0.7
ROBUSTNESS_COVERAGE_THRESHOLD = 0.5

# LLM parameter-change reasons that are effectively no-ops → dropped in code
_NOOP_REASON_RE = re.compile(r"no change|не требует|не нужн", re.IGNORECASE)


class JudgePromptError(RuntimeError):
    """Raised when the configured judge prompt version cannot be loaded."""


# ---------------------------------------------------------------------------
# output schema v2 (grammar-enforced through LocalLLM.structured_generate)
# ---------------------------------------------------------------------------

Status = Literal["GOOD", "IMPROVABLE", "POOR"]


class Dimension(BaseModel):
    status: Status = "IMPROVABLE"
    evidence: list[str] = Field(default_factory=list)


class AssessmentV2(BaseModel):
    coverage: Dimension = Field(default_factory=Dimension)
    distribution: Dimension = Field(default_factory=Dimension)
    clustering: Dimension = Field(default_factory=Dimension)
    utilization: Dimension = Field(default_factory=Dimension)
    robustnessToUncertainty: Dimension = Field(default_factory=Dimension)


class IssueV2(BaseModel):
    type: str = ""
    severity: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    evidenceIds: list[str] = Field(default_factory=list)
    description: str = ""


class ParameterChangeV2(BaseModel):
    parameter: str
    currentValue: float = 0.0
    proposedValue: float = 0.0
    reason: str = ""
    evidenceIds: list[str] = Field(default_factory=list)


class JudgeResponseV2(BaseModel):
    decision: Literal["ACCEPT", "RERUN", "MANUAL_REVIEW"]
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    assessment: AssessmentV2 = Field(default_factory=AssessmentV2)
    issues: list[IssueV2] = Field(default_factory=list)
    parameterChanges: list[ParameterChangeV2] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# prompt loading (versioned files, never inline strings)
# ---------------------------------------------------------------------------

def load_judge_prompt(version: str | None = None) -> tuple[str, str]:
    """Returns (version, prompt_text). Version comes from JUDGE_PROMPT_VERSION
    (default config.JUDGE_PROMPT_VERSION_DEFAULT, currently "v2"); the file is
    prompts/judge/{version}.md at the repo root. A missing file is a hard,
    clearly-worded error — never a silent default."""
    version = version or os.environ.get(
        "JUDGE_PROMPT_VERSION", config.JUDGE_PROMPT_VERSION_DEFAULT)
    path = config.REPO_ROOT / "prompts" / "judge" / f"{version}.md"
    if not path.exists():
        raise JudgePromptError(
            f"judge prompt version '{version}' not found at {path} — "
            f"set JUDGE_PROMPT_VERSION to an existing prompts/judge/*.md version")
    return version, path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# post-validation: evidence stripping + parameter clamping
# ---------------------------------------------------------------------------

def sanitize_parameter_changes(changes: list, packet: dict) -> list[dict]:
    """Whitelist + absolute range + per-iteration max delta enforcement.

    Non-whitelisted parameters are dropped; proposals are clamped to the
    absolute [min, max] range and then to current ± maxDeltaPerIteration.
    No-op results (proposal == current) are dropped, as are changes whose
    stated reason is itself a no-op phrase ("no change", "не требует",
    "не нужно" …) — a sanity pass over LLM output."""
    allowed = packet.get("allowedParameterChanges", {})
    valid_ids = collect_evidence_ids(packet)
    out: list[dict] = []
    for ch in changes or []:
        d = ch.model_dump() if isinstance(ch, BaseModel) else dict(ch)
        name = d.get("parameter")
        if _NOOP_REASON_RE.search(str(d.get("reason") or "")):
            continue
        bounds = allowed.get(name) or PARAMETER_WHITELIST.get(name)
        if bounds is None:
            continue
        current = float(bounds.get("current", d.get("currentValue", 0.0) or 0.0))
        try:
            proposed = float(d.get("proposedValue"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(proposed):
            continue
        proposed = min(bounds["max"], max(bounds["min"], proposed))
        max_delta = bounds["maxDeltaPerIteration"]
        if abs(proposed - current) > max_delta:
            proposed = current + math.copysign(max_delta, proposed - current)
            proposed = min(bounds["max"], max(bounds["min"], proposed))
        if abs(proposed - current) < 1e-9:
            continue
        out.append({
            "parameter": name,
            "currentValue": round(current, 3),
            "proposedValue": round(proposed, 3),
            "reason": str(d.get("reason", ""))[:300],
            "evidenceIds": [e for e in (d.get("evidenceIds") or []) if e in valid_ids],
        })
    return out


def _sanitize_assessment(assessment: AssessmentV2, valid_ids: set[str]) -> dict:
    out = {}
    for dim in ASSESSMENT_DIMENSIONS:
        d: Dimension = getattr(assessment, dim)
        out[dim] = {"status": d.status,
                    "evidence": [e for e in d.evidence if e in valid_ids]}
    return out


def _sanitize_issues(issues: list[IssueV2], valid_ids: set[str]) -> list[dict]:
    out = []
    for i in issues[:6]:
        out.append({"type": i.type[:60], "severity": i.severity,
                    "evidenceIds": [e for e in i.evidenceIds if e in valid_ids],
                    "description": i.description[:300]})
    return out


# ---------------------------------------------------------------------------
# report assembly (v2, with backward-compatible keys for the current frontend)
# ---------------------------------------------------------------------------

def _apply_regulatory_bounds(confidence: float, assessment: dict,
                             issues: list[dict], packet: dict
                             ) -> tuple[float, dict, list[dict]]:
    """Deterministic post-processing of any judge output (LLM or heuristic):

    * confidence is capped at 0.3 + 0.7*regulatoryCoverage — the judge can
      never be more confident than the verified share of hard checks allows;
    * robustnessToUncertainty is at most IMPROVABLE when regulatoryCoverage
      < 0.5 or the site boundary was not detected;
    * if any assessment dimension != GOOD while issues == [], a deterministic
      issue is appended so the report never reads as flawless."""
    coverage = packet.get("regulatoryCoverage")
    parser = packet.get("parserCoverage") or {}
    if coverage is not None:
        confidence = min(float(confidence),
                         CONFIDENCE_CAP_BASE + CONFIDENCE_CAP_SLOPE * float(coverage))
    boundary_unknown = parser.get("siteBoundaryDetected") is False
    if ((coverage is not None and coverage < ROBUSTNESS_COVERAGE_THRESHOLD)
            or boundary_unknown):
        dim = assessment.get("robustnessToUncertainty")
        if dim and dim.get("status") == "GOOD":
            dim["status"] = "IMPROVABLE"
    if not issues:
        for name in ASSESSMENT_DIMENSIONS:
            dim = assessment.get(name) or {}
            status = dim.get("status", "GOOD")
            if status != "GOOD":
                issues = issues + [{
                    "type": "quality_dimension", "severity": "LOW",
                    "evidenceIds": list(dim.get("evidence") or []),
                    "description": f"{name} оценён как {status} — см. evidence",
                }]
    return confidence, assessment, issues


def _report(mode: str, decision: str, confidence: float, assessment: dict,
            issues: list[dict], parameter_changes: list[dict], summary: str,
            packet: dict, llm_model: str | None, prompt_version: str | None) -> dict:
    confidence, assessment, issues = _apply_regulatory_bounds(
        confidence, assessment, issues, packet)
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "mode": mode,
        "decision": decision,
        "confidence": round(min(1.0, max(0.0, float(confidence))), 3),
        "assessment": assessment,
        "issues": issues,
        "parameterChanges": parameter_changes,
        "summary": summary[:2000],
        # deterministic component scores computed in judge_packet.py — the UI
        # summary score derives from these, never from an LLM-invented number
        "deterministicComponents": packet.get("qualityComponents", {}),
        # backward-compatible keys used by the current frontend
        "reasoningSummary": summary[:2000],
        "recommendedParameterChanges": {c["parameter"]: c["proposedValue"]
                                        for c in parameter_changes},
        "llmModel": llm_model,
        # observability
        "model": llm_model,
        "promptVersion": prompt_version,
        "temperature": LLM_TEMPERATURE if mode == "llm" else None,
        "inputSchemaVersion": PACKET_SCHEMA_VERSION,
        "judgeInputHash": packet_hash(packet),
    }


# ---------------------------------------------------------------------------
# heuristic fallback (LLM unreachable) — same v2 schema, real evidence ids
# ---------------------------------------------------------------------------

def heuristic_judge(packet: dict, iteration: int, max_iterations: int) -> dict:
    plan = packet.get("plan", {})
    diag = packet.get("spatialDiagnostics", {})
    coverage = float(plan.get("canopyCoverage", 0.0))
    distribution = float(plan.get("distributionUniformity", 0.0))
    cluster_ids = [c["id"] for c in diag.get("clusters", [])]
    underused_ids = [z["id"] for z in diag.get("underusedZones", [])]
    uncertain_ids = [u["id"] for u in packet.get("classificationUncertainty", [])]
    comps = packet.get("qualityComponents", {})

    def status(good: bool, poor: bool) -> str:
        return "POOR" if poor else ("GOOD" if good else "IMPROVABLE")

    assessment = {
        "coverage": {"status": status(coverage >= 0.15, coverage < 0.05), "evidence": []},
        "distribution": {"status": status(distribution >= 0.7, distribution < 0.35),
                         "evidence": underused_ids[:3]},
        "clustering": {"status": status(not cluster_ids, len(cluster_ids) >= 3),
                       "evidence": cluster_ids[:3]},
        "utilization": {"status": status(not underused_ids, len(underused_ids) >= 3),
                        "evidence": underused_ids[:3]},
        "robustnessToUncertainty": {"status": "GOOD" if not uncertain_ids else "IMPROVABLE",
                                    "evidence": uncertain_ids[:3]},
    }

    issues: list[dict] = []
    raw_changes: list[dict] = []
    if distribution < 0.5:
        issues.append({"type": "uneven_distribution", "severity": "MEDIUM",
                       "evidenceIds": underused_ids[:3],
                       "description": f"Distribution uniformity is low ({distribution:.0%}); "
                                      "parts of the allowed area are underused."})
        raw_changes.append({"parameter": "distributionWeight", "proposedValue": 1.0,
                            "reason": "raise distribution weight to fill underused zones",
                            "evidenceIds": underused_ids[:3]})
    if coverage < 0.08:
        issues.append({"type": "low_coverage", "severity": "MEDIUM",
                       "evidenceIds": underused_ids[:2],
                       "description": f"Canopy coverage is low ({coverage:.0%})."})
        raw_changes.append({"parameter": "candidateDensity", "proposedValue": 3.0,
                            "reason": "denser candidate sampling for more canopy",
                            "evidenceIds": underused_ids[:2]})
        raw_changes.append({"parameter": "coverageWeight", "proposedValue": 1.0,
                            "reason": "prioritize canopy contribution",
                            "evidenceIds": []})
    if cluster_ids:
        issues.append({"type": "clustering", "severity": "LOW",
                       "evidenceIds": cluster_ids[:3],
                       "description": f"{len(cluster_ids)} dense planting clusters detected."})

    changes = sanitize_parameter_changes(raw_changes, packet)
    rerun_worthy = any(i["severity"] in ("MEDIUM", "HIGH") for i in issues)
    decision = "RERUN" if (rerun_worthy and changes and iteration + 1 < max_iterations) \
        else "ACCEPT"
    summary = ("Local LLM unavailable — deterministic heuristic evaluation "
               "(coverage/distribution/cluster thresholds). "
               + "; ".join(i["description"] for i in issues[:3]))
    if decision == "ACCEPT":
        changes = []  # heuristic proposes changes only when it actually reruns
    report = _report("heuristic_fallback", decision, 0.5, assessment, issues,
                     changes, summary, packet, llm_model=None, prompt_version=None)
    report["deterministicComponents"] = comps
    return report


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------

async def judge_iteration(llm: LocalLLM, packet: dict, iteration: int,
                          max_iterations: int) -> dict:
    """Judge one iteration from its deterministic packet.

    Precondition (enforced by the pipeline): the plan is valid — the packet's
    validation.hardViolations is 0. This function never sees invalid plans."""
    if not (llm.enabled and await llm.is_reachable()):
        return heuristic_judge(packet, iteration, max_iterations)

    prompt_version, system_prompt = load_judge_prompt()
    try:
        resp = await llm.structured_generate(JudgeResponseV2, [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "JudgePacket:\n" + packet_json(packet)},
        ])
    except Exception:
        return heuristic_judge(packet, iteration, max_iterations)

    valid_ids = collect_evidence_ids(packet)
    decision = resp.decision
    if decision == "RERUN" and iteration + 1 >= max_iterations:
        decision = "ACCEPT"  # no rerun budget left; plan is already compliant
    changes = sanitize_parameter_changes(resp.parameterChanges, packet)
    # ACCEPT with parameter changes is allowed — the changes stay in the report
    # as informational; the pipeline only applies them on RERUN.
    return _report(
        "llm", decision,
        resp.confidence,
        _sanitize_assessment(resp.assessment, valid_ids),
        _sanitize_issues(resp.issues, valid_ids),
        changes, resp.summary, packet,
        llm_model=llm.model or "unknown-local",
        prompt_version=prompt_version,
    )


# ---------------------------------------------------------------------------
# rerun retention policy (used by the pipeline when picking the selected
# iteration): a judge-triggered rerun replaces the current selection ONLY if
# it is deterministically valid, the objective improves by ≥ 0.02 and neither
# component drops by more than 0.05.
# ---------------------------------------------------------------------------

def _objective(metrics: dict) -> float:
    return 0.5 * float(metrics.get("coverageScore", 0.0)) + \
           0.5 * float(metrics.get("distributionScore", 0.0))


def should_select_rerun(current_metrics: dict, current_valid: bool,
                        candidate_metrics: dict, candidate_valid: bool) -> bool:
    if not candidate_valid:
        return False
    if not current_valid:
        return True  # any valid plan beats an invalid selection
    if _objective(candidate_metrics) - _objective(current_metrics) < RERUN_MIN_OBJECTIVE_GAIN:
        return False
    for key in ("coverageScore", "distributionScore"):
        if float(current_metrics.get(key, 0.0)) - float(candidate_metrics.get(key, 0.0)) \
                > RERUN_MAX_COMPONENT_DROP:
            return False
    return True
