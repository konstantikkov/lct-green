"""Judge-benchmark scorer + runners.

Modes:
  --mock  deterministic stub judge (CI default; no model involved)
  --live  local LLM via apps/api LocalLLM, if reachable (never cloud)

Metrics vs bootstrap labels: accept accuracy, rerun precision, manual-review
recall, parameter-change validity (whitelist + min/max + maxDelta), overall
agreement.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Optional

from evals.judge import CASES_PATH, LABELS_PATH

DECISIONS = ("ACCEPT", "RERUN", "MANUAL_REVIEW")
_EPS = 1e-9
UNCERTAINTY_THRESHOLD = 0.55
UNDERUSED_FRACTION_THRESHOLD = 0.25
LOW_DISTRIBUTION = 0.5
LOW_COVERAGE = 0.08


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ---------------------------------------------------------------------------
# judges
# ---------------------------------------------------------------------------

def _clamped_proposal(packet: dict, param: str, delta: float) -> Optional[float]:
    bounds = (packet.get("allowedParameterChanges") or {}).get(param)
    if bounds is None:  # not whitelisted -> never propose
        return None
    cur = float((packet.get("params") or {}).get(param, 0.0))
    step = max(-float(bounds["maxDelta"]), min(float(bounds["maxDelta"]), delta))
    new = max(float(bounds["min"]), min(float(bounds["max"]), cur + step))
    if abs(new - cur) < _EPS:
        return None
    return round(new, 4)


def mock_judge(packet: dict) -> dict:
    """Deterministic stub judge for CI: threshold rules over the packet only.
    Mirrors the intended decision policy (uncertainty -> MANUAL_REVIEW,
    underused/low-distribution/low-coverage -> RERUN, else ACCEPT); all
    parameter proposals are clamped to the packet's allowed bounds."""
    evidence: list[str] = []
    uncertain = [u for u in packet.get("classificationUncertainty", [])
                 if float(u.get("confidence", 1.0)) < UNCERTAINTY_THRESHOLD]
    if uncertain:
        evidence = [f"layer {u['layer']} confidence {u['confidence']}" for u in uncertain]
        return {"decision": "MANUAL_REVIEW", "parameterChanges": {}, "evidence": evidence}

    pm = packet.get("planMetrics", {})
    sd = packet.get("spatialDiagnostics", {})
    area = float(packet.get("allowedAreaM2") or 1.0)
    under = sd.get("underusedZones", [])
    under_frac = sum(float(z.get("areaM2", 0.0)) for z in under) / area
    dist = float(pm.get("distributionScore", 1.0))
    cov = float(pm.get("coverageScore", 1.0))

    changes: dict[str, float] = {}
    if under_frac > UNDERUSED_FRACTION_THRESHOLD or dist < LOW_DISTRIBUTION:
        evidence.append(f"underused fraction {under_frac:.2f}, distribution {dist:.2f}")
        evidence.extend(f"zone {z['id']}" for z in under[:3])
        v = _clamped_proposal(packet, "distributionWeight", +0.25)
        if v is not None:
            changes["distributionWeight"] = v
    if cov < LOW_COVERAGE:
        evidence.append(f"coverage {cov:.2f} below {LOW_COVERAGE}")
        v = _clamped_proposal(packet, "candidateDensity", +0.4)
        if v is not None:
            changes["candidateDensity"] = v
        v = _clamped_proposal(packet, "coverageWeight", +0.2)
        if v is not None:
            changes["coverageWeight"] = v
    if evidence:
        return {"decision": "RERUN", "parameterChanges": changes, "evidence": evidence}
    return {"decision": "ACCEPT", "parameterChanges": {},
            "evidence": [f"coverage {cov:.2f}", f"distribution {dist:.2f}"]}


def live_judge_factory() -> Optional[Callable[[dict], dict]]:
    """Local LLM judge (apps/api LocalLLM) if configured and reachable."""
    try:
        import asyncio

        from pydantic import BaseModel, Field

        from app.services.llm import LocalLLM

        llm = LocalLLM()
        if not (llm.enabled and asyncio.run(llm.is_reachable())):
            return None

        class BenchDecision(BaseModel):
            decision: str = Field(pattern="^(ACCEPT|RERUN|MANUAL_REVIEW)$")
            parameterChanges: dict[str, float] = Field(default_factory=dict)
            reasoning: str = ""

        system = (
            "You are a landscape-plan QA judge. Decide ACCEPT, RERUN or "
            "MANUAL_REVIEW for the given packet. MANUAL_REVIEW when layer "
            "classification is uncertain; RERUN when large allowed sub-zones "
            "are underused or coverage/distribution are clearly low. Any "
            "parameterChanges must respect allowedParameterChanges "
            "(min/max/maxDelta). JSON only.")

        def judge(packet: dict) -> dict:
            resp = asyncio.run(llm.structured_generate(BenchDecision, [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
            ]))
            return {"decision": resp.decision,
                    "parameterChanges": dict(resp.parameterChanges),
                    "evidence": [resp.reasoning[:300]]}
        return judge
    except Exception:
        return None


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def validate_changes(packet: dict, changes: dict) -> tuple[bool, list[str]]:
    """Whitelist + min/max + maxDelta validity of proposed parameter changes."""
    problems = []
    allowed = packet.get("allowedParameterChanges") or {}
    params = packet.get("params") or {}
    for k, v in (changes or {}).items():
        if k not in allowed:
            problems.append(f"{k}: not in whitelist")
            continue
        b = allowed[k]
        try:
            v = float(v)
        except (TypeError, ValueError):
            problems.append(f"{k}: non-numeric")
            continue
        if not (float(b["min"]) - _EPS <= v <= float(b["max"]) + _EPS):
            problems.append(f"{k}: {v} outside [{b['min']}, {b['max']}]")
        cur = float(params.get(k, 0.0))
        if abs(v - cur) > float(b["maxDelta"]) + _EPS:
            problems.append(f"{k}: |{v}-{cur}| exceeds maxDelta {b['maxDelta']}")
    return not problems, problems


def _ratio(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def score_outputs(cases: list[dict], labels: list[dict],
                  outputs: dict[str, dict]) -> dict:
    by_label = {l["caseId"]: l for l in labels}
    by_case = {c["caseId"]: c for c in cases}

    agree = 0
    accept_hits = accept_total = 0
    rerun_said = rerun_said_right = 0
    review_total = review_hits = 0
    prop_total = prop_valid = 0
    per_case = []

    for cid, out in outputs.items():
        label = by_label.get(cid)
        case = by_case.get(cid)
        if label is None or case is None:
            continue
        expected = label["expectedDecision"]
        got = out.get("decision", "")
        ok = got == expected
        agree += ok
        if expected == "ACCEPT":
            accept_total += 1
            accept_hits += ok
        if got == "RERUN":
            rerun_said += 1
            rerun_said_right += expected == "RERUN"
        if expected == "MANUAL_REVIEW":
            review_total += 1
            review_hits += got == "MANUAL_REVIEW"
        changes = out.get("parameterChanges") or {}
        valid = True
        problems: list[str] = []
        if changes:
            prop_total += 1
            valid, problems = validate_changes(case["packet"], changes)
            prop_valid += valid
        per_case.append({"caseId": cid, "expected": expected, "decision": got,
                         "agrees": ok, "parameterChanges": changes,
                         "changesValid": valid, "changeProblems": problems})

    n = len(per_case)
    return {
        "cases": n,
        "acceptAccuracy": _ratio(accept_hits, accept_total),
        "rerunPrecision": _ratio(rerun_said_right, rerun_said),
        "manualReviewRecall": _ratio(review_hits, review_total),
        "parameterChangeValidity": _ratio(prop_valid, prop_total) if prop_total else 1.0,
        "overallAgreement": _ratio(agree, n),
        "perCase": per_case,
    }


def run_benchmark(mode: str = "mock",
                  cases_path: Path = CASES_PATH,
                  labels_path: Path = LABELS_PATH,
                  judge_fn: Callable[[dict], dict] | None = None) -> dict:
    cases = load_jsonl(cases_path)
    labels = load_jsonl(labels_path)
    if judge_fn is None:
        if mode == "mock":
            judge_fn = mock_judge
        elif mode == "live":
            judge_fn = live_judge_factory()
            if judge_fn is None:
                return {"mode": "live", "status": "SKIPPED",
                        "reason": "local LLM not reachable/configured "
                                  "(LOCAL_LLM_BASE_URL/LOCAL_LLM_MODEL)"}
        else:
            raise ValueError(f"unknown mode: {mode}")
    outputs = {c["caseId"]: judge_fn(c["packet"]) for c in cases}
    result = score_outputs(cases, labels, outputs)
    result.update({"mode": mode, "status": "RAN",
                   "labelProvenance": "engineering-bootstrap (not expert ground truth)"})
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GreenPlan judge benchmark")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--mock", action="store_true", default=True)
    g.add_argument("--live", action="store_true")
    args = ap.parse_args(argv)
    res = run_benchmark("live" if args.live else "mock")
    print(json.dumps({k: v for k, v in res.items() if k != "perCase"},
                     ensure_ascii=False, indent=2))
    if res.get("status") == "RAN":
        for pc in res["perCase"]:
            mark = "ok " if pc["agrees"] else "DIFF"
            print(f"  [{mark}] {pc['caseId']}: {pc['decision']} "
                  f"(expected {pc['expected']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
