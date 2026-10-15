"""Factory da aplicação FastAPI (SPEC-001). Camada fina: sem lógica de negócio."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from prompt_engine import __version__
from prompt_engine.api.errors import GENERIC_MESSAGE, error_response, install_error_handlers
from prompt_engine.api.routes import api_router, health_router
from prompt_engine.config import Settings
from prompt_engine.core.context import new_request_id
from prompt_engine.flows.deps import AppDeps, build_deps
from prompt_engine.flows.registry import FlowRunner, default_flow_registry

logger = logging.getLogger("prompt_engine.api")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _attach(app: FastAPI, deps: AppDeps) -> None:
    app.state.deps = deps
    app.state.runner = FlowRunner(default_flow_registry(), deps)


def create_app(settings: Settings | None = None, *, deps: AppDeps | None = None) -> FastAPI:
    """`deps` pronto (testes) ou montado no startup a partir de `settings` (servidor)."""
    settings = deps.settings if deps else (settings or Settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if getattr(app.state, "deps", None) is None:
            _attach(app, build_deps(settings))  # falha rápido se prompts/KB/config forem inválidos
        yield

    app = FastAPI(
        title="Prompt Engine",
        version=__version__,
        description="Middleware de prompts para triagem de suporte do Chopp Hub com LLMs.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    if deps is not None:
        _attach(app, deps)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else new_request_id()
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:  # último recurso: nada de stack trace ou detalhe interno no body
            logger.exception("unhandled_error request_id=%s", request_id)
            response = error_response(request_id, 500, "internal_error", GENERIC_MESSAGE)
        response.headers["X-Request-ID"] = request_id
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    return app
