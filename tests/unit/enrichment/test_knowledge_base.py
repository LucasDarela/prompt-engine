"""FileKnowledgeBase: carga, revisão humana e busca BM25 em português (SPEC-004)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from prompt_engine.core.errors import KnowledgeBaseError
from prompt_engine.enrichment.knowledge_base import FileKnowledgeBase, load_sla, tokenize

ENTRIES: list[dict[str, Any]] = [
    {
        "id": "kb-billing-001",
        "category": "billing",
        "title": "Cobrança em duplicidade da mensalidade",
        "content": "Cobranças em duplicidade da assinatura são estornadas em até 7 dias úteis.",
        "tags": ["estorno", "duplicada"],
    },
    {
        "id": "kb-shipping-001",
        "category": "shipping",
        "title": "Rotas de entrega",
        "content": "As rotas são montadas no módulo Rotas e Entregas e enviadas ao motorista.",
    },
    {
        "id": "kb-technical-001",
        "category": "technical",
        "title": "App do Motorista não sincroniza",
        "content": "Peça ao motorista para atualizar o app e verificar a conexão. Cobranças não "
        "são afetadas.",
    },
    {
        "id": "kb-draft-001",
        "category": "billing",
        "title": "Rascunho gerado do repositório",
        "content": "Cobrança duplicada: texto extraído e ainda não revisado.",
        "source": "chopp-hub@abc123:docs/x.md",
        "reviewed": False,
    },
]


def write_kb(root: Path, entries: list[dict[str, Any]] = ENTRIES, name: str = "kb.yaml") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(yaml.safe_dump(entries, allow_unicode=True), encoding="utf-8")
    return root


def test_tokenize_folds_accents_stopwords_and_plural() -> None:
    assert tokenize("As cobranças DUPLICADAS da mensalidade") == [
        "cobranca",
        "duplicada",
        "mensalidade",
    ]


def test_best_match_first(tmp_path: Path) -> None:  # EN-1
    kb = FileKnowledgeBase.from_directory(write_kb(tmp_path))
    results = kb.search("fui cobrado duas vezes, cobrança duplicada", category="billing", k=3)
    assert results[0].id == "kb-billing-001"
    assert results[0].score > 0


def test_category_boost_breaks_lexical_tie(tmp_path: Path) -> None:  # EN-2
    kb = FileKnowledgeBase.from_directory(write_kb(tmp_path))
    # "cobranças" aparece em billing e em technical; a categoria decide.
    assert kb.search("cobranças", category="technical", k=1)[0].id == "kb-technical-001"
    assert kb.search("cobranças", category="billing", k=1)[0].id == "kb-billing-001"


def test_unreviewed_entries_are_excluded_by_default(tmp_path: Path) -> None:
    kb = FileKnowledgeBase.from_directory(write_kb(tmp_path))
    assert "kb-draft-001" not in {s.id for s in kb.search("rascunho cobrança duplicada", k=10)}
    kb_all = FileKnowledgeBase.from_directory(write_kb(tmp_path), include_unreviewed=True)
    assert "kb-draft-001" in {s.id for s in kb_all.search("rascunho cobrança duplicada", k=10)}


def test_reviewed_entry_with_pending_notes_is_not_approved(tmp_path: Path) -> None:
    pending = {**ENTRIES[1], "id": "kb-pending", "review_notes": ["termos internos: tabela_exemplo"]}
    kb = FileKnowledgeBase.from_directory(write_kb(tmp_path, [*ENTRIES, pending]))
    assert "kb-pending" not in {e.id for e in kb.entries}
    kb_all = FileKnowledgeBase.from_directory(tmp_path, include_unreviewed=True)
    assert "kb-pending" in {e.id for e in kb_all.entries}


def test_approved_entry_with_internal_terms_is_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    leaky = {
        **ENTRIES[1],
        "id": "kb-leaky",
        "content": "O pedido é gravado em tabela_exemplo via POST /api/exemplo/pedido.",
    }
    kb = FileKnowledgeBase.from_directory(write_kb(tmp_path, [*ENTRIES, leaky]))
    assert "kb-leaky" not in {e.id for e in kb.entries}
    assert "kb.entry_rejected_internal_terms" in caplog.text
    inspect = FileKnowledgeBase.from_directory(tmp_path, include_unreviewed=True)
    assert "kb-leaky" in {e.id for e in inspect.entries}


def test_no_match_returns_empty(tmp_path: Path) -> None:  # EN-4
    kb = FileKnowledgeBase.from_directory(write_kb(tmp_path))
    assert kb.search("xyzzy quântico", k=3) == []


def test_min_score_filters_weak_matches(tmp_path: Path) -> None:
    kb = FileKnowledgeBase.from_directory(write_kb(tmp_path))
    assert kb.search("rotas", k=3, min_score=1000.0) == []


def test_empty_directory_is_empty_kb(tmp_path: Path) -> None:  # EN-4
    assert FileKnowledgeBase.from_directory(tmp_path).search("qualquer coisa", k=3) == []


def test_recursive_load_and_size(tmp_path: Path) -> None:
    write_kb(tmp_path / "manual", ENTRIES[:2])
    write_kb(tmp_path / "generated", ENTRIES[2:])
    assert len(FileKnowledgeBase.from_directory(tmp_path)) == 3  # 1 não revisado


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([{"id": "x", "category": "billing", "title": "t"}], "content"),  # EN-5
        ([{**ENTRIES[0], "category": "vendas"}], "category"),
        ([ENTRIES[0], ENTRIES[0]], "duplicad"),
        ({"not": "a list"}, "lista"),
    ],
)
def test_invalid_kb_fails_fast_with_file_and_field(
    tmp_path: Path, entries: Any, message: str
) -> None:
    write_kb(tmp_path, entries)
    with pytest.raises(KnowledgeBaseError, match=message) as info:
        FileKnowledgeBase.from_directory(tmp_path)
    assert "kb.yaml" in str(info.value)


def test_yaml_syntax_error(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("- id: [aberto", encoding="utf-8")
    with pytest.raises(KnowledgeBaseError, match=r"bad\.yaml"):
        FileKnowledgeBase.from_directory(tmp_path)


def test_load_sla(tmp_path: Path) -> None:
    path = tmp_path / "sla.yaml"
    path.write_text(
        "billing: {first_response_hours: 4, escalate_if_urgency: [high, critical]}\n"
        "other: {first_response_hours: 24}\n",
        encoding="utf-8",
    )
    sla = load_sla(path)
    assert sla["billing"].first_response_hours == 4
    assert sla["other"].escalate_if_urgency == []


def test_load_sla_rejects_unknown_category(tmp_path: Path) -> None:
    path = tmp_path / "sla.yaml"
    path.write_text("vendas: {first_response_hours: 4}\n", encoding="utf-8")
    with pytest.raises(KnowledgeBaseError, match="vendas"):
        load_sla(path)


def test_real_project_kb_loads() -> None:
    from prompt_engine.config import Settings

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    kb = FileKnowledgeBase.from_directory(settings.kb_path)
    assert len(kb) >= 7 * 2  # ao menos 2 artigos revisados por categoria
    sla = load_sla(settings.sla_path)
    assert set(sla) == {
        "billing",
        "technical",
        "account",
        "shipping",
        "cancellation",
        "legal",
        "other",
    }


def test_empty_yaml_file_is_ignored(tmp_path: Path) -> None:
    write_kb(tmp_path)
    (tmp_path / "vazio.yaml").write_text("", encoding="utf-8")
    assert len(FileKnowledgeBase.from_directory(tmp_path)) == 3


@pytest.mark.parametrize(
    ("content", "message"),
    [("- billing\n", "mapa"), ("billing: {first_response_hours: 0}\n", "first_response_hours")],
)
def test_load_sla_invalid(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "sla.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(KnowledgeBaseError, match=message):
        load_sla(path)
