"""Helpers compartilhados entre suítes: monta LLMDeps reais com FakeProvider."""

from __future__ import annotations

from typing import Any

from prompt_engine.config import Settings
from prompt_engine.core.llm_step import LLMDeps
from prompt_engine.llm.fake import FakeProvider
from prompt_engine.llm.pool import ProviderPool
from prompt_engine.output.validator import OutputValidator
from prompt_engine.prompts.registry import PromptRegistry
from prompt_engine.prompts.renderer import PromptRenderer

REGISTRY = PromptRegistry.default()
RENDERER = PromptRenderer(REGISTRY)


def make_settings(**kw: Any) -> Settings:
    # obs_enabled=False: testes nunca gravam em var/traces.db (os de observabilidade usam tmp_path)
    base: dict[str, Any] = {"llm_provider": "fake", "llm_max_retries": 0, "obs_enabled": False}
    return Settings(_env_file=None, **(base | kw))  # type: ignore[call-arg]


def make_llm_deps(script: dict[str, Any], **settings_kw: Any) -> tuple[LLMDeps, FakeProvider]:
    fake = FakeProvider(script)
    cfg = make_settings(**settings_kw)
    deps = LLMDeps(
        registry=REGISTRY,
        renderer=RENDERER,
        pool=ProviderPool({"fake": fake}, cfg),
        validator=OutputValidator(max_repairs=cfg.output_max_repairs),
        settings=cfg,
    )
    return deps, fake


def make_app_deps(script: dict[str, Any], **settings_kw: Any) -> tuple[Any, FakeProvider]:
    """AppDeps completas (KB e SLA reais, prompts reais) com FakeProvider roteirizado."""
    from prompt_engine.flows.deps import build_deps

    fake = FakeProvider(script)
    deps = build_deps(make_settings(**settings_kw), provider_overrides={"fake": fake})
    return deps, fake
