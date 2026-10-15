from fastapi.testclient import TestClient

from prompt_engine import __version__
from prompt_engine.api.app import create_app
from prompt_engine.config import Settings


def make_client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None)))  # type: ignore[call-arg]


def test_health_returns_ok_and_version() -> None:
    response = make_client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_main_exposes_asgi_app() -> None:
    import main

    assert main.app.title == "Prompt Engine"
