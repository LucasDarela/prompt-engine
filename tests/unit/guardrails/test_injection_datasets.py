"""GR-2 / GR-3 offline: camada heurística sobre os datasets, pelo caminho real do pipeline.

Sem juiz real, a heurística é medida pelo que ela decide sozinha:
- recall: ataque NÃO pode passar como "safe" (tem de ser bloqueado ou escalado ao juiz);
- falso positivo: mensagem legítima BLOQUEADA;
- escalonamento: mensagens legítimas enviadas ao juiz (custo de chamadas).
A acurácia do juiz real é medida nos evals ao vivo (SPEC-009 §5).
"""

import json
from pathlib import Path
from typing import Any

import pytest

from prompt_engine.core.context import PipelineContext
from prompt_engine.guardrails.injection.guard import WARNING_WEIGHTS
from prompt_engine.guardrails.injection.heuristics import InjectionHeuristics
from prompt_engine.guardrails.input_validation import InputValidation
from prompt_engine.guardrails.pii.redaction import PIIRedaction
from tests.support import make_settings

FIXTURES = Path(__file__).parents[2] / "fixtures"
SETTINGS = make_settings()
HEURISTICS = InjectionHeuristics.default()


def load(name: str) -> list[dict[str, Any]]:
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


async def heuristic_score(text: str) -> tuple[float, list[str]]:
    ctx = PipelineContext.new(flow="t", raw_input=text)
    ctx = await InputValidation(SETTINGS).run(ctx)
    ctx = await PIIRedaction().run(ctx)
    extra = {w: WARNING_WEIGHTS[w] for w in ctx.warnings if w in WARNING_WEIGHTS}
    result = HEURISTICS.scan(ctx.sanitized_input or "", extra_signals=extra)
    return result.score, result.signals


ATTACKS = load("injection_redteam.jsonl")
BENIGN = load("benign.jsonl")


def test_dataset_sizes() -> None:
    assert len(ATTACKS) >= 30
    assert len(BENIGN) >= 50


async def test_attack_recall_at_least_90_percent() -> None:  # GR-2
    missed = []
    for case in ATTACKS:
        score, _ = await heuristic_score(case["input"])
        if score < SETTINGS.injection_low:
            missed.append(case["id"])
    recall = 1 - len(missed) / len(ATTACKS)
    assert recall >= 0.90, f"recall={recall:.0%} perdidos={missed}"


async def test_benign_false_positive_at_most_5_percent() -> None:  # GR-3
    blocked = []
    for case in BENIGN:
        score, signals = await heuristic_score(case["input"])
        if score >= SETTINGS.injection_high:
            blocked.append((case["id"], signals))
    fpr = len(blocked) / len(BENIGN)
    assert fpr <= 0.05, f"FP={fpr:.0%} bloqueados={blocked}"


async def test_benign_escalation_rate_at_most_20_percent() -> None:
    escalated = []
    for case in BENIGN:
        score, signals = await heuristic_score(case["input"])
        if score >= SETTINGS.injection_low:
            escalated.append((case["id"], signals))
    rate = len(escalated) / len(BENIGN)
    assert rate <= 0.20, f"escalonamento={rate:.0%} {escalated}"


@pytest.mark.parametrize("case", ATTACKS, ids=[c["id"] for c in ATTACKS])
async def test_each_attack_has_a_signal(case: dict[str, Any]) -> None:
    # Diagnóstico por caso: mostra exatamente qual ataque escapou e com qual técnica.
    _score, signals = await heuristic_score(case["input"])
    assert signals, f"{case['technique']}: nenhum sinal para {case['input']!r}"
