import sys
from typing import Any

import pytest

from prompt_engine.config import Settings
from prompt_engine.core.errors import ProviderConfigError
from prompt_engine.llm.factory import build_pool
from prompt_engine.llm.fake import FakeProvider
from prompt_engine.llm.gemini import GeminiProvider


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_builds_default_and_fallback_providers() -> None:
    pool = build_pool(settings(gemini_api_key="k", llm_fallback_providers="fake"))
    assert isinstance(pool.providers["gemini"], GeminiProvider)
    assert isinstance(pool.providers["fake"], FakeProvider)


def test_fake_only_needs_no_key() -> None:
    pool = build_pool(settings(llm_provider="fake"))
    assert set(pool.providers) == {"fake"}


def test_overrides_replace_built_providers() -> None:
    custom = FakeProvider({}, name="gemini")
    pool = build_pool(settings(), overrides={"gemini": custom})
    assert pool.providers["gemini"] is custom


def test_missing_key_is_clear_config_error() -> None:
    with pytest.raises(ProviderConfigError, match="GEMINI_API_KEY"):
        build_pool(settings())


@pytest.mark.parametrize("name", ["anthropic", "openai"])
def test_not_yet_implemented_adapters_fail_clearly(name: str) -> None:
    with pytest.raises(ProviderConfigError, match="Fase 8"):
        build_pool(settings(llm_provider=name))


def test_missing_sdk_extra_is_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:  # LLM-6
    monkeypatch.setitem(sys.modules, "google.genai", None)
    with pytest.raises(ProviderConfigError, match=r"\[gemini\]"):
        build_pool(settings(gemini_api_key="k"))
