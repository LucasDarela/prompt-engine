"""FakeProvider determinístico para testes e demos offline (ADR-0004)."""

from __future__ import annotations

import json
from collections.abc import Callable, MutableMapping
from typing import Any

from prompt_engine.core.errors import ProviderBadRequestError
from prompt_engine.llm.base import LLMRequest, LLMResponse, Usage

ScriptItem = str | Exception | Callable[[LLMRequest], str]
# Valor por step: um item fixo ou uma lista consumida em ordem (útil para simular retry).
Script = MutableMapping[str, ScriptItem | list[Any]]


class FakeProvider:
    """Responde conforme `script[request.metadata["step"]]` e registra cada request em `calls`."""

    def __init__(self, script: Script, name: str = "fake") -> None:
        self.name = name
        self.script = script
        self.calls: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        step = request.metadata.get("step", "")
        if step not in self.script:
            raise ProviderBadRequestError(self.name, f"sem roteiro para o step '{step}'")
        item = self._next_item(step)
        if isinstance(item, Exception):
            raise item
        text = item(request) if callable(item) else item
        prompt_words = sum(len(m.content.split()) for m in request.messages)
        return LLMResponse(
            text=text,
            model=request.model,
            provider=self.name,
            usage=Usage(input_tokens=prompt_words, output_tokens=len(text.split())),
        )

    def _next_item(self, step: str) -> ScriptItem:
        entry = self.script[step]
        if not isinstance(entry, list):
            return entry
        if not entry:
            raise ProviderBadRequestError(self.name, f"roteiro do step '{step}' esgotado")
        item: ScriptItem = entry.pop(0)
        return item


def demo_script() -> Script:
    """Respostas fixas e válidas para cada step: `LLM_PROVIDER=fake` vira modo demo offline.

    Útil para testar o frontend e apresentar o fluxo sem chave nem quota. A classificação é sempre
    a mesma (é uma demonstração do pipeline, não do modelo).
    """
    return {
        # Conservador: só chega ao juiz o que a heurística já achou suspeito (zona cinzenta).
        "injection_judge": json.dumps(
            {
                "is_injection": True,
                "confidence": 0.9,
                "reason": "modo demo: juiz simulado conservador (bloqueia a zona cinzenta)",
            }
        ),
        "classify": json.dumps(
            {
                "reasoning": "Modo demo: classificação fixa.",
                "category": "billing",
                "urgency": "high",
                "sentiment": "negative",
                "entities": {},
                "intents": ["billing_dispute"],
                "complexity_hint": "simple",
            }
        ),
        "draft_reply": json.dumps(
            {
                "summary": "Modo demo: cliente relata problema de cobrança.",
                "suggested_reply": "Olá! Sentimos muito pelo transtorno. Para que o nosso time "
                "financeiro analise o caso, pode nos enviar os comprovantes das cobranças? A sua "
                "solicitação será encaminhada para análise.",
                "needs_human": True,
                "confidence": 0.9,
                "used_kb_ids": ["kb-billing-001"],
            }
        ),
        "review_reply": json.dumps({"approved": True, "issues": [], "revised_reply": None}),
    }
