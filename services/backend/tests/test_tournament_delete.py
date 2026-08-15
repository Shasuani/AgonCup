"""Discarding a draft tournament (PLAN.md §3.1).

Nothing in the API could remove a tournament. The only DELETE is the per-user
``/mine`` dismissal, which is a view filter and refuses anything not already
finished — so a tournament created by mistake, or to try something out, stayed on
every admin's list permanently.

The whole safety of the new route is the DRAFT restriction, so that is what these
tests pin: a draft goes, and everything past it is refused rather than erased.
"""

from __future__ import annotations

import uuid

import pytest
from lol_schemas.enums import GameMode, TournamentStatus

from app.models import Tournament
from app.tournaments import service
from tests._fake_session import FakeSession


def _tournament(*, guild_id: uuid.UUID, status: TournamentStatus) -> Tournament:
    return Tournament(
        id=uuid.uuid4(),
        guild_id=guild_id,
        name="Scratch Cup",
        game_mode=GameMode.ARAM_1V1,
        status=status,
        max_teams=16,
        join_code="ABCD2345",
    )


async def test_a_draft_can_be_thrown_away() -> None:
    guild_id = uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, status=TournamentStatus.DRAFT)
    session.store[Tournament].append(tournament)

    deleted = await service.delete_tournament(session, tournament_id=tournament.id)

    # Returned rather than discarded, so the audit entry can name what went — an
    # id on its own is unreadable once the row no longer exists.
    assert deleted.name == "Scratch Cup"
    assert session.store[Tournament] == []


@pytest.mark.parametrize(
    "status",
    [
        TournamentStatus.OPEN,
        TournamentStatus.CHECK_IN,
        TournamentStatus.RUNNING,
        TournamentStatus.COMPLETED,
        TournamentStatus.CANCELLED,
    ],
)
async def test_anything_past_draft_is_refused(status: TournamentStatus) -> None:
    """Past DRAFT a tournament holds other people's entries and results.

    Deleting cascades to registrations, participants and the bracket, so allowing
    it here would erase somebody else's history on an admin's misclick. A live one
    that should stop gets cancelled, which keeps the record.
    """
    guild_id = uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, status=status)
    session.store[Tournament].append(tournament)

    with pytest.raises(service.InvalidStateError):
        await service.delete_tournament(session, tournament_id=tournament.id)

    assert session.store[Tournament] == [tournament]


async def test_an_unknown_tournament_is_not_found() -> None:
    session = FakeSession()
    session.current_guild = str(uuid.uuid4())

    with pytest.raises(service.TournamentNotFoundError):
        await service.delete_tournament(session, tournament_id=uuid.uuid4())
