"""Authentication & per-(user, guild) authorization routes (PLAN.md §3.1 auth).

Endpoints
---------
* ``GET  /auth/login``    — build the Discord authorize URL (with anti-CSRF
                            ``state``) and redirect the browser to it.
* ``GET  /auth/callback`` — validate ``state``, exchange ``code`` for Discord
                            tokens, fetch the Discord user, upsert our ``User``,
                            store the ENCRYPTED OAuth credential, **sync the
                            ``guild_admin`` role rows against Discord's live
                            ADMINISTRATOR bit**, issue our own access JWT +
                            rotating refresh token, then **303 back to the SPA**
                            with the pair in the URL fragment.
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
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from lol_schemas.enums import GuildStatus, UserGuildRoleName
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts import service as accounts_service
from app.auth import discord_oauth
from app.auth.deps import Principal, get_current_principal
from app.auth.jwt import mint_access_token
from app.auth.session_cookie import (
    InvalidSessionCookie,
    cookie_name,
    mint_session_cookie,
    read_session_cookie,
)
from app.auth.tokens import (
    InvalidRefreshToken,
    RefreshTokenReuse,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.guilds import service as guilds_service
from app.logging import get_logger
from app.models import Guild, OAuthCredential, RiotAccount, User, UserGuildRole, VerificationAttempt
from app.ratelimit import FixedWindowLimiter
from app.security import decrypt, encrypt

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


class AdminGuild(BaseModel):
    """A Discord server the caller administers — a bot-install target.

    ``installed`` distinguishes "the bot is already here, go manage it" from
    "click ``invite_url`` to add it". Ids are strings: Discord snowflakes exceed
    JavaScript's safe integer range and would be silently mangled as JSON numbers.
    """

    discord_guild_id: str
    name: str
    icon_url: str | None = None
    owner: bool = False
    installed: bool = False
    #: Our internal guild UUID (the `X-Guild-Id` tenant key). Null until installed.
    guild_id: str | None = None
    invite_url: str


class RefreshRequest(BaseModel):
    """Refresh body. The token is optional because the SPA does not have one to
    send: its refresh token lives in an HttpOnly cookie the browser attaches
    itself. The desktop client keeps sending the body — it stores its token in
    the OS keychain and has no cookie jar."""

    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    """Logout body. Optional for the same reason as :class:`RefreshRequest`."""

    refresh_token: str | None = None


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


def _callback_redirect(pair: TokenPair, settings: Settings, *, user_id: str) -> RedirectResponse:
    """Hand the token pair back to the SPA via the URL **fragment**.

    The browser lands on the backend's ``/auth/callback``; returning JSON there
    would just render raw tokens in the address bar (and leave the user outside
    the app). Instead we 303 to ``<frontend>/auth/callback#access_token=..``,
    which ``screens/auth/AuthCallback.tsx`` parses, installs, and scrubs from
    history. A fragment is chosen over a query string because it is never
    transmitted to servers, proxies, or access logs.

    Only the SHORT-LIVED access token rides the fragment. The refresh token goes
    into the HttpOnly session cookie instead, so the long-lived credential never
    appears in a URL (where it could survive in history or a screenshot) and is
    unreadable by JavaScript. The cookie is also what makes the session survive a
    page reload — see :mod:`app.auth.session_cookie`.
    """
    fragment = urlencode(
        {
            "access_token": pair.access_token,
            "token_type": pair.token_type,
        }
    )
    redirect = RedirectResponse(
        url=f"{settings.frontend_origin}/auth/callback#{fragment}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_session_cookie(redirect, user_id=user_id, pair=pair, settings=settings)
    return redirect


def set_session_cookie(
    response: Response, *, user_id: str, pair: TokenPair, settings: Settings
) -> None:
    """Attach the signed session cookie carrying ``pair``'s refresh token.

    ``SameSite=lax`` rather than ``strict``: the cookie is set on the 303 that
    Discord's redirect lands on, and a strict cookie is withheld on a cross-site
    navigation — the browser would drop it on the very request that sets it.
    Lax still blocks it on cross-site subrequests, which is what CSRF needs, and
    the SPA and API share an origin so first-party requests always carry it.
    """
    response.set_cookie(
        cookie_name(settings),
        mint_session_cookie(user_id=user_id, refresh_token=pair.refresh_token, settings=settings),
        max_age=int(settings.jwt_refresh_ttl.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Delete the session cookie (logout). Must mirror the set attributes."""
    response.delete_cookie(
        cookie_name(settings),
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )


def _clear_cookie_headers(settings: Settings) -> dict[str, str]:
    """The ``Set-Cookie`` header that deletes the session cookie, as a raw header.

    Needed because a cookie written onto the INJECTED ``Response`` is silently
    dropped when the handler raises: FastAPI builds a fresh response from the
    exception and never merges those headers. ``HTTPException(headers=...)`` is
    carried through, so the delete has to travel on the exception itself — the
    difference is invisible in a unit test that calls the handler directly, and
    only shows up through the real ASGI stack.
    """
    carrier = Response()
    clear_session_cookie(carrier, settings)
    return {
        key.decode(): value.decode() for key, value in carrier.raw_headers if key == b"set-cookie"
    }


@router.get("/callback", summary="Discord OAuth2 callback (303 back to the SPA)")
async def callback(
    request: Request,
    response: Response,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    settings: Annotated[Settings, Depends(_get_settings_dep)],
) -> RedirectResponse:
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

        # Best effort: the guild list drives the guild_admin sync below. A token
        # minted before the `guilds` scope (or a Discord blip) must NOT break
        # login — the user simply keeps whatever roles they already had.
        try:
            discord_guilds = await discord_oauth.fetch_user_guilds(
                tokens.access_token, client=client
            )
        except (discord_oauth.DiscordOAuthError, httpx.HTTPError) as exc:
            log.info("discord_guilds_unavailable_at_login", error=str(exc))
            discord_guilds = ()

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
        # Mirror Discord's ADMINISTRATOR bit onto durable `guild_admin` rows BEFORE
        # minting the pair, so the very first login of a server admin already
        # carries the role (and an acting guild) in its access token.
        if discord_guilds:
            await guilds_service.sync_discord_admin_roles(
                session,
                user_id=user.id,
                admin_discord_guild_ids=[g.discord_guild_id for g in discord_guilds if g.is_admin],
                member_discord_guild_ids=[g.discord_guild_id for g in discord_guilds],
            )
        pair = await _issue_pair(session, str(user.id), settings)

    log.info("auth_login_success", user_id=str(user.id))
    # Returning a Response directly bypasses the injected `response`, so the
    # single-use state cookie must be cleared on the redirect itself.
    redirect = _callback_redirect(pair, settings, user_id=str(user.id))
    redirect.delete_cookie(_STATE_COOKIE)
    return redirect


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


@router.get(
    "/guilds",
    response_model=list[AdminGuild],
    summary="Discord servers the caller administers (bot install targets)",
)
async def list_admin_guilds(
    principal: Annotated[Principal, Depends(get_current_principal)],
    settings: Annotated[Settings, Depends(_get_settings_dep)],
) -> list[AdminGuild]:
    """List the caller's Discord servers where they can install the bot.

    Answers "where do I put this bot?" — previously unanswerable, because there
    was no way to enumerate a user's servers and no invite URL anywhere in the
    product; a guild could only be onboarded by hand-crafting an invite link.

    Admin-ness is Discord's own ``ADMINISTRATOR`` bit read from the user's OAuth
    token, never anything the client asserts. ``installed`` is the join against
    our ``guilds`` table (the tenant root — NOT under guild RLS, so it is
    readable without an acting-guild GUC).
    """
    async with SessionLocal() as session:
        cred = (
            await session.execute(
                select(OAuthCredential).where(
                    OAuthCredential.user_id == principal.user_id,
                    OAuthCredential.provider == DISCORD_PROVIDER,
                )
            )
        ).scalar_one_or_none()
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="discord_reauth_required"
            )

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                guilds = await discord_oauth.fetch_user_guilds(
                    decrypt(cred.access_token_enc), client=client
                )
            except discord_oauth.DiscordReauthRequired:
                # Expired/revoked token, or one minted before the `guilds` scope
                # existed. The SPA turns this into a "sign in again" prompt.
                log.info("discord_guilds_reauth_required", user_id=principal.user_id)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="discord_reauth_required"
                ) from None
            except discord_oauth.DiscordOAuthError as exc:
                log.warning("discord_guilds_failed", error=str(exc))
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail="discord guild list failed"
                ) from exc

        admin_guilds = [g for g in guilds if g.is_admin]
        onboarded = {
            row.discord_guild_id: row
            for row in (
                await session.execute(
                    select(Guild).where(
                        Guild.discord_guild_id.in_([g.discord_guild_id for g in admin_guilds]),
                        Guild.status != GuildStatus.REMOVED,
                    )
                )
            )
            .scalars()
            .all()
        }

    return [
        AdminGuild(
            discord_guild_id=str(g.discord_guild_id),
            name=g.name,
            icon_url=(
                f"https://cdn.discordapp.com/icons/{g.discord_guild_id}/{g.icon}.png"
                if g.icon
                else None
            ),
            owner=g.owner,
            installed=g.discord_guild_id in onboarded,
            guild_id=(
                str(onboarded[g.discord_guild_id].id) if g.discord_guild_id in onboarded else None
            ),
            invite_url=discord_oauth.build_bot_invite_url(
                settings=settings, guild_id=g.discord_guild_id
            ),
        )
        for g in admin_guilds
    ]


@router.post("/refresh", response_model=TokenPair, summary="Rotate a refresh token")
async def refresh(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(_get_settings_dep)],
    body: RefreshRequest | None = None,
) -> TokenPair:
    """Rotate the session. Also the SPA's "am I still signed in?" call on boot.

    The token comes from the request body (desktop, which holds it in the OS
    keychain) or, when the body has none, from the HttpOnly session cookie (the
    browser, which cannot read it). A rotated cookie is written back on success,
    and a rejected refresh clears the stale one so the SPA is not stuck retrying
    a cookie that can only ever 401.
    """
    # Per-IP brake on refresh-token guessing/replay floods (the secret is opaque +
    # high-entropy, but a flood still costs a DB lookup per attempt).
    client_ip = request.client.host if request.client else "unknown"
    if not _refresh_limiter.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many attempts"
        )

    presented = body.refresh_token if body is not None else None
    from_cookie = False
    if not presented:
        try:
            presented = read_session_cookie(
                request.cookies.get(cookie_name(settings)), settings=settings
            ).refresh_token
        except InvalidSessionCookie as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired refresh token",
                headers=_clear_cookie_headers(settings),
            ) from exc
        from_cookie = True

    # Manual transaction control (not session.begin()): on reuse we MUST commit
    # the family revocation before raising — raising inside an auto-rollback
    # transaction would undo the very theft mitigation reuse-detection performed.
    async with SessionLocal() as session:
        try:
            rotated = await rotate_refresh_token(session, token=presented, settings=settings)
        except RefreshTokenReuse as exc:
            await session.commit()  # persist the family revocation, then reject
            log.warning("refresh_token_reuse_detected")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="refresh token reuse detected; session revoked",
                headers=_clear_cookie_headers(settings) if from_cookie else None,
            ) from exc
        except InvalidRefreshToken as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired refresh token",
                headers=_clear_cookie_headers(settings) if from_cookie else None,
            ) from exc

        acting_guild, roles = await _roles_for(session, rotated.user_id)
        access = mint_access_token(
            user_id=rotated.user_id, guild_id=acting_guild, roles=roles, settings=settings
        )
        await session.commit()

    pair = TokenPair(access_token=access, refresh_token=rotated.token)
    if from_cookie:
        # Rotation invalidated the token the old cookie carried, so the cookie
        # MUST be replaced or the next reload would present a dead one.
        set_session_cookie(response, user_id=rotated.user_id, pair=pair, settings=settings)
    return pair


@router.post("/logout", summary="Revoke the presented refresh token")
async def logout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(_get_settings_dep)],
    body: LogoutRequest | None = None,
) -> dict[str, str]:
    """End the session for real: revoke server-side AND drop the cookie.

    The SPA's Sign out used to clear React state only, leaving the refresh token
    valid in the database until it expired — harmless while the token died with
    the tab, but a real hole now that the cookie makes sessions survive a reload.

    Always answers 200: an absent or already-dead token means the caller is
    logged out, which is the outcome they asked for.
    """
    presented = body.refresh_token if body is not None else None
    if not presented:
        try:
            presented = read_session_cookie(
                request.cookies.get(cookie_name(settings)), settings=settings
            ).refresh_token
        except InvalidSessionCookie:
            presented = None

    if presented:
        async with SessionLocal() as session, session.begin():
            await revoke_refresh_token(session, token=presented)
    clear_session_cookie(response, settings)
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

        # Give the paired player a baseline `player` role in the guild they ran
        # /register from (``origin_guild_id``) so their session token carries an
        # acting guild (``gid``) and they can act as a guild member — create a
        # team, join tournaments. Idempotent (skip if they already hold any role
        # there, e.g. captain/admin); scoped under RLS by setting the guild GUC
        # first (mirrors dev-login's role seed).
        origin_guild_id = attempt.origin_guild_id
        if origin_guild_id is not None:
            await session.execute(
                text("SELECT set_config('app.current_guild', :g, true)"),
                {"g": str(origin_guild_id)},
            )
            has_role = (
                await session.execute(
                    select(UserGuildRole.id).where(
                        UserGuildRole.user_id == attempt.user_id,
                        UserGuildRole.guild_id == origin_guild_id,
                    )
                )
            ).first()
            if has_role is None:
                session.add(
                    UserGuildRole(
                        user_id=attempt.user_id,
                        guild_id=origin_guild_id,
                        role=UserGuildRoleName.PLAYER,
                    )
                )
                await session.flush()

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
    response: Response,
    settings: Annotated[Settings, Depends(_get_settings_dep)],
) -> TokenPair:
    """Mint a real admin token for local development — **disabled in production**.

    Upserts a dev guild + dev user with the ``platform_operator`` role and issues
    a normal access/refresh pair, so the SPA AND the backend API both work without
    a configured Discord OAuth app. Returns 404 when ``ENVIRONMENT=production`` so
    it can never be a production backdoor.

    Sets the same session cookie the OAuth callback does. Without it a dev session
    died on the first page reload — the very behaviour the cookie exists to fix,
    left broken on the path developers actually use.
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
    set_session_cookie(response, user_id=str(user.id), pair=pair, settings=settings)
    return pair
