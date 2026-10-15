"""PIIRehydration (stage 10): devolve os valores reais SÓ na resposta sugerida ao cliente."""

from __future__ import annotations

import re

from prompt_engine.config import Settings
from prompt_engine.core.context import PipelineContext
from prompt_engine.core.errors import PipelineMisconfigured
from prompt_engine.core.stage import Stage

REHYDRATED_FIELD = "suggested_reply"
_SPACES_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r" +([.,;:!?])")


class PIIRehydration(Stage):
    name = "pii_rehydration"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_enabled(self, ctx: PipelineContext) -> bool:
        return self.settings.pii_rehydrate_reply

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.result is None:
            raise PipelineMisconfigured("PIIRehydration exige ctx.result montado")
        reply = str(ctx.result.get(REHYDRATED_FIELD, ""))
        restored, unknown = ctx.pii_vault.restore(reply)
        restored = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", _SPACES_RE.sub(" ", restored)).strip()
        ctx = ctx.evolve(result={**ctx.result, REHYDRATED_FIELD: restored})
        # Placeholder que o modelo inventou (não está no vault) é removido e sinalizado.
        return ctx.with_warning("unknown_placeholder") if unknown else ctx
