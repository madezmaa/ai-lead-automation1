"""Application settings.

All configuration is read from environment variables (optionally via a local
`.env` file). Secrets are never hard-coded in the source tree.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Local-development default. Production must inject the connection string; the
# app logs a clear warning instead of silently targeting localhost.
DEFAULT_DATABASE_URL = "postgresql+psycopg://lead:lead@localhost:5432/leads"

# Environment variable names a hosting platform may use for the connection
# string. ``DATABASE_URL`` is the documented primary; the others are common
# aliases so a provider that injects the value under a different name still
# connects to the provisioned database.
DATABASE_URL_ENV_VARS = (
    "DATABASE_URL",
    "DATABASE_URI",
    "POSTGRES_URL",
    "POSTGRESQL_URL",
    "DB_URL",
)


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
    # Origins allowed by the browser CORS policy (the static demo runs on :5500).
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5500",
            "http://127.0.0.1:5500",
        ]
    )

    # --- database --------------------------------------------------------
    database_url: str = DEFAULT_DATABASE_URL
    # Production-safe default: the schema must be managed with `alembic upgrade head`.
    # Local development opts in via AUTO_CREATE_SCHEMA=true (.env / docker-compose).
    auto_create_schema: bool = False

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

    # --- speed-to-lead email ---------------------------------------------
    # DRY_RUN=true (default) logs the reply as `skipped` instead of sending it.
    dry_run: bool = True
    email_api_key: str | None = None
    email_from: str = "AI Lead Automation <leads@example.com>"

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

    @model_validator(mode="before")
    @classmethod
    def _resolve_database_url(cls, values: object) -> object:
        """Resolve the connection string from the platform environment.

        An explicit value (constructor argument or ``DATABASE_URL``) always
        wins. Otherwise the conventional aliases are consulted, skipping blank
        values, so a platform that exposes an empty ``DATABASE_URL`` but a
        populated ``POSTGRES_URL`` (or similar) still connects.
        """
        if isinstance(values, dict):
            current = values.get("database_url")
            if not (isinstance(current, str) and current.strip()):
                for name in DATABASE_URL_ENV_VARS:
                    candidate = os.environ.get(name)
                    if candidate and candidate.strip():
                        values["database_url"] = candidate.strip()
                        break
        return values

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Accept the bare URLs handed out by managed providers.

        Render/Supabase hand out ``postgres://...`` or ``postgresql://...``
        without a driver, which SQLAlchemy would resolve to psycopg2. Only
        psycopg (v3) is installed, so qualify the scheme explicitly.
        """
        if value.startswith("postgres://"):
            value = "postgresql://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

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


def describe_database_url(url: str) -> str:
    """Describe a SQLAlchemy URL for logs without exposing credentials."""
    from sqlalchemy.engine import make_url

    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - logging must never raise on a bad URL
        return "<unparseable database URL>"
    return "{}://{}:{}/{}".format(
        parsed.drivername,
        parsed.host or "<no host>",
        parsed.port or "<default port>",
        parsed.database or "<no database>",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
