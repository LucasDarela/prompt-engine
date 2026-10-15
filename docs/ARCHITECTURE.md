# ARCHITECTURE — Prompt Engine

> Status: **Rascunho para aprovação** · Data: 2026-10-14
> Leitura prévia: [PRD.md](PRD.md). Decisões justificadas em [adr/](adr/).

## 1. Visão geral

O Prompt Engine é um **middleware de prompts** entre um frontend (text area) e provedores de LLM.
O texto cru do usuário nunca chega direto ao modelo: ele passa por uma **cadeia de stages** que protege, enriquece, roteia, executa, valida e registra.

```
┌──────────────┐  POST /api/v1/process-prompt   ┌───────────────────────────────────────────────┐
│  Frontend    │ ─────────────────────────────▶ │                 FastAPI (api/)                │
│ (text area)  │ ◀───────────────────────────── │  schemas · deps · error handlers · auth key   │
└──────────────┘     JSON estruturado e limpo   └──────────────────────┬────────────────────────┘
                                                                       │ FlowRunner.run("support_triage", input)
                                                                       ▼
┌──────────────────────────────────────── Pipeline (core/) ──────────────────────────────────────────┐
│                                                                                                    │
│  ① InputValidation → ② InjectionGuard → ③ PIIRedaction                       [guardrails/]         │
│            │ (falha = short-circuit 422)                                                           │
│            ▼                                                                                       │
│  ④ LLMStep "classify"  ──▶ ⑤ ContextEnrichment ──▶ ⑥ Router ──▶ ⑦ LLMStep "draft_reply"           │
│     (tier fast)              [enrichment/]           [routing/]     (tier escolhido pelo router)   │
│            │                                                                                       │
│            ▼                                                                                       │
│  ⑧ LLMStep "review_reply" (opcional) ──▶ ⑨ OutputValidation ──▶ ⑩ PIIRehydration ──▶ ⑪ Assemble    │
│     (self-critique, tier fast)            [output/]                [guardrails/]                   │
│                                                                                                    │
│  Transversal: Tracer (spans por stage) ─────────────────────────▶ TraceStore (SQLite) [observability/]│
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
                       │ cada LLMStep usa:
                       ▼
     PromptRegistry (prompts/) ── render(template@versão, vars) ──▶ LLMProvider (llm/)
     YAML + Jinja2 versionados                                        Gemini · Anthropic · OpenAI · Fake
```

### Correspondência com o fluxo sugerido

| Fluxo original | Implementação |
|---|---|
| `validate_input()` | Stage `InputValidation` + `InjectionGuard` + `PIIRedaction` |
| `inject_context()` | `ContextEnrichment` (dados de negócio/KB) + parciais do `PromptRegistry` (system prompt, regras, formato) |
| `build_final_prompt()` | `PromptRenderer` dentro de cada `LLMStep` |
| Chamada à LLM | `LLMProvider.generate()` escolhido pelo `Router` |
| `validate_json()` / `clean_markdown()` | `output/` (`extract_json`, `strip_markdown`, validação Pydantic, reparo) |

## 2. Princípios

1. **Pipeline como composição de stages puros.** Cada stage recebe um `PipelineContext` e devolve um novo, sem conhecer os outros. É assim que a gente consegue testar e reutilizar.
2. **Prompt é dado, não código.** Os prompts vivem em YAML versionado. O código só os carrega e renderiza ([ADR-0003](adr/0003-prompts-como-dados-versionados.md)).
3. **Provedor é detalhe.** O núcleo depende só do protocolo `LLMProvider` (Ports & Adapters, [ADR-0002](adr/0002-abstracao-de-provedores-llm.md)).
4. **Nenhuma PII sai do processo.** O mascaramento acontece antes de qualquer I/O externo, e a reidratação só depois que a saída volta.
5. **Determinismo nos testes.** Toda a suíte padrão roda offline com o `FakeProvider` ([ADR-0004](adr/0004-fake-provider-e-testes-offline.md)).
6. **Falhar fechado.** Em caso de dúvida (injection, JSON inválido depois do reparo), a resposta é segura: `needs_human: true`, nunca alucinação.
7. **Config por ambiente.** Modelos, limites e flags ficam em `.env`/`Settings`, nunca hardcoded.

## 3. Estrutura de diretórios (alvo)

```
capstone módulo 2/
├── main.py                         # entrypoint: `uvicorn main:app` → importa create_app()
├── pyproject.toml                  # deps, ruff, mypy, pytest config
├── .env.example                    # todas as variáveis documentadas (sem segredos)
├── README.md
├── CLAUDE.md                       # instruções para agentes de IA
├── docs/
│   ├── PRD.md  ARCHITECTURE.md  CONVENTIONS.md  PROMPT_ENGINEERING.md
│   ├── specs/                      # SPEC-001..009 (contratos de cada módulo)
│   ├── adr/                        # Architecture Decision Records
│   └── plans/IMPLEMENTATION_PLAN.md
├── src/prompt_engine/
│   ├── __init__.py
│   ├── config.py                   # Settings (pydantic-settings)
│   ├── api/
│   │   ├── app.py                  # create_app(), middlewares, CORS, exception handlers
│   │   ├── routes.py               # /health, /api/v1/process-prompt, /api/v1/prompts, /api/v1/traces/{id}
│   │   ├── schemas.py              # ProcessPromptRequest/Response (contrato HTTP)
│   │   └── deps.py                 # injeção de dependências (FlowRunner, Settings, TraceStore)
│   ├── core/
│   │   ├── context.py              # PipelineContext, StepResult, Usage
│   │   ├── stage.py                # protocolo Stage
│   │   ├── pipeline.py             # Pipeline (executa stages, short-circuit, spans)
│   │   ├── errors.py               # hierarquia de exceções de domínio
│   │   ├── vault.py                # PIIVault (estrutura de dados; detecção fica em guardrails/pii)
│   │   ├── tracing.py              # porta TraceSink + Span/PipelineRun (impl. em observability/)
│   │   └── llm_step.py             # LLMStep genérico (render → call → parse → validate)
│   ├── guardrails/
│   │   ├── input_validation.py
│   │   ├── injection/              # heuristics.py, classifier.py, guard.py, patterns.yaml
│   │   └── pii/                    # detectors.py (CPF, CNPJ, cartão...), vault.py, redaction.py, rehydration.py
│   ├── enrichment/
│   │   ├── knowledge_base.py       # protocolo KnowledgeBase + FileKnowledgeBase
│   │   └── enricher.py             # stage ContextEnrichment
│   ├── routing/
│   │   ├── complexity.py           # ComplexityScorer (heurística + sinais do classify)
│   │   └── router.py               # stage Router → (tier, provider, model)
│   ├── llm/
│   │   ├── base.py                 # protocolo LLMProvider, LLMRequest, LLMResponse
│   │   ├── gemini.py  anthropic.py  openai.py
│   │   ├── fake.py                 # FakeProvider determinístico (testes/demos)
│   │   ├── resilience.py           # retry/backoff, timeout, fallback chain
│   │   └── factory.py              # build_provider(name, settings)
│   ├── prompts/
│   │   ├── registry.py             # PromptRegistry (carrega, valida, resolve versões)
│   │   ├── renderer.py             # Jinja2 StrictUndefined + spotlighting
│   │   ├── models.py               # PromptTemplate (schema do YAML)
│   │   └── library/
│   │       ├── _partials/          # system_base.j2, business_rules.j2, output_format.j2, safety.j2
│   │       └── support_triage/     # classify.yaml, draft_reply.yaml, review_reply.yaml, injection_judge.yaml
│   ├── output/
│   │   ├── cleaners.py             # strip_markdown, extract_json, loads_lenient, clean_text
│   │   ├── normalize.py            # enums: caixa, acentos, aliases PT/EN
│   │   ├── fallbacks.py            # saídas seguras por step
│   │   ├── schemas.py              # ClassifyOutput, DraftOutput, ReviewOutput, TriageResult
│   │   └── validator.py            # validate + repair loop + fallback
│   ├── flows/
│   │   ├── registry.py             # FlowRegistry: nome → lista de stages
│   │   └── support_triage.py       # montagem do fluxo de triagem
│   └── observability/
│       ├── logging.py              # structlog JSON
│       ├── tracer.py               # Trace, Span, contexto por request
│       ├── store.py                # protocolo TraceStore + SQLiteTraceStore
│       └── export.py               # traces → candidatos a golden dataset
├── data/
│   └── knowledge_base/             # faq.yaml, policies.yaml, sla.yaml
├── tests/
│   ├── conftest.py                 # fixtures: settings, fake provider, registry, client
│   ├── unit/                       # um arquivo por módulo
│   ├── integration/                # pipeline completo + API com FakeProvider
│   ├── prompts/                    # snapshot dos prompts renderizados (syrupy)
│   ├── evals/                      # @pytest.mark.live — modelo real
│   └── fixtures/                   # golden_dataset.jsonl, injection_redteam.jsonl, benign.jsonl
└── examples/
    ├── curl.sh
    ├── python_client.py
    ├── library_usage.py            # uso da biblioteca sem a API
    └── frontend/index.html         # text area mínimo
```

## 4. Modelo de dados central

```python
class PipelineContext(BaseModel):
    request_id: str
    flow: str
    raw_input: str                         # NUNCA logado nem enviado a provedor
    sanitized_input: str | None            # após PII redaction
    pii_vault: PIIVault                    # placeholders ↔ valores (excluído de serialização)
    customer_id: str | None
    locale: str = "pt-BR"
    security: SecurityReport               # injection_score, verdict, signals
    enrichment: EnrichmentData             # kb_snippets, business_rules, customer_profile
    route: RouteDecision | None            # tier, provider, model, reason, complexity_score
    steps: dict[str, StepResult]           # resultados por nome de step (classify, draft_reply…)
    result: TriageResult | None
    warnings: list[str]
```

Cada `StepResult` guarda: `prompt_id`, `prompt_version`, `rendered_messages`, `provider`, `model`, `raw_text`, `parsed`, `usage` (tokens), `latency_ms`, `attempts` e `repaired: bool`.

O contexto é **imutável por convenção**: os stages retornam `ctx.model_copy(update=...)`. Isso permite snapshotar cada passo nos traces.

## 5. Fluxo `support_triage` (multi-step)

| # | Stage | Tipo | Tier/Modelo | Saída no contexto |
|---|---|---|---|---|
| 1 | `InputValidation` | determinístico | — | normaliza unicode/whitespace, valida tamanho/idioma |
| 2 | `InjectionGuard` | heurística + LLM (zona cinzenta) | `fast` | `security` (pode short-circuitar) |
| 3 | `PIIRedaction` | determinístico | — | `sanitized_input`, `pii_vault` |
| 4 | `LLMStep(classify)` | LLM | `fast`, temp 0 | `category`, `urgency`, `sentiment`, `entities`, `intents`, `complexity_hint` |
| 5 | `ContextEnrichment` | determinístico | — | trechos da KB por categoria + regras + SLA |
| 6 | `Router` | determinístico | — | `RouteDecision` para o draft |
| 7 | `LLMStep(draft_reply)` | LLM | roteado (`fast`/`balanced`/`powerful`) | `suggested_reply`, `needs_human`, `confidence` |
| 8 | `LLMStep(review_reply)` | LLM (self-critique) | `fast` | aprova ou corrige o draft (desligável) |
| 9 | `OutputValidation` | determinístico | — | `TriageResult` validado |
| 10 | `PIIRehydration` | determinístico | — | placeholders → valores reais na `suggested_reply` |
| 11 | `Assemble` | determinístico | — | resposta HTTP + `meta` |

**Por que classificar antes de enriquecer?** O contexto relevante da KB depende da categoria. O enriquecimento *estático* (system prompt, regras, formato) entra em todos os steps via parciais. O enriquecimento *dinâmico* (KB) entra depois da classificação.

**Orçamento de chamadas:** 2 obrigatórias (classify, draft), mais 1 opcional (review), mais 1 condicional (juiz de injection na zona cinzenta). Isso cabe nos limites do plano gratuito do Gemini.

## 6. Contratos entre módulos (resumo)

```python
class Stage(ABC):
    name: str
    def is_enabled(self, ctx: PipelineContext) -> bool: ...   # default True
    async def run(self, ctx: PipelineContext) -> PipelineContext: ...

class LLMProvider(Protocol):
    name: str
    async def generate(self, request: LLMRequest) -> LLMResponse: ...

class KnowledgeBase(Protocol):
    def search(self, query: str, *, category: str | None, k: int) -> list[KBSnippet]: ...

class TraceStore(Protocol):
    def save(self, trace: Trace) -> None: ...
    def get(self, request_id: str) -> Trace | None: ...
    def list(self, *, limit: int, flow: str | None) -> list[TraceSummary]: ...
```

Os detalhes completos ficam nas specs:

| Módulo | Spec |
|---|---|
| API HTTP | [SPEC-001](specs/SPEC-001-api.md) |
| Core do pipeline e flows | [SPEC-002](specs/SPEC-002-pipeline-core.md) |
| Guardrails (validação, injection, PII) | [SPEC-003](specs/SPEC-003-guardrails.md) |
| Enriquecimento / KB | [SPEC-004](specs/SPEC-004-enrichment.md) |
| Biblioteca de prompts | [SPEC-005](specs/SPEC-005-prompt-library.md) |
| Provedores LLM e roteamento | [SPEC-006](specs/SPEC-006-llm-providers-routing.md) |
| Validação de saída | [SPEC-007](specs/SPEC-007-output-validation.md) |
| Observabilidade | [SPEC-008](specs/SPEC-008-observability.md) |
| Testes e evals | [SPEC-009](specs/SPEC-009-testing-evals.md) |

## 7. Regras de dependência entre camadas

```
api  ──▶ flows ──▶ core ◀── guardrails, enrichment, routing, output, prompts
                    │
                    └──▶ llm (apenas via protocolo LLMProvider)
observability ◀── usado por core e api (nunca o contrário)
```

- `core` **não importa** nenhum adapter concreto (`gemini.py` etc.), só `llm/base.py`.
- `api` **não contém lógica de negócio**: só traduz HTTP ↔ `FlowRunner`.
- `prompts/library` **não contém Python**.
- Os SDKs de provedor (`google-genai`, `anthropic`, `openai`) são **dependências opcionais** (extras), importadas de forma lazy no adapter.

## 8. Stack

| Camada | Escolha | Motivo |
|---|---|---|
| Linguagem | Python ≥ 3.12 (venv atual: 3.14) | Pedido do projeto |
| API | FastAPI + Uvicorn | Async, OpenAPI automático, Pydantic nativo ([ADR-0001](adr/0001-fastapi-como-camada-http.md)) |
| Modelos/validação | Pydantic v2 + pydantic-settings | Schemas de I/O e config tipada |
| Templates | Jinja2 (`StrictUndefined`) + YAML (PyYAML) | Prompts como dados, com parciais e herança |
| LLM SDKs | `google-genai`, `anthropic`, `openai` (extras) | Adapters plugáveis |
| Resiliência | `tenacity` | Retry/backoff declarativo |
| Logs | `structlog` (JSON) | Logs estruturados para correlação |
| Traces | SQLite (stdlib `sqlite3`) | Zero infra, consultável, exportável |
| Testes | pytest, pytest-asyncio, pytest-cov, syrupy, httpx (TestClient) | Unit/integration/snapshot |
| Qualidade | ruff (lint+format), mypy (strict em `src/`) | Padrão sênior |

## 9. Tratamento de erros

| Exceção de domínio | Origem | HTTP | Comportamento |
|---|---|---|---|
| `InputValidationError` | InputValidation | 422 | short-circuit, sem chamada LLM |
| `PromptInjectionDetected` | InjectionGuard | 422 | short-circuit, `code: "unsafe_input"`, trace com sinais |
| `ProviderUnavailableError` | llm/ | 503 | depois de retries + fallback esgotados |
| `ProviderRateLimitError` | llm/ | 429 | propaga `retry_after` quando disponível |
| `OutputValidationError` | output/ | — | **não sobe**: reparo → fallback seguro (`needs_human: true`) |
| `PromptNotFoundError` / `TemplateRenderError` | prompts/ | 500 | bug de configuração, logado como error |

O envelope de erro é sempre o mesmo: `{ "request_id", "status": "error", "error": { "code", "message" } }`. Detalhes internos nunca vazam.

## 10. Segurança

- Defesa em profundidade contra injection: heurística → juiz LLM → **spotlighting** (input do usuário entre delimitadores e tratado como dado) → canary token no system prompt → validação de schema na saída.
- PII mascarada com placeholders tipados (`[CPF_1]`); o vault vive só na memória da request.
- O `TraceStore` guarda o input redigido por padrão (`OBS_STORE_RAW_INPUT=false`).
- Chaves de API só via env. `.env` está no `.gitignore`.
- Autenticação opcional do endpoint por `X-API-Key` (`API_KEY` em env). CORS restrito por config.
- Limite de tamanho de entrada (`MAX_INPUT_CHARS`, padrão 4000).

## 11. Extensibilidade (como estender sem tocar no core)

| Quero... | Faço... |
|---|---|
| Novo provedor (ex.: Mistral) | Novo adapter em `llm/` + registro em `factory.py` + teste de contrato |
| Novo fluxo (ex.: resumo de contrato) | Novos YAMLs em `prompts/library/<flow>/` + `flows/<flow>.py` compondo stages existentes |
| Nova versão de prompt | Novo YAML com `version` incrementada; o fluxo aponta `classify@2.0.0` ou `latest` |
| Novo detector de PII | Nova classe em `pii/detectors.py` registrada na lista |
| KB vetorial | Nova implementação de `KnowledgeBase` |
| Store em Postgres | Nova implementação de `TraceStore` |

## 12. Glossário

| Termo | Significado |
|---|---|
| **Stage** | Unidade do pipeline: `PipelineContext → PipelineContext` |
| **Step / LLMStep** | Stage que chama uma LLM com um prompt da biblioteca |
| **Flow** | Sequência nomeada de stages (ex.: `support_triage`) |
| **Tier** | Classe de modelo: `fast`, `balanced`, `powerful` |
| **Spotlighting** | Técnica de marcar o input não confiável com delimitadores e instruções explícitas |
| **Vault** | Mapa em memória placeholder → valor de PII |
| **Golden dataset** | Conjunto rotulado de entradas e saídas esperadas para evals |
| **Trace / Span** | Registro de uma request / de um stage dentro dela |
