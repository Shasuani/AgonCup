"""Command publish-path wiring (PLAN.md §3.6.1 / §5).

Closes the loop between the HTTP write path and the realtime read path: the
orchestration router must publish minted commands through the production
``RedisCommandPublisher`` onto the SAME per-guild/per-match channel the WS hub
subscribes to (``hub.command_channel``) — not the no-op default sink. Offline:
a fake redis client, no live IO.
"""

from __future__ import annotations

import uuid

import pytest

from app.orchestration import router as orch_router
from app.orchestration import service
from app.realtime import hub


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


@pytest.mark.asyncio
async def test_redis_command_publisher_targets_the_hub_channel() -> None:
    guild, match = uuid.uuid4(), uuid.uuid4()
    cmd = service.build_abort_command(match_id=match, host_epoch=3, reason="manual")
    envelope = service.wrap_envelope(cmd, guild_id=guild, match_id=match)

    fake = _FakeRedis()
    await hub.RedisCommandPublisher(fake).publish(envelope)

    assert len(fake.published) == 1
    channel, payload = fake.published[0]
    # The publisher targets exactly the channel the WS hub subscribes to.
    assert channel == hub.command_channel(guild, match)
    assert str(guild) in channel and str(match) in channel
    # The serialized command payload type rides the channel.
    assert envelope.payload.type in payload


def test_orchestration_router_uses_the_redis_publisher_not_the_noop_sink() -> None:
    publisher = orch_router._command_publisher()
    # Production path: a real Redis publisher, NOT the base no-op CommandPublisher.
    assert isinstance(publisher, hub.RedisCommandPublisher)
    assert type(publisher) is not service.CommandPublisher
