"""Router (stage 6): escolhe tier/provider/modelo do draft pela complexidade (SPEC-006 §5)."""

from __future__ import annotations

from prompt_engine.config import Settings, Tier
from prompt_engine.core.context import PipelineContext, RouteDecision
from prompt_engine.core.stage import Stage
from prompt_engine.llm.pool import ProviderPool
from prompt_engine.routing.complexity import ComplexityScorer


class Router(Stage):
    name = "router"

    def __init__(self, scorer: ComplexityScorer, pool: ProviderPool, settings: Settings) -> None:
        self.scorer = scorer
        self.pool = pool
        self.settings = settings

    def _tier_for(self, score: float) -> Tier:
        if score >= self.settings.router_t2:
            return "powerful"
        if score >= self.settings.router_t1:
            return "balanced"
        return "fast"

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        score, reasons = self.scorer.score(ctx)
        tier = self.settings.router_force_tier or self._tier_for(score)
        if self.settings.router_force_tier:
            reasons = [*reasons, f"forced_tier={tier}"]
        target = self.pool.resolve(tier, ctx.options.provider)
        route = RouteDecision(
            tier=target.tier,
            provider=target.provider,
            model=target.model,
            score=score,
            reasons=reasons,
        )
        return ctx.evolve(route=route)
