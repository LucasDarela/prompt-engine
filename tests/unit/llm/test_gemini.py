"""Mapeamento específico do adapter Gemini (SPEC-006 §2)."""

import httpx
import pytest

from prompt_engine.core.errors import ProviderRateLimitError, ProviderUnavailableError
from tests.unit.llm._support import GeminiHarness, gemini_error, gemini_response, make_request


async def test_maps_system_roles_and_config() -> None:
    h = GeminiHarness()
    h.respond("{}")
    await h.provider.generate(make_request(temperature=0.3, max_output_tokens=321))
    (call,) = h.models.calls
    assert call["model"] == "modelo-x"
    assert [c.role for c in call["contents"]] == ["user", "model", "user"]
    assert call["contents"][1].parts[0].text == '{"ok": true}'
    config = call["config"]
    assert config.system_instruction == "Você é um assistente."
    assert config.temperature == 0.3
    assert config.max_output_tokens == 321
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == {"type": "object"}


async def test_without_schema_no_json_mode() -> None:
    h = GeminiHarness()
    h.respond("texto livre")
    await h.provider.generate(make_request(json_schema=None))
    config = h.models.calls[0]["config"]
    assert config.response_mime_type is None
    assert config.response_json_schema is None


async def test_usage_and_finish_reason() -> None:
    h = GeminiHarness()
    h.models.outcome = gemini_response("{}", finish="MAX_TOKENS", prompt=120, out=30)
    response = await h.provider.generate(make_request())
    assert (response.usage.input_tokens, response.usage.output_tokens) == (120, 30)
    assert response.finish_reason == "MAX_TOKENS"


async def test_blocked_response_has_empty_text() -> None:
    h = GeminiHarness()
    h.models.outcome = gemini_response(None, finish="SAFETY")
    response = await h.provider.generate(make_request())
    assert response.text == ""
    assert response.finish_reason == "SAFETY"


async def test_rate_limit_reads_retry_delay() -> None:
    h = GeminiHarness()
    h.models.outcome = gemini_error(
        429, [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}]
    )
    with pytest.raises(ProviderRateLimitError) as info:
        await h.provider.generate(make_request())
    assert info.value.retry_after == 17.0


def test_requires_api_key() -> None:
    from prompt_engine.core.errors import ProviderConfigError
    from prompt_engine.llm.gemini import GeminiProvider

    with pytest.raises(ProviderConfigError, match="GEMINI_API_KEY"):
        GeminiProvider(api_key=None, timeout_s=30)


@pytest.mark.parametrize("delay", ["abcs", "17", None])
async def test_malformed_retry_delay_is_ignored(delay: object) -> None:
    h = GeminiHarness()
    h.models.outcome = gemini_error(429, [{"retryDelay": delay}] if delay else [{}])
    with pytest.raises(ProviderRateLimitError) as info:
        await h.provider.generate(make_request())
    assert info.value.retry_after is None


@pytest.mark.parametrize(
    ("exc", "detail"),
    [(httpx.ConnectError("sem rede"), "falha de rede"), (ValueError("?"), "inesperado")],
)
async def test_network_and_unexpected_errors_are_unavailable(exc: Exception, detail: str) -> None:
    h = GeminiHarness()
    h.models.outcome = exc
    with pytest.raises(ProviderUnavailableError, match=detail):
        await h.provider.generate(make_request())
