# SPEC-008 — Observabilidade

> Módulo: `src/prompt_engine/observability/` · Status: ✅ Implementado (Fase 7)

## 1. Objetivo
Registrar **exatamente** o que aconteceu em cada request (input, prompts finais, respostas, modelo, versão, tokens, latência e decisões), para:
1. depurar ("por que respondeu isso?");
2. medir custo e latência por step, modelo e versão de prompt;
3. **alimentar o sistema de testes**, exportando traces reais como candidatos a golden dataset.

## 2. Modelo de trace

```python
class Span(BaseModel):
    name: str                       # nome do stage
    status: Literal["ok", "skipped", "blocked", "error"]
    started_at: datetime            # UTC, ISO-8601
    duration_ms: int
    attributes: dict[str, Any]      # ex.: injection_score, tier, kb_ids, prompt_ref

class LLMCallRecord(BaseModel):
    step: str; prompt_id: str; prompt_version: str
    provider: str; model: str; attempt: int
    messages: list[Message]         # prompt FINAL renderizado (já sem PII)
    response_text: str              # bruto, antes da validação
    parsed_ok: bool; repaired: bool
    usage: Usage; latency_ms: int; cost_usd_estimate: float | None

class Trace(BaseModel):
    request_id: str; flow: str; created_at: datetime
    status: Literal["ok", "blocked", "error", "degraded"]
    input_redacted: str             # sempre
    input_raw: str | None           # só se OBS_STORE_RAW_INPUT=true (LGPD — desligado por padrão)
    pii_kinds: list[str]
    security: SecurityReport
    route: RouteDecision | None
    spans: list[Span]
    llm_calls: list[LLMCallRecord]
    output: dict[str, Any] | None   # resposta final enviada ao frontend (suggested_reply mascarada)
    warnings: list[str]
    total_latency_ms: int; total_tokens: Usage; total_cost_usd_estimate: float | None
    app_version: str
```

## 3. Persistência: `SQLiteTraceStore`
Arquivo: `OBS_DB_PATH` (padrão `./var/traces.db`; `var/` no `.gitignore`).

```sql
CREATE TABLE IF NOT EXISTS traces (
  request_id      TEXT PRIMARY KEY,
  created_at      TEXT NOT NULL,          -- ISO-8601 UTC, ex.: 2026-10-14T21:30:05.120Z
  flow            TEXT NOT NULL,
  status          TEXT NOT NULL,
  category        TEXT,                   -- desnormalizado p/ consulta
  tier            TEXT,
  provider        TEXT,
  total_latency_ms INTEGER,
  input_tokens    INTEGER,
  output_tokens   INTEGER,
  payload         TEXT NOT NULL           -- Trace completo em JSON
);
CREATE INDEX IF NOT EXISTS idx_traces_created ON traces(created_at);
CREATE INDEX IF NOT EXISTS idx_traces_flow_status ON traces(flow, status);
```
- A escrita roda em thread (`asyncio.to_thread`), sem bloquear o event loop; a request espera só os poucos ms do INSERT. Optamos por isso em vez de fila em background para manter os testes determinísticos. Uma falha ao gravar **não** falha a request (log `trace_store_failed`).
- Uma conexão SQLite por operação (WAL), segura entre threads. `INSERT OR REPLACE` por `request_id`.
- **Sem PII:** `input_redacted` vem do `sanitized_input`; se a request foi bloqueada antes do `PIIRedaction` (ex.: input longo demais), o texto é mascarado na hora. A `output` (já reidratada para o cliente) é **remascarada** com `PIIVault.mask`.
- **Bloqueios rastreáveis:** `error_code` + `error_details` (ex.: `score` e `signals` de um bloqueio por injection, `reason` de uma entrada inválida).
- `OBS_ENABLED=false` desliga a persistência (os logs continuam).
- Retenção: `OBS_RETENTION_DAYS` (padrão 30), com limpeza no startup.

## 4. Logs estruturados
- `structlog` em JSON. Campos sempre presentes: `ts`, `level`, `event`, `request_id`, `flow`, `stage`.
- Eventos-chave: `request.received`, `stage.completed`, `llm.call`, `llm.retry`, `llm.fallback`, `guardrail.blocked`, `output.repaired`, `output.fallback`, `request.completed`.
- **Nunca** loga `raw_input`, valores do vault, chaves de API ou o prompt completo (o prompt completo só vai para o trace store).
- `LOG_LEVEL`, `LOG_FORMAT=json|console`.
- Implementação: structlog sobre o `logging` do stdlib. O código usa `logging.getLogger(...).info("evento", extra={...})`; sem `configure_logging` a lib fica silenciosa. O `request_id` entra via `structlog.contextvars` (middleware). O filtro `mask_pii` roda em **todo** campo antes de renderizar.
- `httpx`, `httpcore`, `google_genai` e `urllib3` ficam em WARNING: o SDK do Gemini logaria cada chamada HTTP em INFO.

## 5. Custo
`llm/pricing.yaml` com preço por 1M tokens (input/output) por modelo. **Entregue vazio de propósito:** não usamos preços inventados (e o plano gratuito do Gemini custa zero). Sem preço, `cost_usd_estimate=null`; o total também fica `null` se algum modelo não tiver preço.

## 6. Exportação para testes (`observability/export.py`)
```bash
python -m prompt_engine.observability.export \
  --since 2026-10-01 --status ok --flow support_triage \
  --out tests/fixtures/candidates.jsonl
```
Gera linhas no formato do golden dataset (`prompt_engine/evals/dataset.py`: `GoldenCase`), com o input **mascarado**, `expected` **pré-preenchido** com a saída do modelo (`category`, `urgency`, `needs_human`, `entities`) e `"reviewed": false`. Um humano confere, corrige e move para `golden_dataset.jsonl`.

Validado ao vivo em 2026-10-17: request com e-mail → logs JSON com o mesmo `request_id` (`llm.call` ×3, `pipeline.finished`, `request.completed`), trace com prompt e versão de cada step, e-mail ausente de logs e trace, export gerando um candidato válido.

## 7. Critérios de aceitação
- [x] **OBS-1** Cada request (ok, blocked, error) gera exatamente 1 trace com spans para todos os stages executados.
- [x] **OBS-2** O trace contém o prompt final renderizado de cada chamada LLM e a versão do prompt.
- [x] **OBS-3** Com `OBS_STORE_RAW_INPUT=false`, `input_raw` é `None` e nenhum valor do vault aparece no `payload` (teste busca o CPF de teste no JSON).
- [x] **OBS-4** Falha do store (disco read-only simulado) não altera a resposta HTTP.
- [x] **OBS-5** Os logs de uma request compartilham o mesmo `request_id`.
- [x] **OBS-6** `export` gera JSONL que valida contra o schema do golden dataset.
- [x] **OBS-7** `GET /api/v1/traces/{id}` (debug) retorna o trace salvo.
