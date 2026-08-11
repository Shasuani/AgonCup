"""Tournament domain service: CRUD, registration/check-in, bracket start.

Guild scoping is enforced by RLS on the tenant session (the GUC is already set by
``get_tenant_session``); inserts read the acting guild back from the GUC via
:func:`_current_guild` so a row can never be written for the wrong tenant (the
``guild_isolation`` WITH CHECK would reject it anyway — this just supplies the
value). The same-guild invariant between tournament/team/registration/match is
additionally structural (composite FKs, PLAN.md §2).

Bracket generation reuses the unit-tested single-elimination engine in
``lol_core`` (PLAN.md §3.1 bracket engine). :func:`plan_bracket_matches` is a pure
function (no DB) so it is exhaustively testable on its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lol_core.bracket import generate_single_elimination
from lol_schemas.enums import (
    GameMode,
    MatchStatus,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TournamentFormat,
    TournamentStatus,
)
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.joincodes import generate_join_code, normalize_join_code
from app.models import (
    Match,
    Registration,
    Team,
    TeamMember,
    Tournament,
    TournamentHide,
    TournamentParticipant,
    User,
)

MAX_TEAMS_HARD_CAP = 16

#: A tournament in one of these states will never be played again, so a player is
#: allowed to clear it off their own "my tournaments" list (:func:`hide_tournament`).
#: Everything else is still live — hiding it would remove their own way back to it.
TERMINAL_STATUSES: frozenset[TournamentStatus] = frozenset(
    {TournamentStatus.COMPLETED, TournamentStatus.CANCELLED}
)

#: Series lengths a tournament may pick: single game, best-of-3, best-of-5.
ALLOWED_BEST_OF: frozenset[int] = frozenset({1, 3, 5})


class TournamentError(Exception):
    """Base class for tournament service failures."""


class TournamentNotFoundError(TournamentError):
    """No tournament with that id in the acting guild."""


class RegistrationClosedError(TournamentError):
    """The tournament is not accepting registrations in its current state."""


class TournamentFullError(TournamentError):
    """The tournament already holds ``max_teams`` registrations."""


class NotEnoughTeamsError(TournamentError):
    """Fewer than two eligible teams to start a bracket."""


class InvalidStateError(TournamentError):
    """The tournament is not in a state that permits the requested transition."""


class AmbiguousTournamentError(TournamentError):
    """More than one tournament in the acting guild shares that name — use the id."""


class InvalidJoinCodeError(TournamentError):
    """No tournament in the acting guild has that join code (bad/foreign code)."""


class NoCaptainTeamError(TournamentError):
    """A team-mode code was redeemed by someone who captains no team — create one first."""


class AmbiguousCaptainTeamError(TournamentError):
    """The redeemer captains more than one team — they must name which team joins."""


class NotTeamCaptainError(TournamentError):
    """A team was named but the redeemer is not its captain (only the leader enrols)."""


class RegistrationNotOpenError(TournamentError):
    """Enrolment was attempted before the scheduled ``registration_opens_at``."""


class TournamentNotFinishedError(TournamentError):
    """A player tried to clear a tournament that is still live off their list."""


def _utcnow() -> datetime:
    """Current UTC time (tz-aware). Isolated so tests can monkeypatch it."""
    return datetime.now(tz=UTC)


async def _current_guild(session: AsyncSession) -> uuid.UUID:
    """Read the acting guild from the transaction GUC (set by get_tenant_session)."""
    gid = (
        await session.execute(text("SELECT current_setting('app.current_guild', true)"))
    ).scalar_one_or_none()
    if not gid:
        # Fail closed: never write a guild-scoped row without a resolved tenant.
        raise InvalidStateError("no acting guild on the session")
    return uuid.UUID(gid)


# ---------------------------------------------------------------------------
# Pure bracket planning (no DB — exhaustively unit-tested)
# ---------------------------------------------------------------------------
def plan_bracket_matches(
    *,
    tournament_id: uuid.UUID,
    guild_id: uuid.UUID,
    seeded_team_ids: Sequence[uuid.UUID],
) -> list[Match]:
    """Map a single-elimination bracket onto ``Match`` rows (best seed first).

    Byes (auto-advanced first-round matches) are emitted as ``WALKOVER`` matches
    with ``winner_team_id`` pre-filled; real matches are ``PENDING`` with no
    winner. Later-round placeholders carry no teams yet (filled as results land).
    """
    bracket = generate_single_elimination(list(seeded_team_ids))
    matches: list[Match] = []
    for bm in bracket.all_matches():
        matches.append(
            Match(
                guild_id=guild_id,
                tournament_id=tournament_id,
                round=bm.round_no,
                bracket_slot=bm.slot,
                team_a=bm.team_a,
                team_b=bm.team_b,
                winner_team_id=bm.winner if bm.is_bye else None,
                status=MatchStatus.WALKOVER if bm.is_bye else MatchStatus.PENDING,
            )
        )
    return matches


# ---------------------------------------------------------------------------
# CRUD + lifecycle (async; tenant-scoped via RLS)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class StartResult:
    tournament_id: str
    matches_created: int
    rounds: int
    byes: int


async def create_tournament(
    session: AsyncSession,
    *,
    name: str,
    format: TournamentFormat = TournamentFormat.SINGLE_ELIMINATION,
    game_mode: GameMode = GameMode.SUMMONERS_RIFT_5V5,
    best_of: int = 1,
    max_teams: int = MAX_TEAMS_HARD_CAP,
    ruleset: dict[str, Any] | None = None,
    registration_opens_at: datetime | None = None,
    start_at: datetime | None = None,
) -> Tournament:
    """Create a DRAFT tournament in the acting guild (5v5 Rift or 1v1 ARAM).

    Unscheduled (both timestamps None) → joinable immediately (as before). Scheduled
    → ``registration_opens_at`` gates enrolment until then, and the scheduler flips
    DRAFT→OPEN + announces at that time (PLAN.md §3.2). Times are UTC and must satisfy
    ``registration_opens_at`` ≤ ``start_at`` (and both in the future).
    """
    if not 2 <= max_teams <= MAX_TEAMS_HARD_CAP:
        raise InvalidStateError(f"max_teams must be in [2, {MAX_TEAMS_HARD_CAP}]")
    if best_of not in ALLOWED_BEST_OF:
        raise InvalidStateError(f"best_of must be one of {sorted(ALLOWED_BEST_OF)}")
    now = _utcnow()
    if registration_opens_at is not None and registration_opens_at < now:
        raise InvalidStateError("registration_opens_at must be in the future")
    if start_at is not None and start_at < now:
        raise InvalidStateError("start_at must be in the future")
    if (
        registration_opens_at is not None
        and start_at is not None
        and start_at < registration_opens_at
    ):
        raise InvalidStateError("start_at must be at/after registration_opens_at")
    guild_id = await _current_guild(session)
    tournament = Tournament(
        guild_id=guild_id,
        name=name,
        format=format,
        game_mode=game_mode,
        best_of=best_of,
        max_teams=max_teams,
        status=TournamentStatus.DRAFT,
        registration_opens_at=registration_opens_at,
        start_at=start_at,
        join_code=generate_join_code(),
        ruleset_json=ruleset or {},
    )
    session.add(tournament)
    await session.flush()
    return tournament


async def get_tournament(session: AsyncSession, *, tournament_id: uuid.UUID) -> Tournament:
    """Load a tournament (RLS already scopes to the acting guild)."""
    t = (
        await session.execute(select(Tournament).where(Tournament.id == tournament_id))
    ).scalar_one_or_none()
    if t is None:
        raise TournamentNotFoundError(str(tournament_id))
    return t


async def get_tournament_by_name(session: AsyncSession, *, name: str) -> Tournament:
    """Resolve a tournament by display name within the acting guild (RLS-scoped).

    Case-insensitive convenience for the Discord flow so admins/captains never
    paste a UUID. No match -> :class:`TournamentNotFoundError`; a name shared by
    more than one tournament -> :class:`AmbiguousTournamentError` (use the id).
    """
    rows = (
        (
            await session.execute(
                select(Tournament).where(func.lower(Tournament.name) == name.strip().lower())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise TournamentNotFoundError(name)
    if len(rows) > 1:
        raise AmbiguousTournamentError(name)
    return rows[0]


def _looks_like_id_prefix(ref: str) -> bool:
    """True if ``ref`` is a short hex id prefix (e.g. ``b00cc7da``) — >=4 hex chars."""
    r = ref.strip().lower()
    return len(r) >= 4 and all(c in "0123456789abcdef" for c in r)


async def get_tournament_by_id_prefix(session: AsyncSession, *, prefix: str) -> Tournament:
    """Resolve a tournament by the short id prefix shown to users (RLS-scoped)."""
    p = prefix.strip().lower()
    rows = (
        (await session.execute(select(Tournament).where(cast(Tournament.id, String).like(p + "%"))))
        .scalars()
        .all()
    )
    if not rows:
        raise TournamentNotFoundError(prefix)
    if len(rows) > 1:
        raise AmbiguousTournamentError(prefix)
    return rows[0]


async def resolve_tournament_ref(session: AsyncSession, *, ref: str) -> Tournament:
    """Resolve a tournament from a full id, a name, OR a short id prefix.

    Precedence: exact UUID -> exact name -> short hex-id prefix.
    """
    ref = ref.strip()
    try:
        return await get_tournament(session, tournament_id=uuid.UUID(ref))
    except ValueError:
        pass
    try:
        return await get_tournament_by_name(session, name=ref)
    except TournamentNotFoundError:
        if _looks_like_id_prefix(ref):
            return await get_tournament_by_id_prefix(session, prefix=ref)
        raise


async def list_tournaments(session: AsyncSession) -> list[Tournament]:
    return list((await session.execute(select(Tournament))).scalars().all())


async def get_tournament_by_code(session: AsyncSession, *, code: str) -> Tournament:
    """Resolve a tournament by its join code within the acting guild (RLS-scoped).

    The code is normalized (upper-cased, separators dropped) before lookup so a
    hand-typed ``t-abcd 2345`` resolves. A code that belongs to another guild is
    invisible under RLS and therefore reads as invalid — cross-server join is
    structurally impossible. No match -> :class:`InvalidJoinCodeError`.
    """
    normalized = normalize_join_code(code)
    if not normalized:
        raise InvalidJoinCodeError("empty code")
    t = (
        await session.execute(select(Tournament).where(Tournament.join_code == normalized))
    ).scalar_one_or_none()
    if t is None:
        raise InvalidJoinCodeError(normalized)
    return t


async def join_by_code(
    session: AsyncSession,
    *,
    code: str,
    user_id: uuid.UUID,
    team_id: uuid.UUID | None = None,
) -> tuple[Tournament, TournamentParticipant | Registration]:
    """Redeem a tournament join code — enrolment shape follows the game mode.

    The **one** code the admin hands out is "smart": what it enrols depends on the
    tournament's :class:`~lol_schemas.enums.GameMode`.

    * **Solo modes** (``team_size == 1``, e.g. ``ARAM_1V1``) → the caller enrols as
      an individual :class:`TournamentParticipant`.
    * **Team modes** (``team_size > 1``, e.g. ``SUMMONERS_RIFT_5V5``) → the caller
      must be a **team captain**; their team is registered as a
      :class:`Registration`. ``team_id`` disambiguates when they captain several
      teams (otherwise their sole captained team is used).

    Both paths are idempotent (a unique constraint backs each) and require open
    registration (DRAFT/OPEN) — a running/finished tournament raises
    :class:`RegistrationClosedError`.
    """
    tournament = await get_tournament_by_code(session, code=code)
    if tournament.status not in (TournamentStatus.OPEN, TournamentStatus.DRAFT):
        raise RegistrationClosedError(tournament.status.value)
    # Scheduled-registration gate (PLAN.md §3.2): reject before the window opens,
    # and once the tournament's scheduled start has passed.
    now = _utcnow()
    if tournament.registration_opens_at is not None and now < tournament.registration_opens_at:
        raise RegistrationNotOpenError(tournament.registration_opens_at.isoformat())
    if tournament.start_at is not None and now >= tournament.start_at:
        raise RegistrationClosedError("started")

    if tournament.game_mode.team_size == 1:
        return tournament, await _enrol_solo(session, tournament=tournament, user_id=user_id)

    team = await _resolve_captain_team(session, user_id=user_id, team_id=team_id)
    return tournament, await _register_team_idempotent(session, tournament=tournament, team=team)


async def _solo_team(session: AsyncSession, *, user_id: uuid.UUID, guild_id: uuid.UUID) -> Team:
    """The player's own one-player team — created on first solo enrolment, reused after.

    A solo player has no team, but everything downstream of the bracket (seeding,
    matches, ready-up, lobby orchestration, results) keys on ``team_id``. So a 1v1
    join quietly gets one: named after the player, so the bracket renders their
    name rather than an anonymous slot, and flagged ``is_solo`` so it never shows
    up as a real team (see the column's note in models/tables.py).

    It carries **no join code** — nobody can be invited into someone's solo team.
    One per (user, guild), reused across tournaments so a player who enters five
    1v1s does not accumulate five phantom teams.
    """
    existing = (
        await session.execute(
            select(Team).where(Team.captain_user_id == user_id, Team.is_solo.is_(True))
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    display_name = (
        await session.execute(select(User.display_name).where(User.id == user_id))
    ).scalar_one_or_none()
    team = Team(
        guild_id=guild_id,
        name=display_name or "Player",
        captain_user_id=user_id,
        join_code=None,
        is_solo=True,
    )
    session.add(team)
    await session.flush()  # assign team.id before the membership row references it
    session.add(
        TeamMember(
            team_id=team.id,
            user_id=user_id,
            guild_id=guild_id,
            role=TeamMemberRole.CAPTAIN,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    await session.flush()
    return team


async def _enrol_solo(
    session: AsyncSession, *, tournament: Tournament, user_id: uuid.UUID
) -> TournamentParticipant:
    """Enrol a lone player (solo game modes). Idempotent on (tournament, user).

    Writes BOTH records, on purpose: the ``TournamentParticipant`` is the "this
    person joined" fact (and what the PII purge walks), while the ``Registration``
    of their :func:`_solo_team` is what the bracket actually seeds from. The
    participant's ``team_id`` links the two — exactly the denormalization that
    column was defined for.

    The registration lands ``PENDING`` like any other: the player still has to
    lock in (:func:`check_in`) before they can be seeded.
    """
    guild_id = await _current_guild(session)
    team = await _solo_team(session, user_id=user_id, guild_id=guild_id)

    existing = (
        await session.execute(
            select(TournamentParticipant).where(
                TournamentParticipant.tournament_id == tournament.id,
                TournamentParticipant.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Re-redeeming the code is idempotent, but repair a participant that
        # predates the solo-team path (or lost its link) rather than leaving it
        # unseedable.
        existing.team_id = team.id
        await _register_team_idempotent(session, tournament=tournament, team=team)
        await session.flush()
        return existing

    participant = TournamentParticipant(
        guild_id=guild_id,
        tournament_id=tournament.id,
        user_id=user_id,
        team_id=team.id,
    )
    session.add(participant)
    await _register_team_idempotent(session, tournament=tournament, team=team)
    await session.flush()
    return participant


async def _resolve_captain_team(
    session: AsyncSession, *, user_id: uuid.UUID, team_id: uuid.UUID | None
) -> Team:
    """Pick the team the caller enrols in a team-mode tournament (captain-only).

    With an explicit ``team_id`` the caller must be that team's captain. Without
    one, the caller's sole captained team is used; zero → :class:`NoCaptainTeamError`,
    many → :class:`AmbiguousCaptainTeamError` (they must name a team).
    """
    if team_id is not None:
        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
        if team is None:
            raise InvalidStateError("team not found in this guild")
        if team.captain_user_id != user_id:
            raise NotTeamCaptainError(str(team_id))
        return team

    # `is_solo` teams are excluded: they are auto-created 1v1 scaffolding, not
    # teams the player captains. Counting them would make anyone who has ever
    # entered a 1v1 look "ambiguous" the moment they redeem a 5v5 code.
    captained = list(
        (
            await session.execute(
                select(Team).where(Team.captain_user_id == user_id, Team.is_solo.is_(False))
            )
        ).scalars()
    )
    if not captained:
        raise NoCaptainTeamError(str(user_id))
    if len(captained) > 1:
        raise AmbiguousCaptainTeamError(str(user_id))
    return captained[0]


async def _register_team_idempotent(
    session: AsyncSession, *, tournament: Tournament, team: Team
) -> Registration:
    """Register a team into a team-mode tournament. Idempotent on (tournament, team)."""
    # Same-guild invariant (defense in depth): a team can only enter a tournament in
    # its own guild. RLS normally makes a foreign-guild tournament invisible (the code
    # reads as invalid), but if RLS is bypassed the composite FK would 500 — so reject
    # a cross-guild code cleanly as invalid instead.
    if tournament.guild_id != team.guild_id:
        raise InvalidJoinCodeError("code belongs to another server")

    existing = (
        await session.execute(
            select(Registration).where(
                Registration.tournament_id == tournament.id,
                Registration.team_id == team.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    count = (
        await session.execute(
            select(func.count())
            .select_from(Registration)
            .where(Registration.tournament_id == tournament.id)
        )
    ).scalar_one()
    if count >= tournament.max_teams:
        raise TournamentFullError(f"tournament is full ({tournament.max_teams})")

    guild_id = await _current_guild(session)
    reg = Registration(
        guild_id=guild_id,
        tournament_id=tournament.id,
        team_id=team.id,
        status=RegistrationStatus.PENDING,
    )
    session.add(reg)
    await session.flush()
    await _sync_team_participants(session, tournament=tournament, team_id=team.id)
    return reg


async def _active_member_ids(session: AsyncSession, *, team_id: uuid.UUID) -> list[uuid.UUID]:
    """The team's active roster — the set that must lock in for the team to be ready."""
    return [
        uid
        for (uid,) in (
            await session.execute(
                select(TeamMember.user_id).where(
                    TeamMember.team_id == team_id,
                    TeamMember.status == TeamMemberStatus.ACTIVE,
                )
            )
        ).all()
    ]


async def _sync_team_participants(
    session: AsyncSession, *, tournament: Tournament, team_id: uuid.UUID
) -> None:
    """Give every active member of a registered team their own participant row.

    The captain redeems the code once and the whole roster is entered — no teammate
    ever needs the code. Each of them still owns a ``TournamentParticipant``, because
    lock-in is per player and needs somewhere to land.

    Idempotent (``(tournament_id, user_id)`` is unique), so re-redeeming the code or a
    late-joining member simply tops the set up. Members who joined the team *after*
    registration are covered by :func:`check_in_self`, which creates the row on demand
    rather than requiring a sweep.
    """
    guild_id = await _current_guild(session)
    existing = {
        uid
        for (uid,) in (
            await session.execute(
                select(TournamentParticipant.user_id).where(
                    TournamentParticipant.tournament_id == tournament.id
                )
            )
        ).all()
    }
    for user_id in await _active_member_ids(session, team_id=team_id):
        if user_id in existing:
            continue
        session.add(
            TournamentParticipant(
                guild_id=guild_id,
                tournament_id=tournament.id,
                user_id=user_id,
                team_id=team_id,
            )
        )
    await session.flush()


async def list_my_tournaments(session: AsyncSession, *, user_id: uuid.UUID) -> list[Tournament]:
    """List tournaments the acting user is in — as a **solo participant** (1v1) OR
    via a **team** they're an active member of (5v5). RLS scopes to the guild.

    Solo enrolment lands a ``TournamentParticipant`` row; team enrolment registers
    the team (a ``Registration``) — so a member sees their team's tournaments too.

    Tournaments the user has dismissed (:func:`hide_tournament`) are filtered out.
    That is a per-user view, not a deletion: another entrant's list is untouched
    and an admin still sees every tournament in the guild.
    """
    # tournament ids where the user's active team is registered
    via_team = (
        select(Registration.tournament_id)
        .join(TeamMember, TeamMember.team_id == Registration.team_id)
        .where(
            TeamMember.user_id == user_id,
            TeamMember.status == TeamMemberStatus.ACTIVE,
        )
    )
    # tournament ids where the user is a solo participant
    via_solo = select(TournamentParticipant.tournament_id).where(
        TournamentParticipant.user_id == user_id
    )
    # tournament ids this user has cleared off their own list
    hidden = select(TournamentHide.tournament_id).where(TournamentHide.user_id == user_id)
    rows = (
        await session.execute(
            select(Tournament)
            .where(
                or_(Tournament.id.in_(via_team), Tournament.id.in_(via_solo)),
                Tournament.id.not_in(hidden),
            )
            .order_by(Tournament.created_at.desc())
        )
    ).scalars()
    return list(rows)


async def hide_tournament(
    session: AsyncSession, *, tournament_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Clear a finished tournament off **this user's** ``/mine`` list (PLAN.md §3.1).

    Not a deletion. The tournament, its bracket and its results stay exactly as
    they are for every other entrant and for the admin — this only writes a
    ``TournamentHide`` row that :func:`list_my_tournaments` filters on.

    Only a terminal tournament (completed / cancelled) may be hidden: dismissing
    one that is still open or running would take away the caller's own route back
    to the lock-in button and the bracket. Idempotent — hiding twice is a no-op,
    so a double-click cannot trip the unique constraint.

    Raises :class:`TournamentNotFoundError` if the id is not in the acting guild
    (RLS makes a foreign guild's tournament indistinguishable from a missing one)
    and :class:`TournamentNotFinishedError` if it is still live.
    """
    tournament = (
        await session.execute(select(Tournament).where(Tournament.id == tournament_id))
    ).scalar_one_or_none()
    if tournament is None:
        raise TournamentNotFoundError(str(tournament_id))
    if tournament.status not in TERMINAL_STATUSES:
        raise TournamentNotFinishedError(str(tournament.status))

    existing = (
        await session.execute(
            select(TournamentHide).where(
                TournamentHide.tournament_id == tournament_id,
                TournamentHide.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    session.add(
        TournamentHide(
            guild_id=await _current_guild(session),
            tournament_id=tournament_id,
            user_id=user_id,
        )
    )
    await session.flush()


@dataclass(frozen=True)
class BracketMatch:
    """One match in a tournament bracket, with team names resolved for display."""

    id: uuid.UUID
    round: int
    slot: int
    team_a_id: uuid.UUID | None
    team_a_name: str | None
    team_b_id: uuid.UUID | None
    team_b_name: str | None
    winner_team_id: uuid.UUID | None
    status: str
    ready_deadline_at: datetime | None
    team_a_ready: bool
    team_b_ready: bool


async def get_bracket(session: AsyncSession, *, tournament_id: uuid.UUID) -> list[BracketMatch]:
    """The tournament's matches ordered by (round, slot), team names resolved.

    Empty list before the tournament is started (no matches yet). RLS scopes both
    the tournament and the team-name lookup to the acting guild. 404 if the
    tournament does not exist here.
    """
    await get_tournament(session, tournament_id=tournament_id)
    matches = (
        (
            await session.execute(
                select(Match)
                .where(Match.tournament_id == tournament_id)
                .order_by(Match.round, Match.bracket_slot)
            )
        )
        .scalars()
        .all()
    )
    names: dict[uuid.UUID, str] = {
        r[0]: r[1] for r in (await session.execute(select(Team.id, Team.name))).all()
    }
    return [
        BracketMatch(
            id=m.id,
            round=m.round,
            slot=m.bracket_slot,
            team_a_id=m.team_a,
            team_a_name=names.get(m.team_a) if m.team_a else None,
            team_b_id=m.team_b,
            team_b_name=names.get(m.team_b) if m.team_b else None,
            winner_team_id=m.winner_team_id,
            status=m.status.value if hasattr(m.status, "value") else str(m.status),
            ready_deadline_at=m.ready_deadline_at,
            team_a_ready=m.team_a_ready_at is not None,
            team_b_ready=m.team_b_ready_at is not None,
        )
        for m in matches
    ]


async def register_team(
    session: AsyncSession, *, tournament_id: uuid.UUID, team_id: uuid.UUID
) -> Registration:
    """Register a team into a tournament (PENDING), enforcing the max_teams cap.

    The same-guild invariant is structural (composite FKs); RLS guarantees the
    tournament and team are both in the acting guild.
    """
    tournament = await get_tournament(session, tournament_id=tournament_id)
    if tournament.status not in (TournamentStatus.OPEN, TournamentStatus.DRAFT):
        raise RegistrationClosedError(tournament.status.value)

    # Team must exist in this guild (RLS-scoped).
    team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise InvalidStateError("team not found in this guild")

    count = (
        await session.execute(
            select(func.count())
            .select_from(Registration)
            .where(Registration.tournament_id == tournament_id)
        )
    ).scalar_one()
    if count >= tournament.max_teams:
        raise TournamentFullError(f"tournament is full ({tournament.max_teams})")

    guild_id = await _current_guild(session)
    reg = Registration(
        guild_id=guild_id,
        tournament_id=tournament_id,
        team_id=team_id,
        status=RegistrationStatus.PENDING,
    )
    session.add(reg)
    await session.flush()
    return reg


async def assert_team_captain(
    session: AsyncSession, *, team_id: uuid.UUID, user_id: uuid.UUID
) -> Team:
    """Raise unless ``user_id`` captains ``team_id`` in the acting guild."""
    team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise InvalidStateError("team not found in this guild")
    if team.captain_user_id != user_id:
        raise NotTeamCaptainError(str(team_id))
    return team


async def resolve_check_in_team(
    session: AsyncSession, *, tournament_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    """Which team the caller locks in, when the client did not name one.

    Solo (1v1) players have no team to name — they lock themselves in — so their
    ``TournamentParticipant.team_id`` (their :func:`_solo_team`) answers it.

    For a team tournament the answer is scoped to **this tournament**: of the teams
    the caller captains, the one actually entered here. Falling back to
    :func:`_resolve_captain_team` instead would reject a captain of several teams as
    ambiguous even when only one of them is in this tournament — the common case
    once someone runs more than one roster.
    """
    solo_team_id = (
        await session.execute(
            select(TournamentParticipant.team_id).where(
                TournamentParticipant.tournament_id == tournament_id,
                TournamentParticipant.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if solo_team_id is not None:
        return solo_team_id

    entered = {
        team_id
        for (team_id,) in (
            await session.execute(
                select(Registration.team_id).where(Registration.tournament_id == tournament_id)
            )
        ).all()
    }
    # Membership, not captaincy, is the test: the captain enrols the team but every
    # player locks themselves in, so an ordinary member must resolve to their team
    # here too. Scoped to teams actually entered in THIS tournament, so belonging to
    # several rosters is only ambiguous when more than one of them is competing.
    mine: list[uuid.UUID] = [
        team_id
        for (team_id,) in (
            await session.execute(
                select(TeamMember.team_id).where(
                    TeamMember.user_id == user_id,
                    TeamMember.status == TeamMemberStatus.ACTIVE,
                )
            )
        ).all()
        if team_id in entered
    ]
    if not mine:
        # Fallback for a captain with no membership row of their own (older teams,
        # or one repaired by hand): they plainly belong to the team they captain.
        mine = [
            team.id
            for team in (
                await session.execute(select(Team).where(Team.captain_user_id == user_id))
            ).scalars()
            if team.id in entered
        ]
    if not mine:
        raise NoCaptainTeamError(str(user_id))
    if len(set(mine)) > 1:
        raise AmbiguousCaptainTeamError(str(user_id))
    return mine[0]


async def check_in(
    session: AsyncSession, *, tournament_id: uuid.UUID, team_id: uuid.UUID
) -> Registration:
    """Force a whole team CHECKED_IN — the admin override, not the player path.

    Players lock themselves in one by one (:func:`check_in_self`). This bypasses
    that: it marks every member locked in and flips the registration, for an admin
    unblocking a bracket held up by a teammate who never showed.
    """
    reg = await _registration_for(session, tournament_id=tournament_id, team_id=team_id)
    now = _utcnow()
    for participant in await _participants_for_team(
        session, tournament_id=tournament_id, team_id=team_id
    ):
        if participant.locked_in_at is None:
            participant.locked_in_at = now
    reg.status = RegistrationStatus.CHECKED_IN
    await session.flush()
    return reg


async def _registration_for(
    session: AsyncSession, *, tournament_id: uuid.UUID, team_id: uuid.UUID
) -> Registration:
    reg = (
        await session.execute(
            select(Registration).where(
                Registration.tournament_id == tournament_id,
                Registration.team_id == team_id,
            )
        )
    ).scalar_one_or_none()
    if reg is None:
        raise InvalidStateError("registration not found")
    return reg


async def repair_missing_registration(
    session: AsyncSession, *, tournament: Tournament, team_id: uuid.UUID
) -> Registration:
    """Ensure the entry a participant already holds has a registration behind it.

    "You are in this tournament" and "who is entered" read from different tables:
    the first from ``TournamentParticipant``, the second from ``Registration``.
    Solo enrolments written before the solo-team path only ever created the
    participant, so those rows say both — the client shows *you're in* next to
    *nobody has joined*, and lock-in has nothing to attach to.

    Rather than leave that data stranded (its owner cannot re-redeem a code for a
    tournament they are already in), the missing half is written on demand. Not a
    migration: the broken rows are only discoverable per (tournament, user), and
    the entry itself was always legitimate.
    """
    existing = (
        await session.execute(
            select(Registration).where(
                Registration.tournament_id == tournament.id,
                Registration.team_id == team_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    reg = Registration(
        guild_id=await _current_guild(session),
        tournament_id=tournament.id,
        team_id=team_id,
        status=RegistrationStatus.PENDING,
    )
    session.add(reg)
    await session.flush()
    return reg


async def _participants_for_team(
    session: AsyncSession, *, tournament_id: uuid.UUID, team_id: uuid.UUID
) -> list[TournamentParticipant]:
    return list(
        (
            await session.execute(
                select(TournamentParticipant).where(
                    TournamentParticipant.tournament_id == tournament_id,
                    TournamentParticipant.team_id == team_id,
                )
            )
        )
        .scalars()
        .all()
    )


async def check_in_self(
    session: AsyncSession, *, tournament_id: uuid.UUID, user_id: uuid.UUID
) -> Registration:
    """Lock the **caller themselves** in — the button every entrant presses.

    Works the same for both shapes, which is the point: a 1v1 player is a roster of
    one, so "everyone locked in" and "I locked in" coincide and no branch is needed.

    The participant row is created on demand rather than assumed, so someone who
    joined the team after the captain redeemed the code can still lock in without a
    backfill sweep. Idempotent — pressing it twice keeps the first timestamp.

    The team's registration flips to CHECKED_IN only once **every active member** has
    locked in; until then it stays PENDING and the team cannot be seeded. That is the
    whole reason lock-in moved off the captain: their word is not evidence that four
    other people are at their keyboards.
    """
    team_id = await resolve_check_in_team(session, tournament_id=tournament_id, user_id=user_id)
    # Repaired rather than required: an entry whose registration was never written
    # (see `repair_missing_registration`) would otherwise show the player as in the
    # tournament while refusing every lock-in they attempt.
    tournament = await get_tournament(session, tournament_id=tournament_id)
    reg = await repair_missing_registration(session, tournament=tournament, team_id=team_id)

    participant = (
        await session.execute(
            select(TournamentParticipant).where(
                TournamentParticipant.tournament_id == tournament_id,
                TournamentParticipant.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if participant is None:
        participant = TournamentParticipant(
            guild_id=await _current_guild(session),
            tournament_id=tournament_id,
            user_id=user_id,
            team_id=team_id,
        )
        session.add(participant)
    if participant.locked_in_at is None:
        participant.locked_in_at = _utcnow()
    participant.team_id = team_id
    await session.flush()

    locked = {
        p.user_id
        for p in await _participants_for_team(session, tournament_id=tournament_id, team_id=team_id)
        if p.locked_in_at is not None
    }
    roster = await _active_member_ids(session, team_id=team_id)
    if roster and all(uid in locked for uid in roster):
        reg.status = RegistrationStatus.CHECKED_IN
    await session.flush()
    return reg


@dataclass(frozen=True)
class EntrantPlayer:
    """One player inside an entry, and whether they personally locked in."""

    user_id: uuid.UUID
    name: str
    locked_in: bool
    is_you: bool


@dataclass(frozen=True)
class Entrant:
    """One entry in a tournament, as the join/lock-in screens show it.

    Deliberately mode-agnostic: a 5v5 entry is a team and a 1v1 entry is a player
    (their :func:`_solo_team`, named after them), so a single list renders both.
    ``is_solo`` lets the client drop the word "team" from the 1v1 wording.
    """

    team_id: uuid.UUID
    name: str
    locked_in: bool
    is_solo: bool
    #: True for the caller's own entry — what lets the client say "you're in"
    #: without guessing by name.
    is_you: bool
    #: True when the caller still has to lock THEMSELVES in on this entry — i.e.
    #: it is their team and they have not pressed it yet. Lock-in is per player,
    #: so this is never true for somebody else's team.
    can_lock_in: bool
    #: The roster, each with their own lock-in state. This is what makes "waiting
    #: on two teammates" visible instead of a team that is silently not ready.
    players: tuple[EntrantPlayer, ...] = ()


async def list_entrants(
    session: AsyncSession, *, tournament_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> list[Entrant]:
    """Everyone registered in a tournament, in seed (registration) order.

    This is what tells a player their join actually landed — and who else is in.
    RLS scopes both the registrations and the team names to the acting guild.

    ``user_id`` marks the caller's own entry. Membership is the test, so it covers
    both shapes: their solo team in 1v1, the team they play for in 5v5.

    A participant whose registration was never written still shows up here, as an
    entry that has simply not locked in. Omitting them is what made this list say
    "nobody has joined" to a player the tournament list had just told they were in
    — and this is a GET, so it reports the entry rather than repairing it; the
    write happens when they press Lock in (:func:`repair_missing_registration`).
    """
    regs = list(
        (
            await session.execute(
                select(Registration)
                .where(Registration.tournament_id == tournament_id)
                .order_by(Registration.id)
            )
        )
        .scalars()
        .all()
    )
    participants = list(
        (
            await session.execute(
                select(TournamentParticipant).where(
                    TournamentParticipant.tournament_id == tournament_id
                )
            )
        )
        .scalars()
        .all()
    )
    #: Entry team -> its registration, ``None`` for a participant whose
    #: registration was never written. Registrations first so seed order (which is
    #: registration order) is unchanged for every healthy tournament.
    entries: dict[uuid.UUID, Registration | None] = {r.team_id: r for r in regs}
    for participant in participants:
        if participant.team_id is not None:
            entries.setdefault(participant.team_id, None)

    # Names come from a second read rather than a JOIN: a tournament holds at most
    # MAX_TEAMS_HARD_CAP rows, so this is one extra small query, and it keeps the
    # function exercisable by the offline fake session (which models no joins).
    teams = {
        t.id: t for t in (await session.execute(select(Team))).scalars().all() if t.id in entries
    }

    my_team_ids: set[uuid.UUID] = set()
    if user_id is not None:
        my_team_ids = {
            tid
            for (tid,) in (
                await session.execute(
                    select(TeamMember.team_id).where(
                        TeamMember.user_id == user_id,
                        TeamMember.status == TeamMemberStatus.ACTIVE,
                    )
                )
            ).all()
        }

    # Rosters come from team_members (the source of truth), so a player who joined
    # the team after the captain redeemed the code shows up immediately — this is a
    # GET and must not write the participant rows a backfill would need.
    roster: dict[uuid.UUID, list[uuid.UUID]] = {}
    for team_id, member_id in (
        await session.execute(
            select(TeamMember.team_id, TeamMember.user_id).where(
                TeamMember.team_id.in_(list(entries)),
                TeamMember.status == TeamMemberStatus.ACTIVE,
            )
        )
    ).all():
        roster.setdefault(team_id, []).append(member_id)

    locked_user_ids = {
        p.user_id
        for p in (
            await session.execute(
                select(TournamentParticipant).where(
                    TournamentParticipant.tournament_id == tournament_id
                )
            )
        )
        .scalars()
        .all()
        if p.locked_in_at is not None
    }
    member_names: dict[uuid.UUID, str] = {}
    for uid, display_name in (
        await session.execute(
            select(User.id, User.display_name).where(
                User.id.in_([uid for ids in roster.values() for uid in ids])
            )
        )
    ).all():
        member_names[uid] = display_name

    entrants: list[Entrant] = []
    for entry_team_id, reg in entries.items():
        team = teams.get(entry_team_id)
        if team is None:  # RLS hid it (foreign guild) — not ours to show
            continue
        players = tuple(
            EntrantPlayer(
                user_id=uid,
                name=member_names.get(uid) or "Player",
                locked_in=uid in locked_user_ids,
                is_you=uid == user_id,
            )
            for uid in roster.get(entry_team_id, [])
        )
        mine = entry_team_id in my_team_ids
        entrants.append(
            Entrant(
                team_id=entry_team_id,
                name=team.name,
                # Derived from the roster, not read off the registration: a team
                # that locked in and THEN gained a member is not ready any more,
                # and a stored flag would keep claiming it was while the new
                # player sits there marked "not ready".
                locked_in=(
                    all(p.locked_in for p in players)
                    if players
                    else reg is not None and reg.status == RegistrationStatus.CHECKED_IN
                ),
                is_solo=bool(team.is_solo),
                is_you=mine,
                # Only your own entry, and only while you personally have not
                # pressed it — the captain no longer locks in on anyone's behalf.
                can_lock_in=mine and user_id is not None and user_id not in locked_user_ids,
                players=players,
            )
        )
    return entrants


async def _seeded_team_ids(session: AsyncSession, tournament_id: uuid.UUID) -> list[uuid.UUID]:
    """Checked-in teams in deterministic seed order (registration order = seed).

    v1 seeding is by registration order (stable, attacker-neutral). A skill-based
    seeding input can replace the ORDER BY later without touching the bracket map.
    """
    rows = (
        await session.execute(
            select(Registration.team_id)
            .where(
                Registration.tournament_id == tournament_id,
                Registration.status == RegistrationStatus.CHECKED_IN,
            )
            .order_by(Registration.id)
        )
    ).all()
    return [r[0] for r in rows]


async def start_tournament(session: AsyncSession, *, tournament_id: uuid.UUID) -> StartResult:
    """Generate the bracket from checked-in teams and move to RUNNING.

    Idempotency: only a tournament in OPEN/CHECK_IN can be started; a second call
    finds it RUNNING and raises InvalidStateError rather than double-creating.
    """
    tournament = await get_tournament(session, tournament_id=tournament_id)
    # DRAFT/OPEN/CHECK_IN are all startable: registration is allowed in DRAFT (see
    # register_team), so a draft with >=2 checked-in teams can go straight to a
    # bracket. RUNNING/COMPLETED/CANCELLED cannot restart (no double-bracket).
    if tournament.status not in (
        TournamentStatus.DRAFT,
        TournamentStatus.OPEN,
        TournamentStatus.CHECK_IN,
    ):
        raise InvalidStateError(f"cannot start from state {tournament.status.value}")

    seeds = await _seeded_team_ids(session, tournament_id)
    if len(seeds) < 2:
        raise NotEnoughTeamsError(f"need >=2 checked-in teams, got {len(seeds)}")

    guild_id = await _current_guild(session)
    matches = plan_bracket_matches(
        tournament_id=tournament_id, guild_id=guild_id, seeded_team_ids=seeds
    )
    session.add_all(matches)
    tournament.status = TournamentStatus.RUNNING
    await session.flush()

    rounds = max(m.round for m in matches)
    byes = sum(1 for m in matches if m.status == MatchStatus.WALKOVER)
    return StartResult(
        tournament_id=str(tournament_id),
        matches_created=len(matches),
        rounds=rounds,
        byes=byes,
    )
