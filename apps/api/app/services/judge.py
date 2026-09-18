"""LLM judge — evaluates SOFT solution quality and may recommend safe
parameter changes for a rerun. It never sees raw CAD data (only metrics,
constraint summaries and compact chunk digests) and can NEVER override the
deterministic validator: a plan with hard violations is unacceptable
regardless of the judge's opinion.

When the local LLM is disabled or unreachable, a clearly-labeled deterministic
heuristic produces the judge report (mode="heuristic_fallback") so the
pipeline stays functional. No cloud fallback exists by design.
"""
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field

from .llm import LocalLLM, LocalLLMError
from .optimizer import OptimizerParams

SAFE_BOUNDS = {
    "candidateDensity": (0.3, 3.0),
    "spacingWeight": (0.0, 1.0),
    "coverageWeight": (0.0, 1.0),
    "distributionWeight": (0.0, 1.0),
    "streetWeight": (0.0, 1.0),
}


class JudgeScore(BaseModel):
    overall: float = 0.0
    coverage: float = 0.0
    distribution: float = 0.0
    confidence: float = 0.0


class JudgeResponse(BaseModel):
    decision: str = Field(pattern="^(ACCEPT|RERUN)$")
    score: JudgeScore = Field(default_factory=JudgeScore)
    issues: list[str] = Field(default_factory=list)
    recommendedParameterChanges: dict[str, Optional[float]] = Field(default_factory=dict)
    reasoningSummary: str = ""


def clamp_changes(changes: dict[str, Optional[float]]) -> dict[str, float]:
    """Only whitelisted parameters, clamped to safe bounds."""
    out: dict[str, float] = {}
    for k, v in (changes or {}).items():
        if v is None or k not in SAFE_BOUNDS:
            continue
        lo, hi = SAFE_BOUNDS[k]
        try:
            out[k] = min(hi, max(lo, float(v)))
        except (TypeError, ValueError):
            continue
    return out


def _heuristic_judge(metrics: dict, validation: dict, iteration: int,
                     max_iterations: int, params: OptimizerParams) -> dict:
    """Deterministic fallback when no local LLM is reachable. Clearly labeled."""
    coverage = float(metrics.get("coverageScore", 0))
    distribution = float(metrics.get("distributionScore", 0))
    issues: list[str] = []
    changes: dict[str, float] = {}
    decision = "ACCEPT"

    if not validation.get("valid", False):
        issues.append(f"{len(validation.get('hardViolations', []))} hard violations — "
                      "deterministic validator forbids acceptance")
        decision = "RERUN"
        changes["candidateDensity"] = max(0.3, params.candidateDensity * 0.8)
    if distribution < 0.5 and iteration + 1 < max_iterations:
        issues.append(f"Spatial distribution is low ({distribution:.0%}) — parts of the "
                      "allowed area are underused")
        decision = "RERUN"
        changes["distributionWeight"] = min(1.0, params.distributionWeight + 0.25)
    if coverage < 0.08 and iteration + 1 < max_iterations:
        issues.append(f"Canopy coverage is low ({coverage:.0%})")
        decision = "RERUN"
        changes["candidateDensity"] = min(3.0, params.candidateDensity + 0.4)
        changes["coverageWeight"] = min(1.0, params.coverageWeight + 0.2)

    overall = round(min(1.0, 0.5 * coverage * 4 + 0.5 * distribution), 3)
    return {
        "mode": "heuristic_fallback",
        "decision": decision if iteration + 1 < max_iterations else "ACCEPT",
        "score": {"overall": overall, "coverage": round(coverage, 3),
                  "distribution": round(distribution, 3), "confidence": 0.5},
        "issues": issues,
        "recommendedParameterChanges": changes,
        "reasoningSummary": "Local LLM unavailable — deterministic heuristic evaluation "
                            "(coverage/distribution thresholds). " + "; ".join(issues[:3]),
        "llmModel": None,
    }


async def judge_iteration(llm: LocalLLM, objective: str, params: OptimizerParams,
                          metrics: dict, validation: dict, constraint_summary: dict,
                          chunk_digest: list[dict], iteration: int,
                          max_iterations: int) -> dict:
    if not (llm.enabled and await llm.is_reachable()):
        return _heuristic_judge(metrics, validation, iteration, max_iterations, params)

    # keep the context tight: small local models process prompts slowly and
    # never need raw geometry — only aggregates
    def _r(v):
        return round(v, 2) if isinstance(v, float) else v

    compact_chunks = [{"bbox": [int(b) for b in c["bbox"]],
                       "counts": {k: v for k, v in c["summary"].items() if v}}
                      for c in (chunk_digest or [])[:4]]
    context = {
        "objective": objective,
        "iteration": iteration,
        "maxIterations": max_iterations,
        "params": {k: _r(v) for k, v in params.to_api().items()
                   if k != "strategy" and v is not None},
        "metrics": {k: _r(v) for k, v in metrics.items() if v is not None},
        "validator": {
            "valid": validation.get("valid"),
            "hardViolationCount": len(validation.get("hardViolations", [])),
        },
        "constraints": {
            "exclusionZones": constraint_summary.get("exclusionZoneCount"),
            "allowedAreaM2": constraint_summary.get("allowedAreaM2"),
            "inactiveRuleCount": len(constraint_summary.get("inactiveRules", [])),
        },
        "chunks": compact_chunks,
    }
    system = (
        "You are a landscape-planning QA judge evaluating SOFT quality of a planting plan "
        "(distribution, underused areas, clustering, coverage). No geometry math, no legal "
        "citations. A deterministic validator already decided compliance: if hardViolationCount>0 "
        "you must not ACCEPT. RERUN only if another iteration can plausibly improve the plan. "
        "Allowed parameter changes: candidateDensity [0.3..3]; spacingWeight, coverageWeight, "
        "distributionWeight, streetWeight [0..1]. Be terse: max 3 short issues, "
        "reasoningSummary under 40 words."
    )
    try:
        resp = await llm.structured_generate(JudgeResponse, [
            {"role": "system", "content": system},
            {"role": "user", "content": "Evaluate this iteration:\n" +
             json.dumps(context, ensure_ascii=False)},
        ])
    except (LocalLLMError, Exception):
        return _heuristic_judge(metrics, validation, iteration, max_iterations, params)

    decision = resp.decision
    # Deterministic authority: LLM may never accept an invalid plan.
    if not validation.get("valid", False) and decision == "ACCEPT":
        decision = "RERUN"
        resp.issues.append("Overridden: deterministic validator reports hard violations; "
                           "ACCEPT is not allowed.")
    if iteration + 1 >= max_iterations:
        decision = "ACCEPT" if validation.get("valid", False) else decision

    return {
        "mode": "llm",
        "decision": decision,
        "score": resp.score.model_dump(),
        "issues": resp.issues,
        "recommendedParameterChanges": clamp_changes(resp.recommendedParameterChanges),
        "reasoningSummary": resp.reasoningSummary[:2000],
        "llmModel": llm.model or "unknown-local",
    }
