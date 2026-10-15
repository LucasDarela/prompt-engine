"""Saídas seguras usadas quando o modelo não produz JSON válido nem após reparo (SPEC-007 §3)."""

from __future__ import annotations

from prompt_engine.output.schemas import ClassifyOutput, DraftOutput, ReviewOutput


def classify_fallback() -> ClassifyOutput:
    # complexity "complex" + category "other" levam a needs_human=true e ao tier mais forte.
    return ClassifyOutput(
        reasoning="fallback: saída do modelo inválida",
        category="other",
        urgency="medium",
        sentiment="neutral",
        complexity_hint="complex",
    )


def draft_fallback(sla_hours: int | None = None) -> DraftOutput:
    when = f"em até {sla_hours} horas" if sla_hours else "o quanto antes"
    return DraftOutput(
        summary="Resumo indisponível: a resposta automática falhou; requer análise humana.",
        suggested_reply=(
            f"Olá! Recebemos sua mensagem e um especialista da nossa equipe vai responder {when}."
        ),
        needs_human=True,
        confidence=0.0,
    )


def review_fallback() -> ReviewOutput:
    # Sem revisão disponível, mantém o draft (o pipeline registra o warning).
    return ReviewOutput(approved=True)
