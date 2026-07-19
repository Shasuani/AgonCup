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
    TournamentParticipant,
)

MAX_TEAMS_HARD_CAP = 16

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


async def _enrol_solo(
    session: AsyncSession, *, tournament: Tournament, user_id: uuid.UUID
) -> TournamentParticipant:
    """Enrol a lone player (solo game modes). Idempotent on (tournament, user)."""
    existing = (
        await session.execute(
            select(TournamentParticipant).where(
                TournamentParticipant.tournament_id == tournament.id,
                TournamentParticipant.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    guild_id = await _current_guild(session)
    participant = TournamentParticipant(
        guild_id=guild_id,
        tournament_id=tournament.id,
        user_id=user_id,
    )
    session.add(participant)
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
        team = (
            await session.execute(select(Team).where(Team.id == team_id))
        ).scalar_one_or_none()
        if team is None:
            raise InvalidStateError("team not found in this guild")
        if team.captain_user_id != user_id:
            raise NotTeamCaptainError(str(team_id))
        return team

    captained = list(
        (
            await session.execute(select(Team).where(Team.captain_user_id == user_id))
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
    return reg


async def list_my_tournaments(session: AsyncSession, *, user_id: uuid.UUID) -> list[Tournament]:
    """List tournaments the acting user is in — as a **solo participant** (1v1) OR
    via a **team** they're an active member of (5v5). RLS scopes to the guild.

    Solo enrolment lands a ``TournamentParticipant`` row; team enrolment registers
    the team (a ``Registration``) — so a member sees their team's tournaments too.
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
    rows = (
        await session.execute(
            select(Tournament)
            .where(or_(Tournament.id.in_(via_team), Tournament.id.in_(via_solo)))
            .order_by(Tournament.created_at.desc())
        )
    ).scalars()
    return list(rows)


@dataclass(frozen=True)
class BracketMatch:
    """One match in a tournament bracket, with team names resolved for display."""

    round: int
    slot: int
    team_a_id: uuid.UUID | None
    team_a_name: str | None
    team_b_id: uuid.UUID | None
    team_b_name: str | None
    winner_team_id: uuid.UUID | None
    status: str


async def get_bracket(session: AsyncSession, *, tournament_id: uuid.UUID) -> list[BracketMatch]:
    """The tournament's matches ordered by (round, slot), team names resolved.

    Empty list before the tournament is started (no matches yet). RLS scopes both
    the tournament and the team-name lookup to the acting guild. 404 if the
    tournament does not exist here.
    """
    await get_tournament(session, tournament_id=tournament_id)
    matches = (
        await session.execute(
            select(Match)
            .where(Match.tournament_id == tournament_id)
            .order_by(Match.round, Match.bracket_slot)
        )
    ).scalars().all()
    names: dict[uuid.UUID, str] = {
        r[0]: r[1] for r in (await session.execute(select(Team.id, Team.name))).all()
    }
    return [
        BracketMatch(
            round=m.round,
            slot=m.bracket_slot,
            team_a_id=m.team_a,
            team_a_name=names.get(m.team_a) if m.team_a else None,
            team_b_id=m.team_b,
            team_b_name=names.get(m.team_b) if m.team_b else None,
            winner_team_id=m.winner_team_id,
            status=m.status.value if hasattr(m.status, "value") else str(m.status),
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


async def check_in(
    session: AsyncSession, *, tournament_id: uuid.UUID, team_id: uuid.UUID
) -> Registration:
    """Move an approved registration to CHECKED_IN (eligible for the bracket)."""
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
    reg.status = RegistrationStatus.CHECKED_IN
    await session.flush()
    return reg


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
