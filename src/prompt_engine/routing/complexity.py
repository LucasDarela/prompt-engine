"""ComplexityScorer: score 0-1 explicável a partir do classify + heurísticas (SPEC-006 §5)."""

from __future__ import annotations

import re
import unicodedata

from prompt_engine.core.context import PipelineContext

SENSITIVE_CATEGORIES = frozenset({"legal", "cancellation"})
LONG_INPUT_CHARS = 800
_LEGAL_KEYWORDS = re.compile(
    r"\b(procon|advogad\w*|processo judicial|processar|acao judicial|judicial|justica"
    r"|chargeback|reclame aqui|indeniza\w*|lgpd)\b"
)

WEIGHTS: dict[str, float] = {
    "complexity_hint": 0.35,
    "multiple_intents": 0.15,
    "urgency": 0.15,
    "sensitive": 0.25,
    "long_input": 0.10,
    "sentiment": 0.10,
    "security": 0.10,
    "no_kb_match": 0.10,
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


class ComplexityScorer:
    def score(self, ctx: PipelineContext) -> tuple[float, list[str]]:
        """Retorna (score limitado a [0, 1], razões que contribuíram)."""
        c = ctx.step("classify").parsed or {}
        text = ctx.sanitized_input or ""
        signals: list[tuple[str, float]] = []

        if c.get("complexity_hint") == "complex":
            signals.append(("complexity_hint=complex", WEIGHTS["complexity_hint"]))
        if len(c.get("intents", [])) >= 2:
            signals.append(("multiple_intents", WEIGHTS["multiple_intents"]))
        if c.get("urgency") in ("high", "critical"):
            signals.append((f"urgency={c['urgency']}", WEIGHTS["urgency"]))
        if c.get("category") in SENSITIVE_CATEGORIES:
            signals.append((f"sensitive_category={c['category']}", WEIGHTS["sensitive"]))
        elif _LEGAL_KEYWORDS.search(_fold(text)):
            signals.append(("legal_keywords", WEIGHTS["sensitive"]))
        if len(text) > LONG_INPUT_CHARS:
            signals.append(("long_input", WEIGHTS["long_input"]))
        if c.get("sentiment") == "very_negative":
            signals.append(("sentiment=very_negative", WEIGHTS["sentiment"]))
        if ctx.security.verdict == "suspicious":
            signals.append(("security=suspicious", WEIGHTS["security"]))
        if not ctx.enrichment.kb_snippets:
            signals.append(("no_kb_match", WEIGHTS["no_kb_match"]))

        total = min(1.0, sum(weight for _, weight in signals))
        return round(total, 4), [reason for reason, _ in signals]
