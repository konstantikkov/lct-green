"""Level C: offline GPT evaluator (dev/CI only, NEVER a runtime dependency).

Capability policy (non-negotiable):
  * The configured model (OPENAI_EVAL_MODEL) is probed against GET /models.
  * Missing API key or absent model  ->  CapabilityError with an explicit,
    actionable message. NO silent fallback to another model, EVER, and an
    eval that did not run is reported as SKIPPED — never faked.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Literal, Optional

import httpx
from pydantic import BaseModel, Field

PROMPT_VERSION = "gpt-eval-v1"
# The evaluator speaks the OpenAI-compatible protocol but is expected to be a
# LOCAL open-source model (llama.cpp / vLLM / Ollama). The default points at
# the local eval server; a hosted endpoint can still be configured explicitly.
DEFAULT_BASE_URL = "http://127.0.0.1:8081/v1"
DEFAULT_MODEL = ""  # empty = auto-adopt the single model served locally

_HOSTED_HOSTS = ("api.openai.com",)  # endpoints that always require an API key
# Local CPU inference of a 7B evaluator is slow (prompt processing alone can
# take minutes); generous default, overridable for hosted endpoints.
_TIMEOUT = float(os.environ.get("EVAL_LLM_TIMEOUT_S", "900"))


class CapabilityError(RuntimeError):
    """GPT evaluator capability is not available. Callers must surface this
    as SKIPPED (capability: ...) — deterministic gates are unaffected."""


@dataclass
class EvaluatorConfig:
    model: str
    api_key: str
    base_url: str

    @classmethod
    def from_env(cls) -> "EvaluatorConfig":
        return cls(
            model=os.environ.get("OPENAI_EVAL_MODEL", DEFAULT_MODEL),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        )


class Criterion(BaseModel):
    id: str
    verdict: Literal["PASS", "FAIL", "UNCERTAIN"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    comment: str = ""


class EvalReport(BaseModel):
    criteria: list[Criterion]
    criticalFailures: list[str] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    summary: str = ""


class PairVerdict(BaseModel):
    winner: Literal["A", "B", "TIE"]
    reasons: list[str] = Field(default_factory=list)


_EVAL_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "greenplan_eval_report",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "criteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "verdict": {"type": "string",
                                        "enum": ["PASS", "FAIL", "UNCERTAIN"]},
                            "confidence": {"type": "number"},
                            "evidence": {"type": "array", "items": {"type": "string"}},
                            "comment": {"type": "string"},
                        },
                        "required": ["id", "verdict", "confidence", "evidence", "comment"],
                        "additionalProperties": False,
                    },
                },
                "criticalFailures": {"type": "array", "items": {"type": "string"}},
                "regressions": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
            "required": ["criteria", "criticalFailures", "regressions", "summary"],
            "additionalProperties": False,
        },
    },
}

_PAIR_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "greenplan_pair_verdict",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "winner": {"type": "string", "enum": ["A", "B", "TIE"]},
                "reasons": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["winner", "reasons"],
            "additionalProperties": False,
        },
    },
}

_SYSTEM_PROMPT = (
    "You are an offline QA evaluator for GreenPlan, a deterministic landscape-"
    "planning engine. You receive an EvalPacket: derived plan facts, the verified "
    "rule set, the deterministic validation report (which is authoritative on "
    "compliance — never contradict it), and fixture expectations. Judge SOFT "
    "quality and consistency: sensible distribution, expectations honored, "
    "explanations coherent, classifications plausible. Emit criteria with "
    "verdict PASS/FAIL/UNCERTAIN, each with concrete evidence strings taken "
    "from packet fields. Use UNCERTAIN instead of guessing."
)

_PAIR_SYSTEM_PROMPT = (
    "You compare two GreenPlan planting plans, blinded as 'Plan A' and 'Plan B'. "
    "Both are compliant per the deterministic validator; compare SOFT quality only "
    "(distribution, coverage vs. expectations, spatial sanity). Answer with the "
    "winner A, B, or TIE."
)


class GptEvaluator:
    """Synchronous evaluator over an OpenAI-compatible /v1 API (httpx)."""

    prompt_version = PROMPT_VERSION

    def __init__(self, config: EvaluatorConfig | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.config = config or EvaluatorConfig.from_env()
        self._transport = transport
        self._probed: Optional[str] = None

    def _client(self) -> httpx.Client:
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return httpx.Client(
            base_url=self.config.base_url,
            headers=headers,
            timeout=_TIMEOUT,
            transport=self._transport,
        )

    # -- capability ---------------------------------------------------------
    def probe_capability(self) -> str:
        """Verify endpoint + configured model via GET /models. Raises CapabilityError.

        Local open-source endpoints (llama.cpp / vLLM / Ollama) need no API key;
        hosted endpoints (api.openai.com) still require one explicitly."""
        cfg = self.config
        hosted = any(h in cfg.base_url for h in _HOSTED_HOSTS)
        if hosted and not cfg.api_key:
            raise CapabilityError(
                f"OPENAI_API_KEY is not set — hosted evaluator endpoint "
                f"'{cfg.base_url}' (model '{cfg.model}') cannot run. Either set "
                f"OPENAI_API_KEY or point OPENAI_BASE_URL at the local eval model "
                f"server. No fallback model is used by design.")
        try:
            with self._client() as client:
                resp = client.get("/models")
        except httpx.HTTPError as exc:
            raise CapabilityError(
                f"cannot reach {cfg.base_url}/models to verify model "
                f"'{cfg.model}': {exc}. Check OPENAI_BASE_URL / network. "
                f"No fallback model is used by design.") from exc
        if resp.status_code != 200:
            raise CapabilityError(
                f"GET {cfg.base_url}/models returned HTTP {resp.status_code} — "
                f"cannot verify model '{cfg.model}' (check OPENAI_API_KEY). "
                f"No fallback model is used by design.")
        ids = [m.get("id") for m in resp.json().get("data", [])]
        if not cfg.model:
            # No model configured: adopt the endpoint's model ONLY when it is
            # unambiguous (exactly one served). Never a silent choice between
            # alternatives — with several models the operator must pick.
            if len(ids) == 1 and ids[0]:
                self.config.model = ids[0]
                self._probed = ids[0]
                return ids[0]
            raise CapabilityError(
                f"OPENAI_EVAL_MODEL is not set and {cfg.base_url} serves "
                f"{len(ids)} models — cannot choose one implicitly. Set "
                f"OPENAI_EVAL_MODEL explicitly. No implicit model selection "
                f"between alternatives by design.")
        if cfg.model not in ids:
            raise CapabilityError(
                f"configured OPENAI_EVAL_MODEL '{cfg.model}' is NOT available at "
                f"{cfg.base_url} ({len(ids)} models visible). "
                f"Change OPENAI_EVAL_MODEL to a model this endpoint serves. "
                f"Silent fallback to another model is forbidden by design.")
        self._probed = cfg.model
        return cfg.model

    # -- calls ---------------------------------------------------------------
    def _chat(self, messages: list[dict], response_format: dict) -> dict:
        if self._probed is None:
            self.probe_capability()
        with self._client() as client:
            resp = client.post("/chat/completions", json={
                "model": self.config.model,
                "messages": messages,
                "temperature": 0,
                "response_format": response_format,
            })
        if resp.status_code != 200:
            raise CapabilityError(
                f"chat.completions with model '{self.config.model}' failed: "
                f"HTTP {resp.status_code}: {resp.text[:300]}")
        return json.loads(resp.json()["choices"][0]["message"]["content"])

    def evaluate(self, packet: dict) -> EvalReport:
        raw = self._chat(
            [{"role": "system", "content": _SYSTEM_PROMPT},
             {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}],
            _EVAL_RESPONSE_FORMAT)
        return EvalReport.model_validate(raw)

    def evaluate_pair(self, packet_a: dict, packet_b: dict) -> PairVerdict:
        """Blind A/B comparison — caller controls order (see evals/pairwise.py)."""
        blinded = {"planA": _blind(packet_a), "planB": _blind(packet_b)}
        raw = self._chat(
            [{"role": "system", "content": _PAIR_SYSTEM_PROMPT},
             {"role": "user", "content": json.dumps(blinded, ensure_ascii=False)}],
            _PAIR_RESPONSE_FORMAT)
        return PairVerdict.model_validate(raw)


def _blind(packet: dict) -> dict:
    """Strip identity hints so the pair judge cannot tell baseline/candidate."""
    p = dict(packet)
    p.pop("caseId", None)
    plan = dict(p.get("planSummary") or {})
    plan.pop("seed", None)
    p["planSummary"] = plan
    return p


PairJudge = Callable[[dict, dict], PairVerdict]
