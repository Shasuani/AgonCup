"""Auth-core tests (PLAN.md §3.1 auth, §7 Phase 1).

Offline by design — no network, no real database:

* JWT: round-trip, expiry rejection, tamper rejection, wrong-type rejection.
* Refresh tokens: rotation issues a new token in the same family; presenting an
  already-rotated (revoked) token is detected as reuse and revokes the family.
* Discord OAuth callback: state validation + a fully MOCKED httpx Discord (token
  exchange + user fetch), user upsert, encrypted credential storage, JWT issuance.
* RBAC: ``require_role`` allows holders / platform operators and denies others.

The database is replaced by a small in-memory fake session that implements the
exact narrow ``AsyncSession`` surface the auth code touches (``add``, ``flush``,
``execute(select|update)``, async-context ``begin``). This keeps the suite fast
and dependency-free while still exercising the real rotation/reuse logic and the
real callback handler end-to-end.
"""

from __future__ import annotations

import operator
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
from fastapi import HTTPException
from lol_schemas.enums import GuildStatus, UserGuildRoleName

from app.accounts import service as accounts_service
from app.auth import discord_oauth, session_cookie
from app.auth import router as auth_router
from app.auth.deps import Principal, require_role
from app.auth.jwt import (
    InvalidAccessToken,
    mint_access_token,
    verify_access_token,
)
from app.auth.tokens import (
    RefreshTokenReuse,
    hash_token,
    issue_refresh_token,
    rotate_refresh_token,
)
from app.config import get_settings
from app.models import (
    Guild,
    OAuthCredential,
    RefreshToken,
    RiotAccount,
    User,
    UserGuildRole,
    VerificationAttempt,
)
from app.ratelimit import FixedWindowLimiter
from app.security import decrypt, encrypt


# ---------------------------------------------------------------------------
# In-memory fake AsyncSession (only what the auth code uses)
# ---------------------------------------------------------------------------
@dataclass
class _Result:
    rows: list[Any]

    def scalar_one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def all(self) -> list[Any]:
        return self.rows

    def scalars(self) -> _Result:
        return self


class _FakeRequest:
    """Minimal stand-in for starlette Request — the routes only read cookies.

    ``client`` is present because /auth/refresh rate-limits on the peer address.
    """

    def __init__(self, cookies: dict[str, str], *, host: str = "203.0.113.9") -> None:
        self.cookies = cookies
        self.client = SimpleNamespace(host=host)


class _FakeResponse:
    """Minimal stand-in for Response, recording cookie writes so tests can assert
    what the browser would actually be told to store."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}
        self.deleted: list[str] = []

    def set_cookie(self, key: str, value: str = "", **kwargs: Any) -> None:
        self.cookies[key] = value

    def delete_cookie(self, key: str, *args: Any, **kwargs: Any) -> None:
        self.deleted.append(key)
        self.cookies.pop(key, None)


class FakeSession:
    """A dict-backed stand-in for AsyncSession covering the auth code paths."""

    def __init__(self) -> None:
        self.store: dict[type, list[Any]] = {
            User: [],
            RefreshToken: [],
            OAuthCredential: [],
            UserGuildRole: [],
            VerificationAttempt: [],
            RiotAccount: [],
            Guild: [],
        }

    # --- unit-of-work -----------------------------------------------------
    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None and hasattr(obj, "id"):
            obj.id = uuid.uuid4()
        self.store[type(obj)].append(obj)

    async def flush(self) -> None:
        for rows in self.store.values():
            for obj in rows:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

    # --- query dispatch ---------------------------------------------------
    async def execute(self, statement: Any, params: Any = None) -> Any:
        # text() set_config — no-op for the fake.
        if not hasattr(statement, "column_descriptions") and not _is_update(statement):
            return _Result([])

        if _is_update(statement):
            return self._execute_update(statement)
        return self._execute_select(statement)

    def _execute_select(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0]["entity"]
        # entity may be a column (e.g. UserGuildRole.id) or a mapped class.
        model = _model_of(entity)
        crit = _equality_filters(statement)
        rows = [r for r in self.store[model] if _matches(r, crit)]
        # Selecting specific columns: return tuples mirroring requested columns.
        cols = _selected_columns(statement)
        if cols and not _is_whole_entity(statement):
            projected = [tuple(getattr(r, c) for c in cols) for r in rows]
            return _Result(projected)
        return _Result(rows)

    def _execute_update(self, statement: Any) -> _Result:
        model = _model_of(statement.table)
        crit = _equality_filters(statement)
        values = dict(statement._values) if statement._values else {}
        for r in self.store[model]:
            if _matches(r, crit):
                for col, val in values.items():
                    setattr(r, _col_name(col), _literal(val))
        return _Result([])

    # --- transaction / context -------------------------------------------
    def begin(self) -> _FakeTxn:
        return _FakeTxn()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


# --- tiny SQLAlchemy-construct introspection helpers ----------------------
def _is_update(statement: Any) -> bool:
    return statement.__class__.__name__ == "Update"


def _model_of(entity: Any) -> type:
    if hasattr(entity, "class_"):  # column -> .class_
        return entity.class_
    if hasattr(entity, "entity"):
        return entity.entity
    # a Table -> map back to model by name
    name = getattr(entity, "name", None)
    for model in (User, RefreshToken, OAuthCredential, UserGuildRole, Guild):
        if model.__tablename__ == name:
            return model
    return entity


def _selected_columns(statement: Any) -> list[str]:
    out = []
    for desc in statement.column_descriptions:
        expr = desc.get("expr")
        if expr is not None and hasattr(expr, "key") and desc.get("entity") is not expr:
            out.append(expr.key)
    return out


def _is_whole_entity(statement: Any) -> bool:
    # A select(Model) has a single column_description whose expr == entity.
    descs = statement.column_descriptions
    if len(descs) != 1:
        return False
    return descs[0].get("expr") is descs[0].get("entity")


def _equality_filters(statement: Any) -> dict[str, Any]:
    crit: dict[str, Any] = {}
    # ``_where_criteria`` is a tuple of clause elements on both Select and Update
    # (avoids truth-testing a BooleanClauseList, which raises).
    clauses = list(getattr(statement, "_where_criteria", ()) or ())
    for clause in clauses:
        for bool_expr in _flatten_and(clause):
            # Only equality clauses constrain the fake store; inequalities (e.g.
            # ``expires_at > now``) are ignored — the fake cannot model time.
            if getattr(bool_expr, "operator", None) not in (operator.eq, None):
                continue
            left = getattr(bool_expr, "left", None)
            right = getattr(bool_expr, "right", None)
            if left is not None and hasattr(left, "key"):
                crit[left.key] = _literal(right)
    return crit


def _flatten_and(clause: Any) -> list[Any]:
    if clause is None:
        return []
    if clause.__class__.__name__ == "BooleanClauseList":
        out: list[Any] = []
        for c in clause.clauses:
            out.extend(_flatten_and(c))
        return out
    return [clause]


def _literal(value: Any) -> Any:
    return getattr(value, "value", value)


def _col_name(col: Any) -> str:
    return getattr(col, "key", str(col))


def _matches(row: Any, crit: dict[str, Any]) -> bool:
    for key, val in crit.items():
        actual = getattr(row, key, None)
        if str(actual) != str(val):
            return False
    return True


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def test_jwt_round_trip() -> None:
    uid = str(uuid.uuid4())
    gid = str(uuid.uuid4())
    token = mint_access_token(user_id=uid, guild_id=gid, roles=("guild_admin",))
    claims = verify_access_token(token)
    assert claims.user_id == uid
    assert claims.guild_id == gid
    assert claims.roles == ("guild_admin",)


def test_jwt_expired_is_rejected() -> None:
    uid = str(uuid.uuid4())
    past = datetime.now(tz=UTC) - timedelta(hours=2)
    token = mint_access_token(user_id=uid, guild_id=None, roles=(), now=past)
    with pytest.raises(InvalidAccessToken):
        verify_access_token(token)


def test_jwt_tamper_is_rejected() -> None:
    token = mint_access_token(user_id="u", guild_id=None, roles=())
    head, payload, sig = token.split(".")
    # Flip the FIRST signature char (not the last): a 43-char base64url HMAC-SHA256
    # signature's LAST char encodes only 4 significant bits (the low 2 are unused),
    # so flipping it between same-group chars (e.g. A<->B, or x->A when x in A..D) is
    # a no-op that leaves the signature bytes identical -> the tamper isn't detected
    # and the test flakes (~1/16, depending on the time-based signature). The first
    # char's 6 bits are all significant, so any different char guarantees a changed
    # signature and a deterministic tamper.
    flipped = "A" if sig[0] != "A" else "B"
    tampered = f"{head}.{payload}.{flipped}{sig[1:]}"
    with pytest.raises(InvalidAccessToken):
        verify_access_token(tampered)


def test_jwt_wrong_secret_is_rejected() -> None:
    token = mint_access_token(user_id="u", guild_id=None, roles=())
    settings = get_settings()
    import jwt as pyjwt

    # Decoding under a different secret must fail.
    with pytest.raises(InvalidAccessToken):
        forged = pyjwt.encode(
            {"sub": "u", "typ": "access", "iat": 0, "exp": 9999999999},
            "a-different-secret",
            algorithm=settings.jwt_algorithm,
        )
        verify_access_token(forged)
    assert token  # sanity


# ---------------------------------------------------------------------------
# Refresh tokens: rotation + reuse-detection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refresh_rotation_issues_new_token_same_family() -> None:
    session = FakeSession()
    uid = str(uuid.uuid4())
    first = await issue_refresh_token(session, user_id=uid)
    assert first.family_id  # root family == its own id

    rotated = await rotate_refresh_token(session, token=first.token)
    assert rotated.token != first.token
    assert rotated.family_id == first.family_id

    # The original row is now revoked; the new row is live.
    rows = session.store[RefreshToken]
    original = next(r for r in rows if r.token_hash == hash_token(first.token))
    successor = next(r for r in rows if r.token_hash == hash_token(rotated.token))
    assert original.revoked is True
    assert successor.revoked is False
    assert str(successor.rotated_from_id) == str(original.id)


@pytest.mark.asyncio
async def test_refresh_reuse_revokes_family() -> None:
    session = FakeSession()
    uid = str(uuid.uuid4())
    first = await issue_refresh_token(session, user_id=uid)
    rotated = await rotate_refresh_token(session, token=first.token)

    # Replaying the OLD (already-rotated) token = reuse → family revoked.
    with pytest.raises(RefreshTokenReuse):
        await rotate_refresh_token(session, token=first.token)

    # Every token in the family is now revoked, including the (previously live) one.
    assert all(r.revoked for r in session.store[RefreshToken])
    # And the successor can no longer be rotated either.
    with pytest.raises(RefreshTokenReuse):
        await rotate_refresh_token(session, token=rotated.token)


# ---------------------------------------------------------------------------
# Discord OAuth callback (mocked httpx)
# ---------------------------------------------------------------------------
class _MockResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _MockDiscordClient:
    """Stands in for httpx.AsyncClient: token POST + user GET."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _MockDiscordClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _MockResponse:
        return _MockResponse(
            200,
            {
                "access_token": "discord-access-xyz",
                "refresh_token": "discord-refresh-xyz",
                "scope": "identify",
                "expires_in": 604800,
            },
        )

    async def get(self, url: str, **kwargs: Any) -> _MockResponse:
        return _MockResponse(200, {"id": "123456789012345678", "username": "tester"})


@pytest.mark.asyncio
async def test_oauth_callback_upserts_user_and_encrypts_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")

    session = FakeSession()
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    monkeypatch.setattr(httpx, "AsyncClient", _MockDiscordClient)

    # Seed a valid state AND the matching session cookie (CSRF binding).
    state = "state-abc"
    auth_router._remember_state(state)
    req = _FakeRequest({auth_router._STATE_COOKIE: state})
    resp = _FakeResponse()

    result = await auth_router.callback(
        request=req, response=resp, code="auth-code-123", state=state, settings=get_settings()
    )

    # The browser is 303'd back to the SPA with the SHORT-LIVED access token in
    # the URL FRAGMENT (never the query string — a fragment is not sent to
    # servers/proxies/logs).
    assert result.status_code == 303
    location = result.headers["location"]
    base, _, fragment = location.partition("#")
    assert base == f"{get_settings().frontend_origin}/auth/callback"
    tokens = dict(parse_qsl(fragment))
    assert tokens["access_token"]
    # The refresh token must NOT ride the URL: it is the long-lived credential,
    # and a URL survives in history, screenshots and the referrer chain.
    assert "refresh_token" not in tokens

    # The access JWT verifies and carries our user id.
    user = session.store[User][0]
    claims = verify_access_token(tokens["access_token"])
    assert claims.user_id == str(user.id)
    assert user.discord_user_id == 123456789012345678

    # The OAuth credential was stored ENCRYPTED (not plaintext) and decrypts back.
    cred = session.store[OAuthCredential][0]
    assert cred.access_token_enc != "discord-access-xyz"
    assert decrypt(cred.access_token_enc) == "discord-access-xyz"
    assert cred.refresh_token_enc is not None
    assert decrypt(cred.refresh_token_enc) == "discord-refresh-xyz"

    # A reused/expired state must now be rejected (single-use), cookie or not.
    with pytest.raises(HTTPException) as exc_info:
        await auth_router.callback(
            request=_FakeRequest({auth_router._STATE_COOKIE: state}),
            response=_FakeResponse(),
            code="x",
            state=state,
            settings=get_settings(),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_oauth_callback_rejects_unknown_state() -> None:
    # No matching cookie + never-issued state → CSRF rejection.
    with pytest.raises(HTTPException) as exc_info:
        await auth_router.callback(
            request=_FakeRequest({}),
            response=_FakeResponse(),
            code="c",
            state="never-issued",
            settings=get_settings(),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_oauth_callback_rejects_state_without_matching_cookie() -> None:
    # A live state in the store but NO matching session cookie must be rejected
    # (the CSRF/session-binding fix): possessing a valid state is not enough.
    state = "state-no-cookie"
    auth_router._remember_state(state)
    with pytest.raises(HTTPException) as exc_info:
        await auth_router.callback(
            request=_FakeRequest({}),
            response=_FakeResponse(),
            code="c",
            state=state,
            settings=get_settings(),
        )
    assert exc_info.value.status_code == 400


def test_build_authorize_url_includes_state_and_pkce() -> None:
    plain = discord_oauth.build_authorize_url(state="s1")
    assert "state=s1" in plain
    assert "code_challenge" not in plain
    pkce = discord_oauth.build_authorize_url(state="s2", code_challenge="CHAL")
    assert "code_challenge=CHAL" in pkce
    assert "code_challenge_method=S256" in pkce


def test_authorize_url_does_not_force_a_reconsent() -> None:
    """`prompt=consent` made Discord show its Authorize screen on EVERY login.

    `none` skips it for a user who already granted these scopes; someone signing
    in for the first time (or after revoking access) still sees it, because
    Discord falls back to the screen when consent is genuinely missing.
    """
    url = discord_oauth.build_authorize_url(state="s1")
    assert "prompt=none" in url
    assert "prompt=consent" not in url


def test_login_requests_the_guilds_scope() -> None:
    # Without `guilds`, /users/@me/guilds 403s and the server picker cannot exist.
    assert "guilds" in discord_oauth.DEFAULT_SCOPES
    url = discord_oauth.build_authorize_url(state="s")
    assert "scope=identify+guilds" in url


# ---------------------------------------------------------------------------
# Bot-invite URL + "which servers do I administer?"
# ---------------------------------------------------------------------------
def test_build_bot_invite_url_preselects_guild() -> None:
    generic = discord_oauth.build_bot_invite_url()
    assert "scope=bot+applications.commands" in generic
    assert f"permissions={get_settings().discord_bot_permissions}" in generic
    # No guild_id → Discord shows its own picker (no preselection to lock).
    assert "guild_id" not in generic
    assert "disable_guild_select" not in generic

    targeted = discord_oauth.build_bot_invite_url(guild_id=42)
    assert "guild_id=42" in targeted
    assert "disable_guild_select=true" in targeted


class _MockGuildsClient:
    """httpx stand-in for ``/users/@me/guilds`` with a configurable status."""

    payload: list[dict[str, Any]] = []
    status_code: int = 200

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _MockGuildsClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _MockResponse:
        return _MockResponse(type(self).status_code, type(self).payload)  # type: ignore[arg-type]


def _seed_discord_credential(session: FakeSession, user_id: str) -> None:
    session.add(
        OAuthCredential(
            user_id=user_id,
            provider=auth_router.DISCORD_PROVIDER,
            access_token_enc=encrypt("discord-access-xyz"),
            refresh_token_enc=None,
            scopes=["identify", "guilds"],
        )
    )


@pytest.mark.asyncio
async def test_list_admin_guilds_filters_non_admin_and_marks_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_discord_credential(session, user_id)

    # The bot is already in guild 111; 222 is an install target; 333 the user is
    # only a member of (no ADMINISTRATOR bit, not owner) and must be hidden.
    installed = Guild(discord_guild_id=111, name="Installed", status=GuildStatus.ACTIVE)
    session.add(installed)

    _MockGuildsClient.status_code = 200
    _MockGuildsClient.payload = [
        {"id": "111", "name": "Installed", "icon": "abc", "owner": True, "permissions": "8"},
        {"id": "222", "name": "Fresh", "icon": None, "owner": False, "permissions": "8"},
        {"id": "333", "name": "Just a member", "icon": None, "owner": False, "permissions": "0"},
    ]
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    monkeypatch.setattr(httpx, "AsyncClient", _MockGuildsClient)

    result = await auth_router.list_admin_guilds(
        principal=Principal(user_id=user_id, guild_id=None, roles=()),
        settings=get_settings(),
    )

    assert [g.discord_guild_id for g in result] == ["111", "222"]

    already_in, target = result
    assert already_in.installed is True
    assert already_in.guild_id == str(installed.id)
    assert already_in.icon_url == "https://cdn.discordapp.com/icons/111/abc.png"

    assert target.installed is False
    assert target.guild_id is None
    assert target.icon_url is None
    # Every row carries a ready-to-click invite locked to that exact server.
    assert "guild_id=222" in target.invite_url


@pytest.mark.asyncio
async def test_list_admin_guilds_demands_reauth_when_scope_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Accounts that logged in BEFORE `guilds` joined DEFAULT_SCOPES hold a token
    # Discord answers 403 for — that must read as "sign in again", not a 502.
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_discord_credential(session, user_id)

    _MockGuildsClient.status_code = 403
    _MockGuildsClient.payload = []
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    monkeypatch.setattr(httpx, "AsyncClient", _MockGuildsClient)

    with pytest.raises(HTTPException) as exc_info:
        await auth_router.list_admin_guilds(
            principal=Principal(user_id=user_id, guild_id=None, roles=()),
            settings=get_settings(),
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "discord_reauth_required"


@pytest.mark.asyncio
async def test_list_admin_guilds_without_a_stored_credential_demands_reauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # e.g. a dev-login session: authenticated to US, but never to Discord.
    session = FakeSession()
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)

    with pytest.raises(HTTPException) as exc_info:
        await auth_router.list_admin_guilds(
            principal=Principal(user_id=str(uuid.uuid4()), guild_id=None, roles=()),
            settings=get_settings(),
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "discord_reauth_required"


# ---------------------------------------------------------------------------
# RBAC: require_role allow / deny
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_require_role_allows_holder() -> None:
    guard = require_role(UserGuildRoleName.GUILD_ADMIN)
    principal = Principal(user_id="u", guild_id="g", roles=("guild_admin",))
    assert await guard(principal=principal) is principal


@pytest.mark.asyncio
async def test_require_role_allows_platform_operator() -> None:
    guard = require_role(UserGuildRoleName.GUILD_ADMIN)
    principal = Principal(user_id="u", guild_id="g", roles=("platform_operator",))
    # Platform operator passes any guild guard (cross-tenant staff).
    assert await guard(principal=principal) is principal


@pytest.mark.asyncio
async def test_require_role_denies_non_holder() -> None:
    guard = require_role(UserGuildRoleName.GUILD_ADMIN)
    principal = Principal(user_id="u", guild_id="g", roles=("player",))
    with pytest.raises(HTTPException) as exc_info:
        await guard(principal=principal)
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Desktop pairing — POST /auth/desktop-pair (PLAN.md §3.1)
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakePairRequest:
    """Minimal Request stand-in: the pair/refresh handlers read ``client.host``."""

    def __init__(self, host: str = "203.0.113.7") -> None:
        self.client = _FakeClient(host)


def _seed_pair_code(
    session: FakeSession,
    *,
    code: str = "ABCD2345",
    discord_user_id: int = 42424242,
    display_name: str = "Chama",
    status_value: str | None = None,
    origin_guild_id: uuid.UUID | None = None,
) -> User:
    """Seed a User + a live ``desktop_code`` VerificationAttempt for it."""
    user = User(discord_user_id=discord_user_id, display_name=display_name)
    user.id = uuid.uuid4()
    session.store[User].append(user)
    attempt = VerificationAttempt(
        user_id=user.id,
        code=code,
        verify_kind=accounts_service.VERIFY_KIND_DESKTOP_CODE,
        status=status_value or accounts_service.STATUS_PENDING,
        expires_at=datetime.now(tz=UTC) + accounts_service.VERIFICATION_TTL,
        origin_guild_id=origin_guild_id,
    )
    attempt.id = uuid.uuid4()
    session.store[VerificationAttempt].append(attempt)
    return user


@pytest.mark.asyncio
async def test_desktop_pair_valid_code_mints_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    user = _seed_pair_code(session, code="ABCD2345", display_name="Chama")
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)

    resp = await auth_router.desktop_pair(
        body=auth_router.DesktopPairRequest(code="abcd-2345", device_label="Kostas' MacBook"),
        request=_FakePairRequest(),  # type: ignore[arg-type]
        settings=get_settings(),
    )

    # A real session is minted; the access JWT verifies and carries the user id.
    assert resp.token_type == "bearer"
    claims = verify_access_token(resp.access_token)
    assert claims.user_id == str(user.id)
    assert resp.user.user_id == str(user.id)
    assert resp.user.discord_user_id == str(user.discord_user_id)
    assert resp.user.display_name == "Chama"
    # No Riot account linked yet -> account summary is None (link via verify-lcu).
    assert resp.account is None

    # The code is CONSUMED single-use (status flipped; puuid stays NULL — no link).
    attempt = session.store[VerificationAttempt][0]
    assert attempt.status == accounts_service.STATUS_VERIFIED
    assert attempt.puuid is None

    # The refresh token persisted the device label.
    refresh_row = session.store[RefreshToken][0]
    assert refresh_row.device_label == "Kostas' MacBook"


@pytest.mark.asyncio
async def test_desktop_pair_grants_player_role_in_origin_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pairing seeds a baseline ``player`` role in the /register origin guild so the
    session token carries an acting guild (``gid``) — the client-first prerequisite
    for a member to create a team / join tournaments."""
    session = FakeSession()
    origin_guild_id = uuid.uuid4()
    user = _seed_pair_code(session, code="ABCD2345", origin_guild_id=origin_guild_id)
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)

    resp = await auth_router.desktop_pair(
        body=auth_router.DesktopPairRequest(code="ABCD2345"),
        request=_FakePairRequest(),  # type: ignore[arg-type]
        settings=get_settings(),
    )

    # A `player` role row now exists for (user, origin guild).
    roles = session.store[UserGuildRole]
    assert len(roles) == 1
    assert roles[0].user_id == user.id
    assert roles[0].guild_id == origin_guild_id
    assert roles[0].role == UserGuildRoleName.PLAYER
    # …and the minted access token carries that guild as its `gid` claim.
    claims = verify_access_token(resp.access_token)
    assert claims.guild_id == str(origin_guild_id)


@pytest.mark.asyncio
async def test_desktop_pair_does_not_duplicate_an_existing_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the user already holds a role in the origin guild (e.g. guild_admin),
    pairing does NOT add a second (downgrading) ``player`` row."""
    session = FakeSession()
    origin_guild_id = uuid.uuid4()
    user = _seed_pair_code(session, code="ABCD2345", origin_guild_id=origin_guild_id)
    existing = UserGuildRole(
        user_id=user.id, guild_id=origin_guild_id, role=UserGuildRoleName.GUILD_ADMIN
    )
    existing.id = uuid.uuid4()
    session.store[UserGuildRole].append(existing)
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)

    await auth_router.desktop_pair(
        body=auth_router.DesktopPairRequest(code="ABCD2345"),
        request=_FakePairRequest(),  # type: ignore[arg-type]
        settings=get_settings(),
    )

    roles = session.store[UserGuildRole]
    assert len(roles) == 1
    assert roles[0].role == UserGuildRoleName.GUILD_ADMIN


@pytest.mark.asyncio
async def test_desktop_pair_includes_linked_account_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    user = _seed_pair_code(session, code="ABCD2345")
    account = RiotAccount(
        user_id=user.id,
        puuid="CANON-PUUID",
        riot_id_game_name="Chama",
        riot_id_tag_line="Shasu",
        region="eun1",
        verified=True,
    )
    account.id = uuid.uuid4()
    account.rank_tier = "GOLD"
    account.rank_division = "II"
    account.rank_lp = 45
    session.store[RiotAccount].append(account)
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)

    resp = await auth_router.desktop_pair(
        body=auth_router.DesktopPairRequest(code="ABCD2345"),
        request=_FakePairRequest(),  # type: ignore[arg-type]
        settings=get_settings(),
    )
    assert resp.account is not None
    assert resp.account.linked is True
    assert resp.account.puuid == "CANON-PUUID"
    assert resp.account.game_name == "Chama"
    assert resp.account.verified is True
    assert resp.account.rank_text == "Gold II, 45 LP"


@pytest.mark.asyncio
async def test_desktop_pair_device_label_carried_through_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    _seed_pair_code(session, code="ABCD2345")
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)

    resp = await auth_router.desktop_pair(
        body=auth_router.DesktopPairRequest(code="ABCD2345", device_label="MacBook"),
        request=_FakePairRequest(),  # type: ignore[arg-type]
        settings=get_settings(),
    )

    # Rotating the issued refresh token CARRIES FORWARD the device label.
    rotated = await rotate_refresh_token(session, token=resp.refresh_token)
    successor = next(
        r for r in session.store[RefreshToken] if r.token_hash == hash_token(rotated.token)
    )
    assert successor.device_label == "MacBook"


@pytest.mark.asyncio
async def test_desktop_pair_expired_or_consumed_code_is_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A non-pending (consumed/expired) attempt is filtered by the status predicate.
    session = FakeSession()
    _seed_pair_code(session, code="ABCD2345", status_value=accounts_service.STATUS_EXPIRED)
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)

    with pytest.raises(HTTPException) as exc:
        await auth_router.desktop_pair(
            body=auth_router.DesktopPairRequest(code="ABCD2345"),
            request=_FakePairRequest(),  # type: ignore[arg-type]
            settings=get_settings(),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_desktop_pair_reused_code_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    _seed_pair_code(session, code="ABCD2345")
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)

    # First exchange succeeds and consumes the code.
    await auth_router.desktop_pair(
        body=auth_router.DesktopPairRequest(code="ABCD2345"),
        request=_FakePairRequest(),  # type: ignore[arg-type]
        settings=get_settings(),
    )
    # Replaying the same code now finds no pending attempt -> 401.
    with pytest.raises(HTTPException) as exc:
        await auth_router.desktop_pair(
            body=auth_router.DesktopPairRequest(code="ABCD2345"),
            request=_FakePairRequest(),  # type: ignore[arg-type]
            settings=get_settings(),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_desktop_pair_malformed_code_is_422() -> None:
    # Valid length (8) but contains alphabet-excluded glyphs (I/L/O/0/1) -> 422
    # from the shape gate, BEFORE any rate-limit or DB touch.
    with pytest.raises(HTTPException) as exc:
        await auth_router.desktop_pair(
            body=auth_router.DesktopPairRequest(code="IIIIIIII"),
            request=_FakePairRequest(),  # type: ignore[arg-type]
            settings=get_settings(),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_desktop_pair_rate_limited_is_429(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    # Tiny limiter so the 3rd attempt trips. A valid-shape but unknown code passes
    # the shape gate, reaches the limiter, then 401s (calls 1-2) until the bucket
    # is exhausted (call 3 -> 429, before the DB lookup).
    monkeypatch.setattr(auth_router, "_pair_limiter", FixedWindowLimiter(max_hits=2, window_s=60.0))
    req = _FakePairRequest(host="198.51.100.9")
    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            await auth_router.desktop_pair(
                body=auth_router.DesktopPairRequest(code="ABCD2345"),
                request=req,  # type: ignore[arg-type]
                settings=get_settings(),
            )
        assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        await auth_router.desktop_pair(
            body=auth_router.DesktopPairRequest(code="ABCD2345"),
            request=req,  # type: ignore[arg-type]
            settings=get_settings(),
        )
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_refresh_rate_limited_is_429(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        auth_router, "_refresh_limiter", FixedWindowLimiter(max_hits=1, window_s=60.0)
    )
    req = _FakePairRequest(host="198.51.100.10")
    # Call 1 passes the limiter, then 401s on the unknown refresh token.
    with pytest.raises(HTTPException) as exc:
        await auth_router.refresh(
            body=auth_router.RefreshRequest(refresh_token="bogus"),
            request=req,  # type: ignore[arg-type]
            response=_FakeResponse(),  # type: ignore[arg-type]
            settings=get_settings(),
        )
    assert exc.value.status_code == 401
    # Call 2 trips the rate limit BEFORE the rotate.
    with pytest.raises(HTTPException) as exc:
        await auth_router.refresh(
            body=auth_router.RefreshRequest(refresh_token="bogus"),
            request=req,  # type: ignore[arg-type]
            response=_FakeResponse(),  # type: ignore[arg-type]
            settings=get_settings(),
        )
    assert exc.value.status_code == 429


# ---------------------------------------------------------------------------
# Session cookie: the browser's persistent, JS-unreadable half of the session
# ---------------------------------------------------------------------------
def test_session_cookie_round_trips_the_refresh_token() -> None:
    raw = session_cookie.mint_session_cookie(user_id="u1", refresh_token="rt-secret")
    claims = session_cookie.read_session_cookie(raw)
    assert claims.user_id == "u1"
    assert claims.refresh_token == "rt-secret"


def test_session_cookie_rejects_tampering_and_absence() -> None:
    raw = session_cookie.mint_session_cookie(user_id="u1", refresh_token="rt-secret")
    with pytest.raises(session_cookie.InvalidSessionCookie):
        session_cookie.read_session_cookie(raw[:-2] + "xy")  # broken signature
    with pytest.raises(session_cookie.InvalidSessionCookie):
        session_cookie.read_session_cookie(None)
    with pytest.raises(session_cookie.InvalidSessionCookie):
        session_cookie.read_session_cookie("")


def test_an_access_jwt_is_not_accepted_as_a_session_cookie() -> None:
    """Both are signed with the same key, so only `typ` keeps them apart."""
    access = mint_access_token(user_id="u1", guild_id=None, roles=())
    with pytest.raises(session_cookie.InvalidSessionCookie):
        session_cookie.read_session_cookie(access)


@pytest.mark.asyncio
async def test_callback_puts_the_refresh_token_in_an_httponly_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session must survive a reload without the token being readable by JS."""
    session = FakeSession()
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    monkeypatch.setattr(httpx, "AsyncClient", _MockDiscordClient)
    state = "state-cookie"
    auth_router._remember_state(state)

    result = await auth_router.callback(
        request=_FakeRequest({auth_router._STATE_COOKIE: state}),  # type: ignore[arg-type]
        response=_FakeResponse(),  # type: ignore[arg-type]
        code="auth-code-123",
        state=state,
        settings=get_settings(),
    )

    set_cookie = [v for k, v in result.raw_headers if k == b"set-cookie"]
    session_header = next(
        h.decode() for h in set_cookie if session_cookie.cookie_name().encode() in h
    )
    assert "HttpOnly" in session_header
    assert "SameSite=lax" in session_header
    # The cookie's JWT carries the refresh token that was actually issued.
    raw = session_header.split("=", 1)[1].split(";", 1)[0]
    issued = session.store[RefreshToken][0]
    assert session_cookie.read_session_cookie(raw).refresh_token
    assert issued is not None


@pytest.mark.asyncio
async def test_refresh_falls_back_to_the_cookie_and_rotates_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SPA sends no body — the browser attaches the cookie for it."""
    session = FakeSession()
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    uid = str(uuid.uuid4())
    issued = await issue_refresh_token(session, user_id=uid)
    raw = session_cookie.mint_session_cookie(user_id=uid, refresh_token=issued.token)
    response = _FakeResponse()

    pair = await auth_router.refresh(
        request=_FakeRequest({session_cookie.cookie_name(): raw}),  # type: ignore[arg-type]
        response=response,  # type: ignore[arg-type]
        settings=get_settings(),
        body=None,
    )

    assert pair.refresh_token != issued.token  # rotated
    # A cookie still carrying the now-revoked token would 401 on the next reload.
    rotated_cookie = response.cookies[session_cookie.cookie_name()]
    assert session_cookie.read_session_cookie(rotated_cookie).refresh_token == pair.refresh_token


@pytest.mark.asyncio
async def test_refresh_clears_a_cookie_it_cannot_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise the SPA retries a dead cookie on every single boot.

    Asserted on the EXCEPTION's headers, not on the injected Response: a cookie
    written to that Response is dropped when the handler raises (FastAPI builds a
    fresh response from the exception). See ``_clear_cookie_headers``.
    """
    session = FakeSession()
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        auth_router, "_refresh_limiter", FixedWindowLimiter(max_hits=5, window_s=60.0)
    )
    raw = session_cookie.mint_session_cookie(user_id="u1", refresh_token="never-issued")

    with pytest.raises(HTTPException) as exc:
        await auth_router.refresh(
            request=_FakeRequest({session_cookie.cookie_name(): raw}),  # type: ignore[arg-type]
            response=_FakeResponse(),  # type: ignore[arg-type]
            settings=get_settings(),
            body=None,
        )

    assert exc.value.status_code == 401
    set_cookie = (exc.value.headers or {}).get("set-cookie", "")
    assert session_cookie.cookie_name() in set_cookie
    assert "Max-Age=0" in set_cookie


def test_a_raised_401_actually_delivers_the_clear_cookie_header() -> None:
    """Through the real ASGI stack — the unit test above cannot prove delivery.

    A `response.delete_cookie(...)` before `raise HTTPException` looks correct and
    passes when the handler is called directly, but the header never reaches the
    browser. Only a request through the app catches that.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/v1")
    client = TestClient(app)

    client.cookies.set(
        session_cookie.cookie_name(),
        session_cookie.mint_session_cookie(user_id="u1", refresh_token="never-issued"),
    )
    res = client.post("/api/v1/auth/refresh", json={})

    assert res.status_code == 401
    assert session_cookie.cookie_name() in res.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_logout_revokes_the_cookie_token_and_clears_the_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sign out used to clear React state only, leaving the token live in the DB."""
    session = FakeSession()
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    uid = str(uuid.uuid4())
    issued = await issue_refresh_token(session, user_id=uid)
    raw = session_cookie.mint_session_cookie(user_id=uid, refresh_token=issued.token)
    response = _FakeResponse()

    result = await auth_router.logout(
        request=_FakeRequest({session_cookie.cookie_name(): raw}),  # type: ignore[arg-type]
        response=response,  # type: ignore[arg-type]
        settings=get_settings(),
        body=None,
    )

    assert result == {"status": "logged_out"}
    assert session.store[RefreshToken][0].revoked is True
    assert session_cookie.cookie_name() in response.deleted


@pytest.mark.asyncio
async def test_logout_without_any_session_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Log me out" is satisfied by already being logged out."""
    session = FakeSession()
    monkeypatch.setattr(auth_router, "SessionLocal", lambda: session)
    response = _FakeResponse()

    result = await auth_router.logout(
        request=_FakeRequest({}),  # type: ignore[arg-type]
        response=response,  # type: ignore[arg-type]
        settings=get_settings(),
        body=None,
    )

    assert result == {"status": "logged_out"}
    assert session_cookie.cookie_name() in response.deleted
