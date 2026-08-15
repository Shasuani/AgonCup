"""Deleting a tournament outright (PLAN.md §3.1).

Nothing in the API could remove a tournament. The only DELETE was the per-user
``/mine`` dismissal, which is a view filter and refuses anything not already
finished — so a tournament created by mistake, or one long dead, stayed on
every admin's list permanently.

The route started DRAFT-only; it now deletes in any state (the guild's admins
own the history that cascades with it), so what these tests pin is that every
status goes, the row is returned for the audit entry, and an unknown id is a
clean not-found.
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


@pytest.mark.parametrize("status", list(TournamentStatus))
async def test_any_status_can_be_deleted(status: TournamentStatus) -> None:
    """DRAFT through CANCELLED — the admin decides, the row goes."""
    guild_id = uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, status=status)
    session.store[Tournament].append(tournament)

    deleted = await service.delete_tournament(session, tournament_id=tournament.id)

    # Returned rather than discarded, so the audit entry can name what went — an
    # id on its own is unreadable once the row no longer exists.
    assert deleted.name == "Scratch Cup"
    assert session.store[Tournament] == []


async def test_an_unknown_tournament_is_not_found() -> None:
    session = FakeSession()
    session.current_guild = str(uuid.uuid4())

    with pytest.raises(service.TournamentNotFoundError):
        await service.delete_tournament(session, tournament_id=uuid.uuid4())
