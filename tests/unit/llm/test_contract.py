"""LLM-1: todo adapter cumpre o mesmo contrato de LLMProvider."""

import pytest

from prompt_engine.core import errors as e
from prompt_engine.llm.base import LLMResponse
from tests.unit.llm._support import FailureKind, Harness, make_request


async def test_returns_normalized_response(harness: Harness) -> None:
    harness.respond('{"ok": true}')
    response = await harness.provider.generate(make_request())
    assert isinstance(response, LLMResponse)
    assert response.text == '{"ok": true}'
    assert response.provider == harness.provider.name
    assert response.model == "modelo-x"
    assert response.usage.input_tokens >= 0
    assert response.usage.output_tokens >= 0
    assert response.latency_ms >= 0
    assert response.finish_reason


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("rate_limit", e.ProviderRateLimitError),
        ("unavailable", e.ProviderUnavailableError),
        ("timeout", e.ProviderUnavailableError),
        ("bad_request", e.ProviderBadRequestError),
    ],
)
async def test_errors_are_translated_to_domain(
    harness: Harness, kind: FailureKind, expected: type[Exception]
) -> None:
    harness.fail(kind)
    with pytest.raises(expected) as info:
        await harness.provider.generate(make_request())
    assert info.value.provider == harness.provider.name  # type: ignore[attr-defined]
