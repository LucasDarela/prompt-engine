# SPEC-008 — Observabilidade

> Módulo: `src/prompt_engine/observability/` · Status: Rascunho

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
- A escrita acontece **fora do caminho crítico**: `asyncio.to_thread` + fila. Uma falha ao gravar o trace **não** falha a request (log em `warning`).
- `OBS_ENABLED=false` desliga a persistência (os logs continuam).
- Retenção: `OBS_RETENTION_DAYS` (padrão 30), com limpeza no startup.

## 4. Logs estruturados
- `structlog` em JSON. Campos sempre presentes: `ts`, `level`, `event`, `request_id`, `flow`, `stage`.
- Eventos-chave: `request.received`, `stage.completed`, `llm.call`, `llm.retry`, `llm.fallback`, `guardrail.blocked`, `output.repaired`, `output.fallback`, `request.completed`.
- **Nunca** loga `raw_input`, valores do vault, chaves de API ou o prompt completo (o prompt completo só vai para o trace store).
- `LOG_LEVEL`, `LOG_FORMAT=json|console`.

## 5. Custo
`llm/pricing.yaml` com preço por 1M tokens (input/output) por modelo. É opcional; quando ausente, `cost_usd_estimate=None`. Os valores são informativos e devem ser atualizados manualmente.

## 6. Exportação para testes (`observability/export.py`)
```bash
python -m prompt_engine.observability.export \
  --since 2026-10-01 --status ok --flow support_triage \
  --out tests/fixtures/candidates.jsonl
```
Gera linhas sintéticas/redigidas no formato do golden dataset (SPEC-009) com `expected` **pré-preenchido** com a saída do modelo e `"reviewed": false`. Um humano revisa e move para `golden_dataset.jsonl`.

## 7. Critérios de aceitação
- [ ] **OBS-1** Cada request (ok, blocked, error) gera exatamente 1 trace com spans para todos os stages executados.
- [ ] **OBS-2** O trace contém o prompt final renderizado de cada chamada LLM e a versão do prompt.
- [ ] **OBS-3** Com `OBS_STORE_RAW_INPUT=false`, `input_raw` é `None` e nenhum valor do vault aparece no `payload` (teste busca o CPF de teste no JSON).
- [ ] **OBS-4** Falha do store (disco read-only simulado) não altera a resposta HTTP.
- [ ] **OBS-5** Os logs de uma request compartilham o mesmo `request_id`.
- [ ] **OBS-6** `export` gera JSONL que valida contra o schema do golden dataset.
- [ ] **OBS-7** `GET /api/v1/traces/{id}` (debug) retorna o trace salvo.
