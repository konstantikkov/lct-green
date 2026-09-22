"""Contracts of the LLM-layer plumbing that must hold WITHOUT any LLM:
pairwise order-swap logic, GPT capability gating (no silent fallback),
judge benchmark scoring math.
"""
from __future__ import annotations

import json

import httpx
import pytest

from evals import pairwise
from evals.evaluator import (CapabilityError, EvaluatorConfig, GptEvaluator,
                             PairVerdict)
from evals.judge import CASES_PATH, LABELS_PATH
from evals.judge.score import (load_jsonl, mock_judge, run_benchmark,
                               score_outputs, validate_changes)


# ---------------------------------------------------------------------------
# pairwise order-swap logic (mocked pair judge)
# ---------------------------------------------------------------------------

def _judge_preferring(packet_key: str):
    """Pair judge that always prefers the packet whose planSummary carries
    the given marker — regardless of A/B position (order-consistent)."""
    def judge(a: dict, b: dict) -> PairVerdict:
        if a["marker"] == packet_key:
            return PairVerdict(winner="A", reasons=["marker match A"])
        if b["marker"] == packet_key:
            return PairVerdict(winner="B", reasons=["marker match B"])
        return PairVerdict(winner="TIE")
    return judge


BASE = {"marker": "baseline"}
CAND = {"marker": "candidate"}


def test_pairwise_consistent_candidate_win():
    res = pairwise.compare(BASE, CAND, _judge_preferring("candidate"))
    assert res["winner"] == pairwise.CANDIDATE
    # the raw positional labels must differ between the two runs (order swapped)
    assert res["runs"][0]["rawWinner"] == "B"
    assert res["runs"][1]["rawWinner"] == "A"


def test_pairwise_consistent_baseline_win():
    res = pairwise.compare(BASE, CAND, _judge_preferring("baseline"))
    assert res["winner"] == pairwise.BASELINE


def test_pairwise_position_bias_yields_inconclusive():
    # a biased judge that always picks whatever sits in slot A
    def biased(a, b):
        return PairVerdict(winner="A", reasons=["position bias"])
    res = pairwise.compare(BASE, CAND, biased)
    assert res["winner"] == pairwise.INCONCLUSIVE


def test_pairwise_double_tie_is_tie():
    def tie(a, b):
        return PairVerdict(winner="TIE")
    assert pairwise.compare(BASE, CAND, tie)["winner"] == pairwise.TIE


def test_pairwise_tie_vs_win_is_inconclusive():
    calls = {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        return PairVerdict(winner="TIE" if calls["n"] == 1 else "A")
    assert pairwise.compare(BASE, CAND, flaky)["winner"] == pairwise.INCONCLUSIVE


# ---------------------------------------------------------------------------
# GPT evaluator capability gating
# ---------------------------------------------------------------------------

def test_capability_error_when_key_missing():
    ev = GptEvaluator(EvaluatorConfig(model="gpt-6", api_key="",
                                      base_url="https://api.openai.com/v1"))
    with pytest.raises(CapabilityError) as ei:
        ev.probe_capability()
    msg = str(ei.value)
    assert "OPENAI_API_KEY" in msg and "gpt-6" in msg
    assert "fallback" in msg.lower()


def test_capability_error_when_model_absent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"},
                                                  {"id": "o4-mini"}]})
    ev = GptEvaluator(EvaluatorConfig(model="gpt-6", api_key="sk-test",
                                      base_url="https://example.test/v1"),
                      transport=httpx.MockTransport(handler))
    with pytest.raises(CapabilityError) as ei:
        ev.probe_capability()
    msg = str(ei.value)
    assert "gpt-6" in msg and "OPENAI_EVAL_MODEL" in msg
    assert "forbidden" in msg.lower() or "fallback" in msg.lower()


def test_probe_passes_when_model_available():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gpt-6"}]})
    ev = GptEvaluator(EvaluatorConfig(model="gpt-6", api_key="sk-test",
                                      base_url="https://example.test/v1"),
                      transport=httpx.MockTransport(handler))
    assert ev.probe_capability() == "gpt-6"


# ---------------------------------------------------------------------------
# judge benchmark: dataset shape + scorer math
# ---------------------------------------------------------------------------

def test_benchmark_dataset_shape():
    cases = load_jsonl(CASES_PATH)
    labels = load_jsonl(LABELS_PATH)
    assert len(cases) >= 12
    assert {c["caseId"] for c in cases} == {l["caseId"] for l in labels}
    kinds = {c["kind"] for c in cases}
    assert {"clearly_accept", "clearly_rerun", "manual_review", "param_trap"} <= kinds
    for l in labels:
        assert l["reviewer"] == "engineering-bootstrap"
        assert l["expectedDecision"] in ("ACCEPT", "RERUN", "MANUAL_REVIEW")
        assert "BOOTSTRAP" in l["notes"].upper()


def test_mock_benchmark_runs_and_scores():
    res = run_benchmark(mode="mock")
    assert res["status"] == "RAN"
    assert res["cases"] >= 12
    for key in ("acceptAccuracy", "rerunPrecision", "manualReviewRecall",
                "parameterChangeValidity", "overallAgreement"):
        assert res[key] is not None and 0.0 <= res[key] <= 1.0, key
    # the deterministic stub must agree with the bootstrap labels
    assert res["overallAgreement"] == 1.0
    assert res["parameterChangeValidity"] == 1.0


def test_mock_judge_clamps_param_traps():
    cases = {c["caseId"]: c for c in load_jsonl(CASES_PATH)}
    for cid in ("jb_012", "jb_013", "jb_014"):
        packet = cases[cid]["packet"]
        out = mock_judge(packet)
        assert out["decision"] == "RERUN"
        ok, problems = validate_changes(packet, out["parameterChanges"])
        assert ok, f"{cid}: {problems}"


def test_scorer_flags_invalid_parameter_changes():
    cases = load_jsonl(CASES_PATH)
    labels = load_jsonl(LABELS_PATH)
    outputs = {c["caseId"]: mock_judge(c["packet"]) for c in cases}
    # sabotage one output: out-of-bounds + non-whitelisted proposals
    outputs["jb_012"] = {"decision": "RERUN",
                         "parameterChanges": {"distributionWeight": 1.15,
                                              "minSpacingM": 2.0}}
    res = score_outputs(cases, labels, outputs)
    bad = next(pc for pc in res["perCase"] if pc["caseId"] == "jb_012")
    assert not bad["changesValid"]
    assert any("whitelist" in p for p in bad["changeProblems"])
    assert any("outside" in p for p in bad["changeProblems"])
    assert res["parameterChangeValidity"] < 1.0


def test_labels_are_valid_jsonl_with_dates():
    for l in load_jsonl(LABELS_PATH):
        assert l["review_date"]
        json.dumps(l)  # serializable round-trip
