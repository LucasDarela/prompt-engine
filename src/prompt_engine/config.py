"""Configuração tipada da aplicação, carregada de variáveis de ambiente e do `.env`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ProviderName = Literal["gemini", "anthropic", "openai", "fake"]
Tier = Literal["fast", "balanced", "powerful"]
CsvList = Annotated[list[str], NoDecode]

Unit = Annotated[float, Field(ge=0.0, le=1.0)]


class Settings(BaseSettings):
    """Todas as variáveis documentadas em docs/plans/IMPLEMENTATION_PLAN.md (Apêndice A)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # App / API
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"
    api_key: SecretStr | None = None
    cors_origins: CsvList = ["http://localhost:5173", "http://localhost:8000"]
    debug_endpoints: bool = False
    max_input_chars: int = Field(default=4000, gt=0)

    # LLM
    llm_provider: ProviderName = "gemini"
    llm_fallback_providers: Annotated[list[ProviderName], NoDecode] = []
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)

    gemini_api_key: SecretStr | None = None
    gemini_model_fast: str | None = "gemini-3.5-flash-lite"
    gemini_model_balanced: str | None = "gemini-3.7-flash"
    gemini_model_powerful: str | None = "gemini-3.8-flash"

    anthropic_api_key: SecretStr | None = None
    anthropic_model_fast: str | None = "claude-haiku-5-5"
    anthropic_model_balanced: str | None = "claude-sonnet-5-5"
    anthropic_model_powerful: str | None = "claude-opus-5-5"

    openai_api_key: SecretStr | None = None
    openai_model_fast: str | None = None
    openai_model_balanced: str | None = None
    openai_model_powerful: str | None = None

    # Pipeline
    enable_review_step: bool = True
    injection_low: Unit = 0.3
    injection_high: Unit = 0.8
    injection_judge_enabled: bool = True
    pii_rehydrate_reply: bool = True
    confidence_min: Unit = 0.6
    output_max_repairs: int = Field(default=1, ge=0)

    # Enriquecimento
    kb_path: Path = Path("data/knowledge_base")
    kb_include_unreviewed: bool = False  # snippets gerados só entram após revisão humana
    sla_path: Path = Path("data/sla.yaml")
    kb_top_k: int = Field(default=3, gt=0)
    kb_min_score: float = Field(default=0.1, ge=0)
    kb_min_relative_score: Unit = 0.3  # descarta snippets < 30% do melhor (escala do BM25 varia)
    kb_max_chars: int = Field(default=2000, gt=0)

    # Roteamento
    router_t1: Unit = 0.35
    router_t2: Unit = 0.7
    router_force_tier: Tier | None = None

    # Observabilidade
    obs_enabled: bool = True
    obs_db_path: Path = Path("var/traces.db")
    obs_store_raw_input: bool = False
    obs_retention_days: int = Field(default=30, gt=0)

    # Evals
    eval_concurrency: int = Field(default=2, gt=0)
    eval_delay_s: float = Field(default=1.0, ge=0)

    @field_validator("cors_origins", "llm_fallback_providers", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "api_key",
        "router_force_tier",
        "gemini_api_key",
        "gemini_model_fast",
        "gemini_model_balanced",
        "gemini_model_powerful",
        "anthropic_api_key",
        "anthropic_model_fast",
        "anthropic_model_balanced",
        "anthropic_model_powerful",
        "openai_api_key",
        "openai_model_fast",
        "openai_model_balanced",
        "openai_model_powerful",
        mode="before",
    )
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        """`VAR=` no .env significa 'não configurado', não string vazia."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_thresholds(self) -> Self:
        if self.injection_low >= self.injection_high:
            raise ValueError("injection_low deve ser menor que injection_high")
        if self.router_t1 >= self.router_t2:
            raise ValueError("router_t1 deve ser menor que router_t2")
        return self

    def model_for(self, provider: ProviderName, tier: Tier) -> str | None:
        """Modelo configurado para (provider, tier); None se não houver."""
        value: str | None = getattr(self, f"{provider}_model_{tier}", None)
        return value or None
