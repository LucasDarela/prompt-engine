# SPEC-004 — Enriquecimento Oculto e Base de Conhecimento

> Módulo: `src/prompt_engine/enrichment/` · Dados: `data/knowledge_base/`, `data/sla.yaml`, `data/kb_sources.yaml` · Status: ✅ Implementado (Fase 5)

## 1. Objetivo
O usuário escreve só a intenção. O sistema anexa, **sem que ele veja**:
1. **Estático** (todo step): system prompt, persona, regras de negócio, formato de saída. Vem das parciais do `PromptRegistry` (SPEC-005).
2. **Dinâmico** (depois do `classify`): trechos relevantes da KB, a política e o SLA da categoria e o perfil do cliente (opcional).

## 2. Formato da KB

```
data/
├── sla.yaml                      # SLA por categoria (valores de demonstração)
├── kb_sources.yaml               # lista permitida para a ingestão a partir do repositório chopp-hub
└── knowledge_base/
    ├── manual/                   # escrita à mão, revisada (reviewed: true por padrão)
    └── generated/                # gerada por ingestão; reviewed: false até revisão humana
```

Cada arquivo `*.yaml` é uma **lista** de artigos:
```yaml
- id: kb-billing-001                # [a-z0-9_-], único em toda a KB
  category: billing                 # mesma enum de ClassifyOutput.category
  title: Cobrança em duplicidade da mensalidade
  content: Cobranças em duplicidade são estornadas em até 7 dias úteis...
  tags: [estorno, duplicada]
  updated_at: 2026-10-14            # opcional
  source: manual-demo               # manual-demo | chopp-hub:<arquivo> | chopp-hub@<commit>:<arquivo>
  reviewed: true                    # false = NÃO entra na KB (KB_INCLUDE_UNREVIEWED=false)
  review_notes: []                  # alertas da ingestão (ex.: termos internos a remover)
```

- `source: manual-demo` marca **suposições de demonstração** (prazos de estorno, SLA, passos de diagnóstico) que precisam ser validadas com a operação. `source: chopp-hub:<arquivo>` marca fatos extraídos do produto (termos, privacidade, FAQ).
- `sla.yaml`: `categoria: {first_response_hours: int > 0, escalate_if_urgency: [urgências]}`. Categoria desconhecida é erro no startup.
- O perfil de cliente (`customers.yaml`) ficou **fora do MVP**.

## 3. Interfaces

```python
class KBSnippet(BaseModel):
    id: str; category: str; title: str; content: str; score: float

class KnowledgeBase(Protocol):
    def search(self, query: str, *, category: str | None = None, k: int = 3) -> list[KBSnippet]: ...

class FileKnowledgeBase:   # implementação MVP
    """Carrega os YAMLs (recursivo) no startup. BM25 (k1=1.5, b=0.75) sobre título×2 + conteúdo + tags;
    tokenização PT: minúsculas, sem acentos, stopwords, stemming leve de plural. Boost ×2 por categoria."""

class CustomerRepository(Protocol):
    def get(self, customer_id: str) -> CustomerProfile | None: ...

class ContextEnrichment:   # Stage
    """Lê ctx.steps['classify'].parsed.category + sanitized_input; escreve ctx.enrichment."""
```

## 4. Regras
- `k = KB_TOP_K` (padrão 3); snippets com `score < KB_MIN_SCORE` são descartados.
- **Corte relativo:** snippets com score < `KB_MIN_RELATIVE_SCORE` (0.3) × melhor score saem. A escala do BM25 muda com o tamanho da base; o corte relativo não. Em testes com a KB real ele removeu exatamente o ruído (ex.: artigo de LGPD com 1.7 numa pergunta sobre NF-e cujo topo tinha 10.75).
- A consulta é `sanitized_input` + `intents` do classify.
- Orçamento de contexto: total de `content` ≤ `KB_MAX_CHARS` (padrão 2000), truncando o último snippet.
- Se nada relevante for encontrado: `enrichment.kb_snippets = []`, e o prompt instrui *"se não houver informação na base, não invente políticas; marque needs_human"*.
- Os snippets são renderizados no prompt dentro de `<knowledge_base>` com `id`, para que o modelo cite `used_kb_ids` na saída (rastreabilidade).

## 5. Ingestão a partir do repositório do produto

```bash
python -m prompt_engine.enrichment.ingest --repo <clone do chopp-hub> \
    --config data/kb_sources.yaml --out data/knowledge_base/generated
```

| Extrator | Fonte | Como |
|---|---|---|
| `ts_objects` | `app/dashboard/help/page.tsx` | tokenizador TS; um snippet por objeto raiz do array; filhos viram itens; comentários `//` e `/* */` ignorados fora de strings |
| `jsx_accordion` | `app/marketing/sections/FAQ.tsx` | pares `AccordionTrigger` → `AccordionContent` |
| `jsx_sections` | `terms`, `privacy` | `<Section title>`/`<SummaryCard title>`; `BulletList items={[...]}` vira lista; `{" "}`, `&quot;` e marcação removidos |
| `markdown_sections` | `docs/superpowers/specs/*.md` | um snippet por `## `, com **lista permitida** de títulos (`include_headings`); tabelas e blocos de código saem; linhas quebradas são refeitas |

Garantias:
- **Determinística e local:** nada sai da máquina; rodar de novo produz o mesmo resultado.
- **Lista permitida** de arquivos e, para specs internos, de seções. Specs descrevem funcionalidades **planejadas**: confirme se já estão no ar.
- **Segredos:** trechos com padrão de credencial (Google/GitHub/Stripe/AWS, JWT, chave privada, `api_key = ...`) são **descartados** e listados no relatório.
- **Termos internos:** caminhos de código e identificadores snake_case viram `review_notes` para o revisor remover.
- **Proveniência:** `source: <repo>@<commit>:<arquivo>`.
- **Revisão preservada:** re-ingerir mantém `reviewed: true` quando título e conteúdo não mudaram; se mudaram, volta para `false`.
- Regras de categoria (`category_rules`) olham só o **título**: palavras soltas no conteúdo geravam categorias erradas (ex.: "entregas" no FAQ da planilha).

Resultado em 2026-10-14 (`chopp-hub@1674717`): 84 snippets, 0 segredos, 21 com termos internos sinalizados.

## 6. Critérios de aceitação
- [x] **EN-1** A busca "cobrado duas vezes" com category `billing` retorna `kb-billing-001` em 1º lugar.
- [x] **EN-2** O boost de categoria muda o ranking quando há empate lexical.
- [x] **EN-3** O orçamento `KB_MAX_CHARS` é respeitado.
- [x] **EN-4** KB vazia ou sem match → lista vazia, sem exceção.
- [x] **EN-5** YAML de KB inválido (campo faltando) falha no startup com mensagem clara.
- [x] **EN-6** `ContextEnrichment` sem `classify` no contexto lança `PipelineMisconfigured`.
- [x] **EN-7** Ingestão: extratores cobertos com fixtures sintéticas; segredos descartados; revisão preservada ao re-ingerir; determinística.
- [x] **EN-8** Snippets `reviewed: false` não entram na busca com a configuração padrão.
