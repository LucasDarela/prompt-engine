"""Validação de saída com reparo e fallback (SPEC-007 §3). Nunca deixa saída inválida passar."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from prompt_engine.core.errors import OutputValidationError, PromptEngineError
from prompt_engine.output.cleaners import extract_json, loads_lenient, strip_markdown
from prompt_engine.output.normalize import normalize_enums

# Recebe (saída inválida, erros) e devolve uma nova saída do modelo (prompt common.repair_json).
RepairFn = Callable[[str, list[str]], Awaitable[str]]


@dataclass(frozen=True)
class ValidationOutcome[T: BaseModel]:
    value: T
    raw_text: str
    attempts: int
    repaired: bool = False
    fallback_used: bool = False
    errors: list[str] = field(default_factory=list)


def _format_errors(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
    ]


class OutputValidator:
    def __init__(self, max_repairs: int = 1) -> None:
        self.max_repairs = max_repairs

    def parse[T: BaseModel](self, raw: str, model: type[T], *, step: str = "") -> T:
        """markdown -> JSON -> normalização -> Pydantic. Falha vira OutputValidationError."""
        candidate = extract_json(strip_markdown(raw))
        if candidate is None:
            raise OutputValidationError(step, ["nenhum objeto JSON encontrado na resposta"])
        try:
            data = loads_lenient(candidate)  # candidate é sempre "{...}" -> dict
        except ValueError as exc:
            raise OutputValidationError(step, [f"JSON inválido: {exc}"]) from exc
        try:
            return model.model_validate(normalize_enums(data, model))
        except ValidationError as exc:
            raise OutputValidationError(step, _format_errors(exc)) from exc

    async def validate[T: BaseModel](
        self,
        raw: str,
        model: type[T],
        *,
        step: str,
        repair: RepairFn | None = None,
        fallback: Callable[[], T] | None = None,
    ) -> ValidationOutcome[T]:
        attempts = 0
        errors: list[str] = []
        current = raw
        while True:
            attempts += 1
            try:
                value = self.parse(current, model, step=step)
                return ValidationOutcome(
                    value=value, raw_text=current, attempts=attempts, repaired=attempts > 1
                )
            except OutputValidationError as exc:
                errors = exc.errors
            if repair is None or attempts > self.max_repairs:
                break
            try:
                current = await repair(current, errors)
            except PromptEngineError as exc:
                errors = [*errors, f"reparo falhou: {exc.code}"]
                break

        if fallback is None:
            raise OutputValidationError(step, errors)
        return ValidationOutcome(
            value=fallback(), raw_text=current, attempts=attempts, fallback_used=True, errors=errors
        )
