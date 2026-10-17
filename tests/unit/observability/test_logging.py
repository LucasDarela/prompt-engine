"""Logs estruturados JSON, request_id por contexto e filtro anti-PII (SPEC-008 §4)."""

import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from prompt_engine.observability.logging import configure_logging
from tests.support import make_settings

CPF = "529.982.247-25"


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging(make_settings(log_format="json", log_level="INFO"), stream=buffer)
    yield buffer
    logging.getLogger().handlers.clear()
    structlog.contextvars.clear_contextvars()


def lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def test_json_lines_with_level_timestamp_and_extra(stream: io.StringIO) -> None:
    logging.getLogger("prompt_engine.teste").info(
        "llm.call", extra={"provider": "gemini", "latency_ms": 12}
    )
    (entry,) = lines(stream)
    assert entry["event"] == "llm.call"
    assert entry["level"] == "info"
    assert entry["provider"] == "gemini"
    assert entry["latency_ms"] == 12
    assert "timestamp" in entry


def test_request_id_from_context_is_attached(stream: io.StringIO) -> None:
    structlog.contextvars.bind_contextvars(request_id="req_abc")
    logging.getLogger("prompt_engine.x").info("algo")
    assert lines(stream)[0]["request_id"] == "req_abc"


def test_pii_is_masked_in_any_field(stream: io.StringIO) -> None:
    logging.getLogger("prompt_engine.x").warning(
        f"falhou para {CPF}", extra={"detalhe": f"email a@b.com e {CPF}"}
    )
    raw = stream.getvalue()
    assert CPF not in raw
    assert "a@b.com" not in raw
    entry = lines(stream)[0]
    assert entry["event"] == "falhou para [CPF_1]"
    assert entry["detalhe"] == "email [EMAIL_1] e [CPF_1]"


def test_console_format_is_human_readable() -> None:
    buffer = io.StringIO()
    configure_logging(make_settings(log_format="console", log_level="INFO"), stream=buffer)
    try:
        logging.getLogger("prompt_engine.x").info("evento.console")
        assert "evento.console" in buffer.getvalue()
        assert not buffer.getvalue().lstrip().startswith("{")
    finally:
        logging.getLogger().handlers.clear()


def test_level_is_respected(stream: io.StringIO) -> None:
    logging.getLogger("prompt_engine.x").debug("invisível")
    assert lines(stream) == []
