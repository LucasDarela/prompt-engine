"""Exporta traces reais como candidatos a golden dataset (SPEC-008 §6).

    python -m prompt_engine.observability.export --since 2026-10-01 --status ok \\
        --out tests/fixtures/candidates.jsonl

O `expected` vem PRÉ-PREENCHIDO com a saída do modelo e `reviewed: false`: um humano confere,
corrige e só então move o caso para o golden_dataset.jsonl.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from prompt_engine.evals.dataset import GoldenCase, GoldenExpected, dump_cases
from prompt_engine.observability.store import SQLiteTraceStore
from prompt_engine.observability.trace import Trace


def trace_to_case(trace: Trace) -> GoldenCase | None:
    if trace.output is None:
        return None
    out = trace.output
    return GoldenCase(
        id=f"trace-{trace.request_id}",
        input=trace.input_redacted,
        expected=GoldenExpected(
            category=out.get("category"),
            urgency=out.get("urgency"),
            needs_human=out.get("needs_human"),
            entities=out.get("entities") or None,
        ),
        tags=["from_trace", trace.flow],
        reviewed=False,
        source=f"trace:{trace.request_id}",
    )


def export_candidates(
    store: SQLiteTraceStore,
    *,
    since: datetime | None = None,
    status: str | None = "ok",
    flow: str | None = None,
    limit: int = 500,
) -> list[GoldenCase]:
    traces = store.list(limit=limit, flow=flow, status=status, since=since)
    return [case for trace in traces if (case := trace_to_case(trace)) is not None]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exporta traces como candidatos a golden dataset")
    parser.add_argument("--db", type=Path, default=Path("var/traces.db"))
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/candidates.jsonl"))
    parser.add_argument("--since", type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC))
    parser.add_argument("--status", default="ok")
    parser.add_argument("--flow")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args(argv)
    cases = export_candidates(
        SQLiteTraceStore(args.db),
        since=args.since,
        status=args.status,
        flow=args.flow,
        limit=args.limit,
    )
    count = dump_cases(args.out, cases)
    print(f"{count} candidatos gravados em {args.out} (reviewed: false, revise antes de usar)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
