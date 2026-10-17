"""Formato do golden dataset (SPEC-009 §3): JSONL, uma linha por caso."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from prompt_engine.output.schemas import Category, EntityKey, Urgency


class GoldenExpected(BaseModel):
    """Campos parciais: só os presentes são avaliados."""

    model_config = ConfigDict(extra="forbid")

    category: Category | None = None
    urgency: Urgency | None = None
    needs_human: bool | None = None
    entities: dict[EntityKey, str] | None = None


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    input: str
    expected: GoldenExpected
    tags: list[str] = []
    reviewed: bool = False
    source: str = "manual"


def load_cases(path: Path) -> list[GoldenCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [GoldenCase.model_validate_json(line) for line in lines if line.strip()]


def dump_cases(path: Path, cases: Iterable[GoldenCase]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            payload = case.model_dump(mode="json", exclude_none=True)
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            count += 1
    return count
