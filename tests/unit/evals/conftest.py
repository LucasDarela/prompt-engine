"""A CLI dos evals configura o logging global; cada teste devolve o logging limpo."""

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    yield
    logging.getLogger().handlers.clear()
