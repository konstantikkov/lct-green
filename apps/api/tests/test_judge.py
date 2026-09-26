"""Judge v2 tests: packet construction, evidence stripping, parameter
clamping, judge precondition (deterministic repair / skip), heuristic
fallback schema, rerun retention rule and prompt versioning.

No network anywhere: the local LLM is either disabled or monkeypatched.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from app.services import judge as judge_mod  # noqa: E402
from app.services.judge import (JudgeResponseV2, AssessmentV2, Dimension,  # noqa: E402
                                IssueV2, ParameterChangeV2, JudgePromptError,
                                heuristic_judge, judge_iteration,
                                load_judge_prompt, sanitize_parameter_changes,
                                should_select_rerun)
from app.services.judge_packet import (PACKET_SCHEMA_VERSION,  # noqa: E402
                                       PARAMETER_WHITELIST, build_judge_packet,
                                       collect_evidence_ids, packet_hash,
                                       packet_json)
from app.services.llm import LocalLLM  # noqa: E402
from app.services.optimizer import OptimizerParams  # noqa: E402
from app.services.validator import Placement, validate_plan  # noqa: E402

from .test_integration import FIXTURE, _run_pipeline  # noqa: E402


@pytest.fixture(scope="module")
def pipe():
    return _run_pipeline()


@pytest.fixture(scope="module")
def packet(pipe):
    doc, idx, registry, cons, placements, validation = pipe
    params = OptimizerParams.for_strategy("balanced")
    return build_judge_packet(
        objective="balanced", iteration=0, max_iterations=3, params=params,
        validation=validation, allowed_area=cons.allowed_area,
        placements=placements, spacing_m=5.0,
        classification_uncertainty=[
            {"name": l.name, "category": l.classification.category,
             "confidence": l.classification.confidence}
            for l in doc.layers if l.classification.requiresReview])


# ---------------------------------------------------------------------------
# packet construction
# ---------------------------------------------------------------------------

def test_packet_construction(packet, pipe):
    _, _, _, _, placements, validation = pipe
    assert packet["schemaVersion"] == PACKET_SCHEMA_VERSION
    assert packet["validation"]["hardViolations"] == 0

    diag = packet["spatialDiagnostics"]
    quadrants = diag["quadrants"]
    assert quadrants and len(quadrants) <= 8
    for q in quadrants:
        assert re.fullmatch(r"q_[0-3]_[0-3]", q["id"])
        assert q["allowedM2"] > 0
        assert 0 <= q["plantings"] <= len(placements)
        assert q["densityPerHa"] >= 0
    for z in diag["underusedZones"]:
        assert re.fullmatch(r"under_q_[0-3]_[0-3]", z["id"])
    for c in diag["clusters"]:
        assert re.fullmatch(r"cluster_\d+", c["id"])
        assert c["size"] >= 5
    assert 0.0 <= diag["edgeDensity"] <= 1.0
    assert len(diag["clusters"]) <= 5 and len(diag["underusedZones"]) <= 5

    # whitelist: exactly the safe params, each fully specified
    apc = packet["allowedParameterChanges"]
    assert set(apc) == {"distributionWeight", "coverageWeight", "spacingWeight",
                        "streetWeight", "candidateDensity"}
    for name, b in apc.items():
        assert set(b) == {"min", "max", "maxDeltaPerIteration", "current"}
        assert b["min"] <= b["current"] <= b["max"]
    assert apc["candidateDensity"]["maxDeltaPerIteration"] == 0.6
    assert apc["distributionWeight"]["maxDeltaPerIteration"] == 0.25

    # deterministic components (no LLM), all in [0, 1]
    comps = packet["qualityComponents"]
    assert set(comps) == {"coverage", "distribution", "utilizationBalance", "clusterPenalty"}
    for v in comps.values():
        assert 0.0 <= v <= 1.0
    assert comps["coverage"] == round(validation["metrics"]["coverageScore"], 3)

    # compact: stays well under the ~700-token budget (≈4 chars/token)
    assert len(packet_json(packet)) < 3000
    # hash is deterministic and content-addressed
    assert packet_hash(packet) == packet_hash(packet)
    assert re.fullmatch(r"[0-9a-f]{64}", packet_hash(packet))


def test_collect_evidence_ids(packet):
    ids = collect_evidence_ids(packet)
    diag = packet["spatialDiagnostics"]
    for item in diag["quadrants"] + diag["clusters"] + diag["underusedZones"]:
        assert item["id"] in ids
    for u in packet["classificationUncertainty"]:
        assert u["id"] in ids


# ---------------------------------------------------------------------------
# post-validation: evidence stripping + parameter clamping
# ---------------------------------------------------------------------------

def test_parameter_clamping(packet):
    apc = packet["allowedParameterChanges"]
    cur_dw = apc["distributionWeight"]["current"]     # 0.4 for "balanced"
    cur_cd = apc["candidateDensity"]["current"]       # 1.0
    some_id = packet["spatialDiagnostics"]["quadrants"][0]["id"]
    changes = [
        {"parameter": "distributionWeight", "proposedValue": 0.99,   # delta > 0.25
         "reason": "spread", "evidenceIds": [some_id, "bogus_id"]},
        {"parameter": "candidateDensity", "proposedValue": 99.0,     # out of range
         "reason": "denser", "evidenceIds": []},
        {"parameter": "seed", "proposedValue": 7, "reason": "hack"},  # not whitelisted
        {"parameter": "coverageWeight",
         "proposedValue": apc["coverageWeight"]["current"], "reason": "noop"},
        {"parameter": "spacingWeight", "proposedValue": "NaN", "reason": "junk"},
    ]
    out = sanitize_parameter_changes(changes, packet)
    by_name = {c["parameter"]: c for c in out}
    assert set(by_name) == {"distributionWeight", "candidateDensity"}
    # clamped to current + maxDeltaPerIteration
    assert by_name["distributionWeight"]["proposedValue"] == pytest.approx(cur_dw + 0.25)
    assert by_name["candidateDensity"]["proposedValue"] == pytest.approx(
        min(3.0, cur_cd + 0.6))
    # unknown evidence ids stripped, known kept
    assert by_name["distributionWeight"]["evidenceIds"] == [some_id]
    assert by_name["distributionWeight"]["currentValue"] == pytest.approx(cur_dw)


def test_llm_response_evidence_stripping(packet, monkeypatch):
    valid_id = packet["spatialDiagnostics"]["quadrants"][0]["id"]

    async def fake_reachable(self):
        return True

    async def fake_generate(self, schema, messages):
        return JudgeResponseV2(
            decision="RERUN", confidence=0.9,
            assessment=AssessmentV2(
                coverage=Dimension(status="GOOD", evidence=[valid_id, "made_up_7"]),
                distribution=Dimension(status="POOR", evidence=["zone_fake"])),
            issues=[IssueV2(type="uneven", severity="MEDIUM",
                            evidenceIds=[valid_id, "invented"], description="d")],
            parameterChanges=[
                ParameterChangeV2(parameter="distributionWeight", currentValue=0.4,
                                  proposedValue=0.9, reason="r",
                                  evidenceIds=["nonsense"]),
                ParameterChangeV2(parameter="notAParam", proposedValue=1.0)],
            summary="short summary")

    monkeypatch.setattr(LocalLLM, "is_reachable", fake_reachable)
    monkeypatch.setattr(LocalLLM, "structured_generate", fake_generate)
    report = asyncio.run(judge_iteration(LocalLLM(enabled=True), packet, 0, 3))

    assert report["mode"] == "llm"
    assert report["decision"] == "RERUN"
    assert report["assessment"]["coverage"]["evidence"] == [valid_id]
    assert report["assessment"]["distribution"]["evidence"] == []
    assert report["issues"][0]["evidenceIds"] == [valid_id]
    assert [c["parameter"] for c in report["parameterChanges"]] == ["distributionWeight"]
    # observability of every judgment
    assert report["promptVersion"] == "v2"
    assert report["inputSchemaVersion"] == PACKET_SCHEMA_VERSION
    assert report["judgeInputHash"] == packet_hash(packet)
    assert report["temperature"] == 0.1
    # backward-compatible keys, and no LLM-invented overall score
    assert report["reasoningSummary"] == report["summary"]
    assert "score" not in report
    assert report["recommendedParameterChanges"]["distributionWeight"] == pytest.approx(
        packet["allowedParameterChanges"]["distributionWeight"]["current"] + 0.25)


def test_llm_rerun_on_last_iteration_becomes_accept(packet, monkeypatch):
    async def fake_reachable(self):
        return True

    async def fake_generate(self, schema, messages):
        return JudgeResponseV2(decision="RERUN", summary="wants rerun")

    monkeypatch.setattr(LocalLLM, "is_reachable", fake_reachable)
    monkeypatch.setattr(LocalLLM, "structured_generate", fake_generate)
    report = asyncio.run(judge_iteration(LocalLLM(enabled=True), packet, 2, 3))
    assert report["decision"] == "ACCEPT"


def test_manual_review_passes_through(packet, monkeypatch):
    async def fake_reachable(self):
        return True

    async def fake_generate(self, schema, messages):
        return JudgeResponseV2(decision="MANUAL_REVIEW", confidence=0.3,
                               summary="classification uncertainty too high")

    monkeypatch.setattr(LocalLLM, "is_reachable", fake_reachable)
    monkeypatch.setattr(LocalLLM, "structured_generate", fake_generate)
    report = asyncio.run(judge_iteration(LocalLLM(enabled=True), packet, 0, 3))
    assert report["decision"] == "MANUAL_REVIEW"


# ---------------------------------------------------------------------------
# heuristic fallback (LLM disabled/unreachable) — must emit schema v2
# ---------------------------------------------------------------------------

def test_heuristic_fallback_v2_schema(packet):
    report = asyncio.run(judge_iteration(LocalLLM(enabled=False), packet, 0, 3))
    assert report["mode"] == "heuristic_fallback"
    assert report["llmModel"] is None
    assert report["schemaVersion"] == "judge-report-2"
    assert report["decision"] in ("ACCEPT", "RERUN", "MANUAL_REVIEW")
    assert 0.0 <= report["confidence"] <= 1.0
    assert set(report["assessment"]) == {"coverage", "distribution", "clustering",
                                         "utilization", "robustnessToUncertainty"}
    valid_ids = collect_evidence_ids(packet)
    for dim in report["assessment"].values():
        assert dim["status"] in ("GOOD", "IMPROVABLE", "POOR")
        assert set(dim["evidence"]) <= valid_ids
    for issue in report["issues"]:
        assert issue["severity"] in ("LOW", "MEDIUM", "HIGH")
        assert set(issue["evidenceIds"]) <= valid_ids
    for ch in report["parameterChanges"]:
        assert ch["parameter"] in PARAMETER_WHITELIST
    assert "score" not in report
    assert report["deterministicComponents"] == packet["qualityComponents"]
    assert report["judgeInputHash"] == packet_hash(packet)
    assert report["inputSchemaVersion"] == PACKET_SCHEMA_VERSION


def test_heuristic_evidence_ids_are_real(packet):
    # force the RERUN branch: weak metrics but identical diagnostics
    weak = {**packet, "plan": {**packet["plan"],
                               "canopyCoverage": 0.02, "distributionUniformity": 0.2}}
    report = heuristic_judge(weak, iteration=0, max_iterations=3)
    assert report["decision"] == "RERUN"
    assert report["parameterChanges"], "rerun must carry parameter changes"
    valid_ids = collect_evidence_ids(weak)
    for ch in report["parameterChanges"]:
        assert set(ch["evidenceIds"]) <= valid_ids
        b = PARAMETER_WHITELIST[ch["parameter"]]
        assert abs(ch["proposedValue"] - ch["currentValue"]) <= b["maxDeltaPerIteration"] + 1e-9
        assert b["min"] <= ch["proposedValue"] <= b["max"]


# ---------------------------------------------------------------------------
# regulatory separation: confidence cap + sanity pass (iteration 5)
# ---------------------------------------------------------------------------

def _packet_with_regulatory(packet, coverage, boundary_detected=True):
    p = dict(packet)
    p["regulatoryCoverage"] = coverage
    p["parserCoverage"] = {"siteBoundaryDetected": boundary_detected,
                           "utilitiesTypedShare": 1.0, "roadsClassified": True,
                           "existingVegetationDetected": True,
                           "crossingsDetected": 0}
    return p


def _llm_report(monkeypatch, packet, response):
    async def fake_reachable(self):
        return True

    async def fake_generate(self, schema, messages):
        return response

    monkeypatch.setattr(LocalLLM, "is_reachable", fake_reachable)
    monkeypatch.setattr(LocalLLM, "structured_generate", fake_generate)
    return asyncio.run(judge_iteration(LocalLLM(enabled=True), packet, 0, 3))


def test_confidence_capped_by_regulatory_coverage(packet, monkeypatch):
    p = _packet_with_regulatory(packet, coverage=0.2)
    report = _llm_report(monkeypatch, p, JudgeResponseV2(
        decision="ACCEPT", confidence=0.95, summary="great"))
    # cap = 0.3 + 0.7*0.2 = 0.44 — the LLM can never sound more sure than
    # the verified share of hard checks supports
    assert report["confidence"] == pytest.approx(0.44)

    p2 = _packet_with_regulatory(packet, coverage=1.0)
    report2 = _llm_report(monkeypatch, p2, JudgeResponseV2(
        decision="ACCEPT", confidence=0.95, summary="great"))
    assert report2["confidence"] == pytest.approx(0.95)


def test_noop_parameter_change_reasons_dropped(packet):
    apc = packet["allowedParameterChanges"]
    changes = [
        {"parameter": "distributionWeight",
         "proposedValue": apc["distributionWeight"]["current"] + 0.2,
         "reason": "the plan не требует изменений", "evidenceIds": []},
        {"parameter": "coverageWeight",
         "proposedValue": apc["coverageWeight"]["current"] + 0.2,
         "reason": "No change needed here", "evidenceIds": []},
        {"parameter": "candidateDensity",
         "proposedValue": apc["candidateDensity"]["current"] + 0.5,
         "reason": "denser sampling fills the north", "evidenceIds": []},
    ]
    out = sanitize_parameter_changes(changes, packet)
    assert [c["parameter"] for c in out] == ["candidateDensity"]


def test_deterministic_issue_when_dim_not_good_and_no_issues(packet, monkeypatch):
    report = _llm_report(monkeypatch, packet, JudgeResponseV2(
        decision="ACCEPT", confidence=0.5,
        assessment=AssessmentV2(distribution=Dimension(status="IMPROVABLE")),
        issues=[], summary="fine"))
    assert report["issues"], "non-GOOD dimension with empty issues must gain one"
    issue = report["issues"][0]
    assert issue["type"] == "quality_dimension"
    assert issue["severity"] == "LOW"
    assert "IMPROVABLE" in issue["description"]


def test_robustness_bounded_by_coverage_and_boundary(packet, monkeypatch):
    good = AssessmentV2(robustnessToUncertainty=Dimension(status="GOOD"))
    # low coverage → at most IMPROVABLE
    p = _packet_with_regulatory(packet, coverage=0.4)
    r = _llm_report(monkeypatch, p, JudgeResponseV2(
        decision="ACCEPT", assessment=good, summary="s"))
    assert r["assessment"]["robustnessToUncertainty"]["status"] == "IMPROVABLE"
    # unknown site boundary → at most IMPROVABLE even with high coverage
    p = _packet_with_regulatory(packet, coverage=0.9, boundary_detected=False)
    r = _llm_report(monkeypatch, p, JudgeResponseV2(
        decision="ACCEPT", assessment=good, summary="s"))
    assert r["assessment"]["robustnessToUncertainty"]["status"] == "IMPROVABLE"
    # high coverage + detected boundary → GOOD allowed
    p = _packet_with_regulatory(packet, coverage=0.9)
    r = _llm_report(monkeypatch, p, JudgeResponseV2(
        decision="ACCEPT", assessment=good, summary="s"))
    assert r["assessment"]["robustnessToUncertainty"]["status"] == "GOOD"
    # POOR is never upgraded
    p = _packet_with_regulatory(packet, coverage=0.1)
    r = _llm_report(monkeypatch, p, JudgeResponseV2(
        decision="ACCEPT",
        assessment=AssessmentV2(robustnessToUncertainty=Dimension(status="POOR")),
        summary="s"))
    assert r["assessment"]["robustnessToUncertainty"]["status"] == "POOR"


def test_packet_carries_regulatory_coverage(pipe):
    """build_judge_packet lifts the validator's regulatory coverage + parser
    flags into the packet (judge-packet-2)."""
    doc, idx, registry, cons, placements, validation = pipe
    params = OptimizerParams.for_strategy("balanced")
    pkt = build_judge_packet(
        objective="balanced", iteration=0, max_iterations=3, params=params,
        validation=validation, allowed_area=cons.allowed_area,
        placements=placements, spacing_m=5.0)
    assert pkt["schemaVersion"] == "judge-packet-2"
    reg = validation["regulatory"]
    assert pkt["regulatoryCoverage"] == pytest.approx(
        reg["coverage"]["regulatoryCoverage"], abs=1e-3)
    assert pkt["parserCoverage"]["siteBoundaryDetected"] is True
    assert pkt["parserCoverage"]["crossingsDetected"] == 0


# ---------------------------------------------------------------------------
# precondition: judge only on valid plans; deterministic repair first
# ---------------------------------------------------------------------------

def test_deterministic_repair_removes_violators(pipe):
    from app.pipeline import deterministic_repair
    doc, idx, registry, cons, placements, _ = pipe
    # a tree 1 m from the building wall → guaranteed min-distance violation
    # (spacing is a design heuristic now: warnings, never hardViolations)
    broken = placements + [Placement(id="bad_0", kind="TREE", speciesId="tree_linden",
                                     x=29.0, y=95.0, crownRadiusM=3.5)]
    validation = validate_plan(broken, idx, registry, cons.site_boundary, cons.allowed_area)
    assert not validation["valid"]

    repaired, validation2, info = deterministic_repair(
        broken, idx, registry, cons.site_boundary, cons.allowed_area, validation)
    assert validation2["valid"]
    assert info["removed"] >= 1
    assert 1 <= info["passes"] <= 3
    assert all(p.id != "bad_0" for p in repaired)  # violator gone
    assert len(repaired) < len(broken)


def _run_generation_offline(monkeypatch, tmp_path, req):
    """Runs the real pipeline offline: SQLite in tmp, local LLM disabled."""
    from app import config as cfg, store, pipeline
    from app.services import classification
    from app.services.dxf_parser import parse_dxf
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "LOCAL_LLM_ENABLED", False)
    store.init_db()
    doc = parse_dxf(FIXTURE, "judge-e2e-doc", "synthetic_site.dxf")
    classification.classify_document(doc)
    pid = "prj_judge_test"
    pipeline.put_runtime(pid, doc)
    run_id = "run_judge_test"
    pipeline._init_status(run_id)
    pipeline._run_generation(pid, run_id, req)
    status = pipeline.get_run_status(run_id)
    rec = store.load_json(pid, f"iteration_{run_id}_0")
    return status, rec


def test_pipeline_judges_valid_plan_with_observability(monkeypatch, tmp_path):
    status, rec = _run_generation_offline(
        monkeypatch, tmp_path,
        {"seed": 42, "maxIterations": 1, "useLlmJudge": True})
    assert status["state"] == "COMPLETED", status.get("error")
    assert rec["validation"]["valid"]
    assert rec["judgeSkipped"] is None
    judge = rec["judge"]
    assert judge is not None
    assert judge["mode"] == "heuristic_fallback"  # LLM disabled, no network
    # per-iteration observability
    assert judge["inputSchemaVersion"] == PACKET_SCHEMA_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", judge["judgeInputHash"])
    assert "promptVersion" in judge and "model" in judge and "temperature" in judge
    assert rec["observability"]["judgeInputHash"] == judge["judgeInputHash"]
    # frontend-compatible keys
    for key in ("decision", "mode", "llmModel", "reasoningSummary"):
        assert key in judge
    assert set(judge["deterministicComponents"]) == {
        "coverage", "distribution", "utilizationBalance", "clusterPenalty"}
    assert rec["needsManualReview"] is False


def test_pipeline_skips_judge_on_unrepairable_plan(monkeypatch, tmp_path):
    from app import pipeline
    real_validate = pipeline.validate_plan

    def always_invalid(placements, idx, registry, site_boundary, allowed_area, **kw):
        v = real_validate(placements, idx, registry, site_boundary, allowed_area, **kw)
        if placements:  # persistent fake violation → repair can never converge
            v["hardViolations"] = list(v["hardViolations"]) + [{
                "plantingId": placements[0].id, "ruleId": "FAKE_RULE",
                "actual": 0.0, "requiredM": 1.0, "obstacle": "test"}]
            v["valid"] = False
        return v

    monkeypatch.setattr(pipeline, "validate_plan", always_invalid)
    status, rec = _run_generation_offline(
        monkeypatch, tmp_path,
        {"seed": 42, "maxIterations": 1, "useLlmJudge": True})
    assert status["state"] == "COMPLETED", status.get("error")
    assert rec["judge"] is None
    assert rec["judgeSkipped"] == "hard_violations"
    repair = rec["deterministicRepair"]
    assert repair["passes"] == 3          # tried up to 3 deterministic passes
    assert repair["removed"] >= 3         # removed the flagged violator each pass


# ---------------------------------------------------------------------------
# rerun retention rule
# ---------------------------------------------------------------------------

def test_retention_rule():
    cur = {"coverageScore": 0.20, "distributionScore": 0.60}

    # improved enough, no component regression → selected
    assert should_select_rerun(cur, True,
                               {"coverageScore": 0.25, "distributionScore": 0.62}, True)
    # objective gain below 0.02 → kept
    assert not should_select_rerun(cur, True,
                                   {"coverageScore": 0.21, "distributionScore": 0.61}, True)
    # big gain but one component drops > 0.05 → kept
    assert not should_select_rerun(cur, True,
                                   {"coverageScore": 0.45, "distributionScore": 0.52}, True)
    # component drop of exactly 0.05 is tolerated when objective gain suffices
    assert should_select_rerun(cur, True,
                               {"coverageScore": 0.32, "distributionScore": 0.55}, True)
    # invalid rerun never selected, however good the numbers
    assert not should_select_rerun(cur, True,
                                   {"coverageScore": 0.9, "distributionScore": 0.9}, False)
    # a valid rerun always beats an invalid current selection
    assert should_select_rerun(cur, False,
                               {"coverageScore": 0.05, "distributionScore": 0.05}, True)


# ---------------------------------------------------------------------------
# prompt versioning
# ---------------------------------------------------------------------------

def test_prompt_loader_default_v2():
    version, text = load_judge_prompt()
    assert version == "v2"
    assert "spatial-optimization reviewer" in text
    assert "MANUAL_REVIEW" in text
    assert "allowedParameterChanges" in text
    # regulatory separation: never legal-compliance wording, decision =
    # optimization continuation only
    assert "NEVER claim" in text
    assert "further optimization iteration" in text


def test_prompt_loader_env_override(monkeypatch):
    monkeypatch.setenv("JUDGE_PROMPT_VERSION", "v1")
    version, _ = load_judge_prompt()
    assert version == "v1"


def test_prompt_loader_missing_version_errors():
    with pytest.raises(JudgePromptError) as exc:
        load_judge_prompt("v999")
    assert "v999" in str(exc.value)
    assert "prompts/judge" in str(exc.value).replace("\\", "/")
