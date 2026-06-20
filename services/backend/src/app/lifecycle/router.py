"""Guild + user lifecycle routes (PLAN.md §3.2 multi-guild, §7 Phase 1 PII).

Three endpoints (mounted under ``/api/v1`` by the application factory):

* ``POST /lifecycle/guilds/{guild_id}/purge`` — RBAC ``guild_admin`` (of that
  guild) or ``platform_operator``. Soft-deletes the guild anchor and HARD-deletes
  its guild-scoped data. Runs inside the tenant session so RLS applies.
* ``POST /lifecycle/users/{user_id}/delete`` — self (the principal deleting their
  OWN account) or ``platform_operator``. HARD-deletes the user's global PII.
* ``POST /lifecycle/internal/guild-remove`` — **service-auth** (shared-secret
  header, NOT a user JWT). Called by the bot's ``on_guild_remove`` so leaving a
  Discord server purges its data. Keyed by the Discord snowflake.

The deletion logic lives in :mod:`app.lifecycle.service`; this module is the thin
HTTP + authorization shell (PLAN.md §3.1 backend modules).
"""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from lol_schemas.enums import UserGuildRoleName
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Principal, get_current_principal, require_tenant_role
from app.db import SessionLocal
from app.lifecycle import service
from app.logging import get_logger

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])
log = get_logger("app.lifecycle")

#: Env var holding the shared service-auth secret for internal (bot→backend)
#: calls. Read directly (mirrors ``app.security.crypto``) because ``app.config``
#: is owned by the Auth phase; the bot reads the same value to sign its calls.
SERVICE_TOKEN_ENV = "LIFECYCLE_SERVICE_TOKEN"


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------
class PurgeResponse(BaseModel):
    """Result of a guild purge (soft anchor + hard scoped-data delete)."""

    guild_id: str
    status: str
    removed_at: str
    deleted_rows: dict[str, int]
    total_deleted: int


class UserDeletionResponse(BaseModel):
    user_id: str
    deleted: bool


class GuildRemoveRequest(BaseModel):
    """Internal service-auth payload: a Discord guild the bot was removed from."""

    discord_guild_id: int = Field(..., description="Discord guild snowflake.")


# ---------------------------------------------------------------------------
# Service-auth dependency
# ---------------------------------------------------------------------------
def require_service_token(
    x_service_token: Annotated[str | None, Header(alias="X-Service-Token")] = None,
) -> None:
    """Authorize an internal service caller via the shared-secret header.

    Compared in constant time. Fails CLOSED: if the server has no token configured
    the endpoint is unusable (503) rather than open. Returns ``None`` on success.
    """
    expected = os.environ.get(SERVICE_TOKEN_ENV, "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service auth not configured",
        )
    if not x_service_token or not hmac.compare_digest(x_service_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid service token",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/guilds/{guild_id}/purge",
    response_model=PurgeResponse,
    summary="Purge a guild: soft-remove the anchor + hard-delete its scoped data",
)
async def purge_guild(
    guild_id: str,
    session: Annotated[AsyncSession, Depends(require_tenant_role(UserGuildRoleName.GUILD_ADMIN))],
) -> PurgeResponse:
    """Mark the guild ``removed`` and purge its guild-scoped data (PLAN.md §3.2/§7).

    Authorized for a ``guild_admin`` **of this same guild** or a
    ``platform_operator``. ``require_tenant_role`` sets ``app.current_guild`` to
    the path ``guild_id`` and verifies the guild_admin role IN that guild (under
    RLS) — closing the cross-guild escalation where a JWT's home-guild admin role
    was honored against a different path guild.
    """
    try:
        result = await service.purge_guild(session, guild_id=guild_id)
    except service.GuildNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="guild not found"
        ) from exc

    return PurgeResponse(
        guild_id=result.guild_id,
        status=result.status,
        removed_at=result.removed_at.isoformat(),
        deleted_rows=result.deleted_rows,
        total_deleted=result.total_deleted,
    )


@router.post(
    "/users/{user_id}/delete",
    response_model=UserDeletionResponse,
    summary="Delete a user's global PII (User, RiotAccount, OAuth, verification)",
)
async def delete_user(
    user_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> UserDeletionResponse:
    """HARD-delete the user + all global PII cascading off ``users.id``.

    Authorized for the user deleting their OWN account (``principal.user_id ==
    user_id``) or a ``platform_operator``. Operates on GLOBAL tables (no guild
    RLS), so it uses a plain :data:`SessionLocal` transaction rather than the
    tenant session (PLAN.md §7 Privacy/PII).
    """
    if not (principal.is_platform_operator or principal.user_id == user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="can only delete your own account",
        )

    async with SessionLocal() as session, session.begin():
        try:
            result = await service.delete_user(session, user_id=user_id)
        except service.UserNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="user not found"
            ) from exc

    return UserDeletionResponse(user_id=result.user_id, deleted=result.deleted)


@router.post(
    "/internal/guild-remove",
    response_model=PurgeResponse,
    summary="Service-auth: purge a guild the bot was removed from (by Discord id)",
)
async def internal_guild_remove(
    body: Annotated[GuildRemoveRequest, Body()],
    _auth: Annotated[None, Depends(require_service_token)],
) -> PurgeResponse:
    """Purge a guild keyed by Discord snowflake (bot ``on_guild_remove``).

    Service-authenticated (shared secret), NOT a user JWT — the bot has no user
    context. This runs WITHOUT a tenant GUC: the guild is resolved by its Discord
    id and the explicit ``guild_id`` predicate scopes every delete (PLAN.md §3.2).
    A not-found guild is treated as success-noop (idempotent: the bot may retry, or
    the guild may have already been purged).
    """
    async with SessionLocal() as session, session.begin():
        try:
            result = await service.purge_guild_by_discord_id(
                session, discord_guild_id=body.discord_guild_id
            )
        except service.GuildNotFoundError:
            log.info("guild_remove_noop_unknown_guild", discord_guild_id=body.discord_guild_id)
            return PurgeResponse(
                guild_id="",
                status="not_found",
                removed_at="",
                deleted_rows={},
                total_deleted=0,
            )

    return PurgeResponse(
        guild_id=result.guild_id,
        status=result.status,
        removed_at=result.removed_at.isoformat(),
        deleted_rows=result.deleted_rows,
        total_deleted=result.total_deleted,
    )
