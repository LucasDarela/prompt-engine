# SPEC-006 — Provedores LLM e Roteamento Dinâmico

> Módulos: `src/prompt_engine/llm/`, `src/prompt_engine/routing/` · Status: Rascunho
> Decisão: [ADR-0002](../adr/0002-abstracao-de-provedores-llm.md), [ADR-0005](../adr/0005-roteamento-heuristico-por-tiers.md)

## 1. Objetivo
1. Permitir **plugar provedores** sem tocar no core. **Implementado: Gemini.** Anthropic e OpenAI estão **adiados** (2026-10-17): a tabela abaixo descreve o mapeamento esperado, e o contrato e a suíte de testes de contrato já estão prontos para eles.
2. Escolher **tier e modelo** conforme a complexidade da tarefa, para economizar custo e quota.
3. Ser resiliente: timeout, retry com backoff e fallback entre provedores.

## 2. Contrato do provedor

```python
class Message(BaseModel):
    role: Literal["system", "user", "assistant"]; content: str

class LLMRequest(BaseModel):
    messages: list[Message]
    model: str
    temperature: float = 0.0
    max_output_tokens: int = 1024
    json_schema: dict[str, Any] | None = None   # se presente, pedir saída JSON nativa
    metadata: dict[str, str] = {}               # request_id, step — para logs

class Usage(BaseModel):
    input_tokens: int; output_tokens: int

class LLMResponse(BaseModel):
    text: str; model: str; provider: str; usage: Usage
    latency_ms: int; finish_reason: str; raw: dict[str, Any] | None = None  # raw só em debug

class LLMProvider(Protocol):
    name: str
    async def generate(self, request: LLMRequest) -> LLMResponse: ...
```

### Mapeamento por adapter

| Aspecto | Gemini (`google-genai`) | Anthropic (`anthropic`) | OpenAI (`openai`) | Fake |
|---|---|---|---|---|
| System prompt | `config.system_instruction` | parâmetro `system` | mensagem `role=system` (ou `instructions`) | — |
| Few-shots | `contents` com roles `user`/`model` | `messages` user/assistant | `messages` user/assistant | — |
| JSON nativo | `response_mime_type="application/json"` + `response_schema` | instrução no prompt + prefill/structured output quando disponível | `response_format={"type":"json_schema",...}` | retorna roteiro |
| Tokens | `usage_metadata` | `usage` | `usage` | contagem fake por palavras |
| Erros → domínio | 429 → `ProviderRateLimitError`; 5xx/timeout → `ProviderUnavailableError`; 400 → `ProviderBadRequestError` | idem | idem | configurável |

- Os SDKs são **extras opcionais** (`pip install -e ".[gemini]"`), importados **dentro** do adapter. Se o extra não estiver instalado e o provider estiver habilitado, o startup falha com uma mensagem clara.
- Os adapters **não** fazem retry por conta própria: isso é responsabilidade do `ProviderPool` (`llm/pool.py`). Os retries internos dos SDKs ficam desligados quando possível.

### `FakeProvider`
```python
FakeProvider(script: Mapping[str, str | Callable[[LLMRequest], str] | Exception])
# chave = metadata["step"] (ex.: "classify"); registra todas as requests em .calls
```
É usado em todos os testes offline, no modo demo (`LLM_PROVIDER=fake`) e para simular falhas e respostas inválidas.

## 3. Configuração de modelos por tier

```dotenv
LLM_PROVIDER=gemini                     # provider padrão
LLM_FALLBACK_PROVIDERS=anthropic,openai # ordem de fallback (só os com chave configurada)

GEMINI_API_KEY=
GEMINI_MODEL_FAST=gemini-3.5-flash-lite      # validados em 2026-10-14 com a chave do projeto (ver §3.1)
GEMINI_MODEL_BALANCED=gemini-3.7-flash
GEMINI_MODEL_POWERFUL=gemini-3.8-flash

ANTHROPIC_API_KEY=
ANTHROPIC_MODEL_FAST=claude-haiku-5-5
ANTHROPIC_MODEL_BALANCED=claude-sonnet-5-5
ANTHROPIC_MODEL_POWERFUL=claude-opus-5-5

OPENAI_API_KEY=
OPENAI_MODEL_FAST=
OPENAI_MODEL_BALANCED=
OPENAI_MODEL_POWERFUL=
```
### 3.1 Validação dos modelos Gemini (2026-10-14, plano gratuito)
Lista obtida com `client.models.list()` (SDK `google-genai`) e uma chamada mínima por modelo:

| Modelo | Resultado | Uso |
|---|---|---|
| `gemini-3.5-flash-lite` | ✅ OK | `fast`: classify, injection_judge, review, repair |
| `gemini-3.5-flash` | ❌ 503 persistente durante o dia todo ("high demand") | — (era o `balanced`) |
| `gemini-3.7-flash` | ✅ OK | `balanced` |
| `gemini-3.6-flash` | ✅ OK | alternativa |
| `gemini-3.8-flash` | ✅ OK | `powerful`: o Flash mais recente, já que não há Pro no plano gratuito |
| `gemini-2.5-pro` | ❌ 404, indisponível para novos usuários | — |
| `gemini-3.1-pro-preview` | ❌ 429, sem cota no plano gratuito | `powerful`, se houver billing ativo |

- **Família 1.5 descontinuada:** os modelos `gemini-1.5-*` não existem mais e não devem ser usados.
- **Sem aliases nem previews:** evite aliases `*-latest` e modelos `preview`. Eles mudam sem aviso e quebram a comparabilidade dos evals entre versões de prompt.
- **Re-listagem periódica:** a lista muda com frequência. Antes de cada release, rode de novo a listagem e as chamadas de teste.

`ProviderPool.resolve(tier, provider=None) -> Target(provider, model)`. Se o tier não estiver configurado para um provider, ele cai para o tier inferior configurado e emite um warning.

## 4. Resiliência (`llm/pool.py`)

```python
@dataclass(frozen=True)
class Target: provider: ProviderName; model: str; tier: Tier

class ProviderPool:
    def resolve(self, tier: Tier, provider: ProviderName | None = None) -> Target: ...
    async def generate(self, target: Target, request: LLMRequest) -> PoolResult: ...  # response + attempts
```
- O timeout é aplicado pelo pool (`asyncio.wait_for`), não pelo `LLMRequest`.
- `PoolResult.attempts` registra cada tentativa (`"gemini:rate_limited"`, `"gemini:ok"`) e vai para `StepResult.call_log`. Chamadas de reparo aparecem com prefixo `repair:`.
- **Timeout** por request: `LLM_TIMEOUT_S` (padrão 30).
- **Retry** (loop próprio com `sleep` injetável, determinístico nos testes): só para `ProviderRateLimitError` e `ProviderUnavailableError`. Até `LLM_MAX_RETRIES` (padrão 2), backoff exponencial com jitter (0.5s, 1s, 2s…). Respeita `retry_after` se vier.
- **Quota esgotada:** um 429 cujo `retryDelay` passa de **5 minutos** é tratado como quota esgotada (`quota_exhausted` no `call_log`): sem retry, direto para o próximo modelo/provider. Motivo (2026-10-17): o `gemini-3.7-flash` tem **20 requisições/dia** no plano gratuito (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) e devolveu `retryDelay: 29526s`; o pool esperava 30s e repetia, travando a rodada de evals. O limiar de 5 min existe porque o limite **por minuto** também devolve 429, com `retryDelay` de até ~60s: esse é esperado (teto de backoff = 60s) e repetido. Na 1ª versão o limiar era 30s e confundia os dois.
- **Fallback de modelo (mesmo provider)** — `LLM_MODEL_FALLBACK=true`: esgotados os retries, tenta os outros tiers do mesmo provider antes de trocar de provider. Ordem: `fast → balanced → powerful`; `balanced → powerful → fast`; `powerful → balanced` (primeiro sobe em qualidade, depois desce em custo). Modelos repetidos entre tiers não são tentados duas vezes. Motivo: com só a chave do Gemini, um 503 persistente num modelo derrubava a request (visto ao vivo em 2026-10-14) mesmo com outros modelos Gemini respondendo.
- **Fallback de provider:** depois, o próximo provider de `LLM_FALLBACK_PROVIDERS` **no mesmo tier**.
- `call_log` mostra `gemini[gemini-3.8-flash]:ok` quando a resposta veio de um fallback de modelo, para dar para auditar.
- `ProviderBadRequestError` **não** tem retry (é bug nosso).

## 5. Roteamento (`routing/`)

### `ComplexityScorer`
Score 0–1 a partir de sinais **determinísticos** e do resultado do `classify`:

| Sinal | Peso |
|---|---|
| `classify.complexity_hint == "complex"` | +0.35 |
| `len(intents) ≥ 2` | +0.15 |
| `urgency in {high, critical}` | +0.15 |
| `category in {legal, cancellation}` ou palavras-chave (procon, advogado, processo, chargeback) | +0.25 |
| tamanho do input > 800 chars | +0.10 |
| `sentiment == "very_negative"` | +0.10 |
| `security.verdict == "suspicious"` | +0.10 |
| nenhum snippet de KB encontrado | +0.10 |

Score limitado a [0, 1].

### `Router` (stage)
| Score | Tier |
|---|---|
| `< ROUTER_T1 (0.35)` | `fast` |
| `0.35 – 0.7` | `balanced` |
| `≥ ROUTER_T2 (0.7)` | `powerful` |

- `options.provider` da request sobrescreve o provider, mas não o tier.
- `ROUTER_FORCE_TIER` (env) força um tier, para testes e economia.
- Grava `RouteDecision(tier, provider, model, score, reasons=[...])`. As `reasons` listam os sinais que contribuíram, o que dá explicabilidade.

## 6. Critérios de aceitação
- [ ] **LLM-1** Cada adapter passa a mesma suíte de **contract tests** (com o SDK mockado): mapeamento de mensagens, JSON mode, usage e tradução de erros.
- [ ] **LLM-2** Trocar `LLM_PROVIDER` não exige mudança de código, e o fluxo completo roda com qualquer um (smoke `@live` por provider com chave).
- [ ] **LLM-3** 429 → retry com backoff → sucesso na 2ª tentativa (Fake com roteiro `[RateLimit, ok]`).
- [ ] **LLM-4** Gemini falha 3× → fallback para o próximo provider configurado; o trace registra as tentativas.
- [ ] **LLM-5** `BadRequest` não gera retry.
- [ ] **LLM-6** Provider habilitado sem extra instalado → erro claro no startup.
- [ ] **RT-1** Mensagem simples ("horário de atendimento?") → `fast`.
- [ ] **RT-2** Mensagem com ameaça jurídica + múltiplas intenções → `powerful`.
- [ ] **RT-3** `ROUTER_FORCE_TIER=fast` sempre retorna `fast`.
- [ ] **RT-4** `RouteDecision.reasons` não fica vazia quando o tier ≠ `fast`.
