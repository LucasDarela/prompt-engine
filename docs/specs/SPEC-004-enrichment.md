# SPEC-004 — Enriquecimento Oculto e Base de Conhecimento

> Módulo: `src/prompt_engine/enrichment/` · Dados: `data/knowledge_base/` · Status: Rascunho

## 1. Objetivo
O usuário escreve só a intenção. O sistema anexa, **sem que ele veja**:
1. **Estático** (todo step): system prompt, persona, regras de negócio, formato de saída. Vem das parciais do `PromptRegistry` (SPEC-005).
2. **Dinâmico** (depois do `classify`): trechos relevantes da KB, a política e o SLA da categoria e o perfil do cliente (opcional).

## 2. Formato da KB

`data/knowledge_base/faq.yaml`, `policies.yaml`, `sla.yaml`:
```yaml
- id: kb-billing-001
  category: billing                 # mesma enum de ClassifyOutput.category
  title: Cobrança duplicada
  content: |
    Cobranças duplicadas no cartão são estornadas em até 7 dias úteis após confirmação.
    O agente deve solicitar o número do pedido e abrir chamado no financeiro.
  tags: [estorno, duplicada, cartão]
  updated_at: 2026-09-30
```

`sla.yaml`:
```yaml
billing:   { first_response_hours: 4,  escalate_if_urgency: [high, critical] }
technical: { first_response_hours: 8,  escalate_if_urgency: [critical] }
```

`data/customers.yaml` (opcional, só para demo, com dados sintéticos):
```yaml
cus_123: { plan: premium, tenure_months: 26, open_tickets: 1, display_name: "Maria" }
```
> O `display_name` do perfil **não** é enviado ao modelo. O modelo usa o placeholder `{{customer_name}}`, que é substituído na reidratação.

## 3. Interfaces

```python
class KBSnippet(BaseModel):
    id: str; category: str; title: str; content: str; score: float

class KnowledgeBase(Protocol):
    def search(self, query: str, *, category: str | None = None, k: int = 3) -> list[KBSnippet]: ...

class FileKnowledgeBase:   # implementação MVP
    """Carrega os YAMLs no startup. Ranqueia por BM25 simples (tokenização PT, sem acentos, stopwords)
    com boost ×2 para snippets da mesma categoria."""

class CustomerRepository(Protocol):
    def get(self, customer_id: str) -> CustomerProfile | None: ...

class ContextEnrichment:   # Stage
    """Lê ctx.steps['classify'].parsed.category + sanitized_input; escreve ctx.enrichment."""
```

## 4. Regras
- `k = KB_TOP_K` (padrão 3); snippets com `score < KB_MIN_SCORE` são descartados.
- Orçamento de contexto: total de `content` ≤ `KB_MAX_CHARS` (padrão 2000), truncando o último snippet.
- Se nada relevante for encontrado: `enrichment.kb_snippets = []`, e o prompt instrui *"se não houver informação na base, não invente políticas; marque needs_human"*.
- Os snippets são renderizados no prompt dentro de `<knowledge_base>` com `id`, para que o modelo cite `used_kb_ids` na saída (rastreabilidade).

## 5. Critérios de aceitação
- [ ] **EN-1** A busca "cobrado duas vezes" com category `billing` retorna `kb-billing-001` em 1º lugar.
- [ ] **EN-2** O boost de categoria muda o ranking quando há empate lexical.
- [ ] **EN-3** O orçamento `KB_MAX_CHARS` é respeitado.
- [ ] **EN-4** KB vazia ou sem match → lista vazia, sem exceção.
- [ ] **EN-5** YAML de KB inválido (campo faltando) falha no startup com mensagem clara.
- [ ] **EN-6** `ContextEnrichment` sem `classify` no contexto lança `PipelineMisconfigured`.
