"""Account-V1 linking + verification-flow tests (PLAN.md §3.1 / §3.5).

Offline by design — no network, no real database:

* ``link_account`` resolves the Riot ID via a MOCKED Account-V1 client, upserts
  the global ``RiotAccount`` (``verified=False``), and opens a
  ``VerificationAttempt`` with a random target icon + single-use nonce + TTL.
* Re-linking refreshes display data, re-resolves the canonical PUUID, and
  re-opens the challenge (``verified`` is NOT permanent).
* A 404 from Account-V1 maps to ``UnknownRiotId``; other Riot errors map to
  ``RiotUnavailable``.
* Region parsing + the platform→continent routing map (PLAN.md §3.5).
* ``get_attempt`` is scoped to the owning user (status reporting).

The DB is a small in-memory fake session covering only the narrow
``AsyncSession`` surface the service touches (mirrors ``tests/test_auth.py``).
"""

from __future__ import annotations

import operator
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from lol_riot import (
    AccountDTO,
    ContinentalRoute,
    LeagueEntryDTO,
    NotFound,
    PlatformRoute,
    RiotApiError,
)

from app.accounts import service
from app.models import RiotAccount, User, VerificationAttempt


# ---------------------------------------------------------------------------
# In-memory fakes
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

    def scalars(self) -> _Scalars:
        return _Scalars(self.rows)


@dataclass
class _Scalars:
    rows: list[Any]

    def all(self) -> list[Any]:
        return self.rows


class _FakeNested:
    """Async context manager standing in for ``session.begin_nested()``.

    The real savepoint isolates a failed INSERT; the fake just lets the body run
    (the happy path never raises an IntegrityError under the fake store).
    """

    async def __aenter__(self) -> _FakeNested:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeSession:
    """Dict-backed stand-in for AsyncSession covering the accounts code paths."""

    def __init__(self) -> None:
        from app.models import Guild

        self.store: dict[type, list[Any]] = {
            RiotAccount: [],
            VerificationAttempt: [],
            User: [],
            Guild: [],
        }
        self.rolled_back = False

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None and hasattr(obj, "id"):
            obj.id = uuid.uuid4()
        self.store.setdefault(type(obj), []).append(obj)

    async def flush(self) -> None:
        for rows in self.store.values():
            for obj in rows:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

    async def rollback(self) -> None:
        self.rolled_back = True

    def begin_nested(self) -> _FakeNested:
        """SAVEPOINT stand-in: the code-mint loop wraps each INSERT in one."""
        return _FakeNested()

    def begin(self) -> _FakeNested:
        """Transaction stand-in: ``verify_desktop_code`` opens short txns itself.

        The fake store is in-memory and never partially rolls back, so a plain
        pass-through async CM (same shape as ``begin_nested``) is sufficient.
        """
        return _FakeNested()

    async def execute(self, statement: Any, params: Any = None) -> Any:
        desc = statement.column_descriptions[0]
        entity = desc["entity"]
        model = entity if isinstance(entity, type) else getattr(entity, "class_", entity)
        crit = _equality_filters(statement)
        rows = [r for r in self.store.get(model, []) if _matches(r, crit)]
        # Single-column selects (e.g. ``select(User.discord_user_id)``) project the
        # attribute, mirroring SQLAlchemy's scalar result; entity selects return rows.
        expr = desc.get("expr")
        col_key = getattr(expr, "key", None)
        if expr is not entity and col_key is not None and not isinstance(expr, type):
            rows = [getattr(r, col_key, None) for r in rows]
        return _Result(rows)


def _equality_filters(statement: Any) -> dict[str, Any]:
    crit: dict[str, Any] = {}
    for clause in list(getattr(statement, "_where_criteria", ()) or ()):
        for expr in _flatten_and(clause):
            # Only equality clauses constrain the fake store; inequalities (e.g.
            # ``expires_at > now``) are ignored here and asserted in the test.
            if getattr(expr, "operator", None) is not operator.eq:
                continue
            left = getattr(expr, "left", None)
            right = getattr(expr, "right", None)
            if left is not None and hasattr(left, "key"):
                crit[left.key] = getattr(right, "value", right)
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


def _matches(row: Any, crit: dict[str, Any]) -> bool:
    return all(str(getattr(row, k, None)) == str(v) for k, v in crit.items())


class FakeRiotClient:
    """Stands in for RiotAccountClient (Account-V1 resolve + League-V4 rank).

    ``dto`` is returned by ``get_account_by_riot_id``; ``raises`` (if set) is
    raised from it. ``entries`` is returned by ``get_league_entries_by_puuid``;
    ``rank_raises`` (if set) is raised from it to exercise the best-effort path.
    """

    def __init__(
        self,
        *,
        dto: AccountDTO | None = None,
        raises: Exception | None = None,
        entries: list[LeagueEntryDTO] | None = None,
        rank_raises: Exception | None = None,
    ) -> None:
        self._dto = dto
        self._raises = raises
        self._entries = entries if entries is not None else []
        self._rank_raises = rank_raises
        self.calls: list[tuple[str, str]] = []
        self.rank_calls: list[tuple[str, PlatformRoute]] = []

    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> AccountDTO:
        self.calls.append((game_name, tag_line))
        if self._raises is not None:
            raise self._raises
        assert self._dto is not None
        return self._dto

    async def get_league_entries_by_puuid(
        self, puuid: str, platform: PlatformRoute
    ) -> list[LeagueEntryDTO]:
        self.rank_calls.append((puuid, platform))
        if self._rank_raises is not None:
            raise self._rank_raises
        return self._entries


# ---------------------------------------------------------------------------
# Region routing (PLAN.md §3.5)
# ---------------------------------------------------------------------------
def test_parse_platform_region_valid() -> None:
    assert service.parse_platform_region("NA1") is PlatformRoute.NA1
    assert service.parse_platform_region("euw1") is PlatformRoute.EUW1


def test_parse_platform_region_invalid() -> None:
    with pytest.raises(service.InvalidRegion):
        service.parse_platform_region("middle-earth")


def test_continent_for_maps_platform_to_continent() -> None:
    assert service.continent_for(PlatformRoute.NA1) is ContinentalRoute.AMERICAS
    assert service.continent_for(PlatformRoute.EUW1) is ContinentalRoute.EUROPE
    assert service.continent_for(PlatformRoute.KR) is ContinentalRoute.ASIA


# ---------------------------------------------------------------------------
# link_account
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_link_creates_account_and_attempt() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    client = FakeRiotClient(dto=AccountDTO(puuid="PUUID-1", game_name="Faker", tag_line="KR1"))

    result = await service.link_account(
        session,
        client,  # type: ignore[arg-type]
        user_id=user_id,
        game_name="Faker",
        tag_line="KR1",
        region="kr",
    )

    # One account + one attempt were created.
    account = session.store[RiotAccount][0]
    attempt = session.store[VerificationAttempt][0]

    assert account.puuid == "PUUID-1"
    assert account.user_id == user_id
    assert account.verified is False
    assert account.region == "kr"
    assert account.riot_id_game_name == "Faker"

    # The attempt carries a pending status, a target icon, a nonce, and a TTL.
    assert attempt.puuid == "PUUID-1"
    assert attempt.status == service.STATUS_PENDING
    assert attempt.nonce  # non-empty single-use nonce
    assert attempt.expires_at > datetime.now(tz=UTC)

    # The result echoes the challenge the caller must surface to the user.
    assert result.puuid == "PUUID-1"
    assert result.verified is False
    assert result.attempt_id == str(attempt.id)
    assert result.target_icon_id == attempt.target_icon_id
    assert result.account_id == str(account.id)


@pytest.mark.asyncio
async def test_relink_refreshes_display_and_reopens_challenge() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    # Pre-existing, already-VERIFIED account for the same canonical PUUID.
    existing = RiotAccount(
        user_id=user_id,
        puuid="PUUID-1",
        riot_id_game_name="OldName",
        riot_id_tag_line="OLD",
        region="na1",
        verified=True,
        verified_at=datetime.now(tz=UTC),
    )
    existing.id = uuid.uuid4()
    session.store[RiotAccount].append(existing)

    client = FakeRiotClient(dto=AccountDTO(puuid="PUUID-1", game_name="NewName", tag_line="NEW"))
    result = await service.link_account(
        session,
        client,  # type: ignore[arg-type]
        user_id=user_id,
        game_name="NewName",
        tag_line="NEW",
        region="euw1",
    )

    # No second account row — the canonical PUUID is reused, display refreshed.
    assert len(session.store[RiotAccount]) == 1
    assert existing.riot_id_game_name == "NewName"
    assert existing.region == "euw1"
    # verified is NOT permanent — re-linking re-opens the challenge.
    assert existing.verified is False
    assert existing.verified_at is None
    assert result.verified is False
    assert len(session.store[VerificationAttempt]) == 1


@pytest.mark.asyncio
async def test_link_unknown_riot_id_maps_to_unknown_error() -> None:
    session = FakeSession()
    client = FakeRiotClient(raises=NotFound("no such riot id"))
    with pytest.raises(service.UnknownRiotId):
        await service.link_account(
            session,
            client,  # type: ignore[arg-type]
            user_id=str(uuid.uuid4()),
            game_name="Nobody",
            tag_line="ZZZ",
            region="na1",
        )
    # Nothing was persisted on a failed resolve.
    assert session.store[RiotAccount] == []
    assert session.store[VerificationAttempt] == []


@pytest.mark.asyncio
async def test_link_riot_error_maps_to_unavailable() -> None:
    session = FakeSession()
    client = FakeRiotClient(raises=RiotApiError("boom", status_code=500))
    with pytest.raises(service.RiotUnavailable):
        await service.link_account(
            session,
            client,  # type: ignore[arg-type]
            user_id=str(uuid.uuid4()),
            game_name="g",
            tag_line="t",
            region="na1",
        )


@pytest.mark.asyncio
async def test_link_invalid_region_rejected_before_riot_call() -> None:
    session = FakeSession()
    client = FakeRiotClient(dto=AccountDTO(puuid="P", game_name="g", tag_line="t"))
    with pytest.raises(service.InvalidRegion):
        await service.link_account(
            session,
            client,  # type: ignore[arg-type]
            user_id=str(uuid.uuid4()),
            game_name="g",
            tag_line="t",
            region="nope",
        )
    # Region is validated first — no Riot call, no state.
    assert client.calls == []
    assert session.store[RiotAccount] == []


# ---------------------------------------------------------------------------
# get_attempt (status reporting; owner-scoped)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_attempt_returns_owned_attempt() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    attempt = VerificationAttempt(
        user_id=user_id,
        puuid="P",
        nonce="n",
        target_icon_id=7,
        status=service.STATUS_PENDING,
        expires_at=datetime.now(tz=UTC),
    )
    attempt.id = uuid.uuid4()
    session.store[VerificationAttempt].append(attempt)

    found = await service.get_attempt(session, attempt_id=str(attempt.id), user_id=user_id)
    assert found is attempt

    # A different user cannot see it (ownership scoped in the query).
    other = await service.get_attempt(
        session, attempt_id=str(attempt.id), user_id=str(uuid.uuid4())
    )
    assert other is None


# ---------------------------------------------------------------------------
# Desktop code flow — _new_code / normalize_code shape
# ---------------------------------------------------------------------------
def test_new_code_shape_and_alphabet() -> None:
    code = service._new_code()
    assert len(code) == service._CODE_LEN
    assert set(code) <= set(service._CODE_ALPHABET)
    # Ambiguous glyphs are excluded from the alphabet.
    assert not (set("ILO01") & set(service._CODE_ALPHABET))


def test_normalize_code_strips_hyphen_and_uppercases() -> None:
    assert service.normalize_code(" 7k3m-qp9r ") == "7K3MQP9R"
    assert service.normalize_code("ABCD2345") == "ABCD2345"


# ---------------------------------------------------------------------------
# start_desktop_registration
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_start_desktop_registration_mints_pending_code_row() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    guild_id = str(uuid.uuid4())

    result = await service.start_desktop_registration(
        session, user_id=user_id, origin_guild_id=guild_id
    )

    attempt = session.store[VerificationAttempt][0]
    assert attempt.verify_kind == service.VERIFY_KIND_DESKTOP_CODE
    assert attempt.status == service.STATUS_PENDING
    assert attempt.puuid is None
    assert attempt.nonce is None
    assert attempt.target_icon_id is None
    assert str(attempt.origin_guild_id) == guild_id
    # Stored de-hyphenated; the result carries the grouped display form.
    assert "-" not in attempt.code
    assert result.code == service._group_code(attempt.code)
    assert result.origin_guild_id == guild_id
    assert result.expires_at > datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_start_desktop_registration_supersedes_prior_pending() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    guild_uuid = uuid.uuid4()
    prior = VerificationAttempt(
        user_id=user_id,
        code="OLDCODE2",
        origin_guild_id=guild_uuid,
        verify_kind=service.VERIFY_KIND_DESKTOP_CODE,
        status=service.STATUS_PENDING,
        expires_at=datetime.now(tz=UTC),
    )
    prior.id = uuid.uuid4()
    session.store[VerificationAttempt].append(prior)

    await service.start_desktop_registration(
        session, user_id=user_id, origin_guild_id=str(guild_uuid)
    )

    # The prior live code is superseded; only the new one is pending.
    assert prior.status == service.STATUS_EXPIRED
    pending = [a for a in session.store[VerificationAttempt] if a.status == service.STATUS_PENDING]
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_start_desktop_registration_allows_null_guild() -> None:
    session = FakeSession()
    result = await service.start_desktop_registration(
        session, user_id=str(uuid.uuid4()), origin_guild_id=None
    )
    assert result.origin_guild_id is None
    assert session.store[VerificationAttempt][0].origin_guild_id is None


# ---------------------------------------------------------------------------
# verify_desktop_code
# ---------------------------------------------------------------------------
def _pending_code_attempt(user_id: str, *, code: str = "ABCD2345") -> VerificationAttempt:
    attempt = VerificationAttempt(
        user_id=user_id,
        code=code,
        verify_kind=service.VERIFY_KIND_DESKTOP_CODE,
        status=service.STATUS_PENDING,
        expires_at=datetime.now(tz=UTC) + service.VERIFICATION_TTL,
    )
    attempt.id = uuid.uuid4()
    return attempt


#: The canonical 78-char PUUID Account-V1 resolves to (distinct from the LCU UUID).
_CANON = "jdEe2S7wOyumCANONICALpuuid"
_GOLD = LeagueEntryDTO(
    queue_type="RANKED_SOLO_5x5",
    tier="GOLD",
    rank="II",
    league_points=45,
    wins=20,
    losses=10,
)


def _seed_user(session: FakeSession, user_id: str, *, discord_user_id: int = 1234567890) -> None:
    """Insert a global User row so the verify path can resolve discord_user_id."""
    user = User(id=uuid.UUID(user_id), discord_user_id=discord_user_id, display_name="Chama")
    session.store[User].append(user)


@pytest.mark.asyncio
async def test_verify_desktop_code_resolves_canonical_and_verifies() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_user(session, user_id, discord_user_id=999)
    guild_uuid = uuid.uuid4()
    attempt = _pending_code_attempt(user_id)
    attempt.origin_guild_id = guild_uuid
    session.store[VerificationAttempt].append(attempt)

    client = FakeRiotClient(
        dto=AccountDTO(puuid=_CANON, game_name="Chama", tag_line="Shasu"),
        entries=[_GOLD],
    )

    result = await service.verify_desktop_code(
        session,
        code="abcd-2345",  # hyphenated/lowercase — normalized server-side
        game_name="Chama",
        tag_line="Shasu",
        region="eun1",
        account_client=client,  # type: ignore[arg-type]
        lcu_puuid="ec15f3f7-785e-57e8-8931-590e59d62f9e",
        summoner_id="55997829",
    )

    account = session.store[RiotAccount][0]
    # The CANONICAL puuid is stored, NOT the LCU UUID (the bug fix).
    assert account.puuid == _CANON
    assert account.lcu_puuid == "ec15f3f7-785e-57e8-8931-590e59d62f9e"
    assert str(account.user_id) == user_id
    assert account.verified is True
    assert account.verified_at is not None
    assert account.region == "eun1"
    assert account.summoner_id == "55997829"
    assert account.riot_id_game_name == "Chama"
    assert account.riot_id_tag_line == "Shasu"
    assert account.origin_guild_id == guild_uuid
    # Rank snapshot persisted.
    assert account.rank_tier == "GOLD"
    assert account.rank_division == "II"
    assert account.rank_lp == 45
    assert account.rank_queue == "RANKED_SOLO_5x5"
    assert account.rank_checked_at is not None

    assert attempt.status == service.STATUS_VERIFIED
    assert attempt.puuid == _CANON

    assert result.verified is True
    assert result.puuid == _CANON
    assert result.account_id == str(account.id)
    assert result.origin_guild_id == str(guild_uuid)
    assert result.game_name == "Chama"
    assert result.tag_line == "Shasu"
    assert result.region == "eun1"
    assert result.rank_text == "Gold II, 45 LP"
    assert result.discord_user_id == "999"


@pytest.mark.asyncio
async def test_verify_desktop_code_unranked_when_no_solo_entry() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_user(session, user_id)
    attempt = _pending_code_attempt(user_id)
    session.store[VerificationAttempt].append(attempt)

    client = FakeRiotClient(
        dto=AccountDTO(puuid=_CANON, game_name="Chama", tag_line="Shasu"),
        entries=[],  # fully unranked
    )

    result = await service.verify_desktop_code(
        session,
        code=attempt.code,
        game_name="Chama",
        tag_line="Shasu",
        region="eun1",
        account_client=client,  # type: ignore[arg-type]
    )
    assert result.rank_text == "Unranked"
    assert result.tier is None
    account = session.store[RiotAccount][0]
    assert account.rank_tier is None
    assert account.rank_checked_at is None


@pytest.mark.asyncio
async def test_verify_desktop_code_rank_fetch_failure_does_not_fail_link() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_user(session, user_id)
    attempt = _pending_code_attempt(user_id)
    session.store[VerificationAttempt].append(attempt)

    client = FakeRiotClient(
        dto=AccountDTO(puuid=_CANON, game_name="Chama", tag_line="Shasu"),
        rank_raises=RiotApiError("league-v4 boom", status_code=500),
    )

    result = await service.verify_desktop_code(
        session,
        code=attempt.code,
        game_name="Chama",
        tag_line="Shasu",
        region="eun1",
        account_client=client,  # type: ignore[arg-type]
    )
    # The link still succeeds; rank is unknown.
    assert result.verified is True
    assert result.rank_text == "Unranked"
    assert session.store[RiotAccount][0].verified is True


@pytest.mark.asyncio
async def test_verify_desktop_code_unknown_riot_id_rejects() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_user(session, user_id)
    attempt = _pending_code_attempt(user_id)
    session.store[VerificationAttempt].append(attempt)

    client = FakeRiotClient(raises=NotFound("no such riot id"))
    with pytest.raises(service.UnknownRiotId):
        await service.verify_desktop_code(
            session,
            code=attempt.code,
            game_name="Nobody",
            tag_line="ZZZ",
            region="eun1",
            account_client=client,  # type: ignore[arg-type]
        )
    # No account created on a rejected resolve.
    assert session.store[RiotAccount] == []


@pytest.mark.asyncio
async def test_verify_desktop_code_riot_unavailable_maps_to_unavailable() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    attempt = _pending_code_attempt(user_id)
    session.store[VerificationAttempt].append(attempt)

    client = FakeRiotClient(raises=RiotApiError("boom", status_code=500))
    with pytest.raises(service.RiotUnavailable):
        await service.verify_desktop_code(
            session,
            code=attempt.code,
            game_name="g",
            tag_line="t",
            region="eun1",
            account_client=client,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_verify_desktop_code_unknown_code_raises() -> None:
    session = FakeSession()
    client = FakeRiotClient(dto=AccountDTO(puuid=_CANON, game_name="g", tag_line="t"))
    with pytest.raises(service.UnknownOrExpiredCode):
        await service.verify_desktop_code(
            session,
            code="ZZZZ2345",
            game_name="g",
            tag_line="t",
            region="na1",
            account_client=client,  # type: ignore[arg-type]
        )
    # The code lookup fails BEFORE any Riot call.
    assert client.calls == []


@pytest.mark.asyncio
async def test_verify_desktop_code_consumed_code_raises() -> None:
    # A non-pending (already verified/expired) row is filtered by the status
    # predicate, so it is not found and the generic error is raised. (TTL expiry
    # is enforced by the SQL ``expires_at > now`` predicate against a real DB; the
    # fake session cannot model time-based filtering — see the live-DB tests.)
    session = FakeSession()
    user_id = str(uuid.uuid4())
    attempt = _pending_code_attempt(user_id)
    attempt.status = service.STATUS_VERIFIED
    session.store[VerificationAttempt].append(attempt)

    client = FakeRiotClient(dto=AccountDTO(puuid=_CANON, game_name="g", tag_line="t"))
    with pytest.raises(service.UnknownOrExpiredCode):
        await service.verify_desktop_code(
            session,
            code=attempt.code,
            game_name="g",
            tag_line="t",
            region="na1",
            account_client=client,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_verify_desktop_code_puuid_owned_by_other_user_conflicts() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_user(session, user_id)
    other_user = uuid.uuid4()
    existing = RiotAccount(user_id=other_user, puuid=_CANON, verified=True)
    existing.id = uuid.uuid4()
    session.store[RiotAccount].append(existing)

    attempt = _pending_code_attempt(user_id)
    session.store[VerificationAttempt].append(attempt)

    client = FakeRiotClient(dto=AccountDTO(puuid=_CANON, game_name="Chama", tag_line="Shasu"))
    with pytest.raises(service.PuuidConflict):
        await service.verify_desktop_code(
            session,
            code=attempt.code,
            game_name="Chama",
            tag_line="Shasu",
            region="na1",
            account_client=client,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_verify_desktop_code_dedupes_existing_icon_row_same_user() -> None:
    # The old icon-flow row carries the SAME canonical puuid (unverified). The
    # desktop verify must UPDATE it in place, never create a duplicate (the bug).
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_user(session, user_id)
    existing = RiotAccount(
        user_id=uuid.UUID(user_id),
        puuid=_CANON,
        riot_id_game_name="OldName",
        verified=False,
    )
    existing.id = uuid.uuid4()
    session.store[RiotAccount].append(existing)

    attempt = _pending_code_attempt(user_id)
    session.store[VerificationAttempt].append(attempt)

    client = FakeRiotClient(
        dto=AccountDTO(puuid=_CANON, game_name="Chama", tag_line="Shasu"),
        entries=[_GOLD],
    )
    result = await service.verify_desktop_code(
        session,
        code=attempt.code,
        game_name="Chama",
        tag_line="Shasu",
        region="eun1",
        account_client=client,  # type: ignore[arg-type]
    )
    # No second account row — the existing canonical row is updated in place.
    assert len(session.store[RiotAccount]) == 1
    assert existing.verified is True
    assert existing.riot_id_game_name == "Chama"
    assert result.account_id == str(existing.id)


@pytest.mark.asyncio
async def test_verify_desktop_code_invalid_region_raises() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    attempt = _pending_code_attempt(user_id)
    session.store[VerificationAttempt].append(attempt)

    client = FakeRiotClient(dto=AccountDTO(puuid=_CANON, game_name="g", tag_line="t"))
    with pytest.raises(service.InvalidRegion):
        await service.verify_desktop_code(
            session,
            code=attempt.code,
            game_name="g",
            tag_line="t",
            region="middle-earth",
            account_client=client,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# verify_lcu_account (JWT-authed, no code) — PLAN.md §3.1
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_verify_lcu_resolves_canonical_and_links_to_principal() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_user(session, user_id, discord_user_id=777)
    guild_id = str(uuid.uuid4())

    client = FakeRiotClient(
        dto=AccountDTO(puuid=_CANON, game_name="Chama", tag_line="Shasu"),
        entries=[_GOLD],
    )

    result = await service.verify_lcu_account(
        session,
        user_id=user_id,
        game_name="Chama",
        tag_line="Shasu",
        region="eun1",
        account_client=client,  # type: ignore[arg-type]
        lcu_puuid="ec15f3f7-785e-57e8-8931-590e59d62f9e",
        summoner_id="55997829",
        origin_guild_id=guild_id,
    )

    account = session.store[RiotAccount][0]
    # The USER comes from the principal (no code/VerificationAttempt involved).
    assert str(account.user_id) == user_id
    assert account.puuid == _CANON  # canonical, not the LCU UUID
    assert account.lcu_puuid == "ec15f3f7-785e-57e8-8931-590e59d62f9e"
    assert account.verified is True
    assert account.region == "eun1"
    assert account.summoner_id == "55997829"
    assert account.rank_tier == "GOLD"
    # No VerificationAttempt is touched in this flow.
    assert session.store[VerificationAttempt] == []

    assert result.verified is True
    assert result.puuid == _CANON
    assert result.origin_guild_id == guild_id
    assert result.rank_text == "Gold II, 45 LP"
    assert result.discord_user_id == "777"


@pytest.mark.asyncio
async def test_verify_lcu_dedupes_existing_row_same_user() -> None:
    # An existing row carrying the SAME canonical puuid is UPDATED in place.
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_user(session, user_id)
    existing = RiotAccount(
        user_id=uuid.UUID(user_id),
        puuid=_CANON,
        riot_id_game_name="OldName",
        verified=False,
    )
    existing.id = uuid.uuid4()
    session.store[RiotAccount].append(existing)

    client = FakeRiotClient(dto=AccountDTO(puuid=_CANON, game_name="Chama", tag_line="Shasu"))
    result = await service.verify_lcu_account(
        session,
        user_id=user_id,
        game_name="Chama",
        tag_line="Shasu",
        region="eun1",
        account_client=client,  # type: ignore[arg-type]
    )
    assert len(session.store[RiotAccount]) == 1  # no duplicate row
    assert existing.verified is True
    assert existing.riot_id_game_name == "Chama"
    assert result.account_id == str(existing.id)


@pytest.mark.asyncio
async def test_verify_lcu_puuid_owned_by_other_user_conflicts() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    _seed_user(session, user_id)
    other = RiotAccount(user_id=uuid.uuid4(), puuid=_CANON, verified=True)
    other.id = uuid.uuid4()
    session.store[RiotAccount].append(other)

    client = FakeRiotClient(dto=AccountDTO(puuid=_CANON, game_name="Chama", tag_line="Shasu"))
    with pytest.raises(service.PuuidConflict):
        await service.verify_lcu_account(
            session,
            user_id=user_id,
            game_name="Chama",
            tag_line="Shasu",
            region="eun1",
            account_client=client,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_verify_lcu_unknown_riot_id_rejects() -> None:
    session = FakeSession()
    user_id = str(uuid.uuid4())
    client = FakeRiotClient(raises=NotFound("no such riot id"))
    with pytest.raises(service.UnknownRiotId):
        await service.verify_lcu_account(
            session,
            user_id=user_id,
            game_name="Nobody",
            tag_line="ZZZ",
            region="eun1",
            account_client=client,  # type: ignore[arg-type]
        )
    assert session.store[RiotAccount] == []


@pytest.mark.asyncio
async def test_verify_lcu_riot_unavailable_maps_to_unavailable() -> None:
    session = FakeSession()
    client = FakeRiotClient(raises=RiotApiError("boom", status_code=500))
    with pytest.raises(service.RiotUnavailable):
        await service.verify_lcu_account(
            session,
            user_id=str(uuid.uuid4()),
            game_name="g",
            tag_line="t",
            region="eun1",
            account_client=client,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_verify_lcu_invalid_region_rejected_before_riot_call() -> None:
    session = FakeSession()
    client = FakeRiotClient(dto=AccountDTO(puuid=_CANON, game_name="g", tag_line="t"))
    with pytest.raises(service.InvalidRegion):
        await service.verify_lcu_account(
            session,
            user_id=str(uuid.uuid4()),
            game_name="g",
            tag_line="t",
            region="middle-earth",
            account_client=client,  # type: ignore[arg-type]
        )
    assert client.calls == []


def test_format_rank_fields_renders_snapshot() -> None:
    assert (
        service.format_rank_fields(tier="GOLD", division="II", league_points=45) == "Gold II, 45 LP"
    )
    # Apex tiers carry no division.
    assert service.format_rank_fields(tier="MASTER", division=None, league_points=120) == (
        "Master, 120 LP"
    )
    assert service.format_rank_fields(tier=None, division=None, league_points=None) == "Unranked"


# ---------------------------------------------------------------------------
# Admin roster (list_linked_accounts) — PLAN.md §3.4
# ---------------------------------------------------------------------------
class _RosterSession:
    """Minimal AsyncSession stand-in returning the join rows.

    ``list_linked_accounts`` issues a single
    ``select(RiotAccount, User.display_name, Guild.name)`` join; the shared
    ``FakeSession`` only models single-entity selects, so this fake just hands back
    the pre-built ``(account, display_name, origin_guild_name)`` tuples.
    """

    def __init__(self, rows: list[tuple[RiotAccount, str, str | None]]) -> None:
        self._rows = rows

    async def execute(self, statement: Any, params: Any = None) -> Any:
        return _Result(list(self._rows))


def _roster_account(
    *, game: str, tag: str, region: str, verified: bool, origin_guild_id: uuid.UUID | None = None
) -> RiotAccount:
    return RiotAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        puuid=f"puuid-{game}",
        riot_id_game_name=game,
        riot_id_tag_line=tag,
        region=region,
        verified=verified,
        origin_guild_id=origin_guild_id,
    )


@pytest.mark.asyncio
async def test_list_linked_accounts_maps_join_rows() -> None:
    gid = uuid.uuid4()
    a1 = _roster_account(game="Faker", tag="KR1", region="kr", verified=True, origin_guild_id=gid)
    a2 = _roster_account(game="Caps", tag="EUW", region="euw1", verified=False)
    session = _RosterSession([(a1, "ShadowFox", "Faker Fan Club"), (a2, "BlueWizard", None)])

    rows = await service.list_linked_accounts(session)  # type: ignore[arg-type]

    assert [r.discord_name for r in rows] == ["ShadowFox", "BlueWizard"]
    assert rows[0].account_id == str(a1.id)
    assert rows[0].user_id == str(a1.user_id)
    assert (rows[0].game_name, rows[0].tag_line, rows[0].region) == ("Faker", "KR1", "kr")
    assert rows[0].verified is True
    assert rows[0].origin_guild_id == str(gid)
    assert rows[0].origin_guild_name == "Faker Fan Club"
    assert rows[1].verified is False
    assert rows[1].origin_guild_id is None
    assert rows[1].origin_guild_name is None


@pytest.mark.asyncio
async def test_list_linked_accounts_empty() -> None:
    assert await service.list_linked_accounts(_RosterSession([])) == []  # type: ignore[arg-type]
