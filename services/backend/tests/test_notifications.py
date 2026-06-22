"""Notification publisher + envelope tests (PLAN.md §3.2 internal push).

Offline by design — NO live Redis. The injectable
:class:`~app.notifications.service.RecordingNotificationPublisher` fake captures
envelopes in memory, so importing the notification module and exercising every
``publish_*`` helper connects to nothing.

We assert the *contract*: each helper mints the right typed envelope
(``origin_guild_id`` + discriminated kind + whitelisted payload), serializes to
the right Redis channel, and — critically — the wire payload excludes every
sensitive field (NO PUUID / Discord id / secret / verification state).
"""

from __future__ import annotations

import json

import pytest

from app.notifications import (
    NOTIFICATIONS_CHANNEL,
    NotificationEnvelope,
    NotificationKind,
    RecordingNotificationPublisher,
    publish_check_in_reminder,
    publish_match_ready,
    publish_registration_approved,
    publish_result_recorded,
)
from app.notifications.events import (
    NOTIFICATION_SCHEMA_VERSION,
    CheckInReminderPayload,
    MatchReadyPayload,
    RegistrationApprovedPayload,
    ResultRecordedPayload,
)
from app.notifications.service import RedisNotificationPublisher, build_envelope

GUILD = "11111111-2222-3333-4444-555555555555"

#: Substrings that must NEVER appear in a serialized notification envelope.
#: PLAN.md §3.2 / §7: the internal-push payload is a privacy boundary — only
#: pre-resolved display strings, never identifiers or secrets.
SENSITIVE_TOKENS = (
    "puuid",
    "discord_id",
    "discord_user_id",
    "user_id",
    "summoner_id",
    "summoner_name",
    "encrypted_puuid",
    "oauth",
    "refresh_token",
    "access_token",
    "verification",
    "secret",
)


def _assert_no_sensitive_fields(envelope: NotificationEnvelope) -> None:
    """The serialized envelope (keys + values) carries no identifier/secret."""
    blob = envelope.model_dump_json().lower()
    for token in SENSITIVE_TOKENS:
        assert token not in blob, f"sensitive token {token!r} leaked into envelope: {blob}"


async def test_registration_approved_serializes_expected_envelope() -> None:
    publisher = RecordingNotificationPublisher()

    returned = await publish_registration_approved(
        publisher,
        origin_guild_id=GUILD,
        tournament_name="Spring Cup",
        team_name="Team Liquid",
    )

    assert len(publisher.published) == 1
    env = publisher.published[0]
    assert env is returned
    assert env.origin_guild_id == GUILD
    assert env.kind is NotificationKind.REGISTRATION_APPROVED
    assert env.schema_version == NOTIFICATION_SCHEMA_VERSION
    assert isinstance(env.payload, RegistrationApprovedPayload)
    assert env.payload.tournament_name == "Spring Cup"
    assert env.payload.team_name == "Team Liquid"
    _assert_no_sensitive_fields(env)


async def test_registration_verified_carries_discord_user_id_for_dm() -> None:
    """The DM-routed kind is the ONE exception allowed to carry discord_user_id.

    Unlike every other kind (public guild embeds, no identifiers), this one is
    DMed to the verifying user, so the snowflake rides in the payload by design.
    """
    from app.notifications import (
        RegistrationVerifiedPayload,
        publish_registration_verified,
    )

    publisher = RecordingNotificationPublisher()
    returned = await publish_registration_verified(
        publisher,
        origin_guild_id=GUILD,
        discord_user_id="123456789012345678",
        riot_id="Chama#Shasu",
        region="EUNE",
        rank_text="Solo/Duo: Gold II, 45 LP",
    )

    env = publisher.published[0]
    assert env is returned
    assert env.origin_guild_id == GUILD
    assert env.kind is NotificationKind.REGISTRATION_VERIFIED
    assert isinstance(env.payload, RegistrationVerifiedPayload)
    assert env.payload.discord_user_id == "123456789012345678"
    assert env.payload.riot_id == "Chama#Shasu"
    assert env.payload.region == "EUNE"
    assert env.payload.rank_text == "Solo/Duo: Gold II, 45 LP"
    # Round-trips through the discriminated wire form.
    parsed = NotificationEnvelope.model_validate(json.loads(env.model_dump_json()))
    assert parsed == env


async def test_check_in_reminder_serializes_expected_envelope() -> None:
    publisher = RecordingNotificationPublisher()

    await publish_check_in_reminder(
        publisher,
        origin_guild_id=GUILD,
        tournament_name="Spring Cup",
        deadline="in 30 minutes",
    )

    env = publisher.published[0]
    assert env.kind is NotificationKind.CHECK_IN_REMINDER
    assert isinstance(env.payload, CheckInReminderPayload)
    assert env.payload.tournament_name == "Spring Cup"
    assert env.payload.deadline == "in 30 minutes"
    _assert_no_sensitive_fields(env)


async def test_match_ready_includes_manual_fallback_lobby_strings() -> None:
    publisher = RecordingNotificationPublisher()

    await publish_match_ready(
        publisher,
        origin_guild_id=GUILD,
        match_label="Quarterfinal 1",
        host_name="Faker",
        lobby_name="lol-qf1",
        lobby_password="hunter2",
    )

    env = publisher.published[0]
    assert env.kind is NotificationKind.MATCH_READY
    assert isinstance(env.payload, MatchReadyPayload)
    assert env.payload.match_label == "Quarterfinal 1"
    assert env.payload.host_name == "Faker"
    # Manual-fallback lobby join strings (PLAN.md §3.3) are shareable, not
    # auth-secrets — they ride along, but no identifier does.
    assert env.payload.lobby_name == "lol-qf1"
    assert env.payload.lobby_password == "hunter2"
    _assert_no_sensitive_fields(env)


async def test_match_ready_lobby_strings_optional() -> None:
    publisher = RecordingNotificationPublisher()

    await publish_match_ready(
        publisher,
        origin_guild_id=GUILD,
        match_label="Final",
        host_name="Caps",
    )

    env = publisher.published[0]
    assert isinstance(env.payload, MatchReadyPayload)
    assert env.payload.lobby_name is None
    assert env.payload.lobby_password is None


async def test_result_recorded_serializes_expected_envelope() -> None:
    publisher = RecordingNotificationPublisher()

    await publish_result_recorded(
        publisher,
        origin_guild_id=GUILD,
        match_label="Final",
        winner_name="Team A",
        loser_name="Team B",
        decided_by="reported",
    )

    env = publisher.published[0]
    assert env.kind is NotificationKind.RESULT_RECORDED
    assert isinstance(env.payload, ResultRecordedPayload)
    assert env.payload.winner_name == "Team A"
    assert env.payload.loser_name == "Team B"
    assert env.payload.decided_by == "reported"
    _assert_no_sensitive_fields(env)


async def test_envelope_payload_forbids_unknown_fields() -> None:
    """Defence in depth: the payload models reject any extra (e.g. id) field."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RegistrationApprovedPayload.model_validate(
            {
                "tournament_name": "Spring Cup",
                "team_name": "Team Liquid",
                "puuid": "leaked-puuid-should-be-rejected",
            }
        )


async def test_build_envelope_round_trips_through_json() -> None:
    """The wire form deserializes back to the discriminated payload."""
    env = build_envelope(
        origin_guild_id=GUILD,
        payload=MatchReadyPayload(match_label="SF1", host_name="Chovy"),
    )

    wire = env.model_dump_json()
    parsed = NotificationEnvelope.model_validate(json.loads(wire))
    assert parsed == env
    assert parsed.kind is NotificationKind.MATCH_READY


async def test_redis_publisher_writes_json_to_channel() -> None:
    """RedisNotificationPublisher serializes to JSON on the configured channel.

    Uses a fake async Redis client (a recording stub) — NO live Redis.
    """

    class _FakeRedis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def publish(self, channel: str, message: str) -> int:
            self.calls.append((channel, message))
            return 1

    fake = _FakeRedis()
    publisher = RedisNotificationPublisher(fake)  # type: ignore[arg-type]

    await publish_registration_approved(
        publisher,
        origin_guild_id=GUILD,
        tournament_name="Spring Cup",
        team_name="Team Liquid",
    )

    assert len(fake.calls) == 1
    channel, message = fake.calls[0]
    assert channel == NOTIFICATIONS_CHANNEL
    decoded = json.loads(message)
    assert decoded["origin_guild_id"] == GUILD
    assert decoded["payload"]["kind"] == NotificationKind.REGISTRATION_APPROVED.value
    # The serialized wire form leaks no identifier/secret.
    for token in SENSITIVE_TOKENS:
        assert token not in message.lower()
