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

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from lol_schemas.enums import UserGuildRoleName

from app.auth import discord_oauth
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
from app.models import OAuthCredential, RefreshToken, User, UserGuildRole
from app.security import decrypt


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


class _FakeRequest:
    """Minimal stand-in for starlette Request — the callback only reads cookies."""

    def __init__(self, cookies: dict[str, str]) -> None:
        self.cookies = cookies


class _FakeResponse:
    """Minimal stand-in for Response — the callback only clears a cookie."""

    def delete_cookie(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeSession:
    """A dict-backed stand-in for AsyncSession covering the auth code paths."""

    def __init__(self) -> None:
        self.store: dict[type, list[Any]] = {
            User: [],
            RefreshToken: [],
            OAuthCredential: [],
            UserGuildRole: [],
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
    for model in (User, RefreshToken, OAuthCredential, UserGuildRole):
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
    tampered = f"{head}.{payload}.{sig[:-2]}xx"
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
    assert result.access_token
    assert result.refresh_token

    # The access JWT verifies and carries our user id.
    user = session.store[User][0]
    claims = verify_access_token(result.access_token)
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
