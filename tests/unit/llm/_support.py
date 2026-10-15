"""Helpers dos testes de LLM: requests de exemplo e harnesses que simulam cada backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal, Protocol

import httpx
from google.genai import errors as genai_errors

from prompt_engine.llm.base import LLMProvider, LLMRequest, Message
from prompt_engine.llm.fake import FakeProvider
from prompt_engine.llm.gemini import GeminiProvider

FailureKind = Literal["rate_limit", "unavailable", "bad_request", "timeout"]


def make_request(**overrides: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "messages": [
            Message(role="system", content="Você é um assistente."),
            Message(role="user", content="exemplo"),
            Message(role="assistant", content='{"ok": true}'),
            Message(role="user", content="pergunta real"),
        ],
        "model": "modelo-x",
        "temperature": 0.0,
        "max_output_tokens": 100,
        "json_schema": {"type": "object"},
        "metadata": {"step": "classify", "request_id": "req_1"},
    }
    return LLMRequest(**(base | overrides))


class Harness(Protocol):
    provider: LLMProvider

    def respond(self, text: str) -> None: ...
    def fail(self, kind: FailureKind) -> None: ...


# --- Fake ---------------------------------------------------------------------


class FakeHarness:
    def __init__(self) -> None:
        self.provider = FakeProvider({})

    def respond(self, text: str) -> None:
        self.provider.script["classify"] = text

    def fail(self, kind: FailureKind) -> None:
        from prompt_engine.core import errors as e

        self.provider.script["classify"] = {
            "rate_limit": e.ProviderRateLimitError("fake"),
            "unavailable": e.ProviderUnavailableError("fake"),
            "bad_request": e.ProviderBadRequestError("fake"),
            "timeout": e.ProviderUnavailableError("fake", "timeout"),
        }[kind]


# --- Gemini (SDK simulado) ------------------------------------------------------


def gemini_response(
    text: str | None, finish: str = "STOP", prompt: int = 11, out: int = 7
) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=None
        ),
    )


def gemini_error(code: int, details: list[dict[str, Any]] | None = None) -> Exception:
    body = {"error": {"code": code, "message": f"erro {code}", "status": "X", "details": details}}
    if code >= 500:
        return genai_errors.ServerError(code, body)
    return genai_errors.ClientError(code, body)


@dataclass
class FakeGeminiModels:
    outcome: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class GeminiHarness:
    def __init__(self) -> None:
        self.models = FakeGeminiModels()
        client = SimpleNamespace(aio=SimpleNamespace(models=self.models))
        self.provider = GeminiProvider(api_key="test-key", timeout_s=30, client=client)

    def respond(self, text: str) -> None:
        self.models.outcome = gemini_response(text)

    def fail(self, kind: FailureKind) -> None:
        self.models.outcome = {
            "rate_limit": gemini_error(429),
            "unavailable": gemini_error(503),
            "bad_request": gemini_error(400),
            "timeout": httpx.ReadTimeout("lento demais"),
        }[kind]


HARNESSES: dict[str, type[FakeHarness] | type[GeminiHarness]] = {
    "fake": FakeHarness,
    "gemini": GeminiHarness,
}
