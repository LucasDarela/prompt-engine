"""Persistência de traces em SQLite (SPEC-008 §3) e o TraceSink que o pipeline usa."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from prompt_engine.core.tracing import PipelineRun
from prompt_engine.llm.pricing import Pricing
from prompt_engine.observability.trace import Trace, build_trace

logger = logging.getLogger("prompt_engine.observability")

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
  request_id       TEXT PRIMARY KEY,
  created_at       TEXT NOT NULL,
  flow             TEXT NOT NULL,
  status           TEXT NOT NULL,
  category         TEXT,
  tier             TEXT,
  provider         TEXT,
  total_latency_ms INTEGER,
  input_tokens     INTEGER,
  output_tokens    INTEGER,
  payload          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_created ON traces(created_at);
CREATE INDEX IF NOT EXISTS idx_traces_flow_status ON traces(flow, status);
"""


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


class SQLiteTraceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Uma conexão por operação: seguro com asyncio.to_thread (threads diferentes).
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save(self, trace: Trace) -> None:
        output = trace.output or {}
        row = (
            trace.request_id,
            _iso(trace.created_at),
            trace.flow,
            trace.status,
            output.get("category"),
            trace.route.tier if trace.route else None,
            trace.route.provider if trace.route else None,
            trace.total_latency_ms,
            trace.total_tokens.input_tokens,
            trace.total_tokens.output_tokens,
            trace.model_dump_json(),
        )
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO traces VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)

    def get(self, request_id: str) -> Trace | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM traces WHERE request_id = ?", (request_id,)
            ).fetchone()
        return Trace.model_validate_json(row[0]) if row else None

    def list(
        self,
        *,
        limit: int = 50,
        flow: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[Trace]:
        clauses, params = [], []
        for column, value in (("flow", flow), ("status", status)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(_iso(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT payload FROM traces {where} ORDER BY created_at DESC LIMIT ?"  # noqa: S608
        with self._connect() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()
        return [Trace.model_validate_json(r[0]) for r in rows]

    def purge(self, older_than_days: int) -> int:
        cutoff = _iso(datetime.now(UTC) - timedelta(days=older_than_days))
        with self._connect() as conn:
            return conn.execute("DELETE FROM traces WHERE created_at < ?", (cutoff,)).rowcount


class StoreTraceSink:
    """TraceSink do pipeline: monta o trace e grava em thread. Falha de gravação só gera log."""

    def __init__(
        self,
        store: SQLiteTraceStore,
        *,
        store_raw_input: bool = False,
        pricing: Pricing | None = None,
    ) -> None:
        self.store = store
        self.store_raw_input = store_raw_input
        self.pricing = pricing

    async def record(self, run: PipelineRun) -> None:
        trace = build_trace(run, store_raw_input=self.store_raw_input, pricing=self.pricing)
        await asyncio.to_thread(self._save_safely, trace)

    def _save_safely(self, trace: Trace) -> None:
        try:
            self.store.save(trace)
        except Exception as exc:  # observabilidade nunca derruba a request (OBS-4)
            extra: dict[str, Any] = {"request_id": trace.request_id, "error": type(exc).__name__}
            logger.warning("trace_store_failed", extra=extra)
