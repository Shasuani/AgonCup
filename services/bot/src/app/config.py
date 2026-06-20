"""Bot configuration (12-factor, env-driven).

Every variable referenced here must also exist in the repo-root `.env.example`
(see PLAN.md §6 / SHARED CONVENTIONS).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    """Bot runtime settings (field names map 1:1 to env vars, case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Secret — empty in .env.example, real value in .env / secrets manager.
    discord_bot_token: str = Field(default="")
    # Base URL of the backend API the bot calls (PLAN.md §3.2 "thin layer").
    backend_base_url: str = Field(default="http://backend:8000")

    # Shared service-auth secret the bot presents to the backend bot bridge in the
    # ``X-Service-Token`` header (PLAN.md §3.2). The bot is the SOLE holder; the
    # backend trusts the asserted Discord context because only the bot can present
    # this token. Empty default → backend bridge fails closed (503).
    bot_service_token: str = Field(default="")

    # Redis DSN for the backend→bot notification fan-out channel (PLAN.md §3.2).
    redis_url: str = Field(default="redis://redis:6379/0")

    environment: Environment = Field(default="development")
    log_level: str = Field(default="INFO")


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached `Settings` instance."""
    return Settings()
