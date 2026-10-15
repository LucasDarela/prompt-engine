import pytest

from prompt_engine.output.normalize import normalize_enums
from prompt_engine.output.schemas import ClassifyOutput, DraftOutput


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("category", "Cobrança", "billing"),  # OUT-4
        ("category", " BILLING ", "billing"),
        ("category", "entrega", "shipping"),
        ("category", "Jurídico", "legal"),
        ("urgency", "Urgente", "high"),
        ("urgency", "média", "medium"),
        ("urgency", "Crítica", "critical"),
        ("sentiment", "very negative", "very_negative"),
        ("sentiment", "Muito negativo", "very_negative"),
        ("sentiment", "neutro", "neutral"),
        ("complexity_hint", "Complexo", "complex"),
    ],
)
def test_aliases_are_normalized(field: str, raw: str, expected: str) -> None:
    assert normalize_enums({field: raw}, ClassifyOutput)[field] == expected


def test_unknown_values_are_left_for_validation_to_reject() -> None:
    assert normalize_enums({"category": "vendas"}, ClassifyOutput)["category"] == "vendas"


def test_non_enum_fields_and_non_strings_untouched() -> None:
    data = {"reasoning": "Cobrança", "intents": ["x"], "category": 3}
    assert normalize_enums(data, ClassifyOutput) == data


def test_models_without_enums_are_untouched() -> None:
    data = {"summary": "Urgente", "needs_human": True}
    assert normalize_enums(data, DraftOutput) == data


def test_input_dict_is_not_mutated() -> None:
    data = {"category": "Cobrança"}
    normalize_enums(data, ClassifyOutput)
    assert data == {"category": "Cobrança"}
