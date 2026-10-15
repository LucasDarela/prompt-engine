"""Camada A do InjectionGuard: padrões ponderados sobre texto dobrado e base64 (SPEC-003 §3)."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

PATTERNS_FILE = Path(__file__).parent / "patterns.yaml"

# Homóglifos cirílicos/gregos mais usados para disfarçar letras latinas.
_HOMOGLYPHS = str.maketrans(
    "АВЕКМНОРСТХІЈЅаеорсухіјѕԁΑΒΕΖΗΙΚΜΝΟΡΤΥΧαιονρτυχ",
    "abekmhopctxijsaeopcyxijsdabezhikmnoptyxaiovptux",
)
_LEET = str.maketrans("0134579@$", "oieastgas")
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")
ENCODED_PAYLOAD_WEIGHT = 0.3
PRINTABLE_MIN_RATIO = 0.9


def fold_for_detection(text: str) -> str:
    """Cópia só para detecção: minúsculas, sem acentos, sem homóglifos e sem leetspeak."""
    text = unicodedata.normalize("NFKC", text).translate(_HOMOGLYPHS).lower()
    decomposed = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return text.translate(_LEET)


def combine(weights: Iterable[float]) -> float:
    """OU probabilístico: 1 - Π(1 - w). Monotônico e limitado a [0, 1]."""
    remaining = 1.0
    for weight in weights:
        remaining *= 1.0 - weight
    return 1.0 - remaining


@dataclass(frozen=True)
class Pattern:
    id: str
    weight: float
    regexes: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class ScanResult:
    score: float
    signals: list[str]


def _decode_text(candidate: str) -> str | None:
    try:
        raw = base64.b64decode(candidate, validate=True)
        decoded = raw.decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    printable = sum(1 for ch in decoded if ch.isprintable() or ch.isspace())
    return decoded if decoded and printable / len(decoded) >= PRINTABLE_MIN_RATIO else None


class InjectionHeuristics:
    def __init__(self, patterns: list[Pattern]) -> None:
        self.patterns = patterns

    @classmethod
    def default(cls) -> InjectionHeuristics:
        return cls.from_yaml(PATTERNS_FILE)

    @classmethod
    def from_yaml(cls, path: Path) -> InjectionHeuristics:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        patterns = [
            Pattern(
                id=item["id"],
                weight=float(item["weight"]),
                regexes=tuple(re.compile(p) for p in item["patterns"]),
            )
            for item in data
        ]
        return cls(patterns)

    def _matching(self, folded: str) -> list[Pattern]:
        return [p for p in self.patterns if any(r.search(folded) for r in p.regexes)]

    def scan(self, text: str, extra_signals: Mapping[str, float] | None = None) -> ScanResult:
        hits: dict[str, float] = {p.id: p.weight for p in self._matching(fold_for_detection(text))}

        for candidate in _BASE64_RE.findall(text):
            decoded = _decode_text(candidate)
            if decoded is None:
                continue
            hits["encoded_payload"] = ENCODED_PAYLOAD_WEIGHT
            for pattern in self._matching(fold_for_detection(decoded)):
                hits[f"b64:{pattern.id}"] = pattern.weight

        hits.update(extra_signals or {})
        return ScanResult(score=combine(hits.values()), signals=list(hits))
