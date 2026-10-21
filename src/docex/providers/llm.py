"""LLM / VLM provider abstraction with structured (JSON-schema) output.

Providers:
- gemini: Google Gemini API through its OpenAI-compatible endpoint (default). Meant for the free
  tier: a key from a Google project without billing, throttled to stay under the free rate limit.
- openai: OpenAI Chat Completions (text and vision) with strict JSON-schema output.
- anthropic: Claude via the official SDK (text and vision), structured outputs.
- openai_compatible: any OpenAI-compatible server, e.g. a local open-source VLM
  served by Ollama / vLLM / llama.cpp (Qwen2.5-VL, MiniCPM-V, ...).
- mock: deterministic responder for tests (never used for reported numbers).
"""
from __future__ import annotations

import base64
import json
import os
import threading
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
        _require_paid_opt_in("Anthropic")
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

    strict = False

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None, timeout: float = 300,
                 max_tokens: Optional[int] = None) -> None:
        import httpx

        self.model = model
        self.max_tokens = max_tokens
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
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "extraction", "schema": schema, "strict": self.strict}},
            "temperature": 0,
        }
        if self.max_tokens:
            body["max_completion_tokens"] = self.max_tokens
        t0 = time.perf_counter()
        r = self._post(body)
        if r.status_code >= 400:
            raise ProviderError(f"{self.name} error {r.status_code}: {_error_body(r.text)}")
        j = r.json()
        choice = j["choices"][0]
        if choice["message"].get("refusal"):
            raise ProviderError("model refused the request")
        if choice.get("finish_reason") == "length":
            raise ProviderError("output truncated (max tokens)")
        text = choice["message"]["content"]
        u = j.get("usage") or {}
        usage = Usage(self.model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), time.perf_counter() - t0)
        try:
            return LLMResponse(json.loads(text), usage, text)
        except json.JSONDecodeError as e:
            raise ProviderError(f"invalid JSON from model: {e}") from e

    def _post(self, body: dict[str, Any]):
        return self._http.post("/chat/completions", json=body)


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI API (api.openai.com) with strict structured outputs; key from OPENAI_API_KEY."""

    name = "openai"
    strict = True

    def __init__(self, model: str, max_tokens: Optional[int] = None,
                 base_url: str = "https://api.openai.com/v1") -> None:
        _require_paid_opt_in("OpenAI")
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderError("OPENAI_API_KEY is not set")
        super().__init__(os.environ.get("OPENAI_BASE_URL", base_url), model, api_key=key, timeout=120,
                         max_tokens=max_tokens)


def _error_body(text: str) -> str:
    """Short, single-line error body. An HTML page (CDN or proxy error) is not worth storing."""
    t = text.strip()
    if t[:1] == "<":
        return "(HTML page instead of JSON; check the base URL or proxy)"
    return t[:300]


def _require_https(url: str) -> None:
    """The API key travels in a header, so a non-local base URL (a reverse proxy) must use TLS."""
    from urllib.parse import urlparse

    u = urlparse(url)
    if u.scheme != "https" and u.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ProviderError(f"base URL {url!r} must use https (the API key is sent in a header)")


class GeminiProvider(OpenAICompatibleProvider):
    """Gemini API (generativelanguage.googleapis.com) via its OpenAI-compatible endpoint; key from GEMINI_API_KEY.

    The free tier is rate-limited per project, so requests are spaced ``min_interval_s`` apart (shared by
    every instance in the process, since eval threads each build their own provider) and a 429/503 is
    retried after a wait. When the daily quota is used up the retries fail too: the free tier rejects
    the request, it does not bill it.
    """

    name = "gemini"
    strict = False  # Gemini ignores OpenAI's strict flag; the schema itself is still enforced

    _lock = threading.Lock()
    _next_slot = 0.0

    def __init__(self, model: str, max_tokens: Optional[int] = None,
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai",
                 min_interval_s: float = 6.0, max_retries: int = 3,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        _require_paid_opt_in("Gemini")
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("GEMINI_API_KEY is not set")
        base_url = os.environ.get("GEMINI_BASE_URL") or base_url
        _require_https(base_url)
        super().__init__(base_url, model, api_key=key, timeout=120, max_tokens=max_tokens)
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self._sleep = sleep

    def _wait_for_slot(self) -> None:
        with GeminiProvider._lock:
            now = time.monotonic()
            slot = max(now, GeminiProvider._next_slot)
            GeminiProvider._next_slot = slot + self.min_interval_s
        if slot > now:
            self._sleep(slot - now)

    def _post(self, body: dict[str, Any]):
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            r = self._http.post("/chat/completions", json=body)
            if r.status_code not in (429, 503) or attempt == self.max_retries:
                break
            retry_after = r.headers.get("retry-after", "")
            self._sleep(float(retry_after) if retry_after.isdigit() else 10.0 * 2 ** attempt)
        if r.status_code == 429:
            raise ProviderError("gemini free-tier quota reached (429, nothing billed); "
                                f"wait for the quota to reset: {r.text[:200]}")
        return r


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
    provider = section.get("provider", "gemini")
    if provider == "gemini":
        gc = cfg.get("gemini", {})
        return GeminiProvider(section["model"], int(section.get("max_tokens", 16000)),
                              gc.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai"),
                              float(gc.get("min_interval_s", 6.0)), int(gc.get("max_retries", 3)))
    if provider == "openai":
        return OpenAIProvider(section["model"], int(section.get("max_tokens", 16000)))
    if provider == "anthropic":
        return AnthropicProvider(section["model"], section.get("effort"), int(section.get("max_tokens", 16000)),
                                 section.get("fallbacks", "default"))
    if provider == "openai_compatible":
        oc = cfg.get("openai_compatible", {})
        return OpenAICompatibleProvider(oc.get("base_url"), section.get("model") or oc.get("model"))
    raise ValueError(f"unknown LLM provider {provider} (mock providers are injected in tests)")


GATED_PROVIDERS = ("gemini", "openai", "anthropic")


def paid_llm_enabled() -> bool:
    """Hosted model APIs are off unless DOCEX_ENABLE_LLM=true, even when a key is present.

    A key alone must never start spending or using up a quota: B and C stay off in the API, the UI and
    the eval until someone opts in explicitly (with a spend limit set, or a free-tier key without billing).
    """
    return os.environ.get("DOCEX_ENABLE_LLM", "").lower() in ("1", "true", "yes")


def _require_paid_opt_in(provider: str) -> None:
    if not paid_llm_enabled():
        raise ProviderError(f"{provider} calls are disabled; set DOCEX_ENABLE_LLM=true to allow model API calls")


def llm_available(section: dict[str, Any]) -> bool:
    provider = section.get("provider", "gemini")
    if provider in GATED_PROVIDERS and not paid_llm_enabled():
        return False
    if provider == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY"))
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return True
