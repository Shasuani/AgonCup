"""Account-V1 linking + ownership-verification service (PLAN.md §3.1 / §3.5).

This is the request-path half of account linking. The worker owns the polling
job (``app.main.account_verification_poll``); here we:

* Resolve a Riot ID (``gameName`` + ``tagLine``) to its **canonical, immutable
  PUUID** via Account-V1 ``by-riot-id`` (the only Riot Web API in v3, PLAN.md
  §3.5). PUUID is the identity key; ``gameName``/``tagLine`` are stale,
  refreshable, NON-identity display data (PLAN.md §2).
* Create (or reuse) the global :class:`~app.models.RiotAccount` row for that
  PUUID, owned by the linking user, ``verified=False`` until the ownership
  challenge passes.
* Open a :class:`~app.models.VerificationAttempt`: a single-use ``nonce`` bound
  to ``(user_id, puuid)`` mapped to a random **target profile-icon id** with a
  short hard ``expires_at`` TTL. The user proves ownership by setting that icon
  on the linked account; the worker polls Summoner-V4 ``by-puuid`` and flips
  ``verified`` on a match (PLAN.md §3.1).

Trust note (PLAN.md §3.1): the LCU ``current-summoner`` PUUID is only an
*advisory* secondary signal — the icon nonce against Summoner-V4 is the
verification authority here. ``verified=True`` is **not** permanent; re-linking
re-opens an attempt.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from lol_riot import AccountDTO, NotFound, RiotAccountClient, RiotApiError
from lol_riot.routing import ContinentalRoute, PlatformRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import RiotAccount, VerificationAttempt

log = get_logger("app.accounts")

# Ownership-challenge tuning (PLAN.md §3.1 — short-TTL, single-use nonce).
#: Hard expiry for an attempt: covers the user manually changing their profile
#: icon plus the worker's bounded poll/backoff window.
VERIFICATION_TTL = timedelta(minutes=15)
#: Inclusive range of valid League ``profileIconId`` values to pick the random
#: target from. Base icons are dense from 0 upward; this conservative window
#: stays well inside the always-available default set so any account can set it.
_ICON_ID_MIN = 0
_ICON_ID_MAX = 28
#: Opaque nonce length (bytes of entropy, hex-encoded → 2x chars).
_NONCE_BYTES = 32

#: Attempt lifecycle states (mirror the ``verification_attempts.status`` CHECK).
STATUS_PENDING = "pending"
STATUS_VERIFIED = "verified"
STATUS_EXPIRED = "expired"
STATUS_FAILED = "failed"

#: Platform region (``na1``/``euw1``/...) → Account-V1 continental cluster.
#: Account-V1 is continental, Summoner-V4 is platform (PLAN.md §3.5): we store
#: the player's platform ``region`` and derive the continent for the by-riot-id
#: resolve from it.
_PLATFORM_TO_CONTINENT: dict[PlatformRoute, ContinentalRoute] = {
    PlatformRoute.NA1: ContinentalRoute.AMERICAS,
    PlatformRoute.BR1: ContinentalRoute.AMERICAS,
    PlatformRoute.LA1: ContinentalRoute.AMERICAS,
    PlatformRoute.LA2: ContinentalRoute.AMERICAS,
    PlatformRoute.OC1: ContinentalRoute.AMERICAS,
    PlatformRoute.EUW1: ContinentalRoute.EUROPE,
    PlatformRoute.EUN1: ContinentalRoute.EUROPE,
    PlatformRoute.TR1: ContinentalRoute.EUROPE,
    PlatformRoute.RU: ContinentalRoute.EUROPE,
    PlatformRoute.KR: ContinentalRoute.ASIA,
    PlatformRoute.JP1: ContinentalRoute.ASIA,
}


class AccountLinkError(Exception):
    """Base class for account-linking failures (mapped to HTTP by the router)."""


class UnknownRiotId(AccountLinkError):
    """The supplied Riot ID does not resolve to a PUUID (Account-V1 404)."""


class InvalidRegion(AccountLinkError):
    """The supplied region is not a recognised Riot platform route."""


class RiotUnavailable(AccountLinkError):
    """Account-V1 was unreachable / rate-limited / errored (transient upstream)."""


@dataclass(frozen=True, slots=True)
class LinkResult:
    """Outcome of a link request returned to the caller (PLAN.md §3.1).

    Carries the resolved canonical PUUID, the (un)verified account, and the
    ownership challenge the user must satisfy — the ``target_icon_id`` they must
    set on their League profile plus the ``attempt_id`` to poll for status.
    """

    account_id: str
    puuid: str
    verified: bool
    attempt_id: str
    target_icon_id: int
    expires_at: datetime
    game_name: str
    tag_line: str


def parse_platform_region(region: str) -> PlatformRoute:
    """Validate a user-supplied region string → :class:`PlatformRoute`.

    Raises :class:`InvalidRegion` for anything outside the known platform set so
    the router can answer 422 before any Riot call (PLAN.md §3.5 routing).
    """
    try:
        return PlatformRoute(region.lower())
    except ValueError as exc:
        raise InvalidRegion(f"unknown region: {region!r}") from exc


def continent_for(region: PlatformRoute) -> ContinentalRoute:
    """Map a platform region to its Account-V1 continental cluster (PLAN.md §3.5)."""
    return _PLATFORM_TO_CONTINENT.get(region, ContinentalRoute.AMERICAS)


def _random_target_icon_id() -> int:
    """Pick a random target ``profileIconId`` for the ownership challenge."""
    return secrets.randbelow(_ICON_ID_MAX - _ICON_ID_MIN + 1) + _ICON_ID_MIN


def _new_nonce() -> str:
    """Generate an opaque, single-use challenge nonce."""
    return secrets.token_hex(_NONCE_BYTES)


async def resolve_account(
    client: RiotAccountClient, *, game_name: str, tag_line: str
) -> AccountDTO:
    """Resolve a Riot ID to its canonical PUUID via Account-V1 (PLAN.md §3.5).

    Maps the typed ``lol-riot`` errors to the account-linking error hierarchy:
    a 404 is the expected "unknown Riot ID" user outcome; everything else is a
    transient upstream failure the caller should surface as a 502.
    """
    try:
        return await client.get_account_by_riot_id(game_name, tag_line)
    except NotFound as exc:
        raise UnknownRiotId(f"no Riot account for {game_name}#{tag_line}") from exc
    except RiotApiError as exc:
        raise RiotUnavailable(str(exc)) from exc


async def link_account(
    session: AsyncSession,
    client: RiotAccountClient,
    *,
    user_id: str,
    game_name: str,
    tag_line: str,
    region: str,
    now: datetime | None = None,
) -> LinkResult:
    """Link a Riot account to ``user_id`` and open an ownership challenge.

    Flow (PLAN.md §3.1 "Account verification", §3.5):

    1. Validate the region, resolve the Riot ID → canonical PUUID (Account-V1).
    2. Upsert the global :class:`RiotAccount` for that PUUID (PUUID is the
       identity key; ``gameName``/``tagLine``/``region`` are refreshable display
       data only and are refreshed on every (re)link).
    3. Create a :class:`VerificationAttempt` with a random ``target_icon_id``,
       single-use ``nonce``, and a hard ``expires_at`` TTL; the account is set
       (back) to ``verified=False`` for the duration of the challenge.

    The caller (router) is responsible for enqueuing the worker poll
    (``account_verification_poll``) for the returned ``attempt_id``.

    Runs inside the caller's transaction; the caller commits.
    """
    now = now or datetime.now(tz=UTC)
    platform = parse_platform_region(region)

    # Resolve PUUID first — fail fast before mutating any state.
    dto = await resolve_account(client, game_name=game_name, tag_line=tag_line)

    account = (
        await session.execute(select(RiotAccount).where(RiotAccount.puuid == dto.puuid))
    ).scalar_one_or_none()

    if account is None:
        account = RiotAccount(
            user_id=user_id,
            puuid=dto.puuid,
            riot_id_game_name=dto.game_name,
            riot_id_tag_line=dto.tag_line,
            region=platform.value,
            verified=False,
        )
        session.add(account)
        await session.flush()
    else:
        # PUUID is canonical and immutable; only display/region data is refreshed.
        # Re-linking re-opens the challenge, so ``verified`` is NOT permanent.
        account.user_id = uuid.UUID(user_id)
        account.riot_id_game_name = dto.game_name
        account.riot_id_tag_line = dto.tag_line
        account.region = platform.value
        account.verified = False
        account.verified_at = None

    target_icon_id = _random_target_icon_id()
    attempt = VerificationAttempt(
        user_id=user_id,
        puuid=dto.puuid,
        nonce=_new_nonce(),
        target_icon_id=target_icon_id,
        status=STATUS_PENDING,
        expires_at=now + VERIFICATION_TTL,
    )
    session.add(attempt)
    await session.flush()

    log.info(
        "account_link_attempt_created",
        user_id=user_id,
        puuid=dto.puuid,
        attempt_id=str(attempt.id),
        target_icon_id=target_icon_id,
        region=platform.value,
    )

    return LinkResult(
        account_id=str(account.id),
        puuid=dto.puuid,
        verified=False,
        attempt_id=str(attempt.id),
        target_icon_id=target_icon_id,
        expires_at=attempt.expires_at,
        game_name=dto.game_name,
        tag_line=dto.tag_line,
    )


async def get_attempt(
    session: AsyncSession, *, attempt_id: str, user_id: str
) -> VerificationAttempt | None:
    """Load a verification attempt owned by ``user_id`` (status reporting).

    Scopes the lookup to the requesting user so one principal can never poll
    another's challenge — ``RiotAccount``/``VerificationAttempt`` are global
    (not guild-RLS-scoped), so ownership is enforced in the query (PLAN.md §2).
    """
    return (
        await session.execute(
            select(VerificationAttempt).where(
                VerificationAttempt.id == attempt_id,
                VerificationAttempt.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


__all__ = [
    "STATUS_EXPIRED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_VERIFIED",
    "VERIFICATION_TTL",
    "AccountLinkError",
    "InvalidRegion",
    "LinkResult",
    "RiotUnavailable",
    "UnknownRiotId",
    "continent_for",
    "get_attempt",
    "link_account",
    "parse_platform_region",
    "resolve_account",
]
