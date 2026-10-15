"""Erros -> envelope JSON estável (SPEC-001 §3). Detalhes internos nunca vão para o body."""

from __future__ import annotations

import logging
import math

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from prompt_engine.api.schemas import ErrorDetail, ErrorResponse
from prompt_engine.core.errors import PromptEngineError, ProviderRateLimitError

logger = logging.getLogger("prompt_engine.api")

HTTP_STATUS: dict[str, int] = {
    "invalid_input": 422,
    "unsafe_input": 422,
    "unauthorized": 401,
    "flow_not_found": 404,
    "prompt_not_found": 404,
    "rate_limited": 429,
    "provider_unavailable": 503,
}
GENERIC_MESSAGE = PromptEngineError.public_message


def error_response(
    request_id: str, status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorResponse(request_id=request_id, error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", "req_unknown"))


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, PromptEngineError)  # noqa: S101 - registrado só para esse tipo
    status = HTTP_STATUS.get(exc.code)
    if status is None:  # erro de domínio sem mapeamento HTTP = bug/config nossa
        logger.error("domain_error_unmapped", exc_info=exc)
        return error_response(request_id_of(request), 500, "internal_error", GENERIC_MESSAGE)
    headers = None
    if isinstance(exc, ProviderRateLimitError) and exc.retry_after:
        headers = {"Retry-After": str(math.ceil(exc.retry_after))}
    log = logger.warning if status >= 500 else logger.info
    log("domain_error code=%s request_id=%s", exc.code, request_id_of(request))
    return error_response(request_id_of(request), status, exc.code, exc.public_message, headers)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response(
        request_id_of(request), 422, "invalid_payload", "O corpo da requisição é inválido."
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PromptEngineError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
