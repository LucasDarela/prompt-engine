# ADR-0005 — Roteamento heurístico por tiers, alimentado pelo step de classificação

- **Status:** Proposto · **Data:** 2026-10-14

## Contexto
Tarefas simples não justificam um modelo caro, e as analíticas não toleram um modelo fraco. O plano gratuito tem limites por modelo, então distribuir carga entre tiers também preserva quota.

## Decisão
- Três tiers abstratos: `fast`, `balanced`, `powerful`. Cada provider mapeia os tiers para modelos via env.
- O `classify` roda **sempre** no `fast` (é barato e já devolve `complexity_hint`, `intents`, `urgency`).
- O `ComplexityScorer` combina esses sinais com heurísticas determinísticas (palavras-chave jurídicas, tamanho, veredito de segurança, ausência de KB) e gera um score explicável (`reasons`).
- O `Router` decide o tier **do draft** por limiares configuráveis.

## Alternativas
- **Classificador de roteamento treinado (ML):** exige dados que ainda não temos. Os traces (SPEC-008) vão gerar esse dataset no futuro.
- **Sempre o modelo mais forte:** custo e quota inviáveis.
- **Roteamento só por heurística de texto, sem o classify:** perde a semântica e acerta menos em mensagens curtas e complexas.

## Consequências
- ✅ Explicável, testável e ajustável por env.
- ✅ O custo do classify é amortizado: ele serve a classificação, o roteamento e o enriquecimento.
- ⚠️ Os limiares precisam de calibração. Os evals reportam a acurácia por tier para ajustar.
