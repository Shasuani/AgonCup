"""Worker configuration (12-factor, env-driven).

All settings come from the environment / `.env`. Every variable referenced here
must also exist in the repo-root `.env.example` (see PLAN.md §6 / SHARED
CONVENTIONS). The worker shares the same canonical env set as the backend
(DATABASE_URL, REDIS_URL, LOG_LEVEL, ENVIRONMENT).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from arq.connections import RedisSettings
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    """Worker runtime settings.

    Loaded once and cached (see `get_settings`). Field names map 1:1 to env vars
    (case-insensitive), e.g. ``REDIS_URL`` -> ``redis_url``.
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
        description="Async SQLAlchemy DSN (asyncpg driver). Worker sessions use "
        "the same RLS GUC discipline as the backend (PLAN.md §7 Phase 1).",
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        description="Redis DSN — the arq broker (and cache / pub/sub).",
    )

    # --- Runtime / observability -------------------------------------------
    environment: Environment = Field(default="development")
    log_level: str = Field(default="INFO")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def arq_redis_settings(self) -> RedisSettings:
        """Build arq ``RedisSettings`` from ``REDIS_URL``.

        arq connects with its own ``RedisSettings`` rather than a DSN string;
        ``from_dsn`` parses host/port/db/password out of ``redis_url`` so the
        worker and backend (which enqueues jobs) share one source of truth.
        """
        return RedisSettings.from_dsn(self.redis_url)


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached `Settings` instance."""
    return Settings()
