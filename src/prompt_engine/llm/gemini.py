"""Adapter do Google Gemini via SDK `google-genai` (SPEC-006 §2). Import lazy (extra opcional)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from prompt_engine.core.errors import (
    ProviderBadRequestError,
    ProviderConfigError,
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from prompt_engine.llm.base import LLMRequest, LLMResponse, Usage

NAME = "gemini"
_ROLE_MAP = {"user": "user", "assistant": "model"}


def _retry_delay(body: Any) -> float | None:
    """Extrai `retryDelay` ("17s") do RetryInfo que o Gemini manda junto com o 429."""
    details = (body or {}).get("error", {}).get("details") or []
    for detail in details:
        delay = str(detail.get("retryDelay", ""))
        if delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


class GeminiProvider:
    name = NAME

    def __init__(self, api_key: str | None, timeout_s: float, client: Any | None = None) -> None:
        try:
            from google import genai
            from google.genai import errors, types
        except ImportError as exc:
            raise ProviderConfigError(
                "SDK do Gemini não instalado: rode pip install -e '.[gemini]'"
            ) from exc
        self._types = types
        self._errors = errors
        if client is None:
            if not api_key:
                raise ProviderConfigError("GEMINI_API_KEY não configurada no .env")
            # Retry interno do SDK desligado: retry e fallback são do ProviderPool.
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=int(timeout_s * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        self._client = client

    async def generate(self, request: LLMRequest) -> LLMResponse:
        types = self._types
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        contents = [
            types.Content(role=_ROLE_MAP[m.role], parts=[types.Part(text=m.content)])
            for m in request.messages
            if m.role != "system"
        ]
        config = types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            response_mime_type="application/json" if request.json_schema else None,
            response_json_schema=request.json_schema,
        )
        start = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=request.model, contents=contents, config=config
            )
        except Exception as exc:
            raise self._translate(exc) from exc
        latency_ms = round((time.perf_counter() - start) * 1000)

        usage = response.usage_metadata
        candidates = response.candidates or []
        finish = candidates[0].finish_reason if candidates else None
        return LLMResponse(
            text=response.text or "",  # None quando bloqueado (ex.: SAFETY)
            model=request.model,
            provider=NAME,
            usage=Usage(
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            ),
            latency_ms=latency_ms,
            finish_reason=getattr(finish, "name", None) or str(finish or "UNKNOWN"),
        )

    def _translate(self, exc: Exception) -> ProviderError:
        if isinstance(exc, httpx.TimeoutException | TimeoutError):
            return ProviderUnavailableError(NAME, "timeout")
        if isinstance(exc, httpx.TransportError):
            return ProviderUnavailableError(NAME, f"falha de rede: {type(exc).__name__}")
        if isinstance(exc, self._errors.APIError):
            code = exc.code
            if code == 429:
                return ProviderRateLimitError(
                    NAME, "quota/rate limit", retry_after=_retry_delay(exc.details)
                )
            if code >= 500:
                return ProviderUnavailableError(NAME, f"HTTP {code}")
            return ProviderBadRequestError(NAME, f"HTTP {code}: {exc.message}")
        return ProviderUnavailableError(NAME, f"erro inesperado: {type(exc).__name__}")
