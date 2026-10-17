"""Os exemplos da pasta examples/ continuam funcionando (modo offline, sem chave)."""

import json
import runpy
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parents[2] / "examples"


def test_library_usage_offline(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["library_usage.py", "--offline", "Cobrados 2x. CPF 529.982.247-25"]
    )
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(EXAMPLES / "library_usage.py"), run_name="__main__")
    assert exit_info.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["triage"]["category"] == "billing"
    assert result["pii_redacted"] == ["CPF"]
    assert "529.982.247-25" not in result["prompt_sent_to_llm"]
    assert "529.982.247-25" in result["triage"]["suggested_reply"]  # reidratado só para o cliente


def test_frontend_never_uses_inner_html() -> None:
    html = (EXAMPLES / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "innerHTML" not in html.replace("nunca innerHTML", "")
    assert "textContent" in html
