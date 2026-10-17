"""Monta o ProviderPool a partir das Settings. Único lugar que conhece adapters concretos."""

from __future__ import annotations

from collections.abc import Mapping

from prompt_engine.config import ProviderName, Settings
from prompt_engine.core.errors import ProviderConfigError
from prompt_engine.llm.base import LLMProvider
from prompt_engine.llm.fake import FakeProvider, demo_script
from prompt_engine.llm.pool import ProviderPool


def _secret(value: object) -> str | None:
    getter = getattr(value, "get_secret_value", None)
    return getter() if callable(getter) else None


def build_provider(name: ProviderName, settings: Settings) -> LLMProvider:
    if name == "fake":
        return FakeProvider(demo_script())  # modo demo offline (respostas fixas por step)
    if name == "gemini":
        from prompt_engine.llm.gemini import GeminiProvider

        return GeminiProvider(
            api_key=_secret(settings.gemini_api_key), timeout_s=settings.llm_timeout_s
        )
    raise ProviderConfigError(f"adapter '{name}' não implementado (Fase 8 adiada; use gemini)")


def build_pool(
    settings: Settings, overrides: Mapping[str, LLMProvider] | None = None
) -> ProviderPool:
    """Provider padrão + fallbacks. `overrides` permite injetar providers (testes, demos)."""
    overrides = overrides or {}
    names: list[ProviderName] = [settings.llm_provider, *settings.llm_fallback_providers]
    providers: dict[str, LLMProvider] = {}
    for name in dict.fromkeys(names):  # sem duplicatas, mantendo a ordem
        providers[name] = overrides[name] if name in overrides else build_provider(name, settings)
    return ProviderPool(providers, settings)
