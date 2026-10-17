"""Cliente HTTP da API (precisa do servidor no ar: `make run`).

python examples/python_client.py "O app do motorista não abre e temos 30 entregas hoje"
"""

from __future__ import annotations

import os
import sys

import httpx

BASE_URL = os.environ.get("BASE", "http://localhost:8000")


def process(message: str, *, enable_review: bool = True) -> dict[str, object]:
    headers = {"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {}
    response = httpx.post(
        f"{BASE_URL}/api/v1/process-prompt",
        json={"input": message, "options": {"enable_review": enable_review}},
        headers=headers,
        timeout=120,
    )
    body: dict[str, object] = response.json()
    if response.status_code != 200:
        error = body["error"]
        raise SystemExit(f"[{response.status_code}] {error}  (request_id={body['request_id']})")
    return body


if __name__ == "__main__":
    body = process(" ".join(sys.argv[1:]) or "Qual o horário de atendimento do suporte?")
    triage, meta = body["triage"], body["meta"]
    if not isinstance(triage, dict) or not isinstance(meta, dict):
        raise SystemExit("resposta inesperada da API")
    print(f"request_id: {body['request_id']}")
    print(f"categoria:  {triage['category']} / urgência {triage['urgency']}")
    print(f"humano:     {triage['needs_human']} {triage['escalation_reasons']}")
    print(f"rota:       {meta['route']}")
    print(f"resposta:\n{triage['suggested_reply']}")
