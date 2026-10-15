"""Executor de stages com short-circuit, spans e entrega ao TraceSink (SPEC-002 §3)."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from prompt_engine.core.context import PipelineContext
from prompt_engine.core.errors import PromptEngineError, ShortCircuit
from prompt_engine.core.stage import Stage
from prompt_engine.core.tracing import NoopSink, PipelineRun, RunStatus, Span, SpanStatus, TraceSink

logger = logging.getLogger(__name__)


def _elapsed_ms(start: float) -> int:
    return max(0, round((time.perf_counter() - start) * 1000))


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

        async def finish(status: RunStatus, error_code: str | None = None) -> None:
            run = PipelineRun(
                ctx=ctx,
                spans=spans,
                status=status,
                started_at=run_started_at,
                duration_ms=_elapsed_ms(run_start),
                error_code=error_code,
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
                await finish("blocked", exc.code)
                raise
            except Exception as exc:
                spans.append(_span(stage.name, "error", started_at, start))
                code = exc.code if isinstance(exc, PromptEngineError) else "internal_error"
                await finish("error", code)
                raise
            spans.append(_span(stage.name, "ok", started_at, start))

        await finish("degraded" if ctx.degraded else "ok")
        return ctx
