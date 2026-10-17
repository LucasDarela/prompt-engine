"""SQLiteTraceStore + StoreTraceSink (SPEC-008 §3)."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prompt_engine.core.context import PipelineContext
from prompt_engine.core.tracing import PipelineRun
from prompt_engine.observability.store import SQLiteTraceStore, StoreTraceSink
from prompt_engine.observability.trace import Trace, build_trace


def make_trace(
    request_id: str, *, status: str = "ok", flow: str = "support_triage", age_days: int = 0
) -> Trace:
    ctx = PipelineContext.new(flow=flow, raw_input="oi", request_id=request_id).evolve(
        sanitized_input="oi", result={"category": "billing", "suggested_reply": "Olá!"}
    )
    started = datetime.now(UTC) - timedelta(days=age_days)
    run = PipelineRun(ctx=ctx, spans=[], status=status, started_at=started, duration_ms=10)  # type: ignore[arg-type]
    return build_trace(run)


def test_save_and_get_roundtrip(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "sub" / "traces.db")  # cria diretórios
    trace = make_trace("req_1")
    store.save(trace)
    assert store.get("req_1") == trace
    assert store.get("req_inexistente") is None


def test_save_is_idempotent_per_request_id(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "t.db")
    store.save(make_trace("req_1"))
    store.save(make_trace("req_1", status="degraded"))
    assert store.get("req_1").status == "degraded"  # type: ignore[union-attr]
    assert len(store.list()) == 1


def test_list_filters_and_orders_newest_first(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "t.db")
    store.save(make_trace("old", age_days=3))
    store.save(make_trace("new"))
    store.save(make_trace("blocked", status="blocked"))
    store.save(make_trace("other", flow="outro_flow"))
    assert [t.request_id for t in store.list(status="ok", flow="support_triage")] == ["new", "old"]
    since = datetime.now(UTC) - timedelta(days=1)
    assert {t.request_id for t in store.list(since=since)} == {"new", "blocked", "other"}
    assert len(store.list(limit=2)) == 2


def test_purge_removes_old_traces(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "t.db")
    store.save(make_trace("old", age_days=40))
    store.save(make_trace("new"))
    assert store.purge(older_than_days=30) == 1
    assert [t.request_id for t in store.list()] == ["new"]


def test_denormalized_columns_are_queryable(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "t.db")
    store.save(make_trace("req_1"))
    # `with sqlite3.connect()` não fecha a conexão (só faz commit): closing() fecha.
    with closing(sqlite3.connect(tmp_path / "t.db")) as conn:
        row = conn.execute("SELECT flow, status, category FROM traces").fetchone()
    assert row == ("support_triage", "ok", "billing")


async def test_sink_records_runs(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "t.db")
    ctx = PipelineContext.new(flow="f", raw_input="oi", request_id="req_x").evolve(
        sanitized_input="oi"
    )
    run = PipelineRun(ctx=ctx, spans=[], status="ok", started_at=datetime.now(UTC), duration_ms=1)
    await StoreTraceSink(store).record(run)
    assert store.get("req_x") is not None


async def test_sink_swallows_store_failures(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:  # OBS-4
    class BrokenStore(SQLiteTraceStore):
        def save(self, trace: Trace) -> None:
            raise sqlite3.OperationalError("disk I/O error")

    ctx = PipelineContext.new(flow="f", raw_input="oi").evolve(sanitized_input="oi")
    run = PipelineRun(ctx=ctx, spans=[], status="ok", started_at=datetime.now(UTC), duration_ms=1)
    await StoreTraceSink(BrokenStore(tmp_path / "t.db")).record(run)  # não levanta
    assert "trace_store_failed" in caplog.text
