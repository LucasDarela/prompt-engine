"""Limpeza de saída de LLM: markdown, extração de JSON e parse tolerante (SPEC-007 §3)."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```[a-zA-Z]*[ \t]*\n?(.*?)```", re.DOTALL)
_HEADING_RE = re.compile(r"^\s*#{1,6}\s*")
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"'})


def strip_markdown(text: str) -> str:
    """Remove BOM e cercas de código; devolve o conteúdo da primeira cerca, se houver."""
    text = text.lstrip("﻿").strip()
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text


def _balanced_object_end(text: str, start: int) -> int | None:
    """Índice do '}' que fecha o objeto aberto em `start`, respeitando strings e escapes."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_json(text: str) -> str | None:
    """Primeiro objeto {...} balanceado no texto; None se não houver."""
    start = text.find("{")
    while start != -1:
        end = _balanced_object_end(text, start)
        if end is not None:
            return text[start : end + 1]
        start = text.find("{", start + 1)
    return None


def _drop_trailing_commas(text: str) -> str:
    out: list[str] = []
    in_string = False
    escaped = False
    for i, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == ",":
            rest = text[i + 1 :].lstrip()
            if rest[:1] in ("}", "]"):
                continue
        out.append(char)
    return "".join(out)


def loads_lenient(text: str) -> Any:
    """json.loads com correções triviais (vírgula final, aspas tipográficas). Erro → ValueError."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        pass
    fixed = _drop_trailing_commas(text.translate(_SMART_QUOTES))
    try:
        return json.loads(fixed)
    except RecursionError as exc:
        raise ValueError("JSON aninhado demais") from exc


def clean_text(text: str) -> str:
    """Remove markdown leve e normaliza espaços, preservando parágrafos."""
    lines = []
    for line in text.splitlines():
        line = _HEADING_RE.sub("", line)
        line = line.replace("**", "").replace("__", "").replace("`", "")
        lines.append(re.sub(r"[ \t]+", " ", line).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
