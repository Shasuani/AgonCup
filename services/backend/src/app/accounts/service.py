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

from lol_riot import AccountDTO, LeagueEntryDTO, NotFound, RiotAccountClient, RiotApiError
from lol_riot.routing import ContinentalRoute, PlatformRoute
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Guild, RiotAccount, User, VerificationAttempt

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

# Desktop-code flow tuning (SHARED CONTRACT codeFormat). The TTL reuses
# ``VERIFICATION_TTL`` verbatim — no new constant.
#: Crockford-base32 alphabet MINUS ambiguous glyphs (no I, L, O, 0, 1). 31
#: symbols x 8 chars ≈ 39.6 bits — ample for a single-use, 15-min credential.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
#: Code length (characters, pre-grouping).
_CODE_LEN = 8
#: How many fresh codes to try before giving up on a collision (defensive).
_CODE_MINT_RETRIES = 5

#: Verification flavour (mirrors ``verification_attempts.verify_kind`` CHECK).
VERIFY_KIND_ICON = "icon"
VERIFY_KIND_DESKTOP_CODE = "desktop_code"

#: League-V4 queue type for the Solo/Duo ranked ladder (the rank we snapshot).
RANKED_SOLO_QUEUE = "RANKED_SOLO_5x5"

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

#: Platform route → human display label for notifications/embeds (e.g. the DM
#: "Linked: Chama#Shasu (EUNE) ...""). Falls back to the upper-cased route value
#: for any platform not explicitly mapped.
_PLATFORM_TO_LABEL: dict[PlatformRoute, str] = {
    PlatformRoute.NA1: "NA",
    PlatformRoute.BR1: "BR",
    PlatformRoute.LA1: "LAN",
    PlatformRoute.LA2: "LAS",
    PlatformRoute.OC1: "OCE",
    PlatformRoute.EUW1: "EUW",
    PlatformRoute.EUN1: "EUNE",
    PlatformRoute.TR1: "TR",
    PlatformRoute.RU: "RU",
    PlatformRoute.KR: "KR",
    PlatformRoute.JP1: "JP",
}


class AccountLinkError(Exception):
    """Base class for account-linking failures (mapped to HTTP by the router)."""


class UnknownRiotId(AccountLinkError):
    """The supplied Riot ID does not resolve to a PUUID (Account-V1 404)."""


class InvalidRegion(AccountLinkError):
    """The supplied region is not a recognised Riot platform route."""


class RiotUnavailable(AccountLinkError):
    """Account-V1 was unreachable / rate-limited / errored (transient upstream)."""


class CodeMintError(AccountLinkError):
    """Could not mint a unique one-time code after the bounded retry loop (500)."""


class UnknownOrExpiredCode(AccountLinkError):
    """The submitted desktop code is unknown, expired, or already consumed (404)."""


class PuuidConflict(AccountLinkError):
    """The claimed PUUID is already linked to a DIFFERENT user (409)."""


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


@dataclass(frozen=True, slots=True)
class LinkedAccountSummary:
    """One row of the admin player roster (PLAN.md §3.4).

    Joins a ``RiotAccount`` to its owning ``User`` so the admin SPA can list who
    has linked, with the canonical Riot ID, region, verification state, and the
    owner's Discord display name. ``RiotAccount`` is global (not guild-scoped), so
    the roster is platform-wide and gated to staff at the route.
    """

    account_id: str
    user_id: str
    discord_name: str
    game_name: str | None
    tag_line: str | None
    region: str | None
    verified: bool
    verified_at: datetime | None
    # The Discord server the user ran /register from (origin_guild_id → name).
    origin_guild_id: str | None
    origin_guild_name: str | None


@dataclass(frozen=True, slots=True)
class DesktopRegistrationResult:
    """Outcome of minting a one-time desktop-link code at ``/register``.

    ``code`` is the grouped (``XXXX-XXXX``) DISPLAY form returned to the bot; the
    DB stores it de-hyphenated. The desktop completes the link by POSTing the code
    plus the LCU PUUID to the public verify endpoint.
    """

    attempt_id: str
    code: str
    expires_at: datetime
    origin_guild_id: str | None


@dataclass(frozen=True, slots=True)
class DesktopVerifyResult:
    """Outcome of the desktop completing a code link (account now verified).

    Carries the resolved canonical Riot ID / region, a best-effort Solo/Duo rank
    snapshot (``None`` when unranked or the rank fetch failed), and the
    ``discord_user_id`` the router needs to publish the DM notification.
    """

    account_id: str
    puuid: str
    verified: bool
    origin_guild_id: str | None
    game_name: str
    tag_line: str
    region: str
    tier: str | None
    division: str | None
    league_points: int | None
    rank_text: str
    discord_user_id: str


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


def region_label_for(region: PlatformRoute) -> str:
    """Map a platform route to a human display label (e.g. ``eun1`` -> ``EUNE``)."""
    return _PLATFORM_TO_LABEL.get(region, region.value.upper())


def format_rank(solo: LeagueEntryDTO | None) -> str:
    """Render a Solo/Duo :class:`LeagueEntryDTO` as a display string (PLAN.md §3.5).

    ``None`` (unranked / rank fetch failed) -> ``"Unranked"``. Apex tiers
    (MASTER and above) carry an empty division, so the division is omitted there.
    Riot returns the tier upper-cased (``"GOLD"``); we title-case it (``"Gold"``).
    """
    if solo is None:
        return "Unranked"
    tier = solo.tier.title()
    if solo.rank:
        return f"{tier} {solo.rank}, {solo.league_points} LP"
    return f"{tier}, {solo.league_points} LP"


def _random_target_icon_id() -> int:
    """Pick a random target ``profileIconId`` for the ownership challenge."""
    return secrets.randbelow(_ICON_ID_MAX - _ICON_ID_MIN + 1) + _ICON_ID_MIN


def _new_nonce() -> str:
    """Generate an opaque, single-use challenge nonce."""
    return secrets.token_hex(_NONCE_BYTES)


def _new_code() -> str:
    """Generate a cryptographically-secure one-time desktop-link code.

    ``_CODE_LEN`` characters over the ambiguity-free Crockford-base32 alphabet.
    Stored/compared de-hyphenated; rendered grouped via :func:`_group_code`.
    """
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


def normalize_code(code: str) -> str:
    """Normalise a user-typed code: strip, drop hyphens, uppercase.

    The desktop form may submit the grouped ``XXXX-XXXX`` display form (or with
    stray whitespace); lookup is always on the canonical de-hyphenated form.
    """
    return code.strip().replace("-", "").upper()


def _group_code(code: str) -> str:
    """Render a stored code in the grouped ``XXXX-XXXX`` display form."""
    half = _CODE_LEN // 2
    return f"{code[:half]}-{code[half:]}"


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


async def start_desktop_registration(
    session: AsyncSession,
    *,
    user_id: str,
    origin_guild_id: str | None,
    now: datetime | None = None,
) -> DesktopRegistrationResult:
    """Mint a one-time desktop-link code for ``user_id`` (SHARED CONTRACT).

    Flow (PLAN.md §3.1, replacing the icon challenge):

    1. SUPERSEDE any prior *pending* desktop-code attempt for
       ``(user_id, origin_guild_id)`` — mark it ``expired`` so only one live code
       exists per (user, guild) and the partial-unique active-code index stays
       satisfied.
    2. Mint a code via :func:`_new_code` and insert a ``desktop_code``
       :class:`VerificationAttempt` (``puuid``/``nonce``/``target_icon_id`` all
       NULL — they are unknown / unused for this flow) with the standard
       ``VERIFICATION_TTL``. Retry on the (near-impossible) unique-index collision.

    GLOBAL: ``verification_attempts`` is not guild-scoped, so this runs on a plain
    session (no tenant GUC). ``origin_guild_id`` is just a recorded FK value.

    Runs inside the caller's transaction; the caller commits.
    """
    now = now or datetime.now(tz=UTC)
    guild_uuid = uuid.UUID(origin_guild_id) if origin_guild_id else None

    # 1) Supersede prior live codes for this (user, guild) so only one is active.
    existing = (
        (
            await session.execute(
                select(VerificationAttempt).where(
                    VerificationAttempt.user_id == user_id,
                    VerificationAttempt.origin_guild_id == guild_uuid,
                    VerificationAttempt.verify_kind == VERIFY_KIND_DESKTOP_CODE,
                    VerificationAttempt.status == STATUS_PENDING,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in existing:
        row.status = STATUS_EXPIRED

    # 2) Mint + insert with a bounded collision-retry loop (defensive).
    last_error: Exception | None = None
    for _ in range(_CODE_MINT_RETRIES):
        code = _new_code()
        attempt = VerificationAttempt(
            user_id=user_id,
            code=code,
            origin_guild_id=guild_uuid,
            verify_kind=VERIFY_KIND_DESKTOP_CODE,
            status=STATUS_PENDING,
            puuid=None,
            nonce=None,
            target_icon_id=None,
            expires_at=now + VERIFICATION_TTL,
        )
        try:
            # SAVEPOINT: roll back ONLY the failed INSERT on a collision, not the
            # outer transaction (which holds the supersede UPDATEs and is owned by
            # the caller's ``session.begin()``). A full ``session.rollback()`` here
            # would discard the supersede and break the caller's context manager.
            async with session.begin_nested():
                session.add(attempt)
                await session.flush()
        except IntegrityError as exc:  # active-code unique-index collision
            last_error = exc
            continue

        log.info(
            "desktop_registration_code_minted",
            user_id=user_id,
            attempt_id=str(attempt.id),
            origin_guild_id=origin_guild_id,
        )
        return DesktopRegistrationResult(
            attempt_id=str(attempt.id),
            code=_group_code(code),
            expires_at=attempt.expires_at,
            origin_guild_id=str(guild_uuid) if guild_uuid else None,
        )

    raise CodeMintError("could not mint a unique code") from last_error


async def _fetch_solo_rank(
    account_client: RiotAccountClient, puuid: str, platform: PlatformRoute
) -> LeagueEntryDTO | None:
    """Best-effort Solo/Duo rank for ``puuid`` (League-V4). NEVER raises.

    The rank snapshot must not be able to fail the link (PLAN.md §3.5): any Riot
    error (404 / rate-limit / transport / parse) is logged and treated as "rank
    unknown" (returns ``None``). The typed errors stay inside ``lol_riot`` for
    limiter/retry uniformity; the best-effort policy lives here, in the backend.
    """
    try:
        entries = await account_client.get_league_entries_by_puuid(puuid, platform)
    except RiotApiError as exc:
        log.warning("desktop_rank_fetch_failed", puuid=puuid, error=str(exc))
        return None
    except Exception as exc:  # defence-in-depth: never let rank break the link
        log.warning("desktop_rank_fetch_failed", puuid=puuid, error=str(exc))
        return None
    return next((e for e in entries if e.queue_type == RANKED_SOLO_QUEUE), None)


async def verify_desktop_code(
    session: AsyncSession,
    *,
    code: str,
    game_name: str,
    tag_line: str,
    region: str,
    account_client: RiotAccountClient,
    lcu_puuid: str | None = None,
    summoner_id: str | None = None,
    now: datetime | None = None,
) -> DesktopVerifyResult:
    """Complete a desktop code link: resolve the CANONICAL PUUID + dedupe-link it.

    DOUBLE VERIFICATION (SHARED CONTRACT securityNote / PLAN.md §3.1): the ``code``
    proves the submitter controls the Discord registration; resolving the Riot ID
    (``game_name``/``tag_line``) to its canonical 78-char PUUID via Account-V1
    by-riot-id proves the Riot ID is REAL (a 404 rejects the link). The desktop no
    longer sends the canonical PUUID — the backend resolves it server-side, which
    also fixes the duplicate-row bug (the LCU's advisory 36-char UUID is NOT the
    canonical identity). Residual gap (acceptable, PLAN §3.1 advisory model):
    resolving by Riot ID proves it exists, not that the submitter owns it; true
    ownership is backstopped by the LCU read + cross-client corroboration + admin
    override, never by this endpoint alone.

    Flow (THREE short transactions; the service OWNS its own ``session.begin()``
    blocks — the caller must NOT wrap this in a transaction so no Postgres row
    lock or open txn is ever held across the Riot HTTP calls):

    1. PHASE 1 (short txn, FOR UPDATE): look up the LIVE attempt for this code
       (pending, desktop_code, unexpired) ``FOR UPDATE`` only to confirm it is
       live and capture ``user_id``/``origin_guild_id``; commit immediately to
       RELEASE the row lock. None -> :class:`UnknownOrExpiredCode`.
    2. NO LOCK HELD: resolve the canonical PUUID via Account-V1 (404 ->
       :class:`UnknownRiotId`, other upstream error -> :class:`RiotUnavailable`)
       and fetch the best-effort Solo/Duo rank (League-V4). These can each block
       on a 429 ``Retry-After`` for up to ~2 min PER call; doing them here means
       a rate-limit storm never pins a connection/lock or exhausts the pool.
    3. PHASE 2 (short txn): re-validate the attempt is STILL pending+unexpired
       (it could have been consumed/expired during the HTTP window) and do the
       DEDUPE upsert on the canonical PUUID — refuse if it belongs to a DIFFERENT
       user (:class:`PuuidConflict`), else create/update the row in place (the
       old icon-flow row carrying the same canonical PUUID is found + updated,
       never duplicated). The INSERT path is wrapped in a SAVEPOINT +
       IntegrityError retry so a concurrent same-PUUID verify can't 500 the
       loser (it re-SELECTs and applies the same owner-equality check). Mark the
       attempt verified and stamp its canonical PUUID.
    """
    now = now or datetime.now(tz=UTC)
    normalized = normalize_code(code)
    platform = parse_platform_region(region)

    # PHASE 1: confirm the code is live under a brief FOR UPDATE lock, capture the
    # owner + guild, then COMMIT to release the lock BEFORE any Riot HTTP call.
    async with session.begin():
        attempt = (
            await session.execute(
                select(VerificationAttempt)
                .where(
                    VerificationAttempt.code == normalized,
                    VerificationAttempt.verify_kind == VERIFY_KIND_DESKTOP_CODE,
                    VerificationAttempt.status == STATUS_PENDING,
                    VerificationAttempt.expires_at > now,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise UnknownOrExpiredCode("invalid or expired code")
        origin_guild_id = attempt.origin_guild_id

    # NO LOCK / NO OPEN TXN HELD across these (potentially slow, 429-retrying)
    # Riot calls. The double verification: resolve the canonical PUUID from the
    # Riot ID. Then the best-effort rank snapshot — must NOT fail the link.
    dto = await resolve_account(account_client, game_name=game_name, tag_line=tag_line)
    canonical_puuid = dto.puuid
    solo = await _fetch_solo_rank(account_client, canonical_puuid, platform)
    rank_text = format_rank(solo)

    # PHASE 2: re-open a short txn, re-validate the attempt is still consumable,
    # then dedupe-upsert + mark verified (race-safe INSERT via SAVEPOINT retry).
    async with session.begin():
        attempt = (
            await session.execute(
                select(VerificationAttempt)
                .where(
                    VerificationAttempt.code == normalized,
                    VerificationAttempt.verify_kind == VERIFY_KIND_DESKTOP_CODE,
                    VerificationAttempt.status == STATUS_PENDING,
                    VerificationAttempt.expires_at > now,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if attempt is None:
            # Consumed/expired during the HTTP window (or a racing verify won).
            raise UnknownOrExpiredCode("invalid or expired code")

        account = await _dedupe_upsert_account(
            session,
            canonical_puuid=canonical_puuid,
            attempt_user_id=attempt.user_id,
            dto=dto,
            platform=platform,
            origin_guild_id=attempt.origin_guild_id,
            summoner_id=summoner_id,
            lcu_puuid=lcu_puuid,
            now=now,
        )

        if solo is not None:
            account.rank_tier = solo.tier
            account.rank_division = solo.rank or None
            account.rank_lp = solo.league_points
            account.rank_queue = solo.queue_type
            account.rank_checked_at = now

        attempt.puuid = canonical_puuid
        attempt.status = STATUS_VERIFIED

        # The DM notification needs the user's Discord snowflake (global User row).
        discord_user_id = (
            await session.execute(select(User.discord_user_id).where(User.id == attempt.user_id))
        ).scalar_one_or_none()

        account_id = str(account.id)
        log.info(
            "desktop_code_verified",
            user_id=str(attempt.user_id),
            attempt_id=str(attempt.id),
            account_id=account_id,
            origin_guild_id=str(origin_guild_id) if origin_guild_id else None,
            ranked=solo is not None,
        )

    return DesktopVerifyResult(
        account_id=account_id,
        puuid=canonical_puuid,
        verified=True,
        origin_guild_id=str(origin_guild_id) if origin_guild_id else None,
        game_name=dto.game_name,
        tag_line=dto.tag_line,
        region=platform.value,
        tier=(solo.tier if solo is not None else None),
        division=(solo.rank or None) if solo is not None else None,
        league_points=(solo.league_points if solo is not None else None),
        rank_text=rank_text,
        discord_user_id=str(discord_user_id) if discord_user_id is not None else "",
    )


async def _dedupe_upsert_account(
    session: AsyncSession,
    *,
    canonical_puuid: str,
    attempt_user_id: uuid.UUID,
    dto: AccountDTO,
    platform: PlatformRoute,
    origin_guild_id: uuid.UUID | None,
    summoner_id: str | None,
    lcu_puuid: str | None,
    now: datetime,
) -> RiotAccount:
    """Race-safe DEDUPE upsert of the :class:`RiotAccount` keyed on the canonical
    PUUID (PLAN.md §3.1 dedupe / SHARED CONTRACT securityNote).

    SELECT-then-INSERT on the UNIQUE ``puuid`` can race: two concurrent verifies
    for the same PUUID can both see ``None`` and both INSERT — the loser hits an
    :class:`IntegrityError`. We wrap the INSERT in a SAVEPOINT; on collision we
    re-SELECT the now-existing row and fall through to the update-in-place branch,
    re-applying the owner-equality :class:`PuuidConflict` guard. This keeps the
    "a PUUID belonging to a different user is rejected" guarantee under concurrency
    and never lets the loser surface a raw 500.
    """
    account = (
        await session.execute(select(RiotAccount).where(RiotAccount.puuid == canonical_puuid))
    ).scalar_one_or_none()

    if account is not None and str(account.user_id) != str(attempt_user_id):
        raise PuuidConflict("this Riot account is already linked to another user")

    if account is None:
        account = RiotAccount(
            user_id=attempt_user_id,
            puuid=canonical_puuid,
            summoner_id=summoner_id,
            riot_id_game_name=dto.game_name,
            riot_id_tag_line=dto.tag_line,
            region=platform.value,
            verified=True,
            verified_at=now,
            origin_guild_id=origin_guild_id,
            lcu_puuid=lcu_puuid,
        )
        try:
            # SAVEPOINT so a unique-puuid collision rolls back ONLY this INSERT,
            # not the outer txn (the attempt status flip / rank snapshot).
            async with session.begin_nested():
                session.add(account)
                await session.flush()
            return account
        except IntegrityError:
            # A concurrent verify inserted the same PUUID first — re-SELECT it and
            # fall through to the update-in-place branch (race-safe dedupe).
            account = (
                await session.execute(
                    select(RiotAccount).where(RiotAccount.puuid == canonical_puuid)
                )
            ).scalar_one_or_none()
            if account is None:  # pragma: no cover - integrity error implies a row
                raise
            if str(account.user_id) != str(attempt_user_id):
                raise PuuidConflict("this Riot account is already linked to another user") from None

    # DEDUPE: update the existing row in place (incl. the old icon-flow row
    # carrying the SAME canonical PUUID) — never create a second row.
    account.user_id = attempt_user_id
    account.riot_id_game_name = dto.game_name
    account.riot_id_tag_line = dto.tag_line
    account.region = platform.value
    account.verified = True
    account.verified_at = now
    account.origin_guild_id = origin_guild_id
    if summoner_id is not None:
        account.summoner_id = summoner_id
    if lcu_puuid is not None:
        account.lcu_puuid = lcu_puuid
    return account


async def list_linked_accounts(session: AsyncSession) -> list[LinkedAccountSummary]:
    """Return every linked ``RiotAccount`` joined to its owner (admin roster).

    Platform-wide: ``RiotAccount``/``User`` are global tables (not guild-RLS),
    so this returns all linked accounts regardless of acting guild — the route
    gates it to staff. Ordered by Discord display name then Riot ID for a stable,
    human-scannable list (PLAN.md §3.4).
    """
    rows = (
        await session.execute(
            select(RiotAccount, User.display_name, Guild.name)
            .join(User, RiotAccount.user_id == User.id)
            # LEFT join: origin_guild_id is nullable (SET NULL on guild delete), so
            # a linked account whose origin guild is gone still appears in the list.
            .outerjoin(Guild, RiotAccount.origin_guild_id == Guild.id)
            .order_by(User.display_name, RiotAccount.riot_id_game_name)
        )
    ).all()
    return [
        LinkedAccountSummary(
            account_id=str(acct.id),
            user_id=str(acct.user_id),
            discord_name=discord_name,
            game_name=acct.riot_id_game_name,
            tag_line=acct.riot_id_tag_line,
            region=acct.region,
            verified=acct.verified,
            verified_at=acct.verified_at,
            origin_guild_id=str(acct.origin_guild_id) if acct.origin_guild_id else None,
            origin_guild_name=origin_guild_name,
        )
        for acct, discord_name, origin_guild_name in rows
    ]


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
    "RANKED_SOLO_QUEUE",
    "STATUS_EXPIRED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_VERIFIED",
    "VERIFICATION_TTL",
    "VERIFY_KIND_DESKTOP_CODE",
    "VERIFY_KIND_ICON",
    "AccountLinkError",
    "CodeMintError",
    "DesktopRegistrationResult",
    "DesktopVerifyResult",
    "InvalidRegion",
    "LinkResult",
    "LinkedAccountSummary",
    "PuuidConflict",
    "RiotUnavailable",
    "UnknownOrExpiredCode",
    "UnknownRiotId",
    "continent_for",
    "format_rank",
    "get_attempt",
    "link_account",
    "list_linked_accounts",
    "normalize_code",
    "parse_platform_region",
    "region_label_for",
    "resolve_account",
    "start_desktop_registration",
    "verify_desktop_code",
]
