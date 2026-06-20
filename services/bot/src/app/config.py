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

    environment: Environment = Field(default="development")
    log_level: str = Field(default="INFO")


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached `Settings` instance."""
    return Settings()
