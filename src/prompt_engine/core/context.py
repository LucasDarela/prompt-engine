"""PipelineContext e modelos auxiliares (ARCHITECTURE §4). Imutável: stages retornam cópias."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from prompt_engine.config import ProviderName, Tier
from prompt_engine.core.errors import PipelineMisconfigured
from prompt_engine.core.vault import PIIVault
from prompt_engine.llm.base import Message, Usage

__all__ = [
    "EnrichmentData",
    "KBSnippet",
    "Message",
    "PipelineContext",
    "RouteDecision",
    "RunOptions",
    "SecurityReport",
    "StepResult",
    "Usage",
]

_FROZEN = ConfigDict(frozen=True)


def new_request_id() -> str:
    return f"req_{ULID()}"


class RunOptions(BaseModel):
    model_config = _FROZEN

    provider: ProviderName | None = None
    enable_review: bool | None = None
    include_debug: bool = False


class SecurityReport(BaseModel):
    model_config = _FROZEN

    score: float = 0.0
    verdict: Literal["safe", "suspicious", "blocked"] = "safe"
    signals: list[str] = []
    judge_used: bool = False
    leak_detected: bool = False


class KBSnippet(BaseModel):
    model_config = _FROZEN

    id: str
    category: str
    title: str
    content: str
    score: float = 0.0


class EnrichmentData(BaseModel):
    model_config = _FROZEN

    kb_snippets: list[KBSnippet] = []
    business_rules: list[str] = []
    sla_hours: int | None = None
    customer_profile: dict[str, Any] | None = None


class RouteDecision(BaseModel):
    model_config = _FROZEN

    tier: Tier
    provider: ProviderName
    model: str
    score: float
    reasons: list[str] = []


class StepResult(BaseModel):
    """Resultado de um LLMStep (também é o que o trace registra por chamada)."""

    model_config = _FROZEN

    name: str
    prompt_id: str
    prompt_version: str
    provider: str
    model: str
    rendered_messages: list[Message] = []
    raw_text: str = ""
    parsed: dict[str, Any] | None = None
    usage: Usage = Usage()
    latency_ms: int = 0
    attempts: int = 1
    repaired: bool = False
    fallback_used: bool = False


class PipelineContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    request_id: str = Field(default_factory=new_request_id)
    flow: str
    # Texto original do usuário: NUNCA logado, serializado ou enviado a provedor.
    raw_input: str = Field(repr=False, exclude=True)
    sanitized_input: str | None = None
    pii_vault: PIIVault = Field(default_factory=PIIVault, repr=False, exclude=True)
    canary: str | None = Field(default=None, repr=False)
    customer_id: str | None = None
    locale: str = "pt-BR"
    options: RunOptions = RunOptions()
    security: SecurityReport = SecurityReport()
    enrichment: EnrichmentData = EnrichmentData()
    route: RouteDecision | None = None
    steps: dict[str, StepResult] = {}
    result: dict[str, Any] | None = None
    warnings: list[str] = []
    degraded: bool = False

    @classmethod
    def new(cls, *, flow: str, raw_input: str, **kwargs: Any) -> Self:
        if kwargs.get("request_id") is None:
            kwargs.pop("request_id", None)
        return cls(flow=flow, raw_input=raw_input, **kwargs)

    def evolve(self, **changes: Any) -> Self:
        """Cópia com campos alterados (validação fica a cargo de quem chama)."""
        return self.model_copy(update=changes)

    def with_step(self, step: StepResult) -> Self:
        return self.evolve(steps={**self.steps, step.name: step})

    def with_warning(self, warning: str) -> Self:
        return self.evolve(warnings=[*self.warnings, warning])

    def get_step(self, name: str) -> StepResult | None:
        return self.steps.get(name)

    def step(self, name: str) -> StepResult:
        """Step obrigatório: ausência indica pipeline montado na ordem errada."""
        found = self.steps.get(name)
        if found is None:
            raise PipelineMisconfigured(f"step '{name}' ausente no contexto")
        return found

    def total_usage(self) -> Usage:
        total = Usage()
        for step in self.steps.values():
            total = total + step.usage
        return total
