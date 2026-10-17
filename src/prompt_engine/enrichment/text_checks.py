"""Detectores de conteúdo que não pode chegar ao cliente (usados na ingestão e na carga da KB)."""

from __future__ import annotations

import re

# Caminhos de código e identificadores snake_case (tabelas, colunas, flags). Não são segredo,
# mas revelam a implementação e não fazem sentido para o cliente.
_INTERNAL_TERM_RE = re.compile(
    r"\b(?:app|lib|components|supabase|docs|api)/[\w./\[\]-]+|\b[a-z]+(?:_[a-z0-9]+)+\b"
)


def find_internal_terms(text: str) -> list[str]:
    return sorted(set(_INTERNAL_TERM_RE.findall(text)))
