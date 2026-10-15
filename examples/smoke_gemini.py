"""Smoke test manual contra o Gemini real: roda os steps `classify` e `draft_reply`.

Gasta quota (2 chamadas, mais reparo se houver). Requer GEMINI_API_KEY no .env.

    python examples/smoke_gemini.py "O app do motorista não abre e temos 30 entregas hoje"
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import get_args

from prompt_engine.config import Settings
from prompt_engine.core.context import EnrichmentData, KBSnippet, PipelineContext
from prompt_engine.core.llm_step import LLMDeps, LLMStep
from prompt_engine.core.pipeline import Pipeline
from prompt_engine.llm.factory import build_pool
from prompt_engine.output.fallbacks import classify_fallback, draft_fallback
from prompt_engine.output.schemas import Category
from prompt_engine.output.validator import OutputValidator
from prompt_engine.prompts.registry import PromptRegistry
from prompt_engine.prompts.renderer import PromptRenderer

DEFAULT_MESSAGE = "Fomos cobrados duas vezes na mensalidade de outubro. Como resolvo?"
DEMO_KB = [
    KBSnippet(
        id="kb-billing-001",
        category="billing",
        title="Cobrança em duplicidade da assinatura",
        content="Cobranças em duplicidade são estornadas em até 7 dias úteis após a confirmação "
        "pelo financeiro. Solicite as datas ou comprovantes das cobranças.",
    )
]


async def main(message: str) -> None:
    settings = Settings()
    registry = PromptRegistry.default()
    deps = LLMDeps(
        registry=registry,
        renderer=PromptRenderer(registry),
        pool=build_pool(settings),
        validator=OutputValidator(max_repairs=settings.output_max_repairs),
        settings=settings,
    )
    pipeline = Pipeline(
        [
            LLMStep(
                name="classify",
                prompt_ref="support_triage.classify@latest",
                variables=lambda c: {
                    "message": c.sanitized_input,
                    "categories": list(get_args(Category)),
                },
                tier="fast",
                fallback=lambda c: classify_fallback(),
                deps=deps,
            ),
            LLMStep(
                name="draft_reply",
                prompt_ref="support_triage.draft_reply@latest",
                variables=lambda c: {
                    "message": c.sanitized_input,
                    "classification": c.step("classify").parsed,
                    "kb_snippets": [s.model_dump() for s in c.enrichment.kb_snippets],
                    "sla_hours": 4,
                },
                tier="balanced",
                fallback=lambda c: draft_fallback(sla_hours=4),
                deps=deps,
            ),
        ]
    )
    ctx = PipelineContext.new(flow="smoke", raw_input=message).evolve(
        sanitized_input=message,  # smoke sem PII: guardrails entram na Fase 4
        enrichment=EnrichmentData(kb_snippets=DEMO_KB),
    )
    ctx = await pipeline.run(ctx)
    for step in ctx.steps.values():
        print(f"\n=== {step.name} ({step.provider}/{step.model}) ===")
        print(f"tentativas={step.call_log} reparo={step.repaired} fallback={step.fallback_used}")
        usage = step.usage
        print(f"tokens={usage.input_tokens}/{usage.output_tokens} latência={step.latency_ms}ms")
        print(json.dumps(step.parsed, ensure_ascii=False, indent=2))
    print(f"\ndegraded={ctx.degraded} warnings={ctx.warnings}")


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or DEFAULT_MESSAGE))
