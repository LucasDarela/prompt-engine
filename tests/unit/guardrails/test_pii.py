"""Detectores de PII brasileira, redação e reidratação (SPEC-003 §4)."""

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from prompt_engine.core.context import PipelineContext
from prompt_engine.core.errors import PipelineMisconfigured
from prompt_engine.core.vault import PIIVault
from prompt_engine.guardrails.pii.detectors import is_valid_cnpj, is_valid_cpf, luhn_ok
from prompt_engine.guardrails.pii.redaction import PIIRedaction, redact
from prompt_engine.guardrails.pii.rehydration import PIIRehydration
from tests.support import make_settings

# --- dados sintéticos válidos ------------------------------------------------------


def make_cpf(base9: str) -> str:
    digits = [int(d) for d in base9]
    for size in (9, 10):
        total = sum(d * w for d, w in zip(digits, range(size + 1, 1, -1), strict=True))
        digits.append(0 if total % 11 < 2 else 11 - total % 11)
    return "".join(map(str, digits))


def make_cnpj(base12: str) -> str:
    digits = [int(d) for d in base12]
    for weights in ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]):
        total = sum(d * w for d, w in zip(digits, weights, strict=True))
        digits.append(0 if total % 11 < 2 else 11 - total % 11)
    return "".join(map(str, digits))


def make_card(base15: str) -> str:
    for check in range(10):
        if luhn_ok(base15 + str(check)):
            return base15 + str(check)
    raise AssertionError


CPF = "529.982.247-25"  # válido (exemplo público)
CNPJ = "11.222.333/0001-81"  # válido (exemplo público)
CARD = "4111 1111 1111 1111"  # cartão de teste Visa


# --- validadores ---------------------------------------------------------------------


def test_validators() -> None:
    assert is_valid_cpf("52998224725")
    assert not is_valid_cpf("52998224726")  # dígito errado
    assert not is_valid_cpf("11111111111")  # repetido
    assert is_valid_cnpj("11222333000181")
    assert not is_valid_cnpj("11222333000182")
    assert not is_valid_cpf("52998224715")  # primeiro dígito verificador errado
    assert not is_valid_cpf("123")
    assert not is_valid_cnpj("1122233300018")  # 13 dígitos
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")
    assert not luhn_ok("4111")  # fora da faixa de 13 a 19 dígitos


# --- redact ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Meu CPF é {CPF}.", "Meu CPF é [CPF_1]."),  # GR-5
        ("CPF 52998224725 sem pontos", "CPF [CPF_1] sem pontos"),
        (f"CNPJ {CNPJ}", "CNPJ [CNPJ_1]"),
        ("CNPJ 11222333000181", "CNPJ [CNPJ_1]"),
        (f"cartão {CARD}", "cartão [CARTAO_1]"),  # GR-6
        ("cartão 4111-1111-1111-1111", "cartão [CARTAO_1]"),
        ("mande para joao.silva+nf@distribuidora.com.br", "mande para [EMAIL_1]"),
        ("ligue (11) 98765-4321", "ligue [TELEFONE_1]"),
        ("whats +55 11 98765-4321", "whats [TELEFONE_1]"),
        ("fixo 1132654321", "fixo [TELEFONE_1]"),
        ("CEP: 01310-100", "CEP: [CEP_1]"),
        ("pix 123e4567-e89b-42d3-a456-426614174000", "pix [PIX_1]"),
    ],
)
def test_redacts_each_kind(text: str, expected: str) -> None:
    assert redact(text, PIIVault()) == expected


@pytest.mark.parametrize(
    "text",
    [
        "CPF 529.982.247-26",  # GR-5: dígito verificador inválido
        "cartão 4111 1111 1111 1112",  # GR-6: falha no Luhn
        "pedido PED-48213 de R$ 1.234,56",
        "nota fiscal 000.123.456",
        "rota 12, 40 entregas, 8 barris de 50L",
        "o CEP é importante",
        "protocolo 20261014",
    ],
)
def test_lookalikes_are_not_redacted(text: str) -> None:
    assert redact(text, PIIVault()) == text


def test_same_value_same_placeholder() -> None:  # GR-7
    vault = PIIVault()
    text = redact("a@b.com, depois de novo a@b.com e c@d.com", vault)
    assert text == "[EMAIL_1], depois de novo [EMAIL_1] e [EMAIL_2]"


def test_cnpj_is_not_mistaken_for_card() -> None:
    # 14 dígitos crus cabem na faixa de cartão: CNPJ deve vencer
    assert redact("11222333000181", PIIVault()) == "[CNPJ_1]"


def test_vault_receives_values_and_kinds() -> None:
    vault = PIIVault()
    redact(f"{CPF} e {CNPJ}", vault)
    assert vault.kinds() == ["CNPJ", "CPF"]
    assert vault.restore("[CPF_1]")[0] == CPF


separators = st.text(alphabet="abc xyz.,:;!?-\n", max_size=15)


@given(
    base=st.text(alphabet="0123456789", min_size=9, max_size=9),
    before=separators,
    after=separators,
    formatted=st.booleans(),
)
def test_property_valid_cpf_never_survives(  # GR-8
    base: str, before: str, after: str, formatted: bool
) -> None:
    cpf = make_cpf(base)
    if len(set(cpf)) == 1:
        return
    shown = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if formatted else cpf
    vault = PIIVault()
    out = redact(f"{before} {shown} {after}", vault)
    assert shown not in out
    assert not vault.contains_value_in(out)


@given(base=st.text(alphabet="0123456789", min_size=15, max_size=15), before=separators)
def test_property_valid_card_never_survives(base: str, before: str) -> None:  # GR-8
    card = make_card("4" + base[1:])
    spaced = " ".join(card[i : i + 4] for i in range(0, 16, 4))
    out = redact(f"{before} cartão {spaced} ok", PIIVault())
    assert spaced not in out
    assert card not in out.replace(" ", "")


def test_random_cnpjs_are_redacted() -> None:
    rng = random.Random(42)
    for _ in range(200):
        cnpj = make_cnpj("".join(rng.choice("0123456789") for _ in range(12)))
        if len(set(cnpj)) == 1:
            continue
        assert redact(f"CNPJ {cnpj}.", PIIVault()) == "CNPJ [CNPJ_1]."


# --- stages ------------------------------------------------------------------------


async def test_redaction_stage_sets_sanitized_input() -> None:
    ctx = PipelineContext.new(flow="t", raw_input=f"CPF {CPF}")
    ctx = await PIIRedaction().run(ctx)
    assert ctx.sanitized_input == "CPF [CPF_1]"
    assert ctx.pii_vault.kinds() == ["CPF"]
    assert ctx.raw_input == f"CPF {CPF}"


async def test_rehydration_restores_only_suggested_reply() -> None:  # GR-9
    ctx = PipelineContext.new(flow="t", raw_input=f"CPF {CPF}")
    ctx = await PIIRedaction().run(ctx)
    ctx = ctx.evolve(
        result={
            "summary": "Cliente [CPF_1] cobrado 2x",
            "suggested_reply": "Confirmamos o CPF [CPF_1]. Seu e-mail [EMAIL_9] foi atualizado.",
        }
    )
    ctx = await PIIRehydration(make_settings()).run(ctx)
    assert ctx.result is not None
    assert ctx.result["suggested_reply"] == f"Confirmamos o CPF {CPF}. Seu e-mail foi atualizado."
    assert ctx.result["summary"] == "Cliente [CPF_1] cobrado 2x"
    assert "unknown_placeholder" in ctx.warnings


async def test_rehydration_can_be_disabled() -> None:
    stage = PIIRehydration(make_settings(pii_rehydrate_reply=False))
    assert stage.is_enabled(PipelineContext.new(flow="t", raw_input="x")) is False


async def test_rehydration_requires_result() -> None:
    with pytest.raises(PipelineMisconfigured):
        await PIIRehydration(make_settings()).run(PipelineContext.new(flow="t", raw_input="x"))
