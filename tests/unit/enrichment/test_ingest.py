"""Ingestão determinística de conhecimento a partir de um repositório (SPEC-004 §5).

Fixtures sintéticas no mesmo formato das fontes reais; nenhum código privado nos testes.
"""

from pathlib import Path

import pytest
import yaml

from prompt_engine.enrichment.ingest import (
    RawSnippet,
    chunk,
    extract_jsx_accordion,
    extract_jsx_sections,
    extract_markdown_sections,
    extract_ts_objects,
    find_internal_terms,
    find_secrets,
    main,
    run_ingest,
)

TS_SOURCE = """
"use client";
type HelpVideo = { id: string; title: string };
const other = [{ title: "não é a lista certa" }];
const helpSections: HelpSection[] = [
  {
    id: "orders",
    title: "Vendas e pedidos",
    description:
      "Criação de pedidos, pagamento e entrega.",
    icon: ShoppingCart,
    videos: [
      {
        id: "order-create",
        title: "Como criar uma venda",
        description: "Mostra como registrar uma venda.",
        videoUrl: "https://www.youtube.com/embed/x?si=1",
      },
      // {
      //   id: "old",
      //   title: "Vídeo antigo",
      //   description: "não deve aparecer",
      // },
      /* { title: "bloco comentado", description: "também não" } */
    ],
  },
  { id: "misc", title: 'Aspas simples', description: "Com \\"escape\\" e // não é comentário" },
];
"""

ACCORDION_SOURCE = """
<Accordion type="single">
  {/* <AccordionItem value="velho"><AccordionTrigger>Antiga?</AccordionTrigger>
  <AccordionContent>Não.</AccordionContent></AccordionItem> */}
  <AccordionItem value="nota-fiscal">
    <AccordionTrigger>
      Sou obrigado a emitir nota fiscal?
    </AccordionTrigger>
    <AccordionContent>
      Não. Você emite <strong>apenas</strong> quando quiser.
    </AccordionContent>
  </AccordionItem>
</Accordion>
"""

SECTIONS_SOURCE = """
<main>
  <SummaryCard title="Coleta">
    Coletamos dados para o acesso{" "}ao sistema.
  </SummaryCard>
  <Section title="Rescisão">
    <p className="mb-4">
      Você terá 14 dias para remediar a &quot;violação&quot;.
    </p>
    <BulletList
      items={[
        "Registros de acesso: IP e horário.",
        <>
          Cookies de análise: só após o{" "}
          <a href="https://policies.google.com/privacy">aceite</a>.
        </>,
      ]}
    />
  </Section>
</main>
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h2>{title}</h2>{children}</section>;
}
"""

MARKDOWN_SOURCE = """# Solicitar exclusão de conta — design

Data: 2026-09-20

## Regras de negócio

- Enquanto o pedido está pendente, a conta segue funcionando.
- O prazo formal é de até 30 dias.

### Detalhe

Só o admin pode pedir.

## Componentes

| Peça | Arquivo |
| rota | app/api/x |
"""


# --- extratores ---------------------------------------------------------------------


def test_ts_objects_groups_children_and_skips_comments() -> None:
    snippets = extract_ts_objects(TS_SOURCE, array="helpSections")
    assert [s.title for s in snippets] == ["Vendas e pedidos", "Aspas simples"]
    orders = snippets[0]
    assert orders.anchor == "orders"
    assert "Criação de pedidos, pagamento e entrega." in orders.content
    assert "- Como criar uma venda: Mostra como registrar uma venda." in orders.content
    assert "Vídeo antigo" not in orders.content
    assert "bloco comentado" not in orders.content
    assert "youtube" not in orders.content
    assert snippets[1].content == 'Com "escape" e // não é comentário'


def test_ts_objects_missing_array_raises() -> None:
    with pytest.raises(ValueError, match="naoExiste"):
        extract_ts_objects(TS_SOURCE, array="naoExiste")


def test_jsx_accordion_pairs_question_and_answer() -> None:
    (snippet,) = extract_jsx_accordion(ACCORDION_SOURCE)
    assert snippet.title == "Sou obrigado a emitir nota fiscal?"
    assert snippet.content == "Não. Você emite apenas quando quiser."
    assert snippet.anchor == "nota-fiscal"


def test_jsx_sections_cleans_markup_and_lists() -> None:
    coleta, rescisao = extract_jsx_sections(SECTIONS_SOURCE, components=["Section", "SummaryCard"])
    assert (coleta.title, coleta.content) == ("Coleta", "Coletamos dados para o acesso ao sistema.")
    assert rescisao.title == "Rescisão"
    assert 'Você terá 14 dias para remediar a "violação".' in rescisao.content
    assert "- Registros de acesso: IP e horário." in rescisao.content
    assert "- Cookies de análise: só após o aceite." in rescisao.content
    for noise in ("className", "<", "{", "items=", "https://"):
        assert noise not in rescisao.content


def test_markdown_sections_and_skip_headings() -> None:
    snippets = extract_markdown_sections(MARKDOWN_SOURCE, skip_headings=["Componentes"])
    (rules,) = snippets
    assert rules.title == "Solicitar exclusão de conta: Regras de negócio"
    assert "até 30 dias" in rules.content
    assert "Só o admin pode pedir." in rules.content  # subseção entra na seção pai
    assert "app/api" not in rules.content


def test_markdown_reflows_wrapped_lines() -> None:
    source = (
        "# Doc\n\n## Regras\n\nUma frase quebrada\nem duas linhas.\n\n"
        "- Item longo que\n  continua aqui.\n- Outro item.\n"
    )
    (snippet,) = extract_markdown_sections(source)
    assert snippet.content == (
        "Uma frase quebrada em duas linhas.\n- Item longo que continua aqui.\n- Outro item."
    )


def test_markdown_include_headings_is_an_allowlist() -> None:
    snippets = extract_markdown_sections(MARKDOWN_SOURCE, include_headings=["^Regras"])
    assert [s.title for s in snippets] == ["Solicitar exclusão de conta: Regras de negócio"]
    assert extract_markdown_sections(MARKDOWN_SOURCE, include_headings=["^Inexistente"]) == []


# --- segurança e chunking ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "chave AIzaSyA1234567890abcdefghijklmnopqrstuv",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "stripe sk_live_51Habcdefghijklmnop",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcDEF123",
        "-----BEGIN PRIVATE KEY-----",
        "api_key = 'f3a9c1d2e4b5a6c7'",
        "SUPABASE_SERVICE_ROLE_KEY=abc123def456",
    ],
)
def test_find_secrets_detects_common_credentials(text: str) -> None:
    assert find_secrets(text)


def test_find_internal_terms() -> None:
    text = "O código app/api/exemplo/remover grava em tabela_exemplo e cancel_at_period_end."
    assert find_internal_terms(text) == [
        "app/api/exemplo/remover",
        "cancel_at_period_end",
        "tabela_exemplo",
    ]
    assert find_internal_terms("Enquanto o pedido está pendente, a conta funciona.") == []


def test_find_secrets_ignores_normal_prose() -> None:
    assert find_secrets("O token de acesso expira em 30 dias; peça um novo ao admin.") == []


def test_chunk_splits_long_content_at_sentences() -> None:
    long = RawSnippet(title="T", content=" ".join(["Frase número um."] * 100), anchor="t")
    parts = chunk(long, max_chars=300)
    assert len(parts) > 1
    assert all(len(p.content) <= 300 for p in parts)
    assert parts[0].title == "T (parte 1)"
    assert all(p.content.endswith(".") for p in parts)
    short = RawSnippet(title="T", content="curto.", anchor="t")
    assert chunk(short, max_chars=300) == [short]


# --- run_ingest ----------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "app" / "help.tsx").write_text(TS_SOURCE, encoding="utf-8")
    (root / "app" / "faq.tsx").write_text(ACCORDION_SOURCE, encoding="utf-8")
    (root / "docs" / "spec.md").write_text(
        MARKDOWN_SOURCE
        + "\n## Config\n\nUse api_key = 'f3a9c1d2e4b5a6c7' no deploy.\n"
        + "\n## Notas\n\nO pedido fica gravado em tabela_exemplo.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def config(tmp_path: Path) -> Path:
    cfg = {
        "repo": "chopp-hub",
        "sources": [
            {
                "path": "app/help.tsx",
                "extractor": "ts_objects",
                "options": {"array": "helpSections"},
                "prefix": "help",
                "category": "technical",
                # regra olha só o título: "pagamento" no conteúdo de "Vendas e pedidos" não conta
                "category_rules": [
                    {"match": "pedido|venda", "category": "shipping"},
                    {"match": "pagamento", "category": "billing"},
                ],
            },
            {
                "path": "app/faq.tsx",
                "extractor": "jsx_accordion",
                "prefix": "faq",
                "category": "other",
            },
            {
                "path": "docs/spec.md",
                "extractor": "markdown_sections",
                "options": {"skip_headings": ["Componentes"]},
                "prefix": "spec",
                "category": "account",
            },
        ],
    }
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


def load_out(out: Path, prefix: str) -> list[dict[str, object]]:
    data = yaml.safe_load((out / f"{prefix}.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


def test_run_ingest_writes_unreviewed_entries_with_provenance(
    repo: Path, config: Path, tmp_path: Path
) -> None:
    out = tmp_path / "generated"
    report = run_ingest(repo, config, out, commit="abc1234")
    help_entries = load_out(out, "help")
    first = help_entries[0]
    assert first["id"] == "help-orders"
    assert first["category"] == "shipping"  # category_rules
    assert help_entries[1]["category"] == "technical"  # default
    assert first["source"] == "chopp-hub@abc1234:app/help.tsx"
    assert first["reviewed"] is False
    assert load_out(out, "faq")[0]["id"] == "faq-nota-fiscal"
    assert report.written == 5  # 2 help + 1 faq + 2 spec (Config com segredo descartada)
    notes = {e["id"]: e.get("review_notes") for e in load_out(out, "spec")}
    assert notes["spec-notas"] == ["termos internos (remover antes de revisar): tabela_exemplo"]
    assert report.dropped_secrets == ["spec: Solicitar exclusão de conta: Config"]
    assert "review_notes" not in first  # conteúdo de cliente, sem termos internos


def test_reingest_keeps_review_when_content_unchanged(
    repo: Path, config: Path, tmp_path: Path
) -> None:
    out = tmp_path / "generated"
    run_ingest(repo, config, out, commit="abc1234")
    entries = load_out(out, "help")
    entries[0]["reviewed"] = True
    entries[1]["reviewed"] = True
    (out / "help.yaml").write_text(yaml.safe_dump(entries, allow_unicode=True), encoding="utf-8")

    source = (repo / "app" / "help.tsx").read_text(encoding="utf-8")
    (repo / "app" / "help.tsx").write_text(
        source.replace('Com \\"escape\\"', "Texto novo"), encoding="utf-8"
    )
    run_ingest(repo, config, out, commit="def5678")
    after = {e["id"]: e for e in load_out(out, "help")}
    assert after["help-orders"]["reviewed"] is True  # mesmo conteúdo: revisão mantida
    assert after["help-misc"]["reviewed"] is False  # mudou: volta para revisão
    assert after["help-orders"]["source"] == "chopp-hub@def5678:app/help.tsx"


def test_cli_main(
    repo: Path, config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "generated"
    code = main(
        ["--repo", str(repo), "--config", str(config), "--out", str(out), "--commit", "abc"]
    )
    assert code == 0
    assert "5 snippets" in capsys.readouterr().out
    assert (out / "faq.yaml").exists()


# --- casos de borda ------------------------------------------------------------------------


def test_ts_objects_edge_cases() -> None:
    source = """
const items = [
  { id: "sem-titulo", description: "ignorado" },
  { title: `Template literal`, children: [{ description: "filho sem título" }] },
];
"""
    (snippet,) = extract_ts_objects(source, array="items")
    assert snippet.title == "Template literal"
    assert snippet.content == ""
    assert snippet.anchor == "Template literal"


def test_accordion_item_without_content_is_skipped() -> None:
    source = (
        '<AccordionItem value="x"><AccordionTrigger>Pergunta?</AccordionTrigger></AccordionItem>'
    )
    assert extract_jsx_accordion(source) == []


def test_markdown_ignores_code_blocks_preamble_and_empty_sections() -> None:
    source = (
        "# Doc\n\npreâmbulo ignorado\n\n## Vazia\n\n"
        "## Com código\n\n```sql\nselect 1;\n```\nTexto.\n"
    )
    (snippet,) = extract_markdown_sections(source)
    assert snippet.title == "Doc: Com código"
    assert snippet.content == "Texto."


def test_chunk_hard_splits_giant_sentence() -> None:
    giant = RawSnippet(title="T", content="palavra " * 100 + "fim.", anchor="t")
    parts = chunk(giant, max_chars=120)
    assert all(len(p.content) <= 120 for p in parts)
    assert " ".join(p.content for p in parts).split() == giant.content.split()


def test_duplicate_anchors_get_suffix(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "faq.tsx").write_text(ACCORDION_SOURCE + ACCORDION_SOURCE, encoding="utf-8")
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "repo": "r",
                "sources": [
                    {
                        "path": "faq.tsx",
                        "extractor": "jsx_accordion",
                        "prefix": "f",
                        "category": "other",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_ingest(root, cfg, tmp_path / "out", commit="x")
    ids = [e["id"] for e in load_out(tmp_path / "out", "f")]
    assert ids == ["f-nota-fiscal", "f-nota-fiscal-2"]


def test_commit_is_read_from_git_or_unknown(repo: Path, config: Path, tmp_path: Path) -> None:
    import subprocess

    run_ingest(repo, config, tmp_path / "a")  # não é repositório git
    assert str(load_out(tmp_path / "a", "faq")[0]["source"]).startswith("chopp-hub@unknown:")

    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run(
        [*git, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], check=True
    )
    head = subprocess.run(
        [*git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    run_ingest(repo, config, tmp_path / "b")
    assert load_out(tmp_path / "b", "faq")[0]["source"] == f"chopp-hub@{head}:app/faq.tsx"


def test_ts_objects_tolerates_unterminated_array() -> None:
    (snippet,) = extract_ts_objects(
        'const xs = [ { title: "Único", description: "ok" }', array="xs"
    )
    assert (snippet.title, snippet.content) == ("Único", "ok")


def test_stale_terms_add_review_notes(repo: Path, tmp_path: Path) -> None:
    (repo / "docs" / "nfe.md").write_text(
        "# NF-e\n\n## Contexto\n\nO cancelamento continua via Focus NFe.\n\n"
        "## Fluxo\n\nEmissão pela SEFAZ.\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "stale.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "repo": "chopp-hub",
                "stale_terms": [{"match": r"\bfocus\b", "note": "Focus não é mais usada"}],
                "sources": [
                    {
                        "path": "docs/nfe.md",
                        "extractor": "markdown_sections",
                        "prefix": "nfe",
                        "category": "technical",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_ingest(repo, cfg, tmp_path / "out", commit="x")
    notes = {e["id"]: e.get("review_notes") for e in load_out(tmp_path / "out", "nfe")}
    assert notes["nfe-contexto"] == ["Focus não é mais usada"]
    assert notes["nfe-fluxo"] is None
