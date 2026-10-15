"""Schemas de saída dos steps (SPEC-007 §2). Fonte única para o validador e para os prompts."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal["billing", "technical", "account", "shipping", "cancellation", "legal", "other"]
Urgency = Literal["low", "medium", "high", "critical"]
Sentiment = Literal["very_negative", "negative", "neutral", "positive"]
Complexity = Literal["simple", "complex"]
EntityKey = Literal["order_id", "product", "amount", "date", "payment_method"]


class StrictModel(BaseModel):
    """Campos extras são erro: o modelo não pode 'inventar' saída."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class ClassifyOutput(StrictModel):
    # Raciocínio curto ANTES dos rótulos (CoT oculto); nunca vai para a resposta final.
    reasoning: str = Field(max_length=300)
    category: Category
    urgency: Urgency
    sentiment: Sentiment
    entities: dict[EntityKey, str] = {}
    intents: list[str] = Field(default=[], max_length=5)
    complexity_hint: Complexity


class DraftOutput(StrictModel):
    summary: str = Field(max_length=280)
    suggested_reply: str = Field(min_length=20, max_length=1500)
    needs_human: bool
    confidence: float = Field(ge=0, le=1)
    used_kb_ids: list[str] = []


class ReviewOutput(StrictModel):
    approved: bool
    issues: list[str] = []
    revised_reply: str | None = Field(default=None, max_length=1500)

    @model_validator(mode="after")
    def _rejection_needs_revision(self) -> Self:
        if not self.approved and not self.revised_reply:
            raise ValueError("revised_reply é obrigatório quando approved=false")
        return self


class InjectionJudgeOutput(StrictModel):
    is_injection: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=300)


Score = Annotated[int, Field(ge=1, le=5)]


class JudgeReplyOutput(StrictModel):
    """LLM-as-judge dos evals (rubrica de 1 a 5 por critério)."""

    rationale: str = Field(max_length=600)
    policy_adherence: Score
    correctness: Score
    tone: Score
    completeness: Score
    overall: Score


class TriageResult(StrictModel):
    """Resultado final entregue ao frontend."""

    category: Category
    urgency: Urgency
    sentiment: Sentiment
    summary: str
    entities: dict[EntityKey, str]
    suggested_reply: str
    needs_human: bool
    confidence: float = Field(ge=0, le=1)


SCHEMAS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        ClassifyOutput,
        DraftOutput,
        ReviewOutput,
        InjectionJudgeOutput,
        JudgeReplyOutput,
        TriageResult,
    )
}
