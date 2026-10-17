"""Ingestão determinística de conhecimento a partir do repositório do produto (SPEC-004 §5).

    python -m prompt_engine.enrichment.ingest --repo ../chopp-hub \\
        --config data/kb_sources.yaml --out data/knowledge_base/generated

- Lê SÓ os arquivos listados em `kb_sources.yaml` (lista permitida, voltada ao cliente).
- Nada sai da máquina: extração por parsing local, sem LLM.
- Trechos com cara de segredo são descartados; tudo é gravado com `reviewed: false` e a origem
  `repo@commit:arquivo`. Re-ingerir mantém `reviewed: true` se o conteúdo não mudou.
"""

from __future__ import annotations

import argparse
import ast
import html
import re
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from prompt_engine.enrichment.text_checks import find_internal_terms

DEFAULT_MAX_CHARS = 900

# --- modelo --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RawSnippet:
    title: str
    content: str
    anchor: str


@dataclass
class IngestReport:
    written: int = 0
    needs_review: int = 0
    dropped_secrets: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


def slugify(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:60] or "item"


# --- segredos ------------------------------------------------------------------------------------

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "stripe_key": re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "assignment": re.compile(
        r"(?i)\b[\w-]*(?:api[_-]?key|secret|password|senha|token|service_role_key)[\w-]*"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{8,}"
    ),
}


def find_secrets(text: str) -> list[str]:
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


# --- utilidades de JS/JSX ------------------------------------------------------------------------

_STRING_START = {'"', "'", "`"}


def strip_js_comments(source: str) -> str:
    """Remove // e /* */ fora de strings (uma URL "https://..." não é comentário)."""
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch in _STRING_START:
            j = i + 1
            while j < n and source[j] != ch:
                j += 2 if source[j] == "\\" else 1
            out.append(source[i : j + 1])
            i = j + 1
        elif source.startswith("//", i):
            i = source.find("\n", i) if "\n" in source[i:] else n
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _unquote(literal: str) -> str:
    if literal.startswith("`"):
        return literal[1:-1]
    value = ast.literal_eval(literal)
    return str(value)


_TOKEN_RE = re.compile(
    r"\s+|(?P<str>\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)"
    r"|(?P<ident>[A-Za-z_$][\w$]*)|(?P<punct>.)",
    re.S,
)


def _clean_inline(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


# --- extratores ----------------------------------------------------------------------------------


def extract_ts_objects(source: str, *, array: str) -> list[RawSnippet]:
    """Array de objetos TS: um snippet por objeto raiz; `title`/`description` dos filhos = itens."""
    code = strip_js_comments(source)
    start = re.search(rf"\b(?:const|let|var)\s+{re.escape(array)}\b[^=]*=\s*\[", code)
    if not start:
        raise ValueError(f"array '{array}' não encontrado")

    roots: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    depth = 0  # profundidade de [ { ( dentro do array
    tokens = [m for m in _TOKEN_RE.finditer(code, start.end()) if not m.group(0).isspace()]
    for i, token in enumerate(tokens):
        text = token.group(0)
        if text in "[(":
            depth += 1
        elif text in ")]":
            if depth == 0:
                break  # fim do array
            depth -= 1
        elif text == "{":
            obj: dict[str, Any] = {"children": []}
            if not stack:
                roots.append(obj)
            else:
                roots[-1]["children"].append(obj)
            stack.append(obj)
            depth += 1
        elif text == "}":
            stack.pop()
            depth -= 1
        elif (
            token.group("ident")
            and stack
            and i + 2 < len(tokens)
            and tokens[i + 1].group(0) == ":"
            and tokens[i + 2].group("str")
        ):
            stack[-1][text] = _unquote(tokens[i + 2].group("str"))

    snippets = []
    for root in roots:
        title = root.get("title")
        if not title:
            continue
        lines = [root["description"]] if root.get("description") else []
        for child in root["children"]:
            if child.get("title"):
                desc = f": {child['description']}" if child.get("description") else ""
                lines.append(f"- {child['title']}{desc}")
        snippets.append(
            RawSnippet(title=title, content="\n".join(lines), anchor=root.get("id") or title)
        )
    return snippets


_JSX_COMMENT_RE = re.compile(r"\{/\*.*?\*/\}", re.S)
_BULLETS_RE = re.compile(r"<BulletList\s+items=\{\[(?P<items>.*?)\]\}\s*/>", re.S)
_ITEM_RE = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|<>(.*?)</>", re.S)
_BLOCK_TAG_RE = re.compile(r"</?(?:p|li|ul|ol|div|section|h[1-6])\b[^>]*>|<br\s*/?>", re.I)


def jsx_to_text(fragment: str) -> str:
    """Texto legível de um trecho JSX: listas viram '- item', marcação e expressões somem."""
    text = _JSX_COMMENT_RE.sub(" ", fragment)
    text = re.sub(r"\{\s*([\"'])\s*\1\s*\}|\{\s*\"\s+\"\s*\}", " ", text)  # {" "}
    text = re.sub(r"\s+", " ", text)

    def bullets(match: re.Match[str]) -> str:
        items = []
        for item in _ITEM_RE.finditer(match.group("items")):
            value = item.group(1) if item.group(1) is not None else item.group(2)
            items.append("\n- " + value.replace('\\"', '"'))
        return "".join(items) + "\n"

    text = _BULLETS_RE.sub(bullets, text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"\{[^{}]*\}", " ", text)
    text = html.unescape(text)
    lines = [_clean_inline(line) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def extract_jsx_accordion(source: str) -> list[RawSnippet]:
    source = _JSX_COMMENT_RE.sub(" ", source)
    snippets = []
    for item in re.finditer(
        r"<AccordionItem\s+value=\"([^\"]*)\"[^>]*>(.*?)</AccordionItem>", source, re.S
    ):
        trigger = re.search(r"<AccordionTrigger[^>]*>(.*?)</AccordionTrigger>", item.group(2), re.S)
        content = re.search(r"<AccordionContent[^>]*>(.*?)</AccordionContent>", item.group(2), re.S)
        if trigger and content:
            snippets.append(
                RawSnippet(
                    title=jsx_to_text(trigger.group(1)),
                    content=jsx_to_text(content.group(1)),
                    anchor=item.group(1),
                )
            )
    return snippets


def extract_jsx_sections(source: str, *, components: Sequence[str]) -> list[RawSnippet]:
    found: list[tuple[int, RawSnippet]] = []
    for component in components:
        pattern = rf"<{component}\s+title=\"([^\"]*)\"\s*>(.*?)</{component}>"
        for match in re.finditer(pattern, source, re.S):
            title = html.unescape(match.group(1))
            snippet = RawSnippet(title=title, content=jsx_to_text(match.group(2)), anchor=title)
            found.append((match.start(), snippet))
    return [snippet for _, snippet in sorted(found, key=lambda item: item[0])]


def extract_markdown_sections(
    source: str,
    *,
    skip_headings: Sequence[str] = (),
    include_headings: Sequence[str] = (),
) -> list[RawSnippet]:
    """Um snippet por seção '## '; subseções entram na seção pai; tabelas e código saem.

    `include_headings` (lista permitida) é mais seguro que `skip_headings` para docs internos:
    uma seção interna nova não entra por acidente.
    """
    doc_title = ""
    sections: list[tuple[str, list[str]]] = []
    in_code = False
    for line in source.splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if line.startswith("# ") and not doc_title:
            doc_title = re.split(r"\s+[—-]\s+", line[2:].strip())[0]
        elif line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections and not line.lstrip().startswith("|"):
            sections[-1][1].append(re.sub(r"^#{3,6}\s+(.*)$", r"\1:", line).replace("`", ""))

    skip = [re.compile(pattern, re.I) for pattern in skip_headings]
    include = [re.compile(pattern, re.I) for pattern in include_headings]
    snippets = []
    for heading, lines in sections:
        if any(p.search(heading) for p in skip):
            continue
        if include and not any(p.search(heading) for p in include):
            continue
        content = _reflow_markdown(lines).replace("**", "")
        if content:
            title = f"{doc_title}: {heading}" if doc_title else heading
            snippets.append(RawSnippet(title=title, content=content, anchor=heading))
    return snippets


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _reflow_markdown(lines: list[str]) -> str:
    """Junta linhas quebradas por largura: um parágrafo ou item de lista por linha."""
    blocks: list[str] = []
    current = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            if current:
                blocks.append(current)
            current = ""
        elif _LIST_ITEM_RE.match(raw) or (line.endswith(":") and not current):
            if current:
                blocks.append(current)
            current = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "- ", line)
        else:
            current = f"{current} {line}" if current else line
    if current:
        blocks.append(current)
    return "\n".join(blocks)


EXTRACTORS: dict[str, Callable[..., list[RawSnippet]]] = {
    "ts_objects": extract_ts_objects,
    "jsx_accordion": extract_jsx_accordion,
    "jsx_sections": extract_jsx_sections,
    "markdown_sections": extract_markdown_sections,
}


# --- chunking ------------------------------------------------------------------------------------


def chunk(snippet: RawSnippet, max_chars: int = DEFAULT_MAX_CHARS) -> list[RawSnippet]:
    """Divide conteúdo longo em frases/linhas, para cada trecho ser recuperável sozinho."""
    if len(snippet.content) <= max_chars:
        return [snippet]
    units = [u for u in re.split(r"(?<=[.!?])\s+|\n", snippet.content) if u.strip()]
    parts: list[str] = []
    current = ""
    for unit in units:
        while len(unit) > max_chars:  # unidade gigante: corta por palavras
            cut = unit.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            parts.extend([current] if current else [])
            current = ""
            parts.append(unit[:cut].strip())
            unit = unit[cut:].strip()
        candidate = f"{current} {unit}".strip() if current else unit
        if len(candidate) > max_chars:
            parts.append(current)
            current = unit
        else:
            current = candidate
    parts.extend([current] if current else [])
    return [
        RawSnippet(
            title=f"{snippet.title} (parte {i})", content=part, anchor=f"{snippet.anchor}-{i}"
        )
        for i, part in enumerate(parts, 1)
    ]


# --- execução ------------------------------------------------------------------------------------


def _git_commit(repo: Path) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - comando fixo, sem shell
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _category(snippet: RawSnippet, source: dict[str, Any]) -> str:
    """Regras olham só o TÍTULO: palavras soltas no conteúdo geram categorias erradas."""
    for rule in source.get("category_rules", []):
        if re.search(rule["match"], snippet.title, re.I):
            return str(rule["category"])
    return str(source["category"])


def _load_existing(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return {str(entry["id"]): entry for entry in data}


def run_ingest(repo: Path, config: Path, out: Path, *, commit: str | None = None) -> IngestReport:
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    commit = commit or _git_commit(repo)
    max_chars = int(cfg.get("max_chars", DEFAULT_MAX_CHARS))
    report = IngestReport()
    out.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    for source in cfg["sources"]:
        prefix = source["prefix"]
        text = (repo / source["path"]).read_text(encoding="utf-8")
        raw = EXTRACTORS[source["extractor"]](text, **source.get("options", {}))
        existing = _load_existing(out / f"{prefix}.yaml")
        entries: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        for snippet in raw:
            secrets = find_secrets(f"{snippet.title}\n{snippet.content}")
            if secrets:
                report.dropped_secrets.append(f"{prefix}: {snippet.title}")
                continue
            for part in chunk(snippet, max_chars):
                entry_id, n = f"{prefix}-{slugify(part.anchor)}", 2
                while entry_id in used_ids:
                    entry_id, n = f"{prefix}-{slugify(part.anchor)}-{n}", n + 1
                used_ids.add(entry_id)
                previous = existing.get(entry_id, {})
                unchanged = (
                    previous.get("content") == part.content and previous.get("title") == part.title
                )
                reviewed = bool(previous.get("reviewed")) and unchanged
                entries.append(
                    {
                        "id": entry_id,
                        "category": _category(part, source),
                        "title": part.title,
                        "content": part.content,
                        "tags": [prefix],
                        "updated_at": previous.get("updated_at", today) if unchanged else today,
                        "source": f"{cfg['repo']}@{commit}:{source['path']}",
                        "reviewed": reviewed,
                    }
                )
                notes = []
                internal = find_internal_terms(f"{part.title}\n{part.content}")
                if internal:
                    notes.append(
                        "termos internos (remover antes de revisar): " + ", ".join(internal)
                    )
                for stale in cfg.get(
                    "stale_terms", []
                ):  # fatos que o código-fonte ainda não reflete
                    if re.search(stale["match"], f"{part.title}\n{part.content}", re.IGNORECASE):
                        notes.append(str(stale["note"]))
                if notes:
                    entries[-1]["review_notes"] = notes
                report.needs_review += 0 if reviewed else 1
        path = out / f"{prefix}.yaml"
        header = (
            f"# GERADO por prompt_engine.enrichment.ingest a partir de {cfg['repo']}@{commit}.\n"
            "# Revise cada item e marque reviewed: true. Itens não revisados NÃO entram na KB.\n"
        )
        body = yaml.safe_dump(entries, allow_unicode=True, sort_keys=False, width=100)
        path.write_text(header + body, encoding="utf-8")
        report.written += len(entries)
        report.files.append(path)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--repo", type=Path, required=True, help="clone local do repositório")
    parser.add_argument("--config", type=Path, default=Path("data/kb_sources.yaml"))
    parser.add_argument("--out", type=Path, default=Path("data/knowledge_base/generated"))
    parser.add_argument("--commit", help="commit a registrar (padrão: HEAD do --repo)")
    args = parser.parse_args(argv)
    report = run_ingest(args.repo, args.config, args.out, commit=args.commit)
    print(
        f"{report.written} snippets gravados em {args.out} "
        f"({report.needs_review} aguardando revisão; "
        f"{len(report.dropped_secrets)} descartados por possível segredo)"
    )
    for dropped in report.dropped_secrets:
        print(f"  descartado: {dropped}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
