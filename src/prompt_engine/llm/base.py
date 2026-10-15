"""Contrato neutro de provedor de LLM (SPEC-006 §2). O core depende só deste módulo."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: list[Message]
    model: str
    temperature: float = 0.0
    max_output_tokens: int = 1024
    json_schema: dict[str, Any] | None = None  # presente => pedir saída JSON nativa
    metadata: dict[str, str] = {}  # request_id, step: para logs e roteiro do Fake


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    model: str
    provider: str
    usage: Usage = Usage()
    latency_ms: int = 0
    finish_reason: str = "STOP"


class LLMProvider(Protocol):
    """Adapter de um provedor. Deve traduzir falhas para ProviderError (core/errors.py)."""

    name: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...
