"""ContextEnrichment (stage 5): busca na KB pela categoria e anexa o SLA (SPEC-004 §4)."""

from __future__ import annotations

from collections.abc import Mapping

from prompt_engine.config import Settings
from prompt_engine.core.context import KBSnippet, PipelineContext
from prompt_engine.core.stage import Stage
from prompt_engine.enrichment.knowledge_base import KnowledgeBase, SLAPolicy

ELLIPSIS = "…"
MIN_TRUNCATED_CHARS = 40  # abaixo disso, um trecho cortado mais confunde do que ajuda


def drop_weak(snippets: list[KBSnippet], min_relative: float) -> list[KBSnippet]:
    """Remove a cauda fraca: score < min_relative x melhor score (independe da escala do BM25)."""
    if not snippets:
        return snippets
    best = max(s.score for s in snippets)
    return [s for s in snippets if s.score >= best * min_relative]


def apply_budget(snippets: list[KBSnippet], max_chars: int) -> list[KBSnippet]:
    """Limita o total de `content`; o último snippet que não cabe é truncado com reticências."""
    kept: list[KBSnippet] = []
    remaining = max_chars
    for snippet in snippets:
        if len(snippet.content) <= remaining:
            kept.append(snippet)
            remaining -= len(snippet.content)
            continue
        if remaining >= MIN_TRUNCATED_CHARS:
            content = snippet.content[: remaining - len(ELLIPSIS)].rstrip() + ELLIPSIS
            kept.append(snippet.model_copy(update={"content": content}))
        break
    return kept


class ContextEnrichment(Stage):
    name = "context_enrichment"

    def __init__(self, kb: KnowledgeBase, sla: Mapping[str, SLAPolicy], settings: Settings) -> None:
        self.kb = kb
        self.sla = sla
        self.settings = settings

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        classified = ctx.step("classify").parsed or {}
        category = classified.get("category")
        intents = [str(i) for i in classified.get("intents", [])]
        query = " ".join([ctx.sanitized_input or "", *intents]).strip()
        snippets = self.kb.search(
            query,
            category=category,
            k=self.settings.kb_top_k,
            min_score=self.settings.kb_min_score,
        )
        policy = self.sla.get(category) if category else None
        enrichment = ctx.enrichment.model_copy(
            update={
                "kb_snippets": apply_budget(
                    drop_weak(snippets, self.settings.kb_min_relative_score),
                    self.settings.kb_max_chars,
                ),
                "sla_hours": policy.first_response_hours if policy else None,
            }
        )
        return ctx.evolve(enrichment=enrichment)
