"""LLMStep com a biblioteca REAL de prompts e o FakeProvider (SPEC-002 §4)."""

import json
from typing import Any, get_args

import pytest

from prompt_engine.config import Settings
from prompt_engine.core import errors as e
from prompt_engine.core.context import PipelineContext, RouteDecision, RunOptions
from prompt_engine.core.llm_step import LLMDeps, LLMStep
from prompt_engine.llm.fake import FakeProvider
from prompt_engine.llm.pool import ProviderPool
from prompt_engine.output.fallbacks import classify_fallback
from prompt_engine.output.schemas import Category
from prompt_engine.output.validator import OutputValidator
from prompt_engine.prompts.registry import PromptRegistry
from prompt_engine.prompts.renderer import PromptRenderer

VALID = {
    "reasoning": "Cobrança duplicada.",
    "category": "billing",
    "urgency": "high",
    "sentiment": "negative",
    "entities": {},
    "intents": ["refund"],
    "complexity_hint": "simple",
}
VALID_JSON = json.dumps(VALID)
CPF = "123.456.789-09"
REGISTRY = PromptRegistry.default()


def make_deps(script: dict[str, Any], **settings_kw: Any) -> tuple[LLMDeps, FakeProvider]:
    fake = FakeProvider(script)
    cfg = Settings(_env_file=None, llm_provider="fake", llm_max_retries=0, **settings_kw)  # type: ignore[call-arg]
    deps = LLMDeps(
        registry=REGISTRY,
        renderer=PromptRenderer(REGISTRY),
        pool=ProviderPool({"fake": fake}, cfg),
        validator=OutputValidator(max_repairs=cfg.output_max_repairs),
        settings=cfg,
    )
    return deps, fake


def classify_step(deps: LLMDeps, **kw: Any) -> LLMStep:
    params: dict[str, Any] = {
        "name": "classify",
        "prompt_ref": "support_triage.classify@latest",
        "variables": lambda c: {
            "message": c.sanitized_input,
            "categories": list(get_args(Category)),
        },
        "tier": "fast",
        "fallback": lambda c: classify_fallback(),
        "deps": deps,
    }
    return LLMStep(**(params | kw))


def make_ctx(**kw: Any) -> PipelineContext:
    ctx = PipelineContext.new(flow="support_triage", raw_input=f"Cobrado 2x, CPF {CPF}", **kw)
    placeholder = ctx.pii_vault.put("CPF", CPF)
    return ctx.evolve(sanitized_input=f"Cobrado 2x, CPF {placeholder}")


async def test_happy_path_records_step_result() -> None:
    deps, fake = make_deps({"classify": VALID_JSON})
    ctx = await classify_step(deps).run(make_ctx())
    step = ctx.step("classify")
    assert step.parsed == VALID
    assert (step.prompt_id, step.prompt_version) == ("support_triage.classify", "1.0.0")
    assert (step.provider, step.model) == ("fake", "fake-fast")
    assert step.attempts == 1
    assert not step.repaired and not step.fallback_used
    assert step.usage.output_tokens > 0
    (request,) = fake.calls
    assert request.metadata == {"step": "classify", "request_id": ctx.request_id}
    assert request.temperature == 0.0
    assert request.json_schema is not None
    assert ctx.canary and ctx.canary in request.messages[0].content
    assert "[CPF_1]" in request.messages[-1].content


async def test_canary_is_generated_per_context() -> None:
    assert make_ctx().canary != make_ctx().canary


async def test_pii_in_rendered_prompt_is_blocked() -> None:  # CORE-6 (defesa em profundidade)
    deps, fake = make_deps({"classify": VALID_JSON})
    leaky = classify_step(
        deps,
        variables=lambda c: {"message": c.raw_input, "categories": ["billing"]},
    )
    with pytest.raises(e.PipelineMisconfigured, match="PII"):
        await leaky.run(make_ctx())
    assert fake.calls == []


async def test_disabled_step_reports_not_enabled() -> None:  # CORE-4
    deps, _ = make_deps({})
    step = classify_step(deps, enabled=lambda c: c.options.enable_review is True)
    assert step.is_enabled(make_ctx()) is False
    assert step.is_enabled(make_ctx(options=RunOptions(enable_review=True))) is True


async def test_routed_without_route_is_misconfigured() -> None:  # CORE-5
    deps, _ = make_deps({"classify": VALID_JSON})
    with pytest.raises(e.PipelineMisconfigured, match="route"):
        await classify_step(deps, tier="routed").run(make_ctx())


async def test_routed_uses_route_decision() -> None:
    deps, fake = make_deps({"classify": VALID_JSON})
    route = RouteDecision(tier="powerful", provider="fake", model="fake-powerful", score=0.9)
    ctx = await classify_step(deps, tier="routed").run(make_ctx().evolve(route=route))
    assert fake.calls[0].model == "fake-powerful"
    assert ctx.step("classify").model == "fake-powerful"


async def test_invalid_output_is_repaired() -> None:
    deps, fake = make_deps({"classify": "não é json", "classify.repair": VALID_JSON})
    ctx = await classify_step(deps).run(make_ctx())
    step = ctx.step("classify")
    assert step.repaired is True
    assert step.parsed == VALID
    assert step.attempts == 2
    repair_request = fake.calls[1]
    assert "não é json" in repair_request.messages[-1].content
    # Uso soma a chamada original e a de reparo (Fake conta palavras da saída).
    assert step.usage.output_tokens == len(["não", "é", "json"]) + len(VALID_JSON.split())


async def test_repair_failure_uses_fallback_and_degrades() -> None:
    deps, _ = make_deps({"classify": "lixo", "classify.repair": "ainda lixo"})
    ctx = await classify_step(deps).run(make_ctx())
    step = ctx.step("classify")
    assert step.fallback_used is True
    assert step.parsed == classify_fallback().model_dump(mode="json")
    assert ctx.degraded is True
    assert "classify_fallback" in ctx.warnings


async def test_canary_leak_discards_output_without_repair() -> None:
    def leak(request: Any) -> str:
        canary = request.messages[0].content.split("token ")[1].split(".")[0]
        return json.dumps(VALID | {"reasoning": f"meu token é {canary}"})

    deps, fake = make_deps({"classify": leak})
    ctx = await classify_step(deps).run(make_ctx())
    assert ctx.security.leak_detected is True
    assert ctx.step("classify").fallback_used is True
    assert len(fake.calls) == 1
    assert "canary_leak" in ctx.warnings


async def test_provider_failure_propagates() -> None:
    deps, _ = make_deps({"classify": e.ProviderUnavailableError("fake")})
    with pytest.raises(e.ProviderUnavailableError):
        await classify_step(deps).run(make_ctx())


async def test_request_provider_override_is_used() -> None:
    deps, _ = make_deps({"classify": VALID_JSON})
    other = FakeProvider({"classify": VALID_JSON}, name="gemini")
    deps.pool.providers["gemini"] = other
    ctx = make_ctx(options=RunOptions(provider="gemini"))
    await classify_step(deps).run(ctx)
    assert len(other.calls) == 1


def test_schema_mismatch_is_detected_at_build_time() -> None:
    deps, _ = make_deps({})
    with pytest.raises(e.PipelineMisconfigured, match="output_schema"):
        LLMStep(
            name="x",
            prompt_ref="common.repair_json",
            variables=lambda c: {},
            tier="fast",
            fallback=lambda c: classify_fallback(),
            deps=deps,
        )
