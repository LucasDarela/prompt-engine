"""PromptRegistry: carrega, valida e resolve versões da biblioteca de prompts (SPEC-005 §4)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from prompt_engine.core.errors import PromptLibraryError, PromptNotFoundError
from prompt_engine.prompts.models import PromptTemplate

LIBRARY_PATH = Path(__file__).parent / "library"
PARTIALS_DIR = "_partials"


def _format_validation(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
    )


class PromptRegistry:
    def __init__(
        self,
        root: Path,
        templates: Mapping[str, Mapping[str, PromptTemplate]],
        schemas: Mapping[str, type[BaseModel]],
    ) -> None:
        self.root = root
        self._templates = templates
        self._schemas = schemas

    @classmethod
    def default(cls) -> PromptRegistry:
        """Biblioteca embarcada no pacote, com os schemas de output/schemas.py."""
        from prompt_engine.output.schemas import SCHEMAS

        return cls.from_directory(LIBRARY_PATH, SCHEMAS)

    @classmethod
    def from_directory(cls, root: Path, schemas: Mapping[str, type[BaseModel]]) -> PromptRegistry:
        templates: dict[str, dict[str, PromptTemplate]] = {}
        for path in sorted(root.rglob("*.yaml")):
            if PARTIALS_DIR in path.relative_to(root).parts:
                continue
            template = cls._load_file(path, schemas)
            versions = templates.setdefault(template.id, {})
            if template.version in versions:
                raise PromptLibraryError(f"{path}: prompt duplicado {template.ref}")
            versions[template.version] = template
        if not templates:
            raise PromptLibraryError(f"{root}: nenhum prompt encontrado")
        return cls(root, templates, schemas)

    @staticmethod
    def _load_file(path: Path, schemas: Mapping[str, type[BaseModel]]) -> PromptTemplate:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise PromptLibraryError(f"{path}: YAML inválido: {exc}") from exc
        try:
            template = PromptTemplate.model_validate(data)
        except ValidationError as exc:
            raise PromptLibraryError(f"{path}: {_format_validation(exc)}") from exc
        if template.output_schema is not None and template.output_schema not in schemas:
            raise PromptLibraryError(
                f"{path}: output_schema: '{template.output_schema}' não existe em output/schemas.py"
            )
        return template

    def get(self, ref: str) -> PromptTemplate:
        """Resolve 'id@1.2.3', 'id@latest' ou 'id' (= latest)."""
        prompt_id, _, version = ref.partition("@")
        versions = self._templates.get(prompt_id)
        if not versions:
            raise PromptNotFoundError(ref)
        if version in ("", "latest"):
            return max(versions.values(), key=lambda t: t.version_key)
        if version not in versions:
            raise PromptNotFoundError(ref)
        return versions[version]

    def list(self) -> list[PromptTemplate]:
        return sorted(
            (t for versions in self._templates.values() for t in versions.values()),
            key=lambda t: (t.id, t.version_key),
        )

    def schema_for(self, template: PromptTemplate) -> type[BaseModel] | None:
        return self._schemas[template.output_schema] if template.output_schema else None
