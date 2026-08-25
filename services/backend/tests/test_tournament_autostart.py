"""Unscheduled tournaments start themselves when the last entrant locks in.

PLAN.md §3.2 covers the *scheduled* path (kickoff clock + admin start). These
tests pin the unscheduled complement (:func:`service.maybe_autostart_tournament`):
``start_at IS NULL`` + a **full** field (``max_teams``) + **every** registration
CHECKED_IN → the bracket draws itself, with no admin in the loop. Anything less —
a partial field, a scheduled kickoff, an already-running bracket — must leave the
tournament exactly as the admin left it.

Offline, on the shared :class:`FakeSession` (no DB, no network); the orchestration
half (``open_started_tournament``) is best-effort by design and swallows the
missing Redis, which is precisely its production contract.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from lol_schemas.enums import (
    GameMode,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TournamentStatus,
)

from app.models import Match, Team, TeamMember, Tournament
from app.tournaments import service
from tests._fake_session import FakeSession

_CODE = "ABCD2345"  # already-normalized join code (alphabet-valid)


def _tournament(
    *,
    guild_id: uuid.UUID,
    game_mode: GameMode = GameMode.ARAM_1V1,
    max_teams: int = 2,
    start_at: datetime | None = None,
) -> Tournament:
    return Tournament(
        id=uuid.uuid4(),
        guild_id=guild_id,
        name="Cup",
        game_mode=game_mode,
        status=TournamentStatus.OPEN,
        max_teams=max_teams,
        join_code=_CODE,
        start_at=start_at,
    )


async def _join_and_lock(session: FakeSession, tournament: Tournament, user_id: uuid.UUID) -> None:
    """One solo player's whole path: redeem the code, press Lock in."""
    await service.join_by_code(session, code=_CODE, user_id=user_id)
    await service.check_in_self(session, tournament_id=tournament.id, user_id=user_id)


async def test_unscheduled_full_field_autostarts_on_last_lock_in() -> None:
    """max_teams solo entrants, all locked in → RUNNING with a drawn bracket."""
    guild_id, alice, bob = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, max_teams=2)
    session.store[Tournament].append(tournament)

    await _join_and_lock(session, tournament, alice)
    assert tournament.status is TournamentStatus.OPEN, "half a field must not start"

    await _join_and_lock(session, tournament, bob)

    assert tournament.status is TournamentStatus.RUNNING
    assert len(session.store[Match]) == 1, "2 checked-in seeds -> the one final"


async def test_partial_field_stays_put_even_fully_locked_in() -> None:
    """Everyone present locked in, but the field is not full → no auto-start."""
    guild_id = uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, max_teams=4)
    session.store[Tournament].append(tournament)

    for player in (uuid.uuid4(), uuid.uuid4()):
        await _join_and_lock(session, tournament, player)

    assert tournament.status is TournamentStatus.OPEN, "2/4 slots: a team may be mid-join"
    assert session.store[Match] == []


async def test_scheduled_tournament_never_autostarts() -> None:
    """A kickoff clock (start_at) keeps the admin/scheduler path authoritative."""
    guild_id = uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(
        guild_id=guild_id, max_teams=2, start_at=datetime.now(UTC) + timedelta(hours=1)
    )
    session.store[Tournament].append(tournament)

    for player in (uuid.uuid4(), uuid.uuid4()):
        await _join_and_lock(session, tournament, player)

    assert tournament.status is TournamentStatus.OPEN
    assert session.store[Match] == []


async def test_admin_force_check_in_can_complete_the_field() -> None:
    """The admin override on the last no-show team triggers the same auto-start."""
    guild_id, captain, solo = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5, max_teams=2)
    session.store[Tournament].append(tournament)

    for cap, name in ((captain, "Alpha"), (solo, "Bravo")):
        team = Team(id=uuid.uuid4(), guild_id=guild_id, name=name, captain_user_id=cap)
        session.store[Team].append(team)
        session.store[TeamMember].append(
            TeamMember(
                team_id=team.id,
                user_id=cap,
                guild_id=guild_id,
                role=TeamMemberRole.CAPTAIN,
                status=TeamMemberStatus.ACTIVE,
            )
        )
        await service.join_by_code(session, code=_CODE, user_id=cap)

    reg = await service.check_in_self(session, tournament_id=tournament.id, user_id=captain)
    assert reg.status is RegistrationStatus.CHECKED_IN
    assert tournament.status is TournamentStatus.OPEN, "the other team never locked in"

    # Bravo's captain vanished; the admin forces the team in — the field is now
    # full and fully checked in, so the bracket must draw itself.
    bravo = next(t for t in session.store[Team] if t.name == "Bravo")
    await service.check_in(session, tournament_id=tournament.id, team_id=bravo.id)

    assert tournament.status is TournamentStatus.RUNNING
    assert len(session.store[Match]) == 1


async def test_relocking_after_the_start_is_a_harmless_no_op() -> None:
    """A player pressing Lock in again post-start must not raise or re-bracket."""
    guild_id, alice, bob = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, max_teams=2)
    session.store[Tournament].append(tournament)

    await _join_and_lock(session, tournament, alice)
    await _join_and_lock(session, tournament, bob)
    assert tournament.status is TournamentStatus.RUNNING

    reg = await service.check_in_self(session, tournament_id=tournament.id, user_id=alice)

    assert reg.status is RegistrationStatus.CHECKED_IN
    assert tournament.status is TournamentStatus.RUNNING
    assert len(session.store[Match]) == 1, "no second bracket"
