"""ProviderPool: escolhe provider/modelo por tier e aplica timeout, retry com backoff e fallback.

Os adapters não fazem retry: toda a política de resiliência fica aqui (SPEC-006 §4).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field

from prompt_engine.config import ProviderName, Settings, Tier
from prompt_engine.core.errors import ProviderConfigError, ProviderError, ProviderUnavailableError
from prompt_engine.llm.base import LLMProvider, LLMRequest, LLMResponse

TIER_ORDER: tuple[Tier, ...] = ("fast", "balanced", "powerful")
BASE_DELAY_S = 0.5
MAX_DELAY_S = 30.0
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
                try:
                    response = await asyncio.wait_for(
                        provider.generate(scoped), timeout=self.settings.llm_timeout_s
                    )
                except TimeoutError:
                    last_error = ProviderUnavailableError(candidate.provider, "timeout")
                except ProviderError as exc:
                    last_error = exc
                else:
                    attempts.append(f"{candidate.provider}:ok")
                    return PoolResult(response=response, attempts=attempts)

                attempts.append(f"{candidate.provider}:{last_error.code}")
                if not last_error.retryable:
                    raise last_error  # bug nosso (400): nem retry nem fallback
                if attempt < self.settings.llm_max_retries:
                    await self._sleep(self._delay(attempt, last_error))

        if last_error is None:  # pragma: no cover - sempre há ao menos um candidato
            raise ProviderConfigError("nenhum provider candidato")
        raise last_error

    def _candidates(self, target: Target) -> list[Target]:
        """Target pedido + providers de fallback, no mesmo tier."""
        candidates = [target]
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
