# Prompt Engine — Triagem de Suporte com LLM

> Capstone Módulo 2 · Sistema de prompts **modular, testável e reutilizável**
> Status: ✅ **MVP funcional + observabilidade** (Fases 0–7): API ponta a ponta com Gemini · 522 testes · cobertura 100%

Um middleware em **Python + FastAPI** que recebe a mensagem livre de um cliente (text area), protege, enriquece, roteia, consulta uma LLM em várias etapas e devolve um **JSON de triagem limpo e validado**.

```
texto do cliente ─▶ validação ─▶ anti-injection ─▶ mascarar PII ─▶ classificar (LLM rápido)
                 ─▶ buscar contexto na KB ─▶ rotear modelo ─▶ redigir resposta (LLM roteado)
                 ─▶ revisar (self-critique) ─▶ validar JSON ─▶ reidratar PII ─▶ frontend
                                   (tudo registrado em traces para testes)
```

## Destaques
- 🛡️ **Guardrails:** detecção de prompt injection em 4 camadas, mascaramento de CPF/CNPJ/cartão/e-mail/telefone com validação de dígitos
- 🧩 **Biblioteca de prompts** em YAML + Jinja2, versionada (SemVer), com técnicas declaradas e snapshot tests
- 🔀 **Roteamento dinâmico** por complexidade (`fast` / `balanced` / `powerful`)
- 🔌 **Provedores plugáveis:** Gemini (implementado) e Fake para testes; a arquitetura aceita outros adapters (Anthropic/OpenAI adiados)
- ✅ **Saída sempre válida:** limpeza de markdown, extração de JSON, reparo automático e fallback seguro
- 🔭 **Observabilidade:** prompt final, resposta, modelo, versão, tokens, latência e custo por request, exportáveis como dataset de teste
- 🧪 **Testes offline + evals** com métricas (acurácia, consistência, recall de injection, vazamento de PII)

## Documentação
| Documento | Conteúdo |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Problema, escopo, contrato de saída, métricas |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Arquitetura, fluxo, estrutura de pastas, decisões |
| [docs/PROMPT_ENGINEERING.md](docs/PROMPT_ENGINEERING.md) | Padrão de prompts e catálogo de técnicas |
| [docs/specs/](docs/specs/) | Especificação de cada módulo com critérios de aceitação |
| [docs/adr/](docs/adr/) | Registros de decisão arquitetural |
| [docs/plans/IMPLEMENTATION_PLAN.md](docs/plans/IMPLEMENTATION_PLAN.md) | Plano em fases com checklist |
| [docs/CONVENTIONS.md](docs/CONVENTIONS.md) | Padrões de código, testes, git, DoD |
| [CLAUDE.md](CLAUDE.md) | Instruções para agentes de IA |

## Quickstart
```bash
source venv/bin/activate
pip install -e ".[dev,gemini]"
cp .env.example .env            # preencha GEMINI_API_KEY
make test                       # 522 testes offline, sem chave
make run                        # http://localhost:8000/docs
./examples/curl.sh              # 5 cenários de exemplo
```

Ou com Docker (lê o `.env`; traces persistem em `./var`):
```bash
docker compose up --build    # http://localhost:8000/docs
```

```bash
curl -X POST http://localhost:8000/api/v1/process-prompt \
  -H "Content-Type: application/json" \
  -d '{"input": "Fomos cobrados duas vezes na mensalidade de outubro."}'
```

## Resultados ao vivo (Gemini, 2026-10-14)

| Mensagem | Classificação | Rota (modelo do draft) | Resultado |
|---|---|---|---|
| Cobrança duplicada + CPF | billing / high | fast (`3.5-flash-lite`) | CPF mascarado antes do LLM; pede comprovantes; **sem prometer estorno** |
| Cobrança duplicada **pedindo desconto** | billing / high | fast | não oferece desconto; encaminha ao financeiro |
| App do motorista parado, 35 entregas | technical / critical | balanced (`3.7-flash`) | passos de diagnóstico da KB; "será encaminhada com urgência"; escalado |
| Procon + advogado + cancelamento | legal / critical | powerful (`3.8-flash`, score 1.0) | escalado (`category=legal`, `urgency=critical`) |
| Cancelamento **pedindo promoção** | cancellation / medium | fast | não oferece nada; especialista entrará em contato |
| `Ignore all previous instructions...` | — | — | **422 `unsafe_input`**, nenhuma chamada ao LLM |

Latência típica: ~1 s por step no `flash-lite`; o draft chega a ~7 s quando o Gemini devolve 503 e o retry com backoff entra em ação.

## Observabilidade
- **Logs JSON** (`LOG_FORMAT=json`) com `request_id` em todas as linhas e filtro anti-PII em todos os campos.
- **Trace por request** em `var/traces.db` (SQLite): prompt final e versão de cada step, resposta bruta, tentativas (`call_log`), tokens, latência, rota, segurança. Sem PII. Ver com `DEBUG_ENDPOINTS=true`: `GET /api/v1/traces/{request_id}`.
- **Traces → dataset de teste:** `python -m prompt_engine.observability.export --since 2026-10-01 --out tests/fixtures/candidates.jsonl`.

**Problemas encontrados ao vivo e corrigidos** (veja `docs/plans/IMPLEMENTATION_PLAN.md` 6.6):
- o modelo afirmava ações não executadas ("encaminhei", "registramos") → regra no prompt + item no checklist do revisor + guarda determinística `unverified_action_claim`;
- promessas financeiras (prazo de estorno) → **política: nada financeiro é prometido pela IA**, com guarda `financial_promise`;
- falta de acentuação intermitente do Gemini → warning `missing_accents` (correção será medida nos evals, plano 9.0c).
