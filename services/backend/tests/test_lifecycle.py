"""Lifecycle tests: guild purge + user PII deletion (PLAN.md §3.2 / §7 Phase 1).

Offline by design — no network, no real database. The database is replaced by a
small in-memory fake session that implements the narrow ``AsyncSession`` surface
the lifecycle *service* touches (``execute(update|delete)`` returning a rowcount,
and ``RETURNING`` via ``scalar_one_or_none``). The service is exercised directly;
the routes are exercised with a fake session injected and dependencies invoked
by hand (FastAPI is not spun up), keeping the suite fast and dependency-free.

Coverage:

* ``purge_guild`` flips the anchor to ``removed`` + stamps ``removed_at`` (soft)
  and HARD-deletes every guild-scoped table; a missing guild raises.
* ``purge_guild_by_discord_id`` resolves the snowflake then purges.
* ``delete_user`` HARD-deletes the user (PII cascades at the DB); a missing user
  raises.
* ``require_service_token`` fails closed (503 unconfigured, 401 bad token, ok).
* the user-delete route enforces self-or-platform-operator.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.auth.deps import Principal
from app.lifecycle import router as lifecycle_router
from app.lifecycle import service
from app.lifecycle.service import (
    GuildNotFoundError,
    UserNotFoundError,
    delete_user,
    purge_guild,
    purge_guild_by_discord_id,
)
from app.models import Guild, RefreshToken, User
from app.models.tables import Match, Team, Tournament, UserGuildRole


# ---------------------------------------------------------------------------
# In-memory fake AsyncSession (only what the lifecycle service uses)
# ---------------------------------------------------------------------------
@dataclass
class _Result:
    rowcount: int = 0
    _scalar: Any = None

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class FakeSession:
    """A dict-backed stand-in for AsyncSession covering the purge/delete paths.

    Rows are seeded per model; ``execute`` interprets the SQLAlchemy ``Update`` /
    ``Delete`` construct just enough to apply equality-filtered mutations and
    report a rowcount (mirrors the real DML's ``CursorResult.rowcount``).
    """

    def __init__(self) -> None:
        # Keyed by TABLE NAME so a DML against any guild-scoped table resolves even
        # if no rows were seeded for it (an unseeded table → 0-row no-op).
        self.store: dict[str, list[Any]] = {}

    def seed(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None and hasattr(obj, "id"):
            obj.id = uuid.uuid4()
        self.store.setdefault(obj.__tablename__, []).append(obj)

    def rows(self, model: type) -> list[Any]:
        """Read accessor for assertions: rows currently stored for ``model``."""
        return self.store.get(model.__tablename__, [])

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        kind = statement.__class__.__name__
        if kind == "Update":
            return self._execute_update(statement)
        if kind == "Delete":
            return self._execute_delete(statement)
        return _Result()

    # --- DML interpretation ----------------------------------------------
    def _execute_update(self, statement: Any) -> _Result:
        table = str(statement.table.name)
        crit = _equality_filters(statement)
        values = {_col_name(c): _literal(v) for c, v in (statement._values or {}).items()}
        returning = _returning_keys(statement)
        matched: list[Any] = []
        for row in self.store.get(table, []):
            if _matches(row, crit):
                matched.append(row)
                for col, val in values.items():
                    setattr(row, col, val)
        scalar = None
        if returning and matched:
            scalar = getattr(matched[0], returning[0])
        return _Result(rowcount=len(matched), _scalar=scalar)

    def _execute_delete(self, statement: Any) -> _Result:
        table = str(statement.table.name)
        crit = _equality_filters(statement)
        existing = self.store.get(table, [])
        kept = [r for r in existing if not _matches(r, crit)]
        removed = len(existing) - len(kept)
        self.store[table] = kept
        return _Result(rowcount=removed)

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
def _equality_filters(statement: Any) -> dict[str, Any]:
    crit: dict[str, Any] = {}
    for clause in list(getattr(statement, "_where_criteria", ()) or ()):
        left = getattr(clause, "left", None)
        right = getattr(clause, "right", None)
        if left is not None and hasattr(left, "key"):
            crit[left.key] = _literal(right)
    return crit


def _returning_keys(statement: Any) -> list[str]:
    return [c.key for c in getattr(statement, "_returning", ()) or () if hasattr(c, "key")]


def _literal(value: Any) -> Any:
    return getattr(value, "value", value)


def _col_name(col: Any) -> str:
    return getattr(col, "key", str(col))


def _matches(row: Any, crit: dict[str, Any]) -> bool:
    return all(str(getattr(row, key, None)) == str(val) for key, val in crit.items())


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
class _Row:
    """Lightweight scoped-row stand-in (the fake dispatches by table, not type)."""

    def __init__(self, guild_id: Any) -> None:
        self.id = uuid.uuid4()
        self.guild_id = guild_id


def _seed_guild_with_data(session: FakeSession, *, discord_guild_id: int = 4242) -> str:
    guild = Guild(name="g", discord_guild_id=discord_guild_id)
    guild.id = uuid.uuid4()
    guild.status = "active"
    guild.removed_at = None
    session.seed(guild)
    gid = guild.id
    # One guild-scoped row in this tenant + one in ANOTHER tenant (must survive).
    other = uuid.uuid4()
    for model in (Team, Tournament, Match, UserGuildRole):
        session.store.setdefault(model.__tablename__, []).append(_Row(gid))
        session.store.setdefault(model.__tablename__, []).append(_Row(other))
    return str(gid)


# ---------------------------------------------------------------------------
# purge_guild
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_purge_guild_soft_removes_anchor_and_hard_deletes_scoped() -> None:
    session = FakeSession()
    gid = _seed_guild_with_data(session)

    result = await purge_guild(session, guild_id=gid)

    # Anchor soft-deleted (kept, status flipped, timestamp stamped).
    guild = session.rows(Guild)[0]
    assert guild.status == "removed"
    assert isinstance(guild.removed_at, datetime)
    assert result.status == "removed"

    # Every guild-scoped table for THIS tenant is emptied; the other tenant's rows
    # survive (RLS/scoping by guild_id predicate).
    for model in (Team, Tournament, Match, UserGuildRole):
        remaining = session.rows(model)
        assert len(remaining) == 1
        assert str(remaining[0].guild_id) != gid
        assert result.deleted_rows[model.__tablename__] == 1

    assert result.total_deleted == 4


@pytest.mark.asyncio
async def test_purge_guild_missing_raises() -> None:
    session = FakeSession()
    with pytest.raises(GuildNotFoundError):
        await purge_guild(session, guild_id=str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_purge_guild_by_discord_id_resolves_then_purges() -> None:
    session = FakeSession()
    gid = _seed_guild_with_data(session, discord_guild_id=999000)

    result = await purge_guild_by_discord_id(session, discord_guild_id=999000)

    assert result.guild_id == gid
    assert session.rows(Guild)[0].status == "removed"
    assert result.total_deleted == 4


@pytest.mark.asyncio
async def test_purge_guild_by_discord_id_missing_raises() -> None:
    session = FakeSession()
    with pytest.raises(GuildNotFoundError):
        await purge_guild_by_discord_id(session, discord_guild_id=123)


# ---------------------------------------------------------------------------
# delete_user
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_user_removes_row() -> None:
    session = FakeSession()
    user = User(discord_user_id=777, display_name="x")
    user.id = uuid.uuid4()
    session.seed(user)

    result = await delete_user(session, user_id=str(user.id))

    assert result.deleted is True
    assert session.rows(User) == []


@pytest.mark.asyncio
async def test_delete_user_missing_raises() -> None:
    session = FakeSession()
    with pytest.raises(UserNotFoundError):
        await delete_user(session, user_id=str(uuid.uuid4()))


def _seed_refresh_token(session: FakeSession, *, user_id: Any) -> RefreshToken:
    rt = RefreshToken(
        user_id=user_id,
        token_hash=f"hash-{uuid.uuid4()}",
        family_id=uuid.uuid4(),
        revoked=False,
    )
    rt.id = uuid.uuid4()
    session.store.setdefault(RefreshToken.__tablename__, []).append(rt)
    return rt


@pytest.mark.asyncio
async def test_delete_user_revokes_refresh_tokens() -> None:
    # delete_user revokes the user's refresh tokens BEFORE the cascading DELETE,
    # while leaving ANOTHER user's tokens untouched.
    session = FakeSession()
    user = User(discord_user_id=888, display_name="x")
    user.id = uuid.uuid4()
    session.seed(user)
    mine = _seed_refresh_token(session, user_id=user.id)
    others = _seed_refresh_token(session, user_id=uuid.uuid4())

    result = await delete_user(session, user_id=str(user.id))

    assert result.deleted is True
    assert session.rows(User) == []
    assert mine.revoked is True  # the deleted user's token is revoked
    assert others.revoked is False  # an unrelated user's token is untouched


@pytest.mark.asyncio
async def test_purge_guild_leaves_refresh_tokens() -> None:
    # A guild purge is global-PII-safe: refresh tokens are identity-scoped, so a
    # user removed from one guild stays signed in elsewhere — tokens survive.
    session = FakeSession()
    gid = _seed_guild_with_data(session)
    rt = _seed_refresh_token(session, user_id=uuid.uuid4())

    await purge_guild(session, guild_id=gid)

    assert rt.revoked is False
    assert session.store.get(RefreshToken.__tablename__) == [rt]


# ---------------------------------------------------------------------------
# Service-auth dependency (fails closed)
# ---------------------------------------------------------------------------
def test_require_service_token_unconfigured_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(lifecycle_router.SERVICE_TOKEN_ENV, raising=False)
    with pytest.raises(HTTPException) as exc:
        lifecycle_router.require_service_token(x_service_token="anything")
    assert exc.value.status_code == 503


def test_require_service_token_bad_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lifecycle_router.SERVICE_TOKEN_ENV, "secret")
    with pytest.raises(HTTPException) as exc:
        lifecycle_router.require_service_token(x_service_token="wrong")
    assert exc.value.status_code == 401


def test_require_service_token_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lifecycle_router.SERVICE_TOKEN_ENV, "secret")
    assert lifecycle_router.require_service_token(x_service_token="secret") is None


# ---------------------------------------------------------------------------
# Route-level authorization for user deletion (self or platform operator)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_user_route_denies_other_user() -> None:
    principal = Principal(user_id=str(uuid.uuid4()), guild_id=None, roles=("player",))
    with pytest.raises(HTTPException) as exc:
        await lifecycle_router.delete_user(user_id=str(uuid.uuid4()), principal=principal)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_user_route_allows_self(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    user = User(discord_user_id=1, display_name="self")
    user.id = uuid.uuid4()
    session.seed(user)
    monkeypatch.setattr(lifecycle_router, "SessionLocal", lambda: session)

    principal = Principal(user_id=str(user.id), guild_id=None, roles=("player",))
    resp = await lifecycle_router.delete_user(user_id=str(user.id), principal=principal)
    assert resp.deleted is True
    assert session.rows(User) == []


@pytest.mark.asyncio
async def test_delete_user_route_allows_platform_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    target = User(discord_user_id=2, display_name="victim")
    target.id = uuid.uuid4()
    session.seed(target)
    monkeypatch.setattr(lifecycle_router, "SessionLocal", lambda: session)

    operator = Principal(user_id=str(uuid.uuid4()), guild_id=None, roles=("platform_operator",))
    resp = await lifecycle_router.delete_user(user_id=str(target.id), principal=operator)
    assert resp.deleted is True


# ---------------------------------------------------------------------------
# Internal guild-remove route (service-auth) — noop on unknown guild
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_internal_guild_remove_unknown_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(lifecycle_router, "SessionLocal", lambda: session)

    body = lifecycle_router.GuildRemoveRequest(discord_guild_id=55)
    resp = await lifecycle_router.internal_guild_remove(body=body, _auth=None)
    assert resp.status == "not_found"
    assert resp.total_deleted == 0


@pytest.mark.asyncio
async def test_internal_guild_remove_purges_known_guild(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    _seed_guild_with_data(session, discord_guild_id=314159)
    monkeypatch.setattr(lifecycle_router, "SessionLocal", lambda: session)

    body = lifecycle_router.GuildRemoveRequest(discord_guild_id=314159)
    resp = await lifecycle_router.internal_guild_remove(body=body, _auth=None)
    assert resp.status == "removed"
    assert resp.total_deleted == 4


# Sanity: the service module re-exports its public helpers under app.lifecycle.service.
def test_service_exports() -> None:
    assert service.purge_guild is purge_guild
    assert service.delete_user is delete_user
