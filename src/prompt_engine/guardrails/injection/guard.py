"""InjectionGuard (stage 3): heurística, juiz LLM na zona cinzenta e decisão (SPEC-003 §3).

Roda DEPOIS do PIIRedaction: o juiz só vê `sanitized_input`.
"""

from __future__ import annotations

from typing import Literal

from prompt_engine.config import Settings
from prompt_engine.core.context import PipelineContext, SecurityReport
from prompt_engine.core.errors import PipelineMisconfigured, PromptInjectionDetected, ProviderError
from prompt_engine.core.llm_step import LLMDeps, LLMStep
from prompt_engine.core.stage import Stage
from prompt_engine.guardrails.injection.heuristics import InjectionHeuristics
from prompt_engine.output.schemas import InjectionJudgeOutput

# Warnings de stages anteriores que viram sinais fracos de injection.
WARNING_WEIGHTS: dict[str, float] = {"suspicious_charset": 0.15}

Verdict = Literal["safe", "suspicious", "blocked"]


def build_injection_judge(deps: LLMDeps) -> LLMStep:
    return LLMStep(
        name="injection_judge",
        prompt_ref="support_triage.injection_judge@latest",
        variables=lambda c: {"message": c.sanitized_input},
        tier="fast",
        fallback=lambda c: InjectionJudgeOutput(
            is_injection=False, confidence=0.0, reason="juiz indisponível"
        ),
        deps=deps,
    )


class InjectionGuard(Stage):
    name = "injection_guard"

    def __init__(
        self, heuristics: InjectionHeuristics, judge: LLMStep | None, settings: Settings
    ) -> None:
        self.heuristics = heuristics
        self.judge = judge
        self.settings = settings

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.sanitized_input is None:
            raise PipelineMisconfigured("InjectionGuard exige PIIRedaction antes")
        extra = {w: WARNING_WEIGHTS[w] for w in ctx.warnings if w in WARNING_WEIGHTS}
        scan = self.heuristics.scan(ctx.sanitized_input, extra_signals=extra)
        score, judge_used = scan.score, False

        if self.settings.injection_low <= score < self.settings.injection_high:
            ctx, judged_score = await self._ask_judge(ctx, score)
            judge_used = judged_score is not None
            score = score if judged_score is None else judged_score

        verdict = self._verdict(score)
        if verdict == "blocked":
            raise PromptInjectionDetected(score=score, signals=scan.signals)
        report = SecurityReport(
            score=round(score, 4),
            verdict=verdict,
            signals=scan.signals,
            judge_used=judge_used,
            leak_detected=ctx.security.leak_detected,
        )
        return ctx.evolve(security=report)

    async def _ask_judge(
        self, ctx: PipelineContext, score: float
    ) -> tuple[PipelineContext, float | None]:
        """Retorna (ctx, novo score) ou (ctx, None) se o juiz não pôde opinar."""
        if self.judge is None or not self.settings.injection_judge_enabled:
            return ctx.with_warning("injection_judge_disabled"), None
        try:
            judged = await self.judge.run(ctx)
        except ProviderError:
            return ctx.with_warning("injection_judge_unavailable"), None

        step = judged.step(self.judge.name)
        # Falha do juiz não degrada a resposta ao cliente: só o guard perde precisão.
        judged = judged.evolve(degraded=ctx.degraded)
        if step.fallback_used:
            return judged.with_warning("injection_judge_unavailable"), None
        opinion = InjectionJudgeOutput.model_validate(step.parsed)
        if opinion.is_injection:
            return judged, max(score, opinion.confidence)
        return judged, score * (1.0 - opinion.confidence)

    def _verdict(self, score: float) -> Verdict:
        if score >= self.settings.injection_high:
            return "blocked"
        if score >= self.settings.injection_low:
            return "suspicious"
        return "safe"
