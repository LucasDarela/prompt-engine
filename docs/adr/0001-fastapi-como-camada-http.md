# ADR-0001 — FastAPI como camada HTTP

- **Status:** Proposto · **Data:** 2026-10-14

## Contexto
O frontend (text area) precisa de um endpoint HTTP. O núcleo faz várias chamadas de rede (LLMs) por request, e o contrato de I/O precisa ser validado e documentado.

## Decisão
Usar **FastAPI + Uvicorn**, com a API como uma camada fina sobre o `FlowRunner`, e uma factory `create_app(settings)`.

## Alternativas
- **Flask:** sync por padrão, sem validação/OpenAPI nativos.
- **Django/DRF:** pesado demais para um middleware sem ORM.
- **Só biblioteca, sem API:** não atende o fluxo "frontend → API" pedido.

## Consequências
- ✅ Async nativo (chamadas LLM concorrentes no futuro), Pydantic compartilhado com o core, `/docs` automático, `TestClient` e `dependency_overrides` para testes.
- ⚠️ A lógica de negócio **não** pode vazar para as rotas. Isso é revisado em code review e garantido pela regra de dependência da ARCHITECTURE §7.
- A biblioteca continua utilizável sem a API (`examples/library_usage.py`).
