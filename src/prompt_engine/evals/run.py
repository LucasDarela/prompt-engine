"""CLI dos evals contra o modelo real (gasta quota).

    python -m prompt_engine.evals.run                          # todas as suítes
    python -m prompt_engine.evals.run --sample 15 --suites golden,injection

Sai com código 1 se algum limiar da SPEC-009 §5 não for atingido.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from prompt_engine.config import Settings
from prompt_engine.evals.dataset import GoldenCase, load_cases
from prompt_engine.evals.report import render_markdown, write_report
from prompt_engine.evals.runner import ALL_SUITES, EvalConfig, run_evaluation
from prompt_engine.flows.deps import AppDeps, build_deps
from prompt_engine.observability.logging import configure_logging

FIXTURES = Path("tests/fixtures")


def _inputs(path: Path) -> list[str]:
    return [
        json.loads(line)["input"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _select(cases: list[GoldenCase], ids: str | None) -> list[GoldenCase]:
    if not ids:
        return cases
    wanted = {i.strip() for i in ids.split(",") if i.strip()}
    unknown = wanted - {c.id for c in cases}
    if unknown:
        raise SystemExit(f"ids inexistentes no golden dataset: {sorted(unknown)}")
    return [c for c in cases if c.id in wanted]


def main(
    argv: Sequence[str] | None = None,
    *,
    deps_factory: Callable[[Settings], AppDeps] = build_deps,
) -> int:
    parser = argparse.ArgumentParser(description="Evals contra o provider configurado no .env")
    parser.add_argument("--golden", type=Path, default=FIXTURES / "golden_dataset.jsonl")
    parser.add_argument("--redteam", type=Path, default=FIXTURES / "injection_redteam.jsonl")
    parser.add_argument("--benign", type=Path, default=FIXTURES / "benign.jsonl")
    parser.add_argument("--out", type=Path, default=Path("reports"))
    parser.add_argument("--suites", default=",".join(sorted(ALL_SUITES)))
    parser.add_argument("--sample", type=int)
    parser.add_argument(
        "--ids", help="só estes casos do golden (ex.: g-029,g-030): retomar rodadas"
    )
    parser.add_argument("--delay", type=float)
    parser.add_argument("--concurrency", type=int)
    args = parser.parse_args(argv)

    settings = Settings()
    # Só avisos (retries, 503, fallbacks) e já formatados: o resumo dos evals vai no stdout.
    configure_logging(settings.model_copy(update={"log_level": "WARNING", "log_format": "console"}))
    overrides: dict[str, Any] = {}
    if args.delay is not None:
        overrides["delay_s"] = args.delay
    config = EvalConfig(
        suites=frozenset(s.strip() for s in args.suites.split(",") if s.strip()),
        sample=args.sample,
        concurrency=args.concurrency or settings.eval_concurrency,
        delay_s=overrides.get("delay_s", settings.eval_delay_s),
        progress=lambda line: print(line, file=sys.stderr, flush=True),
    )
    result = asyncio.run(
        run_evaluation(
            deps_factory(settings),
            golden=_select(load_cases(args.golden), args.ids),
            attacks=_inputs(args.redteam),
            benign=_inputs(args.benign),
            config=config,
        )
    )
    paths = write_report(result, args.out)
    print(render_markdown(result))
    print(f"Relatório: {paths['md']} · {paths['json']}")
    return 0 if result.passed else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
