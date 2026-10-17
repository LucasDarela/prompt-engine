# Plano de Implementação

> Status: **Aguardando aprovação** · Método: TDD, uma task por vez, um commit por task
> Cada task referencia os critérios de aceitação (IDs) das specs. A task só fecha quando a [Definition of Done](../CONVENTIONS.md#7-definition-of-done-por-task) é cumprida.

## Visão das fases

| Fase | Entrega | Depende de | Demo ao final |
|---|---|---|---|
| 0 | Fundação do repositório | — | `pytest` roda (0 testes), lint/mypy verdes |
| 1 | Core do pipeline + output validation | 0 | pipeline com stages fake + parser robusto |
| 2 | Biblioteca de prompts | 1 | `GET /prompts` lista; snapshots |
| 3 | Camada LLM (Fake + Gemini) e resiliência | 1 | chamada real ao Gemini via script |
| 4 | Guardrails (validação, injection, PII) | 1, 2 | bloqueio de injection; PII nunca sai |
| 5 | Enriquecimento + roteamento | 2, 3 | tier muda conforme a mensagem |
| 6 | Flow `support_triage` + API | 2–5 | **MVP ponta a ponta** com Gemini |
| 7 | Observabilidade | 6 | traces no SQLite + export |
| 8 | Adapters Anthropic e OpenAI | 3 | troca de provider por env |
| 9 | Evals + datasets | 6, 7 | relatório de métricas |
| 10 | Exemplos, README final e polimento | todas | entrega do capstone |

> **Caminho crítico do MVP:** 0 → 1 → 2 → 3 → 4 → 5 → 6. As fases 7, 8 e 9 podem ser paralelizadas depois da 6.

---

## Fase 0 — Fundação
- [x] **0.1** `git init`, `.gitignore` (venv/, .env, var/, reports/, `__pycache__`, .pytest_cache, .mypy_cache, .coverage)
- [x] **0.2** `pyproject.toml`: projeto `prompt-engine`, `requires-python>=3.12`, layout `src/`, deps core (`fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `jinja2`, `pyyaml`, `tenacity`, `structlog`, `python-ulid`), extras `gemini`/`anthropic`/`openai`/`all`/`dev` (`pytest`, `pytest-asyncio`, `pytest-cov`, `syrupy`, `hypothesis`, `httpx`, `ruff`, `mypy`, `types-PyYAML`). Config de ruff, mypy e pytest (`-m "not live"`, `asyncio_mode=auto`, cov ≥ 80)
- [x] **0.3** Verificar a instalação no venv Python 3.14 → ✅ todas as dependências instalaram no 3.14.4 (2026-10-14)
- [x] **0.4** `.env.example` com todas as variáveis (Apêndice A); `src/prompt_engine/config.py` com `Settings` + teste de defaults
- [x] **0.5** Esqueleto de pacotes (`__init__.py`) conforme ARCHITECTURE §3; `core/errors.py` com a hierarquia de exceções
- [x] **0.6** `main.py`: `from prompt_engine.api.app import create_app; app = create_app()` com `/health` mínimo + teste
- [x] **0.7** `Makefile`/scripts: `make test`, `make lint`, `make run`, `make eval`

## Fase 1 — Core + Output (SPEC-002, SPEC-007)
- [x] **1.1** `core/context.py`: `PipelineContext`, `StepResult`, `Usage`, `SecurityReport`, `RouteDecision`, `EnrichmentData` (testes de imutabilidade/`with_step`)
- [x] **1.2** `core/stage.py` + `core/pipeline.py` com o tracer no-op injetável → CORE-1, CORE-2, CORE-3
- [x] **1.3** `output/schemas.py` (Classify/Draft/Review/TriageResult) + validators → OUT-7
- [x] **1.4** `output/cleaners.py`: `strip_markdown`, `extract_json` (balanceamento com strings) → OUT-1, OUT-2, OUT-3
- [x] **1.5** Normalização de enums/aliases → OUT-4
- [x] **1.6** `output/validator.py`: validate + loop de reparo (callback) + fallbacks → OUT-5, OUT-6, OUT-9 (hypothesis)

## Fase 2 — Biblioteca de prompts (SPEC-005)
- [x] **2.1** `prompts/models.py` (`PromptTemplate`, enum `Technique`, `Version` SemVer)
- [x] **2.2** `prompts/registry.py`: load do diretório, resolução `@latest`/`@x.y.z`, erros claros → PL-1, PL-2, PL-3
- [x] **2.3** `prompts/renderer.py`: Jinja2 StrictUndefined, verificação de variáveis, escape de `<user_input>`, injeção de `schema_json` → PL-4, PL-6, GR-11
- [x] **2.4** Parciais `_partials/*.j2`
- [x] **2.5** YAMLs: `classify`, `draft_reply`, `review_reply`, `injection_judge`, `common.repair_json`, `evals.judge_reply` → PL-7
- [x] **2.6** Teste "few-shots validam contra o schema" → PL-8
- [x] **2.7** Snapshot tests de todos os prompts → PL-5

## Fase 3 — Camada LLM (SPEC-006 §2–4)
- [x] **3.1** `llm/base.py` (Message, LLMRequest, LLMResponse, Usage, Protocol, erros de provider)
- [x] **3.2** `llm/fake.py` roteirizável + registro de calls
- [x] **3.3** Suíte de **contract tests** parametrizável (roda com o Fake primeiro)
- [x] **3.4** `llm/gemini.py` com `google-genai` (system_instruction, roles user/model, JSON schema nativo, usage, tradução de erros); contract tests com SDK mockado → LLM-1
- [x] **3.5** `llm/pool.py` (era `resilience.py`): timeout, retry com backoff (loop próprio) e fallback chain → LLM-3, LLM-4, LLM-5
- [x] **3.6** `llm/factory.py` + `ProviderPool.resolve(tier)` + import lazy com erro claro → LLM-6
- [x] **3.7** `core/llm_step.py` integrando registry, renderer, pool e validator → CORE-4, CORE-5
- [x] **3.8** Script `examples/smoke_gemini.py` (manual, `@live`): 1 chamada real ao classify

## Fase 4 — Guardrails (SPEC-003)
- [x] **4.1** `input_validation.py` → GR-1 (normalização) e limites
- [x] **4.2** `injection/patterns.yaml` + `heuristics.py` (score combinado)
- [x] **4.3** `injection/guard.py` com zona cinzenta → juiz LLM (via `LLMStep` interno) → GR-4
- [x] **4.4** (feito na Fase 3, em `LLMStep`) Canary: geração por request, injeção via `safety.j2`, verificação na saída → GR-10
- [x] **4.5** `pii/detectors.py`: CPF, CNPJ (dígitos verificadores), cartão (Luhn), e-mail, telefone, CEP, PIX → GR-5, GR-6
- [x] **4.6** `pii/vault.py` + `redaction.py` → GR-7, GR-8 (hypothesis)
- [x] **4.7** `pii/rehydration.py` → GR-9
- [x] **4.8** Datasets iniciais `injection_redteam.jsonl` (30) e `benign.jsonl` (50) + testes offline → GR-2, GR-3

## Fase 5 — Enriquecimento + Roteamento (SPEC-004, SPEC-006 §5)
- [x] **5.1** `data/knowledge_base/*.yaml` com conteúdo realista (≥ 3 snippets por categoria) + `sla.yaml`
- [x] **5.2** `enrichment/knowledge_base.py` (BM25 simples PT + boost de categoria) → EN-1..5
- [x] **5.3** `enrichment/enricher.py` (stage) → EN-6
- [x] **5.4** `routing/complexity.py` → RT-1, RT-2, RT-4
- [x] **5.5** `routing/router.py` (stage, force tier, override de provider) → RT-3
- [x] **5.6** (novo) `enrichment/ingest.py`: ingestão determinística do repositório `chopp-hub` (4 extratores, filtro de segredos, `review_notes` de termos internos, revisão preservada) → EN-7, EN-8
- [x] **5.7** (novo) Ingestão real: `data/kb_sources.yaml` → 84 snippets em `data/knowledge_base/generated/` (`chopp-hub@1674717`)
- [ ] **5.8** 👤 **Revisão humana (Lucas)** dos 84 snippets gerados: remover termos internos apontados em `review_notes`, confirmar se as funcionalidades dos specs já estão no ar e marcar `reviewed: true`. Validar também os itens `manual-demo` e o `sla.yaml`.

## Fase 6 — Flow + API (SPEC-002 §5, SPEC-001) — **MVP**
- [x] **6.1** `AssembleTriage` com a regra de `needs_human` → OUT-8
- [x] **6.2** `flows/registry.py` + `flows/support_triage.py` → CORE-7, CORE-8
- [x] **6.3** Teste da invariante de PII no fluxo completo → CORE-6
- [x] **6.4** `api/schemas.py`, `api/routes.py`, `api/deps.py`, `api/app.py` (request id, CORS, API key, handlers) → API-1..8
- [x] **6.5** Rodar manualmente com Gemini real (`make run` + `examples/curl.sh`) e registrar a saída no README
- [x] **6.6** (achados do teste ao vivo) Regras "IA não executa ações" e "nada financeiro é prometido" em prompt + review + guarda determinística (`unverified_action_claim`, `financial_promise`); warning `missing_accents`; latência por step no debug
- [x] **6.7** Fallback entre **modelos do mesmo provedor**: com só a chave do Gemini, um 503 persistente num modelo derruba a request (visto ao vivo em 2026-10-14) mesmo com outros modelos Gemini respondendo. Ex.: `balanced` em 503 → tentar `powerful`, depois `fast`

## Fase 7 — Observabilidade (SPEC-008)
- [ ] **7.1** `observability/logging.py` (structlog, filtros anti-PII) → OBS-5
- [ ] **7.2** `observability/tracer.py` (Trace/Span por request, `LLMCallRecord`) integrado ao Pipeline/LLMStep → OBS-1, OBS-2
- [ ] **7.3** `observability/store.py` SQLite com escrita assíncrona e tolerante a falha → OBS-3, OBS-4
- [ ] **7.4** `GET /api/v1/traces/{id}` (debug) → OBS-7, API-6
- [ ] **7.5** `llm/pricing.yaml` + estimativa de custo
- [ ] **7.6** `observability/export.py` (CLI) → OBS-6

## Fase 8 — Provedores adicionais (SPEC-006)
- [ ] **8.1** `llm/anthropic.py` + contract tests → LLM-1
- [ ] **8.2** `llm/openai.py` + contract tests → LLM-1
- [ ] **8.3** Smoke `@live` por provider com chave → LLM-2

## Fase 9 — Evals (SPEC-009 §5)
- [ ] **9.1** `golden_dataset.jsonl` com ≥ 40 casos revisados
- [ ] **9.2** `tests/evals/conftest.py`: opção `--provider`, rate limiting e coleta de resultados
- [ ] **9.3** Evals de validade, acurácia, needs_human, consistência, injection e PII leak
- [ ] **9.4** `evals.judge_reply` + `test_reply_quality`
- [ ] **9.5** Gerador de relatório `reports/eval-*.{json,md}`
- [ ] **9.0c** Medir a frequência do warning `missing_accents` nos evals (Gemini omite acentos de forma intermitente em JSON mode, inclusive na `revised_reply` do review). Se relevante: tier mínimo `balanced` no draft, ou reparo dirigido.
- [ ] **9.0b** Ampliar `injection_redteam.jsonl` com ataques **escritos por outra pessoa** (os atuais foram escritos junto com os padrões; risco de sobreajuste) e medir recall do juiz real.
- [ ] **9.0** Corrigir extração de entidades do `classify`: no smoke de 2026-10-14 o modelo pôs `"amount": "outubro"` (mês no campo de valor). Medir no golden dataset, ajustar critério/few-shot, subir MINOR.
- [ ] **9.6** Rodar com o Gemini, iterar prompts até atingir os limiares e registrar a tabela de resultados no README

## Fase 10 — Entrega
- [ ] **10.1** `examples/`: `curl.sh`, `python_client.py`, `library_usage.py`, `frontend/index.html`
- [ ] **10.2** README final (setup, arquitetura resumida, como rodar testes/evals, resultados dos evals, limitações)
- [ ] **10.3** (Opcional) `Dockerfile` + `docker-compose.yml`
- [ ] **10.4** Revisão final: `ruff`, `mypy`, cobertura ≥ 80% e checklist do PRD §7

---

## Apêndice A — Variáveis de ambiente (`.env.example`)

| Variável | Default | Descrição |
|---|---|---|
| `APP_ENV` | `dev` | `dev` \| `test` \| `prod` |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `console` | `json` em prod |
| `API_KEY` | vazio | se setada, exige o header `X-API-Key` |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8000` | |
| `DEBUG_ENDPOINTS` | `false` | habilita `/traces` e `include_debug` |
| `MAX_INPUT_CHARS` | `4000` | |
| `LLM_PROVIDER` | `gemini` | `gemini` \| `anthropic` \| `openai` \| `fake` |
| `LLM_FALLBACK_PROVIDERS` | vazio | ex.: `anthropic,openai` |
| `LLM_TIMEOUT_S` / `LLM_MAX_RETRIES` | `30` / `2` | |
| `GEMINI_API_KEY`, `GEMINI_MODEL_{FAST,BALANCED,POWERFUL}` | — / `gemini-3.5-flash-lite` / `gemini-3.7-flash` / `gemini-3.8-flash` | validados em 2026-10-14 (SPEC-006 §3.1) |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL_{FAST,BALANCED,POWERFUL}` | — | |
| `OPENAI_API_KEY`, `OPENAI_MODEL_{FAST,BALANCED,POWERFUL}` | — | |
| `ENABLE_REVIEW_STEP` | `true` | desligar para economizar quota |
| `INJECTION_LOW` / `INJECTION_HIGH` | `0.3` / `0.8` | |
| `INJECTION_JUDGE_ENABLED` | `true` | |
| `PII_REHYDRATE_REPLY` | `true` | |
| `KB_PATH` / `KB_TOP_K` / `KB_MIN_SCORE` / `KB_MAX_CHARS` | `data/knowledge_base` / `3` / `0.1` / `2000` | |
| `KB_MIN_RELATIVE_SCORE` / `KB_INCLUDE_UNREVIEWED` / `SLA_PATH` | `0.3` / `false` / `data/sla.yaml` | corte relativo; gerados só após revisão |
| `ROUTER_T1` / `ROUTER_T2` / `ROUTER_FORCE_TIER` | `0.35` / `0.7` / vazio | |
| `CONFIDENCE_MIN` | `0.6` | abaixo disso, `needs_human=true` |
| `OUTPUT_MAX_REPAIRS` | `1` | |
| `OBS_ENABLED` / `OBS_DB_PATH` / `OBS_STORE_RAW_INPUT` / `OBS_RETENTION_DAYS` | `true` / `var/traces.db` / `false` / `30` | |
| `EVAL_CONCURRENCY` / `EVAL_DELAY_S` | `2` / `1.0` | |

## Apêndice B — Riscos de execução

| Risco | Plano |
|---|---|
| Quota do Gemini acabar durante os evals | `ENABLE_REVIEW_STEP=false`, `EVAL_CONCURRENCY=1` e subconjunto `--sample 15` |
| Dependência sem wheel no Python 3.14 | Task 0.3: recriar o venv em 3.12/3.13 |
| Prompt não atingir a acurácia | Iterar os few-shots (adicionar casos de erro do relatório), ajustar `reasoning` e subir para `balanced` no classify como último recurso |
