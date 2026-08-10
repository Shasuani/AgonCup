"""Typed domain notification envelope (PLAN.md §3.2 internal push).

PLAN.md §3.2 mandates exactly ONE internal-push channel: Redis pub/sub, with the
**backend as the sole emitter** and the bot consuming and rejecting any event
whose ``guild_id`` falls outside its guild set. This module is the canonical wire
contract for that channel.

Every message is a :class:`NotificationEnvelope`: it carries the tenant of origin
(``origin_guild_id``, the bot's tenant-authz gate), a :class:`NotificationKind`
discriminator, and a **whitelisted** ``payload`` of already-resolved display
strings. The envelope is a privacy boundary by construction — the payload models
forbid extra fields (``extra="forbid"``) and the whitelisted shapes carry NO
PUUID / Discord id / OAuth secret / verification state. Identifier→display-name
resolution and per-player consent are the emitting service's responsibility; this
contract only ever transports the projected, presentation-ready strings the bot
renders into an embed via :mod:`lol_discord.notifications`.

This is deliberately a *separate* envelope from :class:`lol_realtime.Envelope`
(the backend⇄desktop-client WS command/ack plane, PLAN.md §3.6): the notification
plane is backend→bot fan-out of domain lifecycle events, not lobby orchestration,
and must not be able to carry a lobby command payload.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Wire schema version; bumped on any breaking change to a payload shape.
NOTIFICATION_SCHEMA_VERSION = "1"

#: Redis pub/sub channel the backend emits on and the bot subscribes to.
#: Single channel per PLAN.md §3.2 ("ONE channel"); per-guild authz is enforced
#: by the consumer against ``origin_guild_id`` (the bot rejects foreign guilds).
NOTIFICATIONS_CHANNEL = "lol:notifications"


class _Strict(BaseModel):
    """Base config: forbid unknown fields so wire drift / id leakage is caught."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class NotificationKind(StrEnum):
    """Bounded set of domain notification kinds (PLAN.md §3.2).

    Each kind maps 1:1 to a payload model and to a ``lol_discord.notifications``
    embed builder the bot renders. The set is intentionally closed so a new
    notification surface is an explicit contract change.
    """

    REGISTRATION_APPROVED = "registration_approved"
    REGISTRATION_VERIFIED = "registration_verified"
    LOBBY_CREDENTIALS = "lobby_credentials"
    CHECK_IN_REMINDER = "check_in_reminder"
    MATCH_READY = "match_ready"
    RESULT_RECORDED = "result_recorded"
    TOURNAMENT_ANNOUNCED = "tournament_announced"


class RegistrationApprovedPayload(_Strict):
    """A registration was approved/verified for a tournament (PLAN.md §3.2).

    Whitelisted display strings only — NO PUUID/Discord id. ``team_name`` is the
    already-resolved roster label; identity resolution happened upstream.
    """

    kind: Literal[NotificationKind.REGISTRATION_APPROVED] = NotificationKind.REGISTRATION_APPROVED
    tournament_name: str = Field(description="Display name of the tournament.")
    team_name: str = Field(description="Resolved display name of the registered team.")


class RegistrationVerifiedPayload(_Strict):
    """A Riot account was verified + linked from the desktop app (PLAN.md §3.1).

    PRIVACY EXCEPTION (by design): this is the ONE notification kind that carries
    a ``discord_user_id``. Unlike every other kind — which fan out to a *public*
    guild ``notify_channel_id`` embed and therefore must never carry an
    identifier — this kind is **DM-routed**: the bot DMs the verifying user
    directly (falling back to the guild notify channel only if the DM fails). The
    snowflake is required so the bot can address that DM.

    Because it is DM-routed it is exempt-by-design from the central-feed
    ``_FORBIDDEN_CENTRAL_KEYS`` guard (``lol_discord.notifications``): it must
    NEVER be reused as a central-feed projection. ``riot_id`` is the resolved
    Riot ID display string (``"Chama#Shasu"``), ``region`` a display label
    (``"EUNE"``), and ``rank_text`` the Solo/Duo snapshot (``"Gold II, 45 LP"``
    or ``"Unranked"``).
    """

    kind: Literal[NotificationKind.REGISTRATION_VERIFIED] = NotificationKind.REGISTRATION_VERIFIED
    discord_user_id: str = Field(description="Snowflake of the user to DM (DM-routed kind).")
    riot_id: str = Field(description="Resolved Riot ID display string, e.g. 'Chama#Shasu'.")
    region: str = Field(description="Display region label, e.g. 'EUNE'.")
    rank_text: str = Field(description="Solo/Duo rank snapshot display, or 'Unranked'.")


class LobbyCredentialsPayload(_Strict):
    """The custom-lobby name + password, DMed to the people who must act on it.

    PRIVACY EXCEPTION (by design), the second of only two: like
    :class:`RegistrationVerifiedPayload` this carries a ``discord_user_id``
    because it is **DM-routed**, and it is therefore exempt from the central-feed
    ``_FORBIDDEN_CENTRAL_KEYS`` guard and must never be projected there.

    Unlike ``match_ready`` — the same strings posted to the guild's public
    channel — this reaches the host directly, which is what matters when the
    client's lobby-creation write did not happen and somebody has to make the
    lobby by hand. The credentials are shareable by construction (they exist so
    ten people can join), so DMing them discloses nothing the public embed does
    not already.
    """

    kind: Literal[NotificationKind.LOBBY_CREDENTIALS] = NotificationKind.LOBBY_CREDENTIALS
    discord_user_id: str = Field(description="Snowflake of the user to DM (DM-routed kind).")
    lobby_name: str = Field(description="Backend-owned custom-lobby name.")
    lobby_password: str = Field(description="Backend-owned lobby password (not an auth secret).")
    tournament_name: str = Field(description="Display name of the tournament.")
    opponent_name: str | None = Field(
        default=None, description="Resolved display name of the opposing team, if known."
    )


class CheckInReminderPayload(_Strict):
    """Check-in window reminder for a tournament (PLAN.md §3.2)."""

    kind: Literal[NotificationKind.CHECK_IN_REMINDER] = NotificationKind.CHECK_IN_REMINDER
    tournament_name: str = Field(description="Display name of the tournament.")
    deadline: str = Field(description="Human-readable check-in deadline (pre-formatted).")


class MatchReadyPayload(_Strict):
    """A match is ready to be played (PLAN.md §3.2 / §3.3 manual fallback).

    Carries only the presentation strings the bot's ``match_ready`` embed needs.
    ``host_name`` is a resolved display name (NO PUUID/Discord id). ``lobby_name``
    / ``lobby_password`` are the *manual-fallback* lobby join strings the players
    use by hand (PLAN.md §3.3) — these are intentionally shareable, not secrets in
    the auth sense, and are optional (omitted unless the match issued them).
    """

    kind: Literal[NotificationKind.MATCH_READY] = NotificationKind.MATCH_READY
    match_label: str = Field(description="Bracket/round label for the match.")
    host_name: str = Field(description="Resolved display name of the designated host.")
    lobby_name: str | None = Field(
        default=None, description="Manual-fallback lobby name (PLAN.md §3.3), if issued."
    )
    lobby_password: str | None = Field(
        default=None, description="Manual-fallback lobby password (PLAN.md §3.3), if issued."
    )


class ResultRecordedPayload(_Strict):
    """A match result was recorded (PLAN.md §3.2 / §3.6.6)."""

    kind: Literal[NotificationKind.RESULT_RECORDED] = NotificationKind.RESULT_RECORDED
    match_label: str = Field(description="Bracket/round label for the match.")
    winner_name: str = Field(description="Resolved display name of the winning side.")
    loser_name: str = Field(description="Resolved display name of the losing side.")
    decided_by: str = Field(description="How the result was decided (e.g. 'reported').")


class TournamentAnnouncedPayload(_Strict):
    """A scheduled tournament reached a milestone — registration opened, or it is
    starting (PLAN.md §3.2). Whitelisted display strings only — NO ids/secrets.

    ``headline`` is the short banner (e.g. ``"Registration is open"``), ``detail``
    the body line (e.g. ``"Join with code ABCD2345 — starts 2026-08-01 18:00 UTC"``).
    """

    kind: Literal[NotificationKind.TOURNAMENT_ANNOUNCED] = NotificationKind.TOURNAMENT_ANNOUNCED
    tournament_name: str = Field(description="Display name of the tournament.")
    headline: str = Field(description="Short milestone banner (pre-formatted).")
    detail: str = Field(description="Body line with code / times (pre-formatted).")


NotificationPayload = Annotated[
    RegistrationApprovedPayload
    | RegistrationVerifiedPayload
    | LobbyCredentialsPayload
    | CheckInReminderPayload
    | MatchReadyPayload
    | ResultRecordedPayload
    | TournamentAnnouncedPayload,
    Field(discriminator="kind"),
]


class NotificationEnvelope(_Strict):
    """The universal internal-push envelope (PLAN.md §3.2).

    ``origin_guild_id`` is present on **every** message: it is the bot's
    tenant-authz gate (reject events for guilds the bot does not serve) and mirrors
    the RLS tenant of the emitting request. ``payload`` is the discriminated,
    whitelisted domain body — by construction it cannot carry an identifier or a
    secret.
    """

    schema_version: str = Field(default=NOTIFICATION_SCHEMA_VERSION)
    origin_guild_id: str = Field(description="Tenant of origin (bot authz / RLS mirror).")
    payload: NotificationPayload

    @property
    def kind(self) -> NotificationKind:
        """The notification kind (the payload discriminator), for routing/logs."""
        return self.payload.kind


__all__ = [
    "NOTIFICATIONS_CHANNEL",
    "NOTIFICATION_SCHEMA_VERSION",
    "CheckInReminderPayload",
    "LobbyCredentialsPayload",
    "MatchReadyPayload",
    "NotificationEnvelope",
    "NotificationKind",
    "NotificationPayload",
    "RegistrationApprovedPayload",
    "RegistrationVerifiedPayload",
    "ResultRecordedPayload",
    "TournamentAnnouncedPayload",
]
