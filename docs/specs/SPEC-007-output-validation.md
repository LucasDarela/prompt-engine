# SPEC-007 — Validação de Saída e Consistência

> Módulo: `src/prompt_engine/output/` · Status: Rascunho

## 1. Objetivo
Garantir que o frontend **sempre** receba JSON válido e consistente, mesmo quando o modelo responde com markdown, texto extra, JSON quebrado ou valores fora da enum.

## 2. Schemas (`output/schemas.py`)

```python
Category  = Literal["billing", "technical", "account", "shipping", "cancellation", "legal", "other"]
Urgency   = Literal["low", "medium", "high", "critical"]
Sentiment = Literal["very_negative", "negative", "neutral", "positive"]

class ClassifyOutput(StrictModel):            # StrictModel: extra="forbid"
    reasoning: str = Field(max_length=300)     # raciocínio curto ANTES dos rótulos (descartado na resposta final)
    category: Category
    urgency: Urgency
    sentiment: Sentiment
    entities: dict[str, str] = {}              # chaves permitidas: order_id, product, amount, date, payment_method
    intents: list[str] = Field(max_length=5)
    complexity_hint: Literal["simple", "complex"]

class DraftOutput(StrictModel):
    summary: str = Field(max_length=280)       # resumo do caso para o agente humano
    suggested_reply: str = Field(min_length=20, max_length=1500)
    needs_human: bool
    confidence: float = Field(ge=0, le=1)
    used_kb_ids: list[str] = []

class ReviewOutput(StrictModel):
    approved: bool
    issues: list[str] = []                     # itens do checklist violados
    revised_reply: str | None = None           # obrigatório se approved=False (model_validator)

class TriageResult(StrictModel):               # o que vai para o frontend
    category: Category; urgency: Urgency; sentiment: Sentiment
    summary: str; entities: dict[str, str]
    suggested_reply: str; needs_human: bool; confidence: float
```

## 3. Pipeline de validação (`OutputValidator.validate`)

```
raw text
  │ 1. strip_markdown: remove ```json … ``` e ``` … ```, BOM, prefixos tipo "Aqui está o JSON:"
  │ 2. extract_json: primeiro objeto {...} balanceado (respeitando strings/escapes)
  │ 3. json.loads → se falhar: tentativa tolerante (vírgula final, aspas simples) — só para correções triviais
  │ 4. normalização de enums: lower/strip, aliases ("cobrança"→"billing", "urgente"→"high") via tabela
  │ 5. Model.model_validate(strict)
  ▼
sucesso → (parsed, repaired=False)
falha   → REPARO (até OUTPUT_MAX_REPAIRS=1):
            prompt common.repair_json com {schema, raw_output, validation_errors}
            volta ao passo 1
falha final → FALLBACK SEGURO por step
```

### Fallbacks seguros
| Step | Fallback |
|---|---|
| `classify` | `category="other", urgency="medium", sentiment="neutral", complexity_hint="complex"` → força `needs_human` |
| `draft_reply` | resposta padrão de template ("Recebemos sua mensagem e um especialista vai responder em até {SLA}h") + `needs_human=true, confidence=0` |
| `review_reply` | `approved=true` (mantém o draft) + warning `review_unavailable` |

Todo fallback adiciona um warning em `ctx.warnings` e marca `meta.degraded=true` na resposta.

## 4. Regras de consistência (pós-validação, `AssembleTriage`)
- `needs_human = draft.needs_human OR security.verdict=="suspicious" OR urgency=="critical" OR category=="legal" OR confidence < CONFIDENCE_MIN (0.6) OR qualquer fallback`.
- Se o review reprovou, `suggested_reply = review.revised_reply`.
- `summary` vem de `DraftOutput.summary`. O `classify.reasoning` é interno e **nunca** vai para a resposta.
- Strings passam por `clean_text`: sem markdown (`**`, `#`), sem placeholders desconhecidos e com espaços normalizados.

## 5. Técnicas para consistência (resumo; detalhes em PROMPT_ENGINEERING.md)
- `temperature=0` em classify/review/judge; `0.3` no draft.
- JSON mode/schema nativo do provider quando disponível, mais schema no prompt.
- Few-shots que validam contra o schema (PL-8).
- Enums fechadas e aliases.
- Eval de consistência: N=5 execuções, mesmo input (SPEC-009).

## 6. Critérios de aceitação
- [ ] **OUT-1** `"```json\n{...}\n```"` é parseado corretamente.
- [ ] **OUT-2** `"Claro! Aqui está: {...} Espero ter ajudado"` é parseado corretamente.
- [ ] **OUT-3** JSON com chaves dentro de strings (`"reply": "use {x}"`) é extraído corretamente.
- [ ] **OUT-4** `"category": "Cobrança"` é normalizado para `billing`.
- [ ] **OUT-5** Campo extra não permitido → falha → reparo chamado 1× com os erros no prompt.
- [ ] **OUT-6** Reparo falha → fallback do step, `meta.degraded=true` e `needs_human=true`.
- [ ] **OUT-7** `ReviewOutput(approved=False, revised_reply=None)` é inválido.
- [ ] **OUT-8** A regra de `needs_human` é coberta por teste parametrizado (cada condição isolada).
- [ ] **OUT-9** Propriedade: para qualquer string de entrada (hypothesis), `validate` **nunca** lança exceção não tratada e sempre retorna o modelo ou o fallback.
