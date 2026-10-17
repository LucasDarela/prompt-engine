"""build_trace: PipelineRun -> Trace persistível, sem PII (SPEC-008 §2)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from prompt_engine.core.context import Message, PipelineContext, StepResult, Usage
from prompt_engine.core.tracing import PipelineRun, Span
from prompt_engine.llm.pricing import Pricing, load_pricing
from prompt_engine.observability.trace import build_trace

CPF = "529.982.247-25"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def step(name: str, model: str = "m-fast", out: int = 50) -> StepResult:
    return StepResult(
        name=name,
        prompt_id=f"support_triage.{name}",
        prompt_version="1.1.0",
        provider="gemini",
        model=model,
        rendered_messages=[Message(role="user", content="CPF [CPF_1]")],
        raw_text='{"ok": true}',
        parsed={"ok": True},
        usage=Usage(input_tokens=1000, output_tokens=out),
        latency_ms=120,
        attempts=2,
        call_log=["gemini:provider_unavailable", "gemini:ok"],
    )


def finished_ctx() -> PipelineContext:
    ctx = PipelineContext.new(flow="support_triage", raw_input=f"Meu CPF é {CPF}")
    placeholder = ctx.pii_vault.put("CPF", CPF)
    ctx = ctx.evolve(sanitized_input=f"Meu CPF é {placeholder}")
    ctx = ctx.with_step(step("classify")).with_step(step("draft_reply", "m-pro", 200))
    return ctx.evolve(
        result={
            "category": "billing",
            "suggested_reply": f"Confirmamos o CPF {CPF}.",
            "summary": "x",
        }
    )


def run_of(ctx: PipelineContext, **kw: object) -> PipelineRun:
    spans = [Span(name="classify", status="ok", started_at=NOW, duration_ms=120)]
    base: dict[str, object] = {
        "ctx": ctx,
        "spans": spans,
        "status": "ok",
        "started_at": NOW,
        "duration_ms": 300,
    }
    return PipelineRun(**(base | kw))  # type: ignore[arg-type]


def test_trace_has_calls_spans_and_totals() -> None:  # OBS-1, OBS-2
    trace = build_trace(run_of(finished_ctx()))
    assert trace.status == "ok"
    assert [c.step for c in trace.llm_calls] == ["classify", "draft_reply"]
    call = trace.llm_calls[0]
    assert (call.prompt_id, call.prompt_version, call.model) == (
        "support_triage.classify",
        "1.1.0",
        "m-fast",
    )
    assert call.messages[0].content == "CPF [CPF_1]"  # prompt final renderizado (mascarado)
    assert call.call_log == ["gemini:provider_unavailable", "gemini:ok"]
    assert trace.total_tokens == Usage(input_tokens=2000, output_tokens=250)
    assert trace.total_latency_ms == 300
    assert [s.name for s in trace.spans] == ["classify"]
    assert trace.pii_kinds == ["CPF"]


def test_trace_never_contains_raw_pii_by_default() -> None:  # OBS-3
    trace = build_trace(run_of(finished_ctx()))
    payload = trace.model_dump_json()
    assert CPF not in payload
    assert trace.input_raw is None
    assert trace.input_redacted == "Meu CPF é [CPF_1]"
    assert trace.output is not None
    assert trace.output["suggested_reply"] == "Confirmamos o CPF [CPF_1]."  # remascarado


def test_raw_input_only_when_explicitly_enabled() -> None:
    trace = build_trace(run_of(finished_ctx()), store_raw_input=True)
    assert trace.input_raw == f"Meu CPF é {CPF}"


def test_blocked_before_redaction_is_redacted_on_the_fly() -> None:
    ctx = PipelineContext.new(flow="support_triage", raw_input=f"x {CPF} " + "a" * 5000)
    trace = build_trace(
        run_of(
            ctx,
            status="blocked",
            error_code="invalid_input",
            error_details={"reason": "input_too_long"},
        )
    )
    assert CPF not in trace.model_dump_json()
    assert trace.input_redacted.startswith("x [CPF_1] ")
    assert (trace.status, trace.error_code) == ("blocked", "invalid_input")
    assert trace.error_details == {"reason": "input_too_long"}
    assert trace.output is None
    assert trace.llm_calls == []


def test_cost_estimate_with_known_prices() -> None:
    pricing = Pricing({"m-fast": (0.10, 0.40), "m-pro": (1.0, 4.0)})
    trace = build_trace(run_of(finished_ctx()), pricing=pricing)
    fast = 1000 * 0.10 / 1e6 + 50 * 0.40 / 1e6
    pro = 1000 * 1.0 / 1e6 + 200 * 4.0 / 1e6
    assert trace.llm_calls[0].cost_usd_estimate == pytest.approx(fast)
    assert trace.total_cost_usd_estimate == pytest.approx(fast + pro)


def test_cost_is_none_when_any_price_unknown() -> None:
    trace = build_trace(run_of(finished_ctx()), pricing=Pricing({"m-fast": (0.1, 0.4)}))
    assert trace.llm_calls[1].cost_usd_estimate is None
    assert trace.total_cost_usd_estimate is None


def test_load_pricing(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(
        "models:\n  m-fast: {input_per_1m: 0.1, output_per_1m: 0.4}\n", encoding="utf-8"
    )
    assert load_pricing(path).cost(
        "m-fast", Usage(input_tokens=10**6, output_tokens=0)
    ) == pytest.approx(0.1)
    empty = tmp_path / "empty.yaml"
    empty.write_text("models: {}\n", encoding="utf-8")
    assert load_pricing(empty).cost("m-fast", Usage()) is None


def test_default_pricing_file_loads() -> None:
    load_pricing()  # arquivo do pacote válido (mesmo que vazio)
