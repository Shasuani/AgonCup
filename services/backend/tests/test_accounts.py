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

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from lol_riot import AccountDTO, ContinentalRoute, NotFound, PlatformRoute, RiotApiError

from app.accounts import service
from app.models import RiotAccount, VerificationAttempt


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


class FakeSession:
    """Dict-backed stand-in for AsyncSession covering the accounts code paths."""

    def __init__(self) -> None:
        self.store: dict[type, list[Any]] = {RiotAccount: [], VerificationAttempt: []}

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None and hasattr(obj, "id"):
            obj.id = uuid.uuid4()
        self.store[type(obj)].append(obj)

    async def flush(self) -> None:
        for rows in self.store.values():
            for obj in rows:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

    async def execute(self, statement: Any, params: Any = None) -> Any:
        entity = statement.column_descriptions[0]["entity"]
        model = entity if isinstance(entity, type) else getattr(entity, "class_", entity)
        crit = _equality_filters(statement)
        rows = [r for r in self.store.get(model, []) if _matches(r, crit)]
        return _Result(rows)


def _equality_filters(statement: Any) -> dict[str, Any]:
    crit: dict[str, Any] = {}
    for clause in list(getattr(statement, "_where_criteria", ()) or ()):
        for expr in _flatten_and(clause):
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
    """Stands in for RiotAccountClient.get_account_by_riot_id (Account-V1)."""

    def __init__(self, *, dto: AccountDTO | None = None, raises: Exception | None = None) -> None:
        self._dto = dto
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> AccountDTO:
        self.calls.append((game_name, tag_line))
        if self._raises is not None:
            raise self._raises
        assert self._dto is not None
        return self._dto


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
