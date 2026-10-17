"""ProviderPool: escolhe provider/modelo por tier e aplica timeout, retry com backoff e fallback.

Os adapters não fazem retry: toda a política de resiliência fica aqui (SPEC-006 §4).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field

from prompt_engine.config import ProviderName, Settings, Tier
from prompt_engine.core.errors import ProviderConfigError, ProviderError, ProviderUnavailableError
from prompt_engine.llm.base import LLMProvider, LLMRequest, LLMResponse

TIER_ORDER: tuple[Tier, ...] = ("fast", "balanced", "powerful")
# Se o modelo do tier pedido estiver indisponível: primeiro sobe (qualidade), depois desce (custo).
MODEL_FALLBACK_ORDER: dict[Tier, tuple[Tier, ...]] = {
    "fast": ("balanced",),
    "balanced": ("powerful", "fast"),
    "powerful": ("balanced",),
}
BASE_DELAY_S = 0.5
MAX_DELAY_S = 30.0

logger = logging.getLogger("prompt_engine.llm")
JITTER_S = 0.25


@dataclass(frozen=True)
class Target:
    provider: ProviderName
    model: str
    tier: Tier


@dataclass(frozen=True)
class PoolResult:
    response: LLMResponse
    attempts: list[str] = field(default_factory=list)  # ex.: ["gemini:rate_limited", "gemini:ok"]


class ProviderPool:
    def __init__(
        self,
        providers: MutableMapping[str, LLMProvider],
        settings: Settings,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.providers = providers
        self.settings = settings
        self._sleep = sleep
        self._rng = rng

    def resolve(self, tier: Tier, provider: ProviderName | None = None) -> Target:
        """Modelo do tier pedido; se não configurado, desce para o tier inferior disponível."""
        name = provider or self.settings.llm_provider
        if name not in self.providers:
            raise ProviderConfigError(f"provider '{name}' não está habilitado")
        for candidate in reversed(TIER_ORDER[: TIER_ORDER.index(tier) + 1]):
            model = (
                f"fake-{candidate}" if name == "fake" else self.settings.model_for(name, candidate)
            )
            if model:
                return Target(name, model, candidate)
        raise ProviderConfigError(f"nenhum modelo configurado para '{name}' até o tier '{tier}'")

    async def generate(self, target: Target, request: LLMRequest) -> PoolResult:
        attempts: list[str] = []
        last_error: ProviderError | None = None
        for candidate in self._candidates(target):
            provider = self.providers[candidate.provider]
            scoped = request.model_copy(update={"model": candidate.model})
            for attempt in range(self.settings.llm_max_retries + 1):
                started = time.perf_counter()
                try:
                    response = await asyncio.wait_for(
                        provider.generate(scoped), timeout=self.settings.llm_timeout_s
                    )
                except TimeoutError:
                    last_error = ProviderUnavailableError(candidate.provider, "timeout")
                except ProviderError as exc:
                    last_error = exc
                else:
                    attempts.append(f"{self._label(candidate, target)}:ok")
                    self._log(candidate, request, "ok", started)
                    return PoolResult(response=response, attempts=attempts)

                attempts.append(f"{self._label(candidate, target)}:{last_error.code}")
                self._log(candidate, request, last_error.code, started)
                if not last_error.retryable:
                    raise last_error  # bug nosso (400): nem retry nem fallback
                if attempt < self.settings.llm_max_retries:
                    await self._sleep(self._delay(attempt, last_error))

        if last_error is None:  # pragma: no cover - sempre há ao menos um candidato
            raise ProviderConfigError("nenhum provider candidato")
        raise last_error

    @staticmethod
    def _log(candidate: Target, request: LLMRequest, outcome: str, started: float) -> None:
        level = logging.INFO if outcome == "ok" else logging.WARNING
        logger.log(
            level,
            "llm.call",
            extra={
                "step": request.metadata.get("step"),
                "provider": candidate.provider,
                "model": candidate.model,
                "tier": candidate.tier,
                "outcome": outcome,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            },
        )

    @staticmethod
    def _label(candidate: Target, target: Target) -> str:
        """`gemini[modelo]` só no fallback de modelo do MESMO provider; senão, só o provider."""
        model_fallback = candidate.provider == target.provider and candidate.model != target.model
        return f"{candidate.provider}[{candidate.model}]" if model_fallback else candidate.provider

    def _candidates(self, target: Target) -> list[Target]:
        """Target pedido -> outros modelos do mesmo provider -> outros providers (mesmo tier)."""
        candidates = [target]
        if self.settings.llm_model_fallback:
            for tier in MODEL_FALLBACK_ORDER[target.tier]:
                model = (
                    self.settings.model_for(target.provider, tier)
                    if target.provider != "fake"
                    else None
                )
                if model and all(c.model != model for c in candidates):
                    candidates.append(Target(target.provider, model, tier))
        for name in self.settings.llm_fallback_providers:
            if name == target.provider or name not in self.providers:
                continue
            try:
                candidates.append(self.resolve(target.tier, name))
            except ProviderConfigError:
                continue
        return candidates

    def _delay(self, attempt: int, error: ProviderError) -> float:
        retry_after = getattr(error, "retry_after", None)
        if retry_after is not None:
            return min(float(retry_after), MAX_DELAY_S)
        return min(BASE_DELAY_S * 2.0**attempt + self._rng() * JITTER_S, MAX_DELAY_S)
