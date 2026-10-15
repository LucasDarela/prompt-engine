# ADR-0004 — FakeProvider e separação entre testes determinísticos e evals

- **Status:** Proposto · **Data:** 2026-10-14

## Contexto
As saídas de LLM são estocásticas, custam quota e dependem de rede. Um teste que chama o modelo real é lento, instável e consome o plano gratuito. Mesmo assim, precisamos medir a qualidade real do modelo.

## Decisão
1. **Suíte padrão 100% offline** com `FakeProvider` roteirizado por step (respostas válidas, quebradas, com markdown, exceções). Testa o **código**: pipeline, guardrails, validação, fallback e API.
2. **Evals separados** (`@pytest.mark.live`), contra o provider real, com datasets rotulados e limiares numéricos. Testam o **comportamento do modelo e do prompt**.
3. **Snapshot tests** dos prompts renderizados fazem a ponte: garantem que o que vai para o modelo não muda sem revisão.

## Consequências
- ✅ `pytest` roda em segundos, sem chave, de forma reproduzível.
- ✅ Evals produzem relatórios comparáveis por versão de prompt.
- ⚠️ O Fake pode divergir do real: mitigado pelos contract tests dos adapters e pelos evals antes de cada mudança MINOR de prompt.
