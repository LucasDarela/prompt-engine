from typing import Any

import pytest
from pydantic import ValidationError

from prompt_engine.output.fallbacks import classify_fallback, draft_fallback, review_fallback
from prompt_engine.output.schemas import (
    SCHEMAS,
    ClassifyOutput,
    DraftOutput,
    ReviewOutput,
    TriageResult,
)

CLASSIFY_OK: dict[str, Any] = {
    "reasoning": "Cobrança duplicada.",
    "category": "billing",
    "urgency": "high",
    "sentiment": "negative",
    "entities": {"order_id": "PED-1"},
    "intents": ["refund"],
    "complexity_hint": "simple",
}


def test_classify_valid() -> None:
    assert ClassifyOutput.model_validate(CLASSIFY_OK).category == "billing"


@pytest.mark.parametrize(
    "patch",
    [
        {"category": "sales"},
        {"urgency": "urgent"},
        {"extra_field": 1},
        {"entities": {"cpf": "123"}},
        {"intents": ["a", "b", "c", "d", "e", "f"]},
        {"reasoning": "x" * 301},
    ],
)
def test_classify_rejects_invalid(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ClassifyOutput.model_validate(CLASSIFY_OK | patch)


def test_draft_bounds() -> None:
    base = {"summary": "s", "suggested_reply": "x" * 20, "needs_human": False, "confidence": 0.9}
    DraftOutput.model_validate(base)
    for bad in ({"confidence": 1.5}, {"suggested_reply": "curta"}, {"summary": "x" * 281}):
        with pytest.raises(ValidationError):
            DraftOutput.model_validate(base | bad)


def test_review_rejected_requires_revised_reply() -> None:  # OUT-7
    with pytest.raises(ValidationError):
        ReviewOutput.model_validate({"approved": False, "issues": ["tom"]})
    ok = ReviewOutput.model_validate(
        {"approved": False, "issues": ["tom"], "revised_reply": "Olá! Versão revisada da resposta."}
    )
    assert ok.revised_reply


def test_review_approved_needs_nothing_else() -> None:
    assert ReviewOutput.model_validate({"approved": True}).issues == []


def test_triage_result_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        TriageResult(
            category="other",
            urgency="low",
            sentiment="neutral",
            summary="s",
            entities={},
            suggested_reply="r",
            needs_human=False,
            confidence=-0.1,
        )


def test_schema_registry_exposes_models_by_name() -> None:
    assert SCHEMAS["ClassifyOutput"] is ClassifyOutput
    assert set(SCHEMAS) >= {"ClassifyOutput", "DraftOutput", "ReviewOutput"}


def test_fallbacks_are_valid_and_safe() -> None:  # OUT-6 (valores)
    c = classify_fallback()
    assert (c.category, c.complexity_hint) == ("other", "complex")
    d = draft_fallback(sla_hours=4)
    assert d.needs_human is True
    assert d.confidence == 0.0
    assert "4 horas" in d.suggested_reply
    assert "o quanto antes" in draft_fallback(sla_hours=None).suggested_reply
    assert review_fallback().approved is True
