"""Authentication & per-(user, guild) authorization routes (PLAN.md §3.1 auth).

Endpoints
---------
* ``GET  /auth/login``    — build the Discord authorize URL (with anti-CSRF
                            ``state``) and redirect the browser to it.
* ``GET  /auth/callback`` — validate ``state``, exchange ``code`` for Discord
                            tokens, fetch the Discord user, upsert our ``User``,
                            store the ENCRYPTED OAuth credential, then issue our
                            own access JWT + rotating refresh token.
* ``POST /auth/refresh``  — rotate a refresh token (reuse-detection revokes the
                            family) and mint a fresh access JWT.
* ``POST /auth/logout``   — revoke the presented refresh token.

State handling: the ``state`` value is kept in a short-TTL in-process store. This
is sufficient for a single backend instance; a multi-instance deployment should
back it with Redis (noted in :data:`_STATE_TTL_SECONDS`). The desktop client uses
PKCE (see ``app.auth.discord_oauth``) — ``state`` is still validated there too.
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from lol_schemas.enums import UserGuildRoleName
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts import service as accounts_service
from app.auth import discord_oauth
from app.auth.jwt import mint_access_token
from app.auth.tokens import (
    InvalidRefreshToken,
    RefreshTokenReuse,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.logging import get_logger
from app.models import Guild, OAuthCredential, RiotAccount, User, UserGuildRole, VerificationAttempt
from app.ratelimit import FixedWindowLimiter
from app.security import encrypt

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("app.auth")

DISCORD_PROVIDER = "discord"

#: Per-IP fixed window for the unauthenticated ``/auth/desktop-pair`` (code
#: exchange) — a brute-force brake on code-guessing (10/60s on the socket peer).
_pair_limiter = FixedWindowLimiter(max_hits=10, window_s=60.0)
#: Per-IP fixed window for ``/auth/refresh`` (30/60s) — a brake on refresh-token
#: guessing / replay floods. Legitimate clients rotate ~ once per access TTL.
_refresh_limiter = FixedWindowLimiter(max_hits=30, window_s=60.0)

# In-process anti-CSRF state store: state -> expiry epoch seconds. Single-instance
# only; back with Redis for horizontal scale (PLAN.md §3.1 — auth-code + state).
_STATE_TTL_SECONDS = 600
_state_store: dict[str, float] = {}

# The state is ALSO mirrored into an HttpOnly, SameSite=Lax cookie set at /login
# and required to match the ?state at /callback, binding the callback to the
# browser session that began the flow (anti-CSRF / login-fixation). The in-process
# store only bounds TTL/single-use; the cookie is the session binding.
_STATE_COOKIE = "lol_oauth_state"


def _remember_state(state: str) -> None:
    now = time.time()
    # Opportunistic GC of expired entries.
    for k in [k for k, exp in _state_store.items() if exp <= now]:
        _state_store.pop(k, None)
    _state_store[state] = now + _STATE_TTL_SECONDS


def _consume_state(state: str) -> bool:
    """Validate + single-use-consume a state value. Returns True if valid."""
    exp = _state_store.pop(state, None)
    return exp is not None and exp > time.time()


def _get_settings_dep() -> Settings:
    return get_settings()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - not a secret, the scheme name


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class DesktopPairRequest(BaseModel):
    """Desktop pairing body. Mirrors ``lol_schemas.DesktopPairRequestDTO``."""

    code: str = Field(min_length=8, max_length=12)
    device_label: str | None = Field(default=None, max_length=64)


class DesktopPairUser(BaseModel):
    """Authenticated user summary. Mirrors ``lol_schemas.DesktopPairUserDTO``."""

    user_id: str
    discord_user_id: str
    display_name: str


class DesktopPairAccount(BaseModel):
    """Linked-account summary. Mirrors ``lol_schemas.DesktopPairAccountDTO``."""

    linked: bool
    puuid: str | None = None
    game_name: str | None = None
    tag_line: str | None = None
    region: str | None = None
    verified: bool = False
    rank_text: str = "Unranked"


class DesktopPairResponse(BaseModel):
    """Pairing session result. Mirrors ``lol_schemas.DesktopPairResponseDTO``.

    The :class:`TokenPair` fields plus the user + (optional) linked-account summary.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - scheme name, not a secret
    user: DesktopPairUser
    account: DesktopPairAccount | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _roles_for(session: AsyncSession, user_id: str) -> tuple[str | None, tuple[str, ...]]:
    """Return the (acting_guild, role_values) to bake into the access JWT.

    Picks the user's first role row as the default acting guild. With none, the
    token is guild-less (the client selects a guild later via header/path).

    ``user_guild_roles`` is under FORCE RLS, and at login/refresh time no
    ``app.current_guild`` is set, so the ``guild_isolation`` policy alone would
    return zero rows for the app_rw role. We set the ``app.current_user`` GUC so
    the ``self_role_read`` policy (migration 0003) lets the user read their OWN
    role rows across guilds — the legitimate cross-guild read this default-tenant
    selection needs. ``set_config(..., true)`` is transaction-local.
    """
    await session.execute(
        text("SELECT set_config('app.current_user', :uid, true)"),
        {"uid": str(user_id)},
    )
    rows = (
        await session.execute(
            select(UserGuildRole.guild_id, UserGuildRole.role).where(
                UserGuildRole.user_id == user_id
            )
        )
    ).all()
    if not rows:
        return None, ()
    acting_guild = str(rows[0][0])
    roles = tuple(
        str(role.value if hasattr(role, "value") else role)
        for gid, role in rows
        if str(gid) == acting_guild
    )
    return acting_guild, roles


async def _issue_pair(session: AsyncSession, user_id: str, settings: Settings) -> TokenPair:
    acting_guild, roles = await _roles_for(session, user_id)
    access = mint_access_token(
        user_id=user_id, guild_id=acting_guild, roles=roles, settings=settings
    )
    refresh = await issue_refresh_token(session, user_id=user_id, settings=settings)
    return TokenPair(access_token=access, refresh_token=refresh.token)


async def _account_summary(session: AsyncSession, user_id: str) -> DesktopPairAccount | None:
    """Summarise the user's linked ``RiotAccount`` for the pairing response, or ``None``.

    ``RiotAccount`` is a GLOBAL table (not guild-RLS), so ownership is scoped in the
    query. A verified account is preferred; the stored Solo/Duo rank snapshot is
    rendered to ``rank_text`` (no Riot call — desktop pairing is fast). Returns
    ``None`` when the user has not linked a League account yet (they pair first,
    then link via ``/accounts/verify-lcu``).
    """
    accounts = (
        (await session.execute(select(RiotAccount).where(RiotAccount.user_id == user_id)))
        .scalars()
        .all()
    )
    if not accounts:
        return None
    # Prefer a verified account; otherwise the first linked row.
    account = next((a for a in accounts if a.verified), accounts[0])
    rank_text = accounts_service.format_rank_fields(
        tier=account.rank_tier,
        division=account.rank_division,
        league_points=account.rank_lp,
    )
    return DesktopPairAccount(
        linked=True,
        puuid=account.puuid,
        game_name=account.riot_id_game_name,
        tag_line=account.riot_id_tag_line,
        region=account.region,
        verified=account.verified,
        rank_text=rank_text,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/login", summary="Begin Discord OAuth2 (redirect to authorize URL)")
async def login(settings: Annotated[Settings, Depends(_get_settings_dep)]) -> RedirectResponse:
    state = discord_oauth.generate_state()
    _remember_state(state)
    url = discord_oauth.build_authorize_url(state=state, settings=settings)
    resp = RedirectResponse(url=url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    # Bind the flow to this browser: the callback must echo this exact state.
    resp.set_cookie(
        _STATE_COOKIE,
        state,
        max_age=_STATE_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )
    return resp


@router.get("/callback", response_model=TokenPair, summary="Discord OAuth2 callback")
async def callback(
    request: Request,
    response: Response,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    settings: Annotated[Settings, Depends(_get_settings_dep)],
) -> TokenPair:
    # CSRF: the ?state must match the cookie set at /login (session binding) AND
    # be a live, single-use value from the in-process store. Constant-time match.
    cookie_state = request.cookies.get(_STATE_COOKIE)
    response.delete_cookie(_STATE_COOKIE)
    state_ok = bool(cookie_state) and secrets.compare_digest(cookie_state or "", state)
    if not state_ok or not _consume_state(state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired state"
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            tokens = await discord_oauth.exchange_code(code, settings=settings, client=client)
            discord_user = await discord_oauth.fetch_user(tokens.access_token, client=client)
        except discord_oauth.DiscordOAuthError as exc:
            log.warning("discord_oauth_failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail="discord oauth failed"
            ) from exc

    async with SessionLocal() as session, session.begin():
        user = (
            await session.execute(
                select(User).where(User.discord_user_id == discord_user.discord_user_id)
            )
        ).scalar_one_or_none()
        if user is None:
            user = User(
                discord_user_id=discord_user.discord_user_id,
                display_name=discord_user.username,
            )
            session.add(user)
            await session.flush()
        else:
            user.display_name = discord_user.username

        await _upsert_oauth_credential(session, user_id=str(user.id), tokens=tokens)
        pair = await _issue_pair(session, str(user.id), settings)

    log.info("auth_login_success", user_id=str(user.id))
    return pair


async def _upsert_oauth_credential(
    session: AsyncSession,
    *,
    user_id: str,
    tokens: discord_oauth.DiscordTokens,
) -> None:
    """Store the Discord OAuth tokens ENCRYPTED at rest (PLAN.md §7)."""
    existing = (
        await session.execute(
            select(OAuthCredential).where(
                OAuthCredential.user_id == user_id,
                OAuthCredential.provider == DISCORD_PROVIDER,
            )
        )
    ).scalar_one_or_none()

    access_enc = encrypt(tokens.access_token)
    refresh_enc = encrypt(tokens.refresh_token) if tokens.refresh_token else None

    if existing is None:
        session.add(
            OAuthCredential(
                user_id=user_id,
                provider=DISCORD_PROVIDER,
                access_token_enc=access_enc,
                refresh_token_enc=refresh_enc,
                scopes=list(tokens.scopes),
            )
        )
    else:
        existing.access_token_enc = access_enc
        existing.refresh_token_enc = refresh_enc
        existing.scopes = list(tokens.scopes)


@router.post("/refresh", response_model=TokenPair, summary="Rotate a refresh token")
async def refresh(
    body: Annotated[RefreshRequest, Body()],
    request: Request,
    settings: Annotated[Settings, Depends(_get_settings_dep)],
) -> TokenPair:
    # Per-IP brake on refresh-token guessing/replay floods (the secret is opaque +
    # high-entropy, but a flood still costs a DB lookup per attempt).
    client_ip = request.client.host if request.client else "unknown"
    if not _refresh_limiter.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many attempts"
        )

    # Manual transaction control (not session.begin()): on reuse we MUST commit
    # the family revocation before raising — raising inside an auto-rollback
    # transaction would undo the very theft mitigation reuse-detection performed.
    async with SessionLocal() as session:
        try:
            rotated = await rotate_refresh_token(
                session, token=body.refresh_token, settings=settings
            )
        except RefreshTokenReuse as exc:
            await session.commit()  # persist the family revocation, then reject
            log.warning("refresh_token_reuse_detected")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="refresh token reuse detected; session revoked",
            ) from exc
        except InvalidRefreshToken as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired refresh token",
            ) from exc

        acting_guild, roles = await _roles_for(session, rotated.user_id)
        access = mint_access_token(
            user_id=rotated.user_id, guild_id=acting_guild, roles=roles, settings=settings
        )
        await session.commit()

    return TokenPair(access_token=access, refresh_token=rotated.token)


@router.post("/logout", summary="Revoke the presented refresh token")
async def logout(body: Annotated[LogoutRequest, Body()]) -> dict[str, str]:
    async with SessionLocal() as session, session.begin():
        await revoke_refresh_token(session, token=body.refresh_token)
    return {"status": "logged_out"}


# ---------------------------------------------------------------------------
# Desktop pairing: exchange a bot-minted one-time code for a session
# ---------------------------------------------------------------------------
@router.post(
    "/desktop-pair",
    response_model=DesktopPairResponse,
    summary="Exchange a one-time desktop code for a session (anonymous)",
)
async def desktop_pair(
    body: Annotated[DesktopPairRequest, Body()],
    request: Request,
    settings: Annotated[Settings, Depends(_get_settings_dep)],
) -> DesktopPairResponse:
    """Exchange a bot-minted desktop ``code`` for an access/refresh session (PLAN.md §3.1).

    UNAUTHENTICATED by design: the one-time ``code`` (minted for an authenticated
    Discord user via ``/register`` // the ``integrations/discord/accounts/link``
    bridge — 15-min TTL, single-use, superseded on re-register) IS the credential.
    This is the SAME code the deprecated ``/accounts/verify-desktop`` consumed, but
    here it is a SESSION bootstrap ONLY — the Riot link moved to the authenticated
    ``/accounts/verify-lcu``. A per-IP fixed-window rate limit guards against
    code-guessing; a failed lookup returns a generic 401.

    Flow: shape-gate the code (422) → rate-limit (429) → in one txn, ``FOR UPDATE``
    select the live ``desktop_code`` attempt (pending, unexpired) — ``None`` → 401 —
    CONSUME it single-use (``status='verified'``; ``puuid`` stays NULL: this flow
    links no Riot account) → bake the identity access JWT (``_roles_for`` picks the
    advisory acting guild) + issue a refresh token tagged with ``device_label`` →
    attach the user + any linked-account summary → commit.
    """
    # Cheap shape validation before any state touch (also pre-rate-limit), mirroring
    # ``/accounts/verify-desktop``: an obviously-malformed code 422s without a DB hit.
    normalized = accounts_service.normalize_code(body.code)
    if len(normalized) != accounts_service._CODE_LEN or not set(normalized) <= set(
        accounts_service._CODE_ALPHABET
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed code"
        )

    client_ip = request.client.host if request.client else "unknown"
    if not _pair_limiter.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many attempts"
        )

    now = datetime.now(tz=UTC)
    async with SessionLocal() as session, session.begin():
        attempt = (
            await session.execute(
                select(VerificationAttempt)
                .where(
                    VerificationAttempt.code == normalized,
                    VerificationAttempt.verify_kind == accounts_service.VERIFY_KIND_DESKTOP_CODE,
                    VerificationAttempt.status == accounts_service.STATUS_PENDING,
                    VerificationAttempt.expires_at > now,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired code"
            )

        # CONSUME single-use (semantic = consumed; no Riot link, so puuid stays NULL).
        attempt.status = accounts_service.STATUS_VERIFIED
        user_id = str(attempt.user_id)

        user = (
            await session.execute(select(User).where(User.id == attempt.user_id))
        ).scalar_one_or_none()
        if user is None:  # pragma: no cover - attempt.user_id FKs a live user
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired code"
            )

        acting_guild, roles = await _roles_for(session, user_id)
        access = mint_access_token(
            user_id=user_id, guild_id=acting_guild, roles=roles, settings=settings
        )
        refresh = await issue_refresh_token(
            session, user_id=user_id, device_label=body.device_label, settings=settings
        )
        account = await _account_summary(session, user_id)
        user_summary = DesktopPairUser(
            user_id=user_id,
            discord_user_id=str(user.discord_user_id),
            display_name=user.display_name,
        )

    log.info("desktop_pair_success", user_id=user_id, device_label=bool(body.device_label))
    return DesktopPairResponse(
        access_token=access,
        refresh_token=refresh.token,
        user=user_summary,
        account=account,
    )


# ---------------------------------------------------------------------------
# DEV-ONLY login (no Discord) — local development without an OAuth app
# ---------------------------------------------------------------------------
_DEV_DISCORD_GUILD = 100000000000000001
_DEV_DISCORD_USER = 100000000000000002


@router.post(
    "/dev-login",
    response_model=TokenPair,
    summary="DEV ONLY: mint a platform_operator session without Discord OAuth",
)
async def dev_login(
    settings: Annotated[Settings, Depends(_get_settings_dep)],
) -> TokenPair:
    """Mint a real admin token for local development — **disabled in production**.

    Upserts a dev guild + dev user with the ``platform_operator`` role and issues
    a normal access/refresh pair, so the SPA AND the backend API both work without
    a configured Discord OAuth app. Returns 404 when ``ENVIRONMENT=production`` so
    it can never be a production backdoor.
    """
    if settings.is_production:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    async with SessionLocal() as session, session.begin():
        guild = (
            await session.execute(select(Guild).where(Guild.discord_guild_id == _DEV_DISCORD_GUILD))
        ).scalar_one_or_none()
        if guild is None:
            guild = Guild(discord_guild_id=_DEV_DISCORD_GUILD, name="Dev Guild")
            session.add(guild)
            await session.flush()

        user = (
            await session.execute(select(User).where(User.discord_user_id == _DEV_DISCORD_USER))
        ).scalar_one_or_none()
        if user is None:
            user = User(discord_user_id=_DEV_DISCORD_USER, display_name="Dev Admin")
            session.add(user)
            await session.flush()

        # Scope the role insert/read to the dev guild (RLS on user_guild_roles).
        await session.execute(
            text("SELECT set_config('app.current_guild', :g, true)"),
            {"g": str(guild.id)},
        )
        has_role = (
            await session.execute(
                select(UserGuildRole.id).where(
                    UserGuildRole.user_id == user.id,
                    UserGuildRole.guild_id == guild.id,
                    UserGuildRole.role == UserGuildRoleName.PLATFORM_OPERATOR,
                )
            )
        ).first()
        if has_role is None:
            session.add(
                UserGuildRole(
                    user_id=user.id,
                    guild_id=guild.id,
                    role=UserGuildRoleName.PLATFORM_OPERATOR,
                )
            )
            await session.flush()

        pair = await _issue_pair(session, str(user.id), settings)

    log.warning("dev_login_used", user_id=str(user.id))
    return pair
