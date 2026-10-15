# Convenções de Código e Processo

## 1. Python
- Python ≥ 3.12. **Type hints em tudo**; `mypy --strict` em `src/`.
- `ruff` para lint e format (line-length 100). Regras: `E,F,W,I,B,UP,SIM,RUF,ASYNC,S` (S = bandit, para segurança).
- Pydantic v2 para todo dado que cruza fronteira de módulo. `dict` solto só dentro de função.
- `async` de ponta a ponta no caminho da request. I/O bloqueante (SQLite, leitura de arquivo em runtime) via `asyncio.to_thread`.
- Sem estado global mutável. Dependências entram por construtor (stages) ou `Depends` (API).
- Protocolos (`typing.Protocol`) para portas: `LLMProvider`, `KnowledgeBase`, `TraceStore`, `Stage`.
- Funções puras sempre que possível (cleaners, detectores, scorer). Isso as torna fáceis de testar.
- Docstrings curtas (1–3 linhas) em classes e funções públicas. Comentário só onde o "porquê" não é óbvio.

## 2. Nomes
| Item | Padrão | Exemplo |
|---|---|---|
| Módulos | `snake_case` | `input_validation.py` |
| Classes | `PascalCase` | `InjectionGuard` |
| Stages | substantivo da ação | `PIIRedaction`, `Router` |
| Prompt id | `<flow>.<nome>` | `support_triage.classify` |
| Env vars | `UPPER_SNAKE` com prefixo do domínio | `GEMINI_MODEL_FAST`, `OBS_DB_PATH` |
| Exceções | sufixo `Error`, exceto as de controle | `ProviderUnavailableError`, `PromptInjectionDetected` |
| Testes | `test_<comportamento>_<condição>` | `test_cpf_invalid_check_digit_is_not_masked` |

## 3. Erros
- Exceções de domínio herdam de `PromptEngineError` (`core/errors.py`), com `code: str` estável (é o que a API expõe).
- Nunca use `except Exception: pass`. Capturar amplamente só na borda (API handler e escrita de trace), sempre logando.
- Mensagens ao usuário em pt-BR e genéricas; o detalhe técnico fica no log.

## 4. Testes
- **TDD** por task do plano: o teste vermelho vem antes da implementação.
- Testes padrão **nunca** fazem rede. Qualquer chamada real exige `@pytest.mark.live`.
- Um arquivo de teste por módulo, espelhando `src/`.
- Fixtures compartilhadas em `tests/conftest.py`; datasets em `tests/fixtures/`.
- Snapshot só para prompts renderizados. Atualizar snapshot exige revisar o diff.

## 5. Prompts
Seguir [PROMPT_ENGINEERING.md](PROMPT_ENGINEERING.md). Mudou o comportamento? Suba a versão.

## 6. Git
- Branch `main` protegida; trabalho em `feat/<fase>-<assunto>`, `fix/…`, `docs/…`.
- **Conventional Commits:** `feat(guardrails): add CPF detector with check-digit validation`.
- Um commit por task do plano, quando possível. O commit só entra com `pytest`, `ruff` e `mypy` verdes.
- **Nunca** commitar `.env`, `var/`, `reports/` ou `venv/`.

## 7. Definition of Done (por task)
- [ ] Critérios de aceitação da spec correspondente atendidos e testados
- [ ] `pytest` verde (offline) e cobertura não caiu
- [ ] `ruff check`, `ruff format --check` e `mypy src` limpos
- [ ] Sem PII, segredo ou modelo hardcoded
- [ ] Docs/spec atualizadas se o contrato mudou
- [ ] Checkbox marcado no `IMPLEMENTATION_PLAN.md`
