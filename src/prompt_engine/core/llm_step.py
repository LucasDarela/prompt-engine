"""LLMStep: stage genérico que liga prompt, provider e validador (SPEC-002 §4).

render -> checagem de PII -> provider (via pool) -> canary -> validação com reparo/fallback.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from prompt_engine.config import Settings, Tier
from prompt_engine.core.context import PipelineContext, StepResult, Usage
from prompt_engine.core.errors import PipelineMisconfigured
from prompt_engine.core.stage import Stage
from prompt_engine.llm.base import LLMRequest, Message
from prompt_engine.llm.pool import ProviderPool, Target
from prompt_engine.output.validator import OutputValidator, ValidationOutcome
from prompt_engine.prompts.models import PromptTemplate
from prompt_engine.prompts.registry import PromptRegistry
from prompt_engine.prompts.renderer import PromptRenderer

REPAIR_PROMPT = "common.repair_json@latest"

Variables = Callable[[PipelineContext], dict[str, Any]]
Fallback = Callable[[PipelineContext], BaseModel]


@dataclass(frozen=True)
class LLMDeps:
    registry: PromptRegistry
    renderer: PromptRenderer
    pool: ProviderPool
    validator: OutputValidator
    settings: Settings


@dataclass
class _CallStats:
    usage: Usage
    latency_ms: int
    log: list[str]


class LLMStep(Stage):
    def __init__(
        self,
        *,
        name: str,
        prompt_ref: str,
        variables: Variables,
        tier: Tier | Literal["routed"],
        fallback: Fallback,
        deps: LLMDeps,
        enabled: Callable[[PipelineContext], bool] | None = None,
    ) -> None:
        self.name = name
        self.template = deps.registry.get(prompt_ref)
        schema = deps.registry.schema_for(self.template)
        if schema is None:
            raise PipelineMisconfigured(f"{name}: prompt {self.template.ref} sem output_schema")
        self.schema: type[BaseModel] = schema
        self._variables = variables
        self._tier = tier
        self._fallback = fallback
        self._deps = deps
        self._enabled = enabled

    def is_enabled(self, ctx: PipelineContext) -> bool:
        return self._enabled(ctx) if self._enabled else True

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        messages = self._render(self.template, {**self._variables(ctx), "canary": ctx.canary}, ctx)
        target = self._target(ctx)
        result = await self._deps.pool.generate(target, self._request(self.template, messages, ctx))
        stats = _CallStats(result.response.usage, result.response.latency_ms, list(result.attempts))

        outcome: ValidationOutcome[BaseModel]
        if ctx.canary in result.response.text:
            # System prompt vazou: descarta a saída sem pedir reparo ao mesmo modelo.
            outcome = ValidationOutcome(
                value=self._fallback(ctx),
                raw_text="",
                attempts=1,
                fallback_used=True,
                errors=["canary_leak"],
            )
            ctx = ctx.evolve(security=ctx.security.model_copy(update={"leak_detected": True}))
            ctx = ctx.with_warning("canary_leak")
        else:
            outcome = await self._deps.validator.validate(
                result.response.text,
                self.schema,
                step=self.name,
                repair=self._repairer(ctx, target, stats),
                fallback=lambda: self._fallback(ctx),
            )

        ctx = ctx.with_step(
            StepResult(
                name=self.name,
                prompt_id=self.template.id,
                prompt_version=self.template.version,
                provider=result.response.provider,
                model=result.response.model,
                rendered_messages=messages,
                raw_text=outcome.raw_text,
                parsed=outcome.value.model_dump(mode="json"),
                usage=stats.usage,
                latency_ms=stats.latency_ms,
                attempts=len(stats.log),
                call_log=stats.log,
                repaired=outcome.repaired,
                fallback_used=outcome.fallback_used,
            )
        )
        if outcome.fallback_used:
            ctx = ctx.evolve(degraded=True).with_warning(f"{self.name}_fallback")
        return ctx

    # --- internos -------------------------------------------------------------

    def _render(
        self, template: PromptTemplate, variables: dict[str, Any], ctx: PipelineContext
    ) -> list[Message]:
        messages = self._deps.renderer.render(template, variables)
        if any(ctx.pii_vault.contains_value_in(m.content) for m in messages):
            raise PipelineMisconfigured(f"{self.name}: PII real no prompt renderizado")
        return messages

    def _target(self, ctx: PipelineContext) -> Target:
        if self._tier == "routed":
            if ctx.route is None:
                raise PipelineMisconfigured(f"{self.name}: tier 'routed' exige ctx.route")
            return Target(ctx.route.provider, ctx.route.model, ctx.route.tier)
        return self._deps.pool.resolve(self._tier, ctx.options.provider)

    def _request(
        self, template: PromptTemplate, messages: list[Message], ctx: PipelineContext
    ) -> LLMRequest:
        return LLMRequest(
            messages=messages,
            model="",  # definido pelo pool a partir do Target
            temperature=template.params.temperature,
            max_output_tokens=template.params.max_output_tokens,
            json_schema=self.schema.model_json_schema(),
            metadata={"step": self.name, "request_id": ctx.request_id},
        )

    def _repairer(self, ctx: PipelineContext, target: Target, stats: _CallStats) -> Any:
        repair_template = self._deps.registry.get(REPAIR_PROMPT)
        schema_json = json.dumps(
            self.schema.model_json_schema(), ensure_ascii=False, sort_keys=True
        )

        async def repair(raw: str, errors: list[str]) -> str:
            variables = {
                "invalid_output": raw,
                "errors": errors,
                "schema_json": schema_json,
                "canary": ctx.canary,
            }
            messages = self._render(repair_template, variables, ctx)
            request = self._request(repair_template, messages, ctx).model_copy(
                update={"metadata": {"step": f"{self.name}.repair", "request_id": ctx.request_id}}
            )
            repair_target = self._deps.pool.resolve(repair_template.tier, target.provider)
            result = await self._deps.pool.generate(repair_target, request)
            stats.usage = stats.usage + result.response.usage
            stats.latency_ms += result.response.latency_ms
            stats.log.extend(f"repair:{attempt}" for attempt in result.attempts)
            return result.response.text

        return repair
