"""Per-user tournament dismissal — "stop showing me this one" (PLAN.md §3.1).

``/tournaments/mine`` only ever grew: every tournament a player ever entered stayed
on their client screen forever. ``hide_tournament`` is the per-user clear — it
writes a ``TournamentHide`` row that ``list_my_tournaments`` filters on, and it is
NOT a delete (the guild's record is untouched).

Offline over the shared fake session. What is provable here is the **guard** — the
thing that would actually hurt if it were wrong: hiding a live tournament would
take away the caller's own route back to lock-in and the bracket. The filtering
half of the feature is a subquery the fake store cannot model, so it is proved
against a real Postgres in ``test_integration_pg.py``.
"""

from __future__ import annotations

import uuid

import pytest
from lol_schemas.enums import GameMode, TournamentStatus

from app.models import Tournament, TournamentHide
from app.tournaments import service
from tests._fake_session import FakeSession

_GUILD = uuid.uuid4()
_USER = uuid.uuid4()


def _session() -> FakeSession:
    s = FakeSession()
    s.current_guild = str(_GUILD)
    return s


def _tournament(status: TournamentStatus) -> Tournament:
    return Tournament(
        id=uuid.uuid4(),
        guild_id=_GUILD,
        name="Cup",
        status=status,
        game_mode=GameMode.SUMMONERS_RIFT_5V5,
        max_teams=16,
    )


@pytest.mark.parametrize("status", [TournamentStatus.COMPLETED, TournamentStatus.CANCELLED])
async def test_hides_a_finished_tournament(status: TournamentStatus) -> None:
    s = _session()
    t = _tournament(status)
    s.store[Tournament].append(t)

    await service.hide_tournament(s, tournament_id=t.id, user_id=_USER)

    (hide,) = s.store[TournamentHide]
    assert hide.tournament_id == t.id
    assert hide.user_id == _USER
    # The acting guild comes from the transaction GUC, never from the caller.
    assert hide.guild_id == _GUILD
    # A dismissal is a view, not a deletion: the tournament itself is untouched.
    assert s.store[Tournament] == [t]
    assert t.status == status


@pytest.mark.parametrize(
    "status",
    [
        TournamentStatus.DRAFT,
        TournamentStatus.OPEN,
        TournamentStatus.CHECK_IN,
        TournamentStatus.RUNNING,
    ],
)
async def test_refuses_to_hide_a_live_tournament(status: TournamentStatus) -> None:
    """The load-bearing guard.

    A tournament you still have to play is reachable only through this list — the
    Lock in button and the bracket live inside it. Letting a stray click remove it
    would strand the player with no way back.
    """
    s = _session()
    t = _tournament(status)
    s.store[Tournament].append(t)

    with pytest.raises(service.TournamentNotFinishedError):
        await service.hide_tournament(s, tournament_id=t.id, user_id=_USER)

    assert s.store[TournamentHide] == []


async def test_hiding_twice_is_idempotent() -> None:
    """A double-click must not trip the ``(tournament_id, user_id)`` unique index."""
    s = _session()
    t = _tournament(TournamentStatus.COMPLETED)
    s.store[Tournament].append(t)

    await service.hide_tournament(s, tournament_id=t.id, user_id=_USER)
    await service.hide_tournament(s, tournament_id=t.id, user_id=_USER)

    assert len(s.store[TournamentHide]) == 1


async def test_unknown_tournament_is_not_found() -> None:
    """RLS makes another guild's tournament indistinguishable from a missing one."""
    s = _session()

    with pytest.raises(service.TournamentNotFoundError):
        await service.hide_tournament(s, tournament_id=uuid.uuid4(), user_id=_USER)

    assert s.store[TournamentHide] == []


async def test_hide_is_per_user_not_global() -> None:
    """Two entrants dismissing the same finished tournament get a row each.

    If the row were keyed on the tournament alone, one player clearing their list
    would erase it from everyone else's.
    """
    s = _session()
    t = _tournament(TournamentStatus.COMPLETED)
    s.store[Tournament].append(t)
    other = uuid.uuid4()

    await service.hide_tournament(s, tournament_id=t.id, user_id=_USER)
    await service.hide_tournament(s, tournament_id=t.id, user_id=other)

    assert {h.user_id for h in s.store[TournamentHide]} == {_USER, other}


def test_route_is_wired_as_a_delete_on_the_callers_own_list() -> None:
    """The client calls ``DELETE /api/v1/tournaments/{id}/mine``.

    Wiring, not logic — but a path typo here is invisible until the button fails
    in the packaged desktop app.
    """
    from app.main import create_app

    paths = create_app().openapi()["paths"]
    assert "delete" in paths["/api/v1/tournaments/{tournament_id}/mine"]
