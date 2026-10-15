"""PIIRedaction (stage 2): troca PII por placeholders tipados antes de qualquer I/O externo."""

from __future__ import annotations

import re
from collections.abc import Sequence

from prompt_engine.core.context import PipelineContext
from prompt_engine.core.stage import Stage
from prompt_engine.core.vault import PIIVault
from prompt_engine.guardrails.pii.detectors import DEFAULT_DETECTORS, Detector


def redact(text: str, vault: PIIVault, detectors: Sequence[Detector] = DEFAULT_DETECTORS) -> str:
    for detector in detectors:

        def replace(match: re.Match[str], d: Detector = detector) -> str:
            value = match.group(d.group)
            if not d.validate(value):
                return match.group(0)
            whole, offset = match.group(0), match.start(0)
            start, end = match.start(d.group) - offset, match.end(d.group) - offset
            return whole[:start] + vault.put(d.kind, value) + whole[end:]

        text = detector.pattern.sub(replace, text)
    return text


class PIIRedaction(Stage):
    name = "pii_redaction"

    def __init__(self, detectors: Sequence[Detector] = DEFAULT_DETECTORS) -> None:
        self.detectors = detectors

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        sanitized = redact(ctx.raw_input, ctx.pii_vault, self.detectors)
        return ctx.evolve(sanitized_input=sanitized)
