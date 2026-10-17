"""ProviderPool: resolução de tier/modelo, retry com backoff, fallback e timeout (SPEC-006 §3-4)."""

import asyncio
from typing import Any

import pytest

from prompt_engine.config import Settings
from prompt_engine.core import errors as e
from prompt_engine.llm.base import LLMRequest, LLMResponse
from prompt_engine.llm.fake import FakeProvider
from prompt_engine.llm.pool import ProviderPool, Target
from tests.unit.llm._support import make_request


def settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"llm_provider": "gemini", "llm_max_retries": 2, "llm_timeout_s": 5}
    return Settings(_env_file=None, **(base | kw))  # type: ignore[call-arg]


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def named_fake(name: str, script: Any) -> FakeProvider:
    return FakeProvider({"classify": script}, name=name)


def pool(providers: dict[str, Any], **kw: Any) -> tuple[ProviderPool, Sleeps]:
    sleeps = Sleeps()
    return ProviderPool(providers, settings(**kw), sleep=sleeps, rng=lambda: 0.0), sleeps


# --- resolve -------------------------------------------------------------------


def test_resolve_uses_default_provider_and_tier_model() -> None:
    p, _ = pool({"gemini": named_fake("gemini", "ok")})
    assert p.resolve("fast") == Target("gemini", "gemini-3.5-flash-lite", "fast")
    assert p.resolve("powerful").model == "gemini-3.8-flash"


def test_resolve_falls_back_to_lower_configured_tier() -> None:
    p, _ = pool({"gemini": named_fake("gemini", "ok")}, gemini_model_powerful="")
    assert p.resolve("powerful") == Target("gemini", "gemini-3.7-flash", "balanced")


def test_resolve_explicit_provider_and_fake_models() -> None:
    p, _ = pool({"gemini": named_fake("gemini", "ok"), "fake": FakeProvider({})})
    assert p.resolve("balanced", provider="fake") == Target("fake", "fake-balanced", "balanced")


def test_resolve_unknown_provider_raises_config_error() -> None:
    p, _ = pool({"gemini": named_fake("gemini", "ok")})
    with pytest.raises(e.ProviderConfigError):
        p.resolve("fast", provider="openai")


def test_resolve_without_any_model_raises() -> None:
    p, _ = pool(
        {"gemini": named_fake("gemini", "ok")},
        gemini_model_fast="",
        gemini_model_balanced="",
        gemini_model_powerful="",
    )
    with pytest.raises(e.ProviderConfigError, match="modelo"):
        p.resolve("fast")


# --- generate ------------------------------------------------------------------


async def test_success_first_try() -> None:
    p, sleeps = pool({"gemini": named_fake("gemini", "ok")})
    result = await p.generate(p.resolve("fast"), make_request())
    assert result.response.text == "ok"
    assert result.response.model == "gemini-3.5-flash-lite"  # model vem do Target
    assert result.attempts == ["gemini:ok"]
    assert sleeps.delays == []


async def test_rate_limit_then_success_with_backoff() -> None:  # LLM-3
    p, sleeps = pool({"gemini": named_fake("gemini", [e.ProviderRateLimitError("gemini"), "ok"])})
    result = await p.generate(p.resolve("fast"), make_request())
    assert result.response.text == "ok"
    assert result.attempts == ["gemini:rate_limited", "gemini:ok"]
    assert sleeps.delays == [0.5]


async def test_retry_after_is_respected_and_capped() -> None:
    script = [
        e.ProviderRateLimitError("gemini", retry_after=7),
        e.ProviderRateLimitError("gemini", retry_after=30),  # no teto: ainda é espera curta
        "ok",
    ]
    p, sleeps = pool({"gemini": named_fake("gemini", script)})
    await p.generate(p.resolve("fast"), make_request())
    assert sleeps.delays == [7, 30]


async def test_exponential_backoff_on_unavailable() -> None:
    script = [e.ProviderUnavailableError("gemini"), e.ProviderUnavailableError("gemini"), "ok"]
    p, sleeps = pool({"gemini": named_fake("gemini", script)})
    await p.generate(p.resolve("fast"), make_request())
    assert sleeps.delays == [0.5, 1.0]


async def test_fallback_to_next_provider_same_tier() -> None:  # LLM-4
    down = [e.ProviderUnavailableError("gemini")] * 3
    p, _ = pool(
        {"gemini": named_fake("gemini", down), "fake": named_fake("fake", "do fallback")},
        llm_fallback_providers="fake",
        llm_model_fallback=False,
    )
    result = await p.generate(p.resolve("balanced"), make_request())
    assert result.response.text == "do fallback"
    assert result.response.model == "fake-balanced"
    assert result.attempts == ["gemini:provider_unavailable"] * 3 + ["fake:ok"]


async def test_all_providers_exhausted_raises_last_error() -> None:
    p, _ = pool(
        {"gemini": named_fake("gemini", [e.ProviderUnavailableError("gemini")] * 3)},
        llm_model_fallback=False,
    )
    with pytest.raises(e.ProviderUnavailableError):
        await p.generate(p.resolve("fast"), make_request())


async def test_bad_request_is_not_retried_nor_falls_back() -> None:  # LLM-5
    fallback = named_fake("fake", "não deveria")
    p, sleeps = pool(
        {"gemini": named_fake("gemini", [e.ProviderBadRequestError("gemini")]), "fake": fallback},
        llm_fallback_providers="fake",
    )
    with pytest.raises(e.ProviderBadRequestError):
        await p.generate(p.resolve("fast"), make_request())
    assert sleeps.delays == []
    assert fallback.calls == []


async def test_timeout_becomes_unavailable_and_is_retried() -> None:
    class Slow:
        name = "gemini"

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request: LLMRequest) -> LLMResponse:
            self.calls += 1
            await asyncio.sleep(10)
            raise AssertionError("não deveria chegar aqui")

    slow = Slow()
    p, _ = pool({"gemini": slow}, llm_timeout_s=0.01, llm_max_retries=1, llm_model_fallback=False)
    with pytest.raises(e.ProviderUnavailableError, match="timeout"):
        await p.generate(p.resolve("fast"), make_request())
    assert slow.calls == 2


async def test_fallback_list_skips_self_disabled_and_unconfigured() -> None:
    down = [e.ProviderUnavailableError("gemini")] * 3
    p, _ = pool(
        {"gemini": named_fake("gemini", down), "fake": named_fake("fake", "ok")},
        # gemini (o próprio), openai (não habilitado) e anthropic (sem modelo) são ignorados
        llm_fallback_providers="gemini,openai,anthropic,fake",
        llm_model_fallback=False,
    )
    p.providers["anthropic"] = named_fake("anthropic", "nunca")
    p.settings = p.settings.model_copy(
        update={"anthropic_model_fast": None, "anthropic_model_balanced": None}
    )
    result = await p.generate(p.resolve("balanced"), make_request())
    assert result.attempts[-1] == "fake:ok"


# --- fallback entre modelos do mesmo provider (plano 6.7) --------------------------------


def by_model(responses: dict[str, Any]) -> Any:
    """Roteiro que responde conforme o MODELO pedido (simula um modelo sobrecarregado)."""

    def respond(request: LLMRequest) -> str:
        outcome = responses[request.model]
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)

    return respond


DOWN = e.ProviderUnavailableError("gemini", "HTTP 503")


def gemini_by_model(responses: dict[str, Any]) -> FakeProvider:
    fake = FakeProvider({}, name="gemini")
    fake.script["classify"] = by_model(responses)
    return fake


async def test_overloaded_model_falls_back_to_other_gemini_tier() -> None:
    gemini = gemini_by_model({"gemini-3.7-flash": DOWN, "gemini-3.8-flash": "do powerful"})
    p, _ = pool({"gemini": gemini})
    result = await p.generate(p.resolve("balanced"), make_request())
    assert result.response.text == "do powerful"
    assert result.response.model == "gemini-3.8-flash"
    assert result.attempts == ["gemini:provider_unavailable"] * 3 + ["gemini[gemini-3.8-flash]:ok"]


async def test_model_fallback_order_balanced_then_powerful_then_fast() -> None:
    gemini = gemini_by_model(
        {"gemini-3.7-flash": DOWN, "gemini-3.8-flash": DOWN, "gemini-3.5-flash-lite": "do fast"}
    )
    p, _ = pool({"gemini": gemini}, llm_max_retries=0)
    result = await p.generate(p.resolve("balanced"), make_request())
    assert [c.model for c in gemini.calls] == [
        "gemini-3.7-flash",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
    ]
    assert result.response.text == "do fast"


@pytest.mark.parametrize(
    ("tier", "expected_order"),
    [
        ("fast", ["gemini-3.5-flash-lite", "gemini-3.7-flash"]),
        ("powerful", ["gemini-3.8-flash", "gemini-3.7-flash"]),
    ],
)
async def test_model_fallback_order_for_other_tiers(tier: Any, expected_order: list[str]) -> None:
    gemini = gemini_by_model({m: DOWN for m in expected_order})
    p, _ = pool({"gemini": gemini}, llm_max_retries=0)
    with pytest.raises(e.ProviderUnavailableError):
        await p.generate(p.resolve(tier), make_request())
    assert [c.model for c in gemini.calls] == expected_order


async def test_model_fallback_runs_before_other_providers() -> None:
    gemini = gemini_by_model({"gemini-3.7-flash": DOWN, "gemini-3.8-flash": "gemini ainda"})
    other = named_fake("fake", "outro provider")
    p, _ = pool({"gemini": gemini, "fake": other}, llm_fallback_providers="fake")
    result = await p.generate(p.resolve("balanced"), make_request())
    assert result.response.text == "gemini ainda"
    assert other.calls == []


async def test_model_fallback_can_be_disabled() -> None:
    gemini = gemini_by_model({"gemini-3.7-flash": DOWN, "gemini-3.8-flash": "não deveria"})
    p, _ = pool({"gemini": gemini}, llm_model_fallback=False)
    with pytest.raises(e.ProviderUnavailableError):
        await p.generate(p.resolve("balanced"), make_request())
    assert {c.model for c in gemini.calls} == {"gemini-3.7-flash"}


async def test_duplicate_models_across_tiers_are_not_retried_twice() -> None:
    gemini = gemini_by_model({"gemini-3.8-flash": DOWN})
    p, _ = pool(
        {"gemini": gemini},
        llm_max_retries=0,
        gemini_model_balanced="gemini-3.8-flash",
        gemini_model_fast="gemini-3.8-flash",
    )
    with pytest.raises(e.ProviderUnavailableError):
        await p.generate(p.resolve("balanced"), make_request())
    assert len(gemini.calls) == 1


async def test_exhausted_daily_quota_skips_retries_and_falls_back() -> None:
    # retryDelay de horas = quota do dia esgotada (ex.: 20 req/dia no gemini-3.7-flash free tier)
    exhausted = e.ProviderRateLimitError("gemini", "quota diária", retry_after=29526)
    gemini = gemini_by_model({"gemini-3.7-flash": exhausted, "gemini-3.8-flash": "do powerful"})
    p, sleeps = pool({"gemini": gemini})
    result = await p.generate(p.resolve("balanced"), make_request())
    assert result.response.text == "do powerful"
    assert result.attempts == ["gemini:quota_exhausted", "gemini[gemini-3.8-flash]:ok"]
    assert sleeps.delays == []  # não esperou nada: foi direto para o próximo modelo


async def test_short_retry_after_is_still_retried() -> None:
    script = [e.ProviderRateLimitError("gemini", retry_after=12), "ok"]
    p, sleeps = pool({"gemini": named_fake("gemini", script)})
    result = await p.generate(p.resolve("fast"), make_request())
    assert result.attempts == ["gemini:rate_limited", "gemini:ok"]
    assert sleeps.delays == [12]


async def test_all_models_exhausted_raises_rate_limit() -> None:
    exhausted = e.ProviderRateLimitError("gemini", retry_after=29526)
    p, _ = pool({"gemini": named_fake("gemini", [exhausted] * 3)})
    with pytest.raises(e.ProviderRateLimitError):
        await p.generate(p.resolve("balanced"), make_request())
