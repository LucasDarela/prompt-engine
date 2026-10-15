from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from prompt_engine.core.errors import PromptLibraryError, PromptNotFoundError
from prompt_engine.prompts.registry import PromptRegistry


class Echo(BaseModel):
    text: str


SCHEMAS: dict[str, type[BaseModel]] = {"Echo": Echo}

BASE: dict[str, Any] = {
    "id": "demo.hello",
    "version": "1.0.0",
    "description": "Prompt de teste.",
    "techniques": ["role_prompting"],
    "tier": "fast",
    "params": {"temperature": 0.0, "max_output_tokens": 100},
    "output_schema": "Echo",
    "input_variables": ["name"],
    "system": "Sistema.",
    "user": "Olá {{ name }}",
}


def write(root: Path, rel: str, **overrides: Any) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(BASE | overrides, allow_unicode=True), encoding="utf-8")
    return path


def load(root: Path) -> PromptRegistry:
    return PromptRegistry.from_directory(root, SCHEMAS)


def test_loads_and_gets_exact_version(tmp_path: Path) -> None:
    write(tmp_path, "demo/hello.yaml")
    t = load(tmp_path).get("demo.hello@1.0.0")
    assert (t.id, t.version, t.ref) == ("demo.hello", "1.0.0", "demo.hello@1.0.0")


def test_latest_uses_semver_not_lexical_order(tmp_path: Path) -> None:  # PL-3
    write(tmp_path, "demo/hello@1.9.0.yaml", version="1.9.0")
    write(tmp_path, "demo/hello.yaml", version="1.10.0")
    reg = load(tmp_path)
    assert reg.get("demo.hello@latest").version == "1.10.0"
    assert reg.get("demo.hello").version == "1.10.0"
    assert reg.get("demo.hello@1.9.0").version == "1.9.0"


@pytest.mark.parametrize("ref", ["nope.prompt", "demo.hello@9.9.9", "demo.hello@x"])
def test_unknown_ref_raises(tmp_path: Path, ref: str) -> None:
    write(tmp_path, "demo/hello.yaml")
    with pytest.raises(PromptNotFoundError):
        load(tmp_path).get(ref)


def test_duplicate_id_and_version_rejected(tmp_path: Path) -> None:  # PL-2
    write(tmp_path, "demo/hello.yaml")
    write(tmp_path, "demo/copy.yaml")
    with pytest.raises(PromptLibraryError, match="duplicad"):
        load(tmp_path)


def test_invalid_field_error_names_file_and_field(tmp_path: Path) -> None:  # PL-1
    write(tmp_path, "demo/hello.yaml", tier="ultra")
    with pytest.raises(PromptLibraryError) as info:
        load(tmp_path)
    assert "hello.yaml" in str(info.value)
    assert "tier" in str(info.value)


def test_yaml_syntax_error_names_file(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("id: [unclosed", encoding="utf-8")
    with pytest.raises(PromptLibraryError, match=r"broken\.yaml"):
        load(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"techniques": []},  # PL-7: ao menos uma técnica
        {"techniques": ["mind_reading"]},  # PL-7: técnica fora da enum
        {"version": "1.0"},
        {"id": "SemPonto"},
        {"output_schema": "Inexistente"},
        {"unknown_key": 1},
        {"params": {"temperature": 3.0, "max_output_tokens": 10}},
    ],
)
def test_invalid_templates_rejected(tmp_path: Path, overrides: dict[str, Any]) -> None:
    write(tmp_path, "demo/hello.yaml", **overrides)
    with pytest.raises(PromptLibraryError):
        load(tmp_path)


def test_output_schema_may_be_null(tmp_path: Path) -> None:
    write(tmp_path, "demo/hello.yaml", output_schema=None)
    reg = load(tmp_path)
    assert reg.schema_for(reg.get("demo.hello")) is None


def test_partials_dir_is_ignored(tmp_path: Path) -> None:
    write(tmp_path, "demo/hello.yaml")
    (tmp_path / "_partials").mkdir()
    (tmp_path / "_partials" / "x.yaml").write_text("não é prompt", encoding="utf-8")
    assert [t.ref for t in load(tmp_path).list()] == ["demo.hello@1.0.0"]


def test_list_is_sorted_and_schema_for(tmp_path: Path) -> None:
    write(tmp_path, "b.yaml", id="demo.zeta")
    write(tmp_path, "a.yaml", id="demo.alpha")
    reg = load(tmp_path)
    assert [t.id for t in reg.list()] == ["demo.alpha", "demo.zeta"]
    assert reg.schema_for(reg.get("demo.alpha")) is Echo
    assert reg.root == tmp_path


def test_empty_library_rejected(tmp_path: Path) -> None:
    with pytest.raises(PromptLibraryError, match="nenhum prompt"):
        load(tmp_path)
