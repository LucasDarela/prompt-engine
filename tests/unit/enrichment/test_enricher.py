"""ContextEnrichment: KB por categoria + SLA, com orçamento de contexto (SPEC-004 §4)."""

from typing import Any

import pytest

from prompt_engine.core.context import KBSnippet, PipelineContext, StepResult
from prompt_engine.core.errors import PipelineMisconfigured
from prompt_engine.enrichment.enricher import ContextEnrichment
from prompt_engine.enrichment.knowledge_base import SLAPolicy
from tests.support import make_settings


class StubKB:
    def __init__(self, snippets: list[KBSnippet]) -> None:
        self.snippets = snippets
        self.queries: list[dict[str, Any]] = []

    def search(
        self, query: str, *, category: str | None = None, k: int = 3, min_score: float = 0.0
    ) -> list[KBSnippet]:
        self.queries.append({"query": query, "category": category, "k": k, "min": min_score})
        return self.snippets[:k]


def snippet(i: int, size: int = 50) -> KBSnippet:
    return KBSnippet(id=f"kb-{i}", category="billing", title=f"T{i}", content="x" * size, score=1.0)


SLA = {"billing": SLAPolicy(first_response_hours=4, escalate_if_urgency=["high", "critical"])}


def classified_ctx(category: str = "billing", intents: list[str] | None = None) -> PipelineContext:
    ctx = PipelineContext.new(flow="t", raw_input="x").evolve(sanitized_input="cobrado duas vezes")
    return ctx.with_step(
        StepResult(
            name="classify",
            prompt_id="support_triage.classify",
            prompt_version="1.0.0",
            provider="fake",
            model="fake-fast",
            parsed={"category": category, "intents": intents or ["refund"], "urgency": "high"},
        )
    )


async def test_enriches_with_snippets_and_sla() -> None:
    kb = StubKB([snippet(1), snippet(2)])
    stage = ContextEnrichment(kb, SLA, make_settings(kb_top_k=3, kb_min_score=0.2))
    ctx = await stage.run(classified_ctx())
    assert [s.id for s in ctx.enrichment.kb_snippets] == ["kb-1", "kb-2"]
    assert ctx.enrichment.sla_hours == 4
    (query,) = kb.queries
    assert query == {
        "query": "cobrado duas vezes refund",
        "category": "billing",
        "k": 3,
        "min": 0.2,
    }


async def test_context_budget_truncates_last_snippet() -> None:  # EN-3
    kb = StubKB([snippet(1, 600), snippet(2, 600), snippet(3, 600)])
    ctx = await ContextEnrichment(kb, SLA, make_settings(kb_max_chars=1000)).run(classified_ctx())
    snippets = ctx.enrichment.kb_snippets
    assert sum(len(s.content) for s in snippets) <= 1000
    assert [s.id for s in snippets] == ["kb-1", "kb-2"]
    assert snippets[1].content.endswith("…")


async def test_budget_skips_tiny_truncation() -> None:
    kb = StubKB([snippet(1, 980), snippet(2, 600)])
    ctx = await ContextEnrichment(kb, SLA, make_settings(kb_max_chars=1000)).run(classified_ctx())
    assert [s.id for s in ctx.enrichment.kb_snippets] == ["kb-1"]  # sobrariam só 20 chars


async def test_weak_tail_is_dropped_relative_to_best() -> None:
    scored = [
        snippet(1).model_copy(update={"score": 10.0}),
        snippet(2).model_copy(update={"score": 4.0}),
        snippet(3).model_copy(update={"score": 1.7}),
    ]
    stage = ContextEnrichment(StubKB(scored), SLA, make_settings(kb_min_relative_score=0.3))
    ctx = await stage.run(classified_ctx())
    assert [s.id for s in ctx.enrichment.kb_snippets] == ["kb-1", "kb-2"]


async def test_category_without_sla_has_none() -> None:
    ctx = await ContextEnrichment(StubKB([]), SLA, make_settings()).run(classified_ctx("other"))
    assert ctx.enrichment.sla_hours is None
    assert ctx.enrichment.kb_snippets == []


async def test_requires_classify_step() -> None:  # EN-6
    ctx = PipelineContext.new(flow="t", raw_input="x").evolve(sanitized_input="x")
    with pytest.raises(PipelineMisconfigured):
        await ContextEnrichment(StubKB([]), SLA, make_settings()).run(ctx)
