# ADR-0003 — Prompts como dados versionados (YAML + Jinja2)

- **Status:** Proposto · **Data:** 2026-10-14

## Contexto
Os prompts são o principal ativo do sistema e mudam com mais frequência que o código. Precisam ser legíveis por quem não programa, versionados, reutilizáveis (parciais) e testáveis (snapshot e evals por versão).

## Decisão
Cada prompt é um arquivo **YAML** com metadados (`id`, `version` SemVer, `techniques`, `tier`, `params`, `output_schema`, `few_shots`) e corpo em **Jinja2** com `StrictUndefined`. As parciais ficam em `_partials/`. O JSON schema de saída é injetado a partir do modelo Pydantic, então é uma fonte única.

## Alternativas
- **f-strings em Python:** sem separação, sem metadados, difícil de revisar e versionar.
- **Markdown com front-matter:** bom para leitura, mas few-shots estruturados ficam frágeis.
- **Banco de dados / UI de prompts:** fora do escopo do MVP. O `PromptRegistry` é uma interface e pode ganhar um backend de DB depois.

## Consequências
- ✅ Diff legível em PR, snapshot por prompt, métricas por versão no trace.
- ✅ Validação no startup (falha rápido).
- ⚠️ Jinja2 é poderoso demais: a regra é **sem lógica de negócio no template**, só `include`, `for` simples, `join` e condicionais de presença.
