"""AssembleTriage: junta classify + draft (+ review) no TriageResult (SPEC-007 §4)."""

from typing import Any

import pytest

from prompt_engine.core.context import PipelineContext, SecurityReport, StepResult
from prompt_engine.core.errors import PipelineMisconfigured
from prompt_engine.flows.support_triage import AssembleTriage
from tests.support import make_settings

CLASSIFY = {
    "reasoning": "interno, nunca vai para a resposta",
    "category": "billing",
    "urgency": "high",
    "sentiment": "negative",
    "entities": {"order_id": "PED-1"},
    "intents": ["refund"],
    "complexity_hint": "simple",
}
DRAFT = {
    "summary": "Cobrança duplicada na mensalidade.",
    "suggested_reply": "Olá! **Sentimos muito** pela cobrança em duplicidade.",
    "needs_human": False,
    "confidence": 0.9,
    "used_kb_ids": ["kb-billing-001"],
}


def step(name: str, parsed: dict[str, Any]) -> StepResult:
    return StepResult(
        name=name,
        prompt_id=f"x.{name}",
        prompt_version="1.0.0",
        provider="fake",
        model="m",
        parsed=parsed,
    )


def make_ctx(
    classify: dict[str, Any] | None = None,
    draft: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
    **ctx_kw: Any,
) -> PipelineContext:
    ctx = PipelineContext.new(flow="support_triage", raw_input="x").evolve(**ctx_kw)
    ctx = ctx.with_step(step("classify", CLASSIFY | (classify or {})))
    ctx = ctx.with_step(step("draft_reply", DRAFT | (draft or {})))
    if review is not None:
        ctx = ctx.with_step(step("review_reply", review))
    return ctx


async def assemble(ctx: PipelineContext, **settings: Any) -> dict[str, Any]:
    result = (await AssembleTriage(make_settings(**settings)).run(ctx)).result
    assert result is not None
    return result


async def test_happy_path_builds_clean_result() -> None:
    result = await assemble(make_ctx())
    assert result["category"] == "billing"
    assert result["summary"] == "Cobrança duplicada na mensalidade."
    # markdown (**negrito**) removido pelo clean_text
    assert result["suggested_reply"] == "Olá! Sentimos muito pela cobrança em duplicidade."
    assert result["entities"] == {"order_id": "PED-1"}
    assert result["needs_human"] is False
    assert result["escalation_reasons"] == []
    assert "reasoning" not in result  # raciocínio interno nunca vai para o frontend


async def test_rejected_review_replaces_reply() -> None:
    review = {
        "approved": False,
        "issues": ["prazo"],
        "revised_reply": "Olá! Versão revisada da resposta.",
    }
    result = await assemble(make_ctx(review=review))
    assert result["suggested_reply"] == "Olá! Versão revisada da resposta."


async def test_approved_review_keeps_draft() -> None:
    result = await assemble(
        make_ctx(review={"approved": True, "issues": [], "revised_reply": None})
    )
    assert result["suggested_reply"].startswith("Olá! Sentimos muito")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"draft": {"needs_human": True}}, "draft_requested_human"),
        ({"security": SecurityReport(score=0.5, verdict="suspicious")}, "security=suspicious"),
        ({"classify": {"urgency": "critical"}}, "urgency=critical"),
        ({"classify": {"category": "legal"}}, "category=legal"),
        ({"draft": {"confidence": 0.59}}, "low_confidence"),
        ({"degraded": True}, "degraded"),
        (
            {"draft": {"suggested_reply": "Olá! Já registramos sua solicitação de cancelamento."}},
            "unverified_action_claim",
        ),
        (
            {"draft": {"suggested_reply": "Olá! O caso já foi encaminhado ao financeiro."}},
            "unverified_action_claim",
        ),
        (  # eval 2026-10-21, g-013: sem "já"
            {"draft": {"suggested_reply": "Olá! Foi encaminhada imediatamente ao time técnico."}},
            "unverified_action_claim",
        ),
        (
            {"draft": {"suggested_reply": "Olá! Sua solicitação foi imediatamente registrada."}},
            "unverified_action_claim",
        ),
        (
            {"draft": {"suggested_reply": "Olá! Os chamados foram corrigidos."}},
            "unverified_action_claim",
        ),
    ],
)
async def test_each_condition_alone_requires_human(
    overrides: dict[str, Any], reason: str
) -> None:  # OUT-8
    kw = dict(overrides)
    ctx = make_ctx(classify=kw.pop("classify", None), draft=kw.pop("draft", None), **kw)
    result = await assemble(ctx, confidence_min=0.6)
    assert result["needs_human"] is True
    assert result["escalation_reasons"] == [reason]


@pytest.mark.parametrize(
    "reply",
    [
        "Olá! Sua solicitação será encaminhada ao financeiro.",
        "Olá! Sua solicitação será analisada pelo time financeiro.",
        "Olá! Pode nos enviar o registro da cobrança?",
    ],
)
async def test_future_tense_is_not_an_action_claim(reply: str) -> None:
    result = await assemble(make_ctx(draft={"suggested_reply": reply}))
    assert "unverified_action_claim" not in result["escalation_reasons"]


@pytest.mark.parametrize(
    "reply",
    [
        "Olá! Como cortesia, daremos 20% de desconto na próxima mensalidade.",
        "Olá! Temos uma promoção especial para você continuar conosco.",
        "Olá! Vamos te enviar um cupom de compensação.",
        "Olá! O valor será estornado na sua conta.",
        "Olá! O valor cobrado será reembolsado integralmente.",
        "Olá! O estorno ocorre em até 7 dias úteis após a confirmação.",
        "Olá! Você terá um crédito de R$ 50 em até 2 dias.",
    ],
)
async def test_financial_promises_are_escalated(reply: str) -> None:
    result = await assemble(make_ctx(draft={"suggested_reply": reply}))
    assert "financial_promise" in result["escalation_reasons"]
    assert result["needs_human"] is True


@pytest.mark.parametrize(
    "reply",
    [
        "Olá! Sua solicitação de estorno será analisada pelo time financeiro.",
        "Olá! Pode nos enviar os comprovantes das duas cobranças?",
        "Olá! O pedido de reembolso foi recebido e será analisado em breve.",
    ],
)
async def test_mentioning_finance_without_promise_is_fine(reply: str) -> None:
    result = await assemble(make_ctx(draft={"suggested_reply": reply}))
    assert "financial_promise" not in result["escalation_reasons"]


async def test_missing_accents_is_flagged_not_escalated() -> None:
    reply = "Ola! Sua solicitacao de cancelamento sera registrada em breve."
    ctx = await AssembleTriage(make_settings()).run(make_ctx(draft={"suggested_reply": reply}))
    assert "missing_accents" in ctx.warnings
    assert ctx.result is not None
    assert ctx.result["escalation_reasons"] == []  # qualidade, não risco: só sinaliza


async def test_accented_reply_has_no_warning() -> None:
    reply = (
        "Olá! Sua solicitação de cancelamento será registrada; você receberá retorno até amanhã."
    )
    ctx = await AssembleTriage(make_settings()).run(make_ctx(draft={"suggested_reply": reply}))
    assert "missing_accents" not in ctx.warnings


async def test_requires_classify_and_draft() -> None:
    ctx = PipelineContext.new(flow="support_triage", raw_input="x")
    with pytest.raises(PipelineMisconfigured):
        await AssembleTriage(make_settings()).run(ctx)
