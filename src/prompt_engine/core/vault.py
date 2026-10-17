"""PIIVault: mapa placeholder ↔ valor de PII, vivo só na memória da request (SPEC-003 §4)."""

from __future__ import annotations

import re

PLACEHOLDER_RE = re.compile(r"\[([A-Z]+)_(\d+)\]")


class PIIVault:
    """Nunca expõe valores em repr/str; não é serializado com o contexto."""

    def __init__(self) -> None:
        self._by_placeholder: dict[str, str] = {}
        self._by_value: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    def put(self, kind: str, value: str) -> str:
        """Registra o valor e retorna seu placeholder (o mesmo valor reaproveita o placeholder)."""
        key = (kind, value)
        if key in self._by_value:
            return self._by_value[key]
        self._counters[kind] = self._counters.get(kind, 0) + 1
        placeholder = f"[{kind}_{self._counters[kind]}]"
        self._by_value[key] = placeholder
        self._by_placeholder[placeholder] = value
        return placeholder

    def restore(self, text: str) -> tuple[str, list[str]]:
        """Substitui placeholders conhecidos; remove e reporta os desconhecidos."""
        unknown: list[str] = []

        def _sub(match: re.Match[str]) -> str:
            token = match.group(0)
            if token in self._by_placeholder:
                return self._by_placeholder[token]
            unknown.append(token)
            return ""

        return PLACEHOLDER_RE.sub(_sub, text), unknown

    def mask(self, text: str) -> str:
        """Inverso do restore: troca valores reais conhecidos por placeholders (logs/traces)."""
        for placeholder, value in sorted(
            self._by_placeholder.items(), key=lambda item: len(item[1]), reverse=True
        ):
            text = text.replace(value, placeholder)
        return text

    def kinds(self) -> list[str]:
        return sorted(self._counters)

    def contains_value_in(self, text: str) -> bool:
        """True se algum valor real do vault aparece no texto (detector de vazamento)."""
        return any(value in text for value in self._by_placeholder.values())

    def __len__(self) -> int:
        return len(self._by_placeholder)

    def __repr__(self) -> str:
        return f"PIIVault(kinds={self.kinds()}, size={len(self)})"

    __str__ = __repr__
