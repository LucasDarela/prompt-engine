# ADR-0002 — Abstração própria de provedores LLM (Gemini padrão, Anthropic e OpenAI plugáveis)

- **Status:** Proposto · **Data:** 2026-10-14

## Contexto
O plano gratuito do **Gemini** é o mais viável para desenvolvimento e testes. Mesmo assim, o sistema precisa permitir plugar **Anthropic** e **OpenAI** conforme a necessidade do usuário, sem que o provedor vire um bloqueio. Também precisamos de testes offline determinísticos.

## Decisão
Definir um protocolo próprio `LLMProvider` (Ports & Adapters) com `LLMRequest`/`LLMResponse` neutros, e um adapter por SDK oficial:
`GeminiProvider` (`google-genai`), `AnthropicProvider` (`anthropic`), `OpenAIProvider` (`openai`) e `FakeProvider`.
Os SDKs entram como extras opcionais. O provider padrão, os modelos por tier e a cadeia de fallback vêm da config.

## Alternativas
- **LangChain / LlamaIndex:** abstração pesada, muda rápido, esconde o prompt final (ruim para observabilidade e para a avaliação do capstone de "técnica").
- **LiteLLM:** boa opção de gateway, mas adiciona uma dependência grande e torna menos visível o mapeamento de features específicas (JSON schema do Gemini, `system` da Anthropic). Pode virar **mais um adapter** no futuro sem mudar o core.
- **Usar só um SDK:** viola o requisito de plugabilidade.

## Consequências
- ✅ Core independente de fornecedor; testes de contrato iguais para todos; fallback entre provedores.
- ✅ Controle total do prompt final (o que é enviado é exatamente o que é logado).
- ⚠️ Manter três adapters: o custo é mitigado pela suíte de contract tests compartilhada.
- ⚠️ Features exclusivas (ex.: caching explícito) ficam como extensões opcionais do adapter, nunca exigidas pelo core.
