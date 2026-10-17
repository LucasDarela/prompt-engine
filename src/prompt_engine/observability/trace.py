"""Trace persistível de uma execução do pipeline (SPEC-008 §2). Nunca contém PII real."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from prompt_engine import __version__
from prompt_engine.core.context import RouteDecision, SecurityReport, StepResult, Usage
from prompt_engine.core.tracing import PipelineRun, Span
from prompt_engine.core.vault import PIIVault
from prompt_engine.guardrails.pii.redaction import redact
from prompt_engine.llm.base import Message
from prompt_engine.llm.pricing import Pricing


class LLMCallRecord(BaseModel):
    step: str
    prompt_id: str
    prompt_version: str
    provider: str
    model: str
    attempts: int
    call_log: list[str]
    messages: list[Message]  # prompt FINAL renderizado (já mascarado)
    response_text: str  # bruto, antes da validação
    repaired: bool
    fallback_used: bool
    usage: Usage
    latency_ms: int
    cost_usd_estimate: float | None


class Trace(BaseModel):
    request_id: str
    flow: str
    created_at: datetime
    status: Literal["ok", "blocked", "error", "degraded"]
    error_code: str | None
    error_details: dict[str, Any]
    input_redacted: str
    input_raw: str | None  # só com OBS_STORE_RAW_INPUT=true (LGPD: desligado por padrão)
    pii_kinds: list[str]
    security: SecurityReport
    route: RouteDecision | None
    spans: list[Span]
    llm_calls: list[LLMCallRecord]
    output: dict[str, Any] | None  # resposta enviada ao frontend, com PII remascarada
    warnings: list[str]
    degraded: bool
    total_latency_ms: int
    total_tokens: Usage
    total_cost_usd_estimate: float | None
    app_version: str


def _call_record(step: StepResult, pricing: Pricing | None) -> LLMCallRecord:
    return LLMCallRecord(
        step=step.name,
        prompt_id=step.prompt_id,
        prompt_version=step.prompt_version,
        provider=step.provider,
        model=step.model,
        attempts=step.attempts,
        call_log=step.call_log,
        messages=step.rendered_messages,
        response_text=step.raw_text,
        repaired=step.repaired,
        fallback_used=step.fallback_used,
        usage=step.usage,
        latency_ms=step.latency_ms,
        cost_usd_estimate=pricing.cost(step.model, step.usage) if pricing else None,
    )


def _mask(value: Any, vault: PIIVault) -> Any:
    if isinstance(value, str):
        return vault.mask(value)
    if isinstance(value, dict):
        return {k: _mask(v, vault) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, vault) for v in value]
    return value


def build_trace(
    run: PipelineRun, *, store_raw_input: bool = False, pricing: Pricing | None = None
) -> Trace:
    ctx = run.ctx
    calls = [_call_record(step, pricing) for step in ctx.steps.values()]
    costs = [c.cost_usd_estimate for c in calls]
    # Bloqueado antes do PIIRedaction (ex.: input longo demais): mascara agora, vault descartável.
    redacted = (
        ctx.sanitized_input
        if ctx.sanitized_input is not None
        else redact(ctx.raw_input, PIIVault())
    )
    return Trace(
        request_id=ctx.request_id,
        flow=ctx.flow,
        created_at=run.started_at,
        status=run.status,
        error_code=run.error_code,
        error_details=run.error_details,
        input_redacted=redacted,
        input_raw=ctx.raw_input if store_raw_input else None,
        pii_kinds=ctx.pii_vault.kinds(),
        security=ctx.security,
        route=ctx.route,
        spans=run.spans,
        llm_calls=calls,
        output=_mask(ctx.result, ctx.pii_vault) if ctx.result is not None else None,
        warnings=ctx.warnings,
        degraded=ctx.degraded,
        total_latency_ms=run.duration_ms,
        total_tokens=ctx.total_usage(),
        total_cost_usd_estimate=(
            sum(c for c in costs if c is not None) if costs and None not in costs else None
        ),
        app_version=__version__,
    )
