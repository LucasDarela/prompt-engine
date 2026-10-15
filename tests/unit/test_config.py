from pathlib import Path

import pytest
from pydantic import ValidationError

from prompt_engine.config import Settings


def make(**overrides: object) -> Settings:
    """Settings isolado: ignora o .env real e variáveis do ambiente do dev."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LLM_PROVIDER", "GEMINI_API_KEY", "API_KEY", "CORS_ORIGINS", "ROUTER_FORCE_TIER"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_match_documented_values() -> None:
    s = make()
    assert s.llm_provider == "gemini"
    assert s.max_input_chars == 4000
    assert s.gemini_model_fast == "gemini-3.5-flash-lite"
    assert s.gemini_model_balanced == "gemini-3.7-flash"
    assert s.gemini_model_powerful == "gemini-3.8-flash"
    assert s.injection_low == 0.3
    assert s.injection_high == 0.8
    assert s.enable_review_step is True
    assert s.obs_store_raw_input is False
    assert s.debug_endpoints is False
    assert s.obs_db_path == Path("var/traces.db")


def test_reads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    s = make()
    assert s.llm_provider == "anthropic"
    assert s.gemini_api_key is not None
    assert s.gemini_api_key.get_secret_value() == "fake-key"


def test_api_keys_are_not_exposed_in_repr() -> None:
    s = make(gemini_api_key="super-secret")
    assert "super-secret" not in repr(s)


def test_csv_lists_are_parsed() -> None:
    s = make(cors_origins="http://a.com, http://b.com", llm_fallback_providers="anthropic,openai")
    assert s.cors_origins == ["http://a.com", "http://b.com"]
    assert s.llm_fallback_providers == ["anthropic", "openai"]


def test_empty_csv_becomes_empty_list() -> None:
    assert make(llm_fallback_providers="").llm_fallback_providers == []


def test_empty_optional_values_become_none() -> None:
    s = make(api_key="", gemini_api_key="", openai_model_fast="", router_force_tier="")
    assert s.api_key is None
    assert s.gemini_api_key is None
    assert s.openai_model_fast is None
    assert s.router_force_tier is None


def test_env_example_loads_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings(_env_file=Path(__file__).parents[2] / ".env.example")  # type: ignore[call-arg]
    assert s.api_key is None
    assert s.llm_fallback_providers == []
    assert s.gemini_model_fast == "gemini-3.5-flash-lite"


def test_invalid_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make(llm_provider="mistral")


def test_injection_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        make(injection_low=0.9, injection_high=0.5)


def test_router_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        make(router_t1=0.8, router_t2=0.4)


def test_model_for_tier() -> None:
    s = make()
    assert s.model_for("gemini", "fast") == "gemini-3.5-flash-lite"
    assert s.model_for("gemini", "powerful") == "gemini-3.8-flash"


def test_model_for_unconfigured_tier_returns_none() -> None:
    assert make(openai_model_powerful=None).model_for("openai", "powerful") is None
