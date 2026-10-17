"""Logs estruturados (structlog sobre stdlib) com request_id por contexto e filtro anti-PII.

Todo o código usa `logging.getLogger(...).info("evento", extra={...})`; este módulo só formata.
Sem `configure_logging`, a lib fica silenciosa (padrão do stdlib), o que é bom para testes.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import IO, Any

import structlog

from prompt_engine.config import Settings
from prompt_engine.core.vault import PIIVault
from prompt_engine.guardrails.pii.redaction import redact

NOISY_LOGGERS = ("httpx", "httpcore", "google_genai", "urllib3")


def mask_pii(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Último filtro antes de renderizar: nenhum CPF, e-mail, cartão etc. sai num log."""
    vault = PIIVault()
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact(value, vault)
    return event_dict


def configure_logging(settings: Settings, stream: IO[str] | None = None) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        mask_pii,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    # Clientes HTTP (inclusive o do SDK do Gemini) logariam cada chamada em INFO: ruído.
    for noisy in NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
