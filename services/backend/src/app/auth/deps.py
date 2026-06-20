"""FastAPI auth dependencies: principal extraction, RBAC, tenant resolution.

This is the request-time half of auth (PLAN.md §3.1 / §9):

* :func:`get_current_principal` — verify the bearer access JWT and yield the
  authenticated :class:`Principal` (user id + acting guild + roles).
* :func:`require_role` — a dependency *factory* producing an RBAC guard that
  allows a request only if the principal holds one of the named roles (a
  ``platform_operator`` is cross-tenant and satisfies any guild guard).
* :func:`get_tenant_session` — the **request-aware** tenant session that replaces
  the old ``app/db.py`` stub: it resolves the acting guild from the request
  (path → header → JWT claim, in that precedence), **validates the principal has a
  role in that guild**, opens a transaction, sets ``app.current_guild``, and
  yields the session. It fails CLOSED — any ambiguity raises 401/403 rather than
  defaulting to a tenant.

Tenant precedence
-----------------
1. Path parameter ``guild_id`` (explicit, e.g. ``/guilds/{guild_id}/...``).
2. ``X-Guild-Id`` request header.
3. The ``gid`` claim baked into the access JWT at login/refresh.

Whatever the source, the principal MUST hold a role in that guild (or be a
platform operator) or the request is rejected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from lol_schemas.enums import UserGuildRoleName
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import AccessClaims, InvalidAccessToken, verify_access_token
from app.db import SessionLocal
from app.models import UserGuildRole

PLATFORM_OPERATOR = UserGuildRoleName.PLATFORM_OPERATOR.value

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller derived from a verified access JWT."""

    user_id: str
    guild_id: str | None
    roles: tuple[str, ...]

    @property
    def is_platform_operator(self) -> bool:
        return PLATFORM_OPERATOR in self.roles


def _principal_from_claims(claims: AccessClaims) -> Principal:
    return Principal(user_id=claims.user_id, guild_id=claims.guild_id, roles=claims.roles)


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Verify the ``Authorization: Bearer <jwt>`` access token → :class:`Principal`."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = verify_access_token(credentials.credentials)
    except InvalidAccessToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return _principal_from_claims(claims)


def require_role(
    *allowed: UserGuildRoleName | str,
) -> Callable[[Principal], Awaitable[Principal]]:
    """Build a dependency that allows only principals holding one of ``allowed``.

    A ``platform_operator`` always passes (cross-tenant staff). The roles come
    from the JWT's acting-guild claim, so this guards "do you have role R in the
    guild you're acting as".
    """
    allowed_values = frozenset(r.value if isinstance(r, UserGuildRoleName) else r for r in allowed)

    async def _guard(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.is_platform_operator:
            return principal
        if allowed_values & set(principal.roles):
            return principal
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient role",
        )

    return _guard


def _resolve_acting_guild(
    *,
    principal: Principal,
    path_guild_id: str | None,
    header_guild_id: str | None,
) -> str:
    """Pick the acting guild by precedence: path → header → JWT claim.

    Fails closed: if none of the sources yield a guild, the request is rejected
    rather than silently running against a sentinel tenant.
    """
    acting = path_guild_id or header_guild_id or principal.guild_id
    if not acting:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no acting guild: provide a guild via path, X-Guild-Id, or token",
        )
    return acting


async def get_tenant_session(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    x_guild_id: str | None = Header(default=None, alias="X-Guild-Id"),
) -> AsyncIterator[AsyncSession]:
    """Request-aware tenant session: resolve+validate the guild, set the GUC.

    Replaces the Phase-0 ``_extract_guild_id`` stub. Yields a transaction-scoped
    session with ``app.current_guild`` set to the validated acting guild so RLS
    isolates the tenant. Authorization is enforced INSIDE the same transaction:
    the principal must hold a role row in that guild (or be a platform operator),
    looked up under RLS itself — defense in depth.
    """
    path_guild_id = request.path_params.get("guild_id")
    acting_guild = _resolve_acting_guild(
        principal=principal,
        path_guild_id=str(path_guild_id) if path_guild_id else None,
        header_guild_id=x_guild_id,
    )

    async with SessionLocal() as session, session.begin():
        # Set the tenant GUC FIRST so the membership check below itself runs under
        # RLS (a platform operator is exempt from the membership requirement).
        await session.execute(
            text("SELECT set_config('app.current_guild', :gid, true)"),
            {"gid": acting_guild},
        )

        if not principal.is_platform_operator:
            has_role = (
                await session.execute(
                    select(UserGuildRole.id).where(
                        UserGuildRole.user_id == principal.user_id,
                        UserGuildRole.guild_id == acting_guild,
                    )
                )
            ).first()
            if has_role is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="principal has no role in the acting guild",
                )

        yield session


def require_tenant_role(
    *allowed: UserGuildRoleName | str,
) -> Callable[..., AsyncIterator[AsyncSession]]:
    """Tenant session that ALSO requires a specific role IN THE ACTING GUILD.

    ``require_role`` alone is unsafe for guild-acting endpoints: it checks the
    roles baked into the JWT (the token's *home* acting guild), which may differ
    from the guild named in the request path. That mismatch allowed a guild_admin
    of guild A to act as admin on guild B where they were only a member.

    This guard closes that gap: it depends on :func:`get_tenant_session` (which
    sets ``app.current_guild`` to the *resolved acting guild* and validates
    membership), then re-checks that the principal holds one of ``allowed`` roles
    **in that same guild** — the role lookup runs under RLS scoped to the acting
    guild, so it cannot be satisfied by a role held elsewhere. A platform operator
    is exempt. Use this (not ``require_role``) for any guild-scoped privileged
    action like purge.
    """
    allowed_values = frozenset(r.value if isinstance(r, UserGuildRoleName) else r for r in allowed)

    async def _dep(
        session: AsyncSession = Depends(get_tenant_session),
        principal: Principal = Depends(get_current_principal),
    ) -> AsyncIterator[AsyncSession]:
        if not principal.is_platform_operator:
            rows = (
                (
                    await session.execute(
                        select(UserGuildRole.role).where(UserGuildRole.user_id == principal.user_id)
                    )
                )
                .scalars()
                .all()
            )
            held = {r.value if hasattr(r, "value") else str(r) for r in rows}
            if not (allowed_values & held):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="insufficient role in the acting guild",
                )
        yield session

    return _dep


# Re-exported convenience type for route signatures.
__all__ = [
    "Principal",
    "get_current_principal",
    "get_tenant_session",
    "require_role",
    "require_tenant_role",
]
