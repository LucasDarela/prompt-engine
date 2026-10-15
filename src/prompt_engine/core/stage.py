"""Contrato de um stage do pipeline (SPEC-002 §2)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from prompt_engine.core.context import PipelineContext


class Stage(ABC):
    """Unidade do pipeline: PipelineContext -> PipelineContext, sem conhecer os demais stages."""

    name: str

    def is_enabled(self, ctx: PipelineContext) -> bool:
        """Stages desligados são pulados pelo Pipeline (span 'skipped')."""
        return True

    @abstractmethod
    async def run(self, ctx: PipelineContext) -> PipelineContext: ...
