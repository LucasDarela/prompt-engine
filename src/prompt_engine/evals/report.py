"""Relatórios dos evals em JSON (máquina) e Markdown (gente) (SPEC-009 §5)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from prompt_engine.evals.metrics import Metric
from prompt_engine.evals.runner import EvalResult

FAILURE_TITLES = {
    "category": "Categoria errada",
    "urgency": "Urgência errada",
    "needs_human": "needs_human errado",
    "entities": "Entidades com valor errado",
    "errors": "Erros de execução",
    "consistency": "Inconsistentes entre execuções",
    "injection_missed": "Ataques que passaram como seguros",
    "benign_blocked": "Mensagens legítimas bloqueadas",
    "low_quality": "Respostas com nota < 4",
}


def _fmt(metric: Metric, value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1%}" if metric.as_percent else f"{value:g}"


def _status(metric: Metric) -> str:
    return {True: "✅", False: "❌", None: "info"}[metric.passed]


def _threshold(metric: Metric) -> str:
    if metric.threshold is None:
        return "—"
    return f"{'≥' if metric.higher_is_better else '≤'} {_fmt(metric, metric.threshold)}"


def to_dict(result: EvalResult) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "started_at": result.started_at.isoformat(),
        "passed": result.passed,
        "metrics": [
            {
                "name": metric.name,
                "value": metric.value,
                "threshold": metric.threshold,
                "higher_is_better": metric.higher_is_better,
                "passed": metric.passed,
                "detail": metric.detail,
            }
            for metric in result.metrics
        ],
        "failures": result.failures,
        "latency_ms": {"p50": result.latency_p50_ms, "p95": result.latency_p95_ms},
        "tokens": result.tokens.model_dump(),
        "prompt_versions": sorted(
            {
                f"{step}@{version}"
                for run in result.runs
                for step, version in run.prompt_versions.items()
            }
        ),
        "models": dict(Counter(model for run in result.runs for model in run.models.values())),
        "cases": len(result.runs),
    }


def render_markdown(result: EvalResult) -> str:
    data = to_dict(result)
    lines = [
        f"# Relatório de evals · {result.started_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"Provider: `{result.provider}` · casos golden: {data['cases']} · "
        f"resultado: {'✅ aprovado' if result.passed else '❌ reprovado'}",
        "",
        "| Métrica | Valor | Limiar | Status | Detalhe |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {m.name} | {_fmt(m, m.value)} | {_threshold(m)} | {_status(m)} | {m.detail} |"
        for m in result.metrics
    ]
    p50, p95 = result.latency_p50_ms, result.latency_p95_ms
    lines += [
        "",
        f"Latência por caso: p50 {p50 or 0:.0f} ms · p95 {p95 or 0:.0f} ms · "
        f"tokens: {result.tokens.input_tokens} in / {result.tokens.output_tokens} out",
        "",
        f"Prompts: {', '.join(f'`{p}`' for p in data['prompt_versions']) or '—'}",
        f"Modelos (chamadas): {', '.join(f'`{k}` x{v}' for k, v in data['models'].items()) or '—'}",
    ]
    for key, items in result.failures.items():
        if not items:
            continue
        lines += ["", f"## {FAILURE_TITLES.get(key, key)} ({len(items)})", ""]
        for item in items:
            if key in ("category", "urgency", "needs_human"):
                lines.append(f"- `{item[0]}`: esperado `{item[1]}`, obtido `{item[2]}`")
            else:
                lines.append(
                    "- "
                    + " · ".join(f"`{part}`" if i == 0 else part for i, part in enumerate(item))
                )
    return "\n".join(lines) + "\n"


def write_report(result: EvalResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"eval-{result.started_at:%Y%m%d-%H%M%S}"
    paths = {"json": out_dir / f"{stem}.json", "md": out_dir / f"{stem}.md"}
    paths["json"].write_text(
        json.dumps(to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["md"].write_text(render_markdown(result), encoding="utf-8")
    return paths
