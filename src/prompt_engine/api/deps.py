"""Dependências FastAPI: runner, settings e autenticação por API key."""

from __future__ import annotations

import secrets

from fastapi import Request

from prompt_engine.config import Settings
from prompt_engine.core.errors import UnauthorizedError
from prompt_engine.flows.deps import AppDeps
from prompt_engine.flows.registry import FlowRunner


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_runner(request: Request) -> FlowRunner:
    runner: FlowRunner = request.app.state.runner
    return runner


def get_app_deps(request: Request) -> AppDeps:
    deps: AppDeps = request.app.state.deps
    return deps


def require_api_key(request: Request) -> None:
    """Se API_KEY estiver configurada, exige o header X-API-Key (comparação em tempo constante)."""
    expected = get_settings(request).api_key
    if expected is None:
        return
    provided = request.headers.get("X-API-Key", "")
    if not secrets.compare_digest(provided.encode(), expected.get_secret_value().encode()):
        raise UnauthorizedError()
