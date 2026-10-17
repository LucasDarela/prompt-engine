# CLAUDE.md — Guia para agentes de IA

Middleware de prompts em Python/FastAPI para **triagem de suporte**: guardrails → enriquecimento → roteamento → pipeline LLM multi-step → validação de saída → observabilidade. Provedor: Gemini (único adapter implementado). Adapters da Anthropic e da OpenAI estão **adiados** (Fase 8); não os implemente sem pedido.

## Leia antes de codar (nesta ordem)
1. `docs/PRD.md`: problema, escopo e métricas
2. `docs/ARCHITECTURE.md`: módulos, fluxo e regras de dependência
3. A spec do módulo que você vai tocar: `docs/specs/SPEC-00X-*.md`
4. `docs/plans/IMPLEMENTATION_PLAN.md`: qual é a próxima task (primeiro checkbox aberto)
5. `docs/CONVENTIONS.md` e, se for mexer em prompt, `docs/PROMPT_ENGINEERING.md`

## Status atual
Documentação aprovada. Implementação em andamento, fase por fase: o próximo item é o primeiro checkbox aberto em `docs/plans/IMPLEMENTATION_PLAN.md`.
Domínio: **Chopp Hub**, SaaS B2B para distribuidores de bebidas (persona em `prompts/library/_partials/system_base.j2`).

## Comandos (após a Fase 0)
```bash
source venv/bin/activate
pip install -e ".[dev,gemini]"
pytest                          # offline, sem chave — deve ficar sempre verde
pytest -m live --provider gemini   # evals contra o modelo real (gasta quota)
ruff check . && ruff format --check . && mypy src
uvicorn main:app --reload       # API em http://localhost:8000/docs
```

## Regras inegociáveis
- **TDD:** escreva o teste da task (IDs de critério da spec) antes da implementação.
- **Testes padrão nunca usam rede.** Use o `FakeProvider`. Chamada real só com `@pytest.mark.live`.
- **Nunca envie `ctx.raw_input` a um provider** nem o logue. LLMs só recebem `ctx.sanitized_input`.
- **Nada de modelo, chave ou limiar hardcoded.** Tudo vem de `Settings` (`src/prompt_engine/config.py`).
- **Prompts só em `src/prompt_engine/prompts/library/*.yaml`.** Nunca escreva texto de prompt em Python. Mudou o comportamento do prompt? Suba a `version` e revise o snapshot.
- **`core/` não importa adapters concretos** (`llm/gemini.py` etc.), só `llm/base.py`.
- **`api/` não tem lógica de negócio.**
- O JSON schema nos prompts vem do Pydantic (`output/schemas.py`). Não duplique schemas à mão.
- Saída inválida **nunca** sobe exceção para o usuário: reparo → fallback seguro com `needs_human=true`.
- **Nada financeiro é prometido pela IA** (desconto, promoção, estorno, reembolso, crédito, prazos de devolução), nem em few-shot, nem na KB. **A IA não afirma ações já executadas.** Ambas têm guarda em `flows/support_triage.py`.
- Um commit por task, em Conventional Commits, e marque o checkbox no plano.

## Mapa rápido
| Preciso mexer em... | Arquivo(s) | Spec |
|---|---|---|
| Endpoint HTTP | `src/prompt_engine/api/` | SPEC-001 |
| Ordem dos stages / novo flow | `src/prompt_engine/flows/`, `core/pipeline.py` | SPEC-002 |
| Injection / PII | `src/prompt_engine/guardrails/` | SPEC-003 |
| Base de conhecimento | `enrichment/`, `data/knowledge_base/` | SPEC-004 |
| Texto de prompt | `prompts/library/` | SPEC-005 |
| Provider / modelo / tier | `llm/`, `routing/`, `.env` | SPEC-006 |
| Parse/validação de JSON | `output/` | SPEC-007 |
| Logs / traces | `observability/` | SPEC-008 |
| Testes / evals / datasets | `tests/` | SPEC-009 |

## Armadilhas conhecidas
- O venv atual é **Python 3.14**. Se alguma dependência não instalar, recrie em 3.12/3.13 (Task 0.3).
- Os IDs de modelo dos provedores mudam: os do Gemini foram validados em 2026-10-14 (SPEC-006 §3.1). Não há modelo Pro no plano gratuito, e a família `gemini-1.5-*` foi descontinuada. Use o SDK `google-genai` (`from google import genai`), **não** o `google.generativeai`, que foi descontinuado.
- O Gemini retorna 503 em picos de demanda: o retry com backoff é obrigatório, não opcional.
- O plano gratuito do Gemini tem limite de requisições: os evals usam `EVAL_CONCURRENCY` baixo.
- O `.env` existe na raiz e **não** pode ser commitado.
- **Base de conhecimento:** nunca marque `reviewed: true` em `data/knowledge_base/generated/`. A revisão é humana (plano 5.8). Para regenerar: `python -m prompt_engine.enrichment.ingest --repo <clone do chopp-hub>`. Specs do produto podem conter detalhes internos e funcionalidades não lançadas.
- Docstrings dos modelos em `output/schemas.py` entram no JSON schema do prompt: mudá-las muda o prompt e quebra snapshots (atualize com `pytest --snapshot-update` e revise o diff).
