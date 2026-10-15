"""Traduz o PipelineContext final para o contrato HTTP. Só apresentação, sem regra de negócio."""

from __future__ import annotations

from typing import Any

from prompt_engine.api.schemas import (
    Meta,
    ProcessPromptResponse,
    RouteMeta,
    SecurityMeta,
    TokensMeta,
)
from prompt_engine.core.context import PipelineContext
from prompt_engine.core.errors import PipelineMisconfigured
from prompt_engine.output.schemas import TriageResult


def _debug(ctx: PipelineContext) -> dict[str, Any]:
    """Prompts renderizados e respostas brutas. Já estão mascarados (nunca contêm PII real)."""
    return {
        "steps": {
            name: {
                "prompt": f"{step.prompt_id}@{step.prompt_version}",
                "messages": [m.model_dump() for m in step.rendered_messages],
                "raw_text": step.raw_text,
                "call_log": step.call_log,
                "latency_ms": step.latency_ms,
            }
            for name, step in ctx.steps.items()
        }
    }


def to_response(ctx: PipelineContext, *, latency_ms: int, debug: bool) -> ProcessPromptResponse:
    if ctx.result is None:
        raise PipelineMisconfigured("flow terminou sem resultado")
    steps = list(ctx.steps.values())
    usage = ctx.total_usage()
    provider = ctx.route.provider if ctx.route else (steps[0].provider if steps else None)
    meta = Meta(
        flow=ctx.flow,
        steps=[f"{s.prompt_id}@{s.prompt_version}" for s in steps],
        provider=provider,
        models={s.name: s.model for s in steps},
        route=(
            RouteMeta(tier=ctx.route.tier, score=ctx.route.score, reasons=ctx.route.reasons)
            if ctx.route
            else None
        ),
        pii_redacted=ctx.pii_vault.kinds(),
        security=SecurityMeta(
            verdict=ctx.security.verdict,
            score=ctx.security.score,
            judge_used=ctx.security.judge_used,
        ),
        latency_ms=latency_ms,
        tokens=TokensMeta(input=usage.input_tokens, output=usage.output_tokens),
        degraded=ctx.degraded,
        warnings=ctx.warnings,
        debug=_debug(ctx) if debug else None,
    )
    return ProcessPromptResponse(
        request_id=ctx.request_id, triage=TriageResult.model_validate(ctx.result), meta=meta
    )
