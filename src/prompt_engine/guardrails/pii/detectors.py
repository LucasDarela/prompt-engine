"""Detectores de PII brasileira com validação (SPEC-003 §4).

A ORDEM importa: cada detector vê o texto já com os placeholders dos anteriores.
- EMAIL e PIX primeiro (contêm dígitos que confundiriam os numéricos);
- CNPJ e CPF (dígito verificador) antes do CARTÃO: 14 dígitos crus de CNPJ cabem na faixa de cartão;
- TELEFONE depois do cartão; CEP só com a palavra "CEP" por perto.
Números que casam o padrão mas falham na validação NÃO são mascarados (ex.: número de pedido).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def is_valid_cpf(value: str) -> bool:
    digits = [int(d) for d in _digits(value)]
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(d * w for d, w in zip(digits[:size], range(size + 1, 1, -1), strict=True))
        check = 0 if total % 11 < 2 else 11 - total % 11
        if digits[size] != check:
            return False
    return True


_CNPJ_WEIGHTS = ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])


def is_valid_cnpj(value: str) -> bool:
    digits = [int(d) for d in _digits(value)]
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    for weights in _CNPJ_WEIGHTS:
        size = len(weights)
        total = sum(d * w for d, w in zip(digits[:size], weights, strict=True))
        check = 0 if total % 11 < 2 else 11 - total % 11
        if digits[size] != check:
            return False
    return True


def luhn_ok(value: str) -> bool:
    digits = [int(d) for d in _digits(value)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _phone_ok(value: str) -> bool:
    return 10 <= len(_digits(value)) <= 13


def _always(_: str) -> bool:
    return True


@dataclass(frozen=True)
class Detector:
    kind: str
    pattern: re.Pattern[str]
    validate: Callable[[str], bool] = _always
    group: int = 0  # grupo do regex que contém o valor (o resto do match é preservado)


DEFAULT_DETECTORS: tuple[Detector, ...] = (
    Detector("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    Detector(
        "PIX",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
        ),
    ),
    Detector(
        "CNPJ", re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)"), is_valid_cnpj
    ),
    Detector("CPF", re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)"), is_valid_cpf),
    Detector(
        "CARTAO",
        re.compile(r"(?<!\d)(?:\d{4}[ -]){3}\d{1,7}(?!\d)|(?<!\d)\d{13,19}(?!\d)"),
        luhn_ok,
    ),
    Detector(
        "TELEFONE",
        re.compile(r"(?<![\d+])(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?9?\d{4}[-\s]?\d{4}(?!\d)"),
        _phone_ok,
    ),
    Detector("CEP", re.compile(r"(?i)\b(cep\W{0,3})(\d{5}-?\d{3})(?!\d)"), group=2),
)
