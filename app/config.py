"""Application settings.

All configuration is read from environment variables (optionally via a local
`.env` file). Secrets are never hard-coded in the source tree.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API, database, rules and Ollama."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application -----------------------------------------------------
    app_name: str = "AI Lead Qualification API"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    api_key: str | None = None
    log_level: str = "INFO"

    # --- database --------------------------------------------------------
    database_url: str = "postgresql+psycopg://lead:lead@localhost:5432/leads"
    auto_create_schema: bool = True

    # --- Ollama ----------------------------------------------------------
    ollama_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_timeout_seconds: float = Field(default=120.0, gt=0)
    ollama_max_tokens: int = Field(default=512, gt=0)
    ollama_max_retries: int = Field(default=2, ge=1, le=5)

    # --- notifications ---------------------------------------------------
    notify_webhook_url: str | None = None
    notify_timeout_seconds: float = Field(default=3.0, gt=0)

    # --- qualification ---------------------------------------------------
    ai_blend_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    qualified_threshold: int = Field(default=70, ge=0, le=100)
    nurture_threshold: int = Field(default=40, ge=0, le=100)
    min_budget: float = Field(default=1000.0, ge=0)

    target_industries: list[str] = Field(
        default_factory=lambda: [
            "saas",
            "software",
            "ecommerce",
            "fintech",
            "healthtech",
            "agency",
            "logistics",
        ]
    )
    target_countries: list[str] = Field(
        default_factory=lambda: [
            "US",
            "GB",
            "CA",
            "AU",
            "IE",
            "DE",
            "FR",
            "NL",
            "CH",
            "SE",
            "SG",
            "AE",
        ]
    )
    free_email_domains: list[str] = Field(
        default_factory=lambda: [
            "gmail.com",
            "yahoo.com",
            "hotmail.com",
            "outlook.com",
            "icloud.com",
            "aol.com",
            "proton.me",
            "protonmail.com",
            "gmx.com",
            "mail.com",
        ]
    )

    @field_validator("ollama_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/") or value

    @field_validator("target_industries", "free_email_domains")
    @classmethod
    def _normalize_lower(cls, value: list[str]) -> list[str]:
        return [item.strip().lower() for item in value if item and item.strip()]

    @field_validator("target_countries")
    @classmethod
    def _normalize_upper(cls, value: list[str]) -> list[str]:
        return [item.strip().upper() for item in value if item and item.strip()]

    @model_validator(mode="after")
    def _check_thresholds(self) -> Settings:
        if self.nurture_threshold >= self.qualified_threshold:
            raise ValueError("NURTURE_THRESHOLD must be lower than QUALIFIED_THRESHOLD")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
