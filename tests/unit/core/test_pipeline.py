from dataclasses import dataclass, field

import pytest

from prompt_engine.core.context import PipelineContext
from prompt_engine.core.errors import InputValidationError
from prompt_engine.core.pipeline import Pipeline
from prompt_engine.core.stage import Stage
from prompt_engine.core.tracing import PipelineRun


class Append(Stage):
    """Stage de teste: concatena um marcador em sanitized_input."""

    def __init__(self, name: str, enabled: bool = True) -> None:
        self.name = name
        self._enabled = enabled
        self.calls = 0

    def is_enabled(self, ctx: PipelineContext) -> bool:
        return self._enabled

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        self.calls += 1
        return ctx.evolve(sanitized_input=(ctx.sanitized_input or "") + self.name)


class Boom(Stage):
    def __init__(self, exc: Exception) -> None:
        self.name = "boom"
        self.exc = exc

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        raise self.exc


@dataclass
class RecordingSink:
    runs: list[PipelineRun] = field(default_factory=list)

    async def record(self, run: PipelineRun) -> None:
        self.runs.append(run)


class BrokenSink:
    async def record(self, run: PipelineRun) -> None:
        raise OSError("disco cheio")


def ctx() -> PipelineContext:
    return PipelineContext.new(flow="test", raw_input="oi")


async def test_runs_stages_in_order_and_accumulates() -> None:  # CORE-1
    result = await Pipeline([Append("a"), Append("b"), Append("c")]).run(ctx())
    assert result.sanitized_input == "abc"


async def test_each_stage_produces_exactly_one_span() -> None:  # CORE-3
    sink = RecordingSink()
    await Pipeline([Append("a"), Append("b")], sink=sink).run(ctx())
    (run,) = sink.runs
    assert [s.name for s in run.spans] == ["a", "b"]
    assert all(s.duration_ms >= 0 and s.status == "ok" for s in run.spans)
    assert run.status == "ok"


async def test_short_circuit_stops_pipeline_and_marks_blocked() -> None:  # CORE-2
    sink = RecordingSink()
    after = Append("after")
    pipeline = Pipeline([Append("a"), Boom(InputValidationError("empty_input")), after], sink=sink)
    with pytest.raises(InputValidationError):
        await pipeline.run(ctx())
    assert after.calls == 0
    (run,) = sink.runs
    assert run.status == "blocked"
    assert [s.status for s in run.spans] == ["ok", "blocked"]
    assert run.error_code == "invalid_input"
    assert run.error_details == {"reason": "empty_input"}
    assert run.ctx.sanitized_input == "a"  # contexto no ponto da falha


async def test_injection_block_keeps_score_and_signals() -> None:
    from prompt_engine.core.errors import PromptInjectionDetected

    sink = RecordingSink()
    exc = PromptInjectionDetected(score=0.9, signals=["ignore_instructions"])
    with pytest.raises(PromptInjectionDetected):
        await Pipeline([Boom(exc)], sink=sink).run(ctx())
    assert sink.runs[0].error_details == {"score": 0.9, "signals": ["ignore_instructions"]}


async def test_unexpected_error_marks_error_and_reraises() -> None:
    sink = RecordingSink()
    with pytest.raises(RuntimeError):
        await Pipeline([Boom(RuntimeError("bug"))], sink=sink).run(ctx())
    assert sink.runs[0].status == "error"
    assert sink.runs[0].spans[0].status == "error"
    assert sink.runs[0].error_code == "internal_error"


async def test_disabled_stage_is_skipped_with_span() -> None:  # CORE-4 (nível pipeline)
    sink = RecordingSink()
    skipped = Append("review", enabled=False)
    result = await Pipeline([Append("a"), skipped], sink=sink).run(ctx())
    assert skipped.calls == 0
    assert result.sanitized_input == "a"
    assert [s.status for s in sink.runs[0].spans] == ["ok", "skipped"]


async def test_degraded_context_marks_run_degraded() -> None:
    class Degrade(Stage):
        name = "degrade"

        async def run(self, c: PipelineContext) -> PipelineContext:
            return c.evolve(degraded=True)

    sink = RecordingSink()
    await Pipeline([Degrade()], sink=sink).run(ctx())
    assert sink.runs[0].status == "degraded"


async def test_sink_failure_does_not_break_request() -> None:  # OBS-4 (base)
    result = await Pipeline([Append("a")], sink=BrokenSink()).run(ctx())
    assert result.sanitized_input == "a"


async def test_default_sink_is_noop() -> None:
    result = await Pipeline([Append("a")]).run(ctx())
    assert result.sanitized_input == "a"


def test_stage_names_must_be_unique() -> None:
    with pytest.raises(ValueError, match="duplicad"):
        Pipeline([Append("a"), Append("a")])
