"""Notification publisher service (PLAN.md §3.2 internal push).

The backend is the **sole emitter** on the internal-push plane (PLAN.md §3.2):
domain flows raise lifecycle events here, this module mints the typed
:class:`~app.notifications.events.NotificationEnvelope`, and a thin, injectable
:class:`NotificationPublisher` seam serializes it onto the single Redis pub/sub
channel. The bot subscribes, rejects any event whose ``origin_guild_id`` is
outside its guild set, resolves the per-guild ``notify_channel_id``
(:class:`app.guilds.settings.GuildSettings`), and renders the embed.

Publisher seam (mirrors ``app.orchestration.service.CommandPublisher``)
-----------------------------------------------------------------------
The Redis transport is a **documented wiring point**, not a hard dependency:

* :class:`NotificationPublisher` is the base seam — a no-op log sink so the
  notification API is testable offline (importing this module connects to
  nothing).
* :class:`RedisNotificationPublisher` is the production subclass that does
  ``redis.publish(channel, envelope.model_dump_json())``. It accepts an already
  constructed async Redis client (``redis.asyncio.Redis``) so connection
  lifecycle is owned by the app, not this module.
* :class:`RecordingNotificationPublisher` is the in-memory fake the unit tests
  inject — NO live Redis.

The module-level :func:`publish_*` helpers are the **integration seam** the
existing domain flows call (see the "Integration seam" docstring section below):
they build the whitelisted payload + envelope and delegate to the injected
publisher. They do NOT import tournaments/results/teams services — callers pass
already-resolved display strings, so this module never touches a PUUID/secret.

Integration seam — documented call sites (FOLLOW-UP wiring, NOT done here)
-------------------------------------------------------------------------
Per the task contract these emit points are *documented* here and wired by the
owning services in a follow-up (this module must not edit those services):

* ``registration_approved`` ← ``app.tournaments.service`` registration approval
  / verification path (where a ``Registration`` transitions to verified/approved).
  Call ``await publish_registration_approved(publisher, origin_guild_id=...,
  tournament_name=..., team_name=...)`` after the commit.
* ``check_in_reminder`` ← the check-in scheduler (arq worker job that opens the
  check-in window for a tournament). Call ``publish_check_in_reminder(...)``.
* ``match_ready`` ← ``app.tournaments.service`` start / bracket generation, or the
  orchestration ``initialize_lobby`` path, once a match's host + manual-fallback
  lobby strings are known. Call ``publish_match_ready(...)``.
* ``result_recorded`` ← ``app.results.service.record_winner`` after the winner is
  persisted. Call ``publish_result_recorded(...)``.

Each call site already holds the acting guild (the RLS ``app.current_guild`` GUC
/ the bridge ``DiscordContext``); pass it as ``origin_guild_id`` (string form).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging import get_logger
from app.notifications.events import (
    NOTIFICATIONS_CHANNEL,
    CheckInReminderPayload,
    MatchReadyPayload,
    NotificationEnvelope,
    NotificationPayload,
    RegistrationApprovedPayload,
    ResultRecordedPayload,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime redis import.
    from redis.asyncio import Redis

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Publish seam (Redis pub/sub is a documented, injectable wiring point)
# ---------------------------------------------------------------------------
class NotificationPublisher:
    """Thin publish abstraction onto the internal-push plane (PLAN.md §3.2).

    The concrete transport is Redis pub/sub on a single channel; that wiring is a
    **documented seam** so the notification service is testable offline. This base
    class is a no-op log sink — a production subclass
    (:class:`RedisNotificationPublisher`) overrides :meth:`publish` to write to
    Redis. Inject one into the module-level ``publish_*`` helpers.
    """

    async def publish(self, envelope: NotificationEnvelope) -> None:
        """Publish one envelope onto the internal-push plane. Override to wire Redis."""
        logger.info(
            "notifications.publish",
            channel=NOTIFICATIONS_CHANNEL,
            origin_guild_id=envelope.origin_guild_id,
            kind=envelope.kind.value,
            schema_version=envelope.schema_version,
        )


class RedisNotificationPublisher(NotificationPublisher):
    """Production publisher: serializes the envelope to JSON onto Redis pub/sub.

    Accepts an already-constructed async Redis client so the connection lifecycle
    is owned by the app (created from ``settings.redis_url``), not this module.
    Importing this module never opens a connection.
    """

    def __init__(self, client: Redis, *, channel: str = NOTIFICATIONS_CHANNEL) -> None:
        self._client = client
        self._channel = channel

    async def publish(self, envelope: NotificationEnvelope) -> None:
        """Emit ``envelope`` as JSON on the configured Redis channel."""
        await self._client.publish(self._channel, envelope.model_dump_json())
        logger.info(
            "notifications.publish.redis",
            channel=self._channel,
            origin_guild_id=envelope.origin_guild_id,
            kind=envelope.kind.value,
        )


class RecordingNotificationPublisher(NotificationPublisher):
    """A :class:`NotificationPublisher` that records envelopes (tests/debug)."""

    def __init__(self) -> None:
        self.published: list[NotificationEnvelope] = []

    async def publish(self, envelope: NotificationEnvelope) -> None:
        self.published.append(envelope)


# ---------------------------------------------------------------------------
# Envelope minting + publish helpers (the integration seam callers use)
# ---------------------------------------------------------------------------
def build_envelope(*, origin_guild_id: str, payload: NotificationPayload) -> NotificationEnvelope:
    """Wrap a whitelisted ``payload`` in a tenant-stamped envelope (PLAN.md §3.2)."""
    return NotificationEnvelope(origin_guild_id=origin_guild_id, payload=payload)


async def publish_registration_approved(
    publisher: NotificationPublisher,
    *,
    origin_guild_id: str,
    tournament_name: str,
    team_name: str,
) -> NotificationEnvelope:
    """Emit ``registration_approved`` (PLAN.md §3.2). Returns the published envelope."""
    envelope = build_envelope(
        origin_guild_id=origin_guild_id,
        payload=RegistrationApprovedPayload(tournament_name=tournament_name, team_name=team_name),
    )
    await publisher.publish(envelope)
    return envelope


async def publish_check_in_reminder(
    publisher: NotificationPublisher,
    *,
    origin_guild_id: str,
    tournament_name: str,
    deadline: str,
) -> NotificationEnvelope:
    """Emit ``check_in_reminder`` (PLAN.md §3.2). Returns the published envelope."""
    envelope = build_envelope(
        origin_guild_id=origin_guild_id,
        payload=CheckInReminderPayload(tournament_name=tournament_name, deadline=deadline),
    )
    await publisher.publish(envelope)
    return envelope


async def publish_match_ready(
    publisher: NotificationPublisher,
    *,
    origin_guild_id: str,
    match_label: str,
    host_name: str,
    lobby_name: str | None = None,
    lobby_password: str | None = None,
) -> NotificationEnvelope:
    """Emit ``match_ready`` (PLAN.md §3.2/§3.3). Returns the published envelope.

    ``lobby_name`` / ``lobby_password`` are the manual-fallback join strings
    (PLAN.md §3.3); pass them only when the match has issued a lobby.
    """
    envelope = build_envelope(
        origin_guild_id=origin_guild_id,
        payload=MatchReadyPayload(
            match_label=match_label,
            host_name=host_name,
            lobby_name=lobby_name,
            lobby_password=lobby_password,
        ),
    )
    await publisher.publish(envelope)
    return envelope


async def publish_result_recorded(
    publisher: NotificationPublisher,
    *,
    origin_guild_id: str,
    match_label: str,
    winner_name: str,
    loser_name: str,
    decided_by: str,
) -> NotificationEnvelope:
    """Emit ``result_recorded`` (PLAN.md §3.2/§3.6.6). Returns the published envelope."""
    envelope = build_envelope(
        origin_guild_id=origin_guild_id,
        payload=ResultRecordedPayload(
            match_label=match_label,
            winner_name=winner_name,
            loser_name=loser_name,
            decided_by=decided_by,
        ),
    )
    await publisher.publish(envelope)
    return envelope


__all__ = [
    "NotificationPublisher",
    "RecordingNotificationPublisher",
    "RedisNotificationPublisher",
    "build_envelope",
    "publish_check_in_reminder",
    "publish_match_ready",
    "publish_registration_approved",
    "publish_result_recorded",
]
