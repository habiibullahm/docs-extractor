"""LLM / VLM provider abstraction with structured (JSON-schema) output.

Providers:
- anthropic: Claude via the official SDK (text and vision), structured outputs.
- openai_compatible: any OpenAI-compatible server, e.g. a local open-source VLM
  served by Ollama / vLLM / llama.cpp (Qwen2.5-VL, MiniCPM-V, ...).
- mock: deterministic responder for tests (never used for reported numbers).
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Union

from docex.config import pipeline_config


@dataclass
class ImagePart:
    data: bytes
    media_type: str = "image/jpeg"


Content = list[Union[str, ImagePart]]


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    calls: int = 1

    @property
    def cost_usd(self) -> float:
        prices = pipeline_config()["costs"]["token_prices"].get(self.model)
        if not prices:
            return 0.0
        return (self.input_tokens * prices["input"] + self.output_tokens * prices["output"]) / 1e6

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.model if self.model == other.model else f"{self.model}+{other.model}",
                     self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens,
                     self.latency_s + other.latency_s, self.calls + other.calls)


@dataclass
class LLMResponse:
    data: dict[str, Any]
    usage: Usage
    raw_text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, content: Content, schema: dict[str, Any]) -> LLMResponse: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, effort: Optional[str] = "low", max_tokens: int = 16000,
                 fallbacks: Optional[str] = "default") -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=3)
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks

    def _blocks(self, content: Content) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, ImagePart):
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": part.media_type,
                                                           "data": base64.standard_b64encode(part.data).decode()}})
            else:
                blocks.append({"type": "text", "text": part})
        return blocks

    def complete_json(self, system: str, content: Content, schema: dict[str, Any]) -> LLMResponse:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        kwargs: dict[str, Any] = dict(
            model=self.model, max_tokens=self.max_tokens, system=system,
            messages=[{"role": "user", "content": self._blocks(content)}],
            output_config=output_config,
        )
        t0 = time.perf_counter()
        try:
            if self.fallbacks:
                # Server-side refusal fallback (see docs: refusals-and-fallback)
                resp = self.client.beta.messages.create(
                    **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks=self.fallbacks)
            else:
                resp = self.client.messages.create(**kwargs)
        except self._anthropic.APIStatusError as e:
            raise ProviderError(f"anthropic API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise ProviderError(f"anthropic connection error: {e}") from e
        latency = time.perf_counter() - t0
        usage = Usage(self.model, resp.usage.input_tokens, resp.usage.output_tokens, latency)
        if resp.stop_reason == "refusal":
            raise ProviderError("model refused the request")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("output truncated (max_tokens)")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ProviderError(f"invalid JSON from model: {e}") from e
        return LLMResponse(data, usage, text, {"stop_reason": resp.stop_reason, "id": resp.id})


class OpenAICompatibleProvider:
    """For local open-source models behind an OpenAI-compatible /chat/completions endpoint."""

    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None, timeout: float = 300) -> None:
        import httpx

        self.model = model
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                  headers={"Authorization": f"Bearer {api_key or os.environ.get('OPENAI_COMPATIBLE_API_KEY', 'local')}"})

    def complete_json(self, system: str, content: Content, schema: dict[str, Any]) -> LLMResponse:
        parts: list[dict[str, Any]] = []
        for p in content:
            if isinstance(p, ImagePart):
                url = f"data:{p.media_type};base64,{base64.standard_b64encode(p.data).decode()}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            else:
                parts.append({"type": "text", "text": p})
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": parts}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "extraction", "schema": schema}},
            "temperature": 0,
        }
        t0 = time.perf_counter()
        r = self._http.post("/chat/completions", json=body)
        if r.status_code >= 400:
            raise ProviderError(f"openai-compatible error {r.status_code}: {r.text[:300]}")
        j = r.json()
        text = j["choices"][0]["message"]["content"]
        u = j.get("usage") or {}
        usage = Usage(self.model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), time.perf_counter() - t0)
        try:
            return LLMResponse(json.loads(text), usage, text)
        except json.JSONDecodeError as e:
            raise ProviderError(f"invalid JSON from model: {e}") from e


class MockProvider:
    """Test double: ``responder(system, content, schema) -> dict``."""

    name = "mock"

    def __init__(self, responder: Callable[[str, Content, dict[str, Any]], dict[str, Any]], model: str = "mock"):
        self.responder = responder
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, system: str, content: Content, schema: dict[str, Any]) -> LLMResponse:
        self.calls.append({"system": system, "content": content, "schema": schema})
        data = self.responder(system, content, schema)
        return LLMResponse(data, Usage(self.model, 1000, 200, 0.01), json.dumps(data))


def get_llm(section: dict[str, Any], cfg: Optional[dict[str, Any]] = None) -> LLMProvider:
    cfg = cfg or pipeline_config()
    provider = section.get("provider", "anthropic")
    if provider == "anthropic":
        return AnthropicProvider(section["model"], section.get("effort"), int(section.get("max_tokens", 16000)),
                                 section.get("fallbacks", "default"))
    if provider == "openai_compatible":
        oc = cfg.get("openai_compatible", {})
        return OpenAICompatibleProvider(oc.get("base_url"), section.get("model") or oc.get("model"))
    raise ValueError(f"unknown LLM provider {provider} (mock providers are injected in tests)")


def llm_available(section: dict[str, Any]) -> bool:
    if section.get("provider") == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return True
