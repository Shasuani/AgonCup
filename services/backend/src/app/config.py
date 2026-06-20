"""Application configuration (12-factor, env-driven).

All settings come from the environment / `.env`. Every variable referenced here
must also exist in `infra/.env.example` (see PLAN.md §6 / SHARED CONVENTIONS).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    """Backend runtime settings.

    Loaded once and cached (see `get_settings`). Field names map 1:1 to env vars
    (case-insensitive), e.g. ``DATABASE_URL`` -> ``database_url``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core infra ---------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://lol:lol@postgres:5432/lol",
        description="Async SQLAlchemy DSN (asyncpg driver).",
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        description="Redis DSN (cache, pub/sub, arq broker).",
    )

    # --- Runtime / observability -------------------------------------------
    environment: Environment = Field(default="development")
    log_level: str = Field(default="INFO")

    # --- Auth ---------------------------------------------------------------
    # NOTE: placeholder secret only. Provided via the secrets manager in real
    # environments; never commit a real value. See PLAN.md §6 (secrets).
    jwt_secret: str = Field(default="dev-insecure-change-me")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached `Settings` instance."""
    return Settings()
