"""FakeProvider determinístico para testes e demos offline (ADR-0004)."""

from __future__ import annotations

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
