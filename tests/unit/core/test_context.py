import pytest
from pydantic import ValidationError

from prompt_engine.core.context import PipelineContext, StepResult, Usage
from prompt_engine.core.vault import PIIVault

CPF = "123.456.789-09"


def make_ctx(**kw: object) -> PipelineContext:
    return PipelineContext.new(flow="support_triage", raw_input="Olá, meu CPF é " + CPF, **kw)  # type: ignore[arg-type]


def make_step(name: str = "classify") -> StepResult:
    return StepResult(
        name=name,
        prompt_id="support_triage.classify",
        prompt_version="1.0.0",
        provider="fake",
        model="fake-fast",
        raw_text="{}",
        parsed={"category": "billing"},
        usage=Usage(input_tokens=10, output_tokens=5),
        latency_ms=12,
    )


# --- PipelineContext ---------------------------------------------------------


def test_new_generates_request_id() -> None:
    a, b = make_ctx(), make_ctx()
    assert a.request_id.startswith("req_")
    assert a.request_id != b.request_id


def test_new_accepts_explicit_request_id() -> None:
    assert make_ctx(request_id="req_abc").request_id == "req_abc"


def test_context_is_immutable() -> None:
    ctx = make_ctx()
    with pytest.raises(ValidationError):
        ctx.sanitized_input = "x"  # type: ignore[misc]


def test_with_step_returns_new_context() -> None:
    ctx = make_ctx()
    new = ctx.with_step(make_step())
    assert "classify" not in ctx.steps
    assert new.steps["classify"].parsed == {"category": "billing"}
    assert new.request_id == ctx.request_id


def test_evolve_updates_fields() -> None:
    ctx = make_ctx().evolve(sanitized_input="Olá, meu CPF é [CPF_1]")
    assert ctx.sanitized_input == "Olá, meu CPF é [CPF_1]"


def test_with_warning_appends_without_mutating() -> None:
    ctx = make_ctx()
    new = ctx.with_warning("review_unavailable")
    assert ctx.warnings == []
    assert new.warnings == ["review_unavailable"]


def test_parsed_step_helper() -> None:
    ctx = make_ctx().with_step(make_step())
    assert ctx.step("classify").parsed == {"category": "billing"}
    assert ctx.get_step("draft_reply") is None


def test_missing_step_raises_misconfigured() -> None:
    from prompt_engine.core.errors import PipelineMisconfigured

    with pytest.raises(PipelineMisconfigured):
        make_ctx().step("classify")


def test_raw_input_and_vault_never_serialized() -> None:
    ctx = make_ctx()
    ctx.pii_vault.put("CPF", CPF)
    dumped = ctx.model_dump_json()
    assert CPF not in dumped
    assert "raw_input" not in dumped
    assert CPF not in repr(ctx)


def test_total_usage_sums_steps() -> None:
    ctx = make_ctx().with_step(make_step("classify")).with_step(make_step("draft_reply"))
    assert ctx.total_usage() == Usage(input_tokens=20, output_tokens=10)


# --- PIIVault ----------------------------------------------------------------


def test_vault_assigns_typed_sequential_placeholders() -> None:
    v = PIIVault()
    assert v.put("CPF", CPF) == "[CPF_1]"
    assert v.put("EMAIL", "a@b.com") == "[EMAIL_1]"
    assert v.put("CPF", "987.654.321-00") == "[CPF_2]"


def test_vault_same_value_same_placeholder() -> None:
    v = PIIVault()
    assert v.put("EMAIL", "a@b.com") == v.put("EMAIL", "a@b.com")


def test_vault_restore_known_and_drop_unknown_placeholders() -> None:
    v = PIIVault()
    v.put("CPF", CPF)
    restored, unknown = v.restore("CPF [CPF_1] e [CPF_9]")
    assert restored == f"CPF {CPF} e "
    assert unknown == ["[CPF_9]"]


def test_vault_kinds_and_contains() -> None:
    v = PIIVault()
    v.put("CPF", CPF)
    v.put("CARD", "4111111111111111")
    assert v.kinds() == ["CARD", "CPF"]
    assert v.contains_value_in(f"vazou {CPF}!")
    assert not v.contains_value_in("[CPF_1]")


def test_vault_repr_hides_values() -> None:
    v = PIIVault()
    v.put("CPF", CPF)
    assert CPF not in repr(v)
    assert CPF not in str(v)
