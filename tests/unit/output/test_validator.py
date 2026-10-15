import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from prompt_engine.core.errors import OutputValidationError, ProviderUnavailableError
from prompt_engine.output.fallbacks import classify_fallback
from prompt_engine.output.schemas import ClassifyOutput
from prompt_engine.output.validator import OutputValidator

VALID = {
    "reasoning": "ok",
    "category": "billing",
    "urgency": "high",
    "sentiment": "negative",
    "entities": {},
    "intents": ["refund"],
    "complexity_hint": "simple",
}
VALID_JSON = json.dumps(VALID)


class RepairSpy:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, list[str]]] = []

    async def __call__(self, raw: str, errors: list[str]) -> str:
        self.calls.append((raw, errors))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


validator = OutputValidator(max_repairs=1)


# --- parse -------------------------------------------------------------------


def test_parse_valid_with_markdown_and_aliases() -> None:
    raw = "Aqui está:\n```json\n" + VALID_JSON.replace('"billing"', '"Cobrança"') + "\n```"
    assert validator.parse(raw, ClassifyOutput).category == "billing"


def test_parse_errors_are_readable() -> None:
    with pytest.raises(OutputValidationError) as info:
        validator.parse(json.dumps(VALID | {"category": "vendas", "extra": 1}), ClassifyOutput)
    joined = " | ".join(info.value.errors)
    assert "category" in joined
    assert "extra" in joined


def test_parse_no_json() -> None:
    with pytest.raises(OutputValidationError, match="JSON"):
        validator.parse("desculpe, não posso ajudar", ClassifyOutput)


def test_parse_broken_json_object() -> None:
    with pytest.raises(OutputValidationError, match="JSON inválido"):
        validator.parse('{"category": }', ClassifyOutput)


def test_parse_json_array_is_rejected() -> None:
    with pytest.raises(OutputValidationError):
        validator.parse("[1, 2]", ClassifyOutput)


# --- validate ----------------------------------------------------------------


async def test_valid_first_pass_no_repair() -> None:
    repair = RepairSpy()
    outcome = await validator.validate(VALID_JSON, ClassifyOutput, step="classify", repair=repair)
    assert outcome.value.category == "billing"
    assert (outcome.repaired, outcome.fallback_used, outcome.attempts) == (False, False, 1)
    assert repair.calls == []


async def test_invalid_triggers_repair_with_errors() -> None:  # OUT-5
    bad = json.dumps(VALID | {"extra": 1})
    repair = RepairSpy(VALID_JSON)
    outcome = await validator.validate(bad, ClassifyOutput, step="classify", repair=repair)
    assert outcome.repaired is True
    assert outcome.attempts == 2
    assert outcome.raw_text == VALID_JSON
    (raw_sent, errors_sent) = repair.calls[0]
    assert raw_sent == bad
    assert any("extra" in e for e in errors_sent)


async def test_repair_fails_uses_fallback() -> None:  # OUT-6
    repair = RepairSpy("ainda inválido")
    outcome = await validator.validate(
        "lixo", ClassifyOutput, step="classify", repair=repair, fallback=classify_fallback
    )
    assert outcome.fallback_used is True
    assert outcome.value == classify_fallback()
    assert outcome.errors


async def test_repair_provider_error_uses_fallback() -> None:
    repair = RepairSpy(ProviderUnavailableError("gemini"))
    outcome = await validator.validate(
        "lixo", ClassifyOutput, step="classify", repair=repair, fallback=classify_fallback
    )
    assert outcome.fallback_used is True
    assert any("provider_unavailable" in e for e in outcome.errors)


async def test_max_repairs_respected() -> None:
    repair = RepairSpy("x", "y", "z")
    await OutputValidator(max_repairs=2).validate(
        "lixo", ClassifyOutput, step="classify", repair=repair, fallback=classify_fallback
    )
    assert len(repair.calls) == 2


async def test_without_fallback_raises_output_validation_error() -> None:
    with pytest.raises(OutputValidationError) as info:
        await validator.validate("lixo", ClassifyOutput, step="classify")
    assert info.value.step == "classify"


@settings(max_examples=200)
@given(raw=st.text())
async def test_validate_never_raises_with_fallback(raw: str) -> None:  # OUT-9
    outcome = await validator.validate(
        raw, ClassifyOutput, step="classify", fallback=classify_fallback
    )
    assert isinstance(outcome.value, ClassifyOutput)
