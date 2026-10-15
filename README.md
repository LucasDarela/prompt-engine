# Prompt Engine — Triagem de Suporte com LLM

> Capstone Módulo 2 · Sistema de prompts **modular, testável e reutilizável**
> Status: 📄 **Documentação em revisão**, implementação ainda não iniciada

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
- 🔌 **Provedores plugáveis:** Gemini (padrão), Anthropic, OpenAI e Fake para testes
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

## Quickstart *(disponível após a Fase 0)*
```bash
source venv/bin/activate
pip install -e ".[dev,gemini]"
cp .env.example .env            # preencha GEMINI_API_KEY
pytest                          # testes offline
uvicorn main:app --reload       # http://localhost:8000/docs
```

```bash
curl -X POST http://localhost:8000/api/v1/process-prompt \
  -H "Content-Type: application/json" \
  -d '{"input": "Fui cobrado duas vezes no pedido PED-48213"}'
```
