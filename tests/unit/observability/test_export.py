"""Export de traces -> candidatos a golden dataset (SPEC-008 §6)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from prompt_engine.core.context import PipelineContext
from prompt_engine.core.tracing import PipelineRun
from prompt_engine.evals.dataset import GoldenCase, load_cases
from prompt_engine.observability.export import export_candidates, main, trace_to_case
from prompt_engine.observability.store import SQLiteTraceStore
from prompt_engine.observability.trace import build_trace

RESULT = {
    "category": "billing",
    "urgency": "high",
    "sentiment": "negative",
    "summary": "s",
    "entities": {"order_id": "PED-1"},
    "suggested_reply": "Olá!",
    "needs_human": True,
    "confidence": 0.8,
    "escalation_reasons": ["draft_requested_human"],
}


def trace(
    request_id: str,
    *,
    status: str = "ok",
    result: dict[str, object] | None = RESULT,
    age_days: int = 0,
):  # type: ignore[no-untyped-def]
    ctx = PipelineContext.new(flow="support_triage", raw_input="x", request_id=request_id)
    ctx = ctx.evolve(sanitized_input=f"mensagem {request_id} do [CPF_1]", result=result)
    started = datetime.now(UTC) - timedelta(days=age_days)
    return build_trace(
        PipelineRun(ctx=ctx, spans=[], status=status, started_at=started, duration_ms=1)
    )  # type: ignore[arg-type]


def test_trace_to_case_prefills_expected_from_output() -> None:
    case = trace_to_case(trace("req_1"))
    assert case is not None
    assert case.id == "trace-req_1"
    assert case.input == "mensagem req_1 do [CPF_1]"  # input já mascarado
    assert case.expected.category == "billing"
    assert case.expected.urgency == "high"
    assert case.expected.needs_human is True
    assert case.expected.entities == {"order_id": "PED-1"}
    assert case.reviewed is False
    assert case.source == "trace:req_1"


def test_trace_without_output_is_skipped() -> None:
    assert trace_to_case(trace("req_x", status="blocked", result=None)) is None


def test_export_filters(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "t.db")
    for t in (trace("ok_new"), trace("ok_old", age_days=10), trace("deg", status="degraded")):
        store.save(t)
    cases = export_candidates(store, status="ok", since=datetime.now(UTC) - timedelta(days=1))
    assert [c.id for c in cases] == ["trace-ok_new"]


def test_cli_writes_valid_jsonl(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]  # OBS-6
    db = tmp_path / "t.db"
    store = SQLiteTraceStore(db)
    store.save(trace("a"))
    store.save(trace("b"))
    out = tmp_path / "candidates.jsonl"
    assert main(["--db", str(db), "--out", str(out), "--since", "2000-01-01"]) == 0
    cases = load_cases(out)
    assert {c.id for c in cases} == {"trace-a", "trace-b"}
    for line in out.read_text(encoding="utf-8").splitlines():
        GoldenCase.model_validate(json.loads(line))
    assert "2 candidatos" in capsys.readouterr().out
