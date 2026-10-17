"""Evals contra o modelo real (SPEC-009 §5). Rodam só com `pytest -m live` (gastam quota).

A avaliação roda UMA vez por sessão; cada limiar é um teste, para o relatório do pytest mostrar
exatamente o que passou. O relatório completo vai para reports/eval-*.md.
"""

import asyncio
from pathlib import Path

import pytest

from prompt_engine.config import Settings
from prompt_engine.evals.dataset import load_cases
from prompt_engine.evals.metrics import Metric
from prompt_engine.evals.report import write_report
from prompt_engine.evals.run import FIXTURES, _inputs
from prompt_engine.evals.runner import EvalConfig, EvalResult, run_evaluation
from prompt_engine.flows.deps import build_deps

pytestmark = pytest.mark.live

THRESHOLD_METRICS = [
    "json_validity",
    "first_pass_validity",
    "category_accuracy",
    "urgency_accuracy",
    "needs_human_recall",
    "consistency",
    "injection_recall",
    "injection_fpr",
    "pii_leaks",
    "reply_quality",
]


@pytest.fixture(scope="session")
def eval_result(request: pytest.FixtureRequest) -> EvalResult:
    option = request.config.getoption
    settings = Settings(llm_provider=option("--provider"))
    config = EvalConfig(
        suites=frozenset(option("--suites").split(",")),
        sample=option("--sample"),
        concurrency=settings.eval_concurrency,
        delay_s=settings.eval_delay_s,
    )
    result = asyncio.run(
        run_evaluation(
            build_deps(settings),
            golden=load_cases(FIXTURES / "golden_dataset.jsonl"),
            attacks=_inputs(FIXTURES / "injection_redteam.jsonl"),
            benign=_inputs(FIXTURES / "benign.jsonl"),
            config=config,
        )
    )
    paths = write_report(result, Path("reports"))
    print(f"\nRelatório: {paths['md']}")
    return result


def _metric(result: EvalResult, name: str) -> Metric | None:
    return next((m for m in result.metrics if m.name == name), None)


@pytest.mark.parametrize("name", THRESHOLD_METRICS)
def test_threshold(eval_result: EvalResult, name: str) -> None:
    metric = _metric(eval_result, name)
    if metric is None or metric.value is None:
        pytest.skip(f"{name}: suíte não executada ou sem dados")
    assert metric.passed, f"{name}={metric.value} (limiar {metric.threshold}) · {metric.detail}"
