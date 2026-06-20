"""Application configuration (12-factor, env-driven).

All settings come from the environment / `.env`. Every variable referenced here
must also exist in `infra/.env.example` (see PLAN.md §6 / SHARED CONVENTIONS).
"""

from __future__ import annotations

from datetime import timedelta
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
        description="Async SQLAlchemy DSN (asyncpg driver). Used for MIGRATIONS "
        "(owner/superuser). RLS does not bind to a superuser — see app_database_url.",
    )
    app_database_url: str = Field(
        default="",
        description="DSN the REQUEST path connects as: the non-BYPASSRLS app_rw "
        "role, so FORCE-RLS guild policies actually apply. Falls back to "
        "database_url when empty (dev convenience) — but then RLS is NOT enforced "
        "because the superuser bypasses it (PLAN.md §9).",
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        description="Redis DSN (cache, pub/sub, arq broker).",
    )

    @property
    def request_database_url(self) -> str:
        """DSN for the per-request engine: app_rw if set, else database_url."""
        return self.app_database_url or self.database_url

    # --- Runtime / observability -------------------------------------------
    environment: Environment = Field(default="development")
    log_level: str = Field(default="INFO")

    # --- Auth: JWT ----------------------------------------------------------
    # NOTE: placeholder secret only. Provided via the secrets manager in real
    # environments; never commit a real value. See PLAN.md §6 (secrets).
    jwt_secret: str = Field(default="dev-insecure-change-me")
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWS alg for access tokens. HS256 (symmetric) — see PLAN.md §3.1.",
    )
    jwt_access_ttl_seconds: int = Field(
        default=15 * 60,
        description="Access-JWT lifetime (~15m). Short-lived; refresh tokens rotate.",
    )
    jwt_refresh_ttl_seconds: int = Field(
        default=30 * 24 * 60 * 60,
        description="Refresh-token lifetime (~30d). Stored HASHED + rotated on use.",
    )

    @property
    def jwt_access_ttl(self) -> timedelta:
        """Access-token lifetime as a timedelta."""
        return timedelta(seconds=self.jwt_access_ttl_seconds)

    @property
    def jwt_refresh_ttl(self) -> timedelta:
        """Refresh-token lifetime as a timedelta."""
        return timedelta(seconds=self.jwt_refresh_ttl_seconds)

    # --- Auth: Discord OAuth2 (sole IdP — PLAN.md §11 decision 6) ------------
    # Secrets: empty defaults; real values come from the secrets manager / .env.
    discord_client_id: str = Field(default="", description="Discord OAuth2 client id.")
    discord_client_secret: str = Field(
        default="", description="Discord OAuth2 client secret (NEVER commit a real value)."
    )
    discord_redirect_uri: str = Field(
        default="http://localhost:8000/api/v1/auth/callback",
        description="OAuth2 redirect URI registered in the Discord developer portal.",
    )

    # --- PII-at-rest encryption (read by app.security.crypto) ---------------
    # The crypto module reads PII_ENCRYPTION_KEY from the environment directly;
    # this field exists so the value is documented/validated through Settings and
    # surfaced in .env.example. Comma-separated for key rotation (first = primary).
    pii_encryption_key: str = Field(
        default="",
        description="Fernet key(s) for encrypting OAuth tokens + verification state "
        "at rest (PLAN.md §7). Comma-separated for rotation. Read by "
        "app.security.crypto via the PII_ENCRYPTION_KEY env var.",
    )

    # --- Bot ↔ backend service auth (PLAN.md §3.2 multi-tenant bot) ---------
    # The Discord bot is the SOLE holder of this shared secret; it calls the
    # backend's /integrations/discord endpoints with it in the X-Service-Token
    # header. The backend TRUSTS the Discord context (user/guild snowflakes,
    # is_admin) asserted on those calls precisely because only the bot can present
    # the token. Empty default → fail closed (the bridge answers 503 until set).
    bot_service_token: str = Field(
        default="",
        description="Shared secret the bot presents to the /integrations/discord "
        "bridge (X-Service-Token). The bot is the only token holder; the backend "
        "maps discord_user_id->User and enforces roles. Empty → bridge fails closed "
        "(503). NEVER commit a real value. (PLAN.md §3.2)",
    )

    # --- Riot (Account-V1 personal key only; NO Tournament API) -------------
    riot_api_key: str = Field(default="", description="Riot Account-V1 personal API key.")
    riot_region: str = Field(
        default="americas",
        description="Default continental routing cluster for Account-V1 "
        "(americas/asia/europe). See lol_schemas.RiotRegion.",
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached `Settings` instance."""
    return Settings()
