"""PromptRenderer: Jinja2 estrito + spotlighting + schema JSON injetado (SPEC-005 §4)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from prompt_engine.core.errors import TemplateRenderError
from prompt_engine.llm.base import Message
from prompt_engine.prompts.models import PromptTemplate
from prompt_engine.prompts.registry import PromptRegistry

# Delimitadores estruturais dos prompts. Se aparecerem num DADO, viram [tag] e perdem o poder
# de "fechar" a seção de dados (SPEC-003 Camada C).
RESERVED_TAGS = ("user_input", "knowledge_base", "draft", "reply", "invalid_output", "system")
_TAG_RE = re.compile(rf"<\s*(/?)\s*({'|'.join(RESERVED_TAGS)})\s*>", re.IGNORECASE)
AUTO_VARIABLES = frozenset({"schema_json"})


def escape_delimiters(value: Any) -> Any:
    """Aplica o escape recursivamente em strings, listas e dicts."""
    if isinstance(value, str):
        return _TAG_RE.sub(lambda m: f"[{m.group(1)}{m.group(2)}]", value)
    if isinstance(value, Mapping):
        return {k: escape_delimiters(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [escape_delimiters(v) for v in value]
    return value


class PromptRenderer:
    def __init__(self, registry: PromptRegistry) -> None:
        self.registry = registry
        self._env = Environment(
            loader=FileSystemLoader(registry.root),
            undefined=StrictUndefined,
            autoescape=False,  # noqa: S701 - saída é prompt de LLM, não HTML
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,  # parciais mantêm a quebra final: seções separadas
        )

    def render(self, template: PromptTemplate, variables: Mapping[str, Any]) -> list[Message]:
        context = self._build_context(template, variables)
        try:
            system = self._env.from_string(template.system).render(context)
            user = self._env.from_string(template.user).render(context)
        except TemplateError as exc:
            raise TemplateRenderError(template.id, str(exc)) from exc

        messages = [Message(role="system", content=system.strip())]
        for shot in template.few_shots:  # few-shots são literais: não passam pelo Jinja
            messages.append(Message(role="user", content=shot.user.strip()))
            messages.append(Message(role="assistant", content=shot.assistant.strip()))
        messages.append(Message(role="user", content=user.strip()))
        return messages

    def _build_context(
        self, template: PromptTemplate, variables: Mapping[str, Any]
    ) -> dict[str, Any]:
        declared = set(template.input_variables)
        provided = set(variables)
        missing, extra = declared - provided, provided - declared
        if missing or extra:
            raise TemplateRenderError(
                template.id, f"variáveis faltando={sorted(missing)} sobrando={sorted(extra)}"
            )
        context: dict[str, Any] = {k: escape_delimiters(v) for k, v in variables.items()}
        schema = self.registry.schema_for(template)
        if schema is not None:
            if AUTO_VARIABLES & declared:
                raise TemplateRenderError(template.id, "schema_json é injetado automaticamente")
            context["schema_json"] = json.dumps(
                schema.model_json_schema(), ensure_ascii=False, sort_keys=True
            )
        return context
