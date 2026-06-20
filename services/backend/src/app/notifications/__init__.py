"""Internal-push notification plane (PLAN.md §3.2).

The backend is the **sole emitter** on ONE internal-push channel (Redis pub/sub):
domain lifecycle events are minted into a typed, whitelisted
:class:`~app.notifications.events.NotificationEnvelope` and serialized onto the
channel by an injectable :class:`~app.notifications.service.NotificationPublisher`
seam. The bot consumes, rejects foreign-guild events (``origin_guild_id``), and
renders the per-guild ``notify_channel_id`` embed.

Re-exports the wire contract + the publisher seam for callers (the integration
seam — where existing tournaments/results/check-in flows call ``publish_*`` — is
documented in :mod:`app.notifications.service`).
"""

from __future__ import annotations

from app.notifications.events import (
    NOTIFICATION_SCHEMA_VERSION,
    NOTIFICATIONS_CHANNEL,
    CheckInReminderPayload,
    MatchReadyPayload,
    NotificationEnvelope,
    NotificationKind,
    NotificationPayload,
    RegistrationApprovedPayload,
    ResultRecordedPayload,
)
from app.notifications.service import (
    NotificationPublisher,
    RecordingNotificationPublisher,
    RedisNotificationPublisher,
    build_envelope,
    publish_check_in_reminder,
    publish_match_ready,
    publish_registration_approved,
    publish_result_recorded,
)

__all__ = [
    "NOTIFICATIONS_CHANNEL",
    "NOTIFICATION_SCHEMA_VERSION",
    "CheckInReminderPayload",
    "MatchReadyPayload",
    "NotificationEnvelope",
    "NotificationKind",
    "NotificationPayload",
    "NotificationPublisher",
    "RecordingNotificationPublisher",
    "RedisNotificationPublisher",
    "RegistrationApprovedPayload",
    "ResultRecordedPayload",
    "build_envelope",
    "publish_check_in_reminder",
    "publish_match_ready",
    "publish_registration_approved",
    "publish_result_recorded",
]
