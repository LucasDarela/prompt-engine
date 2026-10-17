"""Estimativa de custo por modelo (SPEC-008 §5). Preços vêm de arquivo, nunca do código."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from prompt_engine.llm.base import Usage

PRICING_FILE = Path(__file__).parent / "pricing.yaml"
PER_MILLION = 1_000_000


class Pricing:
    def __init__(self, prices: Mapping[str, tuple[float, float]]) -> None:
        self.prices = dict(prices)  # modelo -> (input por 1M, output por 1M)

    def cost(self, model: str, usage: Usage) -> float | None:
        price = self.prices.get(model)
        if price is None:
            return None
        input_price, output_price = price
        return (usage.input_tokens * input_price + usage.output_tokens * output_price) / PER_MILLION


def load_pricing(path: Path = PRICING_FILE) -> Pricing:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models = data.get("models") or {}
    return Pricing(
        {
            str(model): (float(p["input_per_1m"]), float(p["output_per_1m"]))
            for model, p in models.items()
        }
    )
