# SPEC-001 — API HTTP

> Módulo: `src/prompt_engine/api/` · Depende de: SPEC-002, SPEC-008 · Status: Rascunho

## 1. Objetivo
Expor o pipeline via HTTP como uma camada **fina**: validar o payload, chamar o `FlowRunner` e traduzir o resultado ou as exceções para um envelope JSON estável.

## 2. Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Liveness: `{ "status": "ok", "version": "0.1.0" }` |
| `GET` | `/health/ready` | Readiness: registry de prompts carregado, provider configurado, store acessível |
| `POST` | `/api/v1/process-prompt` | Executa um flow (padrão `support_triage`) |
| `GET` | `/api/v1/prompts` | Lista os prompts da biblioteca (`id`, `version`, `description`, `techniques`) |
| `GET` | `/api/v1/prompts/{prompt_id}` | Metadados e template (sem renderizar) de um prompt |
| `GET` | `/api/v1/traces/{request_id}` | Trace de uma request. **Habilitado só se `DEBUG_ENDPOINTS=true`** |

## 3. `POST /api/v1/process-prompt`

### Request
```json
{
  "input": "Fui cobrado duas vezes no pedido PED-48213, meu CPF é 123.456.789-09",
  "flow": "support_triage",
  "customer_id": "cus_123",
  "options": {
    "provider": "gemini",
    "enable_review": true,
    "include_debug": false
  }
}
```

| Campo | Tipo | Obrigatório | Regras |
|---|---|---|---|
| `input` | string | sim | 1..`MAX_INPUT_CHARS` (4000) após `strip()` |
| `flow` | string | não | default `support_triage`; precisa existir no `FlowRegistry` |
| `customer_id` | string | não | `^[A-Za-z0-9_-]{1,64}$` |
| `options.provider` | enum | não | `gemini` \| `anthropic` \| `openai` \| `fake`; precisa estar habilitado |
| `options.enable_review` | bool | não | default `settings.ENABLE_REVIEW_STEP` |
| `options.include_debug` | bool | não | só tem efeito com `DEBUG_ENDPOINTS=true`; anexa os prompts renderizados |

### Response 200
Ver o contrato completo em [PRD §5](../PRD.md#5-saída-esperada-contrato-com-o-frontend). Header `X-Request-ID` sempre presente.

### Erros
| HTTP | `error.code` | Quando |
|---|---|---|
| 401 | `unauthorized` | `API_KEY` configurada e header `X-API-Key` ausente ou inválido |
| 422 | `invalid_payload` | Falha de schema do FastAPI/Pydantic |
| 422 | `invalid_input` | `InputValidationError` (vazio, muito longo, só símbolos) |
| 422 | `unsafe_input` | `PromptInjectionDetected` |
| 404 | `flow_not_found` | `flow` inexistente |
| 429 | `rate_limited` | `ProviderRateLimitError` (com header `Retry-After` se houver) |
| 503 | `provider_unavailable` | Provedores esgotados |
| 500 | `internal_error` | Qualquer outra exceção; mensagem genérica, detalhe só no log |

Envelope de erro:
```json
{ "request_id": "req_...", "status": "error", "error": { "code": "unsafe_input", "message": "A mensagem não pôde ser processada com segurança." } }
```

## 4. Comportamento
- **Request ID:** aceita `X-Request-ID` do cliente se for válido; senão gera `req_<ulid>`. É propagado em logs, traces e na resposta.
- **CORS:** origens vindas de `CORS_ORIGINS` (lista separada por vírgula). Default: `http://localhost:*` para dev.
- **Dependências:** `FlowRunner`, `Settings` e `TraceStore` são injetados via `Depends`. Os testes sobrescrevem com `app.dependency_overrides`.
- **Factory:** `create_app(settings: Settings | None = None) -> FastAPI`. O `main.py` faz `app = create_app()`.
- **Lifespan:** carrega o `PromptRegistry` e a KB no startup (falha rápido se algum YAML for inválido).

## 5. Critérios de aceitação
- [ ] **API-1** `POST /process-prompt` com input válido e `FakeProvider` retorna 200 e um body que valida contra `ProcessPromptResponse`.
- [ ] **API-2** Toda resposta (sucesso ou erro) contém `request_id` e o header `X-Request-ID` com o mesmo valor.
- [ ] **API-3** Input com injection explícita retorna 422 `unsafe_input` **sem** nenhuma chamada ao provider (verificado pelo contador do Fake).
- [ ] **API-4** Input > `MAX_INPUT_CHARS` retorna 422 `invalid_input`.
- [ ] **API-5** Com `API_KEY` setada, uma request sem header retorna 401.
- [ ] **API-6** `/api/v1/traces/{id}` retorna 404 quando `DEBUG_ENDPOINTS=false`.
- [ ] **API-7** Uma exceção inesperada vira 500 com mensagem genérica, sem stack trace no body.
- [ ] **API-8** O OpenAPI (`/docs`) documenta todos os schemas e exemplos.

## 6. Testes
`tests/integration/test_api.py`: usa `TestClient` + `dependency_overrides` com o FakeProvider e cobre um caso por critério acima.
