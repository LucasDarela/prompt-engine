"""FlowRegistry (nome -> builder) e FlowRunner (cache de pipelines + execução) (SPEC-002 §2)."""

from __future__ import annotations

from collections.abc import Callable

from prompt_engine.core.context import PipelineContext, RunOptions
from prompt_engine.core.errors import FlowNotFoundError
from prompt_engine.core.pipeline import Pipeline
from prompt_engine.flows.deps import AppDeps

FlowBuilder = Callable[[AppDeps], Pipeline]


class FlowRegistry:
    def __init__(self) -> None:
        self._builders: dict[str, FlowBuilder] = {}

    def register(self, name: str, builder: FlowBuilder) -> None:
        self._builders[name] = builder

    def names(self) -> list[str]:
        return sorted(self._builders)

    def build(self, name: str, deps: AppDeps) -> Pipeline:
        if name not in self._builders:
            raise FlowNotFoundError(name)
        return self._builders[name](deps)


def default_flow_registry() -> FlowRegistry:
    from prompt_engine.flows.support_triage import FLOW_NAME, build_support_triage

    registry = FlowRegistry()
    registry.register(FLOW_NAME, build_support_triage)
    return registry


class FlowRunner:
    """Ponto de entrada da biblioteca: monta cada pipeline uma vez e executa requests."""

    def __init__(self, registry: FlowRegistry, deps: AppDeps) -> None:
        self.registry = registry
        self.deps = deps
        self._pipelines: dict[str, Pipeline] = {}

    def pipeline(self, flow: str) -> Pipeline:
        if flow not in self._pipelines:
            self._pipelines[flow] = self.registry.build(flow, self.deps)
        return self._pipelines[flow]

    async def run(
        self,
        flow: str,
        text: str,
        *,
        customer_id: str | None = None,
        options: RunOptions | None = None,
        request_id: str | None = None,
    ) -> PipelineContext:
        pipeline = self.pipeline(flow)
        ctx = PipelineContext.new(
            flow=flow,
            raw_input=text,
            customer_id=customer_id,
            options=options or RunOptions(),
            request_id=request_id,
        )
        return await pipeline.run(ctx)
