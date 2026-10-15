"""InputValidation (stage 1): normaliza o texto e recusa entradas sem conteúdo (SPEC-003 §2)."""

from __future__ import annotations

import re
import unicodedata

from prompt_engine.config import Settings
from prompt_engine.core.context import PipelineContext
from prompt_engine.core.errors import InputValidationError
from prompt_engine.core.stage import Stage

_KEEP_CONTROL = {"\n", "\t"}
_SPACES_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_WORD_RE = re.compile(r"\w+")
_FOREIGN_SCRIPTS = ("CYRILLIC", "GREEK")
FOREIGN_LETTERS_MAX_RATIO = 0.6


def normalize_text(text: str) -> str:
    """NFKC, remove invisíveis/controle (zero-width, bidi), colapsa espaços e linhas em branco."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(
        ch
        for ch in text
        if ch in _KEEP_CONTROL or unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cn")
    )
    lines = [_SPACES_RE.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _script(ch: str) -> str:
    name = unicodedata.name(ch, "")
    for script in ("LATIN", *_FOREIGN_SCRIPTS):
        if name.startswith(script):
            return script
    return "OTHER"


def has_suspicious_charset(text: str) -> bool:
    """Palavra misturando latino e cirílico/grego (homóglifos) ou maioria de letras estrangeiras."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    foreign = sum(1 for ch in letters if _script(ch) in _FOREIGN_SCRIPTS)
    if foreign / len(letters) > FOREIGN_LETTERS_MAX_RATIO:
        return True
    for word in _WORD_RE.findall(text):
        scripts = {_script(ch) for ch in word if ch.isalpha()}
        if "LATIN" in scripts and scripts & set(_FOREIGN_SCRIPTS):
            return True
    return False


class InputValidation(Stage):
    name = "input_validation"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        text = normalize_text(ctx.raw_input)
        if not text:
            raise InputValidationError("empty_input")
        if len(text) > self.settings.max_input_chars:
            raise InputValidationError("input_too_long")
        if sum(1 for ch in text if ch.isalnum()) < 2:
            raise InputValidationError("no_content")
        ctx = ctx.evolve(raw_input=text)
        if has_suspicious_charset(text):
            ctx = ctx.with_warning("suspicious_charset")
        return ctx
