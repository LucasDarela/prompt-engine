"""Contrato HTTP (SPEC-001 §3). Separado dos modelos internos de propósito."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from prompt_engine.config import ProviderName, Tier
from prompt_engine.output.schemas import TriageResult

MAX_PAYLOAD_CHARS = 20_000  # limite de transporte; o limite de negócio é MAX_INPUT_CHARS


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProcessOptions(_Strict):
    provider: ProviderName | None = None
    enable_review: bool | None = None
    include_debug: bool = False


class ProcessPromptRequest(_Strict):
    input: str = Field(max_length=MAX_PAYLOAD_CHARS)
    flow: str = "support_triage"
    customer_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    options: ProcessOptions = ProcessOptions()

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "input": "Fomos cobrados duas vezes na mensalidade de outubro.",
                    "options": {"enable_review": True},
                }
            ]
        },
    )


class RouteMeta(BaseModel):
    tier: Tier
    score: float
    reasons: list[str]


class SecurityMeta(BaseModel):
    verdict: Literal["safe", "suspicious", "blocked"]
    score: float
    judge_used: bool


class TokensMeta(BaseModel):
    input: int
    output: int


class Meta(BaseModel):
    flow: str
    steps: list[str]  # "prompt_id@versão" na ordem executada
    provider: str | None
    models: dict[str, str]
    route: RouteMeta | None
    pii_redacted: list[str]
    security: SecurityMeta
    latency_ms: int
    tokens: TokensMeta
    degraded: bool
    warnings: list[str]
    debug: dict[str, Any] | None = None


class ProcessPromptResponse(BaseModel):
    request_id: str
    status: Literal["ok"] = "ok"
    triage: TriageResult
    meta: Meta


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    request_id: str
    status: Literal["error"] = "error"
    error: ErrorDetail


class PromptSummary(BaseModel):
    id: str
    version: str
    description: str
    techniques: list[str]
    tier: Tier
    output_schema: str | None


class ReadinessResponse(BaseModel):
    status: Literal["ready"]
    prompts: int
    kb_articles: int
    providers: list[str]
    flows: list[str]
