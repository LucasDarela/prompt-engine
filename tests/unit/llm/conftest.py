"""Fixture da suíte de contrato: cada provider é exercitado pelo mesmo conjunto de testes."""

import pytest

from tests.unit.llm._support import HARNESSES, Harness


@pytest.fixture(params=sorted(HARNESSES))
def harness(request: pytest.FixtureRequest) -> Harness:
    return HARNESSES[request.param]()
