"""Executor de stages com short-circuit, spans e entrega ao TraceSink (SPEC-002 §3)."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from prompt_engine.core.context import PipelineContext
from prompt_engine.core.errors import PromptEngineError, ShortCircuit
from prompt_engine.core.stage import Stage
from prompt_engine.core.tracing import NoopSink, PipelineRun, RunStatus, Span, SpanStatus, TraceSink

logger = logging.getLogger(__name__)


def _elapsed_ms(start: float) -> int:
    return max(0, round((time.perf_counter() - start) * 1000))


# Atributos seguros de exceções de domínio que valem ir para o trace (nunca o texto do usuário).
_ERROR_ATTRIBUTES = ("reason", "score", "signals", "provider", "step", "errors")


def _error_details(error: Exception | None) -> dict[str, Any]:
    if error is None:
        return {}
    return {a: getattr(error, a) for a in _ERROR_ATTRIBUTES if hasattr(error, a)}


def _span(name: str, status: SpanStatus, started_at: datetime, start: float) -> Span:
    return Span(name=name, status=status, started_at=started_at, duration_ms=_elapsed_ms(start))


class Pipeline:
    def __init__(self, stages: Sequence[Stage], sink: TraceSink | None = None) -> None:
        names = [s.name for s in stages]
        duplicated = {n for n in names if names.count(n) > 1}
        if duplicated:
            raise ValueError(f"nomes de stage duplicados: {sorted(duplicated)}")
        self.stages = list(stages)
        self.sink: TraceSink = sink or NoopSink()

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        run_started_at = datetime.now(UTC)
        run_start = time.perf_counter()
        spans: list[Span] = []

        async def finish(status: RunStatus, error: Exception | None = None) -> None:
            code = None
            if error is not None:
                code = error.code if isinstance(error, PromptEngineError) else "internal_error"
            run = PipelineRun(
                ctx=ctx,
                spans=spans,
                status=status,
                started_at=run_started_at,
                duration_ms=_elapsed_ms(run_start),
                error_code=code,
                error_details=_error_details(error),
            )
            logger.info(
                "pipeline.finished",
                extra={
                    "flow": ctx.flow,
                    "status": status,
                    "error_code": code,
                    "duration_ms": run.duration_ms,
                    "degraded": ctx.degraded,
                },
            )
            try:
                await self.sink.record(run)
            except Exception:  # observabilidade nunca derruba a request (OBS-4)
                logger.warning("trace_sink_failed", exc_info=True)

        for stage in self.stages:
            started_at = datetime.now(UTC)
            start = time.perf_counter()
            if not stage.is_enabled(ctx):
                spans.append(_span(stage.name, "skipped", started_at, start))
                continue
            try:
                ctx = await stage.run(ctx)
            except ShortCircuit as exc:
                spans.append(_span(stage.name, "blocked", started_at, start))
                await finish("blocked", exc)
                raise
            except Exception as exc:
                spans.append(_span(stage.name, "error", started_at, start))
                await finish("error", exc)
                raise
            spans.append(_span(stage.name, "ok", started_at, start))

        await finish("degraded" if ctx.degraded else "ok")
        return ctx
