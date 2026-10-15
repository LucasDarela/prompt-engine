# CLAUDE.md — Guia para agentes de IA

Middleware de prompts em Python/FastAPI para **triagem de suporte**: guardrails → enriquecimento → roteamento → pipeline LLM multi-step → validação de saída → observabilidade. O provedor padrão é o Gemini; Anthropic e OpenAI são plugáveis.

## Leia antes de codar (nesta ordem)
1. `docs/PRD.md`: problema, escopo e métricas
2. `docs/ARCHITECTURE.md`: módulos, fluxo e regras de dependência
3. A spec do módulo que você vai tocar: `docs/specs/SPEC-00X-*.md`
4. `docs/plans/IMPLEMENTATION_PLAN.md`: qual é a próxima task (primeiro checkbox aberto)
5. `docs/CONVENTIONS.md` e, se for mexer em prompt, `docs/PROMPT_ENGINEERING.md`

## Status atual
**Fase de documentação.** Nenhum código deve ser escrito até o usuário aprovar os docs. Depois da aprovação, siga o plano fase por fase.

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
