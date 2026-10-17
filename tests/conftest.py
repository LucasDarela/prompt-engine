"""Opções de linha de comando dos evals ao vivo (`pytest -m live --provider gemini`)."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("evals", "evals contra o modelo real (gastam quota)")
    group.addoption("--provider", default="gemini", help="provider dos evals live")
    group.addoption("--sample", type=int, default=None, help="limita os casos do golden dataset")
    group.addoption("--suites", default="golden,consistency,injection,judge", help="suítes a rodar")
