"""Flow `support_triage`: montagem dos stages e AssembleTriage (SPEC-002 §5, SPEC-007 §4).

InputValidation -> PIIRedaction -> InjectionGuard -> classify -> ContextEnrichment -> Router
-> draft_reply -> review_reply (opcional) -> AssembleTriage -> PIIRehydration
"""

from __future__ import annotations

import re
from typing import Any, get_args

from prompt_engine.config import Settings
from prompt_engine.core.context import PipelineContext
from prompt_engine.core.llm_step import LLMDeps, LLMStep
from prompt_engine.core.pipeline import Pipeline
from prompt_engine.core.stage import Stage
from prompt_engine.enrichment.enricher import ContextEnrichment
from prompt_engine.flows.deps import AppDeps
from prompt_engine.guardrails.injection.guard import InjectionGuard, build_injection_judge
from prompt_engine.guardrails.input_validation import InputValidation
from prompt_engine.guardrails.pii.redaction import PIIRedaction
from prompt_engine.guardrails.pii.rehydration import PIIRehydration
from prompt_engine.output.cleaners import clean_text
from prompt_engine.output.fallbacks import classify_fallback, draft_fallback, review_fallback
from prompt_engine.output.schemas import (
    Category,
    ClassifyOutput,
    DraftOutput,
    ReviewOutput,
    TriageResult,
)
from prompt_engine.routing.complexity import ComplexityScorer
from prompt_engine.routing.router import Router

FLOW_NAME = "support_triage"
CLASSIFICATION_FIELDS = ("category", "urgency", "sentiment", "intents")

# Defesa determinística: o assistente não executa ações, então a resposta não pode afirmar
# que algo já foi feito. O prompt proíbe; isto garante (resposta é escalada se escapar).
UNVERIFIED_ACTION_RE = re.compile(
    r"\b(registramos|registrei|encaminhamos|encaminhei|estornamos|estornei|reembolsamos"
    r"|cancelamos|cancelei|liberamos|liberei|corrigimos|corrigi|(foi|foram) (\w+mente )?"
    r"(registrad|encaminhad|estornad|cancelad|liberad|corrigid)\w*)\b",
    re.IGNORECASE,
)

# Política do Chopp Hub: o assistente nunca promete nada financeiro (desconto, promoção, crédito,
# estorno, reembolso, prazo de devolução). Mencionar oferta ou prometer devolução => escalar.
FINANCIAL_PROMISE_RE = re.compile(
    r"\b(desconto\w*|promo[cç](?:[aã]o|[oõ]es)|cupo(?:m|ns)|b[oô]nus|brinde\w*|cashback"
    r"|isen[cç][aã]o)\b"
    r"|\b(ser[aá]|ser[aã]o|vai ser|v[aã]o ser)\s+"
    r"(estornad|reembolsad|creditad|devolvid|ressarcid)\w*"
    r"|\b(estorn|reembols|devolu[cç]|ressarc|cr[eé]dit)\w*[^.!?]{0,40}"
    r"\b(em|at[eé])\s+(at[eé]\s+)?\d+\s*(dias?|horas?)",
    re.IGNORECASE,
)

# Palavras frequentes que SEMPRE levam acento. Duas ou mais sem acento = texto sem acentuação
# (falha intermitente observada no Gemini em JSON mode). Só sinaliza: corrigir acento é arriscado.
UNACCENTED_WORDS_RE = re.compile(
    r"\b(voce|voces|nao|sera|serao|ola|solicitacao|periodo|tambem|informacao|atencao|entao"
    r"|duvida|numero|horario|ate|ja|estao|ultima|proximo|analise|historico|cobranca)\b",
    re.IGNORECASE,
)
MISSING_ACCENTS_MIN_HITS = 2


class AssembleTriage(Stage):
    """Monta o TriageResult e decide `needs_human` com razões explícitas."""

    name = "assemble_triage"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        classify = ClassifyOutput.model_validate(ctx.step("classify").parsed)
        draft = DraftOutput.model_validate(ctx.step("draft_reply").parsed)
        review_step = ctx.get_step("review_reply")
        review = ReviewOutput.model_validate(review_step.parsed) if review_step else None

        reply = draft.suggested_reply
        if review is not None and not review.approved and review.revised_reply:
            reply = review.revised_reply

        conditions = [
            (draft.needs_human, "draft_requested_human"),
            (ctx.security.verdict == "suspicious", "security=suspicious"),
            (classify.urgency == "critical", "urgency=critical"),
            (classify.category == "legal", "category=legal"),
            (draft.confidence < self.settings.confidence_min, "low_confidence"),
            (ctx.degraded, "degraded"),
            (bool(UNVERIFIED_ACTION_RE.search(reply)), "unverified_action_claim"),
            (bool(FINANCIAL_PROMISE_RE.search(reply)), "financial_promise"),
        ]
        reasons = [reason for condition, reason in conditions if condition]

        result = TriageResult(
            category=classify.category,
            urgency=classify.urgency,
            sentiment=classify.sentiment,
            summary=clean_text(draft.summary),
            entities=classify.entities,
            suggested_reply=clean_text(reply),
            needs_human=bool(reasons),
            confidence=draft.confidence,
            escalation_reasons=reasons,
        )
        ctx = ctx.evolve(result=result.model_dump(mode="json"))
        if len(UNACCENTED_WORDS_RE.findall(result.suggested_reply)) >= MISSING_ACCENTS_MIN_HITS:
            ctx = ctx.with_warning("missing_accents")
        return ctx


def _kb_vars(ctx: PipelineContext) -> dict[str, Any]:
    return {
        "kb_snippets": [
            {"id": s.id, "title": s.title, "content": s.content} for s in ctx.enrichment.kb_snippets
        ],
        "sla_hours": ctx.enrichment.sla_hours,
    }


def _classification(ctx: PipelineContext) -> dict[str, Any]:
    parsed = ctx.step("classify").parsed or {}
    return {key: parsed[key] for key in CLASSIFICATION_FIELDS if key in parsed}


def make_classify_step(llm: LLMDeps) -> LLMStep:
    """Step de classificação (reusado pelos evals de consistência, idêntico ao de produção)."""
    return LLMStep(
        name="classify",
        prompt_ref="support_triage.classify@latest",
        variables=lambda c: {
            "message": c.sanitized_input,
            "categories": list(get_args(Category)),
        },
        tier="fast",
        fallback=lambda c: classify_fallback(),
        deps=llm,
    )


def build_guardrails(deps: AppDeps) -> list[Stage]:
    """Validação -> PII -> injection (reusado pelos evals de injection)."""
    return [
        InputValidation(deps.settings),
        PIIRedaction(),  # antes do guard: o juiz LLM só vê texto mascarado
        InjectionGuard(deps.heuristics, build_injection_judge(deps.llm), deps.settings),
    ]


def build_support_triage(deps: AppDeps) -> Pipeline:
    settings, llm = deps.settings, deps.llm

    def review_enabled(ctx: PipelineContext) -> bool:
        requested = ctx.options.enable_review
        return settings.enable_review_step if requested is None else requested

    return Pipeline(
        [
            *build_guardrails(deps),
            make_classify_step(llm),
            ContextEnrichment(deps.kb, deps.sla, settings),
            Router(ComplexityScorer(), llm.pool, settings),
            LLMStep(
                name="draft_reply",
                prompt_ref="support_triage.draft_reply@latest",
                variables=lambda c: {
                    "message": c.sanitized_input,
                    "classification": _classification(c),
                    **_kb_vars(c),
                },
                tier="routed",
                fallback=lambda c: draft_fallback(c.enrichment.sla_hours),
                deps=llm,
            ),
            LLMStep(
                name="review_reply",
                prompt_ref="support_triage.review_reply@latest",
                variables=lambda c: {
                    "message": c.sanitized_input,
                    "draft": (c.step("draft_reply").parsed or {}).get("suggested_reply", ""),
                    **_kb_vars(c),
                },
                tier="fast",
                fallback=lambda c: review_fallback(),
                deps=llm,
                enabled=review_enabled,
            ),
            AssembleTriage(settings),
            PIIRehydration(settings),
        ],
        sink=deps.sink,
    )
