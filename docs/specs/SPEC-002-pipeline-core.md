# SPEC-002 — Core do Pipeline e Flows

> Módulos: `src/prompt_engine/core/`, `src/prompt_engine/flows/` · Status: Rascunho

## 1. Objetivo
Definir as abstrações que tornam o sistema **modular, testável e reutilizável**: `PipelineContext`, `Stage`, `Pipeline`, `LLMStep`, `FlowRegistry` e `FlowRunner`.

## 2. Interfaces

```python
# core/stage.py
class Stage(ABC):
    name: str
    def is_enabled(self, ctx: PipelineContext) -> bool: ...   # default True; False → span "skipped"
    @abstractmethod
    async def run(self, ctx: PipelineContext) -> PipelineContext: ...

# core/tracing.py — porta; a implementação persistente vive em observability/
class TraceSink(Protocol):
    async def record(self, run: PipelineRun) -> None: ...   # PipelineRun = ctx + spans + status

# core/pipeline.py
class Pipeline:
    def __init__(self, stages: Sequence[Stage], sink: TraceSink | None = None) -> None: ...
    async def run(self, ctx: PipelineContext) -> PipelineContext: ...

# core/llm_step.py
class LLMStep:
    """Stage genérico: resolve prompt → monta variáveis → renderiza → chama provider → valida saída."""
    def __init__(
        self,
        name: str,                                  # "classify"
        prompt_ref: str,                            # "support_triage.classify@1.0.0" ou "@latest"
        output_model: type[BaseModel],              # ClassifyOutput
        variables: Callable[[PipelineContext], dict[str, Any]],
        tier: Tier | Literal["routed"],             # "fast" fixo ou usa ctx.route
        registry: PromptRegistry,
        providers: ProviderPool,
        validator: OutputValidator,
        enabled: Callable[[PipelineContext], bool] = lambda _: True,
    ) -> None: ...

# flows/registry.py
class FlowRegistry:
    def register(self, name: str, builder: Callable[[Deps], Pipeline]) -> None: ...
    def build(self, name: str, deps: Deps) -> Pipeline: ...   # FlowNotFoundError

class FlowRunner:
    async def run(self, flow: str, input: str, *, customer_id: str | None, options: RunOptions) -> PipelineContext: ...
```

## 3. Comportamento do `Pipeline.run`

1. Para cada stage: se `is_enabled(ctx)` for falso, registra o span `skipped` e segue. Senão, cronometra e registra um `Span(name=stage.name)`.
2. Executa `ctx = await stage.run(ctx)`.
3. Se o stage lança uma `ShortCircuit` (subclasses: `InputValidationError`, `PromptInjectionDetected`), fecha o span com `status=blocked`, persiste o trace e **re-lança**. Os stages seguintes não rodam.
4. Exceções não-domínio: fecha o span com `status=error`, persiste o trace e re-lança.
5. No fim, entrega ao `TraceSink` com `status=ok` (ou `degraded` se `ctx.degraded`). Falha do sink é logada e **nunca** derruba a request.

**Invariantes:**
- Nenhum stage acessa `ctx.raw_input` depois de `PIIRedaction`, exceto `PIIRehydration`. Isso é garantido por teste.
- `LLMStep` só lê `ctx.sanitized_input`. Se for `None`, lança `PipelineMisconfigured`.
- Stages não fazem I/O fora do provider, da KB e do tracer injetados.

## 4. `LLMStep`: algoritmo
```
if not enabled(ctx): return ctx (registra span "skipped")
template = registry.get(prompt_ref)
vars     = variables(ctx) | template.static_vars
messages = renderer.render(template, vars)            # system + few-shots + user (spotlighted)
target   = ctx.route if tier == "routed" else providers.resolve(tier)
response = await providers.generate(target, LLMRequest(messages, json_schema=output_model, temperature=template.params.temperature, ...))
parsed   = await validator.validate(response.text, output_model, repair=lambda err: providers.generate(... repair prompt ...))
return ctx.with_step(name, StepResult(...))
```

## 5. Flow `support_triage`

```python
def build_support_triage(d: Deps) -> Pipeline:
    return Pipeline([
        InputValidation(d.settings),
        InjectionGuard(d.heuristics, d.judge, d.settings),
        PIIRedaction(d.pii_detectors),
        LLMStep("classify", "support_triage.classify@latest", ClassifyOutput,
                variables=lambda c: {"message": c.sanitized_input}, tier="fast", ...),
        ContextEnrichment(d.kb, d.business_rules),
        Router(d.complexity_scorer, d.settings),
        LLMStep("draft_reply", "support_triage.draft_reply@latest", DraftOutput,
                variables=draft_vars, tier="routed", ...),
        LLMStep("review_reply", "support_triage.review_reply@latest", ReviewOutput,
                variables=review_vars, tier="fast",
                enabled=lambda c: c.options.enable_review, ...),
        AssembleTriage(),       # junta classify + draft/review em TriageResult (valida)
        PIIRehydration(),
    ], tracer=d.tracer)
```

## 6. Reutilização
- `LLMStep` é genérico: um novo flow (ex.: `contract_summary`) reaproveita os guardrails, o roteador e o validator e só troca prompts e schemas.
- Os stages podem ser usados **sem a API**: `examples/library_usage.py` monta um `Pipeline` diretamente.

## 7. Critérios de aceitação
- [ ] **CORE-1** Um pipeline com stages fake executa na ordem e acumula o contexto.
- [ ] **CORE-2** `ShortCircuit` interrompe a execução: o stage seguinte não é chamado e o trace fica com `status=blocked`.
- [ ] **CORE-3** Cada stage gera exatamente 1 span com duração ≥ 0.
- [ ] **CORE-4** `LLMStep` com `enabled=False` não chama o provider e registra o span `skipped`.
- [ ] **CORE-5** `LLMStep` com `tier="routed"` e `ctx.route=None` lança `PipelineMisconfigured`.
- [ ] **CORE-6** Teste de invariante: com um spy no provider, nenhuma mensagem enviada contém substrings de `raw_input` que foram classificadas como PII.
- [ ] **CORE-7** `FlowRegistry.build("inexistente")` lança `FlowNotFoundError`.
- [ ] **CORE-8** O flow `support_triage` completo com FakeProvider produz um `TriageResult` válido.
