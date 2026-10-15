"""Schema do arquivo YAML de prompt (SPEC-005 §2)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from prompt_engine.config import Tier

SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"


class Technique(StrEnum):
    """Técnicas de prompt engineering reconhecidas (catálogo em docs/PROMPT_ENGINEERING.md §3)."""

    ROLE_PROMPTING = "role_prompting"
    FEW_SHOT = "few_shot"
    STRUCTURED_OUTPUT = "structured_output"
    HIDDEN_REASONING = "hidden_reasoning"
    PROMPT_CHAINING = "prompt_chaining"
    RAG_CITATION = "rag_citation"
    SELF_CRITIQUE = "self_critique"
    SPOTLIGHTING = "spotlighting"
    DELIMITERS = "delimiters"
    CANARY_TOKEN = "canary_token"  # noqa: S105 - nome de técnica, não segredo
    ERROR_FEEDBACK = "error_feedback"
    LLM_AS_JUDGE = "llm_as_judge"
    RUBRIC_SCORING = "rubric_scoring"
    NEGATIVE_CONSTRAINTS = "negative_constraints"
    TEMPERATURE_CONTROL = "temperature_control"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GenerationParams(_Strict):
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(gt=0)


class FewShot(_Strict):
    user: str
    assistant: str


class PromptTemplate(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    version: str = Field(pattern=SEMVER_PATTERN)
    description: str = Field(min_length=1)
    owner: str | None = None
    techniques: list[Technique] = Field(min_length=1)
    tier: Tier
    params: GenerationParams
    output_schema: str | None
    input_variables: list[str]
    system: str
    user: str
    few_shots: list[FewShot] = []
    changelog: list[str] = []

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def version_key(self) -> tuple[int, int, int]:
        major, minor, patch = (int(part) for part in self.version.split("."))
        return major, minor, patch
