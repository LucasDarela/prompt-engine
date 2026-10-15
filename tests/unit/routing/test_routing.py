"""ComplexityScorer + Router (SPEC-006 §5)."""

from typing import Any

import pytest

from prompt_engine.core.context import (
    EnrichmentData,
    KBSnippet,
    PipelineContext,
    RunOptions,
    SecurityReport,
    StepResult,
)
from prompt_engine.core.errors import PipelineMisconfigured
from prompt_engine.llm.fake import FakeProvider
from prompt_engine.llm.pool import ProviderPool
from prompt_engine.routing.complexity import ComplexityScorer
from prompt_engine.routing.router import Router
from tests.support import make_settings

KB = EnrichmentData(kb_snippets=[KBSnippet(id="kb-1", category="other", title="t", content="c")])


def ctx_with(
    text: str = "Qual o horário de atendimento?",
    *,
    kb: EnrichmentData = KB,
    security: SecurityReport | None = None,
    options: RunOptions | None = None,
    **classify: Any,
) -> PipelineContext:
    parsed = {
        "category": "other",
        "urgency": "low",
        "sentiment": "neutral",
        "intents": ["question"],
        "complexity_hint": "simple",
    } | classify
    ctx = PipelineContext.new(flow="t", raw_input=text, options=options or RunOptions())
    ctx = ctx.evolve(sanitized_input=text, enrichment=kb, security=security or SecurityReport())
    return ctx.with_step(
        StepResult(
            name="classify",
            prompt_id="support_triage.classify",
            prompt_version="1.0.0",
            provider="fake",
            model="fake-fast",
            parsed=parsed,
        )
    )


SCORER = ComplexityScorer()


def test_simple_question_scores_zero() -> None:
    score, reasons = SCORER.score(ctx_with())
    assert score == 0.0
    assert reasons == []


@pytest.mark.parametrize(
    ("kwargs", "reason", "weight"),
    [
        ({"complexity_hint": "complex"}, "complexity_hint=complex", 0.35),
        ({"intents": ["refund", "complaint"]}, "multiple_intents", 0.15),
        ({"urgency": "critical"}, "urgency=critical", 0.15),
        ({"category": "legal"}, "sensitive_category=legal", 0.25),
        ({"sentiment": "very_negative"}, "sentiment=very_negative", 0.10),
    ],
)
def test_each_signal_contributes(kwargs: dict[str, Any], reason: str, weight: float) -> None:
    score, reasons = SCORER.score(ctx_with(**kwargs))
    assert reasons == [reason]
    assert score == pytest.approx(weight)


def test_legal_keywords_in_text() -> None:
    score, reasons = SCORER.score(ctx_with("Vou abrir um chargeback e chamar o Procon"))
    assert reasons == ["legal_keywords"]
    assert score == pytest.approx(0.25)


def test_long_input_no_kb_and_suspicious_security() -> None:
    ctx = ctx_with(
        "a " * 450,
        kb=EnrichmentData(),
        security=SecurityReport(score=0.5, verdict="suspicious"),
    )
    _, reasons = SCORER.score(ctx)
    assert set(reasons) == {"long_input", "no_kb_match", "security=suspicious"}


def test_score_is_capped_at_one() -> None:
    ctx = ctx_with(
        "processo judicial " + "a " * 450,
        kb=EnrichmentData(),
        complexity_hint="complex",
        intents=["a", "b"],
        urgency="critical",
        category="legal",
        sentiment="very_negative",
    )
    assert SCORER.score(ctx)[0] == 1.0


def test_requires_classify() -> None:
    with pytest.raises(PipelineMisconfigured):
        SCORER.score(PipelineContext.new(flow="t", raw_input="x"))


# --- Router ------------------------------------------------------------------------


def router(**kw: Any) -> Router:
    settings = make_settings(**kw)
    pool = ProviderPool(
        {"fake": FakeProvider({}), "gemini": FakeProvider({}, name="gemini")}, settings
    )
    return Router(SCORER, pool, settings)


async def test_simple_message_routes_fast() -> None:  # RT-1
    ctx = await router().run(ctx_with())
    assert ctx.route is not None
    assert (ctx.route.tier, ctx.route.provider, ctx.route.model) == ("fast", "fake", "fake-fast")


async def test_legal_multi_intent_routes_powerful() -> None:  # RT-2
    ctx = ctx_with(
        "Vou processar vocês e chamar o advogado, quero cancelar e o estorno",
        category="legal",
        intents=["cancel", "refund", "legal_threat"],
        complexity_hint="complex",
        urgency="critical",
    )
    route = (await router().run(ctx)).route
    assert route is not None
    assert route.tier == "powerful"
    assert route.reasons  # RT-4


async def test_medium_complexity_routes_balanced() -> None:
    ctx = ctx_with(complexity_hint="complex", intents=["a", "b"])  # 0.35 + 0.15 = 0.5
    route = (await router().run(ctx)).route
    assert route is not None
    assert route.tier == "balanced"
    assert route.score == pytest.approx(0.5)


async def test_force_tier_overrides_score() -> None:  # RT-3
    ctx = ctx_with(category="legal", complexity_hint="complex")
    route = (await router(router_force_tier="fast").run(ctx)).route
    assert route is not None
    assert route.tier == "fast"
    assert "forced_tier=fast" in route.reasons


async def test_request_provider_override_keeps_tier() -> None:
    ctx = ctx_with(options=RunOptions(provider="gemini"))
    route = (await router().run(ctx)).route
    assert route is not None
    assert (route.provider, route.model, route.tier) == ("gemini", "gemini-3.5-flash-lite", "fast")
