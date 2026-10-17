# Prompt Engine — Triagem de Suporte com LLM

> Capstone Módulo 2 · Sistema de prompts **modular, testável e reutilizável**
> Status: ✅ **MVP + observabilidade + evals** (Fases 0–7 e 9): API ponta a ponta com Gemini · 560 testes · cobertura 100%

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
| [docs/DEMO.md](docs/DEMO.md) | Roteiro de demonstração de 5 minutos |
| [CLAUDE.md](CLAUDE.md) | Instruções para agentes de IA |

## Quickstart
```bash
source venv/bin/activate
pip install -e ".[dev,gemini]"
cp .env.example .env            # preencha GEMINI_API_KEY
make test                       # 560 testes offline, sem chave
make run                        # http://localhost:8000/docs
./examples/curl.sh              # 5 cenários de exemplo
```

**Frontend de teste:** com a API no ar, abra **http://localhost:8000/playground**. Tem cenários prontos (cobrança com CPF, pedido de desconto, app parado, NF-e, Procon, injection), mostra a triagem, a rota, os modelos, a PII mascarada e, com `DEBUG_ENDPOINTS=true`, o prompt que cada step enviou ao LLM e o trace.

**Sem chave ou sem quota:** `LLM_PROVIDER=fake DEBUG_ENDPOINTS=true make run` sobe em **modo demo**, com respostas fixas por step. O pipeline inteiro roda de verdade (PII, injection, roteamento, validação, traces); só o LLM é simulado.

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

## Evals contra o modelo real (2026-10-17)

| Métrica | Resultado | Meta | Rodada |
|---|---|---|---|
| JSON válido / sem reparo | 100% / 100% | 100% / ≥ 90% | ambas |
| Acurácia de categoria | 89,3% | ≥ 85% | classify 1.1.0 |
| Acurácia de urgência | 87,5% (100% com até 1 nível de diferença) | ≥ 75% | classify 1.1.0 |
| Recall de `needs_human` | 100% | ≥ 95% | ambas |
| Consistência (mesma categoria em 5 execuções) | 100% | ≥ 95% | classify 1.0.0 * |
| Prompt injection: recall / falso positivo (juiz real) | 100% / 0% | ≥ 90% / ≤ 5% | ambas |
| Vazamento de PII para o provedor | 0 | 0 | ambas |
| Qualidade da resposta (LLM-as-judge, 1 a 5) | 4,88 | ≥ 4,0 | classify 1.0.0 * |

\* Ainda não remedido no `classify` 1.1.0: a quota diária acabou antes dessas suítes.

Medido em 25 a 28 dos 43 casos do golden dataset: o plano gratuito do Gemini (500 req/dia no `flash-lite`, **20/dia** no `3.7-flash` e no `3.8-flash`) interrompeu o restante. Detalhes, metodologia e comando para completar: [SPEC-009 §8](docs/specs/SPEC-009-testing-evals.md) · relatórios em [docs/evals/](docs/evals/).

```bash
python -m prompt_engine.evals.run --sample 10    # ou: make eval
```

## Limitações conhecidas
- **Plano gratuito do Gemini:** 500 req/dia no `flash-lite` e **20/dia** no `3.7-flash` e no `3.8-flash`. Os tiers `balanced`/`powerful` atendem ~20 drafts/dia cada; depois disso o fallback cai para o `fast`. Para produção, é preciso um plano pago.
- **Evals parciais:** 25 a 28 dos 43 casos medidos; consistência e juiz ainda não remedidos no `classify` 1.1.0 (comando para completar em [SPEC-009 §8](docs/specs/SPEC-009-testing-evals.md)).
- **Golden dataset e ataques de injection escritos por Claude:** os rótulos foram auditados (2026-10-17); os ataques ainda pedem complemento por outra pessoa (plano 9.0b), porque foram escritos junto com os padrões de detecção.
- **Base de conhecimento em revisão:** 42 artigos aprovados; parte dos gerados do `chopp-hub` aguarda revisão, e os itens `manual-demo` são suposições de demonstração. Sem artigo para o assunto, a IA escala para humano em vez de inventar (por isso o escalonamento desnecessário fica alto).
- **Acentuação:** o Gemini omite acentos de forma intermitente em JSON mode (8 a 14% das respostas); é sinalizado com `missing_accents`, sem correção automática.
- **PII:** nomes próprios não são detectados (exigiriam NER).
- **Provedores:** só Gemini; os adapters da Anthropic e da OpenAI estão adiados (a arquitetura e a suíte de contrato estão prontas para eles).

## Observabilidade
- **Logs JSON** (`LOG_FORMAT=json`) com `request_id` em todas as linhas e filtro anti-PII em todos os campos.
- **Trace por request** em `var/traces.db` (SQLite): prompt final e versão de cada step, resposta bruta, tentativas (`call_log`), tokens, latência, rota, segurança. Sem PII. Ver com `DEBUG_ENDPOINTS=true`: `GET /api/v1/traces/{request_id}`.
- **Traces → dataset de teste:** `python -m prompt_engine.observability.export --since 2026-10-01 --out tests/fixtures/candidates.jsonl`.

**Problemas encontrados ao vivo e corrigidos** (veja `docs/plans/IMPLEMENTATION_PLAN.md` 6.6):
- o modelo afirmava ações não executadas ("encaminhei", "registramos") → regra no prompt + item no checklist do revisor + guarda determinística `unverified_action_claim`;
- promessas financeiras (prazo de estorno) → **política: nada financeiro é prometido pela IA**, com guarda `financial_promise`;
- falta de acentuação intermitente do Gemini → warning `missing_accents` (correção será medida nos evals, plano 9.0c).
