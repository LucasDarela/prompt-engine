"""Rotas HTTP (SPEC-001 §2). Camada fina: validar, chamar o FlowRunner, apresentar."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from prompt_engine import __version__
from prompt_engine.api.deps import get_app_deps, get_runner, get_settings, require_api_key
from prompt_engine.api.errors import request_id_of
from prompt_engine.api.presenter import to_response
from prompt_engine.api.schemas import (
    ErrorResponse,
    ProcessPromptRequest,
    ProcessPromptResponse,
    PromptSummary,
    ReadinessResponse,
)
from prompt_engine.config import Settings
from prompt_engine.core.context import RunOptions
from prompt_engine.core.errors import TraceNotFoundError
from prompt_engine.flows.deps import AppDeps
from prompt_engine.flows.registry import FlowRunner

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse, "description": description}
    for status, description in {
        401: "API key ausente ou inválida",
        404: "Flow ou prompt inexistente",
        422: "Payload ou mensagem inválida, ou entrada insegura (prompt injection)",
        429: "Limite de requisições do provedor de LLM",
        500: "Erro interno",
        503: "Provedores de LLM indisponíveis",
    }.items()
}

PLAYGROUND = Path(__file__).parent / "static" / "playground.html"

health_router = APIRouter(tags=["health"])
page_router = APIRouter(include_in_schema=False)


@page_router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/playground")


@page_router.get("/playground", response_class=HTMLResponse)
async def playground() -> HTMLResponse:
    """Frontend de teste: text area, cenários, triagem, pipeline e debug por step."""
    return HTMLResponse(PLAYGROUND.read_text(encoding="utf-8"))


api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@health_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@health_router.get("/health/ready", response_model=ReadinessResponse)
async def ready(
    deps: Annotated[AppDeps, Depends(get_app_deps)], request: Request
) -> ReadinessResponse:
    runner: FlowRunner = request.app.state.runner
    return ReadinessResponse(
        status="ready",
        prompts=len(deps.llm.registry.list()),
        kb_articles=len(deps.kb),
        providers=sorted(deps.llm.pool.providers),
        flows=runner.registry.names(),
    )


@api_router.post(
    "/process-prompt",
    response_model=ProcessPromptResponse,
    responses=ERROR_RESPONSES,
    tags=["triage"],
    summary="Processa a mensagem do cliente pelo flow de triagem",
)
async def process_prompt(
    body: ProcessPromptRequest,
    request: Request,
    runner: Annotated[FlowRunner, Depends(get_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProcessPromptResponse:
    start = time.perf_counter()
    ctx = await runner.run(
        body.flow,
        body.input,
        customer_id=body.customer_id,
        options=RunOptions(**body.options.model_dump()),
        request_id=request_id_of(request),
    )
    latency_ms = round((time.perf_counter() - start) * 1000)
    debug = body.options.include_debug and settings.debug_endpoints
    return to_response(ctx, latency_ms=latency_ms, debug=debug)


@api_router.get("/traces/{request_id}", responses=ERROR_RESPONSES, tags=["debug"])
async def get_trace(
    request_id: str,
    deps: Annotated[AppDeps, Depends(get_app_deps)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Trace completo de uma request. Só existe com DEBUG_ENDPOINTS=true (senão, 404)."""
    if not settings.debug_endpoints or deps.traces is None:
        raise TraceNotFoundError()
    trace = deps.traces.get(request_id)
    if trace is None:
        raise TraceNotFoundError()
    return trace.model_dump(mode="json")


@api_router.get("/prompts", response_model=list[PromptSummary], tags=["prompts"])
async def list_prompts(deps: Annotated[AppDeps, Depends(get_app_deps)]) -> list[PromptSummary]:
    return [
        PromptSummary(
            id=t.id,
            version=t.version,
            description=t.description,
            techniques=[str(tech) for tech in t.techniques],
            tier=t.tier,
            output_schema=t.output_schema,
        )
        for t in deps.llm.registry.list()
    ]


@api_router.get("/prompts/{prompt_ref}", responses=ERROR_RESPONSES, tags=["prompts"])
async def get_prompt(
    prompt_ref: str, deps: Annotated[AppDeps, Depends(get_app_deps)]
) -> dict[str, Any]:
    """Template cru (sem renderizar). Aceita `id`, `id@latest` ou `id@x.y.z`."""
    return deps.llm.registry.get(prompt_ref).model_dump(mode="json")
