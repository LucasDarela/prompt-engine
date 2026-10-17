"""Executa as suítes de eval contra o provider configurado (SPEC-009 §5).

Rate limit do plano gratuito: no máximo `concurrency` chamadas simultâneas e uma pausa de
`delay_s` depois de cada caso. Indisponibilidade do provider vira resultado, não exceção.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prompt_engine.core.context import PipelineContext, Usage
from prompt_engine.core.errors import PromptEngineError, PromptInjectionDetected
from prompt_engine.core.llm_step import LLMStep
from prompt_engine.core.pipeline import Pipeline
from prompt_engine.core.vault import PIIVault
from prompt_engine.evals import metrics as m
from prompt_engine.evals.dataset import GoldenCase
from prompt_engine.flows.deps import AppDeps
from prompt_engine.flows.registry import FlowRunner, default_flow_registry
from prompt_engine.flows.support_triage import build_guardrails, make_classify_step
from prompt_engine.guardrails.pii.redaction import redact
from prompt_engine.llm.base import LLMProvider, LLMRequest, LLMResponse
from prompt_engine.output.schemas import JudgeReplyOutput

Sleep = Callable[[float], Awaitable[None]]
ALL_SUITES = frozenset({"golden", "consistency", "injection", "judge"})


@dataclass(frozen=True)
class EvalConfig:
    suites: frozenset[str] = ALL_SUITES
    sample: int | None = None  # limita os casos do golden (economia de quota)
    concurrency: int = 2
    delay_s: float = 1.0
    consistency_cases: int = 10
    consistency_repeats: int = 5


@dataclass
class CaseRun:
    case: GoldenCase
    result: dict[str, Any] | None
    error_code: str | None
    warnings: list[str]
    latency_ms: int
    repaired_steps: list[str] = field(default_factory=list)
    fallback_steps: list[str] = field(default_factory=list)
    prompt_versions: dict[str, str] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)
    tokens: Usage = field(default_factory=Usage)
    ctx: PipelineContext | None = None


@dataclass
class EvalResult:
    provider: str
    started_at: datetime
    metrics: list[m.Metric]
    runs: list[CaseRun]
    failures: dict[str, list[tuple[str, ...]]]
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    tokens: Usage

    @property
    def passed(self) -> bool:
        return all(metric.passed is not False for metric in self.metrics)


class SpyProvider:
    """Registra toda request enviada ao provider real (para provar que nenhuma PII saiu)."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.name = inner.name
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return await self.inner.generate(request)


def install_spies(deps: AppDeps) -> list[SpyProvider]:
    providers = deps.llm.pool.providers
    spies = [SpyProvider(provider) for provider in providers.values()]
    for spy in spies:
        providers[spy.name] = spy
    return spies


def pii_leak_ids(cases: Sequence[GoldenCase], spies: Sequence[SpyProvider]) -> list[str]:
    sent = [msg.content for spy in spies for request in spy.requests for msg in request.messages]
    leaked = []
    for case in cases:
        vault = PIIVault()
        redact(case.input, vault)
        if len(vault) and any(vault.contains_value_in(text) for text in sent):
            leaked.append(case.id)
    return leaked


async def _bounded[T](
    items: Sequence[T], work: Callable[[T], Awaitable[Any]], config: EvalConfig, sleep: Sleep
) -> list[Any]:
    semaphore = asyncio.Semaphore(config.concurrency)

    async def guarded(item: T) -> Any:
        async with semaphore:
            try:
                return await work(item)
            finally:
                await sleep(config.delay_s)

    return list(await asyncio.gather(*(guarded(item) for item in items)))


async def run_golden(
    runner: FlowRunner,
    cases: Sequence[GoldenCase],
    *,
    config: EvalConfig,
    sleep: Sleep = asyncio.sleep,
) -> list[CaseRun]:
    async def one(case: GoldenCase) -> CaseRun:
        start = asyncio.get_running_loop().time()
        try:
            ctx = await runner.run("support_triage", case.input)
        except PromptEngineError as exc:
            elapsed = round((asyncio.get_running_loop().time() - start) * 1000)
            return CaseRun(
                case=case, result=None, error_code=exc.code, warnings=[], latency_ms=elapsed
            )
        elapsed = round((asyncio.get_running_loop().time() - start) * 1000)
        steps = ctx.steps.values()
        return CaseRun(
            case=case,
            result=ctx.result,
            error_code=None,
            warnings=list(ctx.warnings),
            latency_ms=elapsed,
            repaired_steps=[s.name for s in steps if s.repaired],
            fallback_steps=[s.name for s in steps if s.fallback_used],
            prompt_versions={s.name: s.prompt_version for s in steps},
            models={s.name: s.model for s in steps},
            tokens=ctx.total_usage(),
            ctx=ctx,
        )

    return await _bounded(cases, one, config, sleep)


async def run_consistency(
    deps: AppDeps,
    cases: Sequence[GoldenCase],
    *,
    repeats: int,
    config: EvalConfig,
    sleep: Sleep = asyncio.sleep,
) -> dict[str, list[str | None]]:
    """Só guardrails + classify (o step de produção), repetido N vezes por caso."""
    pipeline = Pipeline([*build_guardrails(deps), make_classify_step(deps.llm)])

    async def one(case: GoldenCase) -> str | None:
        try:
            ctx = await pipeline.run(
                PipelineContext.new(flow="eval_consistency", raw_input=case.input)
            )
        except PromptEngineError:
            return None
        step = ctx.step("classify")
        return None if step.fallback_used else str((step.parsed or {}).get("category"))

    jobs = [case for case in cases for _ in range(repeats)]
    answers = await _bounded(jobs, one, config, sleep)
    samples: dict[str, list[str | None]] = {case.id: [] for case in cases}
    for case, answer in zip(jobs, answers, strict=True):
        samples[case.id].append(answer)
    return samples


async def run_injection(
    deps: AppDeps,
    attacks: Sequence[str],
    benign: Sequence[str],
    *,
    config: EvalConfig,
    sleep: Sleep = asyncio.sleep,
) -> tuple[list[str], list[str]]:
    """Só os guardrails (com o juiz LLM real na zona cinzenta)."""
    pipeline = Pipeline(build_guardrails(deps))

    async def one(text: str) -> str:
        try:
            ctx = await pipeline.run(PipelineContext.new(flow="eval_injection", raw_input=text))
        except PromptInjectionDetected:
            return "blocked"
        except PromptEngineError as exc:
            return "invalid" if exc.code == "invalid_input" else "error"
        return ctx.security.verdict

    verdicts = await _bounded([*attacks, *benign], one, config, sleep)
    return verdicts[: len(attacks)], verdicts[len(attacks) :]


def _judge_step(deps: AppDeps) -> LLMStep:
    def variables(ctx: PipelineContext) -> dict[str, Any]:
        reply = str((ctx.result or {}).get("suggested_reply", ""))
        return {
            "message": ctx.sanitized_input,
            "reply": ctx.pii_vault.mask(reply),  # o juiz nunca vê PII real
            "kb_snippets": [
                {"id": s.id, "title": s.title, "content": s.content}
                for s in ctx.enrichment.kb_snippets
            ],
            "sla_hours": ctx.enrichment.sla_hours,
        }

    return LLMStep(
        name="judge_reply",
        prompt_ref="evals.judge_reply@latest",
        variables=variables,
        tier="balanced",  # modelo diferente do draft típico (fast): reduz o viés de autoavaliação
        fallback=lambda c: JudgeReplyOutput(
            rationale="juiz indisponível",
            policy_adherence=1,
            correctness=1,
            tone=1,
            completeness=1,
            overall=1,
        ),
        deps=deps.llm,
    )


async def run_judge(
    deps: AppDeps, runs: Sequence[CaseRun], *, config: EvalConfig, sleep: Sleep = asyncio.sleep
) -> dict[str, JudgeReplyOutput | None]:
    judge = _judge_step(deps)
    judged = [r for r in runs if r.ctx is not None and r.result is not None]

    async def one(run: CaseRun) -> JudgeReplyOutput | None:
        assert run.ctx is not None  # noqa: S101 - filtrado acima
        try:
            ctx = await judge.run(run.ctx)
        except PromptEngineError:
            return None
        step = ctx.step(judge.name)
        return None if step.fallback_used else JudgeReplyOutput.model_validate(step.parsed)

    scores = await _bounded(judged, one, config, sleep)
    return {run.case.id: score for run, score in zip(judged, scores, strict=True)}


async def run_evaluation(
    deps: AppDeps,
    *,
    golden: Sequence[GoldenCase],
    attacks: Sequence[str],
    benign: Sequence[str],
    config: EvalConfig,
    sleep: Sleep = asyncio.sleep,
) -> EvalResult:
    started_at = datetime.now(UTC)
    cases = list(golden[: config.sample] if config.sample else golden)
    spies = install_spies(deps)
    metrics: list[m.Metric] = []
    failures: dict[str, list[tuple[str, ...]]] = {}
    runs: list[CaseRun] = []

    if "golden" in config.suites:
        runs = await run_golden(
            FlowRunner(default_flow_registry(), deps), cases, config=config, sleep=sleep
        )
        metrics += [
            m.json_validity(runs),
            m.first_pass_validity(runs),
            m.category_accuracy(runs),
            m.urgency_accuracy(runs),
            m.urgency_within_one(runs),
            m.needs_human_recall(runs),
            m.needs_human_false_escalation(runs),
            m.entity_recall(runs),
            m.missing_accents_rate(runs),
            m.pii_leaks(pii_leak_ids(cases, spies)),
        ]
        failures["category"] = list(m.misclassified(runs, "category"))
        failures["urgency"] = list(m.misclassified(runs, "urgency"))
        failures["needs_human"] = list(m.misclassified(runs, "needs_human"))
        failures["entities"] = list(m.spurious_entities(runs))
        failures["errors"] = [(r.case.id, str(r.error_code)) for r in runs if r.error_code]

    if "consistency" in config.suites:
        subset = cases[: config.consistency_cases]
        samples = await run_consistency(
            deps, subset, repeats=config.consistency_repeats, config=config, sleep=sleep
        )
        metrics.append(m.consistency(samples))
        failures["consistency"] = [
            (case_id, ", ".join(str(a) for a in answers))
            for case_id, answers in samples.items()
            if len({a for a in answers if a is not None}) > 1
        ]

    if "injection" in config.suites:
        attack_verdicts, benign_verdicts = await run_injection(
            deps, attacks, benign, config=config, sleep=sleep
        )
        metrics += [
            m.injection_recall(attack_verdicts),
            m.injection_fpr(benign_verdicts),
            m.benign_escalation(benign_verdicts),
        ]
        failures["injection_missed"] = [
            (text[:80],) for text, v in zip(attacks, attack_verdicts, strict=True) if v == "safe"
        ]
        failures["benign_blocked"] = [
            (text[:80],) for text, v in zip(benign, benign_verdicts, strict=True) if v == "blocked"
        ]

    if "judge" in config.suites and runs:
        scores = await run_judge(deps, runs, config=config, sleep=sleep)
        metrics.append(m.reply_quality(scores))
        failures["low_quality"] = [
            (case_id, str(score.overall), score.rationale[:160])
            for case_id, score in scores.items()
            if score is not None and score.overall < 4
        ]

    latencies = [float(r.latency_ms) for r in runs if r.result is not None]
    tokens = Usage()
    for run in runs:
        tokens = tokens + run.tokens
    return EvalResult(
        provider=deps.settings.llm_provider,
        started_at=started_at,
        metrics=metrics,
        runs=runs,
        failures=failures,
        latency_p50_ms=m.percentile(latencies, 50),
        latency_p95_ms=m.percentile(latencies, 95),
        tokens=tokens,
    )
