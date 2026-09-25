"""Local LLM abstraction (OpenAI-compatible endpoint only — never a cloud API).

Works with Ollama (/v1), llama.cpp server, vLLM and other local runtimes
exposing /v1/chat/completions. Structured output is enforced by prompting for
JSON + Pydantic validation with one repair retry.
"""
from __future__ import annotations

import json
import re
from typing import Type, TypeVar

import httpx
from pydantic import BaseModel

from .. import config

T = TypeVar("T", bound=BaseModel)


class LocalLLMError(Exception):
    pass


class LocalLLM:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 enabled: bool | None = None, timeout: float | None = None):
        self.base_url = (base_url or config.LOCAL_LLM_BASE_URL).rstrip("/")
        self.model = model if model is not None else config.LOCAL_LLM_MODEL
        self.enabled = config.LOCAL_LLM_ENABLED if enabled is None else enabled
        self.timeout = timeout or config.LOCAL_LLM_TIMEOUT_S

    def _chat_url(self) -> str:
        base = self.base_url
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    async def is_reachable(self) -> bool:
        if not self.enabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                base = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
                r = await client.get(f"{base}/v1/models")
                if r.status_code == 200:
                    if not self.model:
                        data = r.json().get("data") or []
                        if data:
                            self.model = data[0].get("id", "")
                    return True
        except Exception:
            pass
        return False

    async def _chat(self, messages: list[dict], json_schema: dict | None = None,
                    json_mode: bool = True, max_tokens: int | None = None) -> str:
        payload: dict = {
            "model": self.model or "local",
            "messages": messages,
            "temperature": 0.1,
            # terse default suits the judge; extraction passes a larger budget
            "max_tokens": max_tokens or 320,
        }
        if json_schema:
            # grammar-constrained decoding (llama.cpp, vLLM, recent Ollama):
            # guarantees schema-valid JSON without burning prompt tokens
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": json_schema},
            }
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(self._chat_url(), json=payload)
            if r.status_code == 400 and json_schema:
                payload["response_format"] = {"type": "json_object"}
                r = await client.post(self._chat_url(), json=payload)
            if r.status_code == 400 and "response_format" in payload:
                # some local servers reject response_format — retry without it
                payload.pop("response_format", None)
                r = await client.post(self._chat_url(), json=payload)
            r.raise_for_status()
            data = r.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LocalLLMError(f"malformed completion response: {exc}")

    @staticmethod
    def _extract_json(text: str) -> str:
        text = text.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence:
            return fence.group(1)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return text[start:end + 1]
        return text

    async def structured_generate(self, schema: Type[T], messages: list[dict],
                                  max_tokens: int | None = None) -> T:
        """Generate + validate against a Pydantic schema; one repair retry."""
        if not self.enabled:
            raise LocalLLMError("local LLM disabled (LOCAL_LLM_ENABLED=false)")
        json_schema = schema.model_json_schema()
        msgs = list(messages)
        msgs[-1] = {**msgs[-1], "content": msgs[-1]["content"] +
                    "\n\nRespond ONLY with a JSON object."}
        raw = await self._chat(msgs, json_schema=json_schema, max_tokens=max_tokens)
        try:
            return schema.model_validate_json(self._extract_json(raw))
        except Exception as first_err:
            repair = msgs + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"That was not valid JSON for the required schema "
                                            f"({first_err}). Reply with corrected JSON only. "
                                            f"Schema: {json.dumps(json_schema)[:1500]}"},
            ]
            raw2 = await self._chat(repair, max_tokens=max_tokens)
            return schema.model_validate_json(self._extract_json(raw2))


async def capabilities() -> dict:
    llm = LocalLLM()
    reachable = await llm.is_reachable()
    return {
        "enabled": llm.enabled,
        "baseUrl": llm.base_url,
        "model": llm.model or None,
        "reachable": reachable,
    }
