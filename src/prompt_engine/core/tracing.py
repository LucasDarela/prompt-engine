"""Porta de observabilidade do core. Implementação persistente: observability/ (SPEC-008)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from prompt_engine.core.context import PipelineContext

SpanStatus = Literal["ok", "skipped", "blocked", "error"]
RunStatus = Literal["ok", "blocked", "error", "degraded"]


class Span(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: SpanStatus
    started_at: datetime
    duration_ms: int
    attributes: dict[str, Any] = {}


class PipelineRun(BaseModel):
    """Tudo o que aconteceu numa execução do pipeline, entregue ao TraceSink no final."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    ctx: PipelineContext
    spans: list[Span]
    status: RunStatus
    started_at: datetime
    duration_ms: int
    error_code: str | None = None
    error_details: dict[str, Any] = {}  # ex.: score e sinais de um bloqueio por injection


class TraceSink(Protocol):
    async def record(self, run: PipelineRun) -> None: ...


class NoopSink:
    async def record(self, run: PipelineRun) -> None:
        return None
