"""Métricas dos evals: funções puras sobre resultados de execução (SPEC-009 §5)."""

from typing import Any

import pytest

from prompt_engine.evals import metrics as m
from prompt_engine.evals.dataset import GoldenCase, GoldenExpected
from prompt_engine.evals.runner import CaseRun
from prompt_engine.output.schemas import JudgeReplyOutput


def run(
    expected: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    *,
    error: str | None = None,
    repaired: bool = False,
    fallback: bool = False,
    warnings: list[str] | None = None,
    case_id: str = "c",
) -> CaseRun:
    case = GoldenCase(id=case_id, input="x", expected=GoldenExpected(**(expected or {})))
    return CaseRun(
        case=case,
        result=result,
        error_code=error,
        warnings=warnings or [],
        latency_ms=100,
        repaired_steps=["classify"] if repaired else [],
        fallback_steps=["classify"] if fallback else [],
    )


OK = {
    "category": "billing",
    "urgency": "high",
    "needs_human": True,
    "entities": {"order_id": "PED-1"},
}


def test_metric_pass_logic() -> None:
    assert m.Metric("x", 0.9, 0.85).passed is True
    assert m.Metric("x", 0.8, 0.85).passed is False
    assert m.Metric("x", 0.0, 0.0, higher_is_better=False).passed is True
    assert m.Metric("x", 1.0, 0.0, higher_is_better=False).passed is False
    assert m.Metric("x", None, 0.9).passed is None  # sem dados
    assert m.Metric("x", 0.5, None).passed is None  # informativa


def test_validity_ignores_provider_outages() -> None:
    runs = [run(result=OK), run(error="provider_unavailable"), run(error="internal_error")]
    validity = m.json_validity(runs)
    assert validity.value == pytest.approx(0.5)  # 1 válida em 2 que completaram (503 não conta)
    assert "1 indisponibilidade" in validity.detail


def test_first_pass_validity() -> None:
    runs = [
        run(result=OK),
        run(result=OK, repaired=True),
        run(result=OK, fallback=True),
        run(result=OK),
    ]
    assert m.first_pass_validity(runs).value == pytest.approx(0.5)


def test_category_accuracy_only_over_labeled_cases() -> None:
    runs = [
        run({"category": "billing"}, OK),
        run({"category": "legal"}, OK, case_id="erro"),
        run({}, OK),  # sem rótulo: não entra
        run({"category": "billing"}, None, error="provider_unavailable"),  # não completou
    ]
    metric = m.category_accuracy(runs)
    assert metric.value == pytest.approx(0.5)
    assert m.misclassified(runs, "category") == [("erro", "legal", "billing")]


def test_urgency_exact_and_within_one_level() -> None:
    runs = [
        run({"urgency": "high"}, OK),
        run({"urgency": "critical"}, OK),  # 1 nível de distância
        run({"urgency": "low"}, OK),  # 2 níveis
    ]
    assert m.urgency_accuracy(runs).value == pytest.approx(1 / 3)
    assert m.urgency_within_one(runs).value == pytest.approx(2 / 3)


def test_needs_human_recall_and_precision() -> None:
    runs = [
        run({"needs_human": True}, OK),
        run({"needs_human": True}, OK | {"needs_human": False}),
        run({"needs_human": False}, OK),  # escalou à toa
        run({"needs_human": False}, OK | {"needs_human": False}),
    ]
    assert m.needs_human_recall(runs).value == pytest.approx(0.5)
    assert m.needs_human_false_escalation(runs).value == pytest.approx(0.5)


def test_entity_recall_and_spurious() -> None:
    runs = [
        run({"entities": {"order_id": "PED-1"}}, OK),
        run(
            {"entities": {"order_id": "PED-2", "amount": "R$ 10,00"}},
            OK | {"entities": {"order_id": "PED-2", "amount": "outubro"}},
        ),
    ]
    assert m.entity_recall(runs).value == pytest.approx(2 / 3)
    assert m.spurious_entities(runs) == [("c", "amount", "outubro")]


def test_missing_accents_rate() -> None:
    runs = [
        run(result=OK, warnings=["missing_accents"]),
        run(result=OK),
        run(error="provider_unavailable"),
    ]
    assert m.missing_accents_rate(runs).value == pytest.approx(0.5)


def test_consistency() -> None:
    samples = {
        "a": ["billing"] * 5,
        "b": ["billing", "billing", "legal", "billing", "billing"],
        "c": [None] * 5,
    }
    metric = m.consistency(samples)
    assert metric.value == pytest.approx((5 + 4) / 10)  # "c" sem respostas válidas não conta
    assert m.Metric("x", metric.value, 0.95).passed is False


def test_injection_metrics() -> None:
    attacks = ["blocked", "suspicious", "safe", "error"]
    benign = ["safe", "safe", "suspicious", "blocked"]
    assert m.injection_recall(attacks).value == pytest.approx(2 / 3)  # erro não conta
    assert m.injection_fpr(benign).value == pytest.approx(0.25)
    assert m.benign_escalation(benign).value == pytest.approx(0.25)


def test_pii_leaks() -> None:
    assert m.pii_leaks([]).passed is True
    assert m.pii_leaks(["g-001"]).passed is False


def test_reply_quality_mean_and_criteria() -> None:
    def judge(overall: int) -> JudgeReplyOutput:
        return JudgeReplyOutput(
            rationale="r",
            policy_adherence=5,
            correctness=4,
            tone=5,
            completeness=3,
            overall=overall,
        )

    metric = m.reply_quality({"a": judge(5), "b": judge(3), "c": None})
    assert metric.value == pytest.approx(4.0)
    assert "2 avaliadas" in metric.detail
    assert m.Metric("q", metric.value, 4.0).passed is True


def test_percentile() -> None:
    assert m.percentile([], 50) is None
    assert m.percentile([10, 20, 30, 40], 50) == 25
    assert m.percentile([10, 20, 30, 40], 95) == pytest.approx(38.5)
