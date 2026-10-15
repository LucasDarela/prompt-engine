import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from prompt_engine.output.cleaners import clean_text, extract_json, loads_lenient, strip_markdown

# --- strip_markdown ----------------------------------------------------------


def test_strip_json_fence() -> None:  # OUT-1
    assert strip_markdown('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_plain_fence_and_bom() -> None:
    assert strip_markdown('﻿```\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_without_fence_is_identity_trimmed() -> None:
    assert strip_markdown('  {"a": 1} ') == '{"a": 1}'


# --- extract_json ------------------------------------------------------------


def test_extract_from_chatty_text() -> None:  # OUT-2
    raw = 'Claro! Aqui está: {"a": 1, "b": [1, 2]} Espero ter ajudado'
    assert extract_json(raw) == '{"a": 1, "b": [1, 2]}'


def test_extract_respects_braces_inside_strings() -> None:  # OUT-3
    raw = 'x {"reply": "use {x} e }", "n": {"k": "\\"}"}} y'
    assert json.loads(extract_json(raw) or "") == {"reply": "use {x} e }", "n": {"k": '"}'}}


def test_extract_returns_none_when_absent_or_unbalanced() -> None:
    assert extract_json("sem json aqui") is None
    assert extract_json('{"a": 1') is None


def test_extract_skips_unbalanced_prefix() -> None:
    assert extract_json('{ quebrado { "a": 1 }') == '{ "a": 1 }'


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=10,
)


@given(
    obj=st.dictionaries(st.text(), json_values, max_size=5),
    prefix=st.text().filter(lambda s: "{" not in s),
    suffix=st.text(),
)
def test_extract_roundtrip_property(obj: dict[str, Any], prefix: str, suffix: str) -> None:
    extracted = extract_json(prefix + json.dumps(obj, ensure_ascii=False) + suffix)
    assert extracted is not None
    assert json.loads(extracted) == obj


# --- loads_lenient -----------------------------------------------------------


def test_lenient_trailing_commas() -> None:
    assert loads_lenient('{"a": [1, 2,], "b": 3,}') == {"a": [1, 2], "b": 3}


def test_lenient_keeps_commas_inside_strings() -> None:
    assert loads_lenient('{"a": "x,}", }') == {"a": "x,}"}


def test_lenient_handles_escaped_quotes_in_strings() -> None:
    assert loads_lenient('{"a": "diz \\"oi,\\" ]", }') == {"a": 'diz "oi," ]'}


def test_lenient_smart_quotes() -> None:
    assert loads_lenient("{“a”: “b”}") == {"a": "b"}


def test_lenient_raises_value_error_on_garbage() -> None:
    with pytest.raises(ValueError):
        loads_lenient("{nada: aqui")


def test_lenient_deep_nesting_is_value_error() -> None:
    with pytest.raises(ValueError):
        loads_lenient("[" * 100_000 + "]" * 100_000)


# --- clean_text --------------------------------------------------------------


def test_clean_text_removes_markdown_and_extra_spaces() -> None:
    raw = "## Olá!\n\n\n**Seu pedido** foi   `enviado`.  "
    assert clean_text(raw) == "Olá!\n\nSeu pedido foi enviado."
