"""Base de conhecimento em arquivos YAML com busca BM25 em português (SPEC-004 §2-3)."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prompt_engine.core.context import KBSnippet
from prompt_engine.core.errors import KnowledgeBaseError
from prompt_engine.output.schemas import Category, Urgency

BM25_K1 = 1.5
BM25_B = 0.75
TITLE_WEIGHT = 2  # termos do título contam em dobro
CATEGORY_BOOST = 2.0

_STOPWORDS_TEXT = """
a o as os um uma uns umas de da do das dos em na no nas nos por para pra pro com sem e ou que
se ao aos esta este isso isto essa esse foi ser sao estao meu minha meus minhas seu sua seus
suas nosso nossa voce voces eu nos ele ela eles elas me te lhe mais muito ja nao como qual
quando onde tem ter ha la ai vez
"""
STOPWORDS = frozenset(_STOPWORDS_TEXT.split())
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Minúsculas, sem acentos, sem stopwords, com stemming leve de plural."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    tokens = []
    for token in _TOKEN_RE.findall(folded):
        if len(token) < 2 or token in STOPWORDS:
            continue
        tokens.append(token[:-1] if len(token) > 4 and token.endswith("s") else token)
    return tokens


class KBEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    category: Category
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: list[str] = []
    updated_at: date | None = None
    source: str = "manual"  # ex.: "chopp-hub@1674717:app/marketing/terms/page.tsx"
    reviewed: bool = True  # gerados entram como False e só valem após revisão humana
    review_notes: list[str] = []  # alertas da ingestão para o revisor (ex.: termos internos)

    @property
    def is_approved(self) -> bool:
        """Revisado E sem alertas pendentes: apagar `review_notes` faz parte da revisão."""
        return self.reviewed and not self.review_notes


class SLAPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    first_response_hours: int = Field(gt=0)
    escalate_if_urgency: list[Urgency] = []


class KnowledgeBase(Protocol):
    def search(
        self, query: str, *, category: str | None = None, k: int = 3, min_score: float = 0.0
    ) -> list[KBSnippet]: ...


def _format(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise KnowledgeBaseError(f"{path}: YAML inválido: {exc}") from exc


class FileKnowledgeBase:
    def __init__(self, entries: list[KBEntry]) -> None:
        self.entries = entries
        self._docs = [
            Counter(
                tokenize(e.title) * TITLE_WEIGHT + tokenize(e.content) + tokenize(" ".join(e.tags))
            )
            for e in entries
        ]
        self._lengths = [sum(doc.values()) for doc in self._docs]
        self._avg_length = (sum(self._lengths) / len(self._lengths)) if entries else 0.0
        document_frequency: Counter[str] = Counter()
        for doc in self._docs:
            document_frequency.update(doc.keys())
        total = len(entries)
        self._idf = {
            term: math.log((total - df + 0.5) / (df + 0.5) + 1.0)
            for term, df in document_frequency.items()
        }

    @classmethod
    def from_directory(cls, root: Path, *, include_unreviewed: bool = False) -> FileKnowledgeBase:
        entries: list[KBEntry] = []
        seen: dict[str, Path] = {}
        for path in sorted(root.rglob("*.yaml")):
            data = _read_yaml(path)
            if data is None:
                continue
            if not isinstance(data, list):
                raise KnowledgeBaseError(f"{path}: o arquivo deve conter uma lista de artigos")
            for index, item in enumerate(data):
                try:
                    entry = KBEntry.model_validate(item)
                except ValidationError as exc:
                    raise KnowledgeBaseError(f"{path} [item {index}]: {_format(exc)}") from exc
                if entry.id in seen:
                    raise KnowledgeBaseError(
                        f"{path}: id duplicado '{entry.id}' (já existe em {seen[entry.id]})"
                    )
                seen[entry.id] = path
                if entry.is_approved or include_unreviewed:
                    entries.append(entry)
        return cls(entries)

    def __len__(self) -> int:
        return len(self.entries)

    def search(
        self, query: str, *, category: str | None = None, k: int = 3, min_score: float = 0.0
    ) -> list[KBSnippet]:
        terms = tokenize(query)
        scored: list[tuple[float, KBEntry]] = []
        for entry, doc, length in zip(self.entries, self._docs, self._lengths, strict=True):
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if not tf:
                    continue
                norm = 1 - BM25_B + BM25_B * length / self._avg_length
                score += self._idf[term] * tf * (BM25_K1 + 1) / (tf + BM25_K1 * norm)
            if score <= 0:
                continue
            if category is not None and entry.category == category:
                score *= CATEGORY_BOOST
            if score >= min_score:
                scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            KBSnippet(
                id=e.id, category=e.category, title=e.title, content=e.content, score=round(s, 4)
            )
            for s, e in scored[:k]
        ]


def load_sla(path: Path) -> dict[str, SLAPolicy]:
    data = _read_yaml(path)
    if not isinstance(data, Mapping):
        raise KnowledgeBaseError(f"{path}: SLA deve ser um mapa categoria -> política")
    from typing import get_args

    valid = set(get_args(Category))
    policies: dict[str, SLAPolicy] = {}
    for category, raw in data.items():
        if category not in valid:
            raise KnowledgeBaseError(f"{path}: categoria desconhecida '{category}'")
        try:
            policies[str(category)] = SLAPolicy.model_validate(raw)
        except ValidationError as exc:
            raise KnowledgeBaseError(f"{path} [{category}]: {_format(exc)}") from exc
    return policies
