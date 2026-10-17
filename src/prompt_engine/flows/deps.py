"""AppDeps: tudo o que os flows precisam, montado uma vez no startup (composition root)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from prompt_engine.config import Settings
from prompt_engine.core.llm_step import LLMDeps
from prompt_engine.core.tracing import NoopSink, TraceSink
from prompt_engine.enrichment.knowledge_base import FileKnowledgeBase, SLAPolicy, load_sla
from prompt_engine.guardrails.injection.heuristics import InjectionHeuristics
from prompt_engine.llm.base import LLMProvider
from prompt_engine.llm.factory import build_pool
from prompt_engine.llm.pricing import load_pricing
from prompt_engine.observability.store import SQLiteTraceStore, StoreTraceSink
from prompt_engine.output.validator import OutputValidator
from prompt_engine.prompts.registry import PromptRegistry
from prompt_engine.prompts.renderer import PromptRenderer


@dataclass(frozen=True)
class AppDeps:
    settings: Settings
    llm: LLMDeps
    kb: FileKnowledgeBase
    sla: Mapping[str, SLAPolicy]
    heuristics: InjectionHeuristics
    sink: TraceSink = field(default_factory=NoopSink)
    traces: SQLiteTraceStore | None = None  # None quando OBS_ENABLED=false


def build_deps(
    settings: Settings,
    *,
    provider_overrides: Mapping[str, LLMProvider] | None = None,
    sink: TraceSink | None = None,
) -> AppDeps:
    """Carrega prompts, KB, SLA e providers. Qualquer config inválida falha AQUI (startup)."""
    traces = SQLiteTraceStore(settings.obs_db_path) if settings.obs_enabled else None
    default_sink: TraceSink = (
        StoreTraceSink(traces, store_raw_input=settings.obs_store_raw_input, pricing=load_pricing())
        if traces is not None
        else NoopSink()
    )
    registry = PromptRegistry.default()
    llm = LLMDeps(
        registry=registry,
        renderer=PromptRenderer(registry),
        pool=build_pool(settings, overrides=provider_overrides),
        validator=OutputValidator(max_repairs=settings.output_max_repairs),
        settings=settings,
    )
    return AppDeps(
        settings=settings,
        llm=llm,
        kb=FileKnowledgeBase.from_directory(
            settings.kb_path, include_unreviewed=settings.kb_include_unreviewed
        ),
        sla=load_sla(settings.sla_path),
        heuristics=InjectionHeuristics.default(),
        sink=sink or default_sink,
        traces=traces,
    )
