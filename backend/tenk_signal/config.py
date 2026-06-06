"""Application configuration.

All settings load from environment variables. Required-but-missing vars cause
the app to fail loudly on startup — better than a 500 in the wild.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Anthropic ---------------------------------------------------------
    anthropic_api_key: SecretStr = Field(
        ...,
        description="From console.anthropic.com. Required outside tests.",
    )
    anthropic_model: str = Field(default="claude-sonnet-4-6")
    extraction_max_tokens: int = Field(default=800, ge=64, le=4096)
    prompt_version: str = Field(default="v1", min_length=1, max_length=32)

    # --- EDGAR -------------------------------------------------------------
    edgar_user_agent: str = Field(
        ...,
        description='SEC requires "Name email@domain". Blocks without it.',
        min_length=5,
    )
    edgar_rate_limit_rps: int = Field(default=8, ge=1, le=10)

    # --- Database ----------------------------------------------------------
    database_url: str = Field(
        ...,
        description="postgresql+asyncpg://… for the app",
    )
    # Optional sync URL for alembic; derived if absent.
    database_url_sync: str | None = Field(default=None)

    # --- App auth ----------------------------------------------------------
    app_api_key_admin: SecretStr = Field(..., min_length=24)
    app_api_key_viewer: SecretStr = Field(..., min_length=24)

    # --- Observability -----------------------------------------------------
    sentry_dsn: str | None = Field(default=None)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    environment: Literal["dev", "ci", "staging", "prod"] = Field(default="dev")

    # --- Limits ------------------------------------------------------------
    max_request_body_bytes: int = Field(default=1_048_576)  # 1 MiB

    @field_validator("database_url")
    @classmethod
    def _validate_db_url(cls, v: str) -> str:
        if not v.startswith(("postgresql+asyncpg://", "postgresql://")):
            raise ValueError("DATABASE_URL must be a postgresql URL; got something else")
        return v

    @property
    def sync_database_url(self) -> str:
        """URL alembic uses. Either explicit or derived from the async URL."""
        if self.database_url_sync:
            return self.database_url_sync
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so we read env once. Re-import for tests via cache_clear()."""
    return Settings()
