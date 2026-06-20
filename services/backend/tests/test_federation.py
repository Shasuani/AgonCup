"""Federation central-feed tests (PLAN.md §3.2 is_shared_to_central, §9 privacy).

Offline by design — no network, no real DB, no Discord gateway. A small dict-backed
fake session (mirroring ``tests/_fake_session.py``'s approach but covering the
federation models the existing shared fake does not) exercises the privacy + lifecycle
guarantees of :mod:`app.federation.service`:

* **Projection whitelist** excludes sensitive fields (PUUID, Discord ids, summoner id,
  verification state) and surfaces ONLY safe display data — asserted both positively
  (only allowed keys present) and via the defence-in-depth deny list.
* **Per-player visibility** (``team_only`` default vs ``show_riot_id``) gates whether a
  player's Riot id is surfaced.
* **Consent gate**: a guild without ``central_feed_enabled`` cannot request a share.
* **Lifecycle transitions**: request → pending, approve → published, revoke → hidden;
  non-staff cannot approve/revoke; revoked items hidden from the feed; double-approve
  rejected.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.federation import service as federation
from app.models import (
    CentralFeedItem,
    Guild,
    PlayerCentralVisibility,
    Registration,
    RiotAccount,
    Tournament,
)


# ---------------------------------------------------------------------------
# Minimal fake session covering the federation query shapes
# ---------------------------------------------------------------------------
class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._rows[0]

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _Txn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


_MODELS: tuple[type, ...] = (
    Guild,
    Tournament,
    Registration,
    RiotAccount,
    PlayerCentralVisibility,
    CentralFeedItem,
)


class FakeSession:
    """Dict-backed AsyncSession stand-in for the federation service paths.

    Implements the narrow surface the service touches: model SELECTs with equality
    filters, ``func.count()`` aggregates, ``is_not(None)``/``is_(None)`` predicates,
    and ``order_by`` (best-effort), plus ``add``/``flush``. NO real DB/network.
    """

    def __init__(self) -> None:
        self.store: dict[type, list[Any]] = {m: [] for m in _MODELS}
        self.current_guild: str | None = None

    # --- unit of work -----------------------------------------------------
    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None and hasattr(obj, "id"):
            obj.id = uuid.uuid4()
        self.store[type(obj)].append(obj)

    async def flush(self) -> None:
        for rows in self.store.values():
            for obj in rows:
                if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                    obj.id = uuid.uuid4()

    def begin(self) -> _Txn:
        return _Txn()

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    # --- query dispatch ---------------------------------------------------
    async def execute(self, statement: Any, params: Any = None) -> _Result:
        if not hasattr(statement, "column_descriptions"):
            return self._execute_text(statement, params)
        return self._execute_select(statement)

    def _execute_text(self, statement: Any, params: Any) -> _Result:
        sql = str(statement).lower()
        if "set_config" in sql and params and "gid" in params:
            self.current_guild = str(params["gid"])
            return _Result([self.current_guild])
        if "current_setting" in sql:
            return _Result([self.current_guild] if self.current_guild else [])
        return _Result([])

    def _execute_select(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0]["entity"]
        crit, predicates = _filters(statement)

        if entity is None and _is_count(statement):
            model = _model_from_count(statement)
            rows = [r for r in self.store[model] if _matches(r, crit, predicates)]
            return _Result([len(rows)])

        model = _model_of(entity)
        rows = [r for r in self.store[model] if _matches(r, crit, predicates)]
        cols = _selected_columns(statement)
        if cols and not _is_whole_entity(statement):
            projected = [tuple(getattr(r, c) for c in cols) for r in rows]
            # Single-column projections are returned as scalars (.scalars().all()).
            if len(cols) == 1:
                return _Result([p[0] for p in projected])
            return _Result(projected)
        return _Result(rows)


# --- introspection helpers ------------------------------------------------
def _model_of(entity: Any) -> type:
    if hasattr(entity, "class_"):
        return entity.class_
    if hasattr(entity, "entity"):
        return entity.entity
    name = getattr(entity, "name", None)
    for model in _MODELS:
        if model.__tablename__ == name:
            return model
    return entity


def _is_count(statement: Any) -> bool:
    descs = statement.column_descriptions
    if len(descs) != 1:
        return False
    return "count" in str(descs[0].get("name", "")).lower()


def _model_from_count(statement: Any) -> type:
    froms = getattr(statement, "_from_obj", ()) or ()
    for f in froms:
        name = getattr(f, "name", None)
        for model in _MODELS:
            if model.__tablename__ == name:
                return model
    raise LookupError("cannot resolve count model")


def _selected_columns(statement: Any) -> list[str]:
    out = []
    for desc in statement.column_descriptions:
        expr = desc.get("expr")
        if expr is not None and hasattr(expr, "key") and desc.get("entity") is not expr:
            out.append(expr.key)
    return out


def _is_whole_entity(statement: Any) -> bool:
    descs = statement.column_descriptions
    if len(descs) != 1:
        return False
    return descs[0].get("expr") is descs[0].get("entity")


def _filters(statement: Any) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Return (equality crit, [(column_key, 'is_null'|'is_not_null')] predicates)."""
    crit: dict[str, Any] = {}
    predicates: list[tuple[str, str]] = []
    for clause in list(getattr(statement, "_where_criteria", ()) or ()):
        for expr in _flatten_and(clause):
            op = getattr(getattr(expr, "operator", None), "__name__", "")
            left = getattr(expr, "left", None)
            key = getattr(left, "key", None)
            if key is None:
                continue
            if op in ("is_", "is"):
                predicates.append((key, "is_null"))
            elif op in ("is_not", "isnot"):
                predicates.append((key, "is_not_null"))
            else:
                crit[key] = _literal(getattr(expr, "right", None))
    return crit, predicates


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


def _matches(row: Any, crit: dict[str, Any], predicates: list[tuple[str, str]]) -> bool:
    for key, val in crit.items():
        actual = getattr(row, key, None)
        actual = getattr(actual, "value", actual)
        val = getattr(val, "value", val)
        if str(actual) != str(val):
            return False
    for key, kind in predicates:
        actual = getattr(row, key, None)
        if kind == "is_null" and actual is not None:
            return False
        if kind == "is_not_null" and actual is None:
            return False
    return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
GUILD_NAME = "Arena Guild"


def _enabled_guild(session: FakeSession, *, central: bool = False) -> Guild:
    g = Guild(discord_guild_id=111, name=GUILD_NAME, settings={"central_feed_enabled": True})
    g.id = uuid.uuid4()
    g.is_central = central
    session.store[Guild].append(g)
    return g


def _tournament(session: FakeSession, guild: Guild, *, status: str = "open") -> Tournament:
    t = Tournament(guild_id=guild.id, name="Summer Cup", max_teams=8)
    t.id = uuid.uuid4()
    # Plain string for the fake; service stringifies via _enum_value either way.
    t.status = status  # type: ignore[assignment]
    t.is_shared_to_central = False
    session.store[Tournament].append(t)
    return t


def _register_teams(session: FakeSession, guild: Guild, tournament: Tournament, n: int) -> None:
    for _ in range(n):
        reg = Registration(
            guild_id=guild.id,
            tournament_id=tournament.id,
            team_id=uuid.uuid4(),
        )
        reg.id = uuid.uuid4()
        session.store[Registration].append(reg)


# ---------------------------------------------------------------------------
# Projection whitelist (PLAN.md §9)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_projection_whitelist_only_safe_fields() -> None:
    session = FakeSession()
    guild = _enabled_guild(session)
    tournament = _tournament(session, guild, status="open")
    _register_teams(session, guild, tournament, 3)

    projected = await federation.project_tournament(
        session, tournament=tournament, source_guild=guild
    )

    assert projected == {
        "tournament_name": "Summer Cup",
        "status": "open",
        "team_count": "3",
        "source_guild_name": GUILD_NAME,
    }
    # No sensitive keys whatsoever.
    keys = {k.lower() for k in projected}
    for bad in federation.SENSITIVE_FIELD_KEYS:
        assert not any(bad in k for k in keys)


@pytest.mark.asyncio
async def test_projection_excludes_riot_id_when_team_only() -> None:
    """Default visibility (team_only) ⇒ NO Riot id surfaced even for linked players."""
    session = FakeSession()
    guild = _enabled_guild(session)
    tournament = _tournament(session, guild)

    user_id = uuid.uuid4()
    acct = RiotAccount(
        user_id=user_id, puuid="PUUID-SECRET", riot_id_game_name="Faker", riot_id_tag_line="KR1"
    )
    acct.id = uuid.uuid4()
    session.store[RiotAccount].append(acct)
    # team_only visibility row (the default) — must NOT surface.
    vis = PlayerCentralVisibility(
        user_id=user_id, tournament_id=tournament.id, visibility="team_only"
    )
    session.store[PlayerCentralVisibility].append(vis)

    projected = await federation.project_tournament(
        session, tournament=tournament, source_guild=guild
    )

    assert "shared_riot_ids" not in projected
    assert "PUUID-SECRET" not in str(projected)
    assert "Faker" not in str(projected)


@pytest.mark.asyncio
async def test_projection_includes_riot_id_when_show_riot_id() -> None:
    """Explicit show_riot_id ⇒ the DISPLAY Riot id (never PUUID) is surfaced."""
    session = FakeSession()
    guild = _enabled_guild(session)
    tournament = _tournament(session, guild)

    user_id = uuid.uuid4()
    acct = RiotAccount(
        user_id=user_id,
        puuid="PUUID-SECRET",
        summoner_id="SUMM-SECRET",
        riot_id_game_name="Faker",
        riot_id_tag_line="KR1",
    )
    acct.id = uuid.uuid4()
    session.store[RiotAccount].append(acct)
    vis = PlayerCentralVisibility(
        user_id=user_id, tournament_id=tournament.id, visibility="show_riot_id"
    )
    session.store[PlayerCentralVisibility].append(vis)

    projected = await federation.project_tournament(
        session, tournament=tournament, source_guild=guild
    )

    assert projected["shared_riot_ids"] == "Faker#KR1"
    # The PUUID + summoner id are NEVER surfaced.
    blob = str(projected)
    assert "PUUID-SECRET" not in blob
    assert "SUMM-SECRET" not in blob


@pytest.mark.asyncio
async def test_projection_assertion_rejects_sensitive_key() -> None:
    """The defence-in-depth deny list fails loudly on a forbidden projected key."""
    with pytest.raises(federation.SensitiveProjectionError):
        federation._assert_no_sensitive_keys({"puuid": "leak"})
    with pytest.raises(federation.SensitiveProjectionError):
        federation._assert_no_sensitive_keys({"discord_user_id": "123"})


# ---------------------------------------------------------------------------
# Consent gate (per-guild central_feed_enabled)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_request_share_requires_feed_enabled() -> None:
    session = FakeSession()
    guild = _enabled_guild(session)
    guild.settings = {"central_feed_enabled": False}
    tournament = _tournament(session, guild)

    with pytest.raises(federation.FeedNotEnabledError):
        await federation.request_share(
            session, tournament=tournament, source_guild=guild, requested_by=uuid.uuid4()
        )
    # Nothing was shared or projected.
    assert tournament.is_shared_to_central is False
    assert session.store[CentralFeedItem] == []


# ---------------------------------------------------------------------------
# Lifecycle: request → approve → revoke
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_request_share_creates_pending_and_sets_flag() -> None:
    session = FakeSession()
    guild = _enabled_guild(session)
    tournament = _tournament(session, guild)
    _register_teams(session, guild, tournament, 2)

    view = await federation.request_share(
        session, tournament=tournament, source_guild=guild, requested_by=uuid.uuid4()
    )

    assert view.status == "pending"
    assert tournament.is_shared_to_central is True
    assert len(session.store[CentralFeedItem]) == 1
    item = session.store[CentralFeedItem][0]
    assert item.published_at is None and item.approved_at is None and item.revoked_at is None
    assert item.projected_fields["team_count"] == "2"


@pytest.mark.asyncio
async def test_request_share_is_idempotent() -> None:
    session = FakeSession()
    guild = _enabled_guild(session)
    tournament = _tournament(session, guild)
    _register_teams(session, guild, tournament, 1)

    requested_by = uuid.uuid4()
    await federation.request_share(
        session, tournament=tournament, source_guild=guild, requested_by=requested_by
    )
    _register_teams(session, guild, tournament, 1)  # now 2 teams
    view2 = await federation.request_share(
        session, tournament=tournament, source_guild=guild, requested_by=requested_by
    )

    # No duplicate row; the pending projection was refreshed.
    assert len(session.store[CentralFeedItem]) == 1
    assert view2.projected_fields["team_count"] == "2"


@pytest.mark.asyncio
async def test_approve_then_revoke_transitions() -> None:
    session = FakeSession()
    guild = _enabled_guild(session)
    tournament = _tournament(session, guild)
    view = await federation.request_share(
        session, tournament=tournament, source_guild=guild, requested_by=uuid.uuid4()
    )
    item_id = uuid.UUID(view.id)

    approved = await federation.approve_share(
        session, item_id=item_id, approved_by=uuid.uuid4(), is_platform_operator=True
    )
    assert approved.status == "published"
    item = session.store[CentralFeedItem][0]
    assert item.approved_at is not None and item.published_at is not None

    revoked = await federation.revoke_share(session, item_id=item_id, is_platform_operator=True)
    assert revoked.status == "revoked"
    assert item.revoked_at is not None


@pytest.mark.asyncio
async def test_non_staff_cannot_approve_or_revoke() -> None:
    session = FakeSession()
    guild = _enabled_guild(session)
    tournament = _tournament(session, guild)
    view = await federation.request_share(
        session, tournament=tournament, source_guild=guild, requested_by=uuid.uuid4()
    )
    item_id = uuid.UUID(view.id)

    with pytest.raises(federation.NotCentralStaffError):
        await federation.approve_share(
            session, item_id=item_id, approved_by=uuid.uuid4(), is_platform_operator=False
        )
    with pytest.raises(federation.NotCentralStaffError):
        await federation.revoke_share(session, item_id=item_id, is_platform_operator=False)
    # Still pending — no transition leaked through.
    assert session.store[CentralFeedItem][0].approved_at is None


@pytest.mark.asyncio
async def test_double_approve_rejected() -> None:
    session = FakeSession()
    guild = _enabled_guild(session)
    tournament = _tournament(session, guild)
    view = await federation.request_share(
        session, tournament=tournament, source_guild=guild, requested_by=uuid.uuid4()
    )
    item_id = uuid.UUID(view.id)
    await federation.approve_share(
        session, item_id=item_id, approved_by=uuid.uuid4(), is_platform_operator=True
    )
    with pytest.raises(federation.AlreadyApprovedError):
        await federation.approve_share(
            session, item_id=item_id, approved_by=uuid.uuid4(), is_platform_operator=True
        )


# ---------------------------------------------------------------------------
# Feed listing: only published, non-revoked
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_feed_lists_only_published_non_revoked() -> None:
    session = FakeSession()
    guild = _enabled_guild(session)

    # 1) pending (not approved) — hidden.
    t_pending = _tournament(session, guild)
    await federation.request_share(
        session, tournament=t_pending, source_guild=guild, requested_by=uuid.uuid4()
    )

    # 2) published — visible.
    t_pub = _tournament(session, guild)
    v_pub = await federation.request_share(
        session, tournament=t_pub, source_guild=guild, requested_by=uuid.uuid4()
    )
    await federation.approve_share(
        session, item_id=uuid.UUID(v_pub.id), approved_by=uuid.uuid4(), is_platform_operator=True
    )

    # 3) published then revoked — hidden.
    t_rev = _tournament(session, guild)
    v_rev = await federation.request_share(
        session, tournament=t_rev, source_guild=guild, requested_by=uuid.uuid4()
    )
    await federation.approve_share(
        session, item_id=uuid.UUID(v_rev.id), approved_by=uuid.uuid4(), is_platform_operator=True
    )
    await federation.revoke_share(session, item_id=uuid.UUID(v_rev.id), is_platform_operator=True)

    feed = await federation.list_central_feed(session)
    feed_ids = {item.id for item in feed}

    assert feed_ids == {v_pub.id}
    assert all(item.status == "published" for item in feed)


@pytest.mark.asyncio
async def test_revoke_is_idempotent() -> None:
    session = FakeSession()
    guild = _enabled_guild(session)
    tournament = _tournament(session, guild)
    view = await federation.request_share(
        session, tournament=tournament, source_guild=guild, requested_by=uuid.uuid4()
    )
    item_id = uuid.UUID(view.id)
    first = await federation.revoke_share(session, item_id=item_id, is_platform_operator=True)
    revoked_at = session.store[CentralFeedItem][0].revoked_at
    assert revoked_at is not None
    second = await federation.revoke_share(session, item_id=item_id, is_platform_operator=True)
    # No change to the revoked timestamp on the second call.
    assert session.store[CentralFeedItem][0].revoked_at is revoked_at
    assert first.status == "revoked" and second.status == "revoked"


def test_now_is_tz_aware() -> None:
    # Guard the timestamp helper stays tz-aware (datetime.now(UTC)).
    now = federation._now()
    assert now.tzinfo is not None
    assert isinstance(now, datetime)
    assert now.tzinfo is UTC
