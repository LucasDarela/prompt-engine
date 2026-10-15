"""Normalização de enums antes da validação: caixa, acentos e aliases PT/EN (SPEC-007 §3)."""

from __future__ import annotations

import unicodedata
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

# Chaves já normalizadas (minúsculas, sem acento, espaços/hífens -> "_").
ALIASES: dict[str, dict[str, str]] = {
    "category": {
        "cobranca": "billing",
        "pagamento": "billing",
        "fatura": "billing",
        "financeiro": "billing",
        "tecnico": "technical",
        "suporte_tecnico": "technical",
        "conta": "account",
        "cadastro": "account",
        "login": "account",
        "entrega": "shipping",
        "envio": "shipping",
        "frete": "shipping",
        "cancelamento": "cancellation",
        "juridico": "legal",
        "outro": "other",
        "outros": "other",
    },
    "urgency": {
        "baixa": "low",
        "media": "medium",
        "alta": "high",
        "urgente": "high",
        "critica": "critical",
    },
    "sentiment": {
        "muito_negativo": "very_negative",
        "negativo": "negative",
        "neutro": "neutral",
        "positivo": "positive",
    },
    "complexity_hint": {"simples": "simple", "complexo": "complex"},
}


def _key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "_".join(ascii_only.replace("-", " ").split())


def _literal_values(annotation: Any) -> set[str]:
    if get_origin(annotation) is Literal:
        return {v for v in get_args(annotation) if isinstance(v, str)}
    values: set[str] = set()
    for arg in get_args(annotation):
        values |= _literal_values(arg)
    return values


def normalize_enums(data: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    """Retorna uma cópia com campos Literal normalizados. Valores desconhecidos ficam intactos."""
    result = dict(data)
    for name, field in model.model_fields.items():
        value = result.get(name)
        allowed = _literal_values(field.annotation)
        if not isinstance(value, str) or not allowed:
            continue
        key = _key(value)
        candidate = key if key in allowed else ALIASES.get(name, {}).get(key)
        if candidate in allowed:
            result[name] = candidate
    return result
