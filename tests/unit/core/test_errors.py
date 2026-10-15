import pytest

from prompt_engine.core import errors as e


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (e.InputValidationError("empty_input"), "invalid_input"),
        (e.PromptInjectionDetected(score=0.9), "unsafe_input"),
        (e.ProviderUnavailableError("gemini"), "provider_unavailable"),
        (e.ProviderRateLimitError("gemini", retry_after=3.0), "rate_limited"),
        (e.ProviderBadRequestError("gemini"), "provider_bad_request"),
        (e.ProviderConfigError("GEMINI_API_KEY ausente"), "provider_config"),
        (e.OutputValidationError("classify", ["x"]), "output_invalid"),
        (e.PromptNotFoundError("a.b@1.0.0"), "prompt_not_found"),
        (e.TemplateRenderError("a.b", "missing var"), "template_error"),
        (e.PromptLibraryError("x.yaml: tier"), "prompt_library_invalid"),
        (e.FlowNotFoundError("nope"), "flow_not_found"),
        (e.PipelineMisconfigured("route ausente"), "pipeline_misconfigured"),
    ],
)
def test_every_error_has_stable_code_and_base(exc: e.PromptEngineError, code: str) -> None:
    assert isinstance(exc, e.PromptEngineError)
    assert exc.code == code


def test_short_circuit_errors() -> None:
    assert isinstance(e.InputValidationError("x"), e.ShortCircuit)
    assert isinstance(e.PromptInjectionDetected(score=0.9), e.ShortCircuit)
    assert not isinstance(e.ProviderUnavailableError("gemini"), e.ShortCircuit)


def test_retryable_provider_errors() -> None:
    assert e.ProviderUnavailableError("gemini").retryable
    assert e.ProviderRateLimitError("gemini").retryable
    assert not e.ProviderBadRequestError("gemini").retryable


def test_error_details() -> None:
    assert e.InputValidationError("input_too_long").reason == "input_too_long"
    assert e.PromptInjectionDetected(score=0.92, signals=["ignore_instructions"]).signals == [
        "ignore_instructions"
    ]
    rl = e.ProviderRateLimitError("gemini", retry_after=2.5)
    assert rl.provider == "gemini"
    assert rl.retry_after == 2.5
    assert e.OutputValidationError("classify", ["campo x"]).errors == ["campo x"]


def test_public_message_never_contains_internal_details() -> None:
    exc = e.ProviderUnavailableError("gemini", detail="HTTP 503 at https://internal")
    assert "internal" not in exc.public_message
    assert "internal" in str(exc)
