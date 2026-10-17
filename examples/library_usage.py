"""Uso como BIBLIOTECA (sem a API HTTP): monta as dependências e roda o flow direto.

python examples/library_usage.py "Fomos cobrados duas vezes. CPF 529.982.247-25"
python examples/library_usage.py --offline "..."   # sem chave: FakeProvider roteirizado
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from prompt_engine.config import Settings
from prompt_engine.flows.deps import build_deps
from prompt_engine.flows.registry import FlowRunner, default_flow_registry
from prompt_engine.llm.fake import FakeProvider

DEFAULT_MESSAGE = "Fomos cobrados duas vezes na mensalidade de outubro. CPF 529.982.247-25."

# Respostas fixas para demonstrar o pipeline sem rede (cada step recebe o JSON do seu schema).
OFFLINE_SCRIPT = {
    "classify": json.dumps(
        {
            "reasoning": "Cobrança em duplicidade da mensalidade.",
            "category": "billing",
            "urgency": "high",
            "sentiment": "negative",
            "entities": {},
            "intents": ["billing_dispute"],
            "complexity_hint": "simple",
        }
    ),
    "draft_reply": json.dumps(
        {
            "summary": "Cliente [CPF_1] relata cobrança em duplicidade da mensalidade.",
            "suggested_reply": "Olá! Sentimos muito pelo transtorno. Para que o nosso time "
            "financeiro analise o caso do CPF [CPF_1], pode nos enviar os comprovantes das duas "
            "cobranças? A sua solicitação será encaminhada para análise.",
            "needs_human": True,
            "confidence": 0.9,
            "used_kb_ids": ["kb-billing-001"],
        }
    ),
    "review_reply": json.dumps({"approved": True, "issues": [], "revised_reply": None}),
}


async def run(message: str, *, offline: bool) -> dict[str, object]:
    if offline:
        settings = Settings(_env_file=None, llm_provider="fake", obs_enabled=False)  # type: ignore[call-arg]
        deps = build_deps(settings, provider_overrides={"fake": FakeProvider(dict(OFFLINE_SCRIPT))})
    else:
        deps = build_deps(Settings())
    runner = FlowRunner(default_flow_registry(), deps)
    ctx = await runner.run("support_triage", message)
    return {
        "triage": ctx.result,
        "route": ctx.route.model_dump() if ctx.route else None,
        "pii_redacted": ctx.pii_vault.kinds(),
        "prompt_sent_to_llm": ctx.sanitized_input,  # o que o modelo viu: PII mascarada
        "steps": {
            name: f"{s.prompt_id}@{s.prompt_version} ({s.model})" for name, s in ctx.steps.items()
        },
        "warnings": ctx.warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="*")
    parser.add_argument("--offline", action="store_true", help="usa o FakeProvider (sem chave)")
    args = parser.parse_args(argv)
    result = asyncio.run(run(" ".join(args.message) or DEFAULT_MESSAGE, offline=args.offline))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
