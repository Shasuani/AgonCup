"""Team listings — "Find a team" across every server (PLAN.md §3.1).

Offline, over an RLS-*simulating* fake (the same idea as
``test_tenancy_across_guilds``): guild-scoped rows are visible only while the
acting GUC names their guild, a user's own role rows also once
``app.current_user`` is set, and ``team_listings`` — global by design — always.
So a test here fails if the service forgets to reach the team's server, or reads
the wrong one. The real policies are exercised in ``test_integration_pg.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from lol_schemas.enums import TeamMemberRole, TeamMemberStatus, UserGuildRoleName

from app.auth.deps import Principal
from app.models import (
    Guild,
    RiotAccount,
    Team,
    TeamListing,
    TeamMember,
    User,
    UserGuildRole,
)
from app.notifications.events import NotificationKind
from app.notifications.service import RecordingNotificationPublisher
from app.team_listings import service
from app.teams import router as teams_router
from app.teams import service as teams
from tests._fake_session import FakeSession, _Result

#: Server A hosts the team; server B is where the stranger plays.
_A, _B = uuid.uuid4(), uuid.uuid4()
_CAPTAIN, _STRANGER = uuid.uuid4(), uuid.uuid4()


class _RlsSession(FakeSession):
    """The shared fake plus the two policies, and ``team_listings`` left global."""

    def __init__(self) -> None:
        super().__init__()
        self.current_user: str | None = None

    def _execute_text(self, statement: Any, params: Any) -> Any:
        sql = str(statement).lower()
        if "set_config" in sql and params and "uid" in params:
            self.current_user = str(params["uid"])
            return _Result([])
        return super()._execute_text(statement, params)

    def _execute_select(self, statement: Any) -> Any:
        full = self.store
        self.store = {
            model: [row for row in rows if self._visible(model, row)]
            for model, rows in full.items()
        }
        try:
            return super()._execute_select(statement)
        finally:
            self.store = full

    def _visible(self, model: type, row: Any) -> bool:
        if model is TeamListing:  # cross-tenant by design — no guild policy
            return True
        guild_id = getattr(row, "guild_id", None)
        if guild_id is None:  # users, riot_accounts, guilds
            return True
        if self.current_guild is not None and str(guild_id) == self.current_guild:
            return True
        return (
            model is UserGuildRole
            and self.current_user is not None
            and str(row.user_id) == self.current_user
        )


def _account(user_id: uuid.UUID, region: str = "euw1", tier: str | None = "GOLD") -> RiotAccount:
    return RiotAccount(
        id=uuid.uuid4(),
        user_id=user_id,
        puuid=f"p-{uuid.uuid4()}",
        region=region,
        verified=True,
        rank_tier=tier,
        rank_division="II" if tier else None,
        rank_lp=45 if tier else None,
    )


def _world(*, acting: uuid.UUID = _A, extra_active: int = 2) -> tuple[_RlsSession, Team]:
    """Server A with a team (captain + ``extra_active`` players); the stranger on B."""
    s = _RlsSession()
    s.store[Guild].extend(
        [
            Guild(id=_A, discord_guild_id=1, name="Server A"),
            Guild(id=_B, discord_guild_id=2, name="Server B"),
        ]
    )
    s.store[User].extend(
        [
            User(id=_CAPTAIN, discord_user_id=11, display_name="Cap"),
            User(id=_STRANGER, discord_user_id=22, display_name="Stranger"),
        ]
    )
    s.store[UserGuildRole].extend(
        [
            UserGuildRole(id=uuid.uuid4(), user_id=_CAPTAIN, guild_id=_A, role="player"),
            UserGuildRole(id=uuid.uuid4(), user_id=_STRANGER, guild_id=_B, role="player"),
        ]
    )
    s.store[RiotAccount].extend([_account(_CAPTAIN), _account(_STRANGER, tier=None)])
    team = Team(
        id=uuid.uuid4(),
        guild_id=_A,
        name="Blue Wolves",
        captain_user_id=_CAPTAIN,
        join_code="WOLF2345",
        is_solo=False,
    )
    s.store[Team].append(team)
    s.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=_CAPTAIN,
            guild_id=_A,
            role=TeamMemberRole.CAPTAIN,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    for i in range(extra_active):
        uid = uuid.uuid4()
        s.store[User].append(User(id=uid, discord_user_id=100 + i, display_name=f"P{i}"))
        s.store[TeamMember].append(
            TeamMember(
                team_id=team.id,
                user_id=uid,
                guild_id=_A,
                role=TeamMemberRole.PLAYER,
                status=TeamMemberStatus.ACTIVE,
            )
        )
    s.current_guild = str(acting)
    return s, team


async def _post(s: _RlsSession, team: Team, **kw: Any) -> TeamListing:
    s.current_guild = str(team.guild_id)  # the client sends the team's X-Guild-Id
    return await service.create_listing(
        s,
        team_id=team.id,
        user_id=kw.get("user_id", _CAPTAIN),
        region=kw.get("region", "euw1"),
        message=kw.get("message", "need a support"),
    )


# ---------------------------------------------------------------------------
# Post
# ---------------------------------------------------------------------------
async def test_captain_posts_the_team() -> None:
    s, team = _world()
    listing = await _post(s, team, region="EUW1", message="  need a   support  ")

    assert listing.team_id == team.id
    assert listing.guild_id == _A
    assert listing.region == "euw1"
    assert listing.message == "need a support"
    assert s.store[TeamListing] == [listing]


async def test_only_one_post_per_team() -> None:
    s, team = _world()
    await _post(s, team)
    with pytest.raises(service.ListingExistsError):
        await _post(s, team)


async def test_a_non_captain_cannot_post() -> None:
    s, team = _world()
    with pytest.raises(service.NotTeamCaptainError):
        await _post(s, team, user_id=_STRANGER)


async def test_posting_needs_a_verified_account_on_that_server() -> None:
    s, team = _world()
    with pytest.raises(service.RegionAccountRequiredError) as err:
        await _post(s, team, region="eun1")
    assert err.value.region_label == "EUNE"


async def test_a_full_team_cannot_post() -> None:
    s, team = _world(extra_active=4)
    with pytest.raises(teams.TeamFullError):
        await _post(s, team)


@pytest.mark.parametrize("region", ["kr", "mars1"])
async def test_unsupported_regions_are_rejected(region: str) -> None:
    s, team = _world()
    with pytest.raises(service.InvalidRegionError):
        await _post(s, team, region=region)


async def test_an_overlong_message_is_rejected() -> None:
    s, team = _world()
    with pytest.raises(service.ListingError):
        await _post(s, team, message="x" * (service.MAX_MESSAGE_LENGTH + 1))


async def test_a_solo_team_cannot_be_posted() -> None:
    s, team = _world()
    team.is_solo = True
    with pytest.raises(teams.TeamNotFoundError):
        await _post(s, team)


# ---------------------------------------------------------------------------
# The list, seen from another server
# ---------------------------------------------------------------------------
async def test_a_player_on_another_server_sees_the_team() -> None:
    s, team = _world()
    await _post(s, team)
    s.current_guild = str(_B)  # the stranger's client acts on their own server

    views = await service.list_listings(s, viewer_id=_STRANGER, is_platform_operator=False)

    assert len(views) == 1
    v = views[0]
    assert v.team_name == "Blue Wolves"
    assert v.server_name == "Server A"
    assert v.member_count == 3
    assert v.members[0].display_name == "Cap"  # captain first
    assert v.members[0].role == TeamMemberRole.CAPTAIN
    assert v.members[0].rank_text == "Gold II, 45 LP"
    assert v.viewer_status is None
    assert v.can_remove is False
    # The walk to server A is undone: the caller's transaction ends on B.
    assert s.current_guild == str(_B)


async def test_the_region_filter() -> None:
    s, team = _world()
    await _post(s, team)
    s.current_guild = str(_B)

    assert (
        await service.list_listings(
            s, viewer_id=_STRANGER, is_platform_operator=False, region="eun1"
        )
        == []
    )
    assert (
        len(
            await service.list_listings(
                s, viewer_id=_STRANGER, is_platform_operator=False, region="euw1"
            )
        )
        == 1
    )


async def test_the_captain_and_an_operator_may_remove_it() -> None:
    s, team = _world()
    await _post(s, team)
    mine = await service.list_listings(s, viewer_id=_CAPTAIN, is_platform_operator=False)
    staff = await service.list_listings(s, viewer_id=_STRANGER, is_platform_operator=True)

    assert mine[0].viewer_status == "captain"
    assert mine[0].can_remove is True
    assert staff[0].can_remove is True


async def test_a_full_team_drops_off_the_list() -> None:
    s, team = _world(extra_active=3)
    await _post(s, team)
    s.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=uuid.uuid4(),
            guild_id=_A,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    assert await service.list_listings(s, viewer_id=_STRANGER, is_platform_operator=False) == []


async def test_newest_first() -> None:
    s, older = _world()
    await _post(s, older)
    s.store[TeamListing][0].created_at = datetime.now(UTC) - timedelta(hours=1)
    newer = Team(
        id=uuid.uuid4(),
        guild_id=_A,
        name="Red Foxes",
        captain_user_id=_CAPTAIN,
        join_code="FOXX2345",
        is_solo=False,
    )
    s.store[Team].append(newer)
    s.store[TeamMember].append(
        TeamMember(
            team_id=newer.id,
            user_id=_CAPTAIN,
            guild_id=_A,
            role=TeamMemberRole.CAPTAIN,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    await _post(s, newer)
    s.store[TeamListing][1].created_at = datetime.now(UTC)

    views = await service.list_listings(s, viewer_id=_STRANGER, is_platform_operator=False)
    assert [v.team_name for v in views] == ["Red Foxes", "Blue Wolves"]


# ---------------------------------------------------------------------------
# Ask to join, from another server
# ---------------------------------------------------------------------------
async def test_request_from_another_server_lands_pending_on_the_teams_server() -> None:
    s, team = _world()
    listing = await _post(s, team)
    s.current_guild = str(_B)

    result = await service.request_to_join(s, listing_id=listing.id, user_id=_STRANGER)

    assert result.team.id == team.id
    assert result.member.status == TeamMemberStatus.PENDING
    assert result.member.guild_id == _A
    # The stranger now holds a baseline player role on A — what lets their client
    # address the team (roster, leave) there afterwards.
    roles = [r for r in s.store[UserGuildRole] if r.user_id == _STRANGER and r.guild_id == _A]
    assert [r.role for r in roles] == [UserGuildRoleName.PLAYER]
    # …and the captain's queue shows the request.
    s.current_guild = str(_A)
    queue = [m for m in s.store[TeamMember] if m.team_id == team.id and m.status == "pending"]
    assert [m.user_id for m in queue] == [_STRANGER]


async def test_requesting_twice_is_idempotent() -> None:
    s, team = _world()
    listing = await _post(s, team)
    s.current_guild = str(_B)
    first = await service.request_to_join(s, listing_id=listing.id, user_id=_STRANGER)
    s.current_guild = str(_B)
    second = await service.request_to_join(s, listing_id=listing.id, user_id=_STRANGER)

    assert first.member is second.member
    assert len([r for r in s.store[UserGuildRole] if r.user_id == _STRANGER]) == 2  # B + A


async def test_the_viewer_sees_their_pending_request() -> None:
    s, team = _world()
    listing = await _post(s, team)
    s.current_guild = str(_B)
    await service.request_to_join(s, listing_id=listing.id, user_id=_STRANGER)
    s.current_guild = str(_B)

    views = await service.list_listings(s, viewer_id=_STRANGER, is_platform_operator=False)
    assert views[0].viewer_status == "pending"
    assert views[0].member_count == 3  # a pending request is not a member yet


async def test_requesting_needs_an_account_on_that_server() -> None:
    s, team = _world()
    listing = await _post(s, team)
    s.store[RiotAccount] = [a for a in s.store[RiotAccount] if a.user_id != _STRANGER]
    s.current_guild = str(_B)

    with pytest.raises(service.RegionAccountRequiredError):
        await service.request_to_join(s, listing_id=listing.id, user_id=_STRANGER)
    assert not [m for m in s.store[TeamMember] if m.user_id == _STRANGER]


async def test_requesting_a_full_team_is_refused() -> None:
    s, team = _world(extra_active=3)
    listing = await _post(s, team)
    s.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=uuid.uuid4(),
            guild_id=_A,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    s.current_guild = str(_B)
    with pytest.raises(teams.TeamFullError):
        await service.request_to_join(s, listing_id=listing.id, user_id=_STRANGER)


async def test_an_unknown_listing() -> None:
    s, _ = _world()
    with pytest.raises(service.ListingNotFoundError):
        await service.request_to_join(s, listing_id=uuid.uuid4(), user_id=_STRANGER)


# ---------------------------------------------------------------------------
# Take down
# ---------------------------------------------------------------------------
async def test_the_captain_deletes_from_any_server() -> None:
    s, team = _world()
    listing = await _post(s, team)
    s.current_guild = str(_B)  # wherever the captain's client is acting

    await service.delete_listing(
        s, listing_id=listing.id, user_id=_CAPTAIN, is_platform_operator=False
    )
    assert s.store[TeamListing] == []
    assert s.current_guild == str(_B)


async def test_someone_else_cannot_delete() -> None:
    s, team = _world()
    listing = await _post(s, team)
    with pytest.raises(service.NotTeamCaptainError):
        await service.delete_listing(
            s, listing_id=listing.id, user_id=_STRANGER, is_platform_operator=False
        )
    assert s.store[TeamListing] == [listing]


async def test_an_operator_deletes_any_listing() -> None:
    s, team = _world()
    listing = await _post(s, team)
    await service.delete_listing(
        s, listing_id=listing.id, user_id=_STRANGER, is_platform_operator=True
    )
    assert s.store[TeamListing] == []


# ---------------------------------------------------------------------------
# A full roster takes the post down by itself
# ---------------------------------------------------------------------------
async def _pending(s: _RlsSession, team: Team, user_id: uuid.UUID) -> None:
    s.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=user_id,
            guild_id=_A,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.PENDING,
        )
    )


async def test_approving_the_fifth_player_removes_the_post() -> None:
    s, team = _world(extra_active=3)  # 4 active
    await _post(s, team)
    await _pending(s, team, _STRANGER)

    await teams.approve_join_request(s, team_id=team.id, user_id=_STRANGER)

    assert s.store[TeamListing] == []


async def test_approving_the_fourth_player_keeps_the_post() -> None:
    s, team = _world(extra_active=2)  # 3 active
    await _post(s, team)
    await _pending(s, team, _STRANGER)

    await teams.approve_join_request(s, team_id=team.id, user_id=_STRANGER)

    assert len(s.store[TeamListing]) == 1


async def test_approval_dms_the_player() -> None:
    s, team = _world()
    await _pending(s, team, _STRANGER)
    publisher = RecordingNotificationPublisher()
    principal = Principal(user_id=str(_CAPTAIN), guild_id=str(_A), roles=("player",))

    await teams_router.approve_join_request(
        team_id=team.id,
        user_id=_STRANGER,
        principal=principal,
        session=s,  # type: ignore[arg-type]
        publisher=publisher,
    )

    assert len(publisher.published) == 1
    envelope = publisher.published[0]
    assert envelope.kind == NotificationKind.TEAM_JOIN_APPROVED
    assert envelope.origin_guild_id == str(_A)  # the TEAM's server — the invite's
    payload = envelope.payload.model_dump()
    assert payload["discord_user_id"] == "22"
    assert payload["team_name"] == "Blue Wolves"
    assert payload["server_name"] == "Server A"


async def test_a_failing_dm_does_not_fail_the_approval() -> None:
    s, team = _world()
    await _pending(s, team, _STRANGER)

    class _Down(RecordingNotificationPublisher):
        async def publish(self, envelope: Any) -> None:
            raise ConnectionError("redis down")

    principal = Principal(user_id=str(_CAPTAIN), guild_id=str(_A), roles=("player",))
    member = await teams_router.approve_join_request(
        team_id=team.id,
        user_id=_STRANGER,
        principal=principal,
        session=s,  # type: ignore[arg-type]
        publisher=_Down(),
    )
    assert member.user_id == str(_STRANGER)
