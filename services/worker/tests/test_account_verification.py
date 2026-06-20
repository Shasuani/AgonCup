"""account_verification_poll job tests (PLAN.md §3.1 / §3.5).

Offline by design — no Redis, no Postgres, no Riot network. The DB + Riot
collaborators are injected through ``ctx["verification_gateway"]`` (a fake), so
these exercise the real decision logic of the job:

* **success**: the live ``profileIconId`` equals the attempt's ``target_icon_id``
  → the ``RiotAccount`` is marked verified + the attempt consumed.
* **mismatch**: a wrong icon (within TTL) → the job re-enqueues, nothing verified.
* **expiry**: past the hard ``expires_at`` → the attempt is marked expired, no
  Riot call, no re-enqueue.

Mirrors the existing worker test style (``tests/test_jobs.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.main import AttemptRecord, account_verification_poll


@dataclass
class _FakeSummoner:
    profile_icon_id: int


class _FakeRiotClient:
    def __init__(self, profile_icon_id: int) -> None:
        self._icon = profile_icon_id
        self.calls: list[tuple[str, Any]] = []

    async def get_summoner_by_puuid(self, puuid: str, platform: Any) -> _FakeSummoner:
        self.calls.append((puuid, platform))
        return _FakeSummoner(profile_icon_id=self._icon)


@dataclass
class _FakeGateway:
    record: AttemptRecord | None
    icon_id: int = 0
    verified: list[tuple[str, str]] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    reenqueued: list[str] = field(default_factory=list)

    def riot_client(self) -> Any:
        return _FakeRiotClient(self.icon_id)

    async def load_attempt(self, attempt_id: str) -> AttemptRecord | None:
        return self.record

    async def mark_verified(self, attempt_id: str, puuid: str, *, verified_at: datetime) -> None:
        self.verified.append((attempt_id, puuid))

    async def mark_expired(self, attempt_id: str) -> None:
        self.expired.append(attempt_id)

    async def reenqueue(self, attempt_id: str, *, delay_seconds: int) -> None:
        self.reenqueued.append(attempt_id)


def _record(*, icon: int = 5, expires_in: timedelta = timedelta(minutes=10)) -> AttemptRecord:
    return AttemptRecord(
        attempt_id="att-1",
        puuid="PUUID-1",
        target_icon_id=icon,
        status="pending",
        expires_at=datetime.now(tz=UTC) + expires_in,
        region="na1",
    )


def _ctx(gateway: _FakeGateway, *, now: datetime | None = None) -> dict[str, Any]:
    ctx: dict[str, Any] = {"verification_gateway": gateway}
    if now is not None:
        ctx["now_fn"] = lambda: now
    return ctx


@pytest.mark.asyncio
async def test_poll_success_marks_verified() -> None:
    # Live icon matches the target → verified, consumed, no re-enqueue.
    gateway = _FakeGateway(record=_record(icon=12), icon_id=12)
    result = await account_verification_poll(_ctx(gateway), "att-1")

    assert result == "verified"
    assert gateway.verified == [("att-1", "PUUID-1")]
    assert gateway.reenqueued == []
    assert gateway.expired == []


@pytest.mark.asyncio
async def test_poll_mismatch_reenqueues_without_verifying() -> None:
    # Live icon differs from the target → re-enqueue, nothing verified.
    gateway = _FakeGateway(record=_record(icon=12), icon_id=99)
    result = await account_verification_poll(_ctx(gateway), "att-1")

    assert result == "mismatch"
    assert gateway.verified == []
    assert gateway.reenqueued == ["att-1"]
    assert gateway.expired == []


@pytest.mark.asyncio
async def test_poll_past_ttl_marks_expired() -> None:
    # Attempt already past its hard TTL → expired, no Riot call, no re-enqueue.
    gateway = _FakeGateway(record=_record(icon=12, expires_in=timedelta(minutes=-1)))
    result = await account_verification_poll(_ctx(gateway), "att-1")

    assert result == "expired"
    assert gateway.expired == ["att-1"]
    assert gateway.verified == []
    assert gateway.reenqueued == []


@pytest.mark.asyncio
async def test_poll_already_resolved_is_idempotent() -> None:
    record = AttemptRecord(
        attempt_id="att-1",
        puuid="PUUID-1",
        target_icon_id=5,
        status="verified",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        region="na1",
    )
    gateway = _FakeGateway(record=record, icon_id=5)
    result = await account_verification_poll(_ctx(gateway), "att-1")

    assert result == "already_resolved"
    assert gateway.verified == []
    assert gateway.reenqueued == []


@pytest.mark.asyncio
async def test_poll_missing_attempt_is_noop() -> None:
    gateway = _FakeGateway(record=None)
    result = await account_verification_poll(_ctx(gateway), "ghost")
    assert result == "missing"


@pytest.mark.asyncio
async def test_bare_ctx_raises_not_implemented() -> None:
    # No injected gateway and no settings → the engine/Riot client are unwired,
    # so the job refuses to run (keeps the Phase-0 queue-shape smoke test honest).
    with pytest.raises(NotImplementedError):
        await account_verification_poll({}, "att-1")
